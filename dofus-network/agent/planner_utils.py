"""Utility functions and constants for the quest planner.

Extracted from agent/planner.py to reduce its size. These are pure helpers
used by QuestPlanner and by external modules (goal routing, refusal handling,
tool argument construction).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent

# Goal lexicon path.
_GOAL_LEXICON_PATH = REPO_ROOT / "data" / "goal_lexicon.json"


def _load_lexicon() -> Dict[str, Any]:
    try:
        if _GOAL_LEXICON_PATH.exists():
            return json.loads(_GOAL_LEXICON_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("ignored %s", exc, exc_info=True)
    return {}


_LEXICON = _load_lexicon()# Goal routing patterns.
_QUEST_PATTERNS = _LEXICON.get("goal_quest") or ["qu[eê]te?s?", "quests?", "objecti(?:f|ve)s?", "missions?"]
_RE_GOAL_QUEST = re.compile(r"\b(" + "|".join(_QUEST_PATTERNS) + r")\b", re.IGNORECASE)

_FARM_PATTERNS = _LEXICON.get("goal_farm") or [
    "farm(?:ing)?", "farmear", "grind", "kill", "tuer", "matar", "mobs?", "monsters?",
    "monstres?", "r[eé]colte[rz]?", "gather", "harvest", "minar", "collecte[rz]?",
]
_RE_GOAL_FARM = re.compile(r"\b(" + "|".join(_FARM_PATTERNS) + r")\b", re.IGNORECASE)

# Legacy farm pattern (pre-2026-11-07) also matched "kamas" and "resources".
_FARM_LEGACY_PATTERNS = _LEXICON.get("goal_farm_legacy") or [
    "farm(?:ing)?", "farmear", "grind", "kill", "tuer", "matar", "mobs?", "monsters?",
    "monstres?", "r[eé]colte[rz]?", "gather", "harvest", "minar", "collecte[rz]?",
    "kamas?", "resources?", "ressources?",
]
_RE_GOAL_FARM_LEGACY = re.compile(r"\b(" + "|".join(_FARM_LEGACY_PATTERNS) + r")\b", re.IGNORECASE)

_AUTO_GOALS = frozenset(_LEXICON.get("auto_goals") or {"auto", "full auto", "fullauto", "autopilot",
                                                      "autonome", "automatic", ""})

# A user asking the bot to play is a Full Auto request, not an order with no call.
# Checked after quest/farm so "can you play the quests?" stays a quest.
_AUTO_PLAY_PATTERNS = _LEXICON.get("auto_play") or [
    "(?:can|could|would|will) you play", "play (?:the )?game", "play for me", "take over",
    "do everything", "you play", "run the game"
]
_RE_GOAL_AUTO_PLAY = re.compile(r"(?:^|[ \t])(?:" + "|".join(_AUTO_PLAY_PATTERNS) + r")", re.IGNORECASE)

_STRIP_PATTERNS = _LEXICON.get("farm_target_strip") or [
    "farm(?:ing)?", "farmear", "grind", "kill", "tuer", "matar", "mobs?", "monsters?", "monstres?",
    "resources?", "ressources?", "r[eé]colte[rz]?", "gather", "harvest", "minar", "collecte[rz]?",
    "kamas?", "xp", "exp", "level", "levels", "please", "the", "a", "an", "some", "des", "du", "de", "le", "la", "les", "un",
    "une", "for", "of", "in", "on", "to", "my", "character", "personnage", "all", "more"
]
_FARM_TARGET_STRIP = re.compile(r"\b(" + "|".join(_STRIP_PATTERNS) + r")\b", re.IGNORECASE)


def _farm_target(goal: str) -> Optional[str]:
    """The named monster/resource in a farm prompt, if one is named."""
    text = _FARM_TARGET_STRIP.sub(" ", goal or "")
    text = re.sub(r"[^A-Za-zÀ-ſ' -]", " ", text)
    words = [word for word in text.split() if len(word) >= 2]
    return " ".join(words[:3]) or None


def _goal_mode(goal: str, legacy: bool = False) -> str:
    """Which planner owns this goal.

    ``legacy`` restores the pre-2026-11-07 farm pattern, which also matched "kamas"
    and "resources". Only the rule router asks for it: the switch is documented as the
    way back to the old behaviour, and a switch that leaves a different set of goals
    on a different planner is not one.
    """
    text = (goal or "").strip()
    if text.lower() in _AUTO_GOALS:
        return "auto"
    if _RE_GOAL_QUEST.search(text):
        return "quest"
    if (_RE_GOAL_FARM_LEGACY if legacy else _RE_GOAL_FARM).search(text):
        return "farm"
    if _RE_GOAL_AUTO_PLAY.search(text):
        return "auto"
    return "order"


# Known static interior doors (a painted star on the floor rather than a border flag).
# Measured live 2026-10-29 & 2026-11-07: stepping on cell 346 outside Albuera house (145490435)
# transitions into Jarl Ordyn's house interior (146276352); stepping on cell 451 inside transitions out.
KNOWN_DOORS: Dict[Tuple[int, int], int] = {
    (145490435, 146276352): 346,
    (146276352, 145490435): 451,
}
KNOWN_NPC_NAMES: Dict[str, int] = {
    "great jarl ordyn": 3892,
    "jarl ordyn": 3892,
    "ordyn": 3892,
    "roki": 3900,
    "foreman kosh": 3896,
    "kosh": 3896,
    "ayin spyr": 3888,
    "ayin": 3888,
    "ganymede": 7581,
    "ganymède": 7581,
    "archie ologist": 8014,
    "edgar kéolog": 8014,
}
KNOWN_NPC_MAPS: Dict[int, int] = {
    3892: 146276352,  # Great Jarl Ordyn in Albuera house interior
    3900: 145490944,  # Roki
    3896: 145489920,  # Foreman Kosh
    3888: 145489920,  # Ayin Spyr
}

GROUP_REFUSALS = ("elsewhere", "empty_map", "unread_members", "other")

def refusal_payload(detail: Any) -> Dict[str, Any]:
    """Find the refusal's own counts inside whatever envelope the bridge sent.

    A directive result is `{directive_payload, response, snapshot}` and the page's answer sits a
    level or two further in, so handing the raw `result.data` over would find no counts and fall
    back to the sentence -- which is exactly the case the counts were added to separate. Bounded
    walk, no recursion, and nothing found is an empty dict, not a guess.
    """
    node = detail
    for _ in range(4):
        if not isinstance(node, dict):
            return {}
        if "groups_seen" in node:
            return node
        for key in ("response", "result", "data", "value"):
            if isinstance(node.get(key), dict):
                node = node[key]
                break
        else:
            return {}
    return {}


def group_refusal_outcome(detail: Any, note: Any = "") -> str:
    """What a "no group for that monster" refusal actually says, from the page's own counts.

    The refusal was one sentence and the loop knew one repair: travel. The page now answers with
    how many groups it iterated and which monster ids it read inside them, which separates cases
    with opposite answers (KNOWN_GAPS G7): the named mob lives on another map (travel), this map
    carries no group at all (travel, or wait for a respawn), and groups *are* here but none of
    their members could be read. The last one is a defect in the read -- travelling the world for
    it spends the rest of the run and never finds the mob, so it must not be routed like the
    others.
    """
    data = refusal_payload(detail)
    seen = data.get("groups_seen")
    if seen is None:
        # No counts: an older page, or a refusal that never reached the client. The sentence is
        # all there is, and keeping it working is the pre-census behaviour, not a guess.
        low = str(note or "").lower()
        return "elsewhere" if "not on this map" in low else "other"
    try:
        seen = int(seen)
    except (TypeError, ValueError):
        return "other"
    if seen <= 0:
        return "empty_map"
    here = data.get("monsters_here")
    if isinstance(here, (list, tuple)) and len(here) > 0:
        return "elsewhere"
    return "unread_members"


CACHE = REPO_ROOT / "data" / "cache"

# The trimmed quest graph is ~21.8 MB. Loaded per planner, 40 instances paid ~872 MB
# for the same bytes. Planners only read this table (every merge copies), so one
# process-wide copy is both correct and what makes 40 instances cheap.
_QUEST_TABLE: Optional[Dict[int, Dict[str, Any]]] = None
_QUEST_TABLE_LOCK = threading.Lock()

def shared_quest_table() -> Dict[int, Dict[str, Any]]:
    """The process-wide quest graph, trimmed once and shared read-only."""
    global _QUEST_TABLE
    if _QUEST_TABLE is not None:
        return _QUEST_TABLE
    with _QUEST_TABLE_LOCK:
        if _QUEST_TABLE is not None:
            return _QUEST_TABLE
        from agent.jsonio import loads as fast_loads

        table: Dict[int, Dict[str, Any]] = {}
        path = CACHE / "quests_full.json"
        if path.exists():
            for q in fast_loads(path.read_bytes()):
                if not q.get("steps"):
                    continue
                for step in q["steps"]:
                    step["objectives"] = step.get("objectives") or []
                    for key in ("id", "name", "description", "optimal_level"):
                        step.pop(key, None)
                for key in ("name", "category_id", "level_max", "is_dungeon",
                            "is_event", "is_party", "start_criterion",
                            "start_position"):
                    q.pop(key, None)
                q["level_min"] = q.get("level_min") or 1
                table[int(q["id"])] = q
        _QUEST_TABLE = table
        return table


# A refused claim is the start of the objective's action path, not the end of the
# quest. The planner retires a ready objective only after this many hard failures
# in a row, or after this many actions that did not even change the map (a walk
# that is crossing the world legitimately changes it, so only a truly inert
# action loop reaches the second bound).
CLAIM_FAILURE_LIMIT = 3
CLAIM_ACTION_LIMIT = 10
# How many times one objective may walk the loop back to the map it just left
# before it is parked for the session. Measured 2026-10-30: dofus-02 made 216
# immediate A->B->A returns in 229 map changes and dofus-05 132 in 204, so the
# crossing itself is the failure (G61); parking only suppresses the *walk*, never
# an action on the objective's own map.
RETURN_LIMIT = 3

# How many crossings towards one objective's map may fail to come nearer before
# travel to it is left alone for this situation. Counted outside the claim
# counters because those reset on every scene change, and a crossing changes the
# map. Larger than CLAIM_ACTION_LIMIT: a legitimate route can need many borders,
# and only a leg that does not reduce the remaining distance is counted here.
ROUTE_STEP_LIMIT = 12

# One crossing away is not a long route. Measured live 2026-11-04: objective
# 13958 of quest 4262 sat on 145490944 with 145490945 one border away, and the
# session sent `nav.goto {map_id: 145490945}` nine times, each one walking the
# character 299 -> 537 and the next back again, each one logged `advanced`
# because the client did move. Nine attempts, 84 seconds of an 18-minute budget,
# and the run then ended on the retirement guard -- all under a limit raised to
# 12 for routes that legitimately cross many borders. A border that has refused
# to be crossed twice is not a distance problem, it is an exit that does not work
# from here: the door, the skill on it, or the gate. Two retries, then explore.
CROSSING_ASK_LIMIT = 2

NAV_WALL_STAGES = frozenset({"change_requested_no_cell", "change_requested_at_edge"})

# How many consecutive unplannable passes the *active* quest may have before it is
# retired. One `None` from `_plan_quest` used to execute it immediately, and a single
# transient situation -- the promise still in flight, the dialog page not yet arrived, a
# visit whose bar was just spent -- was enough to end the session's last live quest
# (2026-11-04/05: quest 4262 retired on one unplannable step, then "nothing plannable",
# then `safe.stop`). The count is keyed on the objective's step and the map, so a quest
# that changes situation is judged again from zero.
ACTIVE_PLAN_NONE_LIMIT = 3

# How many ready objectives one plan step may look at. The client lists them in
# its own quest order, which is not an order of actionability: a live session put
# an un-actionable wrapper ("Complete the X quest", type 0 = kill) at index 0 in
# 4,285 of 4,286 observations while a claimable talk objective sat at index 1,
# and the loop read only index 0 — so `objectives_completed` never left 0 in
# 11.4 hours. Bounded, because a plan step should not price every ready entry.
READY_SCAN_LIMIT = 8

# A level gate is an objective that asks for a level, not a conversation
# ("Reach level 10" on tutorial step 4978). The text is the client's own and the
# verb anchors the match so a kill objective that mentions a monster's level is
# not mistaken for a gate. English and French are the two languages the served
# client has produced; an unknown language leaves the objective to the ordinary
# planner instead of guessing.
LEVEL_GATE_RE = re.compile(
    r"\b(?:reach|attain|atteindre|atteignez|atteins)\b[^0-9]{0,40}?"
    r"\b(?:level|niveau)\b\s*(\d+)",
    re.IGNORECASE)

# A quest the server will not activate is a quest this session cannot do. The
# client answers a `QuestStartRequestMessage` as sent whether or not the server
# accepts it, and a quest with unmet prerequisites never enters `quests_active`;
# a live session re-sent quest 584 every step for its whole budget. Three start
# attempts that the observation says never activated retire it for the session.
START_ATTEMPT_LIMIT = 3

# Passes a kill objective may wait for a group to respawn on its own map before
# the planner walks out and searches. Passes, not seconds: the loop's wait
# cadence is fixed (0.35 s), so 90 is about half a minute of standing still.
SPAWN_WAIT_PASSES = 90

def args_for_tool(tool: str, quest: Dict[str, Any], obj: Dict[str, Any],
                  obs: Dict[str, Any]) -> Dict[str, Any]:
    """The arguments a tool needs, derived from the objective's own parameters.

    This is data movement, not judgement: `parameter0` *is* the npc id. It lives here
    so the teacher, the student and the corpus generator cannot each invent their own
    slightly different idea of what `farm.start` should be given.
    """
    from agent.features import DELIVER_TYPES, _params, declares_monster_target, direction_hint, known_map

    p = _params(obj)
    t = obj.get("type_id")
    if tool == "nav.goto":
        # `parameter0` is not a destination for a DiscoverMap objective; it is
        # the text id of the destination's name. Sending it as a map id is how
        # a live session asked the client to cross to map 698953 nine times.
        need = obj.get("map_id")
        try:
            need = int(need) if need is not None else None
        except (TypeError, ValueError):
            need = None
        if need:
            return {"map_id": need}
        # A discovery objective names no place, so the only legal move is an edge
        # of the current map. This matters to the learned policy too: it picks the
        # tool, then asks this function for the arguments, and an empty nav.goto is
        # refused by the client before anything can be discovered.
        block = obs.get("map") if isinstance(obs, dict) else None
        exits = block.get("exits") if isinstance(block, dict) else None
        candidates = []
        for row in exits or []:
            if not isinstance(row, dict) or row.get("blocked"):
                continue
            direction = row.get("direction")
            try:
                dest = int(row.get("map_id"))
            except (TypeError, ValueError):
                continue
            if direction and dest > 0:
                candidates.append({"direction": direction, "map_id": dest})
        if candidates:
            hint = direction_hint(obj)
            pick = next((e for e in candidates if e["direction"] == hint), None)
            if pick is None:
                pick = next((e for e in candidates if not known_map(e["map_id"])),
                            candidates[0])
            return {"direction": pick["direction"]}
        return {}
    if tool == "quest.advance":
        npc = p.get("parameter0") if t in (1, 9, 3) else p.get("parameter1")
        args: Dict[str, Any] = {"quest_id": int(quest["id"])}
        if npc is not None:
            try:
                args["npc_id"] = int(npc)
            except (TypeError, ValueError) as exc:
                logger.debug("[planner] npc_id parse failed: %s", exc)
        return args
    if tool == "farm.start":
        if t in DELIVER_TYPES:
            # `resource`, not `item`: the client reads `args.resource` to start
            # harvesting, and `item` was rejected by the contract ("unknown argument(s)
            # for farm.start: item"), so show-item, deliver and soul-stone objectives
            # could not be planned at all.
            item = p.get("parameter1") if t == 3 else p.get("parameter0")
            try:
                return {"resource": int(item)} if item is not None else {}
            except (TypeError, ValueError):
                return {}
        if declares_monster_target(obj) is False:
            return {}
        monster = p.get("parameter0")
        try:
            return {"monster": int(monster)} if monster is not None else {}
        except (TypeError, ValueError):
            return {}
    if tool == "fight.join":
        monster = p.get("parameter0")
        try:
            return {"monster": int(monster)} if monster is not None else {}
        except (TypeError, ValueError):
            return {}
    if tool == "inv.equip":
        uid = p.get("object_uid") or p.get("uid")
        try:
            return {"object_uid": int(uid)} if uid is not None else {}
        except (TypeError, ValueError):
            return {}
    if tool == "ui.tap":
        menu = p.get("menu")
        if menu:
            return {"menu": menu}
        return {}
    if tool == "quest.join":
        return {"quest_id": int(quest["id"])}
    return {}


def names_target(call: Dict[str, Any]) -> bool:
    """Whether a planned call names its target in its arguments.

    A call with no target ("nav.goto" with a direction and no map) or in a read.
    Neither names the thing the objective is about, and a live session spent 4,285
    steps on exactly such a detour while a ready objective naming an NPC on a known
    map sat one entry further down the same list. Ranking by "does this call name its
    target" comes from the plan rather than from the objective type, so it covers
    every type the planner can translate.
    """
    from agent.verifier import READ_ONLY_TOOLS

    tool = call.get("tool")
    args = call.get("args") or {}
    if tool in READ_ONLY_TOOLS:
        return False
    if tool == "nav.goto":
        return args.get("map_id") is not None
    if tool == "quest.advance":
        return (args.get("npc_id") is not None
                or args.get("objective_id") is not None)
    if tool in ("fight.join", "farm.start"):
        return bool(args)
    return True


def catalog_objectives(obs: Dict[str, Any], quest_id: Any) -> List[Dict[str, Any]]:
    """Objective rows the page captured for one active quest (G29).

    The client's own graph is the only source that describes a live quest: the
    downloaded cache stops at id 2,546 and the live ids start at 3,841. This is a
    read of `quests_catalog`; it invents nothing and returns [] when the page did
    not capture the quest.
    """
    catalog = obs.get("quests_catalog") if isinstance(obs, dict) else None
    if not isinstance(catalog, list):
        return []
    out: List[Dict[str, Any]] = []
    for quest in catalog:
        if not isinstance(quest, dict):
            continue
        try:
            if int(quest.get("id")) != int(quest_id):
                continue
        except (TypeError, ValueError):
            continue
        for step in quest.get("steps") or []:
            if not isinstance(step, dict):
                continue
            for obj in step.get("objectives") or []:
                if isinstance(obj, dict):
                    out.append(obj)
    return out


def dialog_offer(dialog: Any) -> Optional[int]:
    """The quest this dialog is offering, as the page read it from the client's link.

    G32 publishes `dialog.start_quest` from the client's own `startquest,<id>`
    payload; an offer is a link, not a reply. The planner may only ask to accept
    an id this function returns (G31's rule: never a curriculum or KB id).
    """
    if not isinstance(dialog, dict):
        return None
    try:
        offered = int(dialog.get("start_quest"))
    except (TypeError, ValueError):
        return None
    return offered if offered > 0 else None


# How many times one answer may be asked on one page for one objective before the
# planner treats that page as reply-insatisfiable. Two keeps the third strike as the
# blocked one, which is how the rest of the loop already counts failures.
DIALOG_ASK_LIMIT = 2

# How many replies one conversation may take without the objective changing before the dialog is
# closed. `DIALOG_ASK_LIMIT` answers "did I repeat this exact page", and its key includes the
# dialog's message text -- so a conversation that shows a new page for every answer never trips
# it. Measured live 2026-11-07: eight steps of `quest.advance {reply: 0}`, each accepted with a
# fresh `reply_id`, none advancing the objective, while the quest wanted "equip the recruit set,
# then talk". This bound is about the conversation going nowhere, not about repetition.
DIALOG_STALL_LIMIT = 16

def client_quest_ids(obs: Dict[str, Any]) -> set:
    """Quest IDs the client reports as active in the observation."""
    active = obs.get("quests_active") if isinstance(obs, dict) else None
    if isinstance(active, dict):
        return set(active.keys())
    return set()