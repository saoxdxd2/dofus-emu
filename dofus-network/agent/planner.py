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

import json
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from agent.observe import flatten
from agent.dialog_policy import rank_dialog
from agent.awareness import quest_key, scene_key
from agent.features import (CRAFT_TYPES, DELIVER_TYPES, FIGHT_TYPES,  # noqa: E402
                            KILL_TYPES, MAP_TYPES, ObsTracker, TALK_TYPES,
                            _params, direction_hint, known_map,
                            objective_type, required_map, travel_hops)

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE = REPO_ROOT / "data" / "cache"

# A refused claim is the start of the objective's action path, not the end of the
# quest. The planner retires a ready objective only after this many hard failures
# in a row, or after this many actions that did not even change the map (a walk
# that is crossing the world legitimately changes it, so only a truly inert
# action loop reaches the second bound).
CLAIM_FAILURE_LIMIT = 3
CLAIM_ACTION_LIMIT = 10

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
    return {}


class QuestPlanner:
    """A policy that plans over the quest graph."""

    def __init__(self, level_hint: int = 60, max_quest_level: int = 200,
                 store: Any = None, curriculum: str = "greedy",
                 seed: int = 0, focus: Optional[int] = None,
                 ordering: str = "step") -> None:
        self.quests: Dict[int, Dict[str, Any]] = {}
        path = CACHE / "quests_full.json"
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                for q in json.load(fh):
                    if q.get("steps"):
                        self.quests[int(q["id"])] = q
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
        self._claim_actions: Dict[Tuple[int, int], int] = {}
        self._claim_map: Dict[Tuple[int, int], Any] = {}
        # A scripted fight can be started by a map NPC even when no monster group
        # exists. Rotate through the NPCs on the current map when the objective is
        # a fight and the map reports no group, so a wrong first NPC does not pin
        # the planner to the same dialogue forever.
        self._npc_tries: Dict[Tuple[int, int, int], int] = {}
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
        exits: List[Dict[str, Any]] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            direction = row.get("direction")
            try:
                dest = int(row.get("map_id"))
            except (TypeError, ValueError):
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
        the encounter standing on the map. Keep the choice generic and rotate
        through the map's NPCs so one wrong dialogue cannot pin the loop.
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
        tries = self._npc_tries.get(key, 0)
        npc = npcs[tries % len(npcs)]
        self._npc_tries[key] = tries + 1
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
        if decision is None:
            return None
        quest_id = decision.get("quest_id")
        self.current_quest = quest_id
        self.current_objective = decision.get("objective_id")
        reply = decision.get("reply")
        if decision.get("status") == "choose" and quest_id is not None and reply:
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
            return ToolCall("quest.advance", {"quest_id": qid}, goal)

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
            monster = p.get("parameter0")
            calls = list(travel)
            if t in FIGHT_TYPES:
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
                    # live tutorial had an empty map and a fight objective, and
                    # the planner sent the join anyway; the guard refused it and
                    # the run ended. The objective names the monster, so the
                    # honest move is to go and find it.
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
            calls.append({"tool": "farm.start",
                          "args": {"monster": monster} if monster else {},
                          "why": "objective %d: kill" % t})
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
            # The tracker names an objective that is not in the step we were given. The
            # client reports `finishedCount` as the step index, and there is no promise
            # that it matches the KB's numbering — so search the whole quest before
            # concluding the objective does not exist. `args_for_tool` reads the
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
                        for key in ("type_id", "map_id"):
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

    def note_result(self, tool: str, ok: bool, note: str, obs: Dict[str, Any]) -> None:
        """The caller reports what happened. Without this the policy cannot learn
        from its own failures, and any history feature would be a guess."""
        self.tracker.record(tool, ok, note, obs)
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
                           or "completed by the server" in note):
                # The client told us this type is completed by playing, not by
                # claiming. Remember it for every quest, not just this one.
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
        twin._prev_map = self._prev_map
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
            self._npc_tries.clear()
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
        level = int(obs.get("level") or self.level_hint)
        active = obs.get("quests_active") or {}
        self._seen_done |= {int(q) for q in (obs.get("quests_done") or [])}
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

        for item in (obs.get("ready_objectives") or [])[:1]:
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
            if type_id in self._server_counted_types:
                # `quest.advance` cannot validate this type: the client answers
                # `objective_server_counted` and sends nothing. Go straight to
                # the action the objective names instead of spending a step on
                # a message that cannot work.
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
                if self._start_failures.get(qid, 0) >= START_ATTEMPT_LIMIT:
                    self._skipped.add(qid)
                    self._start_failures.pop(qid, None)
                    self._start_waits.discard(qid)
                    continue
                self.current_quest = qid
                self.current_objective = None   # accepting has no objective yet
                self._pending_start = qid
                return ToolCall("quest.advance", {"quest_id": qid}, goal)
            call = self._plan_quest(qid, int(active[str(qid)] if str(qid) in active else active[qid]), obs, goal)
            if call is not None:
                return call
            self._skipped.add(qid)
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
