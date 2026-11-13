"""Tests for Sim-to-Real Calibration and Uncertainty-Driven DAgger.

Verifies:
1. SimToRealCalibrator fits empirical distributions from live_policy_decisions.jsonl.
2. World(calibrated=True) accurately simulates server latencies and failure distributions.
3. Active ensemble uncertainty sampling in rollout accurately flags ambiguous states.
4. Generated DAgger trajectories contain uncertainty scores and calibrated latencies.
"""

from __future__ import annotations

import random
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sim.calibration import DEFAULT_LIVE_LOG, EmpiricalDistribution, SimToRealCalibrator
from sim.rollout import compute_ensemble_uncertainty, episode
from sim.world import World


def test_calibrator_fits_live_logs():
    calibrator = SimToRealCalibrator.from_live_log(DEFAULT_LIVE_LOG)
    dist = calibrator.dist

    assert dist.total_decisions > 1000, f"Expected >1000 decisions, got {dist.total_decisions}"
    assert 0.01 < dist.failure_rate < 0.10, f"Expected failure rate in (1%, 10%), got {dist.failure_rate:.4f}"
    assert 3.0 < dist.latency_p50 < 15.0, f"Expected p50 in (3ms, 15ms), got {dist.latency_p50}"
    assert 20.0 < dist.latency_p95 < 60.0, f"Expected p95 in (20ms, 60ms), got {dist.latency_p95}"
    assert "observation_not_actionable" in dist.failure_reasons


def test_calibrated_world_execution():
    rng = random.Random(42)
    world = World(level=60, seed=42, max_steps=50, calibrated=True)

    assert world.calibrated is True
    assert world.calibrator is not None
    assert world.noise is True
    assert abs(world.action_fail - world.calibrator.dist.failure_rate) < 1e-4

    # Execute a step and verify simulated server latency is reported
    ok, note = world.step("state.snapshot", {})
    assert ok is True
    assert world.last_latency_ms > 0.0

    obs = world.observe()
    assert "server_latency_ms" in obs
    assert obs["server_latency_ms"] == world.last_latency_ms


def test_uncertainty_estimator():
    # Test high-uncertainty state (unmatched map, no inventory)
    feat_ambiguous = [0.8, 0.0, 0.0] + [0.0] * 23
    u_score, is_dagger = compute_ensemble_uncertainty(feat_ambiguous, "nav.goto", {})

    assert 0.0 <= u_score <= 1.0
    assert u_score >= 0.40, f"Expected high uncertainty for ambiguous state, got {u_score}"
    assert is_dagger is True


def test_dagger_rollout_generation():
    rng = random.Random(123)
    rows = episode(
        level=60,
        steps=30,
        seed=123,
        rng=rng,
        calibrated=True,
        dagger=True,
        min_uncertainty=0.40,
    )

    assert len(rows) > 0
    for row in rows:
        assert "uncertainty" in row
        assert "is_dagger" in row
        assert "latency_ms" in row
        assert row["latency_ms"] >= 0.0
        assert 0.0 <= row["uncertainty"] <= 1.0

    # Ensure at least some states were flagged as high-uncertainty DAgger queries
    dagger_count = sum(1 for r in rows if r["is_dagger"])
    print(f"\n[DAGGER ROLLOUT] {dagger_count}/{len(rows)} steps flagged as high-uncertainty queries")
    assert dagger_count >= 1


if __name__ == "__main__":
    test_calibrator_fits_live_logs()
    test_calibrated_world_execution()
    test_uncertainty_estimator()
    test_dagger_rollout_generation()
    print("\nALL SIM CALIBRATION & DAGGER TESTS PASSED!")
