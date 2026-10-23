"""Local CPU inference package. No CUDA, no torch.compile, no training here."""

from __future__ import annotations

from .runtime import (
    ForecastResult,
    MultiInstanceNetworkHealth,
    NetworkHealthInference,
    Observation,
    RollingWindow,
)

def resolve_checkpoint(artifacts_dir=None):
    """Which checkpoint should a caller use?

    There are two, and they are not interchangeable — the choice depends on the cadence of the
    series the caller is about to feed in, and only the caller knows that.

        local_model/model.pt   trained on this machine at 0.5 s, validated on held-out hosts.
                               RTT log-MAE 0.1464 at h1; predicted 56.3 ms for a 63.3 ms link.
        model.pt               RIPE Atlas, ~60 s spacing. 0.3597 at h1; predicted 1.2 ms for
                               the same 63.3 ms link, because `log_gap_seconds` is a feature.

    The local one wins when it exists because every tool in this project samples fast. Returning
    the path and a reason, rather than just the path, is deliberate: the caller is expected to
    print the reason when it is the slower-cadence model, instead of silently quoting a forecast
    that does not apply.

    Returns (path, kind, note) where kind is "local", "global" or "missing".
    """
    from pathlib import Path

    root = Path(artifacts_dir) if artifacts_dir else (
        Path(__file__).resolve().parent.parent.parent / "networkhealth_artifacts")
    local = root / "local_model" / "model.pt"
    global_ = root / "model.pt"

    if local.exists():
        return local, "local", "trained at 0.5 s on this machine; validated on held-out hosts"
    if global_.exists():
        return global_, "global", ("RIPE Atlas at ~60 s spacing; out of distribution for a "
                                   "fast series, so treat the forecast as advisory")
    return global_, "missing", "no checkpoint found; build one with ai/collect_local.py"


__all__ = [
    "ForecastResult",
    "MultiInstanceNetworkHealth",
    "NetworkHealthInference",
    "Observation",
    "RollingWindow",
    "resolve_checkpoint",
]
