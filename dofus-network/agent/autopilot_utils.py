"""Pure helper functions extracted from agent/autopilot.py.

These functions implement pure logic without depending on Autopilot instance state.
Extracted to reduce autopilot.py's size and improve separation of concerns.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

from agent.features import DELIVER_TYPES
from agent.planner_utils import _goal_mode

logger = logging.getLogger(__name__)


def session_close_enabled(environ: Optional[Dict[str, str]] = None,
                          dry_run: bool = False) -> bool:
    """Whether a finished run may hand itself to the close pipeline.

    A dry run never closes a session: it wrote no new rows, and the pipeline
    would rescan the log for nothing. The switch is the same `AUTOPILOT_*`
    family the rest of the loop uses.
    """
    import os
    if dry_run:
        return False
    env = environ if environ is not None else os.environ
    return str(env.get("AUTOPILOT_SESSION_CLOSE", "1")).lower() not in (
        "0", "off", "false", "no")


def settle_deadline(tool: str, started: float, last_change: float) -> float:
    """The deadline for a settle operation based on tool type."""
    import time
    return started + 30.0


ABSENT_CLIENT_MARKERS = (
    "no client instance", "not an active client", "no client window is open",
)


def client_absent(error: Any) -> bool:
    """Whether the host's error says no client window exists at all.

    The words are the host's own (desktop/agentbridge.cs), not a generic
    "absent": an in-game refusal can contain that word while a client is very
    much open, and ending the wait on it would abandon a healthy session.
    """
    text = str(error or "").lower()
    return any(marker in text for marker in ABSENT_CLIENT_MARKERS)


def _work_remains(obs: Any) -> bool:
    """Whether there is still work remaining in the observation."""
    if not isinstance(obs, dict):
        return False
    return bool(obs.get("in_fight") or obs.get("in_dialog") or obs.get("ready_objectives"))


def fell_out_of_world(before: Any, after: Any) -> Optional[str]:
    """Whether the character fell out of world between two observations."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return None
    if before.get("in_world") and not after.get("in_world"):
        return "fell_out_of_world"
    return None


def _game_error(result: Any) -> Optional[str]:
    """Extract an error message from a game result, or None."""
    if isinstance(result, dict):
        return result.get("error")
    return None


def brain_inputs() -> Dict[str, str]:
    """The default brain input mappings."""
    return {"in_fight": "in_fight", "in_preparation": "in_preparation"}


def brain_build() -> str:
    """The brain build identifier."""
    import time
    return f"brain-{int(time.time())}"


def _stamp_brain_build(blob: bytes) -> bytes:
    """Stamp a brain build identifier onto a blob."""
    return blob + b"\n" + brain_build().encode("utf-8")


def _accepts_detail(fn: Any) -> bool:
    """Whether a callable accepts a detail keyword argument."""
    import inspect
    try:
        sig = inspect.signature(fn)
        return "detail" in sig.parameters
    except (ValueError, TypeError):
        return False


def _failure_evidence(result: Any) -> Optional[Dict[str, Any]]:
    """Extract failure evidence from a result, or None."""
    if isinstance(result, dict):
        return result.get("evidence")
    return None


def _summarise(result: Any) -> str:
    """Produce a one-line summary of a result."""
    if isinstance(result, dict):
        return str(result.get("ok", result))
    return str(result)


def _tutorial_active(obs: Optional[Dict[str, Any]]) -> bool:
    """Whether the tutorial is currently active."""
    if not isinstance(obs, dict):
        return False
    tutorial = obs.get("tutorial")
    if isinstance(tutorial, dict):
        return bool(tutorial.get("active"))
    return False


def _compact(obs: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Produce a compact representation of an observation."""
    if not isinstance(obs, dict):
        return {}
    return {k: v for k, v in obs.items() if v is not None}


def _world_sig(obs: Optional[Dict[str, Any]]) -> str:
    """Produce a signature string for a world observation."""
    if not isinstance(obs, dict):
        return ""
    return f"{obs.get('map_id','?')}:{obs.get('cell','?')}"


def _standing_on_exit(args: Optional[Dict[str, Any]]) -> bool:
    """Whether the character is standing on an exit."""
    if not isinstance(args, dict):
        return False
    return bool(args.get("on_exit"))


def _call_key(call: Any) -> str:
    """Produce a string key for a tool call."""
    if isinstance(call, dict):
        return f"{call.get('tool','?')}:{call.get('args',{})}"
    return str(call)


def _cell_of(obs: Optional[Dict[str, Any]]) -> Optional[int]:
    """Extract the cell number from an observation."""
    if not isinstance(obs, dict):
        return None
    position = obs.get("position")
    if isinstance(position, dict):
        return position.get("cell")
    return obs.get("cell")


def _map_of(obs: Optional[Dict[str, Any]]) -> Optional[int]:
    """Extract the map id from an observation."""
    if not isinstance(obs, dict):
        return None
    position = obs.get("position")
    if isinstance(position, dict):
        return position.get("map_id")
    return obs.get("map_id")


def _load_goals(args: argparse.Namespace) -> List[str]:
    """Load goals from command-line arguments."""
    goals = getattr(args, "goal", None) or []
    if isinstance(goals, str):
        return [goals]
    return list(goals)


def _build_policy(args: argparse.Namespace) -> Any:
    """Build a policy from command-line arguments."""
    from agent.planner import QuestPlanner
    return QuestPlanner()


def _base_policy(args: argparse.Namespace) -> Any:
    """Build the base policy from command-line arguments."""
    return _build_policy(args)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(description="Run the autopilot agent loop")
    parser.add_argument("--goal", action="append", help="Goal to pursue")
    parser.add_argument("--steps", type=int, default=0, help="Maximum steps")
    parser.add_argument("--policy", default="planner", help="Policy to use")
    return parser


def _obs_map(obs: Any) -> Optional[int]:
    """Extract the map id from an observation."""
    if not isinstance(obs, dict):
        return None
    for value in (
        obs.get("map_id"),
        (obs.get("position") or {}).get("map_id"),
        (obs.get("map") or {}).get("map_id"),
    ):
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _obs_cell(obs: Any) -> Optional[int]:
    """Extract the cell number from an observation."""
    if not isinstance(obs, dict):
        return None
    for value in (
        obs.get("cell"),
        (obs.get("position") or {}).get("cell"),
        (obs.get("map") or {}).get("cell"),
    ):
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _dialog_signature(dialog: Dict[str, Any]) -> Tuple[Any, ...]:
    """The parts of a dialog block that only change when the page does."""
    replies = dialog.get("replies")
    if isinstance(replies, list):
        replies = tuple(
            str(r) if not isinstance(r, dict) else str(r.get("text"))
            for r in replies
        )
    else:
        replies = None
    return (
        bool(dialog.get("open")),
        dialog.get("npc_id"),
        dialog.get("message"),
        dialog.get("reply_text"),
        replies,
    )


def _action_complete(call: Any, before: Any, after: Any) -> bool:
    """Has the action reached the state it promised, not just started?"""
    if not isinstance(after, dict) or not after:
        return False
    before = before if isinstance(before, dict) else {}
    tool = str(getattr(call, "tool", "") or "")
    args = getattr(call, "args", {}) or {}
    if tool == "nav.goto":
        before_map, after_map = _obs_map(before), _obs_map(after)
        if before_map is not None and after_map is not None and after_map != before_map:
            return True
        target = args.get("map_id")
        if target is not None:
            try:
                if after_map != int(target):
                    return False
            except (TypeError, ValueError):
                return False
        target_cell = args.get("cell")
        if target_cell is not None:
            try:
                return _obs_cell(after) == int(target_cell)
            except (TypeError, ValueError):
                return False
        return target is not None
    if tool == "quest.advance":
        a_dlg = after.get("dialog")
        b_dlg = before.get("dialog")
        starting = args.get("accept") or (
            args.get("quest_id") is not None and not any(
                args.get(key) is not None for key in
                ("objective_id", "npc_id", "reply", "reply_text", "close")))
        if starting:
            qid = str(args.get("quest_id"))
            return any(qid == str(value) for key in ("quests_active", "quests_done")
                       for value in (after.get(key) or []))
        if args.get("close"):
            return after.get("in_dialog") is False or (
                isinstance(a_dlg, dict) and a_dlg.get("open") is False)
        elif args.get("reply") is not None or args.get("reply_text") is not None:
            if isinstance(a_dlg, dict) and _dialog_signature(a_dlg) != _dialog_signature(
                b_dlg if isinstance(b_dlg, dict) else {}
            ):
                return True
        if after.get("in_dialog") and not before.get("in_dialog"):
            return True
        for key in ("objectives_completed", "ready_objectives", "quest_steps"):
            if (key in before and key in after and after[key] is not None
                    and before[key] is not None and before[key] != after[key]):
                return True
        b_obj = before.get("current_objective") or {}
        a_obj = after.get("current_objective") or {}
        b_id = b_obj.get("objective_id", b_obj.get("id"))
        a_id = a_obj.get("objective_id", a_obj.get("id"))
        if b_id is not None and a_id is not None and b_id != a_id:
            return True
        before_map, after_map = _obs_map(before), _obs_map(after)
        return before_map is not None and after_map is not None and after_map != before_map
    if tool == "fight.join":
        return bool(after.get("in_fight"))
    if tool == "farm.start":
        return bool(after.get("in_fight") or after.get("auto_fight"))
    if tool == "tap.interactive":
        from agent.verifier import _get, changed_keys
        effects = {"map_id", "mapId", "in_dialog", "in_fight", "inventory_size", "xp",
                   "ready_objectives", "objectives_completed", "quest_steps"}
        observed = (key for key in changed_keys(before, after)
                    if _get(before, key) is not None and _get(after, key) is not None)
        return bool(effects.intersection(observed)) or (
            before.get("open_windows") is not None and after.get("open_windows") is not None
            and before["open_windows"] != after["open_windows"])
    return _changed_keys(before, after)


def _farm_counters(observation: Dict[str, Any]) -> Optional[tuple]:
    """(fights, kills, xp, kamas, drops) from the page session, if present."""
    session = observation.get("farm_session") if isinstance(observation, dict) else None
    if not isinstance(session, dict):
        return None
    return (
        session.get("fights"),
        session.get("kills"),
        session.get("xp"),
        session.get("kamas"),
        session.get("drop_count"),
    )


def _farm_allowed(goal: str, obs: Optional[Dict[str, Any]]) -> bool:
    """A quests prompt may farm only to gather a delivery item."""
    if _goal_mode(goal) != "quest":
        return True
    obj = (obs or {}).get("current_objective") or {}
    try:
        return int(obj.get("type_id")) in DELIVER_TYPES
    except (TypeError, ValueError):
        return False


def _bridge_alive() -> bool:
    """Whether the bridge is currently alive."""
    return True


def _world_screen(obs: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Name the screen the world wait is looking at.

    A wait that only persisted an elapsed time could not say which screen had
    held it, so the recovery row could not be triaged. The states are the ones
    the wait can actually be stuck on, read from the snapshot the loop already
    carries: no page, login, character selection, or in world.
    """
    if not isinstance(obs, dict):
        return {"state": "no_snapshot"}
    if obs.get("in_world") is True:
        return {"state": "in_world"}
    if obs.get("in_world") is False:
        character = obs.get("character")
        name = character.get("name") if isinstance(character, dict) else None
        if name:
            return {"state": "character_selection", "character": name}
        return {"state": "login"}
    return {"state": "unknown"}


def _screen_text(screen: Optional[Dict[str, Any]]) -> str:
    """Format the screen name for the wait's log line."""
    if not isinstance(screen, dict):
        return ""
    state = str(screen.get("state") or "")
    character = screen.get("character")
    if state and character:
        return "%s (%s)" % (state, character)
    return state


def _login_banned(obs: Optional[Dict[str, Any]]) -> bool:
    """Whether the page's login screen is refusing this account for good.

    The page publishes `login` from the client's own popup text ("Your account
    has been permanently banned"); an absent or malformed block is "not
    banned", because guessing a ban would end a healthy session.
    """
    if not isinstance(obs, dict):
        return False
    block = obs.get("login")
    return bool(isinstance(block, dict) and block.get("banned"))


def _login_lockout(obs: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The page's login cooldown once it has escalated past a slow server.

    The page's backoff is 10 min after the first refused guest press, then
    60 min, then 3 h. One refusal is worth waiting out inside the run; from
    the second on the slot stays shut for an hour and only a per-slot reset
    (a fresh guest) or waiting changes the answer, so the wait ends with the
    reason instead of parking silently.
    """
    if not isinstance(obs, dict):
        return None
    cool = obs.get("login_cooldown")
    if not isinstance(cool, dict):
        return None
    try:
        remaining = float(cool.get("remaining_s") or 0)
        fails = int(cool.get("fails") or 0)
    except (TypeError, ValueError):
        return None
    if remaining < 1800 and fails < 2:
        return None
    return {"reason": cool.get("reason"),
            "remaining_s": remaining,
            "fails": fails}


def _quest_data_pending(obs: Any) -> bool:
    """In the world with no quest block at all -- a snapshot taken too early.

    The tell is the bag: a character that has been in the world long enough to hold items
    has quests too, so an empty `quests_active` next to a non-empty bag means the client
    has not published them yet. A genuinely questless character with an empty bag falls
    through to the old verdict, and the caller bounds the wait anyway.

    Measured 2026-11-07: right after a guest login the observation carried `in_world: true`,
    four bag items and `quests_active: {}` / `ready_objectives: []` / `tutorial.active: false`,
    while quests 3841 and 4260 were active minutes earlier. The policy returned no call, the
    controller called the goal unmappable, and a 14-step run ended after one step. Waiting is
    the right answer, and it must be bounded so a genuinely questless character still ends.
    """
    if not isinstance(obs, dict):
        return False
    if obs.get("quest_data_pending"):
        # A publisher may say it outright; that is a wait, never a reason not to.
        return True
    if not obs.get("in_world"):
        return False
    if obs.get("quests_active") or obs.get("ready_objectives"):
        return False
    if obs.get("current_objective"):
        return False
    tutorial = obs.get("tutorial")
    if isinstance(tutorial, dict) and tutorial.get("active"):
        return False
    inventory = obs.get("inventory")
    items = inventory.get("items") if isinstance(inventory, dict) else None
    return bool(items)


def _changed_keys(before: Any, after: Any) -> bool:
    """Check if any keys changed between before and after dicts."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return before != after
    before_keys = set(before.keys())
    after_keys = set(after.keys())
    if before_keys != after_keys:
        return True
    for key in before_keys & after_keys:
        if before[key] != after[key]:
            return True
    return False