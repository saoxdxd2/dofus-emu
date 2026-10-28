"""One process, many instances: the shared brain daemon.

The host used to spawn one ``run_autopilot.py`` per selected instance. Each child
loaded its own interpreter, numpy, ONNX Runtime, knowledge cache and model
sessions, so four instances paid for four copies of everything that is actually
shared. This module keeps one process and one copy of the process-wide models and
knowledge, and runs one ``Autopilot`` per instance on its own thread.

The control surface is deliberately small and stdlib-only:

    POST /brain/start   {"instances": ["dofus-01"], "goals": ["do all the quests"], ...}
    POST /brain/stop    {"instance": "dofus-01"} or {"all": true}
    GET  /brain/status  per-instance state, step count and last log line
    GET  /brain/health  liveness

The loop itself is synchronous (it does blocking HTTP to the bridge and blocking
ONNX inference). Running each loop on a thread is the honest implementation:
the daemon is one process and one model copy, but Python cannot turn a blocking
step into a coroutine without rewriting every call site. The HTTP control plane
is asynchronous from the host's point of view; the per-instance work is
concurrent threads.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from agent.autopilot import COMMAND_SHADOW_MODEL, Autopilot, _build_policy
from agent.bridge import state_dir
from agent.recorder import TrajectoryRecorder
from agent.executor import ToolExecutor
from agent.runtime import AgentRuntime

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_DIR = state_dir()
DEFAULT_RECORDER_DIR = REPO_ROOT / "trajectories"


class _DaemonAutopilot(Autopilot):
    """An Autopilot that does not own the shared ``autopilot.lock``.

    The lock exists so the idle trainer stays away while a live loop runs. In a
    multi-instance daemon one instance finishing must not delete the lock while
    its siblings are still playing, so the daemon owns one process-level lock.
    """

    def _write_lock(self) -> None:  # noqa: D401 - intentional override
        return

    def _clear_lock(self) -> None:  # noqa: D401 - intentional override
        return


def _last_step_line(path: Path) -> Optional[str]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 65536))
            lines = handle.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("kind") in (None, "step"):
            return line
    return None


class BrainInstance:
    def __init__(
        self,
        instance: str,
        goals: List[str],
        log_path: Path,
        stop_path: Path,
        pilot: Autopilot,
    ) -> None:
        self.instance = instance
        self.goals = goals
        self.log_path = log_path
        self.stop_path = stop_path
        self.pilot = pilot
        self.thread: Optional[threading.Thread] = None
        self.state = "starting"
        self.error: Optional[str] = None
        self.summary: Optional[Dict[str, Any]] = None
        self.started_at = time.time()
        self.finished_at: Optional[float] = None

    def alive(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def status(self) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "instance": self.instance,
            "state": self.state,
            "alive": self.alive(),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "log": str(self.log_path),
            "stop_file": str(self.stop_path),
            "goals": self.goals,
        }
        pilot = self.pilot
        for attr in ("_step_total", "_waits", "_recoveries", "_dialog_stalls"):
            if hasattr(pilot, attr):
                row[attr.lstrip("_")] = getattr(pilot, attr)
        last = _last_step_line(self.log_path)
        if last:
            row["last_step"] = last
        if self.error:
            row["error"] = self.error
        if self.summary:
            row["summary"] = self.summary
        return row


class BrainDaemon:
    def __init__(
        self,
        log_dir: Optional[Path] = None,
        recorder_dir: Optional[Path] = None,
        default_policy: str = "rule",
    ) -> None:
        self.log_dir = Path(log_dir or DEFAULT_LOG_DIR)
        self.recorder_dir = Path(recorder_dir or DEFAULT_RECORDER_DIR)
        self.default_policy = default_policy
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._instances: Dict[str, BrainInstance] = {}
        self._lock = threading.RLock()
        self._lock_path = self.log_dir / "brain.lock"

    # -- lifecycle -----------------------------------------------------------

    def _write_daemon_lock(self) -> None:
        # Cleanup still owns the instance, including after a pilot error.
        active = [name for name, item in self._instances.items()
                  if item.alive() and item.finished_at is None]
        payload = {
            "session": "brain-daemon",
            "pid": os.getpid(),
            "instances": active,
            "updated": time.time(),
        }
        try:
            self._lock_path.write_text(json.dumps(payload), encoding="utf-8")
            # The idle trainer reads this historical path. Keep it truthful while
            # at least one loop is alive, and remove it only when none is.
            legacy = self.log_dir / "autopilot.lock"
            if active:
                legacy.write_text(json.dumps(payload), encoding="utf-8")
            else:
                legacy.unlink(missing_ok=True)
        except OSError:
            pass

    def _policy(self, config: Dict[str, Any]):
        ns = argparse.Namespace(
            policy=str(config.get("policy") or self.default_policy),
            level_hint=int(config.get("level_hint") or 60),
            ensemble=str(config.get("ensemble") or "act"),
            model_dir=str(config.get("model_dir")
                          or (REPO_ROOT / "models" / "encoder30_v15")),
        )
        return _build_policy(ns)

    def _make_pilot(self, instance: str, config: Dict[str, Any]) -> Autopilot:
        policy = self._policy(config)
        recorder = TrajectoryRecorder(root=self.recorder_dir, instance=instance)
        executor = ToolExecutor(recorder=recorder)
        runtime = AgentRuntime(
            executor=executor,
            recorder=recorder,
            policy=policy,
            instance=instance,
        )
        if hasattr(policy, "store") and getattr(policy, "store", None) is None:
            try:
                policy.store = executor.store
            except Exception:
                pass
        log_path = self.log_dir / ("autopilot.%s.jsonl" % instance)
        stop_path = self.log_dir / ("AUTOPILOT_STOP.%s" % instance)
        try:
            stop_path.unlink(missing_ok=True)
        except OSError:
            pass
        goals = config.get("goals") or ["do all the quests"]
        if isinstance(goals, str):
            goals = [line.strip() for line in goals.splitlines() if line.strip()]
        verifier = config.get("verifier_model") or None
        return _DaemonAutopilot(
            runtime=runtime,
            goals=list(goals),
            log_path=log_path,
            stop_file=stop_path,
            max_steps=int(config.get("steps") or 0),
            minutes=float(config.get("minutes") or 0.0),
            interval=float(config.get("interval") or 0.0),
            steps_per_goal=int(config.get("steps_per_goal") or 8),
            dry_run=bool(config.get("dry_run")),
            verifier_model=Path(verifier) if verifier else None,
            veto_threshold=float(config.get("veto_threshold") or 0.0),
            explore=float(config.get("explore") or 0.0),
            fight_boards=bool(config.get("fight_boards", True)),
            brain=str(config.get("brain") or "auto"),
            command_shadow=str(config.get("command_shadow") or "off"),
            command_shadow_model=Path(config.get("command_shadow_model"))
            if config.get("command_shadow_model") else COMMAND_SHADOW_MODEL,
        )

    def start(self, config: Dict[str, Any]) -> Dict[str, Any]:
        instances = config.get("instances") or []
        if isinstance(instances, str):
            instances = [instances]
        instances = [str(item).strip() for item in instances if str(item).strip()]
        if not instances:
            return {"ok": False, "error": "no instances selected"}
        started: List[Dict[str, Any]] = []
        with self._lock:
            for instance in instances:
                current = self._instances.get(instance)
                if current is not None and current.alive():
                    started.append(current.status())
                    continue
                try:
                    pilot = self._make_pilot(instance, config)
                except Exception as exc:  # noqa: BLE001 - report, do not take the daemon down
                    self._instances[instance] = BrainInstance(
                        instance, [], self.log_dir / ("autopilot.%s.jsonl" % instance),
                        self.log_dir / ("AUTOPILOT_STOP.%s" % instance), None)  # type: ignore[arg-type]
                    self._instances[instance].state = "error"
                    self._instances[instance].error = "%s: %s" % (type(exc).__name__, exc)
                    started.append(self._instances[instance].status())
                    continue
                item = BrainInstance(
                    instance=instance,
                    goals=list(pilot.goals),
                    log_path=pilot.log_path,
                    stop_path=pilot.stop_file,
                    pilot=pilot,
                )
                self._instances[instance] = item
                thread = threading.Thread(
                    target=self._run_instance,
                    args=(item,),
                    name="brain-" + instance,
                    daemon=True,
                )
                item.thread = thread
                thread.start()
                started.append(item.status())
            self._write_daemon_lock()
        return {"ok": True, "instances": started}

    def _run_instance(self, item: BrainInstance) -> None:
        with self._lock:
            should_run = item.state != "stopping"
            if should_run:
                item.state = "running"
        completed = False
        try:
            if should_run:
                summary = item.pilot.run()
                item.summary = summary if isinstance(summary, dict) else {"result": summary}
                completed = True
        except BaseException as exc:  # noqa: BLE001 - SystemExit is a preflight outcome too
            item.error = "%s: %s" % (type(exc).__name__, exc)
            item.state = "error"
            try:
                traceback.print_exc()
            except Exception:
                pass
        finally:
            try:
                item.pilot.stop_client()
            except Exception:
                pass
            with self._lock:
                if item.state == "running":
                    item.state = "finished" if completed else "exited"
                elif item.state == "stopping":
                    item.state = "stopped"
                item.finished_at = time.time()
                self._write_daemon_lock()

    def stop(self, instance: Optional[str] = None, stop_all: bool = False,
             why: str = "stopped by the host") -> Dict[str, Any]:
        with self._lock:
            names = list(self._instances) if stop_all else ([instance] if instance else [])
            stopped: List[Dict[str, Any]] = []
            for name in names:
                item = self._instances.get(name)
                if item is None:
                    continue
                if item.alive() and item.state in ("starting", "running", "stopping"):
                    item.state = "stopping"
                    try:
                        item.stop_path.write_text(why + " at " + time.strftime("%H:%M:%S"),
                                                  encoding="utf-8")
                    except OSError:
                        pass
                stopped.append(item.status())
            self._write_daemon_lock()
        return {"ok": True, "stopped": stopped}

    def status(self) -> Dict[str, Any]:
        with self._lock:
            active = [name for name, item in self._instances.items() if item.alive()]
            return {
                "ok": True,
                "pid": os.getpid(),
                "active": active,
                "instances": [item.status() for item in self._instances.values()],
            }

    def health(self) -> Dict[str, Any]:
        with self._lock:
            active = [name for name, item in self._instances.items() if item.alive()]
        return {
            "status": "ok",
            "service": "dofus-brain",
            "pid": os.getpid(),
            "active": active,
            "instances": len(self._instances),
        }
