"""Sim-to-Real Calibration: empirical distributions fitted from live server decisions.

Fits:
1. Empirical failure rate and failure modes from `trajectories/live_policy_decisions.jsonl`.
2. Empirical latency distribution: log-normal model fitted to server_latency_ms.
3. Observation staleness and dropout rates.
"""

from __future__ import annotations

import json
import logging
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("dofus.sim.calibration")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_LIVE_LOG = REPO_ROOT / "trajectories" / "live_policy_decisions.jsonl"


@dataclass
class EmpiricalDistribution:
    total_decisions: int = 2112
    failure_rate: float = 0.0341
    latency_mu: float = 2.082
    latency_sigma: float = 0.908
    latency_p50: float = 7.10
    latency_p95: float = 37.65
    latency_p99: float = 404.37
    obs_lag_rate: float = 0.0204
    obs_drop_rate: float = 0.0137
    failure_reasons: Dict[str, float] = field(default_factory=lambda: {
        "observation_not_actionable": 0.597,
        "empty_spell_slot": 0.403,
    })


class SimToRealCalibrator:
    """Calibrates symbolic simulation against live server trajectories."""

    _instance: Optional[SimToRealCalibrator] = None

    def __init__(self, dist: Optional[EmpiricalDistribution] = None) -> None:
        self.dist = dist or EmpiricalDistribution()

    @classmethod
    def get_instance(cls) -> SimToRealCalibrator:
        if cls._instance is None:
            cls._instance = cls.from_live_log(DEFAULT_LIVE_LOG)
        return cls._instance

    @classmethod
    def from_live_log(cls, log_path: Path) -> SimToRealCalibrator:
        """Fits empirical transition, latency, and failure distributions from live logs."""
        if not log_path.exists():
            return cls(EmpiricalDistribution())

        total = 0
        fails = 0
        reasons: Dict[str, int] = {}
        latencies: List[float] = []
        stale_obs = 0
        dropped_obs = 0

        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    total += 1
                    decision = entry.get("decision", {})
                    ok = decision.get("ok", True)
                    if not ok:
                        fails += 1
                        reason = decision.get("reason", "unknown")
                        reasons[reason] = reasons.get(reason, 0) + 1

                    contract = decision.get("contract", {})
                    if not contract.get("ok", True):
                        stale_obs += 1
                    if "cells_missing" in contract.get("errors", []):
                        dropped_obs += 1

                    lat = decision.get("server_latency_ms") or decision.get("latency_ms")
                    if lat and lat > 0:
                        latencies.append(float(lat))
        except Exception as e:
            logger.warning(f"Error parsing live policy decisions for calibration: {e}")
            return cls(EmpiricalDistribution())

        if total == 0:
            return cls(EmpiricalDistribution())

        failure_rate = fails / float(total)
        total_fail_reasons = sum(reasons.values()) or 1
        normalized_reasons = {r: count / float(total_fail_reasons) for r, count in reasons.items()}

        if latencies:
            log_lats = [math.log(x) for x in latencies if x > 0]
            mu = sum(log_lats) / len(log_lats) if log_lats else 2.082
            variance = sum((x - mu) ** 2 for x in log_lats) / len(log_lats) if len(log_lats) > 1 else 0.8
            sigma = math.sqrt(variance)
            latencies.sort()
            n = len(latencies)
            p50 = latencies[int(n * 0.50)]
            p95 = latencies[int(n * 0.95)]
            p99 = latencies[int(n * 0.99)]
        else:
            mu, sigma, p50, p95, p99 = 2.082, 0.908, 7.10, 37.65, 404.37

        dist = EmpiricalDistribution(
            total_decisions=total,
            failure_rate=failure_rate,
            latency_mu=mu,
            latency_sigma=sigma,
            latency_p50=p50,
            latency_p95=p95,
            latency_p99=p99,
            obs_lag_rate=max(0.005, stale_obs / float(total)),
            obs_drop_rate=max(0.005, dropped_obs / float(total)),
            failure_reasons=normalized_reasons,
        )
        return cls(dist)

    def sample_latency(self, rng: random.Random) -> float:
        """Samples simulated round-trip latency (ms) from the fitted log-normal distribution."""
        z = rng.gauss(0.0, 1.0)
        log_lat = self.dist.latency_mu + self.dist.latency_sigma * z
        return round(max(0.1, math.exp(log_lat)), 2)

    def sample_failure(self, rng: random.Random, tool: str) -> Tuple[bool, str]:
        """Samples whether an action fails, returning (failed, reason)."""
        if rng.random() >= self.dist.failure_rate:
            return False, ""

        # Pick failure reason from categorical distribution
        r_val = rng.random()
        cumulative = 0.0
        for reason, prob in self.dist.failure_reasons.items():
            cumulative += prob
            if r_val <= cumulative:
                return True, reason

        return True, "action_failed"
