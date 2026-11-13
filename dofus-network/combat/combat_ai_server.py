"""Live combat policy API for the in-game FarmBot.

The page asks this server for one action per turn:

    POST /v1/combat/decide   body = the raw ``fight.state`` payload

The server binds that board to the encounter policy's slots (the live spells,
the live enemies), runs the trained PPO/PBT actor, and maps the chosen
spell/target/move intent onto the cell solver's legal plan. Nothing is
hardcoded per class or level: every request rebuilds the binding.

The previous 8892 server was a ModernBERT stance classifier with hardcoded Cra
spells. It was never going to work for a different instance, and the page no
longer called it. This file replaces that path; the model is the checkpoint
from ``encounter_selfplay.pkl`` (override with ``--checkpoint``).
"""

from __future__ import annotations

import argparse
import atexit
import hmac
import os
import sys
import threading
import time
import tracemalloc
import urllib.parse
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
for _path in (REPO_ROOT, REPO_ROOT / "dofus-network"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from agent.bridge import local_secret
from agent.jsonio import dumps_bytes, loads
import logging
logger = logging.getLogger(__name__)

# combat/live_policy.py used to be a JAX-only model, and the interpreter that ships inside
# setup.exe has no jax wheel (measured 2026-11-03: the development interpreter imports jax
# 0.12.0.1, runtimes/python raises ModuleNotFoundError, and PyPI has no cp311 jaxlib at all). It
# now runs on whichever array library exists -- jax when installed, numpy otherwise, through
# combat/encounter/backend.py -- so an installed copy serves the learned policy instead of the
# solver. The import stays lazy for the window that still matters: a fresh install has not run
# `pip install -r requirements.txt` yet, so even numpy can be absent, and every handler already
# treats `POLICY is None` as "solver only".
DEFAULT_CHECKPOINT = REPO_ROOT / "dofus-network" / "models" / "encounter_selfplay.pkl"


def _runtime_identity() -> Dict[str, object]:
    """Who is answering `/health` -- interpreter, script, pid, checkpoint hash.

    The run token is deliberately not stored here. `/health` is reachable without
    a token so the manager can probe liveness; it only adds the token back for a
    caller that proved it belongs to this run. A missing token means the server
    was started by hand or by an older build, and the manager treats that as
    foreign because "I cannot tell" is not evidence of ownership.
    """
    import hashlib
    sha = None
    try:
        if DEFAULT_CHECKPOINT.exists():
            sha = hashlib.sha256(DEFAULT_CHECKPOINT.read_bytes()).hexdigest()[:16]
    except OSError:
        sha = None
    return {"pid": os.getpid(),
            "interpreter": sys.executable,
            "script": str(Path(__file__).resolve()),
            "started_at": round(time.time(), 3),
            "checkpoint_sha": sha,
            "token_present": bool(local_secret())}


RUNTIME = _runtime_identity()

# -- loopback control-plane guard -------------------------------------------
# Same contract as agent/serve.py: bearer token for control, exact Origin/Host,
# JSON content type. `/health` stays readable without a token (redacted), so a
# stale/foreign process can still be detected before it is reaped.

_ALLOWED_ORIGIN_HOSTS = {"dofus.local", "dofus.localhost", "127.0.0.1", "localhost", "::1"}
_RATE_LOCK = threading.Lock()
_RATE_TIMES: list[float] = []
_RATE_MAX_PER_SECOND = 40


def _origin_allowed(origin: str) -> bool:
    if not origin:
        return True
    try:
        parsed = urllib.parse.urlsplit(origin)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    return (parsed.hostname or "").lower() in _ALLOWED_ORIGIN_HOSTS


#: How often the SSE health stream emits a snapshot.
STREAM_INTERVAL_S = 1.0

def _host_allowed(host: str, port: int) -> bool:
    if not host:
        return True
    host = host.strip().lower()
    return host in {"127.0.0.1:%d" % port, "localhost:%d" % port, "[::1]:%d" % port}


def _bearer_matches(supplied: str, secret: str) -> bool:
    if not secret:
        return False
    return hmac.compare_digest(supplied or "", "Bearer " + secret)


def _rate_ok() -> bool:
    now = time.monotonic()
    with _RATE_LOCK:
        while _RATE_TIMES and now - _RATE_TIMES[0] > 1.0:
            _RATE_TIMES.pop(0)
        if len(_RATE_TIMES) >= _RATE_MAX_PER_SECOND:
            return False
        _RATE_TIMES.append(now)
        return True


def _policy_backend() -> str:
    """Which array library the loaded policy is running on.

    Asked lazily: `combat.encounter.backend` imports numpy, so on a copy whose pip install has
    not happened yet this must degrade instead of breaking the health endpoint.
    """
    try:
        from combat.encounter.backend import BACKEND
        return BACKEND
    except ImportError:
        return "learned (backend unknown)"

POLICY: Optional[Any] = None
DECISION_LOCK = threading.Lock()
LOG_PATH = REPO_ROOT / "trajectories" / "live_policy_decisions.jsonl"
LOG_MAX_BYTES = 64 * 1024 * 1024
# A decision row is several KB. Opening, writing and closing the log per
# decision cost about a millisecond; keeping one handle and flushing each row
# costs ~0.1 ms and loses nothing if the process is killed. (Block flushing is
# ~0.008 ms but can drop the last rows on TerminateProcess.)
LOG_FLUSH_BYTES = 0
_LOG_LOCK = threading.Lock()
_LOG_HANDLE = None
_LOG_OPEN_PATH: Optional[Path] = None
_LOG_BYTES = 0
_LOG_PENDING = 0


def _close_log_locked(flush: bool = True) -> None:
    global _LOG_HANDLE, _LOG_OPEN_PATH, _LOG_PENDING
    handle, _LOG_HANDLE = _LOG_HANDLE, None
    _LOG_OPEN_PATH = None
    _LOG_PENDING = 0
    if handle is None:
        return
    try:
        if flush:
            handle.flush()
    except OSError:
        pass
    finally:
        try:
            handle.close()
        except OSError:
            pass


def _open_log_locked() -> None:
    global _LOG_HANDLE, _LOG_OPEN_PATH, _LOG_BYTES, _LOG_PENDING
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _LOG_HANDLE = LOG_PATH.open("ab")
    _LOG_OPEN_PATH = LOG_PATH
    try:
        _LOG_BYTES = LOG_PATH.stat().st_size
    except OSError:
        _LOG_BYTES = 0
    _LOG_PENDING = 0


def _rotate_log_locked() -> None:
    """Keep an unattended server from filling the disk with one JSONL file."""
    global _LOG_BYTES
    _close_log_locked()
    try:
        if LOG_PATH.exists():
            stamp = time.strftime("%Y%m%d-%H%M%S")
            rotated = LOG_PATH.with_name(
                f"{LOG_PATH.stem}.{stamp}{LOG_PATH.suffix}")
            suffix = 0
            while rotated.exists():
                suffix += 1
                rotated = LOG_PATH.with_name(
                    f"{LOG_PATH.stem}.{stamp}-{suffix}{LOG_PATH.suffix}")
            LOG_PATH.rename(rotated)
    except OSError:
        pass
    _LOG_BYTES = 0
    _open_log_locked()


def _log_decision(payload: Dict[str, Any], decision: Dict[str, Any]) -> Dict[str, Any]:
    """Append one decision row and hand it back for the live shadow auditor."""
    global _LOG_BYTES, _LOG_PENDING
    record = {
        "time": time.time(),
        "fight_id": payload.get("fight_id"),
        "round": payload.get("round"),
        "my_id": payload.get("my_id"),
        # The optional challenges the fight was started with. They are not
        # scored here -- the reward for one lives in the outcome row -- but a
        # decision row without them cannot be used later to learn a policy
        # that respects one.
        "challenges": payload.get("challenges") or [],
        "decision": decision,
    }
    try:
        line = dumps_bytes(record) + b"\n"
        with _LOG_LOCK:
            if _LOG_HANDLE is None or _LOG_OPEN_PATH != LOG_PATH:
                _close_log_locked()
                _open_log_locked()
            if _LOG_BYTES + len(line) >= LOG_MAX_BYTES:
                _rotate_log_locked()
            _LOG_HANDLE.write(line)
            _LOG_BYTES += len(line)
            _LOG_PENDING += len(line)
            if _LOG_PENDING >= LOG_FLUSH_BYTES:
                _LOG_HANDLE.flush()
                _LOG_PENDING = 0
    except OSError:
        # An unwritable log must never stop the fight API.
        with _LOG_LOCK:
            try:
                _close_log_locked(flush=False)
            except Exception:  # noqa: BLE001 - best effort close
                pass
    return record


@atexit.register
def _flush_decision_log() -> None:
    with _LOG_LOCK:
        _close_log_locked()
    # The cadence leaves up to `every` paired casts unwritten. A session that
    # ends mid-fight would otherwise drop exactly the casts it just measured.
    try:
        if _SHADOW_AUDITOR is not None:
            _SHADOW_AUDITOR.flush()
    except Exception:  # noqa: BLE001 - shutdown must not raise
        pass


_LEARNER = None
_LEARNER_LOCK = threading.Lock()


def _learner() -> Any:
    """The process-wide live learner, created on first use.

    Imported lazily for the same reason the policy is: the brain has to start even
    when the combat data stack is not installed, and a missing learner must degrade
    to "not learning" rather than "no brain".
    """
    global _LEARNER
    if _LEARNER is None:
        with _LEARNER_LOCK:
            if _LEARNER is None:
                from combat.live_learn import LiveLearner
                _LEARNER = LiveLearner()
    return _LEARNER


def _learning_report() -> Dict[str, Any]:
    """The learner's state, or the reason it is not learning.

    Never raises: /health is what the manager uses to decide whether the brain is
    answering, and an exception here would turn "not learning" into "not running".
    """
    try:
        return _learner().report()
    except Exception as exc:  # noqa: BLE001
        return {"status": "unavailable", "error": "%s: %s" % (type(exc).__name__, exc)}


class _ThreatObserver:
    """Folds each fight's message log into `models/threat_model.json`.

    The threat model needs one number the client does not publish: how hard a
    given monster hits, and from how far. The fight's own message log carries
    both -- a cast by a monster, then the life it took off the character -- and
    it arrives on every board, so this costs no extra request.

    The log is cumulative and rolls: it grows while the fight lasts and then
    keeps only the tail. Re-reading it whole would count the same hit on every
    turn (the fight would "teach" the same twelve points forty times), so the
    already-seen part is matched by overlap and only the new suffix is folded
    in.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        from combat import threat as _threat

        self._threat = _threat
        self.path = Path(path or _threat.DEFAULT_MEMORY)
        self.memory = _threat.ThreatMemory.load(self.path)
        self.learned = 0
        # One seen-suffix per (account, fight). A single `_fight`/`_log` pair
        # was overwritten by whichever slot posted next, and two slots in combat
        # alternate turns, so the reset fired on *every* turn: `_new_messages`
        # then returned the whole cumulative log and the same monster hits were
        # folded into `threat_model.json` again and again (10-40x over a fight).
        self._logs: "OrderedDict[Tuple[str, str], List[Any]]" = OrderedDict()
        self._lock = threading.Lock()

    def observe(self, payload: Dict[str, Any]) -> int:
        if not isinstance(payload, dict):
            return 0
        log = payload.get("battle_log")
        if not isinstance(log, list):
            log = payload.get("last_actions")
        if not isinstance(log, list) or not log:
            return 0
        key = _fight_key(payload)
        with self._lock:
            seen = self._logs.pop(key, [])
            fresh = _new_messages(seen, log)
            # Re-insert at the end: the dict is ordered by use, so eviction below
            # drops the fight that stopped posting rather than an active one.
            self._logs[key] = list(log)
            while len(self._logs) > _MAX_TRACKED_FIGHTS:
                self._logs.popitem(last=False)
            if not fresh:
                return 0
            names: Dict[int, str] = {}
            for fighter in payload.get("fighters") or []:
                if not isinstance(fighter, dict):
                    continue
                try:
                    names[int(fighter.get("id"))] = str(fighter.get("name") or "")
                except (TypeError, ValueError):
                    continue
            learned = self.memory.observe(fresh, payload.get("my_id"), names)
            if learned:
                self.learned += learned
                self.memory.save(self.path)
            return learned

    def report(self) -> Dict[str, Any]:
        return {"learned_hits": self.learned, "monsters": len(self.memory.monsters),
                "path": str(self.path)}


#: How many concurrent fights one server process remembers log suffixes for.
#: A leak guard, not a limit on characters: a running server sees a handful of
#: fights in flight and an evicted one is a fight that stopped posting.
_MAX_TRACKED_FIGHTS = 64


def _fight_key(payload: Dict[str, Any]) -> Tuple[str, str]:
    """(account, fight) for a board payload -- the same key `live_learn` uses.

    `fight_id` alone is not enough: two slots on one account can share a fight,
    and a stale board can carry a recycled id. `my_id` is absent on some
    payloads, so the board's own self-identifying fighter stands in; without
    that fallback every id-less board would share one slot and the bug would
    come back through the back door.
    """
    mine = payload.get("my_id")
    if mine is None:
        for fighter in payload.get("fighters") or []:
            if isinstance(fighter, dict) and fighter.get("is_me"):
                mine = fighter.get("id")
                break
    return (str(mine) if mine is not None else "",
            str(payload.get("fight_id")) if payload.get("fight_id") is not None else "")


def _new_messages(previous: List[Any], current: List[Any]) -> List[Any]:
    """The part of ``current`` that is not already in ``previous``.

    Matched by overlap from the tail, not by length: the log both grows and
    rolls, and a length comparison would report "nothing new" forever once the
    cap was reached.
    """
    if not previous:
        return list(current)
    limit = min(len(previous), len(current))
    for overlap in range(limit, 0, -1):
        if previous[-overlap:] == current[:overlap]:
            return list(current[overlap:])
    return list(current)


_THREAT_OBSERVER: Optional[_ThreatObserver] = None
_THREAT_LOCK = threading.Lock()


def _threat_observer() -> _ThreatObserver:
    global _THREAT_OBSERVER
    if _THREAT_OBSERVER is None:
        with _THREAT_LOCK:
            if _THREAT_OBSERVER is None:
                _THREAT_OBSERVER = _ThreatObserver()
    return _THREAT_OBSERVER


def _threat_report() -> Dict[str, Any]:
    try:
        return _threat_observer().report()
    except Exception as exc:  # noqa: BLE001
        return {"status": "unavailable", "error": "%s: %s" % (type(exc).__name__, exc)}


_SHADOW_AUDITOR: Optional[Any] = None
_SHADOW_LOCK = threading.Lock()


def _shadow_auditor() -> Any:
    """The live residual auditor, created on first use.

    Without this the damage calibration is a file only the CLI writes, so the
    solver's correction would be wired to something a running install never
    produces. Lazy for the same reason as the learner and the threat observer:
    a brain that cannot import the combat stack must still answer.
    """
    global _SHADOW_AUDITOR
    if _SHADOW_AUDITOR is None:
        with _SHADOW_LOCK:
            if _SHADOW_AUDITOR is None:
                from combat.live_learn import LiveShadowAuditor
                _SHADOW_AUDITOR = LiveShadowAuditor()
    return _SHADOW_AUDITOR


def _shadow_report() -> Dict[str, Any]:
    try:
        return _shadow_auditor().report()
    except Exception as exc:  # noqa: BLE001
        return {"status": "unavailable", "error": "%s: %s" % (type(exc).__name__, exc)}


def decide(payload: Dict[str, Any]) -> Dict[str, Any]:
    # Learn before deciding. The page posts a board every turn, so the board arriving    # now is the label for the turn that just finished: the drop in our own life is
    # what the enemies cost us, and it needs no spell id to be trustworthy (see
    # combat/live_learn.py for why the per-spell join cannot work on this payload).
    # Doing it first also means learning continues when the policy fails to load --
    # a brain that cannot act can still get better at pricing the fight.
    credit = None
    try:
        credit = _learner().observe(payload)
    except Exception:  # noqa: BLE001 - a learner must never stop a fight
        pass
    # Same ordering, different question: the learner prices a whole enemy turn,
    # this prices one monster. Both have to run before the decision, because the
    # solver reads the per-monster table to decide where to stand this turn.
    try:
        _threat_observer().observe(payload)
    except Exception:  # noqa: BLE001 - a threat model must never stop a fight
        pass
    if POLICY is None:
        return {"ok": False, "action": None, "reason": "policy_not_loaded",
                "latency_ms": 0.0}
    t_srv = time.perf_counter()
    with DECISION_LOCK:
        started = time.perf_counter()
        decision = POLICY.decide(payload)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
    solve_ms = float(decision.get("latency_ms", elapsed_ms))
    record_turn_latency(solve_ms)
    decision["server_latency_ms"] = round((time.perf_counter() - t_srv) * 1000.0, 2)
    if credit is not None:
        # Recorded on the row that ended the turn, so the ledger shows what the model
        # was told as well as what it answered.
        decision["credited_damage"] = credit.damage
    record = _log_decision(payload, decision)
    # Pair this cast with the board update that reflects it, and keep the damage
    # calibration current. After the decision, not before: the residual is a
    # measurement of what the server just priced, and it must not delay the answer.
    try:
        _shadow_auditor().observe(record)
    except Exception:  # noqa: BLE001 - an auditor must never stop a fight
        pass
    return decision


_LATENCY_HISTORY: List[float] = []
_LATENCY_LOCK = threading.Lock()


def record_turn_latency(elapsed_ms: float) -> None:
    with _LATENCY_LOCK:
        _LATENCY_HISTORY.append(elapsed_ms)
        if len(_LATENCY_HISTORY) > 20000:
            del _LATENCY_HISTORY[:5000]


def latency_percentiles() -> Dict[str, Any]:
    with _LATENCY_LOCK:
        if not _LATENCY_HISTORY:
            return {"count": 0, "min": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0, "avg": 0.0}
        vals = sorted(_LATENCY_HISTORY)
        n = len(vals)

        def _calc_p(q: float) -> float:
            pos = (n - 1) * q
            low = int(pos)
            high = min(low + 1, n - 1)
            return round(vals[low] + (vals[high] - vals[low]) * (pos - low), 3)

        return {
            "count": n,
            "min": round(vals[0], 3),
            "max": round(vals[-1], 3),
            "p50": _calc_p(0.50),
            "p95": _calc_p(0.95),
            "p99": _calc_p(0.99),
            "avg": round(sum(vals) / n, 3),
        }


_TOP_ALLOCATORS: List[Dict[str, Any]] = []


def top_allocators(limit: int = 20) -> List[Dict[str, Any]]:
    global _TOP_ALLOCATORS
    if not _TOP_ALLOCATORS:
        was_tracing = tracemalloc.is_tracing()
        if not was_tracing:
            tracemalloc.start(1)
        try:
            snapshot = tracemalloc.take_snapshot()
            top_stats = snapshot.statistics("lineno")[:limit]
            result = []
            for stat in top_stats:
                frame = stat.traceback[0]
                result.append({
                    "filename": str(Path(frame.filename).name),
                    "lineno": frame.lineno,
                    "size_bytes": stat.size,
                    "count": stat.count,
                })
            if result:
                _TOP_ALLOCATORS = result
        finally:
            if not was_tracing and tracemalloc.is_tracing():
                tracemalloc.stop()
    return _TOP_ALLOCATORS


def profile_payload() -> Dict[str, Any]:
    return {
        "status": "ok",
        "timestamp": time.time(),
        "latency": latency_percentiles(),
        "top_allocators": top_allocators(20),
        "active_threads": threading.active_count(),
    }


class CombatRequestHandler(BaseHTTPRequestHandler):
    # The page sends one request per turn; keep-alive avoids a new loopback
    # connection for every turn.
    protocol_version = "HTTP/1.1"

    def _cors_origin(self) -> str:
        origin = (self.headers.get("Origin") or "").strip()
        return origin if origin and _origin_allowed(origin) else ""

    def _authorized(self, require_token: bool = True) -> bool:
        if not _host_allowed(self.headers.get("Host", ""), self.server.server_port):
            return False
        origin = (self.headers.get("Origin") or "").strip()
        if origin and not _origin_allowed(origin):
            return False
        if require_token:
            return _bearer_matches(self.headers.get("Authorization", ""), local_secret())
        return True

    def _reply(self, data: Dict[str, Any], status: int = 200) -> None:
        body = dumps_bytes(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        origin = self._cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        if self.headers.get("Connection", "").lower() == "close":
            self.send_header("Connection", "close")
            self.close_connection = True
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        if not self._authorized(require_token=False):
            self._reply({"ok": False, "error": "forbidden"}, 403)
            return
        self.send_response(204)
        origin = self._cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _health_payload(self, include_token: bool = False) -> Dict[str, Any]:
        checkpoint = str(POLICY.checkpoint) if POLICY is not None else None
        cfg = POLICY.cfg.__dict__ if POLICY is not None else None
        identity = dict(RUNTIME)
        if include_token:
            identity["token"] = local_secret()
        return {
            "status": "ok" if POLICY is not None else "loading",
            "model": "encounter_ppo_pbt_actor",
            "checkpoint": checkpoint,
            "config": cfg,
            # Not a label: a health endpoint has to name the array library the process is
            # actually computing with, and the fallback is a real backend now, not a lack of
            # one. Only when no policy loaded at all is the answer "solver-only".
            "backend": _policy_backend() if POLICY is not None
                       else "solver-only (no array backend installed)",
            # What the live learner has picked up so far. Reported here rather
            # than behind a separate endpoint because "is it learning?" is the
            # first question asked of a brain that is supposed to improve, and a
            # number nobody looks at is a number that stays wrong.
            "learning": _learning_report(),
            # The per-monster half of the same question. The solver reads
            # this table to price a cell, so a run where it stays at zero
            # rows is a run where every cell is still priced by the level
            # prior -- which is exactly what "he walks into the enemy"
            # looks like from the inside.
            "threat": _threat_report(),
            # The third learner: what the solver said a spell would do versus
            # what the board said it did. `writes` staying at zero means the
            # damage correction the solver applies is still the prior.
            "shadow": _shadow_report(),
            # Who is answering. A socket that replies only proves *a* server exists; on
            # 2026-11-04 the server answering :8891 was started 50 minutes and three app
            # restarts earlier, on the dev interpreter instead of the shipped one. The run
            # token, pid, interpreter and checkpoint hash are what let the manager tell its
            # own brain from a leftover one -- and the leftover is the one that must die.
            # The token is only included for a caller that already proved it belongs here.
            "identity": identity,
        }

    def _stream_health(self) -> None:
        """Server-sent events: one health snapshot per second until the reader leaves.

        The panel used to poll `/brain/status` on a one-second timer, so every status
        it showed was up to a second old and every poll opened a new socket against a
        daemon that is CPU-bound under the GIL. An event stream makes the update and
        the read the same operation, and the `server_ms` it carries lets the reader
        report a real transport latency instead of guessing from its own clock.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        origin = self._cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.close_connection = True
        # Flushed once, before the first event, so a proxy or a client that waits
        # for the body to start does not sit on a buffer until the socket closes.
        try:
            self.wfile.write(b": stream\n\n")
            self.wfile.flush()
        except OSError:
            return
        while True:
            payload = self._health_payload(include_token=True)
            payload["server_ms"] = int(time.time() * 1000)
            try:
                self.wfile.write(b"data: " + dumps_bytes(payload) + b"\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
            time.sleep(STREAM_INTERVAL_S)

    def do_GET(self):
        if self.path == "/health":
            self._reply(self._health_payload(include_token=self._authorized(require_token=True)))
            return
        if self.path in ("/v1/combat/profile", "/profile"):
            self._reply(profile_payload())
            return
        if self.path == "/health/stream":
            # Same authorisation as /health: the identity block includes the run
            # token, so an unauthenticated reader must not receive the stream.
            if not self._authorized(require_token=True):
                self._reply({"ok": False, "error": "forbidden"}, 403)
                return
            self._stream_health()
            return
        self._reply({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        if self.path in ("/v1/combat/reset", "/profile/reset"):
            with _LATENCY_LOCK:
                _LATENCY_HISTORY.clear()
            self._reply({"status": "ok", "cleared": True})
            return
        if self.path != "/v1/combat/decide":
            self._reply({"ok": False, "error": "not found"}, 404)
            return
        if not self._authorized():
            self._reply({"ok": False, "action": None,
                         "reason": "unauthorized"}, 401)
            return
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            self._reply({"ok": False, "action": None,
                         "reason": "unsupported_media_type"}, 415)
            return
        if not _rate_ok():
            self._reply({"ok": False, "action": None,
                         "reason": "rate_limited"}, 429)
            return
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            self._reply({"ok": False, "action": None,
                         "reason": "bad_request:invalid_content_length"}, 400)
            return
        if length > 8 * 1024 * 1024:
            self._reply({"ok": False, "action": None,
                         "reason": "bad_request:body_too_large"}, 413)
            return
        raw = self.rfile.read(max(0, length))
        try:
            payload = loads(raw) if raw else {}
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            self._reply(decide(payload))
        except Exception:  # noqa: BLE001 - the page falls back on error
            self._reply({"ok": False, "action": None,
                         "reason": "bad_request"}, 400)

    def log_message(self, format, *args):
        pass


class ExclusiveHTTPServer(ThreadingHTTPServer):
    """Binds WITHOUT SO_REUSEADDR, so a shared port fails instead of silently splitting.

    `HTTPServer.allow_reuse_address` is True by default, and on Windows SO_REUSEADDR means a
    second listener may bind the same port. That is exactly how a leftover brain from an earlier
    app run went on advising fights while the current run believed its own server was answering
    (:8895 hit this before -- see `agent/serve.py:65` and `aipanel.cs:1501`). Two servers with two
    instance tables is not high availability: it is undefined behaviour with a healthy /health.
    """

    allow_reuse_address = False
    daemon_threads = True


DEFAULT_BRAIN_PORT = int(os.environ.get("DOFUS_BRAIN_PORT", "8891"))
DEFAULT_BRAIN_HOST = os.environ.get("DOFUS_BRAIN_HOST", "127.0.0.1")


def run_server(port: int = DEFAULT_BRAIN_PORT,
               host: str = DEFAULT_BRAIN_HOST,
               checkpoint: Optional[Path] = None,
               beam_width: int = 8,
               max_actions: int = 4,
               log_path: Optional[Path] = None) -> None:
    global POLICY, LOG_PATH
    if log_path is not None:
        new_path = Path(log_path)
        if new_path != LOG_PATH:
            with _LOG_LOCK:
                _close_log_locked()
            LOG_PATH = new_path
    logger.debug("==========================================================")
    logger.debug("   DOFUS LIVE COMBAT POLICY API (PPO/PBT ACTOR)          ")
    logger.debug("==========================================================")
    checkpoint = Path(checkpoint or DEFAULT_CHECKPOINT)
    logger.debug(f"checkpoint: {checkpoint}")
    try:
        from combat.live_policy import LivePolicy
    except ImportError as exc:      # the refusal the handlers already report as "loading"
        POLICY = None
        logger.debug(f"[WARN] learned policy unavailable: {type(exc).__name__}: {exc}")
        logger.debug("       serving the solver-only path; /health reports status=loading")
    else:
        if not tracemalloc.is_tracing():
            tracemalloc.start(1)
        POLICY = LivePolicy(checkpoint=checkpoint, beam_width=beam_width,
                            max_actions=max_actions)
        logger.debug(f"slots: {POLICY.cfg.spells} spells | {POLICY.cfg.enemies} enemies")
        logger.debug(f"decision log: {LOG_PATH}")
        logger.debug("warming up the policy and solver...")
        try:
            POLICY.warmup()
            # Also warm up server-level learner and threat observers with a sample board
            decide({
                "in_fight": True, "in_preparation": False, "is_my_turn": True,
                "my_id": 1, "my_cell": 100, "map_id": 84674566, "round": 1, "fight_id": 0,
                "cells": [3] * 560,
                "my_stats": {"strength": 100, "intelligence": 0, "chance": 0,
                             "agility": 0, "power": 0, "damage": 0,
                             "critical_hit": 0, "heals": 0},
                "spells": [{"id": 1, "name": "warmup", "ap_cost": 2,
                            "min_range": 1, "range": 4, "requires_los": True,
                            "castable": True, "casts_this_turn": 0,
                            "max_cast_per_turn": 2, "max_cast_per_target": 2,
                            "effects": [{"effect_id": 100, "dice_num": 5, "dice_side": 5}],
                            "critical_effects": []}],
                "fighters": [
                    {"id": 1, "name": "me", "cell": 100, "team": "player",
                     "is_me": True, "alive": True, "hp": 100, "max_hp": 100,
                     "ap": 6, "max_ap": 6, "mp": 3, "max_mp": 3, "level": 10,
                     "resist_percent": {}, "resist_fixed": {}, "spells": []},
                    {"id": 2, "name": "foe", "cell": 101, "team": "enemy",
                     "is_me": False, "alive": True, "hp": 30, "max_hp": 30,
                     "ap": 4, "max_ap": 4, "mp": 2, "max_mp": 2, "level": 5,
                     "resist_percent": {}, "resist_fixed": {},
                     "spells": []},
                ],
            })
            top_allocators(20)
            if tracemalloc.is_tracing():
                tracemalloc.stop()
            with _LATENCY_LOCK:
                _LATENCY_HISTORY.clear()
            logger.debug("warm-up done")
        except Exception as exc:  # noqa: BLE001 - a warm-up must not block the API
            logger.debug(f"warm-up failed: {type(exc).__name__}: {exc}")
    try:
        server = ThreadingHTTPServer((host, int(port)), CombatRequestHandler)
    except OSError as exc:
        logger.debug(f"[ERROR] cannot bind 127.0.0.1:{port}: {exc}")
        logger.debug(f"Stop the process already using 127.0.0.1:{port}, or start this "
              "one on another port and set window.__agentFightBrain to that URL.")
        raise SystemExit(1)
    server.daemon_threads = True
    logger.debug(f"[OK] listening on http://127.0.0.1:{port}")
    logger.debug("  GET  /health")
    logger.debug("  POST /v1/combat/decide")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.debug("\nshutting down")
        server.shutdown()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", nargs="?", type=int, default=8891)
    parser.add_argument("--checkpoint", default=str(DEFAULT_CHECKPOINT))
    parser.add_argument("--beam-width", type=int, default=8)
    parser.add_argument("--max-actions", type=int, default=4)
    parser.add_argument("--log", default=str(
        REPO_ROOT / "trajectories" / "live_policy_decisions.jsonl"))
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    run_server(port=args.port, checkpoint=Path(args.checkpoint),
               beam_width=args.beam_width, max_actions=args.max_actions,
               log_path=Path(args.log))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
