"""Pure equipment and inventory utilities extracted from QuestPlanner.

These functions evaluate inventory items, equipment slots, and interactive
elements without depending on planner instance state. Extracted from
agent/planner.py to reduce its size and improve separation of concerns.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agent.features import _params, TALK_TYPES
from agent.planner_utils import KNOWN_NPC_NAMES

logger = logging.getLogger(__name__)


def element_usable(row: Dict[str, Any]) -> bool:
    """Whether the page can send a use for this element at all.

    The client's own interactive entry carries the skill instance uid; when
    both it and the enabled list are empty there is nothing to send, and the
    page's refusal is a fact about the element, not about the plan.
    """
    try:
        if row.get("skill_uid") is not None:
            return True
    except AttributeError:
        return False
    skills = row.get("enabled_skills")
    return isinstance(skills, list) and bool(skills)

def worn_slot_for(obs: Dict[str, Any], uid: Any) -> Optional[str]:
    """The slot a copy of the same template already occupies, when visible.

    The page publishes the bag as `{gid, qty, object_uid}` and what is worn as
    `{slot, gid}`, so a second copy of a worn item names its own slot without
    guessing a type table. Live 2026-10-29: 76 refused `inv.equip` calls came
    from item types that declare no positions, and every refusal asked for a
    slot; the worn copy's gid is the client's own answer for where that
    template goes.

    Returns the wire form, not the number: `inv.equip.slot` is declared `str`
    ("'weapon' or 0-16") and `validate_call` rejects an int, so a producer that
    returned an int was refused at the executor no matter how right the slot
    was. The conversion lives here, at the source, so a new caller cannot
    reintroduce it by forgetting a `str()`.
    """
    bag = obs.get("inventory") if isinstance(obs, dict) else None
    if not isinstance(bag, dict):
        return None
    gid = None
    declared: List[int] = []
    for row in bag.get("items") or []:
        if not isinstance(row, dict):
            continue
        try:
            if int(row.get("object_uid") or row.get("uid")) != int(uid):
                continue
        except (TypeError, ValueError):
            continue
        gid = row.get("gid")
        try:
            declared.append(int(row.get("gid")))
        except (TypeError, ValueError) as exc:
            logger.warning("[equipment_utils] gid append failed: %s", exc)
    if gid is None:
        return None
    block = obs.get("map") if isinstance(obs.get("map"), dict) else {}
    # The page publishes what is worn *inside the bag*: `snap.inventory.equipped`
    # (`scripts/mobile-disguise.js`, built from the client's `equippedItems`). The
    # map block and the observation root carry no `equipped` list, so reading
    # those two alone found nothing and every equip call went out slotless --
    # the refusal this function exists to prevent.
    equipped = (bag.get("equipped") or block.get("equipped")
                or obs.get("equipped") or [])
    worn_slot = None
    for row in equipped:
        if not isinstance(row, dict):
            continue
        try:
            if int(row.get("gid")) == int(gid):
                worn_slot = row.get("slot")
                break
        except (TypeError, ValueError):
            continue
    return str(worn_slot) if worn_slot is not None else None

def element_named_by(obj: Dict[str, Any], row: Dict[str, Any]) -> bool:
    """Whether the objective's parameter is this element's type."""
    param = _params(obj).get("parameter0")
    etype = row.get("element_type_id")
    try:
        return param is not None and etype is not None and int(param) == int(etype)
    except (TypeError, ValueError):
        return False


def quest_target_npcs(obs: Dict[str, Any]) -> Dict[int, List[int]]:
    """NPCs an open quest objective names, by template id.

    The client's quest marker over an NPC is paint, not data: the served
    bundle has no indicator field. The graph behind it does carry the fact --
    a talk/go-to-NPC objective's `parameter0` is the NPC's template id -- so
    this reads the source the paint is drawn from. Keys are npc ids, values
    are the objective ids that name them.
    """
    out: Dict[int, List[int]] = {}

    def add(item: Any) -> None:
        if not isinstance(item, dict):
            return
        try:
            type_id = int(item.get("type_id"))
        except (TypeError, ValueError):
            type_id = None
        name = item.get("type")
        looks_like_npc = type_id in TALK_TYPES or (
            isinstance(name, str) and name.lower().startswith("questobjectivegoto"))
        if not looks_like_npc:
            return
        try:
            npc = int(_params(item).get("parameter0"))
        except (TypeError, ValueError):
            return
        if npc <= 0:
            return
        try:
            oid = int(item.get("id", item.get("objective_id")))
        except (TypeError, ValueError):
            oid = 0
        bucket = out.setdefault(npc, [])
        if oid and oid not in bucket:
            bucket.append(oid)

    def check_text(item: Any) -> None:
        if not isinstance(item, dict):
            return
        text = str(item.get("text") or "").lower()
        if not text:
            return
        for kw, nid in KNOWN_NPC_NAMES.items():
            if kw in text:
                try:
                    oid = int(item.get("id", item.get("objective_id")))
                except (TypeError, ValueError):
                    oid = 0
                bucket = out.setdefault(nid, [])
                if oid and oid not in bucket:
                    bucket.append(oid)

    for quest in obs.get("quests_catalog") or []:
        if not isinstance(quest, dict):
            continue
        for step in quest.get("steps") or []:
            if not isinstance(step, dict):
                continue
            for objective in step.get("objectives") or []:
                add(objective)

    for item in obs.get("ready_objectives") or []:
        add(item)
        check_text(item)
    for item in obs.get("step_objectives") or []:
        add(item)
        check_text(item)
    if isinstance(obs.get("current_objective"), dict):
        add(obs["current_objective"])
        check_text(obs["current_objective"])
    return out


def _tutorial(obs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The scripted tutorial block, when the client says a step is running.

    The page publishes this block itself (`scripts/mobile-disguise.js` builds
    `active`, `quest_id`, `step_id`, `special_step_id`, `hint_target`,
    `interactive_hint`, `tap_anywhere`, `unblocked_cells`, ...). The planner
    reads the step's own gates from it instead of deriving a plan the client
    would refuse, so this is an accessor, not a heuristic.
    """
    block = obs.get("tutorial") if isinstance(obs, dict) else None
    if not isinstance(block, dict) or not block.get("active"):
        return None
    return block


def _tutorial_quest(obs: Dict[str, Any],
                    tutorial: Dict[str, Any]) -> Optional[int]:
    """The quest the scenario is driving, from the block's own quest id."""
    try:
        qid = int((tutorial or {}).get("quest_id"))
    except (TypeError, ValueError):
        return None
    return qid if qid > 0 else None