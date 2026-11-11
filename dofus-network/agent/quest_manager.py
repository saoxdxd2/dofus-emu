"""Quest working-set: keep hundreds of stacked quests plannable.

The problem this solves
-----------------------
After the tutorial the client reports every accepted quest as active at once --
`quests_active` is a ``{quest_id: status}`` dict that grows into the hundreds or
thousands. The planner re-scans the whole set every step, so with N quests the
per-step cost is O(N) and the run thrashes on whichever quest the scan happens to
reach, while the ones worth doing now are lost in the noise.

The management rule is not "do them all" -- it is a bounded, prioritised working
set plus a backlog that re-enters when its situation changes:

    score  = readiness (claimable now)
           + level fit (in-band beats too-high / too-low)
           + locality  (a start position on this map)
           + progress  (further-along quests are cheaper to finish)
           - unknown   (a quest the catalogue does not describe)

`apply()` trims the observation's ``quests_active`` to the top ``limit`` quests
(plus the quest the run is mid-objective on, so it never loses its place) and
records the backlog size. Nothing here decides which tool to use; it only decides
*which quests are worth planning this step*. Off by default under `limit` quests
so a small log is unchanged; at scale it holds the per-step set constant.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

# Only kick in once the stack is genuinely large, so early play is untouched.
WORKING_SET = int(os.environ.get("DOFUS_QUEST_WORKING_SET", "32"))


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _map_id(observation: Dict[str, Any]) -> Optional[int]:
    block = observation.get("map") if isinstance(observation.get("map"), dict) else {}
    for value in (observation.get("map_id"), block.get("map_id"),
                  (observation.get("position") or {}).get("map_id")
                  if isinstance(observation.get("position"), dict) else None):
        got = _as_int(value)
        if got is not None:
            return got
    return None


def _level(observation: Dict[str, Any]) -> Optional[int]:
    lv = observation.get("level")
    if lv is None:
        char = observation.get("character")
        if isinstance(char, dict):
            lv = char.get("level")
    return _as_int(lv)


def _active_ids(observation: Dict[str, Any]) -> List[int]:
    qa = observation.get("quests_active")
    ids: List[int] = []
    if isinstance(qa, dict):
        for key in qa:
            got = _as_int(key)
            if got is not None:
                ids.append(got)
    elif isinstance(qa, list):
        for item in qa:
            got = _as_int(item.get("id") if isinstance(item, dict) else item)
            if got is not None:
                ids.append(got)
    return ids


def _status_of(observation: Dict[str, Any], qid: int) -> Optional[int]:
    qa = observation.get("quests_active")
    if isinstance(qa, dict):
        return _as_int(qa.get(str(qid), qa.get(qid)))
    return None


def _catalog(observation: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = {}
    cat = observation.get("quests_catalog")
    if isinstance(cat, list):
        for quest in cat:
            if isinstance(quest, dict):
                qid = _as_int(quest.get("id"))
                if qid is not None:
                    out[qid] = quest
    return out


def _ready_ids(observation: Dict[str, Any]) -> set:
    out = set()
    for entry in observation.get("ready_objectives") or []:
        if isinstance(entry, dict):
            qid = _as_int(entry.get("quest_id"))
            if qid is not None:
                out.add(qid)
    return out


def score_quest(qid: int, status: Optional[int], record: Optional[Dict[str, Any]],
                here: Optional[int], level: Optional[int],
                ready: bool) -> float:
    """Higher is more worth planning this step. Explainable, no hidden state."""
    s = 0.0
    if ready:
        s += 100.0                       # claimable now: free progress
    if isinstance(record, dict):
        lo = _as_int(record.get("level_min"))
        hi = _as_int(record.get("level_max"))
        if level is not None:
            if lo is not None and level < lo:
                s -= 15.0                # too high to do usefully
            elif hi is not None and level > hi:
                s += 5.0                 # trivial but doable
            else:
                s += 20.0                # in band
        starts = record.get("start_position")
        if isinstance(starts, list) and here is not None:
            for sp in starts:
                if isinstance(sp, dict) and _as_int(sp.get("mapId")) == here:
                    s += 30.0            # it starts where we already stand
                    break
        if not record.get("steps"):
            s -= 5.0
    else:
        s -= 5.0                         # the catalogue does not describe it
    if status is not None:
        if status >= 2:
            s += 25.0                    # tracker says it is nearly done
        elif status == 1:
            s += 5.0
    return s


def select(observation: Dict[str, Any], level: Optional[int] = None,
           here: Optional[int] = None,
           limit: int = WORKING_SET
           ) -> Tuple[List[int], List[int], List[Tuple[int, float]]]:
    """(working ids, backlog ids, all scored) for the current observation."""
    obs = observation if isinstance(observation, dict) else {}
    ids = _active_ids(obs)
    if not ids:
        return [], [], []
    recs = _catalog(obs)
    ready = _ready_ids(obs)
    lvl = level if level is not None else _level(obs)
    hmap = here if here is not None else _map_id(obs)
    scored = [(qid, score_quest(qid, _status_of(obs, qid), recs.get(qid),
                                hmap, lvl, qid in ready))
              for qid in ids]
    scored.sort(key=lambda t: (-t[1], t[0]))
    working = [qid for qid, _ in scored[:max(1, limit)]]
    cur = obs.get("current_objective")
    cur_q = _as_int(cur.get("quest_id")) if isinstance(cur, dict) else None
    if cur_q is not None and cur_q not in working:
        working.append(cur_q)            # never drop the quest we are on mid-step
    kept = set(working)
    backlog = [qid for qid, _ in scored if qid not in kept]
    return working, backlog, scored


def apply(observation: Dict[str, Any], level: Optional[int] = None,
          here: Optional[int] = None, limit: int = WORKING_SET
          ) -> Dict[str, Any]:
    """Trim `quests_active` to the working set; record the backlog. No-op when small."""
    obs = observation if isinstance(observation, dict) else {}
    qa = obs.get("quests_active")
    if not isinstance(qa, dict) or len(qa) <= limit:
        return observation
    working, backlog, _ = select(obs, level=level, here=here, limit=limit)
    ws = set(working)
    trimmed = {k: v for k, v in qa.items() if _as_int(k) in ws}
    out = dict(obs)
    out["quests_active"] = trimmed
    out["quest_working_set"] = {
        "working": len(trimmed), "backlog": len(qa) - len(trimmed),
        "total": len(qa),
    }
    return out


def describe(observation: Dict[str, Any]) -> Optional[str]:
    """One line the model/planner can read about the stack, when it is large."""
    obs = observation if isinstance(observation, dict) else {}
    info = obs.get("quest_working_set")
    if isinstance(info, dict) and info.get("backlog"):
        return ("quest stack: %d active, focusing on %d, %d in backlog"
                % (info.get("total"), info.get("working"), info.get("backlog")))
    return None


def self_check() -> None:
    # a small stack is untouched
    small = {"quests_active": {"1": 1, "2": 1}, "level": 30}
    assert apply(small, limit=32) is small, "small stacks must be left alone"
    # a big stack is trimmed to the limit, and the in-band / on-map / ready win
    obs = {
        "map_id": 145490435, "level": 30,
        "quests_active": {str(i): 1 for i in range(1, 101)},
        "quests_catalog": [
            {"id": 5, "level_min": 1, "level_max": 10, "steps": [{"id": 1}],
             "start_position": [{"mapId": 145490435}]},   # in band + on map
            {"id": 6, "level_min": 200, "level_max": 200, "steps": [{"id": 1}]},  # too high
            {"id": 7, "level_min": 1, "level_max": 200, "steps": [{"id": 1}]},    # in band
        ],
        "ready_objectives": [{"quest_id": 9}],
        "current_objective": {"quest_id": 77},
    }
    working, backlog, scored = select(obs, limit=5)
    assert 9 in working, "a claimable quest must be in the working set"
    assert 5 in working, "an on-map in-band quest must be in the working set"
    assert 6 not in working, "an out-of-level quest must not crowd the set"
    assert 77 in working, "the quest mid-objective must never be dropped"
    assert len(working) <= 6, len(working)
    assert len(working) + len(backlog) == 100, (len(working), len(backlog))
    trimmed = apply(obs, limit=5)
    assert len(trimmed["quests_active"]) == len(working), len(trimmed["quests_active"])
    assert trimmed["quest_working_set"]["backlog"] == 100 - len(working)
    assert describe(trimmed) and "backlog" in describe(trimmed)


if __name__ == "__main__":
    self_check()
    print("quest_manager self_check: PASS")