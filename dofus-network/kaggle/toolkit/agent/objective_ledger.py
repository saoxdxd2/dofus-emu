"""Per-objective belief: what we think closes it, what we tried, and what that proved.

Why this exists. The loop already has three layers that answer "did something happen":

    verifier    STATE_KEYS diff -> advanced / noop / failed / blocked   (includes the quest keys)
    controller  cause -> continue / retry / recover / replan            (counts, persisted)
    planner     plan returns None N times -> skip the quest             (a bound, not evidence)

What none of them keeps is the thing a player keeps: **for this objective, what have I believed,
what did I try because of that belief, and what did the attempt prove?** The measured consequence is
`KNOWN_GAPS.md` G98 -- 306 waits on one tutorial objective while the bag held two items and nothing
was ever equipped, because the attempt counters (`_ui_attempts`, `_equip_tries`) are per-call and the
give-up trigger is a wait count (40 passes x 4 windows), not a refuted hypothesis.

The shape here is deliberately small, because it has to run inside the 200 MB instance:

    ObjectiveRecord   status, evidence, an ordered list of hypotheses, a block reason
    Hypothesis        a tool + args, why it was chosen, tries, and the verdicts it earned
    confirm()         did *this objective* move? -- the only question that counts
    next_hypothesis() generate the next thing to try from the observation, or say "nothing left"

`confirm` is the confirmation layer: it compares the objective before and after the step, so an
action that changes the world without moving the objective is a failed hypothesis, not progress.
A hypothesis is refuted after `max_tries` attempts with no movement; when every hypothesis is
refuted the objective is marked `blocked` **with its evidence** and re-opened the moment the
observation changes in a way that could matter (new item, new map, level up) -- instead of being
retired for the run.

    from agent.objective_ledger import ObjectiveLedger
    led = ObjectiveLedger.load(path, instance="dofus-04")
    rec = led.sync(obs)                       # status + evidence from the observation
    hyp = led.next_hypothesis(rec, obs)       # what to try now
    ... execute hyp ...
    moved = led.record(rec, hyp, obs_after, verdict="noop")
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

MAX_TRIES = 3

# The page's own words when it refuses a claim. `quest.advance` on such an objective answers
# "objective 13531 (QuestObjectiveFreeForm) is completed by the server; perform the action
# instead of claiming it", and that sentence is the evidence for a fact worth keeping: this
# *type* of objective is never claimable. Learning it here beats asserting `type_id == 0` in
# `_generate`, because the assertion is a claim about a type we may not have met yet.
SERVER_COUNTED_HINT = "completed by the server"
MAX_RECORDS = 64


# ---------------------------------------------------------------------------
# hypotheses
# ---------------------------------------------------------------------------


@dataclass
class Hypothesis:
    """One thing to try, and the reason it is being tried."""

    tool: str
    args: Dict[str, Any] = field(default_factory=dict)
    why: str = ""
    tries: int = 0
    verdicts: List[str] = field(default_factory=list)

    @property
    def refuted(self) -> bool:
        return self.tries >= MAX_TRIES

    def key(self) -> str:
        return "%s:%s" % (self.tool, json.dumps(self.args, sort_keys=True, default=str))

    def to_dict(self) -> Dict[str, Any]:
        return {"tool": self.tool, "args": self.args, "why": self.why,
                "tries": self.tries, "verdicts": list(self.verdicts)}

    @classmethod
    def from_dict(cls, blob: Dict[str, Any]) -> "Hypothesis":
        return cls(tool=str(blob.get("tool") or ""), args=dict(blob.get("args") or {}),
                   why=str(blob.get("why") or ""), tries=int(blob.get("tries") or 0),
                   verdicts=list(blob.get("verdicts") or []))


@dataclass
class ObjectiveRecord:
    """What is believed about one objective, and what has been tried on it."""

    quest_id: int
    objective_id: int
    status: str = "open"                 # open | ready | closed | blocked
    text: str = ""
    hypotheses: List[Hypothesis] = field(default_factory=list)
    block_reason: str = ""
    reopen_on: Dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    confirmed_at: float = 0.0
    last_seen: float = field(default_factory=time.time)

    def key(self) -> str:
        return "%d:%d" % (self.quest_id, self.objective_id)

    def to_dict(self) -> Dict[str, Any]:
        return {"quest_id": self.quest_id, "objective_id": self.objective_id,
                "status": self.status, "text": self.text,
                "hypotheses": [h.to_dict() for h in self.hypotheses],
                "block_reason": self.block_reason, "reopen_on": self.reopen_on,
                "attempts": self.attempts, "confirmed_at": self.confirmed_at,
                "last_seen": self.last_seen}

    @classmethod
    def from_dict(cls, blob: Dict[str, Any]) -> "ObjectiveRecord":
        rec = cls(quest_id=int(blob.get("quest_id") or 0),
                  objective_id=int(blob.get("objective_id") or 0),
                  status=str(blob.get("status") or "open"),
                  text=str(blob.get("text") or ""))
        rec.hypotheses = [Hypothesis.from_dict(h) for h in (blob.get("hypotheses") or [])]
        rec.block_reason = str(blob.get("block_reason") or "")
        rec.reopen_on = dict(blob.get("reopen_on") or {})
        rec.attempts = int(blob.get("attempts") or 0)
        rec.confirmed_at = float(blob.get("confirmed_at") or 0.0)
        rec.last_seen = float(blob.get("last_seen") or 0.0)
        return rec


# ---------------------------------------------------------------------------
# reading the observation
# ---------------------------------------------------------------------------


def _ready_ids(obs: Dict[str, Any]) -> set:
    out = set()
    for item in (obs.get("ready_objectives") or []):
        try:
            out.add((int(item["quest_id"]), int(item["objective_id"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _current(obs: Dict[str, Any]) -> Optional[tuple]:
    cur = obs.get("current_objective") or {}
    try:
        return int(cur["quest_id"]), int(cur["objective_id"])
    except (KeyError, TypeError, ValueError):
        return None


def _bag(obs: Dict[str, Any]) -> Dict[str, Any]:
    bag = obs.get("inventory")
    return bag if isinstance(bag, dict) else {}


def _unequipped(obs: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Bag items that are not worn. The same rule the planner's equip route uses."""
    bag = _bag(obs)
    items = [row for row in (bag.get("items") or bag.get("objects") or [])
             if isinstance(row, dict)]
    worn = [row for row in (bag.get("equipped") or []) if isinstance(row, dict)]
    worn_gids: Dict[int, int] = {}
    for row in worn:
        try:
            worn_gids[int(row.get("gid"))] = worn_gids.get(int(row.get("gid")), 0) + 1
        except (TypeError, ValueError):
            continue
    bag_gids: Dict[int, int] = {}
    for row in items:
        try:
            bag_gids[int(row.get("gid"))] = bag_gids.get(int(row.get("gid")), 0) + 1
        except (TypeError, ValueError):
            continue
    out = []
    for row in items:
        try:
            gid = int(row.get("gid"))
        except (TypeError, ValueError):
            out.append(row)
            continue
        if worn_gids.get(gid, 0) >= bag_gids.get(gid, 0) > 0:
            continue
        out.append(row)
    return out


def _npcs_here(obs: Dict[str, Any]) -> List[int]:
    out = []
    for row in ((obs.get("map") or {}).get("npcs") or []):
        try:
            npc = int(row.get("npc_id"))
        except (KeyError, TypeError, ValueError):
            continue
        if npc > 0 and npc not in out:
            out.append(npc)
    return out


# ---------------------------------------------------------------------------
# the ledger
# ---------------------------------------------------------------------------


class ObjectiveLedger:
    """Per-instance, per-objective belief, persisted so a rethink survives a restart."""

    def __init__(self, path: Optional[Path] = None, instance: str = "default") -> None:
        self.path = Path(path) if path else None
        self.instance = instance
        self.records: Dict[str, ObjectiveRecord] = {}
        self.completed_seen = 0
        # Objective `type_id`s the page has told us it completes server-side. Empty on a fresh
        # ledger: the first refused claim teaches one, and it persists from then on.
        self.server_counted_types: Set[int] = set()
        self.load()

    # -- persistence ---------------------------------------------------------

    def load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            blob = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.completed_seen = int(blob.get("completed_seen") or 0)
        for raw_type in (blob.get("server_counted_types") or []):
            try:
                self.server_counted_types.add(int(raw_type))
            except (TypeError, ValueError):
                continue
        for key, raw in (blob.get("records") or {}).items():
            try:
                self.records[key] = ObjectiveRecord.from_dict(raw)
            except (TypeError, ValueError):
                continue

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"instance": self.instance, "completed_seen": self.completed_seen,
                   "server_counted_types": sorted(self.server_counted_types),
                   "saved_at": round(time.time(), 3),
                   "records": {k: r.to_dict() for k, r in self.records.items()}}
        self.path.write_text(json.dumps(payload, indent=1), encoding="utf-8")

    # -- reading the world ---------------------------------------------------

    def sync(self, obs: Dict[str, Any]) -> Optional[ObjectiveRecord]:
        """The objective this step is about, with its status refreshed from the observation."""
        # The *ready* objective is the actionable one: the game already accepts it, and a claim
        # works from anywhere. `current_objective` is the tracker's pointer and can name a
        # different objective entirely -- measured on the 2026-11-06 session, where the pointer sat
        # on 13541 ("find the map") while the blocker was 13507 ("open the bag"), and a ledger that
        # followed the pointer would have planned against the wrong one forever.
        ready = _ready_ids(obs)
        cur = _current(obs)
        if cur is not None and cur in ready:
            current = cur
        elif ready:
            current = sorted(ready)[0]
        elif cur is not None:
            current = cur
        else:
            return None
        rec = self.records.get("%d:%d" % current)
        if rec is None:
            rec = ObjectiveRecord(quest_id=current[0], objective_id=current[1])
            if len(self.records) >= MAX_RECORDS:
                oldest = min(self.records.values(), key=lambda r: r.last_seen)
                self.records.pop(oldest.key(), None)
            self.records[rec.key()] = rec
        rec.last_seen = time.time()
        rec.status = "ready" if current in _ready_ids(obs) else "open"
        for item in (obs.get("ready_objectives") or []):
            if (str(item.get("quest_id")), int(item.get("objective_id") or 0)) == (
                    str(current[0]), current[1]):
                rec.text = str(item.get("text") or rec.text)
                break
        if not rec.text:
            rec.text = str((obs.get("current_objective") or {}).get("text") or "")
        # A record that was blocked is re-opened when the world has changed in a way the
        # block reason named. This is the "rethink" half: blocked is not retired.
        if rec.status == "blocked" and self._reopened(rec, obs):
            rec.status = "ready" if current in _ready_ids(obs) else "open"
            rec.block_reason = ""
            rec.reopen_on = {}
        return rec

    @staticmethod
    def _reopened(rec: ObjectiveRecord, obs: Dict[str, Any]) -> bool:
        want = rec.reopen_on or {}
        if not want:
            return False
        if "map_id" in want and want["map_id"] != (obs.get("map") or {}).get("map_id"):
            return True
        if "level" in want:
            try:
                if int(want["level"]) != int((obs.get("character") or {}).get("level") or 0):
                    return True
            except (TypeError, ValueError):
                pass
        if want.get("bag_grew") and len(_bag(obs).get("items") or []) > int(
                want.get("bag_items") or 0):
            return True
        return False

    # -- confirmation --------------------------------------------------------

    def confirm(self, rec: ObjectiveRecord, before: Dict[str, Any],
                after: Dict[str, Any]) -> bool:
        """Did *this objective* move? The only question that counts.

        Three ways it can: the objective left the ready set, the completed counter rose, or the
        current objective changed. A world change that leaves all three alone is not progress on
        this objective, however much else moved.
        """
        key = (rec.quest_id, rec.objective_id)
        was_ready = key in _ready_ids(before)
        now_ready = key in _ready_ids(after)
        if was_ready and not now_ready:
            rec.status = "closed"
            rec.confirmed_at = time.time()
            return True
        try:
            if int(after.get("objectives_completed") or 0) > int(
                    before.get("objectives_completed") or 0):
                rec.status = "closed"
                rec.confirmed_at = time.time()
                return True
        except (TypeError, ValueError):
            pass
        cur_before, cur_after = _current(before), _current(after)
        if cur_after is not None and cur_after != key:
            rec.status = "closed"
            rec.confirmed_at = time.time()
            return True
        if cur_before == key and cur_after is None and not now_ready:
            rec.status = "closed"
            rec.confirmed_at = time.time()
            return True
        return False

    # -- choosing what to try next -------------------------------------------

    def next_hypothesis(self, rec: ObjectiveRecord, obs: Dict[str, Any]) -> Optional[Hypothesis]:
        """The next untried-or-unrefuted thing for this objective, generated from the world.

        Order is evidence-first, cheapest-confirmation-first: a claim the game already accepts,
        then the bag (an item to wear), then a menu (the objective's own text names one), then
        the NPCs standing here, then travel. A hypothesis is skipped once refuted, so the list
        walks forward instead of repeating the first guess forever.
        """
        candidates = self._generate(rec, obs)
        live = set(cand.key() for cand in candidates)
        # Anything stored, unrefuted and no longer generated has lost its premise: the uid is
        # not in the bag any more, the NPC has left the map, the dialog has closed. Retrying it
        # is asking the client for something that is not there -- measured 2026-11-06, a
        # persisted `inv.equip {object_uid: 144784272}` outlived the character it belonged to
        # and every attempt got "sent nothing and the item is not usable", which reads like a
        # missing slot and was really a missing item. The sweep runs *before* the candidates are
        # offered, not after: a fresh candidate returns immediately, so a later sweep would
        # leave the dead one unrefuted and it would come back the moment the fresh one was.
        for hyp in rec.hypotheses:
            if hyp.refuted or hyp.key() in live:
                continue
            # `refuted` is derived from the try budget, so retiring a stale hypothesis means
            # spending that budget -- the same thing `record` does when the page refuses.
            hyp.tries = MAX_TRIES
            hyp.verdicts.append("stale")
        for cand in candidates:
            known = next((h for h in rec.hypotheses if h.key() == cand.key()), None)
            if known is None:
                rec.hypotheses.append(cand)
                return cand
            if not known.refuted:
                return known
        return None

    @staticmethod
    def _objective_type(obs: Dict[str, Any], rec: ObjectiveRecord) -> Optional[int]:
        """The objective's `type_id`, as the ready row publishes it, or None when absent.

        None means "not stated" and must not be read as "claimable": the caller compares
        against 0 explicitly, so an older observation keeps the old behaviour.
        """
        for item in (obs.get("ready_objectives") or []):
            if not isinstance(item, dict):
                continue
            try:
                if (str(item.get("quest_id")) == str(rec.quest_id)
                        and int(item.get("objective_id") or 0) == int(rec.objective_id)):
                    return int(item.get("type_id"))
            except (TypeError, ValueError):
                continue
        current = obs.get("current_objective")
        if isinstance(current, dict):
            try:
                if (str(current.get("quest_id")) == str(rec.quest_id)
                        and int(current.get("objective_id") or 0) == int(rec.objective_id)):
                    return int(current.get("type_id"))
            except (TypeError, ValueError):
                return None
        return None

    def learn_server_counted(self, rec: ObjectiveRecord, obs: Dict[str, Any],
                             reason: str) -> Optional[int]:
        """Record that this objective's type is completed by the server, when it says so.

        The trigger is the page's sentence, not a guess: `SERVER_COUNTED_HINT`. The fact is kept
        per type because the refusal names the type ("objective 13531 (QuestObjectiveFreeForm)
        is completed by the server"), and one refused claim is enough to stop offering claims on
        every objective of that kind -- here and in later sessions.

        Returns the type it learned, or None when the refusal was about something else.
        """
        if SERVER_COUNTED_HINT not in str(reason or "").lower():
            return None
        otype = self._objective_type(obs, rec)
        if otype is None or otype in self.server_counted_types:
            return None
        self.server_counted_types.add(int(otype))
        self.save()
        return int(otype)

    def _generate(self, rec: ObjectiveRecord, obs: Dict[str, Any]) -> List[Hypothesis]:
        out: List[Hypothesis] = []
        # A `QuestObjectiveFreeForm` (type 0) is completed by the server, not claimed: the page
        # answers "objective 13531 (QuestObjectiveFreeForm) is completed by the server; perform
        # the action instead of claiming it". Generating the claim anyway cost one refused step
        # per objective, measured live 2026-11-06 -- twice in eight steps. The type is on the
        # ready row, so the rule is read from the observation rather than hardcoded per quest.
        if (rec.status == "ready"
                and self._objective_type(obs, rec) not in self.server_counted_types):
            out.append(Hypothesis("quest.advance",
                                  {"quest_id": rec.quest_id, "objective_id": rec.objective_id},
                                  "the game already accepts this objective"))
        low = rec.text.lower()
        if any(word in low for word in ("inventaire", "inventory", "sac", "bag")):
            out.append(Hypothesis("ui.tap", {"menu": "Bag", "cycle": True},
                                  "the objective names the bag"))
            for row in _unequipped(obs)[:4]:
                uid = row.get("object_uid", row.get("uid"))
                if uid is None:
                    continue
                out.append(Hypothesis("inv.equip", {"object_uid": int(uid)},
                                      "the bag holds an item that is not worn"))
        if any(word in low for word in ("sorts", "spell")):
            out.append(Hypothesis("ui.tap", {"menu": "Spell", "cycle": True},
                                  "the objective names the spell interface"))
        dialog = obs.get("dialog") if isinstance(obs.get("dialog"), dict) else {}
        npc = dialog.get("npc_id")
        if npc:
            out.append(Hypothesis("quest.advance",
                                  {"quest_id": rec.quest_id, "npc_id": int(npc), "reply": 0},
                                  "a dialog is open with this NPC"))
        for npc_id in _npcs_here(obs)[:4]:
            out.append(Hypothesis("quest.advance",
                                  {"quest_id": rec.quest_id, "npc_id": npc_id},
                                  "this NPC is standing on the map"))
        return out

    # -- recording what happened ---------------------------------------------

    def record(self, rec: ObjectiveRecord, hyp: Optional[Hypothesis],
               before: Dict[str, Any], after: Dict[str, Any],
               verdict: str = "", reason: str = "") -> bool:
        """Credit or refute a hypothesis. Returns True when the objective moved.

        `reason` is the refusal's own sentence when the caller has one. It is what turns a
        single refused claim into a fact about the objective's type, instead of a rule in code.
        """
        if verdict == "failed" and reason:
            learned = self.learn_server_counted(rec, after, reason)
            if learned is not None:
                print("[ledger] learned: objective type %d is completed by the server, so a "
                      "claim on it is never offered again" % learned)
        moved = self.confirm(rec, before, after)
        rec.attempts += 1
        if hyp is not None:
            hyp.tries += 1
            hyp.verdicts.append(str(verdict or ("advanced" if moved else "noop"))[:24])
            if str(verdict) == "failed":
                # A refusal is evidence, not a tie: the page answered "no" ("window grimoire is
                # not open", "no npc_id was given"), so retrying the same call twice more only
                # spends the step budget. A `noop` is different -- accepted, nothing moved --
                # and that one gets the full retry budget before it is refuted.
                hyp.tries = MAX_TRIES
        if moved:
            self.save()
            return True
        if rec.hypotheses and all(h.refuted for h in rec.hypotheses):
            rec.status = "blocked"
            rec.block_reason = ("every hypothesis refuted: "
                                + ", ".join(h.tool for h in rec.hypotheses[:6]))
            rec.reopen_on = {"map_id": (after.get("map") or {}).get("map_id"),
                             "level": (after.get("character") or {}).get("level"),
                             "bag_grew": True,
                             "bag_items": len(_bag(after).get("items") or [])}
        self.save()
        return False
