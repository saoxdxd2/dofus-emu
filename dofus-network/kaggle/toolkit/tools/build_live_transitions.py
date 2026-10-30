"""Turn the live logs into real-outcome transitions: did the issued tool move the world?

Everything else in this repo labels decisions from the harness (`sim/tool_choice.py` forks a
model of the game) or from the rule that proposed the action. Neither is evidence about the
real client, and the measured failure is precisely that: of the world-changing steps in the
recorded baseline, only 4.5% changed anything. This builder makes the other half of the
supervision exist, without inventing a label:

* one row per executed step that has both a before observation and an after observation;
* the label is `agent.verifier.changed_keys` — the shipped definition of "the world moved",
  the same one the settle window uses, so the offline dataset and the live loop cannot drift;
* `progress` is the strict subset: a quest counter moved. That is the criterion the goal is
  judged on, and `moved` alone would count an HP tick as success;
* reads get their own flag, because a read is not allowed to look like progress;
* the 42-number state vector is rebuilt with the *live* extractor
  (`agent.features.objective_features`) and the `streak/last_ok/last_tool/last_note/obs_kind`
  context re-derived from the log, so a model trained on harness forks can be scored on these
  rows with identical features — the sim-to-live transfer number is only meaningful if the two
  sides use the same vector;
* the split is by **segment** (a contiguous run of one goal), never by row, so step *t* cannot
  appear in train while step *t+1* sits in val.

    python tools/build_live_transitions.py                 # all trajectories/*.jsonl
    python tools/build_live_transitions.py --out data/transitions --segment-gap-s 120
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.features import (FEATURE_COUNT, objective_features,  # noqa: E402
                            objective_type)
from agent.gate import VOLATILE_ARGS  # noqa: E402
from agent.observe import flatten  # noqa: E402
from agent.verifier import (READ_ONLY_TOOLS, STATE_KEYS, changed_keys)  # noqa: E402

# The state keys that mean "the quest moved", as opposed to "something on screen ticked".
PROGRESS_KEYS = ("ready_objectives", "objectives_completed", "quests_active",
                 "quests_finished", "quest_steps")
# A stricter label than `progress`: the quest itself moved. `progress` also fires
# when an objective merely *became claimable* (ready_objectives) or a quest was
# accepted (quests_active); measured 2026-10-29, that is 76 events of which only
# 11 complete an objective, and the test split holds none of them.
ADVANCE_KEYS = ("objectives_completed", "quest_steps", "quests_finished")
# Anything here counts as the world having moved. hp/xp are deliberately excluded: they drift
# while a character stands still, and a label that fires on them would make every action look
# like progress.
WORLD_KEYS = ("cell", "map_id", "mapId", "in_dialog", "in_fight", "inventory_size",
              "kamas") + PROGRESS_KEYS

SPLIT_BUCKETS = (700, 150, 150)  # train, val, test, in permille of a segment id hash


def _int(value: Any) -> int:
    """The quest step index, the way `objective_features` expects it (not the log position)."""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _row_ts(row: Dict[str, Any]) -> float:
    for key in ("ts",):
        value = row.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    iso = str(row.get("iso") or "")
    if iso:
        try:
            return time.mktime(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))
        except (ValueError, OverflowError):
            return 0.0
    return 0.0


def _after_obs(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The observation the client returned *for this directive*, if it logged one."""
    result = row.get("result")
    if isinstance(result, dict):
        snapshot = result.get("snapshot")
        if isinstance(snapshot, dict) and snapshot:
            return snapshot
    return None


def _refusal(row: Dict[str, Any]) -> Optional[str]:
    """The page's own refusal of this directive, if it answered without sending.

    A directive can be transport-ok and still refused before any packet leaves
    the page: the bridge wraps the page's `{ok: false, reason, error}` one level
    down (`result.response.result`). The claim label judges a call by the
    after-state, so a refused call was credited whenever the world moved for
    another reason - measured 2026-10-30, 63 of 945 refused steps read as
    `claim_met` (11 `objective_server_counted`, 59 `quest.advance` in all).
    """
    result = row.get("result")
    if not isinstance(result, dict):
        return None
    body = result
    response = result.get("response")
    if isinstance(response, dict):
        inner = response.get("result")
        if isinstance(inner, dict):
            body = inner
    if body.get("ok") is not False:
        return None
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    reason = data.get("reason")
    text = body.get("error") or data.get("error")
    if reason and text:
        return "%s: %s" % (reason, text)
    return str(reason or text or "refused")


def _objective_of(before: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], int]:
    quest_level = 0
    active = before.get("quests_active")
    if isinstance(active, list):
        for entry in active:
            if isinstance(entry, dict):
                try:
                    quest_level = int(entry.get("level") or 0)
                except (TypeError, ValueError):
                    quest_level = 0
                break
    obj = before.get("current_objective")
    if not isinstance(obj, dict):
        ready = before.get("ready_objectives")
        if isinstance(ready, list):
            obj = next((r for r in ready if isinstance(r, dict)), None)
    return (obj if isinstance(obj, dict) else None), quest_level


def _obs_kind(row: Dict[str, Any]) -> str:
    """Stale/partial/fresh, from the fields the page itself reports."""
    source = str(row.get("observation_source") or "").lower()
    if "stale" in source:
        return "stale"
    if "partial" in source:
        return "partial"
    return "fresh"


def segments(rows: Iterable[Dict[str, Any]], source: str,
             gap_s: float) -> List[Dict[str, Any]]:
    """Contiguous (goal, session) runs; a gap, a restart or a new goal starts one."""
    out: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    last_ts = 0.0
    for row in rows:
        if row.get("kind") != "step" or not row.get("tool"):
            continue
        goal = " ".join(str(row.get("goal") or "").split())
        index = row.get("index")
        ts = _row_ts(row)
        restart = isinstance(index, int) and index == 0 and current is not None
        broken = (current is not None and gap_s > 0 and ts and last_ts
                  and (ts - last_ts) > gap_s)
        if current is None or broken or (current["goal"] != goal) or restart:
            current = {"source": source, "goal": goal, "steps": [],
                       "instance": str(row.get("instance") or "default")}
            out.append(current)
        current["steps"].append(row)
        if ts:
            last_ts = ts
    return [segment for segment in out if segment["steps"]]



def _context_of(obs: Any) -> Optional[Dict[str, Any]]:
    """The parts of the observation that decide *what else was possible*.

    The 42-vector keeps the objective, not the choice set. Without the ready list,
    the exits, the groups and the elements, no offline question about alternatives
    can be asked at all - measured 2026-10-29, the derived rows could not say
    whether a second legal action existed. Bounded on purpose (<=8 of each), and
    ids only: this is a decision record, not a copy of the world.
    """
    if not isinstance(obs, dict):
        return None
    block = obs.get("map") if isinstance(obs.get("map"), dict) else {}
    pos = obs.get("position") if isinstance(obs.get("position"), dict) else {}
    def _int(value: Any) -> Optional[int]:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    map_id = _int(block.get("map_id") or obs.get("map_id") or pos.get("map_id"))
    cell = _int(block.get("cell") or obs.get("cell") or pos.get("cell"))
    ready: List[Dict[str, Any]] = []
    for item in (obs.get("ready_objectives") or [])[:8]:
        if not isinstance(item, dict):
            continue
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        ready.append({
            "quest": _int(item.get("quest_id")),
            "objective": _int(item.get("objective_id")),
            "type": _int(item.get("type_id")),
            "step": _int(item.get("step_id")),
            "params": {str(k): v for k, v in list(params.items())[:3]},
        })
    exits: List[Dict[str, Any]] = []
    for entry in (block.get("exits") or [])[:12]:
        if isinstance(entry, dict):
            exits.append({"map": _int(entry.get("map_id")),
                          "cell": _int(entry.get("cell")),
                          "dir": entry.get("direction"),
                          "blocked": bool(entry.get("blocked"))})
        else:
            exits.append({"map": _int(entry)})
    groups: List[Dict[str, Any]] = []
    for entry in (block.get("groups") or [])[:8]:
        if not isinstance(entry, dict):
            continue
        members = entry.get("members") if isinstance(entry.get("members"), list) else []
        monsters = []
        for member in members[:8]:
            if isinstance(member, dict):
                monsters.append(_int(member.get("monster_id") or member.get("id")))
            else:
                monsters.append(_int(member))
        groups.append({"monsters": monsters,
                       "cell": _int(entry.get("cell"))})
    interactives: List[Dict[str, Any]] = []
    raw_elements = obs.get("interactives")
    if not isinstance(raw_elements, list):
        raw_elements = block.get("interactives")
    for entry in (raw_elements or [])[:12]:
        if isinstance(entry, dict):
            # The page publishes the element's skill instance as `skill_uid` and its
            # enabled list as `enabled_skills`; reading only `skill_id` wrote null for
            # every element. Measured 2026-10-30: the corpus could not say whether the
            # page element 513208 (quest 4262/13948) ever offered a usable skill,
            # because both fields were dropped here while the observation had them.
            skills = entry.get("enabled_skills")
            skills = ([_int(s) for s in skills[:4] if _int(s) is not None]
                      if isinstance(skills, list) else [])
            interactives.append({
                "elem": _int(entry.get("elem_id", entry.get("element_id",
                                                             entry.get("elemId")))),
                "skill": _int(entry.get("skill_uid",
                                        entry.get("skill_id",
                                                  entry.get("skillId")))),
                "skills": skills,
                # The client's element type is the one field that says *what* the
                # object is (a door, a resource, a page). 13948's element 513208 is a
                # midground object (sprite gid 401228) on map 145490432 cell 330, and
                # neither the type nor the enabled skills survived into the corpus.
                "type": _int(entry.get("element_type_id", entry.get("type_id"))),
                "door": bool(entry.get("is_door")),
                "cell": _int(entry.get("cell"))})
        else:
            interactives.append({"elem": _int(entry)})
    who = obs.get("character") if isinstance(obs.get("character"), dict) else {}
    character = {"id": _int(who.get("id")), "level": _int(who.get("level")),
                 "breed": _int(who.get("breed"))}
    return {"map": map_id, "cell": cell, "ready": ready, "exits": exits,
            "groups": groups, "interactives": interactives,
            "character": character,
            "active_quests": len(obs.get("quests_active") or []),
            "in_dialog": bool(obs.get("in_dialog")),
            "tutorial": bool((obs.get("tutorial") or {}).get("active"))
            if isinstance(obs.get("tutorial"), dict) else False}


def _dialog_sig(obs: Any) -> Optional[Any]:
    """A comparable identity for the dialog the observation shows.

    The page publishes `{open:false}` when no dialog is up and the full node
    (`message`, per-reply `reply_id`) when one is. A reply's own promise is that this
    changes; `in_dialog` alone cannot see a node move. Measured 2026-10-29: 119 of 160
    judgeable replies advanced the node (or closed the dialog) while only 25 changed
    quest state, so the old claim scored the dialog's working path as a failure.
    """
    d = obs.get("dialog") if isinstance(obs, dict) else None
    if not isinstance(d, dict):
        return None
    if d.get("open") is False:
        return "closed"
    msg = d.get("message")
    replies = d.get("replies") or []
    ids = tuple(r.get("reply_id") for r in replies if isinstance(r, dict))
    if msg is None and not ids:
        return None
    return ("node", str(msg)[:160], ids)


def _claim_of(tool: str, args: Dict[str, Any], before: Any,
              after: Any) -> Tuple[Optional[str], Optional[bool]]:
    """What this call promised to do, and whether the after-state shows it.

    `moved` means "any watched key changed". Measured 2026-10-29 over 2,848 live
    `quest.advance` calls: 636 moved, but **318 of those changed only `cell`** and
    only 59 touched quest state at all. Training on that target teaches the model
    to recognise walking, and it is why `action_only` sits at 0.72 AUC while the
    quest signal is noise. The claim is the action's own promise, read from its
    arguments and the after-snapshot, and it is the target a success head needs.

    Returns (claim, met). `met` is None when the call makes no observable promise
    (reads) or the state needed to judge it is missing - never guessed.
    """
    if not isinstance(before, dict) or not isinstance(after, dict) or not after:
        return None, None
    b = flatten(before)
    a = flatten(after)

    def differs(key: str) -> bool:
        return b.get(key) != a.get(key)

    if tool == "nav.goto":
        want = args.get("map_id", args.get("map"))
        try:
            want_int: Optional[int] = int(want) if want is not None else None
        except (TypeError, ValueError):
            want_int = None
        if want_int is not None:
            try:
                return "at:%d" % want_int, int(a.get("map_id")) == want_int
            except (TypeError, ValueError):
                return "at:%d" % want_int, False
        if args.get("direction") or args.get("cell"):
            return "step", differs("map_id") or differs("cell")
        return None, None

    if tool == "fight.join":
        return "in_fight", a.get("in_fight") is True and b.get("in_fight") is not True

    if tool == "quest.advance":
        qid = args.get("quest_id")
        active = a.get("quests_active")
        accepted = (isinstance(active, dict) and str(qid) in active
                    and not (isinstance(b.get("quests_active"), dict)
                             and str(qid) in b.get("quests_active")))
        touched = [key for key in PROGRESS_KEYS if differs(key)]
        if args.get("accept"):
            return "accept", bool(accepted or touched)
        if args.get("reply") is not None:
            b_sig, a_sig = _dialog_sig(before), _dialog_sig(after)
            if touched:
                return "quest", True
            if b_sig is None or b_sig == "closed" or a_sig is None:
                # No dialog in the before-state or no node identity to compare:
                # unknown, not a failure.
                return "dialog", None
            return "dialog", a_sig != b_sig
        if args.get("npc_id") is not None:
            # A talk promises a dialog; the reply call is what advances the quest.
            # Measured 2026-10-29: 401 of 1,546 talks issued outside a dialog opened
            # one by the next observation, while 13 changed quest state - so the old
            # claim labelled 58% of the quest corpus a failure by definition.
            opened = b.get("in_dialog") is not True and a.get("in_dialog") is True
            return "dialog", bool(opened or touched)
        return "quest", bool(touched)

    if tool == "tap.interactive":
        touched = [key for key in PROGRESS_KEYS if differs(key)]
        return "effect", bool(touched or a.get("in_dialog") is True
                              or differs("inventory_size"))

    if tool == "inv.equip":
        return "equipped", any(differs(key) for key in a
                               if key.startswith(("inventory", "equipment")))

    if tool == "farm.start":
        return "farm", differs("farm_state") and a.get("farm_state") not in (None, "")

    if tool == "safe.stop":
        return "stopped", differs("farm_state") or differs("auto_fight")

    return None, None


def label_steps(segment: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One labelled transition per step, with the ctx re-derived from the log itself."""
    rows = segment["steps"]
    out: List[Dict[str, Any]] = []
    streak = 0
    last_ok: Optional[bool] = None
    last_tool: Optional[str] = None
    last_note: Optional[str] = None
    carried_after: Optional[Dict[str, Any]] = None
    prev_out: Optional[Dict[str, Any]] = None
    prev_stamp: Optional[float] = None
    # Consecutive misses, from the claim label - not from transport ok. Two keys:
    # per tool (`miss_streak`) and per exact rule (`repeat_streak`, same tool *and*
    # same args). Measured 2026-10-29 on the temporal test fold, nav.goto claim:
    # base met 78%, after one identical request miss 22%, after two 0/18. The old
    # `streak` counted `ok`, and a read answers ok, so it reset constantly.
    miss_by_tool: Dict[str, int] = {}
    miss_by_rule: Dict[str, int] = {}
    miss_by_rule_norm: Dict[str, int] = {}
    for position, row in enumerate(rows):
        before = row.get("observation")
        if not isinstance(before, dict):
            before = carried_after
        # The directive's own snapshot is taken before the client has applied the
        # move. Measured 2026-10-29 over 2,139 nav.goto calls: the snapshot showed
        # the move 5 times, the *next* step's observation showed it 449 times, and
        # 1,685 showed it nowhere. The next observation is the first state the loop
        # actually saw after acting, so it is the after-state; the snapshot is the
        # fallback for the last step of a segment, where nothing follows.
        nxt = rows[position + 1].get("observation") if position + 1 < len(rows) else None
        after = (nxt if isinstance(nxt, dict) and nxt else None) or _after_obs(row) \
            or carried_after
        tool = str(row.get("tool") or "")
        ok = bool(row.get("ok"))
        # Latency is the directive round trip (6 minutes in total over 9,687 rows);
        # the decision cadence is the time between records, and that is what a
        # throughput reward needs. It is charged to the *previous* step - the action
        # that caused the wait - not to the one that follows it. Segments split on a
        # 120 s silence, so a pause between sessions is not an action cost.
        dt_ms: Optional[float] = None
        try:
            stamp = float(row.get("ts"))
        except (TypeError, ValueError):
            stamp = None
        if prev_out is not None and stamp is not None and prev_stamp is not None                 and stamp > prev_stamp:
            prev_out["dt_ms"] = round((stamp - prev_stamp) * 1000.0, 1)
        prev_out, prev_stamp = None, stamp
        error = row.get("error") if isinstance(row.get("error"), str) else None
        refused = _refusal(row)
        if refused and not error:
            error = refused
        ctx = {"streak": streak, "last_ok": last_ok, "last_tool": last_tool,
               "last_note": error or last_note, "obs_kind": _obs_kind(row)}
        obj, quest_level = _objective_of(before or {})
        x = None
        x_source = None
        if isinstance(before, dict) and obj is not None:
            try:
                # flatten() first, exactly as QuestPlanner.choose does: the featuriser reads the
                # flat keys (`map_id`, `inventory_size`, ...), and a live snapshot nests them.
                # Featurising the raw page shape instead produced a state vector the model had
                # never seen, and the first transfer run predicted gains of ~95,000.
                candidate = objective_features(obj, quest_level,
                                               _int(obj.get("step")),
                                               flatten(before), ctx)
                if len(candidate) == FEATURE_COUNT:
                    x = [round(float(v), 6) for v in candidate]
                    x_source = "objective_features"
            except Exception:  # noqa: BLE001 - a missing field must not lose the label
                x = None
        changed = changed_keys(before, after) if (before and after) else []
        args = row.get("args") if isinstance(row.get("args"), dict) else {}
        claim, claim_met = _claim_of(tool, args, before, after)
        if refused:
            # The page answered without sending anything, so any movement in the
            # after-state belongs to some other cause. A refused call is a miss:
            # that is also what the retry counters must see.
            claim_met = False
        rule = tool + "|" + json.dumps(args, sort_keys=True)
        # The same call minus fields that change between retries of one intent
        # (agent.gate.VOLATILE_ARGS). Kept beside the exact key: `repeat_streak` is
        # the rule validated in G48-G59; this one exists so the shadow can price the
        # normalized identity without rewriting the corpus the rule was measured on.
        rule_norm = tool + "|" + json.dumps(
            {k: v for k, v in args.items() if k not in VOLATILE_ARGS}, sort_keys=True)
        progress = sorted(set(changed) & set(PROGRESS_KEYS))
        advance = sorted(set(changed) & set(ADVANCE_KEYS))
        world = sorted(set(changed) & set(WORLD_KEYS))
        if refused:
            # Nothing was sent, so this call cannot own any diff: the state moved
            # on its own or because of someone else. `changed` still records what
            # differed; the causal flags do not.
            progress, advance, world = [], [], []
        flat = flatten(after) if isinstance(after, dict) else {}
        out.append({
            "id": "%s#%d" % (segment_key(segment), position),
            "segment": segment_key(segment),
            "source": segment["source"],
            "goal": segment["goal"],
            "instance": segment["instance"],
            "t": position,
            # Wall clock, not the step index: without it a split can only be a hash
            # of segment ids, so "trained on the past, scored on the future" was not
            # expressible and drift could not be measured at all. `t` stays the
            # position because older readers use it as one.
            "at": stamp,
            "tool": tool,
            "args": row.get("args") if isinstance(row.get("args"), dict) else {},
            "ok": ok,
            "error": error,
            # The page's own refusal, verbatim enough to tell "not sent" apart
            # from "sent and did nothing". Additive: older readers ignore it.
            "refused": refused,
            "latency_ms": row.get("latency_ms"),
            # How old the state behind this decision was. Measured 2026-10-29: 4,153
            # of 4,344 steps acted on a *carried* observation, median 707 ms old,
            # p90 1,769 ms. The model's inputs describe a world up to 3.5 s stale,
            # and the age itself is known at decision time.
            "obs_age_ms": row.get("observation_age_ms"),
            "obs_source": row.get("observation_source"),
            "dt_ms": dt_ms,
            "objective_type": objective_type(obj) if obj else None,
            "was_read": tool in READ_ONLY_TOOLS,
            # The call's own promise, judged against the snapshot it returned.
            # `moved` answers "did anything change"; this answers "did the action
            # do what it said", which is the only target where failure prediction
            # is a real task rather than walking detection.
            "claim": claim,
            "claim_met": claim_met,
            "miss_streak": miss_by_tool.get(tool, 0),
            "repeat_streak": miss_by_rule.get(rule, 0),
            "repeat_streak_norm": miss_by_rule_norm.get(rule_norm, 0),
            # What the live repeat-streak gate would have done at this decision
            # (agent/gate.py). Shadow only: the call was still sent.
            "gate": (row.get("meta") or {}).get("gate")
            if isinstance(row.get("meta"), dict) else None,
            "explored": bool(row.get("explored")),
            # The randomized arm's own paperwork. `p_explore` is the propensity the
            # policy actually used at this state (0 when no alternative existed), so
            # an estimator can use it instead of guessing.
            "arm": (row.get("meta") or {}).get("arm")
            if isinstance(row.get("meta"), dict) else None,
            "p_explore": (row.get("meta") or {}).get("p_explore")
            if isinstance(row.get("meta"), dict) else None,
            "has_alternative": (row.get("meta") or {}).get("has_alternative")
            if isinstance(row.get("meta"), dict) else None,
            "why": (row.get("meta") or {}).get("why")
            if isinstance(row.get("meta"), dict) else None,
            "teacher_tool": (row.get("meta") or {}).get("teacher_tool")
            if isinstance(row.get("meta"), dict) else None,
            "has_pair": bool(before and after),
            "changed": changed,
            "moved": bool(world),
            "progress": bool(progress),
            "progress_keys": progress,
            "advance": bool(advance),
            "advance_keys": advance,
            "moved_keys": world,
            "objectives_completed": flat.get("objectives_completed"),
            "x": x,
            "x_source": x_source,
            "context": _context_of(before),
        })
        prev_out = out[-1]
        streak = streak + 1 if not ok else 0
        if claim_met is True:
            miss_by_tool[tool] = 0
            miss_by_rule[rule] = 0
            miss_by_rule_norm[rule_norm] = 0
        elif claim_met is False:
            miss_by_tool[tool] = miss_by_tool.get(tool, 0) + 1
            miss_by_rule[rule] = miss_by_rule.get(rule, 0) + 1
            miss_by_rule_norm[rule_norm] = miss_by_rule_norm.get(rule_norm, 0) + 1
        last_ok = ok
        last_tool = tool
        if after:
            carried_after = after
    _attribute_lagged(out)
    return out


def _attribute_lagged(out: List[Dict[str, Any]]) -> None:
    """Give a revealed effect to the step that acted, not to the read that saw it.

    Measured 2026-10-29 on 9,687 rows: 173 of the 195 reads labelled `moved` sat
    directly after a `quest.advance`, i.e. the read was credited with the action's
    effect because the server applies it after the directive returns. Training a
    value head on that label would teach "reading moves you". A read cannot change
    the world, so a change first observed in a run of reads belongs to the last
    acting step before the run; the raw per-step diff stays in `moved_observed` /
    `progress_observed` so nothing is lost.
    """
    last_action: Optional[int] = None
    for index, row in enumerate(out):
        row["moved_observed"] = bool(row.get("moved"))
        row["progress_observed"] = bool(row.get("progress"))
        row["advance_observed"] = bool(row.get("advance"))
        if row.get("was_read"):
            if last_action is not None:
                action = out[last_action]
                if row["moved"] and not action["moved"]:
                    action["moved"] = True
                    action["moved_lag"] = index - last_action
                    action["moved_keys"] = sorted(set(action["moved_keys"])
                                                  | set(row["moved_keys"]))
                if row["progress"] and not action["progress"]:
                    action["progress"] = True
                    action["progress_lag"] = index - last_action
                    action["progress_keys"] = sorted(set(action["progress_keys"])
                                                     | set(row["progress_keys"]))
                if row["advance"] and not action["advance"]:
                    action["advance"] = True
                    action["advance_lag"] = index - last_action
                    action["advance_keys"] = sorted(set(action["advance_keys"])
                                                    | set(row["advance_keys"]))
            row["moved"] = False
            row["progress"] = False
            row["advance"] = False
        elif not row.get("refused"):
            # A refused call sends nothing, so it is not a candidate owner for a
            # change a later read reveals (G65).
            last_action = index


def segment_key(segment: Dict[str, Any]) -> str:
    stamp = str(segment["steps"][0].get("iso") or segment["steps"][0].get("ts") or "n")
    digest = hashlib.sha1(("%s|%s|%s|%s" % (segment["source"], segment["instance"],
                                            segment["goal"], stamp)).encode("utf-8"))
    return digest.hexdigest()[:16]


def split_of(key: str) -> str:
    value = int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:4], 16) % 1000
    if value < SPLIT_BUCKETS[0]:
        return "train"
    if value < SPLIT_BUCKETS[0] + SPLIT_BUCKETS[1]:
        return "val"
    return "test"


def plan_splits(rows: List[Dict[str, Any]], policy: str = "hash") -> Dict[str, str]:
    """Assign whole segments to splits.

    `hash` balances *labelled actions* across folds but lets the same day, the same
    character and the same stretch of play land in train and test, which is how a
    model can look calibrated and still fail the next session. `temporal` keeps the
    same proportions in time order instead: the earliest segments are train, the
    latest are the fold a live run would actually face. Segments without a
    timestamp go to train -- they cannot be placed in time, and a test fold must be
    a pure future sample.
    """
    counts: Counter = Counter()
    for row in rows:
        if row.get("has_pair") and not row.get("was_read"):
            counts[row["segment"]] += 1
    total = sum(counts.values())
    if not total:
        return {row["segment"]: "train" for row in rows}
    if policy == "temporal":
        starts: Dict[str, float] = {}
        for row in rows:
            at = row.get("at")
            if isinstance(at, (int, float)) and at > 0:
                key = row["segment"]
                starts[key] = min(starts.get(key, float("inf")), float(at))
        assign: Dict[str, str] = {}
        ordered = sorted(starts, key=lambda key: (starts[key], key))
        # Cut by start order, not by an action share: a share leaves the future fold
        # empty whenever the last segment is big (three segments, 70/30/100 actions:
        # every cut falls inside the last one), and the point is a fold that began
        # after the training window ended. A segment that began before the cut stays
        # in train even if it ran past it.
        if len(ordered) >= 3:
            share = SPLIT_BUCKETS[2] / 1000.0
            n = len(ordered)
            n_test = max(1, int(round(share * n)))
            n_val = max(1, int(round(share * n)))
            if n_test + n_val >= n:
                n_val = max(0, n - n_test - 1)
            test_keys = set(ordered[n - n_test:])
            val_keys = set(ordered[n - n_test - n_val:n - n_test])
            for row in rows:
                key = row["segment"]
                if key in test_keys and key in starts:
                    assign[key] = "test"
                elif key in val_keys and key in starts:
                    assign[key] = "val"
                else:
                    assign[key] = "train"
        else:
            for row in rows:
                assign.setdefault(row["segment"], "train")
        for row in rows:
            assign.setdefault(row["segment"], "train")
        return assign
    target = {"train": SPLIT_BUCKETS[0] / 1000.0,
              "val": SPLIT_BUCKETS[1] / 1000.0,
              "test": SPLIT_BUCKETS[2] / 1000.0}
    held = {"train": 0, "val": 0, "test": 0}
    assign = {}
    # biggest segments first, so the greedy fill cannot strand a fold
    for key, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        best = min(target, key=lambda name: ((held[name] + count) / total - target[name],
                                             name))
        assign[key] = best
        held[best] += count
    for row in rows:
        assign.setdefault(row["segment"], "train")
    return assign


def build(paths: Sequence, gap_s: float) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    transitions: List[Dict[str, Any]] = []
    seen: set = set()
    for path in paths:
        path = Path(path)
        rows: List[Dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except ValueError:
                    continue
                if isinstance(parsed, dict):
                    rows.append(parsed)
        for segment in segments(rows, path.name, gap_s):
            labelled = label_steps(segment)
            for row in labelled:
                if row["id"] in seen:
                    continue
                seen.add(row["id"])
                transitions.append(row)
    stats = summarise(transitions)
    return transitions, stats


def _pick(row: Dict[str, Any], path: Sequence[str]) -> Any:
    node: Any = row
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _day(stamp: Any) -> Optional[str]:
    if not isinstance(stamp, (int, float)) or stamp <= 0:
        return None
    return time.strftime("%Y-%m-%d", time.gmtime(stamp))


def _census(transitions: List[Dict[str, Any]], path: Sequence[str],
            day: bool = False) -> Dict[str, int]:
    counts = Counter()
    for row in transitions:
        value = _day(row.get("at")) if day else _pick(row, path)
        if value is None:
            continue
        counts[str(value)] += 1
    return dict(sorted(counts.items()))


def _span_days(transitions: List[Dict[str, Any]]) -> Optional[int]:
    stamps = [r["at"] for r in transitions if isinstance(r.get("at"), (int, float))
              and r["at"] > 0]
    if not stamps:
        return None
    return int((max(stamps) - min(stamps)) // 86400) + 1


def nav_form(actions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """`nav.goto` success by the form of the call, because the forms differ 8x.

    Measured 2026-10-29: a direction hop moved the character on 187 of 1,804 calls
    (10.4%; left 9%, right 13%, top 19%, bottom 45%), while a map-targeted call
    moved on 266 of 338 (78.7%). The planner picks the failing form almost every
    time, and travel is 62% of its action time. A rebuild should say so out loud
    rather than bury it in one nav.goto average.
    """
    out: Dict[str, Counter] = {}
    for row in actions:
        if row.get("tool") != "nav.goto":
            continue
        args = row.get("args") or {}
        if args.get("cell") is not None:
            kind = "cell"
        elif args.get("direction"):
            kind = "direction"
        elif args.get("map_id") is not None:
            kind = "map"
        else:
            kind = "other"
        cell = out.setdefault(kind, Counter())
        cell["n"] += 1
        cell["moved"] += 1 if row.get("moved") else 0
        cell["failed"] += 0 if row.get("ok") else 1
    return {kind: {"n": int(c["n"]), "moved": int(c["moved"]),
                   "moved_pct": round(100.0 * c["moved"] / max(1, c["n"]), 1),
                   "failed_pct": round(100.0 * c["failed"] / max(1, c["n"]), 1)}
            for kind, c in sorted(out.items(), key=lambda kv: -kv[1]["n"])}


def summarise(transitions: List[Dict[str, Any]]) -> Dict[str, Any]:
    usable = [r for r in transitions if r["has_pair"]]
    actions = [r for r in usable if not r["was_read"]]
    per_tool: Dict[str, Counter] = {}
    for row in actions:
        bucket = per_tool.setdefault(row["tool"], Counter())
        bucket["n"] += 1
        bucket["moved"] += 1 if row["moved"] else 0
        bucket["progress"] += 1 if row["progress"] else 0
        bucket["advance"] += 1 if row.get("advance") else 0
        if row.get("claim_met") is not None:
            bucket["claim_n"] += 1
            bucket["claim_met"] += 1 if row["claim_met"] else 0
        if isinstance(row.get("dt_ms"), (int, float)) and row["dt_ms"] > 0:
            bucket["time"] += row["dt_ms"]
        bucket["failed"] += 0 if row["ok"] else 1
    moved = sum(1 for r in actions if r["moved"])
    timed = [r for r in actions if isinstance(r.get("dt_ms"), (int, float)) and r["dt_ms"] > 0]
    hours = sum(r["dt_ms"] for r in timed) / 3600000.0
    stamps = sorted(r["dt_ms"] for r in timed)
    def per_hour(rows_subset, key):
        t_h = sum(r["dt_ms"] for r in rows_subset) / 3600000.0
        hits = sum(1 for r in rows_subset if r.get(key))
        return round(hits / t_h, 2) if t_h > 0 else None
    return {
        "rows": len(transitions),
        "label_version": 3,
        "label_rule": ("a change observed in a run of reads is credited to the last "
                       "acting step; reads never claim a change; a directive the page "
                       "refused before sending is always a miss (G65)"),
        "reads": sum(1 for r in usable if r["was_read"]),
        "reads_that_saw_a_change": sum(1 for r in usable if r["was_read"]
                                       and r.get("moved_observed")),
        "actions_moved_by_lag": sum(1 for r in actions if r.get("moved_lag")),
        "actions_progress_by_lag": sum(1 for r in actions if r.get("progress_lag")),
        "with_observation_pair": len(usable),
        "segments": len({r["segment"] for r in transitions}),
        "split_rows": {},
        "actions": len(actions),
        "actions_moved": moved,
        "action_move_rate": round(100.0 * moved / max(1, len(actions)), 2),
        "hours": round(hours, 3),
        "mean_step_s": round(hours * 3600.0 / max(1, len(timed)), 2) if timed else None,
        "median_step_s": round(stamps[len(stamps) // 2] / 1000.0, 2) if stamps else None,
        "progress_per_hour": per_hour(timed, "progress"),
        "advance_per_hour": per_hour(timed, "advance"),
        "moved_per_hour": per_hour(timed, "moved"),
        "actions_progress": sum(1 for r in actions if r["progress"]),
        "actions_advance": sum(1 for r in actions if r["advance"]),
        "explored_actions": sum(1 for r in actions if r.get("explored")),
        "randomized_actions": sum(1 for r in actions
                                  if isinstance(r.get("p_explore"), float)
                                  and r["p_explore"] > 0),
        "actions_advance_by_lag": sum(1 for r in actions if r.get("advance_lag")),
        "per_tool": {tool: dict(
                         {
                             "n": int(c["n"]),
                             "moved_pct": round(100.0 * c["moved"] / max(1, c["n"]), 1),
                             "progress_pct": round(100.0 * c["progress"] / max(1, c["n"]), 1),
                             "advance_pct": round(100.0 * c["advance"] / max(1, c["n"]), 1),
                             "failed_pct": round(100.0 * c["failed"] / max(1, c["n"]), 1),
                             "claim_n": int(c["claim_n"]),
                             "claim_pct": (round(100.0 * c["claim_met"] / c["claim_n"], 1)
                                           if c["claim_n"] else None),
                         },
                         **({"time_pct": round(100.0 * c["time"] / max(1.0, hours * 3600000.0), 1),
                             "progress_per_hour": round(c["progress"] / (c["time"] / 3600000.0), 2),
                             "advance_per_hour": round(c["advance"] / (c["time"] / 3600000.0), 2)}
                            if c["time"] > 0 and hours > 0 else {}))
                     for tool, c in sorted(per_tool.items(),
                                           key=lambda kv: -kv[1]["n"])},
        "x_coverage_pct": round(100.0 * sum(1 for r in usable if r["x"]) / max(1, len(usable)), 1),
        "context_rows": sum(1 for r in usable if r.get("context")),
        "ready_multi_rows": sum(1 for r in usable
                                if len((r.get("context") or {}).get("ready") or []) > 1),
        "objective_types": dict(Counter(str(r["objective_type"]) for r in usable).most_common(12)),
        "nav_form": nav_form(actions),
        "claims_derivable": sum(1 for r in actions if r.get("claim_met") is not None),
        "claims_met": sum(1 for r in actions if r.get("claim_met")),
        "claim_rule": ("the action's own promise, read from its args and the snapshot it "
                       "returned: nav.goto at:<map> or a step, fight.join in_fight, "
                       "quest.advance a talk opens a dialog / a reply moves the "
                       "dialog to another node or changes quest state / accept activates the "
                       "quest, tap.interactive an effect, "
                       "inv.equip an inventory change; reads have no claim"),
        # Who and when, because a split can only be honest if the corpus can say
        # which character and which day a row came from. Measured 2026-10-29: the
        # rows carried neither, so train and test shared characters and days.
        "characters": _census(transitions, ("context", "character", "id")),
        "levels": _census(transitions, ("context", "character", "level")),
        "days": _census(transitions, ("at",), day=True),
        "instance_days": len({(r.get("instance"), _day(r.get("at"))) for r in transitions
                              if _day(r.get("at"))}),
        "span_days": _span_days(transitions),
        "split_policy": "hash",
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", nargs="*", default=None,
                    help="JSONL files; default: trajectories/*.jsonl")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "transitions")
    ap.add_argument("--segment-gap-s", type=float, default=120.0,
                    help="a longer silence starts a new segment (0 disables)")
    ap.add_argument("--split", choices=("hash", "temporal"), default="hash",
                    help="hash balances actions across folds; temporal holds out the "
                         "latest segments as test (the fold a live run faces next)")
    ap.add_argument("--json", action="store_true", help="print the manifest as JSON")
    args = ap.parse_args(argv)

    paths = [Path(p) for p in (args.log or sorted((REPO_ROOT / "trajectories").glob("*.jsonl")))]
    missing = [p for p in paths if not p.is_file()]
    if missing:
        print("no such log file: %s" % ", ".join(str(m) for m in missing), file=sys.stderr)
        return 2
    if not paths:
        print("no logs to read", file=sys.stderr)
        return 2

    transitions, stats = build(paths, args.segment_gap_s)
    plan = plan_splits(transitions, args.split)
    stats["split_policy"] = args.split
    stats["split_rows"] = dict(Counter(plan[row["segment"]] for row in transitions))
    # Per fold, because the whole-corpus form comparison is confounded by period:
    # measured 2026-10-29, direction hops moved 17% in train and 82% in test, and
    # map-targeted calls 0% (n=12) in train and 85% in test. The aggregate "map
    # beats direction" is a Simpson's paradox - map calls are concentrated in the
    # period where movement worked at all.
    stats["nav_form_by_split"] = {
        name: nav_form([row for row in transitions
                        if plan[row["segment"]] == name
                        and row.get("has_pair") and not row.get("was_read")])
        for name in ("train", "val", "test")}
    stats["split_actions"] = dict(Counter(
        plan[row["segment"]] for row in transitions
        if row["has_pair"] and not row["was_read"]))
    args.out.mkdir(parents=True, exist_ok=True)
    sinks = {}
    try:
        for name in ("train", "val", "test"):
            sinks[name] = (args.out / ("%s.jsonl" % name)).open("w", encoding="utf-8")
        for row in transitions:
            sinks[plan[row["segment"]]].write(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    finally:
        for handle in sinks.values():
            handle.close()
    (args.out / "manifest.json").write_text(json.dumps(stats, indent=1, sort_keys=True),
                                            encoding="utf-8")
    if args.json:
        print(json.dumps(stats, indent=1, sort_keys=True))
    else:
        print("transitions %d (observation pairs %d, segments %d) -> %s"
              % (stats["rows"], stats["with_observation_pair"], stats["segments"], args.out))
        print("split %s  features on %.1f%% of pairs" % (stats["split_rows"],
                                                         stats["x_coverage_pct"]))
        print("actions %d  moved %d (%.1f%%)  progress %d  reads excluded %d"
              % (stats["actions"], stats["actions_moved"], stats["action_move_rate"],
                 stats["actions_progress"], stats["reads"]))
        for tool, cell in list(stats["per_tool"].items())[:8]:
            print("  %-16s n=%-6d moved %5.1f%%  progress %5.1f%%  failed %5.1f%%"
                  % (tool, cell["n"], cell["moved_pct"], cell["progress_pct"],
                     cell["failed_pct"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
