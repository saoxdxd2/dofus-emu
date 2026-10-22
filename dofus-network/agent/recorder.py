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

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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


def _redact(value: Any) -> Any:
    """Recursively redact secrets. Strings that embed credentials in URLs
    (e.g. ``http://user:pass@host``) are scrubbed too."""
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and key.strip().lower() in REDACT_KEYS:
                out[key] = _REDACTED
            else:
                out[key] = _redact(item)
        return out
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        # user:password@host inside a URL
        return re.sub(r"://[^/\s:@]+:[^/\s@]+@", "://[redacted]@", value)
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
        self._counters: Dict[str, int] = {"lines": 0, "ok": 0, "fail": 0}
        self._tool_counts: Dict[str, int] = {}
        self._scan_existing()

    # -- internals -----------------------------------------------------------

    def _scan_existing(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # a torn tail line from a crash is skipped, not fatal
                    self._count(record)
        except OSError as exc:
            logger.warning(f"Could not scan existing trajectory file: {exc}")

    def _count(self, record: Dict[str, Any]) -> None:
        self._counters["lines"] += 1
        if record.get("kind") == "step":
            tool = str(record.get("tool", "?"))
            self._tool_counts[tool] = self._tool_counts.get(tool, 0) + 1
            if record.get("ok"):
                self._counters["ok"] += 1
            else:
                self._counters["fail"] += 1

    def _rotate_if_needed(self) -> None:
        try:
            if self.path.exists() and self.path.stat().st_size >= self.max_bytes:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                rotated = self.path.with_name(f"{self.path.stem}.{stamp}.jsonl")
                self.path.rename(rotated)
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
        self._rotate_if_needed()
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.error(f"Failed to write trajectory record: {exc}")
            return record
        self._count(record)
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
        })

    def stats(self) -> Dict[str, Any]:
        return {
            "file": str(self.path),
            "instance": self.instance,
            "lines": self._counters["lines"],
            "steps_ok": self._counters["ok"],
            "steps_failed": self._counters["fail"],
            "tools": dict(sorted(self._tool_counts.items())),
        }

    def tail(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Read the last ``limit`` records (best effort)."""
        limit = max(1, int(limit))
        if not self.path.exists():
            return []
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except OSError:
            return []
        records: List[Dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records
