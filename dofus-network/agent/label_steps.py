"""Turn recorded live steps into supervised state -> action -> outcome rows.

The trajectory log records what the agent saw and called, but its top-level ``ok``
is the *directive's* transport success. A ``quest.advance`` that the game rejected
("no NPC on this map") is logged ``ok: true``; the verifier then sees no state
change and calls it a noop. That single mismatch hid the live bottleneck: 70 of
264 recorded calls were game-level failures wearing a success flag.

This module recovers the game-level result, pairs every call with the state that
followed it, and writes one row per step containing: the observation the decision
was made against, the tool and args, the after-state, a rule verdict, the
extracted outcome, state deltas and quest-progress flags.

Nothing is filtered out here. Each row carries a ``label`` that says how much it
can be trusted, so consumers can choose: ``verified + advanced`` rows for SFT,
``game_failed`` rows for repair training, query rows for state encoders.

Usage:
    python -m agent.label_steps                    # trajectories/*.jsonl
    python -m agent.label_steps --input live.jsonl --out data/labeled/live.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from agent.autopilot import SETTLE_TOOLS
from agent.verifier import READ_ONLY_TOOLS, Verifier, changed_keys
from planning.outcome import extract

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = REPO_ROOT / "trajectories"
DEFAULT_OUT = REPO_ROOT / "data" / "labeled" / "steps.jsonl"
DEFAULT_SUMMARY = REPO_ROOT / "data" / "labeled" / "summary.json"

MAX_INVENTORY = 120
QUEST_FIELDS = ("current_objective", "ready_objectives", "objectives_completed",
                "quests_active", "step_objectives")


def _inner(result: Any) -> Tuple[Any, Optional[bool], Optional[str]]:
    """The game-level payload, ok flag and error hidden inside a directive result.

    Shape written by the bridge:
        {"response": {"ok": true, "result": {"ok": false, "error": "...", ...}}}
    When the envelope is absent, the result itself is the payload and its ok is
    unknown (None), so the caller keeps the transport flag.
    """
    if not isinstance(result, dict):
        return result, None, None
    response = result.get("response")
    if not isinstance(response, dict):
        return result, None, None
    inner = response.get("result")
    if not isinstance(inner, dict):
        return result, None, None
    ok = inner.get("ok")
    return inner, (bool(ok) if ok is not None else None), inner.get("error")


def _response_snapshot(result: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(result, dict):
        return None
    response = result.get("response")
    if not isinstance(response, dict):
        return None
    snap = response.get("snapshot")
    return snap if isinstance(snap, dict) else None


def _snapshot_from_query(tool: str, payload: Any) -> Optional[Dict[str, Any]]:
    """A state.snapshot call returns the after-state in its own data field."""
    if tool != "state.snapshot" or not isinstance(payload, dict):
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else None


def _own_after(step: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The state a step's own reply reports immediately after it ran."""
    payload, _, _ = _inner(step.get("result"))
    return _snapshot_from_query(str(step.get("tool") or ""), payload) \
        or _response_snapshot(step.get("result"))


def compact_observation(obs: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A snapshot with the two shapes that blew up the log folded back to numbers.

    Older recordings stored a whole ObjectItem as the inventory quantity and keyed
    gid by object UID. Both are normalised here so a dataset built from them has
    the same inventory shape as one built after the client fix.
    """
    if not isinstance(obs, dict):
        return None
    out = dict(obs)
    inv = out.get("inventory")
    if isinstance(inv, dict):
        items: List[Dict[str, Any]] = []
        for item in inv.get("items") or []:
            if not isinstance(item, dict):
                continue
            gid = item.get("objectGID", item.get("gid"))
            qty = item.get("quantity", item.get("qty"))
            if isinstance(qty, dict):
                qty = qty.get("quantity", 1)
            try:
                qty = float(qty)
                if qty.is_integer():
                    qty = int(qty)
            except (TypeError, ValueError):
                qty = 1
            if gid is None:
                continue
            items.append({"gid": gid, "qty": qty})
            if len(items) >= MAX_INVENTORY:
                break
        out["inventory"] = {"kamas": inv.get("kamas"), "items": items}
        if len(inv.get("items") or []) > MAX_INVENTORY:
            out["inventory"]["items_truncated"] = True
    return out


def _quest_view(obs: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    obs = obs or {}
    cur = obs.get("current_objective") if isinstance(obs.get("current_objective"), dict) else {}
    ready = obs.get("ready_objectives") or []
    ready_ids = {r.get("objective_id") for r in ready if isinstance(r, dict)}
    active = obs.get("quests_active") if isinstance(obs.get("quests_active"), dict) else {}
    return {
        "quest_id": cur.get("quest_id"),
        "objective_id": cur.get("objective_id"),
        "status": cur.get("status"),
        "ready_ids": ready_ids,
        "active_ids": set(active.keys()),
        "completed": obs.get("objectives_completed") or 0,
    }


def _position(obs: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    pos = (obs or {}).get("position") or {}
    return {"map_id": pos.get("map_id"), "cell": pos.get("cell")}


def _hp(obs: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    hp = (obs or {}).get("hp") or {}
    return hp if isinstance(hp, dict) else {}


def _inventory_gids(obs: Optional[Dict[str, Any]]) -> set:
    inv = (obs or {}).get("inventory") or {}
    return {i.get("gid") for i in inv.get("items") or [] if isinstance(i, dict)}


def _delta(before: Optional[Dict[str, Any]],
           after: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Scene, wallet and quest deltas. All optional; absent sides yield no claim."""
    out: Dict[str, Any] = {}
    if not isinstance(before, dict) or not isinstance(after, dict):
        return out

    pb, pa = _position(before), _position(after)
    if pb["map_id"] is not None and pa["map_id"] is not None:
        out["map_changed"] = pb["map_id"] != pa["map_id"]
    if pb["cell"] is not None and pa["cell"] is not None:
        out["cell_moved"] = pb["cell"] != pa["cell"]

    hb, ha = _hp(before), _hp(after)
    if hb.get("life") is not None and ha.get("life") is not None:
        out["hp_delta"] = ha["life"] - hb["life"]

    for key in ("kamas", "xp", "xp_level"):
        b, a = before.get(key), after.get(key)
        if b is not None and a is not None:
            try:
                out[key + "_delta"] = a - b
            except TypeError:
                pass

    for key in ("in_dialog", "in_fight", "in_world"):
        if key in before and key in after and before.get(key) != after.get(key):
            out[key] = [bool(before.get(key)), bool(after.get(key))]

    qb, qa = _quest_view(before), _quest_view(after)
    quest: Dict[str, Any] = {}
    if qb["objective_id"] is not None or qa["objective_id"] is not None:
        quest["objective_changed"] = qb["objective_id"] != qa["objective_id"]
    gained = qa["ready_ids"] - qb["ready_ids"]
    lost = qb["ready_ids"] - qa["ready_ids"]
    if gained:
        quest["ready_gained"] = sorted(g for g in gained if g is not None)
    if lost:
        quest["ready_lost"] = sorted(g for g in lost if g is not None)
    if qa["completed"] != qb["completed"]:
        quest["completed_delta"] = qa["completed"] - qb["completed"]
    closed = qb["active_ids"] - qa["active_ids"]
    if closed:
        quest["quests_closed"] = sorted(str(q) for q in closed)
    quest["objective_claimed"] = bool(
        (qb["status"] == 2 and qa["status"] != 2)
        or (qb["status"] not in (None, 0) and qa["status"] == 0
            and qb["objective_id"] == qa["objective_id"])
        or (qb["objective_id"] is not None and qb["objective_id"] != qa["objective_id"])
        or qa["completed"] > qb["completed"]
    )
    if quest:
        out["quest"] = quest

    gb, ga = _inventory_gids(before), _inventory_gids(after)
    if gb or ga:
        out["inventory_gained"] = sorted(str(g) for g in ga - gb)
        out["inventory_lost"] = sorted(str(g) for g in gb - ga)
        out["inventory_size"] = [len(gb), len(ga)]
    return out


def _trust_label(tool: str, verdict: str, effective_ok: bool,
                 delta: Dict[str, Any]) -> str:
    quest = delta.get("quest") or {}
    if not effective_ok:
        return "game_failed"
    if verdict == "failed":
        return "game_failed"
    if verdict == "blocked":
        return "blocked"
    if tool in READ_ONLY_TOOLS:
        return "query"
    if quest.get("objective_claimed") or quest.get("completed_delta"):
        return "quest_progress"
    if quest.get("objective_changed") or quest.get("ready_gained") \
            or quest.get("quests_closed"):
        return "quest_progress"
    if delta.get("map_changed") or delta.get("cell_moved") \
            or delta.get("hp_delta") or delta.get("in_dialog"):
        return "state_changed"
    return "noop"


def _target(record: Dict[str, Any]) -> Optional[str]:
    result = record.get("result")
    if not isinstance(result, dict):
        return None
    response = result.get("response")
    if not isinstance(response, dict):
        return None
    return response.get("target")


def label_record(record: Dict[str, Any],
                 after_override: Optional[Dict[str, Any]] = None,
                 after_source: str = "",
                 before_override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Label one step record.

    ``after_override`` is a later observation when the response carried none.
    ``before_override`` is the previous step's after-state on the same client. A
    loop that retries a plan without refreshing its observation sends the same
    stale snapshot several times; comparing that snapshot with a fresh response
    credits one real transition to every retry (19 "progress" rows where only a
    handful actually moved anything). The previous after-state is the latest
    world state known before this call, so it is the honest before for the
    action's effect. The observation the policy actually saw is still kept in
    ``observation`` and `before_stale` marks the disagreement.
    """
    result = record.get("result")
    payload, inner_ok, inner_error = _inner(result)
    transport_ok = bool(record.get("ok"))
    effective_ok = transport_ok if inner_ok is None else bool(inner_ok)
    effective_error = inner_error if inner_error is not None else record.get("error")

    observation = compact_observation(record.get("observation"))
    before = compact_observation(before_override) if before_override is not None else observation
    tool = str(record.get("tool") or "")
    query_snap = _snapshot_from_query(tool, payload)
    response_snap = _response_snapshot(result)
    # A tool whose effect lands after it returns (a walk, a dialog, a fight) is
    # answered before that effect exists. Its reply snapshot is therefore the
    # world *before* the action landed, while the next step's observation is the
    # world after it. Prefer the later state when it actually differs: labelling
    # a completed walk `noop` teaches the policy that walking does nothing.
    settled = (tool in SETTLE_TOOLS and after_override is not None
               and before is not None
               and changed_keys(before, compact_observation(after_override)))
    if query_snap:
        after, source = query_snap, "query_data"
    elif settled:
        after, source = after_override, (after_source or "next_observation")
    elif response_snap:
        after, source = response_snap, "response_snapshot"
    elif after_override is not None:
        after, source = after_override, after_source or "next_observation"
    else:
        after, source = None, "missing"
    after = compact_observation(after)

    latency = float(record.get("latency_ms") or 0.0)
    verifier = Verifier()
    if before is None or after is None:
        verdict = "unknown"
        confidence = 0.0
        reasons = ("before or after observation missing",)
        drift = False
    else:
        v = verifier.label(str(record.get("tool") or ""), record.get("args") or {},
                           effective_ok, effective_error, before, after, latency, None)
        verdict, confidence, reasons, drift = v.verdict, v.confidence, v.reasons, v.drift

    outcome = extract(str(record.get("tool") or ""), record.get("args") or {},
                      payload, effective_ok, effective_error, latency)
    delta = _delta(before, after)
    if inner_ok is False:
        # The game answered and said no. That is known even when no observation
        # bracket exists, and repair training needs the rejected call itself.
        trust = "game_failed"
    elif not effective_ok or before is None or after is None:
        # A call that never reached the game says nothing about the action; with
        # no game-level answer, an infrastructure failure is not repair data.
        trust = "unlabelable"
    else:
        trust = _trust_label(str(record.get("tool") or ""), verdict,
                             effective_ok, delta)

    return {
        "ts": record.get("ts"),
        "iso": record.get("iso"),
        "session": record.get("instance"),
        "target": _target(record),
        "goal": record.get("goal"),
        "tool": record.get("tool"),
        "args": record.get("args") or {},
        "transport_ok": transport_ok,
        "game_ok": inner_ok,
        "game_error": effective_error,
        "latency_ms": latency,
        "observation": observation,
        "before": before,
        "before_stale": observation is not None and before is not None
                         and observation != before,
        "after": after,
        "after_source": source,
        "verdict": verdict,
        "verdict_confidence": confidence,
        "verdict_reasons": list(reasons),
        "drift": drift,
        "outcome": outcome.as_dict(),
        "delta": delta,
        "label": trust,
        "changed_keys": changed_keys(before, after) if before and after else [],
        "observation_age_ms": record.get("observation_age_ms"),
        "observation_source": record.get("observation_source"),
    }


def _load_steps(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("kind") == "step":
                rows.append(row)
    return rows


def label_file(path: Path, max_wait_s: float = 120.0) -> List[Dict[str, Any]]:
    steps = _load_steps(path)
    rows: List[Dict[str, Any]] = []
    previous_after: Dict[Optional[str], Dict[str, Any]] = {}
    previous_index: Dict[Optional[str], int] = {}
    for i, step in enumerate(steps):
        target = _target(step)
        after, source = None, "missing"
        for later in steps[i + 1:]:
            # Another client's observation says nothing about this one.
            if _target(later) != target:
                continue
            try:
                dt = float(later.get("ts") or 0.0) - float(step.get("ts") or 0.0)
            except (TypeError, ValueError):
                dt = 0.0
            if dt > max_wait_s:
                break
            if isinstance(later.get("observation"), dict):
                # New logs say when a snapshot was carried without a fresh read.
                if later.get("observation_source") == "stale":
                    continue
                # The loop retries a plan from the snapshot it already holds. A
                # later observation identical to this step's own observation is
                # that same cached snapshot, not a fresh look at the world --
                # pairing it with a newer before would invert time.
                if compact_observation(later["observation"]) \
                        == compact_observation(step.get("observation")):
                    continue
                # A later read's observation is the state the agent held when it
                # issued that read, not necessarily the state after this call. If
                # the read's own reply disagrees, the held snapshot is older than
                # the read itself -- adopting it would rewind past this call and
                # credit it with a transition that already happened (or never
                # did). Only a probe that reports the state it was also given can
                # close this step's interval.
                if str(later.get("tool") or "") == "state.snapshot":
                    own = _own_after(later)
                    if isinstance(own, dict) and changed_keys(
                            later["observation"], compact_observation(own)):
                        continue
                after = later["observation"]
                source = "next_observation"
                break
        before_override = None
        if previous_index.get(target) == i - 1:
            before_override = previous_after.get(target)
        row = label_record(step, after_override=after, after_source=source,
                           before_override=before_override)
        row["session"] = path.stem
        row["source_file"] = path.name
        rows.append(row)
        # Seed the next step's `before` with this call's own reply snapshot --
        # the state immediately after it ran. The *resolved* after may be a
        # later step's observation; using that as the next step's before would
        # hide the next action's effect (its before and after would both be
        # that later state).
        own_after = _own_after(step)
        if own_after is not None:
            previous_after[target] = compact_observation(own_after)
            previous_index[target] = i
        else:
            previous_after.pop(target, None)
            previous_index.pop(target, None)
    return rows


def _transition_signature(row: Dict[str, Any]) -> str:
    before = row.get("before") or {}
    after = row.get("after") or {}
    qb = before.get("current_objective") or {}
    qa = after.get("current_objective") or {}
    return json.dumps({
        "tool": row.get("tool"),
        "args": row.get("args"),
        "before": [qb.get("quest_id"), qb.get("objective_id"), qb.get("status")],
        "after": [qa.get("quest_id"), qa.get("objective_id"), qa.get("status")],
    }, sort_keys=True, ensure_ascii=False)


def mark_transitions(rows: List[Dict[str, Any]]) -> int:
    """Give each progress transition a stable id and flag repeats.

    The same quest claim appears several times in the logs because the server
    re-armed it after a reconnect. That is one transition observed repeatedly,
    not several wins; a learner that counts rows would over-weight it.
    """
    seen: Dict[str, str] = {}
    for row in rows:
        if row.get("label") != "quest_progress":
            continue
        signature = _transition_signature(row)
        digest = hashlib.sha1(signature.encode("utf-8")).hexdigest()[:12]
        row["transition_id"] = digest
        first = seen.get(signature)
        row["repeat_transition"] = first is not None
        if first is not None:
            row["first_transition_id"] = first
        else:
            seen[signature] = digest
    return len(seen)


def summarize(rows: Iterable[Dict[str, Any]], files: List[str],
              unique_transitions: int = 0) -> Dict[str, Any]:
    rows = list(rows)
    labels = Counter(r["label"] for r in rows)
    verdicts = Counter(r["verdict"] for r in rows)
    outcomes = Counter(r["outcome"]["signal"] for r in rows)
    tools = Counter(str(r["tool"]) for r in rows)
    after_sources = Counter(r["after_source"] for r in rows)
    game_failed = Counter(str(r["game_error"])[:120] for r in rows
                          if r["label"] == "game_failed")
    claimed = sum(1 for r in rows
                  if (r["delta"].get("quest") or {}).get("objective_claimed"))
    return {
        "files": files,
        "steps": len(rows),
        "with_before": sum(1 for r in rows if r["observation"] is not None),
        "with_after": sum(1 for r in rows if r["after"] is not None),
        "stale_before": sum(1 for r in rows if r.get("before_stale")),
        "after_sources": dict(after_sources),
        "labels": dict(labels),
        "verdicts": dict(verdicts),
        "outcomes": dict(outcomes),
        "tools": dict(tools),
        "game_failures": dict(game_failed),
        "objective_claimed_rows": claimed,
        "trusted_progress_rows": sum(1 for r in rows if r["label"] == "quest_progress"),
        "unique_progress_transitions": unique_transitions,
        "repeated_progress_rows": sum(1 for r in rows if r.get("repeat_transition")),
        "usable_sft_rows": sum(1 for r in rows if r["label"] == "quest_progress"
                               and r["verdict"] == "advanced"
                               and not r.get("before_stale")
                               and not r.get("repeat_transition")),
        "evidence_rows": sum(1 for r in rows if r["label"] == "game_failed"),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", default=None,
                        help="trajectory JSONL (repeatable); default trajectories/*.jsonl")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--summary", default=str(DEFAULT_SUMMARY))
    parser.add_argument("--max-wait", type=float, default=120.0,
                        help="seconds between a call and a later observation used as after-state")
    args = parser.parse_args(argv)

    paths = [Path(p) for p in args.input] if args.input else sorted(DEFAULT_ROOT.glob("*.jsonl"))
    paths = [p for p in paths if p.exists()]
    if not paths:
        parser.error("no trajectory files found")

    all_rows: List[Dict[str, Any]] = []
    for path in paths:
        all_rows.extend(label_file(path, max_wait_s=args.max_wait))
    unique_transitions = mark_transitions(all_rows)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for row in all_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary = summarize(all_rows, [str(p) for p in paths], unique_transitions)
    summary["output"] = str(out)
    sp = Path(args.summary)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
