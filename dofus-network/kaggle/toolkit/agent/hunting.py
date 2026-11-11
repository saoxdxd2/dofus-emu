"""Which monster group to fight, and how to get the character into it.

`farm.start` only *arms* the auto-fight. It sets `__FarmBot.enabled = true` and
returns; it never walks to a group and never joins one. Measured 2026-11-10: a
character stood on map 146539520 for four minutes with a live group on it while
the supervisor re-issued `farm.start` every 20 s and logged `ok=True` each time.
A repair that reports success and changes nothing is worse than no repair,
because it hides the stall it was written to fix.

So engagement is a ladder and every rung reports whether it actually moved:

    join    `fight.join` with the group's contextual id -- the fight starts
    travel  `nav.goto` through an edge, when this map has nothing worth fighting

Which group to join is a risk decision, not a preference. Measured the same day:
a level-12 character lost to three level-8 monsters (total level 24, twice its
own level) and was paid no xp. So the choice is bounded by a *budget* -- the
total monster level this character is willing to take on, as a multiple of its
own level -- and that budget is learned from the record rather than guessed:
wins raise it, losses lower it. One scalar, because a win or a loss is the only
evidence the client gives, and one scalar is enough to stop the obvious suicide.

Everything here is a pure decision over data the caller already has, except
`engage`, which is the only part that talks to the client.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE = REPO_ROOT / "models" / "hunt_policy.json"

# A group whose total level is at or under the character's own is the safe
# starting point: it is the case that was measured to be winnable. The cap keeps
# a lucky streak from walking a character into a fight it cannot survive, and the
# floor keeps a losing streak from refusing every group on the map forever.
START_BUDGET = 1.0
MIN_BUDGET = 0.5
MAX_BUDGET = 2.5
WIN_GAIN = 1.15
LOSS_DROP = 0.7

# The four edges, in the order travel tries them. `nav.goto` resolves the map id
# from the client's own map data, so a direction is enough -- the caller does not
# need the neighbour table.
DIRECTIONS = ("right", "bottom", "left", "top")
OPPOSITE = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or isinstance(value, bool):
            return default
        out = float(value)
        if out != out or out in (float("inf"), float("-inf")):
            return default
        return out
    except (TypeError, ValueError):
        return default


# -- reading a snapshot ------------------------------------------------------

def my_level(snapshot: Dict[str, Any]) -> float:
    """The character's level, from wherever this build happens to publish it."""
    character = snapshot.get("character") or {}
    level = _num(character.get("level"), 0.0)
    if level > 0:
        return level
    base = snapshot.get("characterBaseInformations") or {}
    level = _num(base.get("level"), 0.0)
    if level > 0:
        return level
    stats = character.get("stats") or {}
    return _num(stats.get("level"), 0.0)


def my_hp_fraction(snapshot: Dict[str, Any]) -> Optional[float]:
    """Current life as a fraction of maximum, or None when unreadable."""
    for key in ("lifePoints", "hp"):
        value = snapshot.get(key)
        if value is not None:
            top = _num(value, -1.0)
            break
    else:
        character = snapshot.get("character") or {}
        top = _num(character.get("lifePoints"), -1.0)
    maximum = _num(snapshot.get("effectiveMaxLifePoints"), 0.0)
    if maximum <= 0:
        maximum = _num((snapshot.get("character") or {}).get("maxLifePoints"), 0.0)
    if top < 0 or maximum <= 0:
        return None
    return max(0.0, min(1.0, top / maximum))


# -- reading a group ---------------------------------------------------------

def group_id(group: Dict[str, Any]) -> Optional[int]:
    """The id `fight.join` needs. The actor carries `contextualId`; `id` is a trap."""
    for key in ("contextualId", "group_id"):
        value = group.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def group_threat(group: Dict[str, Any]) -> float:
    """Total monster level in the group -- the quantity that predicts a loss.

    `total_level` is published directly and is exact. The fallbacks exist because
    a group whose members have not been resolved yet still has a size, and a group
    with no readable members is not free: it is unknown, which is a reason to be
    careful, not a reason to attack.
    """
    total = _num(group.get("total_level"), 0.0)
    if total > 0:
        return total
    members = group.get("members") or []
    levels = [_num((m or {}).get("level"), 0.0) for m in members if isinstance(m, dict)]
    known = [lv for lv in levels if lv > 0]
    if known and len(known) == len(levels):
        return float(sum(known))
    count = _num(group.get("member_count"), 0.0) or float(len(members or []))
    if count <= 0:
        count = float(len(group.get("monster_ids") or []))
    highest = _num(group.get("max_level"), 0.0)
    if count > 0 and highest > 0:
        return count * highest
    return count


def group_summary(group: Dict[str, Any]) -> Dict[str, Any]:
    """The few facts worth putting in a log line."""
    return {
        "group_id": group_id(group),
        "cell": group.get("cell"),
        "members": int(_num(group.get("member_count"),
                            len(group.get("monster_ids") or []))),
        "total_level": group_threat(group),
        "max_level": group.get("max_level"),
    }


def choose_group(groups: Iterable[Any], level: float,
                 budget: float = START_BUDGET) -> Tuple[Optional[Dict[str, Any]], str]:
    """The group to attack, or None with the reason there is none.

    Within budget the *biggest* fight wins: xp and drops scale with the group, the
    walk does not. Outside budget nothing is chosen -- an over-budget group is a
    loss with a walk attached, and the answer to "everything here is too strong"
    is to leave, not to die.
    """
    scored: List[Tuple[float, int, Dict[str, Any]]] = []
    for group in groups or []:
        if not isinstance(group, dict):
            continue
        gid = group_id(group)
        if gid is None:
            continue
        threat = group_threat(group)
        if threat <= 0:
            continue
        scored.append((threat, gid, group))
    if not scored:
        return None, "no monster group on this map"

    allowed = max(0.0, level) * max(0.01, budget)
    affordable = [entry for entry in scored if entry[0] <= allowed]
    if not affordable:
        weakest = min(entry[0] for entry in scored)
        return None, ("every group here is over budget (weakest is %.0f, budget is %.0f "
                      "= level %.0f x %.2f)" % (weakest, allowed, level, budget))
    # Biggest first, then lowest id, so the same map always yields the same answer.
    affordable.sort(key=lambda entry: (-entry[0], entry[1]))
    threat, gid, group = affordable[0]
    return group, ("threat %.0f within budget %.0f (level %.0f x %.2f)"
                   % (threat, allowed, level, budget))


def weakest_group(groups: Iterable[Any]) -> Optional[Dict[str, Any]]:
    """The cheapest fight on the map, whatever the budget says.

    Used only when the budget refuses everything *and* there is nowhere to go. A
    character that stands still learns nothing -- the live learner credits turns
    inside fights and has no rows from a map -- so a fight that is likely to be lost
    is still worth more than a character that never fights. It is a last rung, not a
    default: with a working exit, leaving is always the better answer.
    """
    scored: List[Tuple[float, int, Dict[str, Any]]] = []
    for group in groups or []:
        if not isinstance(group, dict):
            continue
        gid = group_id(group)
        if gid is None:
            continue
        threat = group_threat(group)
        if threat <= 0:
            continue
        scored.append((threat, gid, group))
    if not scored:
        return None
    scored.sort(key=lambda entry: (entry[0], entry[1]))
    return scored[0][2]


# -- what the character has learned about its own limits ---------------------

class HuntPolicy:
    """The fight budget, learned from wins and losses and kept across restarts.

    Deliberately small. The evidence available per fight is one bit -- won or
    lost -- so anything richer than a scalar would be fitted to noise. It also
    carries the two facts travel needs to avoid pacing between two maps: where it
    last stood, and which edge it came through.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_STATE
        self.budget = START_BUDGET
        self.wins = 0
        self.losses = 0
        self.last_fight_id: Optional[int] = None
        self.last_map: Optional[int] = None
        self.avoid: Optional[str] = None
        # True between a forced join and the fight it started ending. Not persisted:
        # a restart means no fight is in flight, so a saved `True` could only ever
        # mislabel the next fight.
        self.forced_pending = False

    # -- persistence --
    def to_dict(self) -> Dict[str, Any]:
        return {"budget": round(self.budget, 4), "wins": self.wins,
                "losses": self.losses, "last_fight_id": self.last_fight_id,
                "last_map": self.last_map, "avoid": self.avoid}

    @classmethod
    def from_dict(cls, data: Dict[str, Any], path: Optional[Path] = None) -> "HuntPolicy":
        policy = cls(path)
        if not isinstance(data, dict):
            return policy
        policy.budget = min(MAX_BUDGET, max(MIN_BUDGET,
                                            _num(data.get("budget"), START_BUDGET)))
        policy.wins = int(_num(data.get("wins"), 0))
        policy.losses = int(_num(data.get("losses"), 0))
        raw = data.get("last_fight_id")
        policy.last_fight_id = None if raw is None else int(_num(raw, 0))
        raw = data.get("last_map")
        policy.last_map = None if raw is None else int(_num(raw, 0))
        avoid = data.get("avoid")
        policy.avoid = avoid if avoid in DIRECTIONS else None
        return policy

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "HuntPolicy":
        target = Path(path) if path is not None else DEFAULT_STATE
        try:
            return cls.from_dict(json.loads(target.read_text(encoding="utf-8")), target)
        except (OSError, ValueError):
            # A missing or half-written file is a fresh start, never a crash: this
            # is read on a supervisor tick that must not be able to end the run.
            return cls(target)

    def save(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.to_dict(), indent=1, sort_keys=True),
                           encoding="utf-8")
            tmp.replace(self.path)
            return True
        except OSError:
            return False

    # -- learning --
    def observe(self, won: bool, forced: bool = False) -> float:
        """Fold one fight result into the budget. Returns the new budget.

        A *forced* fight is one the budget refused and the last rung joined anyway.
        Its loss is not evidence about the budget -- the budget had already said no,
        so the loss confirms a decision instead of correcting one -- and folding it
        in walks the budget towards the floor while the overrides keep coming.
        Measured 2026-11-10 on the live session: 14 losses, 13 of them forced, took
        the budget from 1.00 to 0.575, after which every group on the map was over
        budget and every join was forced. Its *win* is still evidence: a group the
        budget refused was beaten, so the budget was too strict and should rise.
        """
        if won:
            self.wins += 1
            self.budget = min(MAX_BUDGET, self.budget * WIN_GAIN)
        else:
            self.losses += 1
            if not forced:
                self.budget = max(MIN_BUDGET, self.budget * LOSS_DROP)
        return self.budget

    def sync(self, snapshot: Dict[str, Any]) -> Optional[bool]:
        """Learn from the last fight the client reported, once per fight.

        The client keeps `last_fight_end` until another fight ends, so this is
        keyed on `fight_id`: re-reading the same record on every supervisor tick
        would count one loss dozens of times and drive the budget to the floor.
        """
        end = snapshot.get("last_fight_end")
        if not isinstance(end, dict):
            return None
        raw = end.get("fight_id")
        if raw is None:
            return None
        try:
            fight_id = int(raw)
        except (TypeError, ValueError):
            return None
        won = end.get("won")
        if not isinstance(won, bool):
            return None
        if self.last_fight_id == fight_id:
            return None
        self.last_fight_id = fight_id
        forced = self.forced_pending
        self.forced_pending = False
        self.observe(won, forced=forced)
        self.save()
        return won

    # -- travel --
    def travel_order(self) -> List[str]:
        """Edges to try, the one we came through last."""
        blocked = self.avoid
        return [d for d in DIRECTIONS if d != blocked] + ([blocked] if blocked else [])

    def note_travel(self, direction: str, arrived_map: Any) -> None:
        """Remember the edge, so the next map does not walk straight back."""
        self.avoid = OPPOSITE.get(direction)
        try:
            self.last_map = None if arrived_map is None else int(arrived_map)
        except (TypeError, ValueError):
            self.last_map = None
        self.save()


# -- the only part that talks to the client ----------------------------------

def _data_of(result: Any) -> Dict[str, Any]:
    """The client's own answer, out of the bridge's two wrappers.

    A live directive answers as `{"response": {"result": {"data": {...}}}}`, so
    reaching for `result.data.get("groups")` finds nothing and reports an empty map
    -- which is what sent this repair travelling away from a map with a group on it.
    `agent.executor.payload_of` exists for exactly this and says so; a test double
    that answers with a bare dict is handled by the fallback.
    """
    try:
        from agent.executor import payload_of
        inner = payload_of(result)
        if inner:
            return inner
    except Exception:  # noqa: BLE001 - the fallback below is always correct
        pass
    data = getattr(result, "data", None)
    return data if isinstance(data, dict) else {}


def _page_read(executor: Any, tool: str, args: Optional[Dict[str, Any]] = None) -> Any:
    """One page directive, through the executor's non-vocabulary path.

    `ToolExecutor.directive` exists for exactly this. The fallback is not for a
    different transport -- it is so a test double that only implements `execute`
    still exercises the same code path instead of failing on a missing method.
    """
    read = getattr(executor, "directive", None)
    if callable(read):
        return read(tool, args or {})
    return executor.execute(tool, args or {})


def engage(executor: Any, policy: Optional[HuntPolicy] = None,
           snapshot: Optional[Dict[str, Any]] = None,
           allow_travel: bool = True) -> Dict[str, Any]:
    """Get the character into a fight. One attempt, no loop, no sleeping.

    Returns a report rather than raising, because the caller is a supervisor tick
    whose contract is that nothing it calls may end the run:

        {"action": "join"|"travel"|"already_fighting"|"waiting"|"none",
         "engaged": bool, "group": {...}|None, "reason": str, "detail": str}

    `action` is what was *attempted*; `engaged` is whether a fight actually
    started. They differ when the client accepts a directive and does nothing,
    which is the exact failure this module exists to make visible.
    """
    report: Dict[str, Any] = {"action": "none", "engaged": False, "group": None,
                              "reason": "", "detail": ""}
    policy = policy if policy is not None else HuntPolicy.load()
    if snapshot is None:
        snapshot = executor.snapshot()
    if not isinstance(snapshot, dict) or not snapshot:
        report["reason"] = "no snapshot from the client"
        return report

    policy.sync(snapshot)

    if snapshot.get("in_fight"):
        report["action"] = "already_fighting"
        report["engaged"] = True
        report["reason"] = "already in a fight"
        return report
    if not snapshot.get("in_world"):
        report["reason"] = "client is not in the world"
        return report

    # Arm first: the fight can start from `fight.join`, but the turns inside it are
    # only taken when the bot is enabled. Arming is idempotent and does not reset
    # the session, so the counters the health check reads survive a repair.
    armed = False
    try:
        arm = executor.execute("farm.start", {})
        armed = bool(getattr(arm, "ok", False))
    except Exception as exc:  # noqa: BLE001 - a repair may not end the run
        report["detail"] = "farm.start raised %s: %s" % (type(exc).__name__, exc)

    groups: List[Any] = []
    try:
        # `map.groups` is not in the planner's vocabulary on purpose: the tool
        # registry is the encoder's label encoding, so a read added for a supervisor
        # must not join it. The page publishes the same listing as `debug.groups`,
        # which is what this calls.
        listing = _page_read(executor, "debug.groups")
        if not getattr(listing, "ok", False):
            # A failed listing is not an empty map. Reporting it as "no monster
            # group" made the repair travel away from a map that had a group on
            # it, and the log looked like a decision instead of an error.
            report["action"] = "listing_failed"
            report["reason"] = "could not list groups: %s" % (
                getattr(listing, "error", None) or "unknown error")
            return report
        groups = _data_of(listing).get("groups") or []
    except Exception as exc:  # noqa: BLE001
        report["action"] = "listing_failed"
        report["reason"] = "could not list groups: %s: %s" % (type(exc).__name__, exc)
        return report

    level = my_level(snapshot)
    if level <= 0:
        report["reason"] = "character level is unreadable, so no group can be priced"
        return report

    group, why = choose_group(groups, level, policy.budget)
    report["reason"] = why
    if group is not None:
        gid = group_id(group)
        report["group"] = group_summary(group)
        report["action"] = "join"
        try:
            joined = executor.execute("fight.join", {"group_id": gid})
        except Exception as exc:  # noqa: BLE001
            report["detail"] = "fight.join raised %s: %s" % (type(exc).__name__, exc)
            return report
        ok = bool(getattr(joined, "ok", False))
        data = _data_of(joined)
        report["detail"] = ("fight.join -> ok=%s %s" % (ok, getattr(joined, "error", None) or ""))
        report["engaged"] = bool(ok and (data.get("in_fight") or data.get("via")))
        if not report["engaged"]:
            report["action"] = "join_failed"
        else:
            # A fight the budget chose: its result is evidence about the budget.
            policy.forced_pending = False
        return report

    if not allow_travel:
        report["action"] = "waiting"
        report["detail"] = "armed=%s, travelling disabled" % armed
        return report

    report["action"] = "travel"
    attempts: List[str] = []
    for direction in policy.travel_order():
        try:
            moved = executor.execute("nav.goto", {"direction": direction})
        except Exception as exc:  # noqa: BLE001
            attempts.append("%s raised %s: %s" % (direction, type(exc).__name__, exc))
            continue
        data = _data_of(moved)
        to_map = data.get("to_map")
        if not bool(getattr(moved, "ok", False)) or to_map is None:
            attempts.append("nav.goto %s -> ok=%s %s" % (
                direction, bool(getattr(moved, "ok", False)),
                getattr(moved, "error", None) or data.get("error") or "no destination"))
            continue
        # An ack is not a transition. The page answers `ok: true` with the map it
        # *asked* for, and on this build the server drops `ChangeMapMessage` because
        # the client published no exit cell for the edge -- measured 2026-11-10: six
        # requests, six acks, the same map. Reporting that as engaged sent the
        # supervisor away believing the character had moved.
        arrived = _map_after(executor, to_map)
        if arrived is None:
            policy.note_travel(direction, to_map)
            report["engaged"] = True
            report["detail"] = ("nav.goto %s -> map %s via %s (unconfirmed: the "
                                "client did not answer a snapshot)"
                                % (direction, to_map, data.get("via") or "walk"))
            return report
        if int(arrived) == int(to_map):
            policy.note_travel(direction, arrived)
            report["engaged"] = True
            report["detail"] = ("nav.goto %s -> map %s via %s"
                                % (direction, arrived, data.get("via") or "walk"))
            return report
        attempts.append("nav.goto %s asked for map %s and stayed on %s"
                        % (direction, to_map, arrived))
    report["action"] = "travel_failed"
    report["attempts"] = attempts
    # The most informative line first: an edge that answered and did not move the
    # character is a different fault from an edge that answered nothing, and only
    # the first one explains a repair that looks like it worked.
    stuck = [line for line in attempts if "stayed on" in line]
    report["detail"] = (stuck[0] if stuck
                        else (attempts[-1] if attempts else "no exit answered"))

    # Nowhere to go and nothing worth fighting. Standing still is the one outcome
    # that cannot teach anything, so take the cheapest fight on the map and say that
    # it was forced -- a loss here is a learning row, a stall is nothing at all.
    fallback = weakest_group(groups)
    if fallback is None:
        return report
    report["group"] = group_summary(fallback)
    report["action"] = "forced_join"
    report["reason"] = ("nothing within budget and nowhere to go; taking the "
                        "weakest group anyway")
    try:
        joined = executor.execute("fight.join", {"group_id": group_id(fallback)})
    except Exception as exc:  # noqa: BLE001
        report["detail"] = "fight.join raised %s: %s" % (type(exc).__name__, exc)
        return report
    ok = bool(getattr(joined, "ok", False))
    data = _data_of(joined)
    report["engaged"] = bool(ok and (data.get("in_fight") or data.get("via")))
    report["detail"] = "fight.join -> ok=%s %s" % (ok, getattr(joined, "error", None) or "")
    # Only a fight that actually started is marked forced. A refused join leaves no
    # fight to label, and a stale flag would mislabel the next chosen fight as one
    # the budget never agreed to.
    policy.forced_pending = bool(report["engaged"])
    if not report["engaged"]:
        # `action` says what happened; `reason` keeps saying why it was attempted.
        # A forced join the client refused is a failed join, not a forced one.
        report["action"] = "join_failed"
    return report


def _map_after(executor: Any, expected: Any, polls: int = 3,
               pause: float = 1.0) -> Optional[int]:
    """The map the client reports after a transition request, or None if unreadable.

    Polled rather than read once: a transition lands a server round trip after the
    ack, so a single immediate read would call a working crossing a failure. Returns
    the first reading that matches `expected`, otherwise the last one read -- the
    caller needs to say *which* map the character stayed on, not just that it did.
    """
    last: Optional[int] = None
    for index in range(max(1, polls)):
        if index:
            time.sleep(pause)
        try:
            snapshot = executor.snapshot()
        except Exception:  # noqa: BLE001
            return last
        if not isinstance(snapshot, dict):
            return last
        position = snapshot.get("position") or {}
        current = snapshot.get("map_id")
        if current is None:
            current = position.get("map_id")
        if current is None:
            return last
        try:
            last = int(current)
        except (TypeError, ValueError):
            return last
        try:
            if expected is not None and last == int(expected):
                return last
        except (TypeError, ValueError):
            return last
    return last


def describe(report: Dict[str, Any]) -> str:
    """One line for a supervisor log."""
    action = report.get("action") or "none"
    group = report.get("group") or {}
    if action == "join":
        what = "group %s (%s monsters, level %s)" % (
            group.get("group_id"), group.get("members"), group.get("total_level"))
    elif action == "travel":
        what = report.get("detail") or "travelling"
    elif action == "already_fighting":
        what = "already in a fight"
    else:
        what = report.get("reason") or report.get("detail") or "nothing to do"
    detail = report.get("detail") or ""
    if detail and detail not in what:
        return "%s: %s (%s)" % (action, what, detail)
    return "%s: %s" % (action, what)
