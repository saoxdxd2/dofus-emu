"""Pure helper functions extracted from agent/autopilot.py.

These functions implement pure logic without depending on Autopilot instance state.
Extracted to reduce autopilot.py's size and improve separation of concerns.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

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


def client_absent(error: Any) -> bool:
    """Whether an error indicates the client is absent."""
    return error is not None and "absent" in str(error).lower()


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
        try:
            return int(value) if value is not None else None
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
        try:
            return int(value) if value is not None else None
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
        if bool(after_map) and after_map != before_map:
            return True
        target = args.get("map_id")
        if target is not None:
            try:
                return _obs_map(after) == int(target)
            except (TypeError, ValueError):
                return False
        target_cell = args.get("cell")
        if target_cell is not None:
            try:
                return _obs_cell(after) == int(target_cell)
            except (TypeError, ValueError):
                return False
        return False
    if tool == "quest.advance":
        a_dlg = after.get("dialog")
        b_dlg = before.get("dialog")
        if args.get("close"):
            if before.get("in_dialog") and not after.get("in_dialog"):
                return True
        elif args.get("reply") is not None or args.get("reply_text") is not None:
            if isinstance(a_dlg, dict) and _dialog_signature(a_dlg) != _dialog_signature(
                b_dlg if isinstance(b_dlg, dict) else {}
            ):
                return True
        if after.get("in_dialog") and not before.get("in_dialog"):
            return True
        if after.get("objectives_completed") != before.get("objectives_completed"):
            return True
        b_obj = before.get("current_objective")
        a_obj = after.get("current_objective")
        if (a_obj or {}).get("objective_id") != (b_obj or {}).get("objective_id"):
            return True
        before_map, after_map = _obs_map(before), _obs_map(after)
        return bool(after_map) and after_map != before_map
    if tool in ("fight.join", "farm.start"):
        if after.get("in_fight") and not before.get("in_fight"):
            return True
        return bool(after.get("auto_fight")) and not bool(before.get("auto_fight"))
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
    """Whether a farm order is allowed given the current observation."""
    if not isinstance(obs, dict):
        return False
    if not obs.get("auto_fight"):
        return False
    return True


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
    """Whether the login is banned."""
    if not isinstance(obs, dict):
        return False
    return obs.get("banned", False)


def _login_lockout(obs: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Lockout info for login."""
    if not isinstance(obs, dict):
        return None
    return obs.get("lockout")


def _quest_data_pending(obs: Any) -> bool:
    """Whether quest data is still pending."""
    if not isinstance(obs, dict):
        return False
    return obs.get("quest_data_pending", False)


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