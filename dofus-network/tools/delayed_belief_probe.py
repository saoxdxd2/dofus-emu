"""Offline fixed-lag belief replay probe with known finite-state dynamics."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_live_transitions import _attribute_lagged


@dataclass(frozen=True)
class Observation:
    epoch: str
    sequence: int
    source_step: Optional[int]
    likelihood: tuple[float, ...]


@dataclass
class _Frame:
    step: int
    transition: np.ndarray
    observation: Optional[Observation] = None
    posterior: np.ndarray = field(default_factory=lambda: np.empty(0))


def _probability_vector(values, size: Optional[int] = None) -> np.ndarray:
    p = np.array(values, dtype=np.float64, copy=True)
    if (p.ndim != 1 or not p.size or (size is not None and p.size != size)
            or not np.isfinite(p).all() or (p < 0).any()
            or not np.isclose(p.sum(), 1.0)):
        raise ValueError("expected a finite normalized probability vector")
    return p / p.sum()


class FixedLagBelief:
    """One immutable source observation per tick; row-stochastic dynamics."""

    def __init__(self, prior, *, epoch: str, capacity: int = 8):
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._base = _probability_vector(prior)
        self.epoch = epoch
        self.capacity = capacity
        self.step = 0
        self.all_received_evidence_used = True
        self._frames = [_Frame(0, np.eye(len(self._base)), posterior=self._base.copy())]

    @property
    def belief(self) -> np.ndarray:
        return self._frames[-1].posterior.copy()

    @property
    def buffered_steps(self) -> tuple[int, ...]:
        return tuple(frame.step for frame in self._frames)

    def advance(self, transition) -> None:
        matrix = np.array(transition, dtype=np.float64, copy=True)
        n = len(self._base)
        if (matrix.shape != (n, n) or not np.isfinite(matrix).all()
                or (matrix < 0).any() or not np.allclose(matrix.sum(axis=1), 1.0)):
            raise ValueError("expected a finite row-stochastic transition matrix")
        matrix /= matrix.sum(axis=1, keepdims=True)
        posterior = self._frames[-1].posterior @ matrix
        self.step += 1
        self._frames.append(_Frame(self.step, matrix, posterior=posterior))
        if len(self._frames) > self.capacity:
            self._base = self._frames.pop(0).posterior.copy()

    def ingest(self, observation: Observation) -> str:
        if observation.epoch != self.epoch:
            return "wrong_epoch"
        if observation.source_step is None:
            self.all_received_evidence_used = False
            return "unknown_source_time"
        if observation.source_step > self.step:
            self.all_received_evidence_used = False
            return "future_source_time"
        if observation.source_step < self._frames[0].step:
            self.all_received_evidence_used = False
            return "outside_window"
        likelihood = np.asarray(observation.likelihood, dtype=np.float64)
        if (likelihood.shape != self._base.shape or not np.isfinite(likelihood).all()
                or (likelihood < 0).any() or (likelihood > 1).any()
                or likelihood.sum() <= 0):
            raise ValueError("invalid observation likelihood")
        for frame in self._frames:
            previous = frame.observation
            if previous is not None and previous.sequence == observation.sequence:
                if previous == observation:
                    return "duplicate"
                self.all_received_evidence_used = False
                return "conflict"
        frame = self._frames[observation.source_step - self._frames[0].step]
        if frame.observation is not None:
            self.all_received_evidence_used = False
            return "conflict"
        frame.observation = observation
        try:
            self._recompute()
        except ValueError:
            frame.observation = None
            self._recompute()
            self.all_received_evidence_used = False
            return "inconsistent_evidence"
        return "accepted"

    def _recompute(self) -> None:
        belief = self._base.copy()
        for frame in self._frames:
            belief = belief @ frame.transition
            if frame.observation is not None:
                belief *= frame.observation.likelihood
                total = float(belief.sum())
                if total <= 0:
                    raise ValueError("observation has zero model evidence")
                belief /= total
            frame.posterior = belief.copy()


def full_history_belief(prior, transitions, observations) -> np.ndarray:
    likelihood = np.ones_like(prior, dtype=np.float64)
    if 0 in observations:
        likelihood *= observations[0].likelihood
    belief = np.asarray(prior, dtype=np.float64) * likelihood
    belief /= belief.sum()
    for step, transition in enumerate(transitions, 1):
        predicted = np.einsum("i,ij->j", belief, transition)
        likelihood = (observations[step].likelihood if step in observations
                      else np.ones_like(predicted))
        weighted = predicted * likelihood
        belief = weighted / weighted.sum()
    return belief


def attribution_controls() -> dict:
    def row(tool, *, read=False, effect=False):
        keys = ["quest_steps"] if effect else []
        return {"tool": tool, "was_read": read, "refused": None,
                "moved": effect, "progress": effect, "advance": effect,
                "moved_keys": list(keys), "progress_keys": list(keys),
                "advance_keys": list(keys)}

    clean = [row("A"), row("state.snapshot", read=True, effect=True)]
    delayed = [row("A"), row("B"), row("state.snapshot", read=True, effect=True)]
    _attribute_lagged(clean)
    _attribute_lagged(delayed)
    return {
        "scope": "synthetic rows through the existing transition builder",
        "clean_origin": "A",
        "clean_credited": [r["tool"] for r in clean if r["advance"]],
        "delayed_injected_origin": "A",
        "delayed_credited": [r["tool"] for r in delayed if r["advance"]],
        "identical_visible_trace_possible_origins": ["A", "B", "joint", "external"],
        "causal_owner_from_belief_replay": None,
    }


def _bootstrap_mean_ci(values: list[float], seed: int) -> list[float]:
    rng = np.random.default_rng(seed)
    data = np.asarray(values)
    means = np.empty(2000)
    for index in range(len(means)):
        means[index] = data[rng.integers(0, len(data), len(data))].mean()
    return [float(v) for v in np.quantile(means, [0.025, 0.975])]


def run_probe(*, episodes: int = 256, horizon: int = 32, capacity: int = 8,
              seed: int = 731) -> dict:
    if episodes < 2 or horizon < 2 or capacity < 2:
        raise ValueError("episodes, horizon and capacity must each be at least two")
    rng = random.Random(seed)
    prior = np.array([0.5, 0.5])
    transitions = (np.array([[0.95, 0.05], [0.05, 0.95]]),
                   np.array([[0.05, 0.95], [0.95, 0.05]]))
    totals = {name: {"nll": 0.0, "brier": 0.0, "correct": 0}
              for name in ("bounded_replay", "arrival_order", "full_history")}
    differences = []
    latency_ns = []
    status_counts: dict[str, int] = {}
    digest = hashlib.sha256()
    max_error = 0.0
    maximum_buffer = 0
    decisions = 0
    scheduled_count = dropped_count = delayed_count = 0
    for episode in range(episodes):
        epoch = str(episode)
        replay = FixedLagBelief(prior, epoch=epoch, capacity=capacity)
        arrival_belief = prior.copy()
        received: dict[int, Observation] = {}
        seen_arrival: set[int] = set()
        history = []
        deliveries: dict[int, list[Observation]] = {}
        state = rng.randrange(2)
        episode_delta = 0.0
        for step in range(horizon):
            action = None
            if step:
                action = rng.randrange(2)
                matrix = transitions[action]
                history.append(matrix)
                state = 0 if rng.random() < matrix[state, 0] else 1
                arrival_belief = arrival_belief @ matrix
            observed = state if rng.random() < 0.95 else 1 - state
            observation = Observation(epoch, step, step,
                                      (0.95, 0.05) if observed == 0 else (0.05, 0.95))
            dropped = rng.random() < 0.10
            delay = rng.randrange(capacity)
            if not dropped:
                deliveries.setdefault(step + delay, []).append(observation)
                scheduled_count += 1
                delayed_count += int(delay > 0)
                if rng.random() < 0.25:
                    deliveries[step + delay].append(observation)
            else:
                dropped_count += 1
            digest.update(json.dumps([episode, step, action, state, observed, dropped, delay],
                                     separators=(",", ":")).encode())
            batch = deliveries.pop(step, [])
            rng.shuffle(batch)
            started = time.perf_counter_ns()
            if step:
                replay.advance(matrix)
            statuses = [replay.ingest(event) for event in batch]
            bounded = replay.belief
            latency_ns.append(time.perf_counter_ns() - started)
            maximum_buffer = max(maximum_buffer, len(replay.buffered_steps))
            for status in statuses:
                status_counts[status] = status_counts.get(status, 0) + 1
            for event in batch:
                if event.sequence not in seen_arrival:
                    seen_arrival.add(event.sequence)
                    arrival_belief *= event.likelihood
                    arrival_belief /= arrival_belief.sum()
                    received[event.source_step] = event
            full = full_history_belief(prior, history, received)
            max_error = max(max_error, float(np.max(np.abs(bounded - full))))
            current_losses = {}
            for name, belief in (("bounded_replay", bounded),
                                 ("arrival_order", arrival_belief), ("full_history", full)):
                loss = -math.log(max(float(belief[state]), 1e-15))
                current_losses[name] = loss
                totals[name]["nll"] += loss
                totals[name]["brier"] += float((belief[1] - state) ** 2)
                totals[name]["correct"] += int(int(np.argmax(belief)) == state)
            episode_delta += current_losses["bounded_replay"] - current_losses["arrival_order"]
            decisions += 1
        differences.append(episode_delta / horizon)
    for values in totals.values():
        values["accuracy"] = values["correct"] / decisions
        values["nll"] /= decisions
        values["brier"] /= decisions
    ci = _bootstrap_mean_ci(differences, seed + 1)
    controls = attribution_controls()
    checks = {
        "same_information_full_history_parity": max_error < 1e-12,
        "bounded_storage": maximum_buffer <= capacity,
        "delays_and_duplicates_exercised": delayed_count > 0 and status_counts.get("duplicate", 0) > 0,
        "lower_nll_than_arrival_order": ci[1] < 0,
        "existing_attribution_failure_reproduced": controls["delayed_credited"] == ["B"],
    }
    return {
        "scope": "synthetic finite-state filtering; not gameplay or causal attribution",
        "config": {"episodes": episodes, "horizon": horizon, "capacity": capacity, "seed": seed,
                   "process_error": 0.05, "sensor_error": 0.05, "drop_probability": 0.10},
        "trace_sha256": digest.hexdigest(),
        "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "baseline_sha256": hashlib.sha256((ROOT / "tools" / "build_live_transitions.py").read_bytes()).hexdigest(),
        "decisions": decisions,
        "scheduled_observations": scheduled_count,
        "dropped_observations": dropped_count,
        "delayed_observations": delayed_count,
        "observations_pending_at_episode_end": scheduled_count - status_counts.get("accepted", 0),
        "ingest_statuses": status_counts,
        "maximum_buffered_frames": maximum_buffer,
        "maximum_full_history_error": max_error,
        "metrics": totals,
        "paired_episode_nll_delta": float(np.mean(differences)),
        "paired_episode_nll_ci95": ci,
        "filter_step_latency_ms": {"mean": float(np.mean(latency_ns) / 1e6),
                                   "p95": float(np.quantile(latency_ns, 0.95) / 1e6),
                                   "scope": "advance + received batch + belief copy; not LivePolicy.decide"},
        "attribution_controls": controls,
        "checks": checks,
        "passed": all(checks.values()),
        "limitations": ["known two-state dynamics and observation model",
                        "trusted source step and source identity supplied by the simulator",
                        "delays within the retained window; missing observations add no evidence",
                        "uniform random shared actions, not a learned decision policy",
                        "no live instrumentation, neural weights, or causal ownership inferred"],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episodes", type=int, default=256)
    parser.add_argument("--horizon", type=int, default=32)
    parser.add_argument("--capacity", type=int, default=8)
    parser.add_argument("--seed", type=int, default=731)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    report = run_probe(episodes=args.episodes, horizon=args.horizon,
                       capacity=args.capacity, seed=args.seed)
    content = json.dumps(report, indent=2, allow_nan=False)
    if args.out:
        with args.out.open("x", encoding="utf-8") as handle:
            handle.write(content + "\n")
    print(content)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
