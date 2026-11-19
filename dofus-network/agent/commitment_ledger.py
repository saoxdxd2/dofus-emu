"""Commitment-aware credit allocation (CACA) for delayed live outcomes.

The live loop dispatches an action and the server answers later, in a different
order, sometimes twice, and sometimes not at all. The log that recorded the step
could only say "this action returned ok", so a quest accepted 1.2 s after the
click was labelled a no-op (G17) and a retreat-return cycle could earn progress
twice (G61, G83).

This module replaces that one-step attribution with a bounded ledger of
*commitments*. A commitment is what an executed action promised:

    c_i = (id, tau, z_tau, a_tau, g_tau, Phi(s_tau, g_tau), psi_i,
           T_exp, sigma)

* ``tau`` is the dispatch time, ``T_exp = tau + Delta_max(a_tau)`` deliberately
  extends past the loop's settle window;
* ``psi_i`` is a predicate over a server event, derived from
  ``agent.verifier.promise_state`` where that function has an opinion;
* ``Phi`` is the signed telescoping goal potential (Ng, Harada & Russell 1999)
  in ``[-1, +1]``;
* ``sigma`` is PENDING -> CREDITED | EXPIRED | AMBIGUOUS, and only a final
  state may teach (``w_learn``).

Attribution is at-most-once. Incoming events are deduplicated by
``Hash(type, payload, floor(t / delta_dedup))``. Exactly one pending match
credits; two or more matches mark every candidate AMBIGUOUS and teach nothing;
zero matches credit nothing and leave the ledger intact. A commitment is never
failed while ``t <= T_exp``; only an explicit advance past its deadline expires
it, and an expired commitment is a real negative (``-c_noop``).

The ledger holds at most ``max_items`` commitments. Registering a ninth expires
the one closest to its deadline rather than silently dropping it, so the
bounded set is still auditable.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

PENDING = "PENDING"
CREDITED = "CREDITED"
EXPIRED = "EXPIRED"
AMBIGUOUS = "AMBIGUOUS"
FINAL = frozenset({CREDITED, EXPIRED, AMBIGUOUS})

# How long each tool's own promise may take to land. These are deliberately
# longer than any settle window: a walk that is still animating, a dialog the
# server opens after its reply packet, a fight the client starts on the next
# tick. A tool with no entry gets DEFAULT_DELTA_S.
DELTA_MAX_S: Dict[str, float] = {
    "nav.goto": 120.0,
    "quest.advance": 90.0,
    "fight.join": 300.0,
    "farm.start": 600.0,
    "hdv.buy": 180.0,
    "shop.buy": 180.0,
    "hdv.list": 180.0,
    "inv.equip": 60.0,
    "ui.tap": 60.0,
    "ui.icons": 60.0,
    "tap.interactive": 60.0,
}
DEFAULT_DELTA_S = 60.0

# World keys whose movement is evidence that *something* happened. Reads and
# snapshots are excluded: a read answering is not progress.
WORLD_KEYS = frozenset({
    "map_id", "cell", "x", "y", "in_fight", "in_dialog",
    "dialog_message", "dialog_replies", "objective_id", "quest_id",
    "level", "hp", "inventory_size", "kamas",
})

DEFAULT_WEIGHTS = (0.5, 0.3, 0.2)   # step, objective counter, hops
DEFAULT_GAMMA = 0.99
DEFAULT_C_NOOP = 0.05


def _num(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number else default


def _nested(state: Any, *path: str) -> Any:
    current = state
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def goal_potential(state: Any, goal: Optional[Dict[str, Any]] = None,
                   weights: Sequence[float] = DEFAULT_WEIGHTS) -> float:
    """Signed progress potential ``Phi(s, g)`` in ``[-1, +1]``.

        Phi = w_step * step_idx / S_max
            + w_prog * obj_counter / C_max
            - w_hops * min(hops_to_target, H_max) / H_max

    A term whose denominator is unknown is skipped, not imputed: a fabricated
    ``S_max`` would turn "no data" into a shaped reward. With all three terms
    present the value is bounded because the weights sum to one.
    """
    if not isinstance(state, dict):
        return 0.0
    goal = goal if isinstance(goal, dict) else {}
    w_step, w_prog, w_hops = (list(weights) + [0.0, 0.0, 0.0])[:3]

    step = _num(state.get("step_idx"),
                _num(_nested(state, "current_objective", "step"), 0.0))
    s_max = _num(goal.get("S_max"), _num(state.get("S_max"), 0.0))
    progress = _num(state.get("obj_counter"),
                    _num(state.get("objectives_completed"), 0.0))
    c_max = _num(goal.get("C_max"), _num(state.get("C_max"), 0.0))
    hops = _num(state.get("hops_to_target"), _num(state.get("hops"), None))
    h_max = _num(goal.get("H_max"), _num(state.get("H_max"), 1.0))

    phi = 0.0
    if s_max and s_max > 0:
        phi += w_step * (step or 0.0) / s_max
    if c_max and c_max > 0:
        phi += w_prog * (progress or 0.0) / c_max
    if hops is not None and h_max and h_max > 0:
        phi -= w_hops * min(max(0.0, hops), h_max) / h_max
    return max(-1.0, min(1.0, phi))


def goal_identity(goal: Any, observation: Any = None,
                  args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The stable identity of the objective an action was aimed at."""
    out: Dict[str, Any] = {}
    args = args if isinstance(args, dict) else {}
    for key in ("quest_id", "item_id", "map_id", "monster_id"):
        if args.get(key) is not None:
            out[key] = args[key]
    if isinstance(observation, dict):
        current = observation.get("current_objective")
        if isinstance(current, dict):
            if current.get("quest_id") is not None:
                out.setdefault("quest_id", current["quest_id"])
            if current.get("objective_id") is not None:
                out.setdefault("objective_id", current["objective_id"])
    if isinstance(goal, str) and goal.strip():
        out["text"] = goal.strip()[:120]
    return out


def _canonical(payload: Any) -> str:
    try:
        return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                          default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(payload)


def event_uid(kind: str, payload: Any, ts: float, dedup_s: float) -> str:
    """Deduplication key: same facts in the same time bucket are one event."""
    bucket = int(ts / max(1e-6, float(dedup_s)))
    return hashlib.sha1(("%s|%s|%d" % (kind, _canonical(payload), bucket))
                        .encode("utf-8")).hexdigest()[:24]


@dataclass
class Commitment:
    id: str
    tool: str
    args: Dict[str, Any]
    tau: float
    delta_max: float
    seq: int = 0
    goal: Dict[str, Any] = field(default_factory=dict)
    phi: float = 0.0
    promise: str = ""
    sigma: str = PENDING
    w_learn: bool = True
    resolved_at: Optional[float] = None
    reward: Optional[float] = None
    event_uid: str = ""
    note: str = ""

    @property
    def t_exp(self) -> float:
        return self.tau + self.delta_max

    @property
    def pending(self) -> bool:
        return self.sigma == PENDING

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "tool": self.tool, "args": dict(self.args),
            "tau": round(self.tau, 3), "t_exp": round(self.t_exp, 3),
            "goal": dict(self.goal), "phi": round(self.phi, 4),
            "promise": self.promise, "sigma": self.sigma,
            "w_learn": self.w_learn, "reward": self.reward,
            "event": self.event_uid, "note": self.note,
        }


@dataclass
class Event:
    uid: str
    ts: float
    kind: str
    changed: Tuple[str, ...]
    before: Dict[str, Any]
    after: Dict[str, Any]
    reward: float = 0.0
    steps: int = 0                 # dispatch intervals elapsed, for gamma^dk
    payload: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"uid": self.uid, "ts": round(self.ts, 3), "kind": self.kind,
                "changed": list(self.changed), "reward": self.reward}


@dataclass
class Attribution:
    """What one event did, for the log and for the tests."""

    event: Event
    status: str                    # credited | ambiguous | unclaimed | duplicate
    commitment_id: str = ""
    rewarded: Tuple[str, ...] = ()
    reward: float = 0.0
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"event": self.event.uid, "status": self.status,
                "commitment": self.commitment_id,
                "rewarded": list(self.rewarded),
                "reward": round(self.reward, 4), "note": self.note}


class CommitmentLedger:
    """Bounded PENDING set, at-most-once events, final states only teach."""

    def __init__(self, max_items: int = 8, dedup_s: float = 1.0,
                 gamma: float = DEFAULT_GAMMA, c_noop: float = DEFAULT_C_NOOP,
                 max_consumed: int = 4096) -> None:
        self.max_items = max(1, int(max_items))
        self.dedup_s = max(1e-6, float(dedup_s))
        self.gamma = float(gamma)
        self.c_noop = float(c_noop)
        self.max_consumed = max(64, int(max_consumed))
        self.items: List[Commitment] = []
        self.history: List[Commitment] = []
        self.consumed: Dict[str, float] = {}
        self.unclaimed = 0
        self.duplicates = 0
        self.evicted = 0
        self._seq = 0

    # -- registration --------------------------------------------------------

    def register(self, tool: str, args: Optional[Dict[str, Any]] = None, *,
                 goal: Any = None, state: Any = None,
                 phi: Optional[float] = None,
                 promise: str = "", delta_max: Optional[float] = None,
                 now: Optional[float] = None) -> Commitment:
        """Append one dispatched action. Never blocks on a full ledger silently."""
        now = time.time() if now is None else float(now)
        self.advance(now)
        if len(self.items) >= self.max_items:
            # Make room by finalizing the commitment closest to its deadline.
            # Dropping it instead would erase an attribution the contract says
            # every dispatched action receives.
            victim = min(self.items, key=lambda item: (item.t_exp, item.tau))
            self._expire(victim, now, note="ledger_full")
            self.evicted += 1
        self._seq += 1
        args = dict(args or {})
        identity = goal_identity(goal, state, args)
        value = goal_potential(state, identity) if phi is None else float(phi)
        commitment = Commitment(
            id="c%d" % self._seq,
            tool=str(tool),
            args=args,
            tau=now,
            seq=self._seq,
            delta_max=float(delta_max if delta_max is not None
                            else DELTA_MAX_S.get(tool, DEFAULT_DELTA_S)),
            goal=identity,
            phi=max(-1.0, min(1.0, value)),
            promise=str(promise or ""),
        )
        self.items.append(commitment)
        return commitment

    def _finalize(self, commitment: Commitment, sigma: str, now: float,
                  reward: Optional[float], w_learn: bool, note: str = "") -> None:
        commitment.sigma = sigma
        commitment.resolved_at = now
        commitment.reward = reward
        commitment.w_learn = bool(w_learn) and sigma in (CREDITED, EXPIRED)
        commitment.note = note or commitment.note
        self.items = [item for item in self.items if item is not commitment]
        self.history.append(commitment)
        if len(self.history) > 4096:
            del self.history[:len(self.history) - 4096]

    def _expire(self, commitment: Commitment, now: float, note: str = "") -> None:
        """Expiry is a real negative and is only legal past its own deadline."""
        if commitment.sigma != PENDING:
            return
        self._finalize(commitment, EXPIRED, now, -abs(self.c_noop), True,
                       note=note or "deadline passed")

    def advance(self, now: Optional[float] = None) -> List[Commitment]:
        """Expire every PENDING commitment whose deadline has passed.

        Never expires at ``t <= T_exp``: the comparison is strict, so a server
        event in the deadline instant still has its chance.
        """
        now = time.time() if now is None else float(now)
        expired: List[Commitment] = []
        for commitment in list(self.items):
            if commitment.sigma == PENDING and now > commitment.t_exp:
                self._expire(commitment, now)
                expired.append(commitment)
        return expired

    # -- matching ------------------------------------------------------------

    @staticmethod
    def _world_moved(event: Event) -> bool:
        if event.reward > 0:
            return True
        return bool(set(event.changed) & WORLD_KEYS)

    def promise_met(self, commitment: Commitment, event: Event) -> bool:
        """``psi_i(e_j)``: did this event satisfy what the action promised?"""
        tool = commitment.tool
        if tool == "nav.goto":
            try:
                from agent.verifier import promise_state

                return promise_state(tool, commitment.args, event.before,
                                     event.after) == "met"
            except Exception:  # noqa: BLE001 - a predicate must not break credit
                return False
        if tool == "quest.advance":
            if event.reward > 0:
                return True
            changed = set(event.changed)
            return bool(changed & {"dialog_message", "dialog_replies",
                                   "objective_id", "quest_id", "level"})
        if tool in ("fight.join", "farm.start"):
            for key in ("kills", "drops", "bought", "qty"):
                if _num(event.payload.get(key), 0.0) > 0:
                    return True
            return "in_fight" in event.changed
        return self._world_moved(event)

    def observe(self, event: Event) -> Attribution:
        """Apply one server event to the pending set.
        """
        if event.uid in self.consumed:
            self.duplicates += 1
            return Attribution(event=event, status="duplicate",
                               note="event already consumed")
        self.advance(event.ts)
        matches = [c for c in self.items
                   if c.sigma == PENDING and c.tau <= event.ts <= c.t_exp
                   and self.promise_met(c, event)]
        self.consumed[event.uid] = event.ts
        if len(self.consumed) > self.max_consumed:
            oldest = sorted(self.consumed.items(), key=lambda pair: pair[1])
            for uid, _ in oldest[:len(self.consumed) - self.max_consumed]:
                self.consumed.pop(uid, None)
        if not matches:
            self.unclaimed += 1
            return Attribution(event=event, status="unclaimed",
                               note="no pending commitment promised this")
        if len(matches) > 1:
            # Two candidates: the event cannot say which, and crediting both
            # would double-count one outcome. Abstain from teaching entirely.
            for commitment in matches:
                self._finalize(commitment, AMBIGUOUS, event.ts, None, False,
                               note="collision: %d candidates" % len(matches))
            return Attribution(event=event, status="ambiguous",
                               rewarded=tuple(c.id for c in matches),
                               note="ambiguous attribution")
        commitment = matches[0]
        # Discount by the caller's dispatch-interval count. The ledger cannot
        # infer steps from wall time (the loop's cadence is not a clock), so an
        # event that does not carry the count is undiscounted rather than
        # discounted by a fabricated rate.
        steps = int(event.steps) if event.steps else max(0, self._seq - commitment.seq)
        phi_after = goal_potential(event.after, commitment.goal)
        reward = (float(event.reward)
                  + (self.gamma ** max(0, steps)) * phi_after
                  - commitment.phi)
        self._finalize(commitment, CREDITED, event.ts, reward, True,
                       note="credited by %s" % event.uid)
        return Attribution(event=event, status="credited",
                           commitment_id=commitment.id,
                           rewarded=(commitment.id,), reward=reward)

    def ingest(self, before: Any, after: Any, *, ts: Optional[float] = None,
               verdict: Optional[str] = None, payload: Optional[Dict[str, Any]] = None,
               kind: str = "observation", steps: int = 0) -> Attribution:
        """Build an event from two observations and attribute it.

        ``verdict`` is the loop's own answer for the action that produced the
        transition; ``advanced`` is terminal evidence, every other verdict is
        not. A read's answer never becomes terminal reward.
        """
        now = time.time() if ts is None else float(ts)
        before = before if isinstance(before, dict) else {}
        after = after if isinstance(after, dict) else {}
        try:
            from agent.verifier import changed_keys

            changed = tuple(changed_keys(before, after))
        except Exception:  # noqa: BLE001
            changed = ()
        payload = dict(payload or {})
        reward = 1.0 if verdict == "advanced" else 0.0
        body = {"changed": list(changed),
                "map_id": after.get("map_id"),
                "objective": _nested(after, "current_objective", "objective_id"),
                "quest": _nested(after, "current_objective", "quest_id")}
        body.update({key: payload.get(key) for key in ("kills", "drops", "qty",
                                                       "bought") if key in payload})
        event = Event(uid=event_uid(kind, body, now, self.dedup_s), ts=now,
                      kind=kind, changed=changed, before=before, after=after,
                      reward=reward, steps=max(0, int(steps)), payload=payload)
        return self.observe(event)

    # -- introspection -------------------------------------------------------

    def pending(self, now: Optional[float] = None) -> List[Commitment]:
        now = time.time() if now is None else float(now)
        self.advance(now)
        return [c for c in self.items if c.sigma == PENDING]

    def stats(self) -> Dict[str, Any]:
        by_sigma: Dict[str, int] = {}
        for commitment in self.history:
            by_sigma[commitment.sigma] = by_sigma.get(commitment.sigma, 0) + 1
        return {
            "pending": sum(1 for c in self.items if c.sigma == PENDING),
            "registered": self._seq,
            "finalized": len(self.history),
            "by_sigma": by_sigma,
            "duplicates": self.duplicates,
            "unclaimed": self.unclaimed,
            "evicted": self.evicted,
        }

    def teaching_rows(self) -> List[Dict[str, Any]]:
        """Final rows a learner may train on: w_learn only, never PENDING/AMBIGUOUS."""
        return [c.as_dict() for c in self.history if c.w_learn]
