"""Trajectory recorder — JSONL log of every observation, action and result.

This log is the single raw material for:

* SFT data (observation -> tool call with the real outcome attached),
* GRPO rollouts (reward can be recomputed from recorded outcomes),
* the semantic simulator (learned action dynamics),
* on-policy distillation (the teacher scores real student rollouts).

Privacy: account identifiers and secrets are redacted before anything touches
disk (see ``REDACT_KEYS``). Rotation keeps files bounded so a farm fleet can
run for weeks without filling the disk.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.jsonio import dumps_bytes as _fast_dumps
from agent.jsonio import loads as _fast_loads

logger = logging.getLogger("dofus.agent.recorder")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = REPO_ROOT / "trajectories"

# Exact key names (case-insensitive) whose values must never reach disk.
REDACT_KEYS = {
    "account", "account_name", "accountname", "password", "pass", "passwd",
    "token", "access_token", "refresh_token", "email", "login", "apikey",
    "api_key", "secret", "authorization", "auth", "credential", "credentials",
    "proxy", "proxy_url", "cookie", "session",
}
_REDACTED = "[redacted]"
_URL_CREDENTIALS = re.compile(r"://[^/\s:@]+:[^/\s@]+@")


@lru_cache(maxsize=1024)
def _is_secret_key(key: str) -> bool:
    return key.strip().lower() in REDACT_KEYS


def _contains_secret(value: Any) -> bool:
    """Cheap read-only scan before the allocating redaction pass.

    Most observations contain no secret at all, so the common path should not
    rebuild tens of thousands of inventory nodes. The scan checks exactly the
    conditions `_redact` acts on.
    """
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, child in item.items():
                if isinstance(key, str):
                    # Exact lowercase names are the common case; skip the
                    # cache/function call for them. Numeric keys are item ids
                    # and can never equal a redaction name. Only mixed-case or
                    # padded keys need the normalising fallback.
                    if key in REDACT_KEYS:
                        return True
                    if not key.isdigit() and (
                            not key.islower() or key.strip() != key):
                        if _is_secret_key(key):
                            return True
                if isinstance(child, (dict, list)):
                    stack.append(child)
                elif (isinstance(child, str) and "://" in child
                      and "@" in child and ":" in child
                      and _URL_CREDENTIALS.search(child)):
                    return True
        elif isinstance(item, list):
            for child in item:
                if isinstance(child, (dict, list)):
                    stack.append(child)
                elif (isinstance(child, str) and "://" in child
                      and "@" in child and ":" in child
                      and _URL_CREDENTIALS.search(child)):
                    return True
    return False


def _redact(value: Any) -> Any:
    """Recursively redact secrets. Strings that embed credentials in URLs
    (e.g. ``http://user:pass@host``) are scrubbed too.

    Returns the original object when nothing was redacted. Observations are
    large (an inventory alone can be hundreds of KB), so the caller first runs
    the read-only scan and skips this allocating pass entirely when it is safe.
    """
    if not _contains_secret(value):
        return value
    return _redact_copy(value)


def _redact_copy(value: Any) -> Any:
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        changed = False
        for key, item in value.items():
            if isinstance(key, str) and _is_secret_key(key):
                out[key] = _REDACTED
                changed = True
                continue
            redacted = _redact_copy(item)
            if redacted is not item:
                changed = True
            out[key] = redacted
        return out if changed else value
    if isinstance(value, list):
        out = []
        changed = False
        for item in value:
            redacted = _redact_copy(item)
            if redacted is not item:
                changed = True
            out.append(redacted)
        return out if changed else value
    if isinstance(value, str):
        if "://" in value and "@" in value and ":" in value:
            scrubbed = _URL_CREDENTIALS.sub("://[redacted]@", value)
            return scrubbed if scrubbed != value else value
        return value
    return value


class TrajectoryRecorder:
    """Append-only JSONL recorder with rotation, redaction and light stats."""

    def __init__(
        self,
        root: Path = DEFAULT_ROOT,
        instance: str = "default",
        max_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        safe_instance = re.sub(r"[^A-Za-z0-9_.-]+", "_", instance) or "default"
        self.instance = safe_instance
        self.max_bytes = max_bytes
        self.path = self.root / f"{safe_instance}.jsonl"
        self._checkpoint_path = self.path.with_name(self.path.name + ".stats.json")
        self._scan_pos = 0
        # Maintained in memory: a stat() per append was one syscall per step
        # just to decide whether to rotate.
        self._size = 0
        self._writes_since_checkpoint = 0
        self._lock = threading.Lock()
        self._counters: Dict[str, int] = {"lines": 0, "ok": 0, "fail": 0}
        self._tool_counts: Dict[str, int] = {}
        self._scan_existing()

    # -- internals -----------------------------------------------------------

    def _scan_existing(self) -> None:
        """Rebuild counters from the last checkpoint plus the new tail.

        A 64 MB trajectory is mostly observation rows. Loading and parsing all
        of it on every process start was pure repeated work; the sidecar stores
        the byte offset and counters already accounted for, so a restart reads
        only the records written since the last checkpoint.
        """
        size = self.path.stat().st_size if self.path.exists() else 0
        self._size = size
        checkpoint = self._read_checkpoint()
        offset = 0
        if (isinstance(checkpoint, dict)
                and checkpoint.get("instance") == self.instance):
            try:
                offset = int(checkpoint.get("size") or 0)
            except (TypeError, ValueError):
                offset = -1
            if 0 <= offset <= size:
                counters = checkpoint.get("counters") or {}
                tools = checkpoint.get("tools") or {}
                if isinstance(counters, dict):
                    for key in self._counters:
                        try:
                            self._counters[key] = int(counters.get(key, 0) or 0)
                        except (TypeError, ValueError):
                            self._counters[key] = 0
                if isinstance(tools, dict):
                    self._tool_counts = {
                        str(key): int(value or 0) for key, value in tools.items()
                    }
            else:
                offset = 0
        if offset == 0 and size == 0:
            return
        if offset == size:
            self._scan_pos = offset
            return
        self._scan_from(offset)
        self._size = max(size, self._scan_pos)

    def _read_checkpoint(self) -> Optional[Dict[str, Any]]:
        try:
            payload = _fast_loads(self._checkpoint_path.read_bytes())
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def _save_checkpoint(self) -> None:
        payload = {
            "instance": self.instance,
            "size": self._scan_pos,
            "counters": dict(self._counters),
            "tools": dict(self._tool_counts),
        }
        tmp = self._checkpoint_path.with_name(self._checkpoint_path.name + ".tmp")
        try:
            tmp.write_bytes(_fast_dumps(payload))
            tmp.replace(self._checkpoint_path)
        except OSError:
            pass

    def _scan_from(self, offset: int) -> None:
        try:
            with self.path.open("rb") as handle:
                handle.seek(offset)
                for raw in handle:
                    self._scan_line(raw)
                self._scan_pos = handle.tell()
        except OSError as exc:
            logger.warning(f"Could not scan existing trajectory file: {exc}")
            return
        self._save_checkpoint()

    def _scan_line(self, raw: Any) -> None:
        if isinstance(raw, (bytes, bytearray)):
            line = bytes(raw).decode("utf-8", "replace")
        else:
            line = str(raw)
        if not line or line in ("\n", "\r\n"):
            return
        self._counters["lines"] += 1
        # Observation and decision rows dominate the file and are large; only
        # step rows contribute to the tool counters. The marker check covers
        # compact orjson rows and older stdlib rows without parsing the rest.
        step_row = ('"kind":"step"' in line
                    or '"kind": "step"' in line
                    or ('"kind"' in line and '"step"' in line))
        if not step_row:
            return
        try:
            record = _fast_loads(line)
        except ValueError:
            return  # a torn tail line from a crash is skipped, not fatal
        self._count_step(record)

    def _count(self, record: Dict[str, Any]) -> None:
        self._counters["lines"] += 1
        self._count_step(record)

    def _count_step(self, record: Dict[str, Any]) -> None:
        if record.get("kind") != "step":
            return
        tool = str(record.get("tool", "?"))
        self._tool_counts[tool] = self._tool_counts.get(tool, 0) + 1
        if record.get("ok"):
            self._counters["ok"] += 1
        else:
            self._counters["fail"] += 1

    def _rotate_if_needed(self) -> None:
        try:
            if self._size >= self.max_bytes:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
                rotated = self.path.with_name(f"{self.path.stem}.{stamp}.jsonl")
                # Two rotations can land in the same second when a test (or a
                # very fast producer) writes several small files. Rename would
                # fail on Windows and the file would keep growing past the cap.
                suffix = 0
                while rotated.exists():
                    suffix += 1
                    rotated = self.path.with_name(
                        f"{self.path.stem}.{stamp}-{suffix}.jsonl")
                self.path.rename(rotated)
                # Counters stay cumulative across files; the new file starts at
                # byte zero for the next checkpoint.
                self._scan_pos = 0
                self._size = 0
                self._writes_since_checkpoint = 0
                self._save_checkpoint()
                logger.info(f"Rotated trajectory log to {rotated.name}")
        except OSError as exc:
            logger.warning(f"Trajectory rotation failed: {exc}")

    # -- public API ----------------------------------------------------------

    def log(self, kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Append one record. Returns the record as written (post-redaction)."""
        record: Dict[str, Any] = {
            "ts": round(time.time(), 3),
            "iso": datetime.now(timezone.utc).isoformat(),
            "instance": self.instance,
            "kind": kind,
        }
        record.update(_redact(payload))
        blob = _fast_dumps(record) + b"\n"
        with self._lock:
            self._rotate_if_needed()
            try:
                with self.path.open("ab") as handle:
                    handle.write(blob)
            except OSError as exc:
                logger.error(f"Failed to write trajectory record: {exc}")
                return record
            self._scan_pos += len(blob)
            self._size += len(blob)
            self._count(record)
            self._writes_since_checkpoint += 1
            if self._writes_since_checkpoint >= 32:
                self._save_checkpoint()
                self._writes_since_checkpoint = 0
        return record

    def observation(
        self,
        observation: Dict[str, Any],
        goal: Optional[str] = None,
        source: str = "live",
    ) -> Dict[str, Any]:
        return self.log("observation", {
            "goal": goal,
            "source": source,
            "observation": observation,
        })

    def step(
        self,
        tool: str,
        args: Dict[str, Any],
        ok: bool,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        latency_ms: Optional[float] = None,
        goal: Optional[str] = None,
        observation: Optional[Dict[str, Any]] = None,
        observation_age_ms: Optional[float] = None,
        observation_source: str = "call",
        source: str = "live",
        explored: bool = False,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Append one executed step.

        ``observation`` is the game state the decision was made against.
        ``observation_age_ms`` is how old that snapshot was when the call was
        made, and ``observation_source`` says where it came from:

        ``"call"``
            the caller passed it with this very request
        ``"carried"``
            it is the most recent snapshot pushed to ``/agent/observe`` (or to
            ``handle_goal``) and reused for this step

        Both fields exist because an observation that is not fresh is a
        *different* training example: pairing a stale snapshot with the action
        taken later teaches the model to ignore the observation. A consumer
        that wants clean observation->action pairs filters on
        ``observation_age_ms``; without the age recorded, that filter cannot be
        written and the staleness is invisible.
        """
        return self.log("step", {
            "goal": goal,
            "source": source,
            "observation": observation,
            "observation_age_ms": (round(observation_age_ms, 1)
                                   if observation_age_ms is not None else None),
            "observation_source": observation_source,
            "tool": tool,
            "args": args,
            "ok": ok,
            "error": error,
            "latency_ms": round(latency_ms, 2) if latency_ms is not None else None,
            "result": result,
            "explored": bool(explored),
            "meta": meta if isinstance(meta, dict) and meta else None,
        })

    def decision(
        self,
        action: str,
        cause: str,
        reason: str,
        tool: Optional[str] = None,
        error: Optional[str] = None,
        explored: bool = False,
        confidence: float = 0.5,
        goal: Optional[str] = None,
        observation: Optional[Dict[str, Any]] = None,
        observation_age_ms: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Append the repair decision that was taken, not just the call it produced.

        Without this the log cannot answer "which repair did the agent choose here" —
        a retry and a continue both end in a tool call, so the action is not
        recoverable from the steps. That made the first reality check possible only by
        guessing from tool names, and it would have made a bandit dataset impossible.

        ``explored`` marks decisions taken to gather data rather than because the rule
        said so. Keeping them distinguishable is what lets a learner correct for the
        exploration instead of being quietly biased by it.
        """
        return self.log("decision", {
            "goal": goal,
            "action": action,
            "cause": cause,
            "reason": reason,
            "tool": tool,
            "error": error,
            "explored": bool(explored),
            "confidence": float(confidence),
            "observation": observation,
            "observation_age_ms": (round(observation_age_ms, 1)
                                   if observation_age_ms is not None else None),
        })

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "file": str(self.path),
                "instance": self.instance,
                "lines": self._counters["lines"],
                "steps_ok": self._counters["ok"],
                "steps_failed": self._counters["fail"],
                "tools": dict(sorted(self._tool_counts.items())),
            }

    def tail(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Read the last ``limit`` records without loading the whole file.

        The recorder rotates at 64 MB, but the dashboard polls this endpoint, and
        ``readlines()`` allocated the entire log on every poll just to keep the
        final lines. Read backwards in blocks instead.
        """
        limit = max(1, min(int(limit), 5000))
        if not self.path.exists():
            return []
        try:
            with self.path.open("rb") as handle:
                handle.seek(0, 2)
                position = handle.tell()
                # Keep blocks separately and count newlines per block. The old
                # loop prepended and re-counted the whole buffer on every step,
                # which is O(total²) for a large tail (5,000 rows can be tens
                # of MB of observations).
                blocks: List[bytes] = []
                newlines = 0
                block = 64 * 1024
                while position > 0 and newlines <= limit:
                    step = min(block, position)
                    position -= step
                    handle.seek(position)
                    chunk = handle.read(step)
                    blocks.append(chunk)
                    newlines += chunk.count(b"\n")
                data = b"".join(reversed(blocks))
        except OSError:
            return []
        records: List[Dict[str, Any]] = []
        for raw in data.splitlines()[-limit:]:
            try:
                records.append(_fast_loads(raw.decode("utf-8", "replace")))
            except ValueError:
                continue
        return records
