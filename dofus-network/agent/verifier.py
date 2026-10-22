"""The second agent: verify what the first agent did.

This is the piece the design called for and the code did not have. It is
deliberately **model-optional**: the loop must run, fail, and produce labels
today, offline training and online learning come later. So the same interface
serves three sources, in order of availability:

    rule    the heuristic that used to live inside autopilot (always available)
    model   an ONNX verifier head, if one exists (ai/verifier_*.onnx)
    human   a hand-written verdict from a reviewer, which wins over both

Why rules first is not a compromise: the rule label is the *baseline* the model
has to beat, and every disagreement between the two is a training sample. A
verifier that starts as rules and accumulates model/human overrides is strictly
more useful than one that waits for a dataset.

Labels emitted per step (the schema the training data will use):

    verdict   advanced | noop | failed | blocked   did the world change?
    residual  seconds late vs the tool's own estimate, when it has one
    drift     the observation contradicts what the step assumed

Human-in-the-loop:

    python -m agent.verifier --label <session> <index> <verdict> [note]
    python -m agent.verifier --stats

writes ``verdicts_human.jsonl`` next to the autopilot log; the loop reads it on
the next run and prefers it over rule/model for that step.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HUMAN = Path(
    os.environ.get("LOCALAPPDATA", str(Path.home()))
) / "DofusFarm" / "verdicts_human.jsonl"

VERDICTS = ("advanced", "noop", "failed", "blocked")

# Observation keys that mean "the world moved". Kept in one place so the rule,
# the dataset builder and the model features cannot drift apart.
STATE_KEYS = ("cell", "map_id", "mapId", "kamas", "xp", "hp", "inventory_size")

# Tools that are *supposed* to change the map / the cell. Used by drift only.
MOVE_TOOLS = ("farm.step", "farm.start", "nav.walk", "nav.goto", "travel.zaap")


# Fixed tool order for the feature vector. Must match data/capabilities.json;
# a name outside this list maps to the "other" bucket instead of crashing.
TOOL_ORDER = (
    "knowledge.search", "nav.goto", "nav.follow", "quest.list", "quest.advance",
    "farm.start", "farm.stop", "fight.join", "fight.pass", "inv.list", "inv.sell",
    "hdv.scan", "hdv.list", "chat.say", "state.snapshot", "safe.stop",
)


def features(tool: str, ok: bool, error: Optional[str],
             before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]],
             latency_ms: float = 0.0) -> list[float]:
    """The verifier's input vector, in pure Python.

    Shared by the trainer and the runtime on purpose: two implementations of a
    feature set is how train/serve skew happens. Everything here is derivable
    from a logged step, so an example and a live step produce the same numbers.
    """
    b = before if isinstance(before, dict) else {}
    a = after if isinstance(after, dict) else {}

    def num(d, k, default=0.0):
        v = d.get(k)
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    err = str(error or "")
    vec: list[float] = []

    # tool identity
    idx = TOOL_ORDER.index(tool) if tool in TOOL_ORDER else len(TOOL_ORDER)
    for i in range(len(TOOL_ORDER) + 1):
        vec.append(1.0 if i == idx else 0.0)

    # outcome shape
    vec.append(1.0 if ok else 0.0)
    vec.append(1.0 if err else 0.0)
    vec.append(1.0 if "not_mapped" in err else 0.0)
    vec.append(1.0 if ("timeout" in err.lower() or "reset" in err.lower()) else 0.0)

    # timing
    import math
    vec.append(math.log1p(max(0.0, float(latency_ms or 0.0))))
    vec.append(1.0 if (latency_ms or 0) > 2500 else 0.0)

    # state deltas
    cell_d = num(a, "cell") - num(b, "cell") if ("cell" in b and "cell" in a) else 0.0
    map_d = 1.0 if (b.get("map_id") is not None and a.get("map_id") is not None
                    and b.get("map_id") != a.get("map_id")) else 0.0
    kamas_d = num(a, "kamas") - num(b, "kamas") if ("kamas" in b and "kamas" in a) else 0.0
    xp_d = num(a, "xp") - num(b, "xp") if ("xp" in b and "xp" in a) else 0.0
    hp_d = num(a, "hp") - num(b, "hp") if ("hp" in b and "hp" in a) else 0.0
    inv_d = num(a, "inventory_size") - num(b, "inventory_size") if (
        "inventory_size" in b and "inventory_size" in a) else 0.0

    vec.extend([
        1.0 if cell_d != 0 else 0.0,
        1.0 if cell_d > 0 else 0.0,
        map_d,
        1.0 if kamas_d > 0 else 0.0,
        1.0 if kamas_d < 0 else 0.0,
        1.0 if xp_d > 0 else 0.0,
        1.0 if hp_d < 0 else 0.0,
        1.0 if inv_d != 0 else 0.0,
        1.0 if (not b or not a) else 0.0,
    ])

    changed = sum(1 for k in STATE_KEYS if k in b or k in a if b.get(k) != a.get(k))
    vec.append(min(changed, 6) / 6.0)
    return vec


@dataclass
class Verdict:
    verdict: str
    confidence: float
    residual_s: Optional[float] = None
    drift: bool = False
    source: str = "rule"
    reasons: Tuple[str, ...] = ()

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _get(obs: Optional[Dict[str, Any]], key: str) -> Any:
    if not isinstance(obs, dict):
        return None
    return obs.get(key)


def _changed(before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for k in STATE_KEYS:
        b, a = _get(before, k), _get(after, k)
        if b is None and a is None:
            continue
        if b != a:
            out.append(k)
    return out


def _int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


class HumanLabels:
    """Hand-written verdicts, keyed by (session, step index).

    Append-only JSONL so a reviewer can work while the bot runs, and so nothing
    is lost if the process dies mid-review.
    """

    def __init__(self, path: Path = DEFAULT_HUMAN) -> None:
        self.path = Path(path)
        self._cache: Dict[Tuple[str, int], Verdict] = {}
        self._loaded_mtime: float = -1.0

    def _reload_if_changed(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return
        if mtime == self._loaded_mtime:
            return
        self._loaded_mtime = mtime
        self._cache.clear()
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    verdict = row.get("verdict")
                    if verdict not in VERDICTS:
                        continue
                    key = (str(row.get("session", "")), int(row.get("index", -1)))
                    self._cache[key] = Verdict(
                        verdict=verdict,
                        confidence=1.0,
                        source="human",
                        reasons=("human:" + str(row.get("note", "")),) if row.get("note") else ("human",),
                    )
        except OSError:
            pass

    def get(self, session: str, index: int) -> Optional[Verdict]:
        self._reload_if_changed()
        return self._cache.get((session, index))

    def add(self, session: str, index: int, verdict: str, note: str = "") -> None:
        if verdict not in VERDICTS:
            raise ValueError("verdict must be one of %s" % (VERDICTS,))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = {"session": session, "index": int(index), "verdict": verdict,
               "note": note, "at": time.time()}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._loaded_mtime = -1.0
        self._reload_if_changed()

    def count(self) -> int:
        self._reload_if_changed()
        return len(self._cache)


class Verifier:
    """Rules now, model when it exists, human always on top."""

    def __init__(self, model_path: Optional[Path] = None,
                 human_path: Path = DEFAULT_HUMAN,
                 veto_threshold: float = 0.0) -> None:
        self.human = HumanLabels(human_path)
        self.veto_threshold = float(veto_threshold)
        self._session: Any = None
        self._baseline: Optional[Dict[str, Any]] = None
        self._onnx_input: str = "features"
        self.model_min_confidence = 0.5
        self.model_error = ""
        self._model_note = "no verifier model (rules only)"
        if model_path:
            self._session = self._load_model(Path(model_path))

    # -- model -------------------------------------------------------------

    def _load_model(self, path: Path) -> Any:
        """Two artifact kinds, same interface.

        ``.json`` is the pure-Python baseline (``ai/train_verifier_baseline.py``)
        and needs no runtime dependency at all. ``.onnx`` is the neural head, used
        when onnxruntime is installed. Anything else is ignored with a note, so a
        missing model degrades to rules instead of stopping the loop.
        """
        self._baseline = None
        try:
            if path.suffix.lower() == ".json":
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("kind") != "verifier-baseline":
                    self._model_note = "%s is not a verifier baseline (rules only)" % path.name
                    return None
                self._baseline = data
                val = (data.get("metrics") or {}).get("val") or {}
                self._model_note = "baseline %s (val acc %s)" % (
                    path.name, val.get("accuracy", "untrained"))
                return "baseline"

            import onnxruntime as ort  # optional dependency
            if not path.exists():
                self._model_note = "model not found: %s (rules only)" % path
                return None
            session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
            self._onnx_input = session.get_inputs()[0].name
            self._model_note = "onnx %s (%d features)" % (path.name, session.get_inputs()[0].shape[-1])
            return session
        except Exception as exc:  # noqa: BLE001 - a missing model must not stop the loop
            self._model_note = "model unavailable (%s); rules only" % exc
            return None

    @property
    def model_note(self) -> str:
        return self._model_note

    def _model_label(self, tool: str, ok: bool, error: Optional[str],
                     before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]],
                     latency_ms: float) -> Optional[Verdict]:
        """Model opinion, or None when there is no model (or it is unsure).

        Returning None on low confidence is deliberate: the rules are a decent
        baseline, and a linear model's 0.35 is not a reason to overrule them.
        """
        # `_session` is the ONNX session or the string "baseline"; only a real
        # session has run(). Checking `is not None` alone sent the baseline down
        # the ONNX path, where it raised and silently fell back to the rules.
        if self._session is not None and hasattr(self._session, "run"):
            try:
                import numpy as np
                x = np.asarray([features(tool, ok, error, before, after, latency_ms)],
                               dtype="float32")
                logits = self._session.run(None, {self._onnx_input: x})[0][0]
                m = float(max(logits))
                ex = [math.exp(float(z) - m) for z in logits]
                total = sum(ex) or 1.0
                probs = [e / total for e in ex]
                best = max(range(len(probs)), key=lambda i: probs[i])
                if probs[best] < self.model_min_confidence:
                    return None
                return Verdict(VERDICTS[best], round(probs[best], 3), None, False, "model",
                               ("onnx p=%.2f" % probs[best],))
            except Exception:
                return None

        if self._baseline is None:
            return None
        try:
            x = features(tool, ok, error, before, after, latency_ms)
            w = self._baseline["w"]
            b = self._baseline["b"]
            classes = self._baseline.get("classes") or list(VERDICTS)
            logits = [sum(wi * xi for wi, xi in zip(row, x)) + bi for row, bi in zip(w, b)]
            m = max(logits)
            ex = [math.exp(z - m) for z in logits]
            total = sum(ex) or 1.0
            probs = [e / total for e in ex]
            best = max(range(len(probs)), key=lambda i: probs[i])
            conf = probs[best]
            if conf < self.model_min_confidence:
                return None
            return Verdict(classes[best], round(conf, 3), None, False, "model",
                           ("model p=%.2f" % conf,))
        except Exception as exc:
            self.model_error = repr(exc)
            return None

    # -- public ------------------------------------------------------------

    def label(self, tool: str, args: Optional[Dict[str, Any]], ok: bool,
              error: Optional[str], before: Optional[Dict[str, Any]],
              after: Optional[Dict[str, Any]], latency_ms: float = 0.0,
              expected_s: Optional[float] = None,
              session: str = "", index: int = -1) -> Verdict:
        human = self.human.get(session, index) if session and index >= 0 else None
        if human is not None:
            return human

        model = self._model_label(tool, ok, error, before, after, latency_ms)
        if model is not None:
            return model

        return self._rule(tool, args, ok, error, before, after, latency_ms, expected_s)

    def _rule(self, tool: str, args: Optional[Dict[str, Any]], ok: bool,
              error: Optional[str], before: Optional[Dict[str, Any]],
              after: Optional[Dict[str, Any]], latency_ms: float,
              expected_s: Optional[float]) -> Verdict:
        residual = None
        if expected_s is not None and latency_ms > 0:
            residual = round(latency_ms / 1000.0 - float(expected_s), 2)

        if not ok:
            err = str(error or "")
            if "not_mapped" in err:
                return Verdict("blocked", 0.9, residual, False, "rule",
                               ("tool not mapped to a client message",))
            return Verdict("failed", 0.85, residual, False, "rule",
                           (err[:120] or "call returned not-ok",))

        changed = _changed(before, after)
        drift, why = self._drift(tool, before, after, changed)
        if changed:
            return Verdict("advanced", 0.8, residual, drift, "rule",
                           ("changed: " + ", ".join(changed),) + why)
        return Verdict("noop", 0.6, residual, drift, "rule",
                       ("call succeeded but no observable state changed",) + why)

    def _drift(self, tool: str, before: Optional[Dict[str, Any]],
               after: Optional[Dict[str, Any]], changed: Iterable[str]) -> Tuple[bool, Tuple[str, ...]]:
        """Cheap contradictions only. Each one has to be explainable to a human."""
        reasons: list[str] = []
        if not isinstance(before, dict) or not isinstance(after, dict):
            return False, ()

        # 1. The observation lost fields it had before: the client is not
        #    reporting what the previous step assumed.
        missing = [k for k in STATE_KEYS if k in before and k not in after]
        if missing:
            reasons.append("observation lost: " + ", ".join(missing))

        # 2. Kamas went down without a spending tool.
        if tool not in ("hdv.buy",) and _int(_get(before, "kamas"), -1) > _int(_get(after, "kamas"), -1) >= 0:
            reasons.append("kamas dropped without a purchase")

        # 3. The map moved on a tool that cannot move the character.
        if tool not in MOVE_TOOLS and _get(before, "map_id") != _get(after, "map_id") \
                and _get(before, "map_id") is not None and _get(after, "map_id") is not None:
            reasons.append("map changed on a non-movement tool")

        return (len(reasons) > 0), tuple(reasons)

    # -- pre-flight --------------------------------------------------------

    def preflight(self, tool: str, args: Optional[Dict[str, Any]],
                  obs: Optional[Dict[str, Any]]) -> Optional[Verdict]:
        """A veto before the call is sent.

        Only a *model* can veto: the rules cannot predict an outcome, and letting
        a heuristic block actions would just add a second, worse policy. With no
        model this returns None and the loop behaves exactly as before.
        """
        if self._session is None or self.veto_threshold <= 0:
            return None
        guess = self._model_label(tool, True, None, obs, None, 0.0)
        if guess is None:
            return None
        if guess.verdict in ("failed", "blocked") and guess.confidence >= self.veto_threshold:
            return guess
        return None


def default_log() -> Path:
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DofusFarm" / "autopilot.jsonl"
    if local.exists():
        return local
    return REPO_ROOT / "trajectories" / "autopilot.jsonl"


def read_steps(path: Path) -> list[dict]:
    rows: list[dict] = []
    try:
        with Path(path).open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return rows


def sessions(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in rows:
        sid = str(row.get("session") or "unknown")
        entry = out.setdefault(sid, {"n": 0, "verdicts": {}, "tools": set(), "last": 0.0})
        entry["n"] += 1
        v = str(row.get("verdict") or "?")
        entry["verdicts"][v] = entry["verdicts"].get(v, 0) + 1
        if row.get("tool"):
            entry["tools"].add(str(row["tool"]))
        entry["last"] = max(entry["last"], float(row.get("at") or 0))
    return out


def _main(argv: list[str]) -> int:
    args = list(argv)
    human = HumanLabels()

    log = default_log()
    if "--log" in args:
        i = args.index("--log")
        if i + 1 < len(args):
            log = Path(args[i + 1])

    if "--sessions" in args:
        rows = read_steps(log)
        if not rows:
            print("no steps in %s" % log)
            return 0
        print("%-10s %6s  %-46s %s" % ("session", "steps", "verdicts", "tools"))
        for sid, e in sorted(sessions(rows).items(), key=lambda kv: -kv[1]["last"]):
            vd = " ".join("%s=%d" % (k, v) for k, v in sorted(e["verdicts"].items()))
            print("%-10s %6d  %-46s %s" % (sid, e["n"], vd, ",".join(sorted(e["tools"]))[:40]))
        print("\nlog: %s" % log)
        return 0

    if "--show" in args:
        i = args.index("--show")
        if i + 1 >= len(args):
            print("usage: --show <session> [n]")
            return 2
        sid = args[i + 1]
        limit = 20
        if len(args) > i + 2:
            try:
                limit = int(args[i + 2])
            except ValueError:
                pass
        rows = [r for r in read_steps(log) if str(r.get("session")) == sid]
        if not rows:
            print("no steps for session %s in %s" % (sid, log))
            return 0
        for r in rows[-limit:]:
            print("%4s  %-16s %-8s %-5s %s" % (
                r.get("index"), r.get("tool"), r.get("verdict"),
                r.get("source", "?"), (r.get("note") or "")[:60]))
        print("\nlabel one:  python -m agent.verifier --label %s <index> <verdict> \"why\"" % sid)
        return 0

    if "--stats" in args or not args:
        print("human labels: %d in %s" % (human.count(), human.path))
        print("steps log:    %s" % log)
        rows = read_steps(log)
        if rows:
            print("sessions:     %d (%d steps)" % (len(sessions(rows)), len(rows)))
        return 0

    if "--label" in args:
        i = args.index("--label")
        rest = args[i + 1:]
        if len(rest) < 3:
            print("usage: --label <session> <index> <verdict> [note]")
            return 2
        session, index, verdict = rest[0], int(rest[1]), rest[2]
        note = " ".join(rest[3:]) if len(rest) > 3 else ""
        human.add(session, index, verdict, note)
        print("recorded %s/%s -> %s" % (session, index, verdict))

        # Point at the next step that still has only a rule label: that is the
        # queue a reviewer actually works through.
        rows = [r for r in read_steps(log) if str(r.get("session")) == session]
        pending = [r for r in rows if r.get("source") != "human"]
        if pending:
            nxt = pending[0]
            print("next unlabeled: %s (%s, %s)" % (nxt.get("index"), nxt.get("tool"), nxt.get("verdict")))
        else:
            print("session %s is fully reviewed" % session)
        return 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
