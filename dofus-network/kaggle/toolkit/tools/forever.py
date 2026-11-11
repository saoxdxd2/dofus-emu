"""Keep the client, the tunnel, the brain and the farm loop alive until told to stop.

The rule this file exists to enforce: **a failure never ends the run.** A lost fight, a
refused login, a crashed WebView2, a dead brain, a dropped tunnel, a stuck character,
a farm loop that reached its kill count -- all of those are events inside a run, not
the end of one. The only exit is the operator's stop file (`<state>/stop`, the same
one `tools/auto_live.py` and `tools/live_ready.py` already use) or Ctrl-C.

That is a different contract from the autopilot's, and deliberately so. The autopilot
owns one instance and ends when its goal is done or its lock is taken; it must be able
to end, or a finished run would hold the client forever. The supervisor is the layer
above it that answers "and then what?" -- it starts the autopilot again, and again, and
logs each time why it had to.

Three things make that honest rather than a `while True` that hides problems:

* Every component is a separate check with its own backoff, so one wedged piece does
  not slow down the recovery of the others.
* Every restart is logged with the reason and the attempt number, so "it kept going"
  is auditable instead of invisible. A supervisor that silently retries forever is
  indistinguishable from one that does nothing.
* Backoff is capped. Waiting longer and longer is how a supervisor stops noticing that
  the thing it is waiting for will never come back.

    python -m tools.forever                # supervise until the stop file appears
    python -m tools.forever --once         # one pass, for testing
    python -m tools.forever --dry-run      # report what is down, change nothing
    python -m tools.forever --stop         # create the stop file deliberately

Kept stdlib-only so it can be tested without the game, the bridge or the models.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = REPO_ROOT.parent

# The embedded runtime ships a `python311._pth`, which ignores PYTHONPATH, so a script
# that imports a repo-root package has to say where the root is. `state_dir()` imported
# `agent.bridge` behind a bare `except Exception` that silently returned the default, so
# on the shipped interpreter the supervisor read a *different* state directory than the
# bridge wrote to -- and the stop file it polls would never have been the operator's.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# A farm loop with a kill count ends when it reaches it. The supervisor's whole point
# is that the run outlives any single loop, so `agent.hunting` arms the loop with no
# kill count at all (the schema's default of 100 applies to a caller that asks for a
# bounded run) and the supervisor simply re-engages if the loop ever stops.

# How long to wait before retrying a component, by consecutive failures. Capped: a
# supervisor that backs off forever has stopped supervising.
BACKOFF_SECONDS = (0.0, 5.0, 15.0, 30.0, 60.0, 120.0)

TUNNEL_PORT = 40000


def state_dir() -> Path:
    """The host's per-user state directory, shared with the bridge and the stop file."""
    try:
        from agent.bridge import state_dir as _state_dir
        return Path(_state_dir())
    except Exception:  # noqa: BLE001 - the supervisor must run before the agent imports
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DofusFarm"


def stop_path() -> Path:
    return state_dir() / "stop"


def stop_requested() -> bool:
    """The operator's stop. The only reason this loop is ever allowed to end."""
    return stop_path().exists()


# -- component checks --------------------------------------------------------
#
# Each returns (healthy, detail). A check must not raise: "I could not tell" has to
# be reported as unhealthy-with-a-reason, not as an exception that ends the tick.

def port_open(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_tunnel() -> tuple:
    if port_open(TUNNEL_PORT):
        return True, "wireproxy accepting on %d" % TUNNEL_PORT
    return False, "nothing listening on %d" % TUNNEL_PORT


def check_client() -> tuple:
    """The game process and the directive bridge, both required to be driving it."""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq DofusFarm.exe"],
                             capture_output=True, text=True, timeout=20)
        running = "DofusFarm.exe" in (out.stdout or "")
    except Exception as exc:  # noqa: BLE001
        return False, "could not list processes: %s" % exc
    if not running:
        return False, "DofusFarm.exe is not running"
    try:
        from agent.bridge import reachable, client_count
        if not reachable():
            return False, "process is up but the directive bridge does not answer"
        count = client_count()
        if not count:
            return False, "bridge answers but no client is attached"
        return True, "%d client(s) on the bridge" % count
    except Exception as exc:  # noqa: BLE001
        return False, "bridge check failed: %s" % exc


def check_brain() -> tuple:
    try:
        from combat.brain_service import brain_is_up
        if brain_is_up():
            return True, "brain answering on 8891"
        return False, "brain not answering on 8891"
    except Exception as exc:  # noqa: BLE001
        return False, "brain probe failed: %s" % exc


class FarmingCheck:
    """Healthy only while the character is actually getting somewhere.

    `auto_fight: true` is not evidence of progress. The page sets it when a fight is
    joined and leaves it set afterwards, so a character that has finished its loop and
    is standing on a map reports itself as farming forever. Measured 2026-11-10:
    `auto_fight` true, `in_fight` false, and the session's fight count unchanged for
    minutes -- a health check reading that flag would have called it healthy.

    So progress is the test: the fight count, the map and the cell must move inside
    `idle_seconds`. Nothing else in the snapshot distinguishes "waiting for a turn"
    from "nothing is going to happen".
    """

    def __init__(self, idle_seconds: float = 90.0,
                 clock: Callable[[], float] = time.time,
                 snapshot: Optional[Callable[[], Dict[str, Any]]] = None) -> None:
        self.idle_seconds = float(idle_seconds)
        self.clock = clock
        self._snapshot = snapshot
        self._last: Optional[tuple] = None
        self._changed_at: Optional[float] = None
        # Seconds since the last change, published for the advisor: "how stuck" is a
        # feature, and recomputing it from a snapshot it does not own would be a
        # second definition of stuck.
        self.seconds_since_progress: float = 0.0

    def _read(self) -> Dict[str, Any]:
        if self._snapshot is not None:
            return self._snapshot()
        from agent.executor import ToolExecutor
        return ToolExecutor().snapshot()

    def _progress(self, snapshot: Dict[str, Any]) -> tuple:
        session = snapshot.get("farm_session") or {}
        position = snapshot.get("position") or {}
        return (session.get("fights"), session.get("wins"), session.get("losses"),
                snapshot.get("map_id") or position.get("map_id"),
                snapshot.get("cell") if snapshot.get("cell") is not None
                else position.get("cell"))

    def __call__(self) -> tuple:
        try:
            snapshot = self._read()
        except Exception as exc:  # noqa: BLE001
            return False, "snapshot failed: %s" % exc
        if not isinstance(snapshot, dict) or not snapshot:
            return False, "no snapshot from the client"
        if not snapshot.get("in_world"):
            return False, "client is not in the world"
        if snapshot.get("in_fight"):
            self._last = self._progress(snapshot)
            self._changed_at = self.clock()
            return True, "in a fight"

        now = self.clock()
        progress = self._progress(snapshot)
        if self._last is None:
            # First look: nothing to compare against yet, so it is not stuck -- it is
            # unwatched. Calling it stuck here would repair on every cold start.
            self._last = progress
            self._changed_at = now
            return True, "watching for progress"
        if progress != self._last:
            self._last = progress
            self._changed_at = now
            return True, "progressing (fights/map/cell moved)"
        if self._changed_at is None:
            self._changed_at = now
            return True, "watching for progress"
        idle = now - self._changed_at
        self.seconds_since_progress = idle
        if idle >= self.idle_seconds:
            return False, "idle for %ds (no fight, map or cell change)" % int(idle)
        return True, "idle for %ds, within the %ds allowance" % (int(idle),
                                                                 int(self.idle_seconds))


def check_farming() -> tuple:
    """A fresh, stateless farming check. Prefer `FarmingCheck` when supervising."""
    return FarmingCheck(idle_seconds=0.0)()


# -- the supervisor ----------------------------------------------------------

@dataclass
class Component:
    name: str
    check: Callable[[], tuple]
    repair: Callable[[], str]
    failures: int = 0
    repairs: int = 0
    last_detail: str = ""
    next_attempt_at: float = 0.0
    # The repairs this component can actually perform, by name. `repair` stays the
    # default and the fallback, so a component without remedies behaves exactly as
    # it did before; with them, `agent.problem_nn` may pick a different one from
    # experience. Only names listed here can ever be chosen -- a learned hint that
    # the component cannot execute would be a guaranteed no-op.
    remedies: Dict[str, Callable[[], str]] = field(default_factory=dict)
    default_remedy: str = ""

    def backoff(self) -> float:
        index = min(self.failures, len(BACKOFF_SECONDS) - 1)
        return BACKOFF_SECONDS[index]


@dataclass
class Supervisor:
    """One pass over every component, repeated until the operator stops it.

    The callables are injected so the whole thing can be tested without a client: the
    promise "a failure never ends the run" is only worth anything if it is proven by a
    test that makes every component fail forever.
    """

    components: List[Component] = field(default_factory=list)
    # A predicate, not the path. Passing `stop_path` itself made every tick end the
    # run on its first check, because a Path object is always truthy -- and the tests
    # did not catch it because they all inject their own predicate.
    stop_requested: Callable[[], bool] = stop_requested
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    log: Callable[[str], None] = lambda line: print(line, flush=True)
    tick_seconds: float = 20.0
    dry_run: bool = False
    ticks: int = 0
    attempts: int = 0
    stops_seen: int = 0
    # `agent.problem_nn.ProblemAdvisor`, or None. Without one the supervisor repairs
    # by habit exactly as before, which is what keeps this a hint rather than a
    # dependency.
    advisor: Optional[Any] = None
    # The attempt whose outcome the next tick decides, per component.
    _pending: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # What was tried last and how it went, per component: two of the features the
    # advisor reads.
    _last: Dict[str, Dict[str, str]] = field(default_factory=dict)

    def _situation(self, component: Component, detail: str) -> Dict[str, Any]:
        """The failure as the advisor sees it.

        Every field is something the supervisor already knows; nothing here needs a
        client call, because this runs in the middle of a failure.
        """
        previous = self._last.get(component.name) or {}
        return {
            "component": component.name,
            "detail": detail,
            "failures": component.failures,
            "seconds_since_progress": getattr(component.check,
                                              "seconds_since_progress", 0.0),
            "last_remedy": previous.get("remedy"),
            "last_outcome": previous.get("outcome"),
            "dry_run": self.dry_run,
        }

    def _remedy_for(self, component: Component, situation: Dict[str, Any]
                    ) -> tuple:
        """(name, callable) for this attempt.

        The advisor only reorders remedies the component already offers. No
        advisor, no remedies, or no opinion all end at `component.repair`, which
        is the behaviour this file shipped with.
        """
        offered = list(component.remedies)
        pick = None
        if self.advisor is not None and offered:
            try:
                pick = self.advisor.choose(situation, offered)
            except Exception:  # noqa: BLE001 - a hint must never stop a repair
                pick = None
        if pick is not None and pick in component.remedies:
            return pick, component.remedies[pick]
        if component.default_remedy in component.remedies:
            return component.default_remedy, component.remedies[component.default_remedy]
        if offered:
            return offered[0], component.remedies[offered[0]]
        return "", component.repair

    def tick(self) -> Dict[str, Any]:
        """Run one pass. Never raises, whatever the components do."""
        self.ticks += 1
        now = self.clock()
        report: Dict[str, Any] = {"tick": self.ticks, "at": now, "components": []}
        for component in self.components:
            entry = {"name": component.name}
            try:
                healthy, detail = component.check()
            except Exception as exc:  # noqa: BLE001 - a check must never end the run
                healthy, detail = False, "check raised %s: %s" % (type(exc).__name__, exc)
            component.last_detail = detail
            entry["healthy"] = healthy
            entry["detail"] = detail
            if healthy:
                self._settle(component, True)
                if component.failures:
                    self.log("[forever] %s recovered after %d failure(s): %s"
                             % (component.name, component.failures, detail))
                component.failures = 0
                component.next_attempt_at = 0.0
                report["components"].append(entry)
                continue
            component.failures += 1
            # Still unhealthy is the verdict on the *previous* attempt, and it is
            # known here before the next one is chosen -- which is the whole point:
            # a supervisor that never learns which repair worked can only repeat it.
            self._settle(component, False)
            if now < component.next_attempt_at:
                entry["action"] = "waiting"
                entry["retry_in_s"] = round(component.next_attempt_at - now, 1)
                report["components"].append(entry)
                continue
            entry["attempt"] = component.failures
            if self.dry_run:
                entry["action"] = "would-repair"
                report["components"].append(entry)
                continue
            situation = self._situation(component, detail)
            remedy, action = self._remedy_for(component, situation)
            entry["remedy"] = remedy or "repair"
            if self.advisor is not None and remedy:
                try:
                    self.advisor.learn(situation, remedy)
                except Exception:  # noqa: BLE001
                    pass
                self._pending[component.name] = {"remedy": remedy,
                                                 "situation": situation}
            try:
                outcome = action()
                component.repairs += 1
                entry["action"] = "repaired"
                entry["outcome"] = outcome
                self.log("[forever] %s unhealthy (%s); repair attempt %d [%s]: %s"
                         % (component.name, detail, component.failures,
                            remedy or "repair", outcome))
            except Exception as exc:  # noqa: BLE001 - a repair must never end the run
                entry["action"] = "repair-failed"
                entry["error"] = "%s: %s" % (type(exc).__name__, exc)
                self.log("[forever] %s repair failed (%s); will retry in %ss"
                         % (component.name, entry["error"], component.backoff()))
                self._settle(component, False)
            component.next_attempt_at = now + component.backoff()
            report["components"].append(entry)
        return report

    def _settle(self, component: Component, healthy: bool) -> None:
        """Label the pending attempt, if the outcome is now known.

        Called on every tick for every component, so the first tick after an attempt
        is the one that decides it. A component that recovers immediately labels the
        attempt as worked; one that is still down labels it failed -- including the
        case where the repair raised, because from the outside that is the same
        thing.
        """
        pending = self._pending.pop(component.name, None)
        if pending is None:
            return
        outcome = "worked" if healthy else "failed"
        self._last[component.name] = {"remedy": pending["remedy"], "outcome": outcome}
        if self.advisor is not None:
            try:
                self.advisor.label(component.name, outcome)
            except Exception:  # noqa: BLE001 - learning must never stop a repair
                pass

    def run(self, max_ticks: Optional[int] = None) -> Dict[str, Any]:
        """Loop until the stop file appears. Every exit is a deliberate one."""
        self.log("[forever] supervising %d component(s); stop with %s"
                 % (len(self.components), stop_path()))
        while True:
            try:
                if self.stop_requested():
                    self.stops_seen += 1
                    self.log("[forever] stop requested after %d tick(s); ending"
                             % self.ticks)
                    return {"stopped": True, "ticks": self.ticks,
                            "reason": "stop requested"}
                # Counted before the work, not after: a tick that raises never
                # completes, and a bound that only counts completed ticks would spin
                # forever against a bug that throws every time -- which is the exact
                # failure this whole file exists to survive, not to hide.
                self.attempts += 1
                report = self.tick()
                if self.dry_run:
                    self.log(json.dumps(report))
            except KeyboardInterrupt:
                self.log("[forever] interrupted after %d tick(s); ending" % self.ticks)
                return {"stopped": True, "ticks": self.ticks, "reason": "interrupted"}
            except Exception:  # noqa: BLE001 - the whole point: keep going
                # A bug in the supervisor must not end the run either. Print the
                # traceback so it is fixable, then carry on to the next tick.
                self.log("[forever] tick raised; continuing\n%s"
                         % traceback.format_exc(limit=6))
            if max_ticks is not None and self.attempts >= max_ticks:
                return {"stopped": False, "ticks": self.ticks, "reason": "max_ticks"}
            try:
                self.sleep(self.tick_seconds)
            except KeyboardInterrupt:
                self.log("[forever] interrupted while idle; ending")
                return {"stopped": True, "ticks": self.ticks, "reason": "interrupted"}


# -- repairs -----------------------------------------------------------------
#
# Each repair is best-effort and returns a one-line description of what it did. They
# deliberately do not wait for the result: the next tick re-checks, and the check is
# the only thing that decides whether the repair worked.

def repair_tunnel() -> str:
    script = PROJECT_ROOT / "start-network.bat"
    if not script.exists():
        raise FileNotFoundError("start-network.bat not found at %s" % script)
    subprocess.Popen(["cmd", "/c", str(script), "--once"], cwd=str(PROJECT_ROOT),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return "launched start-network.bat --once"


def repair_client() -> str:
    exe = PROJECT_ROOT / "DofusFarm.exe"
    if not exe.exists():
        raise FileNotFoundError("DofusFarm.exe not found at %s" % exe)
    subprocess.Popen([str(exe)], cwd=str(PROJECT_ROOT),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # The manager has to be up before it will accept a launch request.
    time.sleep(20)
    subprocess.Popen([str(exe), "--launch", "dofus-01"], cwd=str(PROJECT_ROOT),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return "started the manager and asked it to launch dofus-01"


def repair_brain() -> str:
    from combat.brain_service import ensure_brain
    result = ensure_brain()
    return "ensure_brain -> %s" % (result,)


def repair_farming() -> str:
    """Get the character fighting, not merely *armed*.

    This used to call `farm.stop` then `farm.start` and report the ack. Both halves
    were wrong, and both were measured on 2026-11-10:

    * `farm.stop` disables the bot, so the `farm.start` after it resets the session
      counters -- and those counters are exactly what `FarmingCheck` compares. Every
      repair therefore looked like progress ("fights/map/cell moved" from 1 fight to
      0) and the check declared recovery while nothing had happened.
    * `farm.start` only sets `__FarmBot.enabled`. It does not walk to a group and it
      does not join one. A character stood on a map with a live three-monster group
      for four minutes while this returned `ok=True` on every attempt.

    So the repair no longer stops anything, and it no longer stops at the ack: it
    arms, then joins a group the budget allows, then travels when the map has
    nothing worth fighting. `agent.hunting` owns those decisions and reports what it
    actually did, which is what this string returns.
    """
    from agent.executor import ToolExecutor
    from agent import hunting

    executor = ToolExecutor()
    report = hunting.engage(executor)
    return hunting.describe(report)


def wait_remedy() -> str:
    """Do nothing on purpose, and say so.

    A real remedy, not a placeholder: several failures here are transient by nature
    (a client still loading a map, a fight the page has not finished publishing), and
    the correct response is to let the backoff elapse instead of issuing another
    command into a client that is not listening. Offering it as a named option is
    what lets `agent.problem_nn` learn when it beats acting.
    """
    return "waited; nothing issued this attempt"


def build_supervisor(idle_seconds: float = 90.0, **kwargs: Any) -> Supervisor:
    """The real component set, in the order a person would fix them."""
    advisor = None
    if "advisor" not in kwargs:
        try:
            from agent.problem_nn import ProblemAdvisor

            advisor = ProblemAdvisor()
        except Exception:  # noqa: BLE001 - the supervisor must start without a model
            advisor = None
    return Supervisor(components=[
        Component("tunnel", check_tunnel, repair_tunnel),
        Component("client", check_client, repair_client),
        Component("brain", check_brain, repair_brain,
                  remedies={"restart": repair_brain, "wait": wait_remedy},
                  default_remedy="restart"),
        # A stateful instance, because "is it stuck?" is a question about change over
        # time and cannot be answered from one snapshot.
        Component("farming", FarmingCheck(idle_seconds=idle_seconds), repair_farming,
                  remedies={"engage": repair_farming, "wait": wait_remedy},
                  default_remedy="engage"),
    ], advisor=advisor, **kwargs)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tick", type=float, default=20.0,
                        help="seconds between passes (default 20)")
    parser.add_argument("--idle", type=float, default=90.0,
                        help="seconds without a fight/map/cell change before the "
                             "character counts as stuck (default 90)")
    parser.add_argument("--once", action="store_true", help="one pass, then exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what is down without repairing anything")
    parser.add_argument("--stop", action="store_true",
                        help="create the stop file deliberately and exit")
    args = parser.parse_args(argv)

    if args.stop:
        path = stop_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("stopped by the operator at %s\n" % time.strftime("%FT%T"),
                        encoding="utf-8")
        print("created %s; the supervisor will end on its next tick" % path)
        return 0

    supervisor = build_supervisor(tick_seconds=args.tick, dry_run=args.dry_run,
                                  idle_seconds=args.idle)
    if args.once:
        report = supervisor.tick()
        print(json.dumps(report, indent=1))
        # One pass is a diagnostic, so a down component is reported, not hidden.
        return 0 if all(c["healthy"] for c in report["components"]) else 1

    result = supervisor.run()
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
