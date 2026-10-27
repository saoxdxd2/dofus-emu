"""Agent 2's planner: the quest graph turned into tool calls.

The verifier answers "did that work"; the controller decides "what now"; this is
the missing "what is the *plan*". It reads the knowledge base (1,976 quests with
machine-readable objectives), picks a quest, and translates the current objective
into the one call the actor should make next.

Two deliberate choices:

* **The translation is deterministic.** `objective type 4 → nav.goto(map_id)` is a
  fact about the data, not something to learn. Learning it would be a way to get it
  wrong. What *is* learned later is which quest to pick and what to do when a step
  fails.
* **It re-plans every step.** The planner is a pure function of (observation, KB),
  so a world change is picked up on the next call with no invalidation logic — which
  is the reactivity that action-chunking approaches give up.

`choose()` implements the same `Policy` protocol as the rule policy, so the loop
does not need to know which one it is talking to.
"""

from __future__ import annotations

import random
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from agent.observe import flatten
from agent.jsonio import loads as fast_loads
from agent.dialog_policy import rank_dialog
from agent.awareness import quest_key, scene_key
from agent.features import (CRAFT_TYPES, DELIVER_TYPES,  # noqa: E402
                            KILL_TYPES, MAP_TYPES, ObsTracker, TALK_TYPES,
                            _params, declares_monster_target, direction_hint,
                            known_map, required_map, travel_hops)
from agent.verifier import READ_ONLY_TOOLS  # noqa: E402

# Prompt routing. The panel pins the planner, so it must decide from the goal
# text whether the user asked for quests, a farm session, or full auto; the old
# planner always ran quests and a quest kill objective could start a farm loop.
_RE_GOAL_QUEST = re.compile(
    r"\b(qu[eê]te?s?|quests?|objecti(?:f|ve)s?|missions?)\b", re.IGNORECASE)
_RE_GOAL_FARM = re.compile(
    r"\b(farm(?:ing)?|farmear|grind|kill|tuer|matar|kamas?|mobs?|monsters?|"
    r"monstres?|resources?|ressources?|r[eé]colte[rz]?|gather|harvest|minar|"
    r"collecte[rz]?)\b", re.IGNORECASE)
_AUTO_GOALS = frozenset({"auto", "full auto", "fullauto", "autopilot",
                         "autonome", "automatic", ""})
# A user asking the bot to play is a Full Auto request, not an order with no
# call. Checked after quest/farm so "can you play the quests?" stays a quest.
_RE_GOAL_AUTO_PLAY = re.compile(
    r"(?:^|[ 	])(?:(?:can|could|would|will) you play|play (?:the )?game|"
    r"play for me|take over|do everything|you play|run the game)",
    re.IGNORECASE)
_FARM_TARGET_STRIP = re.compile(
    r"\b(farm(?:ing)?|farmear|grind|kill|tuer|matar|mobs?|monsters?|monstres?|"
    r"resources?|ressources?|r[eé]colte[rz]?|gather|harvest|minar|collecte[rz]?|"
    r"kamas?|xp|exp|level|levels|please|the|a|an|some|des|du|de|le|la|les|un|"
    r"une|for|of|in|on|to|my|character|personnage|all|more)\b", re.IGNORECASE)


def _farm_target(goal: str) -> Optional[str]:
    """The named monster/resource in a farm prompt, if one is named."""
    text = _FARM_TARGET_STRIP.sub(" ", goal or "")
    text = re.sub(r"[^A-Za-zÀ-ſ' -]", " ", text)
    words = [word for word in text.split() if len(word) >= 2]
    return " ".join(words[:3]) or None


def _goal_mode(goal: str) -> str:
    text = (goal or "").strip()
    if text.lower() in _AUTO_GOALS:
        return "auto"
    if _RE_GOAL_QUEST.search(text):
        return "quest"
    if _RE_GOAL_FARM.search(text):
        return "farm"
    if _RE_GOAL_AUTO_PLAY.search(text):
        return "auto"
    return "order"


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
    their members could be read. The last one is a defect in the read — travelling the world for
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

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE = REPO_ROOT / "data" / "cache"

# A refused claim is the start of the objective's action path, not the end of the
# quest. The planner retires a ready objective only after this many hard failures
# in a row, or after this many actions that did not even change the map (a walk
# that is crossing the world legitimately changes it, so only a truly inert
# action loop reaches the second bound).
CLAIM_FAILURE_LIMIT = 3
CLAIM_ACTION_LIMIT = 10

# How many ready objectives one plan step may look at. The client lists them in
# its own quest order, which is not an order of actionability: a live session put
# an un-actionable wrapper ("Complete the X quest", type 0 = kill) at index 0 in
# 4,285 of 4,286 observations while a claimable talk objective sat at index 1,
# and the loop read only index 0 — so `objectives_completed` never left 0 in
# 11.4 hours. Bounded, because a plan step should not price every ready entry.
READY_SCAN_LIMIT = 8

# A quest the server will not activate is a quest this session cannot do. The
# client answers a `QuestStartRequestMessage` as sent whether or not the server
# accepts it, and a quest with unmet prerequisites never enters `quests_active`;
# a live session re-sent quest 584 every step for its whole budget. Three start
# attempts that the observation says never activated retire it for the session.
START_ATTEMPT_LIMIT = 3


def args_for_tool(tool: str, quest: Dict[str, Any], obj: Dict[str, Any],
                  obs: Dict[str, Any]) -> Dict[str, Any]:
    """The arguments a tool needs, derived from the objective's own parameters.

    This is data movement, not judgement: `parameter0` *is* the npc id. It lives here
    so the teacher, the student and the corpus generator cannot each invent their own
    slightly different idea of what `farm.start` should be given.
    """
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
            except (TypeError, ValueError):
                pass
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
        # Both monster routes need a creature id, and `parameter0` is one only when the
        # client's own objective-parameter table says so. A text-only class carries a
        # number the client formats into the objective text; hunting it is how a live
        # session ordered `fight.join` on 715354 and then walked the world for it.
        if declares_monster_target(obj) is False:
            return {}
        monster = p.get("parameter0")
        try:
            return {"monster": int(monster)} if monster is not None else {}
        except (TypeError, ValueError):
            return {}
    return {}


def client_quest_ids(obs: Dict[str, Any]) -> set:
    """Every quest id the running client reported in this snapshot.

    The four places the client names a quest: the active log, the finished list, the
    tracker it is holding, and the ready-objective entries. Read defensively because the
    page mixes string and numeric ids (`"3841"` and `3841` both appear), and an id that
    cannot be read as an int is not evidence of anything.
    """
    out: set = set()
    for raw in list((obs.get("quests_active") or {}).keys()) + list(obs.get("quests_done") or []):
        try:
            out.add(int(raw))
        except (TypeError, ValueError):
            continue
    sources = [obs.get("current_objective")]
    sources += list(obs.get("ready_objectives") or [])
    for entry in sources:
        if isinstance(entry, dict) and entry.get("quest_id") is not None:
            try:
                out.add(int(entry["quest_id"]))
            except (TypeError, ValueError):
                continue
    return out


def names_target(call: Dict[str, Any]) -> bool:
    """Whether a planned call states the actor or place the objective needs.

    A ready objective's own action path can degenerate into a detour: hunting a
    group this map does not report ends in "walk out an exit and look" (`nav.goto`
    with a direction and no map) or in a read. Neither names the thing the
    objective is about, and a live session spent 4,285 steps on exactly such a
    detour while a ready objective naming an NPC on a known map sat one entry
    further down the same list. Ranking by "does this call name its target" comes
    from the plan rather than from the objective type, so it covers every type the
    planner can translate.
    """
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


class QuestPlanner:
    """A policy that plans over the quest graph."""

    def __init__(self, level_hint: int = 60, max_quest_level: int = 200,
                 store: Any = None, curriculum: str = "greedy",
                 seed: int = 0, focus: Optional[int] = None,
                 ordering: str = "step") -> None:
        # Loaded on first quest access. Farm/order goals never plan a quest,
        # so paying ~19 MiB and a JSON parse before the first directive was
        # pure waste; the property keeps the public table contract for tools
        # and tests that do need it.
        self._quests: Dict[int, Dict[str, Any]] = {}
        self._quests_loaded = False
        self.level_hint = level_hint
        self.max_quest_level = max_quest_level
        self.current_quest: Optional[int] = None
        self.last_dialog_decision: Optional[Dict[str, Any]] = None
        # Which objective the call that `choose` just returned is about. The corpus
        # needs this: featurising the world's oldest active objective labelled
        # actions for a different quest whenever the planner skipped one.
        self.current_objective: Optional[int] = None
        self._order: Optional[List[int]] = None
        # Monotone memory of what the observation ever said was finished. The
        # planner is otherwise a pure function of the observation, which is correct
        # only while the observation is complete — and it is not: the client pages
        # the quest log, and a paged list made this planner re-pick a finished quest
        # for four hundred consecutive steps. A fact once observed cannot un-happen,
        # so remembering it costs nothing and removes a whole class of loop.
        self._seen_done: set = set()
        # Every quest id the running client has shown this session -- its active list, its
        # finished list, the tracker and the ready entries. A start request is only
        # sendable for a quest *this game* has, and the cached tables are another game's
        # (G29): all 366 bare `quest.advance {quest_id}` rows in the live corpus named an
        # id the client never reported, 297 of them one Dofus 2 quest (G31, and the number
        # itself was first read as 462-of-1,145 because talk and claim calls carry a
        # quest_id too -- see tools/quest_advance_shapes.py). The 3-attempt bound did not
        # stop the repetition because a scene change clears it. Monotone for the same
        # reason `_seen_done` is: a quest this game has shown does not un-exist when the
        # character walks to the next map.
        self._client_quests: set = set()
        # Quests the planner has looked at and cannot plan (area objectives, crafts
        # with no recipe). Retiring them keeps the loop from re-deriving the same
        # dead end every step.
        self._skipped: set = set()
        # Why each *recoverable* retirement happened, so a changed world can undo
        # it. Quests absent from this map were retired permanently.
        self._skip_sigs: Dict[int, Tuple[Any, ...]] = {}
        # The observation the last decision was made from, so a retirement asked
        # for by the loop (`skip`) can record the situation it happened in.
        self._last_obs: Optional[Dict[str, Any]] = None
        # A targeted farm order that the page refused because the mob is not on
        # this map. The next farm plan walks an exit instead of asking again.
        self._farm_target_missing = False
        # Which kind of group refusal the page last gave. Its counts answer more than its
        # sentence does, and one of the four outcomes must not be routed like the others.
        self._group_refusals = {key: 0 for key in GROUP_REFUSALS}
        self._group_read_broken = False
        # A dialog whose reply list stays empty is a dead end; after a short
        # readiness grace the planner closes it rather than polling forever.
        self._dialog_waits = 0
        self._dialog_key: Any = None
        # A page that came back unchanged after an answer will come back unchanged
        # again: the answer is a function of the page, not of luck. The baseline asked
        # the same reply on the same page 87 times against one objective. Count the
        # asks per (npc, page, reply, objective) and stop once they buy nothing.
        self._dialog_asks: Dict[Tuple[Any, ...], int] = {}
        self._dialog_exhausted: set = set()
        # Plain orders (sell, inventory, travel) are already parsed by the rule
        # policy; the planner only owns quests/farm/auto. Built lazily.
        self._rule_policy: Any = None
        # What the last plan was made against. A claim/retry/start attempt from
        # another map, quest step, dialogue or group set is not evidence about
        # this one; the awareness layer resets those transients on change.
        self._scene_key: Optional[str] = None
        self._quest_key: Optional[str] = None
        self._plannable_cache: Dict[int, bool] = {}
        # "greedy" is the shipping policy: fewest objectives first. "random" exists
        # because a corpus must cover the decisions an agent faces, and the greedy
        # curriculum does not: it always finishes the shortest quests first, so the
        # state distribution it produces is whatever the shortest quests happen to
        # contain. That is a property of the curriculum, not of the game.
        self.curriculum = curriculum
        self.rng = random.Random(seed)
        # A corpus builder may choose which states to include. Following one policy's
        # preferences instead is how a corpus ends up 87% deliveries: the greedy
        # curriculum always finishes the shortest quests first, and the shortest
        # quests are all the same shape.
        self.focus = focus
        # "step" is the KB's order, which is arbitrary. "nearest" costs nothing to
        # compute and is exactly optimal on this data (7.3 vs 7.3 travel steps per
        # step), so the only reason to keep "step" is to have something to compare to.
        self.ordering = ordering
        self._by_type: Optional[Dict[int, List[int]]] = None
        # History of its own attempts. The caller reports the outcome back through
        # `note_result`, so the policy sees exactly what the corpus saw — a policy
        # that infers its own failures from an observation delta is trained on one
        # signal and served another.
        self.tracker = ObsTracker()
        # Discovered facts (npc -> map, for example) change plans without any
        # retraining. This is the point of keeping knowledge out of the weights.
        self.store = store
        # How many times the planner has asked the server to validate a ready
        # objective without it moving. `ready_objectives` repeats every step until
        # something changes, so a planner with no memory of its own attempts
        # re-sends the same message forever — which is exactly what a live session
        # did, for its whole budget, on objective 13493.
        self._claim_attempts: Dict[Tuple[int, int], int] = {}
        # Consecutive hard failures (the caller reports them through `note_result`)
        # and actions emitted since the map last changed. The fallback exists
        # because the guard refuses some validations; retiring on the first action
        # would turn every such refusal into a lost quest.
        self._claim_failures: Dict[Tuple[int, int], int] = {}
        # Claims that cost a connection. `ended the connection` is the client
        # stating that this exact packet got the session dropped, which is a fact
        # about the packet, not about the scene — so unlike the counters above it
        # is deliberately NOT cleared when the scene changes or when an objective
        # stops being ready. A live run re-sent one such claim 287 times at a 3.7 s
        # median, because the scene reset wiped the failure bound between attempts
        # and the tutorial path bypasses it (KNOWN_GAPS, criterion 3).
        self._claim_quarantine: set = set()
        self._claim_actions: Dict[Tuple[int, int], int] = {}
        self._claim_map: Dict[Tuple[int, int], Any] = {}
        # A scripted fight can be started by a map NPC even when no monster group
        # exists. Walk the NPCs on the current map when the objective is a fight and the map
        # reports no group -- each one asked at most once per (quest, objective, map), remembered
        # by identity because a count was wiped whenever the scene key moved and the rotation
        # started over on the nearest NPC.
        self._npc_asked: Dict[Tuple[int, int, int], set] = {}
        # The (key, tool) of the last claim/fallback action, so `note_result` can
        # attribute an outcome to the objective it was for instead of guessing.
        self._pending_claim: Optional[Tuple[Tuple[int, int], str]] = None
        # Start requests for quests that are not active yet. The client's ok only
        # means the message was sent; `quests_active` in the next observation is
        # the evidence that the server accepted it.
        self._start_failures: Dict[int, int] = {}
        # The snapshot a decision was made from predates the start request, so the
        # first report after a start cannot be evidence that the server ignored it.
        # The flag waits for the next observation before counting a failure.
        self._start_waits: set = set()
        self._pending_start: Optional[int] = None
        # Objective types the server counts and completes itself. Asking the
        # client to validate one answers `objective_server_counted` without
        # sending anything, so the claim attempt is pure waste — worse, on a
        # live session it was followed by a disconnect. The action the objective
        # names (talk, walk, fight) is the only thing that can move it.
        self._server_counted_types = {1, 4, 5, 6}
        # The map the character came from, so exploration does not immediately
        # walk back through the exit it just used. Without this, a target on an
        # unindexed map produces a two-map loop instead of a search.
        self._prev_map: Optional[int] = None

    @property
    def quests(self) -> Dict[int, Dict[str, Any]]:
        self._ensure_quests()
        return self._quests

    @quests.setter
    def quests(self, value: Dict[int, Dict[str, Any]]) -> None:
        self._quests = value
        self._quests_loaded = True

    def _ensure_quests(self) -> None:
        if self._quests_loaded:
            return
        self._quests_loaded = True
        path = CACHE / "quests_full.json"
        if not path.exists():
            return
        for q in fast_loads(path.read_bytes()):
            if not q.get("steps"):
                continue
            # Keep only what planning reads: id/level gate, the step list,
            # and the objectives. Names, descriptions, categories, event
            # flags and start metadata are for display/training tooling,
            # not for a live decision. Trim in place; the JSON file is
            # untouched for offline tools.
            for step in q["steps"]:
                step["objectives"] = step.get("objectives") or []
                for key in ("id", "name", "description", "optimal_level"):
                    step.pop(key, None)
            for key in ("name", "category_id", "level_max", "is_dungeon",
                        "is_event", "is_party", "start_criterion",
                        "start_position"):
                q.pop(key, None)
            q["level_min"] = q.get("level_min") or 1
            self._quests[int(q["id"])] = q

    # -- curriculum --------------------------------------------------------

    def _curriculum(self, level: int) -> List[int]:
        """Quests worth doing, cheapest first.

        Level gate first (a level-130 quest at level 42 is not a plan, it is a
        wish), then fewest objectives per level band — the short quests finish, and
        a finished quest is progress the loop can verify.
        """
        if self._order is not None and level == self.level_hint:
            return self._order
        rows = []
        for qid, q in self.quests.items():
            qlevel = int(q.get("level_min") or 1)
            if qlevel > level or qlevel > self.max_quest_level:
                continue
            objectives = sum(len(s.get("objectives") or []) for s in q["steps"])
            if objectives == 0:
                continue
            rows.append((objectives, qlevel, qid))
        rows.sort()
        self._order = [qid for _, _, qid in rows]
        self.level_hint = level
        return self._order

    # -- objective translation --------------------------------------------

    @staticmethod
    def _observed_exits(obs: Optional[Dict[str, Any]],
                        include_blocked: bool = False) -> List[Dict[str, Any]]:
        """The exits the client reports for the map it is standing on.

        An exit the tutorial marks `blocked` is not an exit this agent can take:
        the client's tap gate refuses the crossing, and walking to the border cell
        just parks the character there. Filtering here means every caller — explore,
        travel, a learned tool choice — sees only crossings that can happen.
        """
        if not isinstance(obs, dict):
            return []
        block = obs.get("map")
        rows = block.get("exits") if isinstance(block, dict) else None
        try:
            here = int(obs.get("map_id") or (block or {}).get("map_id")
                       or (obs.get("position") or {}).get("map_id") or 0)
        except (TypeError, ValueError):
            here = 0
        exits: List[Dict[str, Any]] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            direction = row.get("direction")
            try:
                dest = int(row.get("map_id"))
            except (TypeError, ValueError):
                continue
            # A self-edge is not a crossing. The live tutorial map reported
            # right -> its own map id; the client refused every attempt with
            # "needs ... a map_id different from <here>".
            if here and dest == here:
                continue
            blocked = bool(row.get("blocked"))
            if blocked and not include_blocked:
                continue
            if direction and dest > 0:
                exits.append({"direction": direction, "map_id": dest,
                              "blocked": blocked})
        return exits

    @classmethod
    def _explore_call(cls, obj: Dict[str, Any],
                      obs: Optional[Dict[str, Any]],
                      avoid: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Walk an exit of the current map when the destination is not nameable.

        A DiscoverMap objective with no `map_id` is a generic discovery: the
        client says "discover a map" and names none, so the only action that can
        satisfy it is entering a map. Prefer the edge the objective's own text
        names ("click the right edge"); without a hint, prefer a neighbour the
        routing graph does not have, since that is the crossing that also extends
        what the agent knows. None when the observation reports no usable exits.

        Picking `exits[0]` here is what walked a live tutorial character left when
        the tutorial wanted the right edge: the list is ordered left/right/top/
        bottom, which is the client's layout, not a preference. `direction_hint`
        reads the instruction in four languages and knows no quest id.
        """
        exits = cls._observed_exits(obs)
        if not exits:
            return None
        hint = direction_hint(obj)
        probe = None
        if hint:
            probe = next((e for e in exits if e["direction"] == hint), None)
        if probe is None:
            candidates = [e for e in exits if avoid is None or e["map_id"] != avoid]
            if not candidates:
                candidates = exits  # all exits lead back; walking back beats stalling
            probe = next((e for e in candidates if not known_map(e["map_id"])),
                         candidates[0])
        return {
            "tool": "nav.goto",
            "args": {"direction": probe["direction"]},
            "why": ("objective %s: discover a map by walking the %s edge (-> %s)"
                    % (obj.get("type_id"), probe["direction"], probe["map_id"])),
        }

    @staticmethod
    def _visible_target(obs: Optional[Dict[str, Any]],
                        monster: Any) -> Optional[bool]:
        """Whether the map snapshot shows a group containing `monster`.

        None means the observation carries no map block (the offline corpus
        shape), so the caller keeps its historical choice instead of inventing
        a search.
        """
        groups = None
        block = obs.get("map") if isinstance(obs, dict) else None
        if isinstance(block, dict) and isinstance(block.get("groups"), list):
            groups = [row for row in block["groups"] if isinstance(row, dict)]
        if groups is None and isinstance(obs, dict) and isinstance(obs.get("groups"), list):
            # The guard reads `map.groups` with a flat `groups` fallback; reading
            # one source here and the other there would let a join through on a
            # map the estimator calls empty.
            groups = [row for row in obs["groups"] if isinstance(row, dict)]
        if groups is None:
            return None
        if monster is None:
            return bool(groups)
        try:
            target = int(monster)
        except (TypeError, ValueError):
            return True   # a name the guard resolves; not ours to second-guess
        for group in groups:
            for raw in group.get("monster_ids") or []:
                try:
                    if int(raw) == target:
                        return True
                except (TypeError, ValueError):
                    continue
            for member in group.get("members") or []:
                if not isinstance(member, dict):
                    continue
                try:
                    if int(member.get("monster_id")) == target:
                        return True
                except (TypeError, ValueError):
                    continue
        return False

    @staticmethod
    def _map_npcs(obs: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """The NPCs the current map reports, by template id."""
        if not isinstance(obs, dict):
            return []
        block = obs.get("map") if isinstance(obs.get("map"), dict) else {}
        raw = obs.get("npcs") or block.get("npcs") or []
        out: List[Dict[str, Any]] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            npc_id = entry.get("npc_id", entry.get("templateId"))
            try:
                npc_id = int(npc_id)
            except (TypeError, ValueError):
                continue
            if npc_id <= 0:
                continue
            out.append({"npc_id": npc_id, "cell": entry.get("cell"),
                        "actor_id": entry.get("actor_id")})
        return out

    @staticmethod
    def _cell_distance(a: Any, b: Any) -> int:
        """Dofus cell distance; raw cell ids are not row-major."""
        try:
            first, second = int(a), int(b)
        except (TypeError, ValueError):
            return 1 << 30

        def point(cell: int) -> Tuple[int, int]:
            t = cell % 14 - cell // 28
            return t + 19, t + cell // 14

        ax, ay = point(first)
        bx, by = point(second)
        return abs(ax - bx) + abs(ay - by)

    def _npc_talk_calls(self, quest: Dict[str, Any], obj: Dict[str, Any],
                        obs: Dict[str, Any], type_id: Any
                        ) -> List[Dict[str, Any]]:
        """Ask a map NPC to open the scripted fight when no group is visible.

        A `QuestObjectiveFightMonster` is not always a roaming group: the live
        tutorial has the objective, an empty group list, and the NPC that starts
        the encounter standing on the map. Keep the choice generic and walk the map's NPCs so one
        wrong dialogue cannot pin the loop -- once each, then stop, because a dialogue that already
        answered will answer the same way and the loop has no new question to ask.
        """
        npcs = self._map_npcs(obs)
        if not npcs:
            return []
        here = obs.get("cell")
        if here is None:
            position = obs.get("position") if isinstance(obs.get("position"), dict) else {}
            block = obs.get("map") if isinstance(obs.get("map"), dict) else {}
            here = position.get("cell") or block.get("cell")
        npcs.sort(key=lambda entry: self._cell_distance(here, entry.get("cell")))
        quest_id = quest.get("id")
        objective_id = obj.get("id", obj.get("objective_id"))
        try:
            key = (int(quest_id), int(objective_id), int(obs.get("map_id") or 0))
        except (TypeError, ValueError):
            return []
        asked = self._npc_asked.setdefault(key, set())
        candidates = [entry for entry in npcs if entry["npc_id"] not in asked]
        if not candidates:
            # Every NPC here has already been asked and none of them opened the fight. Remembered
            # by identity rather than by a counter: the counter was wiped on every scene flap, so
            # the rotation restarted from the nearest NPC and the live log asked the same four of
            # them 259 times for one kill objective (KNOWN_GAPS G17 family). A map that shows a
            # new stranger is genuinely a new question, and this reopens for exactly that.
            return []
        npc = candidates[0]
        asked.add(int(npc["npc_id"]))
        return [{
            "tool": "quest.advance",
            "args": {"quest_id": int(quest_id), "npc_id": int(npc["npc_id"])},
            "why": ("objective %s: no group on this map; ask NPC %d to start "
                    "the fight" % (type_id, int(npc["npc_id"]))),
        }]

    @classmethod
    def _travel_call(cls, obj: Dict[str, Any], need: int,
                     obs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """How to reach the objective's map, or the edge its text names.

        A destination the routing graph does not know is not a reason to send a
        ChangeMapMessage into the void. The objective's own text often carries
        the actionable detail instead ("click the right edge"), so read that --
        no quest id is involved, and the same rule covers every objective that
        phrases a border crossing this way.

        When there is neither a route nor a named edge, the client still cannot
        change map by being handed an id: it only crosses an exit of the map it
        is on. A live session re-sent `nav.goto {map_id: 698953}` nine times and
        the client answered "map 698953 is not adjacent". Walking an exit is the
        only move that can discover where an unindexed destination actually is.
        """
        try:
            target = int(need)
        except (TypeError, ValueError):
            target = need
        all_exits = cls._observed_exits(obs, include_blocked=True)
        adjacent = any(e["map_id"] == target and not e["blocked"] for e in all_exits)
        # An adjacent exit the tutorial forbids is, for this step, the same as no
        # exit at all. Treating it as adjacent is what produced `nav.goto` calls
        # the client walked and then could not complete.
        blocked_target = any(e["map_id"] == target and e["blocked"] for e in all_exits)
        if blocked_target:
            hint = direction_hint(obj)
            allowed_dirs = {e["direction"] for e in cls._observed_exits(obs)}
            if hint and hint in allowed_dirs:
                return {
                    "tool": "nav.goto",
                    "args": {"direction": hint},
                    "why": ("objective %s: cross the %s edge the objective names; "
                            "the tutorial forbids the direct route to %s"
                            % (obj.get("type_id"), hint, target)),
                }
            # No allowed crossing towards the objective and no instruction naming
            # another edge. Walking a random allowed exit is the "it went the
            # contrary way" behaviour; the scripted gate lifts when its own step is
            # done, so waiting is the move that does not undo progress.
            return {
                "tool": "state.snapshot",
                "args": {},
                "why": ("objective %s: the tutorial forbids the crossing to map %s; "
                        "waiting for the step" % (obj.get("type_id"), target)),
            }
        if not known_map(target) and not adjacent:
            hint = direction_hint(obj)
            allowed_dirs = {e["direction"] for e in cls._observed_exits(obs)}
            if hint and hint in allowed_dirs:
                return {
                    "tool": "nav.goto",
                    "args": {"direction": hint},
                    "why": ("objective %s: cross the %s edge (target map %s is not "
                            "reachable from here)" % (obj.get("type_id"), hint, target)),
                }
            explore = cls._explore_call(obj, obs)
            if explore is not None:
                return explore
        return {
            "tool": "nav.goto",
            "args": {"map_id": target},
            "why": "objective %s: go to map %s" % (obj.get("type_id"), target),
        }

    def _dialog_call(self, obs: Dict[str, Any], goal: str) -> Optional[Any]:
        from agent.runtime import ToolCall

        decision = rank_dialog(obs)
        self.last_dialog_decision = decision
        dialog = obs.get("dialog") if isinstance(obs.get("dialog"), dict) else {}
        # An offer carries the client's own id (G32). Accepting the link is the action,
        # not answering a reply (G33); the page refuses any id it is not offering, so a
        # stale current_quest cannot put an invented id on the wire. A quest the run is
        # already doing (or the ranker's quest) always wins; otherwise a quest/auto run
        # takes a new offer from an NPC, which is how the next quest in a chain starts.
        # An order/farm prompt must not acquire quests (fidelity), a named focus must
        # match, and an offer for an already-active quest is stale -- the ranker answers.
        offered = dialog_offer(dialog)
        if offered is not None:
            wanted = None
            if isinstance(decision, dict):
                try:
                    wanted = int(decision.get("quest_id"))
                except (TypeError, ValueError):
                    wanted = None
            try:
                current = int(self.current_quest) if self.current_quest is not None else None
            except (TypeError, ValueError):
                current = None
            try:
                focus = int(self.focus) if self.focus is not None else None
            except (TypeError, ValueError):
                focus = None
            active_ids = set()
            raw_active = obs.get("quests_active")
            if isinstance(raw_active, dict):
                for key in raw_active:
                    try:
                        active_ids.add(int(key))
                    except (TypeError, ValueError):
                        continue
            # A quest id in the prompt is a named target; an unrelated offer is not
            # taken (fidelity). Live quest ids are four digits in this game.
            named = {int(m) for m in re.findall(r"\b(\d{4,6})\b", goal or "")}
            takes_new = (_goal_mode(goal) in ("quest", "auto")
                         and (not named or offered in named)
                         and (focus is None or focus == offered))
            if offered in active_ids:
                takes_new = False
            if offered == current or offered == wanted or takes_new:
                self.current_quest = offered
                self.current_objective = None
                self._pending_start = offered
                # The offer is client-reported, so G31's rule is satisfied; remembering it
                # lets a start the server did not honour be retried outside the dialog.
                self._client_quests.add(offered)
                return ToolCall("quest.advance",
                                {"quest_id": offered, "accept": True}, goal)
        if decision is None:
            self._dialog_waits = 0
            self._dialog_key = None
            return None
        key = (dialog.get("npc_id"), dialog.get("message"))
        if key != self._dialog_key:
            self._dialog_waits = 0
            self._dialog_key = key
        quest_id = decision.get("quest_id")
        self.current_quest = quest_id
        self.current_objective = decision.get("objective_id")
        reply = decision.get("reply")
        status = decision.get("status")
        if status in ("wait", "abstain"):
            self._dialog_waits += 1
            reply_count = int(dialog.get("reply_count") or 0)
            # A present-but-empty list is definitive; a missing list may still be
            # loading its ids/text, so give it a longer grace before closing.
            definitive_empty = (reply_count == 0
                                and (dialog.get("ids_present") is True
                                     or dialog.get("text_present") is True))
            if (definitive_empty and self._dialog_waits >= 2) or self._dialog_waits >= 6:
                self._dialog_waits = 0
                self._dialog_key = None
                return ToolCall("quest.advance", {
                    "quest_id": int(quest_id or 0), "close": True,
                }, goal)
            return ToolCall("state.snapshot", {}, goal)
        self._dialog_waits = 0
        if status == "choose" and quest_id is not None and reply:
            ask = (dialog.get("npc_id"), dialog.get("message"), reply["index"],
                   decision.get("objective_id"))
            if ask in self._dialog_exhausted:
                # The page came back unchanged twice for this exact answer. Asking a
                # third time is not a plan, so decline the dialog action entirely and
                # let the rest of the planner (and the instrumented wait) decide what
                # the objective actually needs.
                self.last_dialog_decision = {**decision, "status": "exhausted",
                                             "reason": "reply already left this page unchanged"}
                return None
            n = self._dialog_asks.get(ask, 0) + 1
            self._dialog_asks[ask] = n
            if len(self._dialog_asks) > 256:
                for stale in list(self._dialog_asks)[:32]:
                    self._dialog_asks.pop(stale, None)
            if n > DIALOG_ASK_LIMIT:
                self._dialog_asks.pop(ask, None)
                self._dialog_exhausted.add(ask)
                # End the conversation once, so the map is not held open by a dialog
                # that has nothing left to offer. The memo above stops the re-open loop.
                return ToolCall("quest.advance", {
                    "quest_id": int(quest_id or 0), "close": True,
                }, goal)
            return ToolCall("quest.advance", {
                "quest_id": quest_id,
                "reply": reply["index"],
                "reply_text": reply["text"],
            }, goal)
        return ToolCall("state.snapshot", {}, goal)

    # -- tutorial ----------------------------------------------------------
    #
    # The Dofus tutorial is a scripted scenario, not a set of suggestions. While
    # a step runs, the client disables some (or all) map crossings, ignores taps
    # outside a set of cells, and accepts interaction with at most one NPC. The
    # scenario is the authority for what can happen next, so the planner reads it
    # instead of deriving a plan from the quest graph and having the client refuse
    # it. Nothing here names a quest id or a cell: the step's own gate is the rule,
    # which is what keeps this generic for every scripted sequence.

    @staticmethod
    def _tutorial(obs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        block = obs.get("tutorial")
        if isinstance(block, dict) and block.get("active"):
            return block
        return None

    @staticmethod
    def _tutorial_quest(obs: Dict[str, Any],
                        tutorial: Dict[str, Any]) -> Optional[int]:
        """The quest the scenario is driving, without guessing from the list order.

        The scenario knows its own quest id; the tracker is the fallback. Taking
        `quests_active[0]` is what let a stall retire the only live quest while the
        start loop blamed a different one.
        """
        tracker = obs.get("current_objective") or {}
        for raw in (tutorial.get("quest_id"), tracker.get("quest_id")):
            try:
                qid = int(raw)
            except (TypeError, ValueError):
                continue
            if qid > 0:
                return qid
        active = obs.get("quests_active") or {}
        if len(active) == 1:
            try:
                return int(next(iter(active)))
            except (TypeError, ValueError):
                return None
        return None

    def _tutorial_call(self, obs: Dict[str, Any], goal: str) -> Optional[Any]:
        """The tutorial's next action, when it dictates one.

        Three shapes, all read from the step itself:
          * a dialog is open -> answer it (the reply advances the step)
          * one NPC is tappable -> walk to it and talk
          * a set of cells is tappable -> walk to one of them

        `unblocked_cells == []` is a real gate ("stay where you are"), not "no
        restriction", and `[0, ...]` ids other than 0 mean nothing is tappable. When
        the step demands a UI action the tool set does not have, this returns None
        and the caller waits instead of starting an unrelated quest.
        """
        from agent.runtime import ToolCall

        tutorial = self._tutorial(obs)
        if tutorial is None:
            return None
        qid = self._tutorial_quest(obs, tutorial)
        if obs.get("in_dialog"):
            if qid is None:
                return None
            self.current_quest = qid
            self.current_objective = None
            dialog = obs.get("dialog") if isinstance(obs.get("dialog"), dict) else {}
            if dialog_offer(dialog) == qid:
                # The offer is the client's own id (G32): accept the link instead
                # of answering a reply, which is what this call used to do (G33).
                self._pending_start = qid
                return ToolCall("quest.advance", {"quest_id": qid, "accept": True}, goal)
            # An explicit reply keeps the "click next" behaviour; a bare quest id
            # is now reserved for a start request, which the page refuses here.
            return ToolCall("quest.advance", {"quest_id": qid, "reply": 0}, goal)

        npc = tutorial.get("unblocked_npc_id")
        if npc is not None:
            try:
                npc = int(npc)
            except (TypeError, ValueError):
                npc = None
        if npc is not None and npc > 0:
            args: Dict[str, Any] = {"npc_id": npc}
            if qid is not None:
                args["quest_id"] = qid
            self.current_quest = qid
            self.current_objective = None
            return ToolCall("quest.advance", args, goal)

        cells = tutorial.get("unblocked_cells")
        if isinstance(cells, list):
            allowed: List[int] = []
            for raw in cells:
                try:
                    cell = int(raw)
                except (TypeError, ValueError):
                    continue
                if cell >= 0:
                    allowed.append(cell)
            here = obs.get("cell")
            try:
                here = int(here) if here is not None else None
            except (TypeError, ValueError):
                here = None
            pick = next((cell for cell in allowed if cell != here), None)
            if pick is not None:
                self.current_quest = qid
                self.current_objective = None
                return ToolCall("nav.goto", {"cell": pick}, goal)
        return None

    # -- retirement that can be undone -------------------------------------

    @staticmethod
    def _tracker_step(obs: Dict[str, Any], quest_id: int) -> Any:
        active = obs.get("quests_active") or {}
        for key in (str(quest_id), quest_id):
            try:
                if key in active:
                    return active[key]
            except TypeError:
                continue
        return None

    @classmethod
    def _situation_sig(cls, obs: Dict[str, Any], quest_id: int) -> Tuple[Any, ...]:
        """What the world looked like when a quest was given up on.

        A quest retired because the tutorial blocked a crossing is not a quest that
        cannot be done. Remembering the situation lets the next plan reopen it when
        the step changes, the map changes, or the tutorial ends — which is exactly
        what "I advanced the quest myself but the AI still ignored it" was.
        """
        tracker = obs.get("current_objective") or {}
        try:
            if int(tracker.get("quest_id") or 0) != int(quest_id):
                tracker = {}
        except (TypeError, ValueError):
            tracker = {}
        tutorial = cls._tutorial(obs) or {}
        gate = tutorial.get("can_change_map") or {}
        return (
            cls._tracker_step(obs, quest_id),
            tracker.get("step"),
            tracker.get("objective_id"),
            obs.get("map_id"),
            bool(tutorial),
            tuple(sorted((str(k), bool(v)) for k, v in gate.items())),
        )

    def _retire(self, quest_id: Any, obs: Optional[Dict[str, Any]] = None) -> None:
        """Give up on a quest for now, and remember the situation."""
        try:
            qid = int(quest_id)
        except (TypeError, ValueError):
            return
        self._skipped.add(qid)
        self._skip_sigs[qid] = self._situation_sig(obs or {}, qid)

    def _reopen(self, obs: Dict[str, Any]) -> None:
        """Undo retirements whose situation has changed.

        Permanent retirements — a quest with no tool that can express its
        objectives — are not in `_skip_sigs` and stay retired.
        """
        tutorial = self._tutorial(obs)
        if tutorial is not None:
            # A scripted step invalidates earlier stall verdicts about the quests
            # it drives.
            for key in (obs.get("quests_active") or {}):
                try:
                    qid = int(key)
                except (TypeError, ValueError):
                    continue
                self._skipped.discard(qid)
                self._skip_sigs.pop(qid, None)
        for qid, sig in list(self._skip_sigs.items()):
            if qid not in self._skipped:
                self._skip_sigs.pop(qid, None)
                continue
            if self._situation_sig(obs, qid) != sig:
                self._skipped.discard(qid)
                self._skip_sigs.pop(qid, None)

    def plan_objective(self, quest: Dict[str, Any], step_idx: int,
                       obj: Dict[str, Any], obs: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The calls this objective needs, in order. Empty when unsupported."""
        t = obj.get("type_id")
        p = _params(obj)
        here = obs.get("map_id")

        # Where this objective has to happen. The knowledge base names a map for
        # 10,421 of its objectives and the world refuses to complete one from the
        # wrong place, so travelling is a precondition, not a suggestion.
        # Only `map_id` is a place; see `required_map`.
        need = obj.get("map_id")
        travel: List[Dict[str, Any]] = []
        if need is not None:
            try:
                need = int(need)
            except (TypeError, ValueError):
                need = None
        if not need:          # 0 is the KB's "no map", not a destination
            need = None
        if need is not None and here != need:
            travel = [self._travel_call(obj, need, obs)]

        if t in MAP_TYPES:
            if need is not None:
                return travel
            # A destination the DB does not name: discover one by walking out.
            # This is the tutorial's "Découvrir une carte" shape, and it is also
            # the only honest action when `parameter0` is a text id.
            explore = self._explore_call(obj, obs)
            return [explore] if explore is not None else []

        if t in TALK_TYPES:
            npc = p.get("parameter0")
            if npc is None:
                return []
            calls = list(travel)
            # If the agent has discovered where this NPC is, travel first. The KB
            # has no npc -> map table; the store fills that gap at runtime.
            target = None
            if self.store is not None:
                try:
                    target = self.store.get("npc_map", "npc:%d" % int(npc))
                except Exception:  # noqa: BLE001
                    target = None
            if target and not travel and here != target:
                calls.append({"tool": "nav.goto", "args": {"map_id": int(target)},
                              "why": "travelled to npc %d (learned fact)" % int(npc)})
            calls.append({"tool": "quest.advance",
                          "args": {"quest_id": int(quest["id"]), "npc_id": int(npc)},
                          "why": "objective %d: talk" % t})
            return calls

        if t in KILL_TYPES:
            if declares_monster_target(obj) is False:
                # `type_id` 0 was decoded as "monster" from the objective text, and the
                # client's parameter table says otherwise: this class declares only a
                # text parameter, so there is no group to hunt and no fight order to
                # send. Travelling is still honest when the objective names a place;
                # otherwise this objective asks for nothing, and the ready scan moves to
                # the entry behind it that can be acted on.
                return list(travel)
            monster = p.get("parameter0")
            calls = list(travel)
            visible = self._visible_target(obs, monster)
            if visible is False:
                npc_calls = self._npc_talk_calls(quest, obj, obs, t)
                if npc_calls:
                    return calls + npc_calls
                if obj.get("map_id"):
                    # The objective names the map it must happen on. Groups
                    # respawn there; walking away would only require walking
                    # back, so wait for the spawn instead.
                    return calls + [{
                        "tool": "state.snapshot", "args": {},
                        "why": ("objective %d: waiting for a group on map %s"
                                % (t, obj.get("map_id")))}]
                # `fight.join` can only attack a group the map reports. The
                # objective names the monster, so go and find it; the farm loop
                # is a standing order, not a quest action.
                explore = self._explore_call(obj, obs, avoid=self._prev_map)
                if explore is not None:
                    return calls + [explore]
                return calls + [{
                    "tool": "state.snapshot", "args": {},
                    "why": ("objective %d: target %s is not on this map and "
                            "there is no exit to search" % (t, monster))}]
            calls.append({"tool": "fight.join",
                          "args": {"monster": monster} if monster else {},
                          "why": "objective %d: fight" % t})
            return calls

        if t in DELIVER_TYPES:
            item = p.get("parameter1") if t == 3 else p.get("parameter0")
            npc = p.get("parameter0") if t == 3 else p.get("parameter1")
            calls = list(travel)
            # Inventories arrive with either int or str keys depending on who built
            # the observation; a str-only lookup made the planner gather the same
            # item forever because it never believed it had one.
            inv = obs.get("inventory") or {}
            have = bool(item is not None and (inv.get(item) or inv.get(str(item))))
            cap = int(obs.get("inventory_capacity") or 0)
            if item is not None and not have and cap and len(inv) >= cap:
                # Nowhere to put the thing we need. Selling is a precondition here,
                # not an optimisation: without it the loop gathers into a full bag
                # for the rest of its budget.
                calls.append({"tool": "inv.sell", "args": {},
                              "why": "objective %d: bag full, sell first" % t})
            elif item is not None and not have:
                # Getting the item is a farming problem; the honest first move is to
                # go and farm it rather than pretend the delivery can happen.
                # `resource`, matching what the client reads and what the contract
                # declares. This was `item`, which the validator rejected outright
                # ("unknown argument(s) for farm.start: item"), so a deliver objective
                # whose item was missing could never even be planned.
                calls.append({"tool": "farm.start", "args": {"resource": item},
                              "why": "objective %d: need item %s first" % (t, item)})
            if npc is not None:
                calls.append({"tool": "quest.advance",
                              "args": {"quest_id": int(quest["id"]), "npc_id": int(npc)},
                              "why": "objective %d: deliver" % t})
            return calls

        if t in CRAFT_TYPES:
            # Crafting needs a recipe and materials model the simulator does not
            # have. Returning inv.list here looked harmless and cost 213 steps: a
            # read is not progress, and repeating one is a loop with a good excuse.
            return []

        # Area objectives need the area -> map graph, which the KB does not expose
        # here. Refreshing the state is honest: it costs a step and keeps the loop
        # from inventing a destination.
        return []

    def _focus_order(self, level: int) -> List[int]:
        """Quests that contain the focused objective type, cheapest first."""
        if self.focus is None:
            return []
        if self._by_type is None:
            index: Dict[int, List[int]] = {}
            for qid, q in self.quests.items():
                for step in q.get("steps") or []:
                    for obj in step.get("objectives") or []:
                        t = obj.get("type_id")
                        if t is None:
                            continue
                        try:
                            index.setdefault(int(t), []).append(qid)
                        except (TypeError, ValueError):
                            continue
            self._by_type = {k: sorted(set(v)) for k, v in index.items()}
        return self._by_type.get(int(self.focus), [])

    # -- policy interface --------------------------------------------------

    def _objective_in(self, quest: Dict[str, Any], step_idx: int,
                      obs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Which objective is actually open.

        The quest tracker in the observation is the only trustworthy answer: the
        planner used to take "the first objective in the step that produces calls",
        which is a different objective as soon as the first one is already done.
        That mismatch made a talk call complete a kill objective — every tool worked,
        so the tool choice was not a decision at all.

        With ``ordering="nearest"`` it takes the closest open objective instead of the
        first. The step order is arbitrary; the distance is not, and it is worth 22% of
        the travel. Only usable when the tracker belongs to this quest — otherwise the
        step list describes someone else's step and "nearest" would be a guess.
        """
        step = quest["steps"][step_idx]
        objectives = step.get("objectives") or []
        tracker = obs.get("current_objective") or {}

        if self.ordering == "nearest" and int(tracker.get("quest_id") or 0) == int(quest["id"]) \
                and int(tracker.get("step", -1)) == int(step_idx):
            done = {o.get("id") for o in (obs.get("step_objectives") or []) if o.get("done")}
            here = obs.get("map_id")
            best, best_cost = None, None
            for obj in objectives:
                if obj.get("id") in done:
                    continue
                if not self.plan_objective(quest, step_idx, obj, obs):
                    continue
                need = required_map(obj)
                cost = travel_hops(here, need) if need else 0
                if best_cost is None or cost < best_cost:
                    best, best_cost = obj, cost
            if best is not None:
                return best

        if tracker and int(tracker.get("quest_id") or 0) == int(quest["id"]):
            for obj in objectives:
                if obj.get("id") == tracker.get("objective_id"):
                    return obj
            # The tracker names an objective that is not in the step we were given. The step
            # index the page sends is the position of the live `stepId` in the client's own
            # `dbQuest.stepIds`, which need not match the KB's numbering — so search the whole
            # quest before concluding the objective does not exist. `args_for_tool` reads the
            # objective's own params, so a corrected step index is not needed for the
            # call itself.
            for step in quest.get("steps") or []:
                for obj in step.get("objectives") or []:
                    if obj.get("id") == tracker.get("objective_id"):
                        return obj
        for obj in objectives:
            if self.plan_objective(quest, step_idx, obj, obs):
                return obj
        return None

    # -- objectives the knowledge base does not contain --------------------

    @staticmethod
    def _objective_from_observation(entry: Any) -> Optional[Dict[str, Any]]:
        """One observation objective in the shape `plan_objective` reads."""
        if not isinstance(entry, dict):
            return None
        oid = entry.get("id", entry.get("objective_id"))
        if oid is None:
            return None
        obj: Dict[str, Any] = {"id": oid}
        if entry.get("type_id") is not None:
            obj["type_id"] = entry["type_id"]
        # The client's class name has to travel with the entry, or the parameter table
        # is never consulted on the path live quests actually take: the KB holds no live
        # quest id, so this fallback is *every* live quest, and without the name
        # `declares_monster_target` can only answer "unknown" and the invented monster
        # route stays in force (G27).
        if isinstance(entry.get("type"), str) and entry.get("type"):
            obj["type"] = entry["type"]
        if entry.get("map_id"):
            obj["map_id"] = entry["map_id"]
        params = entry.get("params")
        if isinstance(params, dict) and params:
            obj["params"] = params
        if entry.get("text"):
            obj["text"] = entry["text"]
        return obj

    def _lookup_objective(self, quest_id: int, objective_id: Any,
                          fallback: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """The objective's own row: the KB's when it has one, the client's otherwise.

        The client serves quests newer than `quests_full.json` (the tutorial among
        them) and reports their type and parameters in the snapshot. Refusing to plan
        because the cached tables are old is how a whole session was spent
        re-validating objective 13493.
        """
        quest = self.quests.get(int(quest_id))
        if quest:
            for step in quest.get("steps") or []:
                for obj in step.get("objectives") or []:
                    if str(obj.get("id")) == str(objective_id):
                        if not isinstance(fallback, dict):
                            return dict(obj)
                        merged = dict(obj)
                        params = dict(_params(merged))
                        for key, value in _params(fallback).items():
                            params.setdefault(key, value)
                        if params:
                            merged["params"] = params
                        for key in ("type_id", "map_id", "type"):
                            if merged.get(key) is None and fallback.get(key) is not None:
                                merged[key] = fallback[key]
                        if not merged.get("text") and fallback.get("text"):
                            merged["text"] = fallback["text"]
                        return merged
        return self._objective_from_observation(fallback or {})

    def _observed_current(self, quest_id: int,
                          obs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        tracker = obs.get("current_objective") or {}
        try:
            if int(tracker.get("quest_id") or 0) != int(quest_id):
                return None
        except (TypeError, ValueError):
            return None
        oid = tracker.get("objective_id")
        for entry in obs.get("step_objectives") or []:
            if isinstance(entry, dict) and str(entry.get("id")) == str(oid):
                return self._objective_from_observation(entry)
        for entry in obs.get("ready_objectives") or []:
            if isinstance(entry, dict) and str(entry.get("objective_id")) == str(oid):
                return self._objective_from_observation(entry)
        # The page's own active-quest graph (G29). `step_objectives` only covers the
        # quest whose current objective the tracker names, so an active quest with a
        # thin per-quest table had no row to plan from even though the client held
        # one. The catalogue is that row, keyed by the quest the character is on.
        for entry in catalog_objectives(obs, quest_id):
            if str(entry.get("id")) == str(oid):
                return self._objective_from_observation(entry)
        return None

    def _plan_observed(self, quest_id: int, obj: Dict[str, Any],
                       obs: Dict[str, Any], goal: str) -> Optional[Any]:
        from agent.runtime import ToolCall

        calls = self.plan_objective({"id": quest_id}, 0, obj, obs)
        if not calls:
            return None
        self.current_quest = quest_id
        self.current_objective = obj.get("id")
        return ToolCall(calls[0]["tool"], dict(calls[0]["args"]), goal)

    def _plan_quest(self, quest_id: int, step_idx: int,
                    obs: Dict[str, Any], goal: str = "do all the quests") -> Optional[Any]:
        from agent.runtime import ToolCall

        quest = self.quests.get(quest_id)
        if quest is None or step_idx >= len(quest["steps"]):
            return None
        obj = self._objective_in(quest, step_idx, obs)
        if obj is None:
            return None
        calls = self.plan_objective(quest, step_idx, obj, obs)
        if not calls:
            return None
        first = calls[0]
        self.current_quest = quest_id
        self.current_objective = obj.get("id")
        return ToolCall(first["tool"], dict(first["args"]), goal)

    def note_result(self, tool: str, ok: bool, note: str, obs: Dict[str, Any],
                    detail: Any = None) -> None:
        """The caller reports what happened. Without this the policy cannot learn
        from its own failures, and any history feature would be a guess.

        `detail` is the refusal's own payload when the caller has one. It says more than
        `note` does, and for a group refusal the difference is which repair is right.
        """
        self.tracker.record(tool, ok, note, obs)
        if tool in ("farm.start", "fight.join"):
            if ok:
                self._farm_target_missing = False
                self._group_read_broken = False
            else:
                outcome = group_refusal_outcome(detail, note)
                self._group_refusals[outcome] = self._group_refusals.get(outcome, 0) + 1
                # Groups are on this map but none of their members could be read: that is a
                # broken read, not an absent mob, so travelling would spend the rest of the run
                # and never find the target (KNOWN_GAPS G7).
                self._group_read_broken = outcome == "unread_members"
                if outcome in ("elsewhere", "empty_map"):
                    self._farm_target_missing = True
        pending, self._pending_claim = self._pending_claim, None
        started, self._pending_start = self._pending_start, None

        if pending is not None and pending[1] == tool:
            key = pending[0]
            if key in self._claim_attempts:      # the objective may have moved on
                if ok:
                    self._claim_failures[key] = 0
                else:
                    self._claim_failures[key] = self._claim_failures.get(key, 0) + 1
            if not ok and ("objective_server_counted" in note
                           or "completed by the server" in note
                           or "ended the connection" in note
                           or "perform the objective instead" in note):
                if "ended the connection" in note:
                    # The strongest signal available: this packet ended the
                    # session. Quarantine the exact key, because the type-id
                    # learning below needs the objective to resolve in the
                    # knowledge base and an unknown tutorial objective does not.
                    self._claim_quarantine.add(key)
                # The client told us this type is completed by playing, not by
                # claiming. A claim that ended the connection is the strongest
                # version of the same fact; retrying it in a tutorial produced 15
                # reconnect errors in one live baseline. Remember the type for
                # every quest, not just this one.
                obj = self._lookup_objective(key[0], key[1], {}) or {}
                try:
                    self._server_counted_types.add(int(obj.get("type_id")))
                except (TypeError, ValueError):
                    pass

        if started is None or tool != "quest.advance":
            return
        # A start request that the observation does not show as active is not
        # progress, regardless of what the client reported. The one exception is a
        # quest that is already finished — the server removes it from the active
        # list, so it must not be counted against the start bound either.
        active = set()
        for q in (obs.get("quests_active") or {}):
            try:
                active.add(int(q))
            except (TypeError, ValueError):
                continue
        done = set()
        for q in (obs.get("quests_done") or []):
            try:
                done.add(int(q))
            except (TypeError, ValueError):
                continue
        if started in active or started in done:
            self._start_failures.pop(started, None)
            self._start_waits.discard(started)
            return
        if started not in self._start_waits:
            self._start_waits.add(started)
            return
        self._start_failures[started] = self._start_failures.get(started, 0) + 1
        if self._start_failures[started] >= START_ATTEMPT_LIMIT:
            self._skipped.add(started)
            self._start_failures.pop(started, None)
            self._start_waits.discard(started)

    def fork(self) -> "QuestPlanner":
        """A planner for a counterfactual fork.

        The policy carries state — which quests it has retired, which it has seen
        finished. Letting every fork share one planner contaminated the corpus: a
        twin's dead end was recorded as the real episode's, so after a few dozen rows
        the teacher refused every quest and every candidate scored zero. The policy
        has to fork with the world or the labels are fiction.
        """
        twin = object.__new__(QuestPlanner)
        twin.quests = self.quests
        twin.level_hint = self.level_hint
        twin.max_quest_level = self.max_quest_level
        twin.current_quest = self.current_quest
        twin.current_objective = None    # a fork starts before its first action
        twin.last_dialog_decision = None
        twin._order = self._order
        twin._seen_done = set(self._seen_done)
        twin._client_quests = set(self._client_quests)
        twin._skipped = set(self._skipped)
        twin._skip_sigs = dict(self._skip_sigs)
        twin._last_obs = self._last_obs
        twin._claim_attempts = dict(self._claim_attempts)
        twin._claim_failures = dict(self._claim_failures)
        twin._claim_actions = dict(self._claim_actions)
        twin._claim_map = dict(self._claim_map)
        twin._pending_claim = None       # a fork starts before its first action
        twin._start_failures = dict(self._start_failures)
        twin._start_waits = set(self._start_waits)
        twin._pending_start = None
        twin._server_counted_types = set(self._server_counted_types)
        twin._claim_quarantine = set(self._claim_quarantine)
        twin._prev_map = self._prev_map
        twin._farm_target_missing = self._farm_target_missing
        twin._group_refusals = dict(self._group_refusals)
        twin._group_read_broken = self._group_read_broken
        twin._dialog_waits = 0
        twin._dialog_key = None
        # Which replies are spent is knowledge about the dialog, not about the
        # parent's scene: a fork that forgot it would re-ask a dead page.
        twin._dialog_asks = dict(self._dialog_asks)
        twin._dialog_exhausted = set(self._dialog_exhausted)
        twin._npc_asked = {key: set(value)
                           for key, value in self._npc_asked.items()}
        # The twin is about to observe its own world for the first time. Inheriting
        # the parent's scene key would make its first observation look unchanged,
        # so the transient claim/retry state (which belongs to the parent's scene)
        # would survive into the fork.
        twin._scene_key = None
        twin._quest_key = None
        twin._plannable_cache = self._plannable_cache   # pure function of the KB
        twin.store = self.store
        twin.curriculum = self.curriculum
        twin.rng = random.Random(self.rng.random())
        twin.focus = self.focus
        twin.ordering = self.ordering
        twin._by_type = self._by_type
        twin.tracker = ObsTracker()
        return twin

    def _plannable(self, quest_id: int) -> bool:
        """Can this quest be finished at all with the tools and KB we have?

        Checked *before* accepting. Accepting first and discovering later is how the
        loop deadlocks: an accepted quest with one unsupported objective becomes the
        world's current objective forever, every call fails against it, and the run
        burns its whole budget. The pre-check costs one pass over the objective list.
        """
        cached = self._plannable_cache.get(quest_id)
        if cached is not None:
            return cached
        quest = self.quests.get(quest_id)
        ok = bool(quest)
        if ok:
            probe = {"map_id": None, "inventory": {}, "inventory_capacity": 0}
            for step_idx, step in enumerate(quest["steps"]):
                for obj in step.get("objectives") or []:
                    # The teacher's translation, deliberately: "plannable" is a
                    # property of the KB and the tools, not of whatever weights are
                    # loaded today. Asking the student would let a bad epoch decide
                    # that the whole quest graph is unplannable.
                    if not QuestPlanner.plan_objective(self, quest, step_idx, obj, probe):
                        ok = False
                        break
                if not ok:
                    break
        self._plannable_cache[quest_id] = ok
        return ok

    def _learn_world(self, obs: Dict[str, Any]) -> None:
        """Record where NPCs stand, from the map currently being looked at.

        The knowledge base has no npc -> map table, so a talk objective that
        names an NPC but no destination could not be planned at all: the plan
        was "talk to npc 1280 from wherever you happen to be", which the client
        correctly rejected. The observation carries the missing fact, and the
        same fact serves every quest that names that NPC. This is discovery of
        the world, not a rule about a quest.
        """
        if self.store is None:
            return
        details = obs.get("map")
        if not isinstance(details, dict):
            return
        try:
            here = int(obs.get("map_id") or details.get("map_id"))
        except (TypeError, ValueError):
            return
        try:
            known = {fact.key: fact for fact in self.store.find("npc_map", "npc:")}
        except Exception:  # noqa: BLE001 - a fact store must not kill planning
            known = {}
        for npc in details.get("npcs") or []:
            if not isinstance(npc, dict):
                continue
            npc_id = npc.get("npc_id", npc.get("templateId"))
            try:
                npc_id = int(npc_id)
            except (TypeError, ValueError):
                continue
            key = "npc:%d" % npc_id
            fact = known.get(key)
            if fact is not None and fact.value == here:
                continue
            # The loop observes the same map every step. Writing a confirmed
            # fact again would append one line per NPC per step, and starting a
            # dispute with a human placement every step would eventually delete
            # a fact a person put there deliberately.
            if fact is not None and fact.source == "human":
                continue
            try:
                self.store.remember("npc_map", key, here,
                                    source="observed", confidence=0.9)
            except Exception:  # noqa: BLE001 - a fact store must not kill planning
                return

    def _note_situation(self, obs: Dict[str, Any]) -> None:
        """Forget retry/claim state that belonged to a different world state.

        The character can move, a quest step can advance, a group can spawn or
        the user can open a dialogue between two decisions. Reusing the previous
        scene's attempt counters makes the next action wrong even when the model
        reads the new observation correctly.
        """
        scene = scene_key(obs)
        if scene != self._scene_key:
            self._scene_key = scene
            self._claim_attempts.clear()
            self._claim_failures.clear()
            self._claim_actions.clear()
            self._claim_map.clear()
            self._pending_claim = None
            self._pending_start = None
            self._start_failures.clear()
            self._start_waits.clear()
            # `_npc_asked` is deliberately not cleared here. The NPCs on the map are the same
            # NPCs, and a scene key moving is not a new answer to a question already asked --
            # clearing it is what let 259 live steps re-ask four NPCs forever.
        quests = quest_key(obs)
        if quests != self._quest_key:
            self._quest_key = quests
            self._skipped.clear()
            self._skip_sigs.clear()
            self._plannable_cache.clear()

    def choose(self, goal: str, observation: Optional[Dict[str, Any]],
               tools: Optional[Sequence[str]] = None) -> Optional[Any]:
        from agent.runtime import ToolCall

        # A new decision supersedes an action whose result nobody reported. Keeping
        # it would let a later `note_result` for a different call be attributed to
        # the wrong objective.
        self._pending_claim = None
        self._pending_start = None
        self.current_objective = None
        self.last_dialog_decision = None

        # One normalisation for the nested snapshot, so `map_id`, `level` and the
        # inventory-by-id lookup are present instead of None. Without it the planner
        # could not tell which map it was on and never saw the items it already had.
        obs = flatten(observation)
        here = obs.get("map_id")
        previous = (self._last_obs or {}).get("map_id")
        if here is not None and previous is not None and here != previous:
            self._prev_map = previous
        self._last_obs = obs
        self._note_situation(obs)
        if obs.get("in_fight"):
            return ToolCall("state.snapshot", {}, goal)
        self.tracker.observe(obs)
        self._learn_world(obs)
        dialog = self._dialog_call(obs, goal)
        if dialog is not None:
            return dialog
        mode = _goal_mode(goal)
        if mode == "order":
            # The planner is the pinned policy, so simple orders must still reach
            # the rule parser instead of being hijacked into the quest curriculum.
            if self._rule_policy is None:
                from agent.runtime import RulePolicy
                self._rule_policy = RulePolicy()
            return self._rule_policy.choose(goal, obs, tools)
        if mode == "farm":
            target = _farm_target(goal)
            if self._group_read_broken:
                # No map answers this. One more read keeps the measurement alive while the
                # loop's no-progress guard decides, instead of spending the session walking
                # exits for a defect in our own client patch.
                return ToolCall("state.snapshot", {}, goal)
            if target and self._farm_target_missing:
                self._farm_target_missing = False
                explore = self._explore_call({}, obs, avoid=self._prev_map)
                if explore is not None:
                    return ToolCall(explore["tool"],
                                    dict(explore.get("args") or {}), goal)
                return ToolCall("state.snapshot", {}, goal)
            return ToolCall("farm.start", {"monster": target} if target else {}, goal)
        self._ensure_quests()
        level = int(obs.get("level") or self.level_hint)
        active = obs.get("quests_active") or {}
        self._seen_done |= {int(q) for q in (obs.get("quests_done") or [])}
        self._client_quests |= client_quest_ids(obs)
        active_ids = [int(k) for k in active.keys()]
        tutorial = self._tutorial(obs)
        # A changed tutorial step or map is a new situation; a quest given up on
        # under the old one deserves a fresh attempt.
        self._reopen(obs)

        # The tutorial first. If it dictates an action, do that before anything
        # read from the quest graph. The scenario knows which crossing and which tap
        # it is waiting for; the graph does not. This runs before the claim loop
        # because a ready objective left over from the scripted step would otherwise
        # pull the plan back into claiming/retrying while the step waits for an NPC
        # or a cell.
        guided = self._tutorial_call(obs, goal)
        if guided is not None:
            return guided

        # 0. Claim anything the game says is already done.
        #
        # objectiveStatus 2 (CAN_BE_FINISHED) means the conditions are met and the only
        # thing missing is the validation call. That is free progress, and leaving it
        # unclaimed is why a quest could look stuck while it was actually ready.
        #
        # The claim is attempted once. The entry repeats every step until something
        # changes, so a planner without memory of its own attempts re-sends a message
        # that has already been proven inert; the previous live session did exactly
        # that for its entire budget. After a failed claim, fall back to the
        # interaction the game itself uses for these objectives: walk to the NPC and
        # talk to it.
        ready_now = set()
        for item in (obs.get("ready_objectives") or []):
            try:
                ready_now.add((int(item["quest_id"]), int(item["objective_id"])))
            except (KeyError, TypeError, ValueError):
                continue
        # An objective that stops being ready is a new situation; a stale attempt
        # count would skip validation on a later one.
        for key in list(self._claim_attempts):
            if key not in ready_now:
                del self._claim_attempts[key]
                self._claim_failures.pop(key, None)
                self._claim_actions.pop(key, None)
                self._claim_map.pop(key, None)

        # A ready entry whose planned call names no target is a detour — a read, or
        # "walk out an exit and look for the group". Another ready objective may
        # name an NPC or a map it can act on right now, so the detour is kept as the
        # fallback and returned only if the scan finds nothing better.
        detour: Optional[Tuple[Any, ...]] = None
        for item in (obs.get("ready_objectives") or [])[:READY_SCAN_LIMIT]:
            try:
                quest_id = int(item["quest_id"])
                objective_id = int(item["objective_id"])
            except (KeyError, TypeError, ValueError):
                # A malformed ready-objective entry must not stop the plan, but it must
                # not be silently swallowed either: a bare `continue` here hid a
                # four-argument ToolCall for a while, and the branch never ran.
                continue
            if quest_id in self._skipped:
                continue
            obj = self._lookup_objective(quest_id, objective_id, item)
            if obj is None:
                continue
            key = (quest_id, objective_id)
            here = obs.get("map_id")

            # A change of map is progress even while the objective stays ready:
            # walking to a kill or an NPC can take several crossings. Reset only the
            # action bound here — failures are reset by `note_result`, which is the
            # only thing that knows whether the directive was accepted.
            if key in self._claim_map and here is not None and here != self._claim_map[key]:
                self._claim_actions[key] = 0
            failures = self._claim_failures.get(key, 0)
            actions = self._claim_actions.get(key, 0)
            if failures >= CLAIM_FAILURE_LIMIT or actions >= CLAIM_ACTION_LIMIT:
                # The action path has had its chance: three rejections in a row, or
                # ten actions that did not move the character. Retire the quest rather
                # than loop, but only now — not after the first fallback action.
                # During a tutorial the refusals may be the script's, not the quest's,
                # so falling through to the tutorial/active path is the honest move.
                if tutorial is not None:
                    continue
                self._retire(quest_id, obs)
                continue

            # A ready objective still has to happen where it happens. Status 2 is the
            # client's opinion that the conditions are met; the server is the one that
            # decides, and being on the wrong map is a precondition it will not waive.
            need = required_map(obj)
            if need and here != need:
                self.current_quest = quest_id
                self.current_objective = objective_id
                call = self._travel_call(obj, need, obs)
                self._claim_actions[key] = actions + 1
                self._claim_map[key] = here
                self._pending_claim = (key, call["tool"])
                return ToolCall(call["tool"], dict(call["args"]), goal)

            attempts = self._claim_attempts.get(key, 0)
            try:
                type_id = int(obj.get("type_id"))
            except (TypeError, ValueError):
                type_id = None
            if type_id in self._server_counted_types or key in self._claim_quarantine:
                # `quest.advance` cannot validate this type: the client answers
                # `objective_server_counted` and sends nothing. Go straight to
                # the action the objective names instead of spending a step on
                # a message that cannot work. A quarantined claim takes the same
                # route — the objective is still performed, only the packet that
                # cost a connection is withdrawn, so a tutorial is not abandoned.
                attempts = max(attempts, 1)
            if attempts == 0:
                self._claim_attempts[key] = 1
                self._claim_map[key] = here
                self._pending_claim = (key, "quest.advance")
                self.current_quest = quest_id
                self.current_objective = objective_id
                return ToolCall("quest.advance",
                                {"quest_id": quest_id, "objective_id": objective_id}, goal)

            # The claim did not move it. Do the objective's own action, and keep
            # doing it while the caller reports success: the server requires the
            # interaction that produced the status, and a guard refusal means the
            # validation is not available at all. Talk walks to the NPC and talks; a
            # kill farms or joins a fight; a map objective travels. For a talk
            # objective this sends `npc_id` without `objective_id`, because the
            # client prioritises the latter and would otherwise re-send the message
            # that did not work.
            calls = self.plan_objective({"id": quest_id}, 0, obj, obs)
            if calls:
                first = calls[0]
                if not names_target(first):
                    if detour is None:
                        detour = (key, actions, here, first, quest_id, objective_id)
                    continue
                self.current_quest = quest_id
                self.current_objective = objective_id
                self._claim_actions[key] = actions + 1
                self._claim_map[key] = here
                self._pending_claim = (key, first["tool"])
                return ToolCall(first["tool"], dict(first["args"]), goal)
            # The objective's own type translates to nothing we can do (an area or
            # a craft with no recipe). Retire only this case immediately.
            if tutorial is not None:
                continue
            self._skipped.add(quest_id)

        if detour is not None:
            # Nothing ready could be acted on directly. Take the detour after all:
            # it is the respawn wait or the search the objective asked for, and the
            # bounds on it (`_claim_actions`) still retire the quest if it never
            # moves. Retiring a quest must not blind this loop to the ready
            # objectives behind it, which is what one-entry scan did.
            key, actions, here, first, quest_id, objective_id = detour
            self.current_quest = quest_id
            self.current_objective = objective_id
            self._claim_actions[key] = actions + 1
            self._claim_map[key] = here
            self._pending_claim = (key, first["tool"])
            return ToolCall(first["tool"], dict(first["args"]), goal)

        # 1. Continue an open quest if the observation says one is running.
        for qid, idx in sorted(active.items()):
            quest_id = int(qid)
            # A quest this session has given up on stays in `quests_active` — the
            # game does not let go of it — but re-planning it every step produces the
            # same call forever. `_skipped` used to filter only *new* quests, so
            # retiring an active one had no effect at all.
            if quest_id in self._skipped:
                continue
            if quest_id not in self.quests:
                # The client serves quests the cached tables do not have — the tutorial
                # it starts on is one of them. Its snapshot carries the objective's type
                # and parameters, so this quest is plannable without the KB; before,
                # "not in quests_full.json" meant the loop could not even try.
                obj = self._observed_current(quest_id, obs)
                if obj is not None:
                    call = self._plan_observed(quest_id, obj, obs, goal)
                    if call is not None:
                        return call
                continue
            call = self._plan_quest(int(qid), int(idx), obs, goal)
            if call is not None:
                return call
            # The open quest cannot be planned with what we know (an area objective,
            # a craft with no recipe). Retiring it is honest; guessing a tool for it
            # is how a policy spends a hundred steps doing the wrong thing. During a
            # tutorial, keep it open: the script may move on to a plannable step.
            if tutorial is not None:
                continue
            self._retire(int(qid), obs)

        if tutorial is not None:
            # The tutorial drives what happens next. Starting an unrelated quest is
            # the loop the live run showed: the server refused each start while the
            # scenario was active, and the quest the tutorial was actually advancing
            # never got planned again. Wait for the step instead.
            return ToolCall("state.snapshot", {}, goal)

        # 2. Otherwise start the cheapest quest we can actually finish.
        order = self._curriculum(level)
        focused = [q for q in self._focus_order(level) if q in set(order)]
        if focused:
            # Rotate inside the focused set so one long quest cannot monopolise an
            # episode, then fall back to the ordinary order.
            start = self.rng.randrange(len(focused))
            order = focused[start:] + focused[:start] + order
        elif self.curriculum == "random" and order:
            head = order[:300]
            start = self.rng.randrange(len(head))
            order = head[start:] + head[:start] + order[300:]
        seen_ids: set = set()
        for qid in order:
            if qid in seen_ids:
                continue
            seen_ids.add(qid)
            if qid in self._seen_done or qid in self._skipped:
                continue
            if not self._plannable(qid):
                self._skipped.add(qid)
                continue
            if qid not in active_ids:
                # Not active yet: accept it first. A quest is a contract before it
                # is a list of objectives, and a kill-first quest is unstartable
                # otherwise. But a start the server never honours is not a contract:
                # after START_ATTEMPT_LIMIT observations that never show it active,
                # take another quest instead of re-sending the request forever.
                if qid not in self._client_quests:
                    # G31: this game has never shown this quest, so asking to start it is
                    # a request the server cannot honour -- and the attempt bound below
                    # cannot be the guard, because a scene change clears it and a quest-log
                    # change clears `_skipped`. Nothing here hard-codes a quest: an id the
                    # client reports later becomes startable, and the client-side quest
                    # capture (G29) is what will populate this set properly.
                    self._skipped.add(qid)
                    continue
                if self._start_failures.get(qid, 0) >= START_ATTEMPT_LIMIT:
                    self._skipped.add(qid)
                    self._start_failures.pop(qid, None)
                    self._start_waits.discard(qid)
                    continue
                self.current_quest = qid
                self.current_objective = None   # accepting has no objective yet
                self._pending_start = qid
                if obs.get("in_dialog"):
                    dialog = obs.get("dialog") if isinstance(obs.get("dialog"), dict) else {}
                    if dialog_offer(dialog) == qid:
                        return ToolCall("quest.advance",
                                        {"quest_id": qid, "accept": True}, goal)
                    # A dialog is open and it is not offering this quest; a start
                    # request inside one is refused by the page (G33), so close the
                    # conversation first and send the request on the next step.
                    return ToolCall("quest.advance",
                                    {"quest_id": qid, "close": True}, goal)
                return ToolCall("quest.advance", {"quest_id": qid}, goal)
            call = self._plan_quest(qid, int(active[str(qid)] if str(qid) in active else active[qid]), obs, goal)
            if call is not None:
                return call
            self._skipped.add(qid)
        if mode == "auto":
            # Full Auto: quests first (above); when the curriculum has nothing
            # plannable, a standing farm order keeps the session productive.
            return ToolCall("farm.start", {}, goal)
        return None

    def skip(self, quest_id: Any) -> None:
        """Retire a quest the agent cannot advance.

        Nothing inside `_plan_quest` can tell "not yet" from "not ever": it reads the
        KB and the observation, and both say the objective is still open. Only the
        loop knows it has tried three times with no change, so only the loop can say
        this.
        """
        try:
            self._retire(int(quest_id), self._last_obs)
        except (TypeError, ValueError):
            pass

    def plan_quest(self, quest_id: int, obs: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The whole plan for a quest — used to build the SFT corpus."""
        quest = self.quests.get(quest_id)
        if quest is None:
            return []
        out: List[Dict[str, Any]] = []
        for step_idx, step in enumerate(quest["steps"]):
            for obj in step.get("objectives") or []:
                out.extend(self.plan_objective(quest, step_idx, obj, obs))
        return out
