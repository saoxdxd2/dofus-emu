"""Kalman-filter RTT tracker and EWMA loss forecaster.

This module is the lightweight, pure-NumPy companion to the micro-CNN
(``cnn_int8``). While the CNN produces a learned per-horizon forecast from a
feature crop, the forecaster here maintains an online, recursive estimate of
the *current* link quality:

* :class:`KalmanRTT` — a 2-state Kalman filter (level + drift) over RTT that
  smooths noisy samples and extrapolates a short-term RTT trajectory with an
  honest uncertainty band.
* :class:`EWMALoss` — an exponentially-weighted moving average of the loss
  rate, with an adaptive trend term that flags a rising loss rate before it
  becomes sustained.
* :class:`NetworkForecaster` — combines both into a single ``observe`` /
  ``forecast`` API used by the sentinel and the desktop supervisor's proactive
  failover.

Nothing here touches the network, the disk, or the GPU. All state is in RAM
and reset per instance, so it is safe to run one forecaster per game client.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class KalmanRTT:
    """1D Kalman filter over RTT: state is (level, drift in ms per observation).

    The observation model is ``z = level + noise`` and the process model is
    ``level_{k+1} = level + drift``, ``drift_{k+1} = drift``.  A measurement
    of ``None`` (a lost probe) performs a predict-only step and inflates
    uncertainty, which is exactly what we want for lossy links: the forecast
   's uncertainty band widens while we wait for the next good sample.
    """

    # Process noise: how much we expect the level and drift to wander per step.
    q_level: float = 4.0
    q_drift: float = 0.5
    # Measurement noise: trust a single RTT sample this much (variance, ms^2).
    r_meas: float = 25.0

    level: float = 0.0
    drift: float = 0.0
    # Covariance matrix [[P_ll, P_ld], [P_ld, P_dd]] stored as flat floats.
    p_ll: float = 9.0
    p_ld: float = 0.0
    p_dd: float = 1.0
    initialized: bool = False

    def predict(self, dt: float = 1.0) -> None:
        """Advance the state by ``dt`` observation steps (no measurement)."""
        # F = [[1, dt], [0, 1]]
        self.level = self.level + self.drift * dt
        # P = F P F' + Q
        p_ll = self.p_ll + 2.0 * dt * self.p_ld + dt * dt * self.p_dd + self.q_level
        p_ld = self.p_ld + dt * self.p_dd
        p_dd = self.p_dd + self.q_drift
        self.p_ll, self.p_ld, self.p_dd = p_ll, p_ld, p_dd

    def update(self, z: float) -> None:
        """Incorporate one RTT measurement ``z`` (ms)."""
        # H = [1, 0]; innovation y = z - level
        y = z - self.level
        # S = H P H' + R = p_ll + r_meas
        s = self.p_ll + self.r_meas
        if s <= 0:
            s = 1e-6
        # K = P H' / S = [p_ll, p_ld] / s
        k_ll = self.p_ll / s
        k_ld = self.p_ld / s
        self.level = self.level + k_ll * y
        self.drift = self.drift + k_ld * y
        # P = (I - K H) P
        p_ll = (1.0 - k_ll) * self.p_ll
        p_ld = (1.0 - k_ll) * self.p_ld
        p_dd = self.p_dd - k_ld * self.p_ld
        self.p_ll, self.p_ld, self.p_dd = p_ll, p_ld, p_dd

    def filter(self, z: float | None, dt: float = 1.0) -> tuple[float, float]:
        """One predict + (optional) update step.

        Returns ``(level_ms, std_ms)`` where ``std_ms`` is the 1-sigma
        uncertainty of the level estimate.
        """
        self.predict(dt)
        if z is not None:
            self.update(z)
        return self.level, math.sqrt(max(self.p_ll, 0.0))

    def forecast(self, steps: int) -> list[tuple[float, float]]:
        """Extrapolate ``steps`` ahead: list of (level_ms, std_ms)."""
        out: list[tuple[float, float]] = []
        level = self.level
        drift = self.drift
        p_ll, p_ld, p_dd = self.p_ll, self.p_ld, self.p_dd
        for h in range(1, steps + 1):
            level = level + drift
            # uncertainty grows with horizon via process noise
            p_ll = p_ll + 2.0 * p_ld + p_dd + self.q_level
            p_ld = p_ld + p_dd
            p_dd = p_dd + self.q_drift
            out.append((level, math.sqrt(max(p_ll, 0.0))))
        return out


@dataclass
class EWMALoss:
    """Exponentially-weighted moving average of the loss rate with a trend term.

    ``alpha`` controls how fast the average forgets the past. ``trend_alpha``
    smooths the *change* in the loss rate so a steadily climbing loss curve is
    flagged as "degrading" long before the EWMA level itself crosses a hard
    threshold.
    """

    alpha: float = 0.3
    trend_alpha: float = 0.2
    loss_ema: float = 0.0
    trend_ema: float = 0.0  # smoothed first difference of loss_ema
    initialized: bool = False
    _prev: float = 0.0

    def observe(self, lost: bool) -> tuple[float, float]:
        """Feed one probe result (True == lost). Returns (loss_ema, trend)."""
        x = 1.0 if lost else 0.0
        if not self.initialized:
            self.loss_ema = x
            self.trend_ema = 0.0
            self._prev = x
            self.initialized = True
            return self.loss_ema, self.trend_ema
        self.loss_ema = self.alpha * x + (1.0 - self.alpha) * self.loss_ema
        diff = self.loss_ema - self._prev
        self.trend_ema = self.trend_alpha * diff + (1.0 - self.trend_alpha) * self.trend_ema
        self._prev = self.loss_ema
        return self.loss_ema, self.trend_ema


@dataclass
class ForecastSnapshot:
    """A point-in-time RTT/loss forecast produced by :class:`NetworkForecaster`."""

    timestamp: float
    rtt_ms: float
    rtt_std_ms: float
    loss_probability: float
    loss_trend: float
    degradation_probability: float
    horizons: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "rtt_ms": self.rtt_ms,
            "rtt_std_ms": self.rtt_std_ms,
            "loss_probability": self.loss_probability,
            "loss_trend": self.loss_trend,
            "degradation_probability": self.degradation_probability,
            "horizons": self.horizons,
        }


class NetworkForecaster:
    """Combined RTT Kalman filter + EWMA loss forecaster.

    ``observe`` is called once per probe with the RTT (None if lost) and the
    received/sent counts. ``forecast`` returns a :class:`ForecastSnapshot`
    with the current estimate plus per-horizon extrapolations.
    """

    def __init__(
        self,
        *,
        kq_level: float = 4.0,
        kq_drift: float = 0.5,
        kr_meas: float = 25.0,
        loss_alpha: float = 0.3,
        loss_trend_alpha: float = 0.2,
        degradation_threshold: float = 0.5,
    ) -> None:
        self.rtt = KalmanRTT(q_level=kq_level, q_drift=kq_drift, r_meas=kr_meas)
        self.loss = EWMALoss(alpha=loss_alpha, trend_alpha=loss_trend_alpha)
        self.degradation_threshold = degradation_threshold
        self._last_ts: float | None = None

    def observe(
        self,
        rtt_ms: float | None,
        *,
        received: int = 0,
        sent: int = 0,
        timestamp: float | None = None,
    ) -> ForecastSnapshot:
        """Absorb one probe and return the current forecast snapshot."""
        now = time.time() if timestamp is None else float(timestamp)
        dt = 1.0
        if self._last_ts is not None:
            dt = max(1.0, now - self._last_ts)
        self._last_ts = now

        sent = max(int(sent), 1)
        lost = rtt_ms is None or received < sent
        self.rtt.filter(rtt_ms, dt)
        loss_ema, loss_trend = self.loss.observe(lost)

        level, std = self.rtt.level, math.sqrt(max(self.rtt.p_ll, 0.0))
        # Degradation probability: logistic in current RTT std and loss trend.
        z = (std / 60.0) + max(loss_trend, 0.0) * 3.0 - 0.8
        degradation = 1.0 / (1.0 + math.exp(-z))
        degradation = min(max(degradation, 0.0), 1.0)

        snapshot = ForecastSnapshot(
            timestamp=now,
            rtt_ms=level,
            rtt_std_ms=std,
            loss_probability=loss_ema,
            loss_trend=loss_trend,
            degradation_probability=degradation,
        )
        # Per-horizon extrapolation from the Kalman filter.
        for h, (f_level, f_std) in enumerate(self.rtt.forecast(3), start=1):
            snapshot.horizons.append(
                {
                    "horizon": h,
                    "rtt_ms": f_level,
                    "rtt_std_ms": f_std,
                    "loss_probability": loss_ema,
                    "degradation_probability": degradation,
                }
            )
        return snapshot

    def is_degrading(self, snapshot: ForecastSnapshot | None = None) -> bool:
        """True when the link is forecast to degrade within the near horizon."""
        s = snapshot if snapshot is not None else ForecastSnapshot(0, 0, 0, 0, 0, 0)
        return s.degradation_probability >= self.degradation_threshold

    def reset(self) -> None:
        self.rtt = KalmanRTT()
        self.loss = EWMALoss()
        self._last_ts = None


__all__ = [
    "KalmanRTT",
    "EWMALoss",
    "ForecastSnapshot",
    "NetworkForecaster",
]
