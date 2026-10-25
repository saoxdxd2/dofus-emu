"""Experience store: every parameter is a posterior, updated from real outcomes.

Principle
---------
Nothing in the planner should be a constant that someone guessed. Every quantity
that the game can measure is stored as an **exponentially-weighted conjugate
posterior**, updated in O(1) from a single observed event:

    drop rate          Beta(a, b)            hit / miss per kill
    kills per minute   Gamma(count, minutes)  a Poisson process
    duration (any)     log-normal (n, Σlog x, Σlog²x)   robust to corpse runs
    market price       log-normal + prior      drifts, so it forgets fast

Why conjugates instead of gradient descent
------------------------------------------
There is no training step to pay for. An update is a handful of float adds, so
the "training" is the experience itself:

    cost per observation   ~1 µs, no GPU, no replay buffer, no batch
    state per parameter    3–5 floats (a few hundred KB for the whole game)
    cold start             falls back to the prior, then shrinks toward data

The same mechanism handles recency: sufficient statistics are multiplied by
`0.5 ** (dt / half_life)` before each update, so week-old experience fades out of
a world where prices and level bands move. That is the only "learning rate" here,
and it is expressed in days, not steps.

What this store must NOT learn
------------------------------
Feasibility (a level-1 cannot fight a level-200 monster), safety limits, and the
tool schema are hard rules. Learning those online means a catastrophic action can
teach the wrong lesson. See ``docs/EXPERIENCE_LEARNING.md``.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG = REPO_ROOT / "data" / "kb" / "experience.jsonl"
DEFAULT_SNAPSHOT = REPO_ROOT / "data" / "kb" / "posteriors.json"

DAY = 86_400.0


# ---------------------------------------------------------------------------
# posteriors
# ---------------------------------------------------------------------------


class Posterior:
    """Base class: exponentially-weighted sufficient statistics."""

    kind = "base"

    def __init__(self, half_life_days: float = 0.0) -> None:
        self.half_life = half_life_days * DAY
        self.last_ts: Optional[float] = None
        self.n = 0.0

    # -- decay ---------------------------------------------------------------

    def _decay(self, ts: float) -> None:
        if not self.half_life or self.last_ts is None:
            self.last_ts = ts
            return
        delta = ts - self.last_ts
        if delta <= 0:
            return
        factor = 0.5 ** (delta / self.half_life)
        if factor < 1e-4:
            factor = 0.0
        self._scale(factor)
        self.last_ts = ts

    def _scale(self, factor: float) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    # -- interface -----------------------------------------------------------

    def update(self, value: float, weight: float = 1.0, ts: Optional[float] = None) -> None:
        raise NotImplementedError

    def mean(self) -> float:
        raise NotImplementedError

    def interval(self, level: float = 0.9) -> Tuple[float, float]:
        mean = self.mean()
        return mean, mean

    def to_dict(self) -> Dict[str, Any]:
        raise NotImplementedError

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "Posterior":
        raise NotImplementedError

    def confidence(self) -> float:
        """0 at the prior, approaching 1 as evidence accumulates."""
        return min(1.0, self.n / 30.0)


class BetaRate(Posterior):
    """Probability of a binary outcome (drop / no drop, success / failure)."""

    kind = "beta"

    def __init__(self, alpha: float = 2.0, beta: float = 30.0,
                 half_life_days: float = 0.0) -> None:
        super().__init__(half_life_days)
        self.alpha = float(alpha)
        self.beta = float(beta)
        self.n = 0.0

    def _scale(self, factor: float) -> None:
        # Keep the prior mass intact; only observed evidence decays.
        self.alpha = 2.0 + (self.alpha - 2.0) * factor
        self.beta = 30.0 + (self.beta - 30.0) * factor
        self.n *= factor

    def update(self, value: float, weight: float = 1.0, ts: Optional[float] = None) -> None:
        self._decay(ts if ts is not None else time.time())
        hits = max(0.0, min(1.0, float(value))) * weight
        self.alpha += hits
        self.beta += max(0.0, weight - hits)
        self.n += weight

    def mean(self) -> float:
        total = self.alpha + self.beta
        return self.alpha / total if total > 0 else 0.0

    def interval(self, level: float = 0.9) -> Tuple[float, float]:
        # Normal approximation to the Beta; adequate for display, not for testing.
        mean = self.mean()
        total = self.alpha + self.beta
        sd = math.sqrt(max(mean * (1 - mean), 1e-9) / max(total, 1e-9))
        z = 1.645 if level >= 0.9 else 1.0
        return max(0.0, mean - z * sd), min(1.0, mean + z * sd)

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "alpha": self.alpha, "beta": self.beta,
                "n": self.n, "half_life_days": self.half_life / DAY,
                "last_ts": self.last_ts}

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "BetaRate":
        obj = cls(payload.get("alpha", 2.0), payload.get("beta", 30.0),
                  payload.get("half_life_days", 0.0))
        obj.n = payload.get("n", 0.0)
        obj.last_ts = payload.get("last_ts")
        return obj


class GammaRate(Posterior):
    """Rate of events over exposure time (kills per minute, kamas per minute)."""

    kind = "gamma"

    def __init__(self, shape: float = 1.0, rate: float = 1.0,
                 half_life_days: float = 0.0) -> None:
        super().__init__(half_life_days)
        self.shape = float(shape)   # prior count
        self.rate = float(rate)     # prior exposure (minutes)
        self.n = 0.0

    def _scale(self, factor: float) -> None:
        self.shape = 1.0 + (self.shape - 1.0) * factor
        self.rate = 1.0 + (self.rate - 1.0) * factor
        self.n *= factor

    def update(self, value: float, weight: float = 1.0, ts: Optional[float] = None) -> None:
        """``value`` is a rate, ``weight`` the exposure in minutes behind it."""
        self._decay(ts if ts is not None else time.time())
        exposure = max(weight, 1e-6)
        self.shape += max(0.0, float(value)) * exposure
        self.rate += exposure
        self.n += exposure

    def mean(self) -> float:
        return self.shape / self.rate if self.rate > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "shape": self.shape, "rate": self.rate,
                "n": self.n, "half_life_days": self.half_life / DAY,
                "last_ts": self.last_ts}

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "GammaRate":
        obj = cls(payload.get("shape", 1.0), payload.get("rate", 1.0),
                  payload.get("half_life_days", 0.0))
        obj.n = payload.get("n", 0.0)
        obj.last_ts = payload.get("last_ts")
        return obj


class LogNormalDuration(Posterior):
    """Positive duration in minutes. Log space keeps outliers from dominating."""

    kind = "lognormal"

    def __init__(self, prior_median: float = 10.0, prior_strength: float = 3.0,
                 prior_sigma: float = 1.0, half_life_days: float = 0.0) -> None:
        super().__init__(half_life_days)
        self.prior_log = math.log(max(prior_median, 1e-3))
        self.prior_strength = float(prior_strength)
        self.prior_sigma = float(prior_sigma)
        self.sum_log = 0.0
        self.sum_log2 = 0.0
        self.n = 0.0

    def _scale(self, factor: float) -> None:
        self.sum_log *= factor
        self.sum_log2 *= factor
        self.n *= factor

    def update(self, value: float, weight: float = 1.0, ts: Optional[float] = None) -> None:
        self._decay(ts if ts is not None else time.time())
        log_value = math.log(max(float(value), 1e-3))
        self.sum_log += log_value * weight
        self.sum_log2 += log_value * log_value * weight
        self.n += weight

    def _stats(self) -> Tuple[float, float]:
        """Posterior mean and variance of log(duration), shrunk to the prior."""
        k = self.prior_strength
        total = self.n + k
        if total <= 0:
            return self.prior_log, self.prior_sigma ** 2
        mean = (self.sum_log + k * self.prior_log) / total
        if self.n > 1:
            observed_var = max(self.sum_log2 / self.n - (self.sum_log / self.n) ** 2, 1e-4)
        else:
            observed_var = self.prior_sigma ** 2
        var = (self.n * observed_var + k * self.prior_sigma ** 2) / total
        # Shrink the variance itself when evidence is thin.
        var /= max(1.0, self.n / 3.0)
        return mean, max(var, 1e-4)

    def mean(self) -> float:
        mean_log, _var = self._stats()
        return math.exp(mean_log)

    def spread(self) -> float:
        """Multiplicative 1-sigma band, e.g. 1.8 means ±80%."""
        _mean, var = self._stats()
        return math.exp(math.sqrt(var))

    def interval(self, level: float = 0.9) -> Tuple[float, float]:
        mean_log, var = self._stats()
        z = 1.645 if level >= 0.9 else 1.0
        sd = math.sqrt(var)
        return math.exp(mean_log - z * sd), math.exp(mean_log + z * sd)

    def confidence(self) -> float:
        return min(1.0, self.n / 20.0)

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "sum_log": self.sum_log, "sum_log2": self.sum_log2,
                "n": self.n, "prior_log": self.prior_log,
                "prior_strength": self.prior_strength, "prior_sigma": self.prior_sigma,
                "half_life_days": self.half_life / DAY, "last_ts": self.last_ts}

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "LogNormalDuration":
        obj = cls(math.exp(payload.get("prior_log", 0.0)),
                  payload.get("prior_strength", 3.0),
                  payload.get("prior_sigma", 1.0),
                  payload.get("half_life_days", 0.0))
        obj.sum_log = payload.get("sum_log", 0.0)
        obj.sum_log2 = payload.get("sum_log2", 0.0)
        obj.n = payload.get("n", 0.0)
        obj.last_ts = payload.get("last_ts")
        return obj


class NormalPosterior(Posterior):
    """Signed quantity with a normal approximation (log-ratios, residuals)."""

    kind = "normal"

    def __init__(self, prior_mean: float = 0.0, prior_strength: float = 2.0,
                 prior_sigma: float = 0.6, half_life_days: float = 0.0) -> None:
        super().__init__(half_life_days)
        self.prior_mean = float(prior_mean)
        self.prior_strength = float(prior_strength)
        self.prior_sigma = float(prior_sigma)
        self.sum_x = 0.0
        self.sum_x2 = 0.0
        self.n = 0.0

    def _scale(self, factor: float) -> None:
        self.sum_x *= factor
        self.sum_x2 *= factor
        self.n *= factor

    def update(self, value: float, weight: float = 1.0, ts: Optional[float] = None) -> None:
        self._decay(ts if ts is not None else time.time())
        self.sum_x += float(value) * weight
        self.sum_x2 += float(value) ** 2 * weight
        self.n += weight

    def _stats(self) -> Tuple[float, float]:
        k = self.prior_strength
        total = self.n + k
        mean = (self.sum_x + k * self.prior_mean) / total
        if self.n > 1:
            observed_var = max(self.sum_x2 / self.n - (self.sum_x / self.n) ** 2, 1e-6)
        else:
            observed_var = self.prior_sigma ** 2
        var = (self.n * observed_var + k * self.prior_sigma ** 2) / total
        return mean, max(var / max(1.0, self.n / 4.0), 1e-6)

    def mean(self) -> float:
        return self._stats()[0]

    def variance(self) -> float:
        return self._stats()[1]

    def interval(self, level: float = 0.9) -> Tuple[float, float]:
        mean, var = self._stats()
        z = 1.645 if level >= 0.9 else 1.0
        sd = math.sqrt(var)
        return mean - z * sd, mean + z * sd

    def confidence(self) -> float:
        return min(1.0, self.n / 10.0)

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "sum_x": self.sum_x, "sum_x2": self.sum_x2,
                "n": self.n, "prior_mean": self.prior_mean,
                "prior_strength": self.prior_strength, "prior_sigma": self.prior_sigma,
                "half_life_days": self.half_life / DAY, "last_ts": self.last_ts}

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "NormalPosterior":
        obj = cls(payload.get("prior_mean", 0.0), payload.get("prior_strength", 2.0),
                  payload.get("prior_sigma", 0.6), payload.get("half_life_days", 0.0))
        obj.sum_x = payload.get("sum_x", 0.0)
        obj.sum_x2 = payload.get("sum_x2", 0.0)
        obj.n = payload.get("n", 0.0)
        obj.last_ts = payload.get("last_ts")
        return obj


_KINDS = {cls.kind: cls for cls in (BetaRate, GammaRate, LogNormalDuration, NormalPosterior)}


# ---------------------------------------------------------------------------
# events and store
# ---------------------------------------------------------------------------


@dataclass
class Event:
    """One observed outcome. This is the unit of "experience".

    ``source`` matters: simulated runs, unit tests and replays must never teach
    the production posteriors. Anything that is not ``live`` (or explicitly
    allow-listed) is dropped by the store.
    """

    kind: str
    key: str
    value: float
    weight: float = 1.0
    predicted: Optional[float] = None
    ts: float = field(default_factory=time.time)
    source: str = "live"
    context: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts": round(self.ts, 3), "kind": self.kind, "key": self.key,
            "value": round(float(self.value), 6), "weight": round(float(self.weight), 6),
            "predicted": None if self.predicted is None else round(float(self.predicted), 6),
            "source": self.source,
            "context": self.context, "meta": self.meta,
        }


class ExperienceStore:
    """Named posteriors + an append-only event log.

    The log is the source of truth: ``replay()`` rebuilds every posterior from it,
    so a corrupted snapshot is recoverable and every estimate is auditable down to
    the observation that moved it.
    """

    def __init__(
        self,
        log_path: Path = DEFAULT_LOG,
        snapshot_path: Path = DEFAULT_SNAPSHOT,
        autosave_every: int = 25,
    ) -> None:
        self.log_path = Path(log_path)
        self.snapshot_path = Path(snapshot_path)
        self.autosave_every = autosave_every
        self.params: Dict[str, Posterior] = {}
        self.pending: List[Event] = []
        self.events_seen = 0
        # None = accept every source (used by the closed-loop demo, which runs on
        # a throwaway store). Production sets {"live", "manual"}.
        self.sources_allowed: Optional[set] = {"live", "manual"}
        self.rejected_events = 0
        self._load()

    # -- persistence ---------------------------------------------------------

    def _load(self) -> None:
        if self.snapshot_path.exists():
            try:
                payload = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                payload = {}
            for key, blob in (payload.get("params") or {}).items():
                factory = _KINDS.get(blob.get("kind", ""))
                if factory is not None:
                    self.params[key] = factory.from_dict(blob)
            self.events_seen = int(payload.get("events_seen") or 0)

    def save(self) -> None:
        self.flush_log()
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_path.write_text(json.dumps({
            "events_seen": self.events_seen,
            "params": {key: value.to_dict() for key, value in self.params.items()},
        }, indent=1), encoding="utf-8")

    def flush_log(self) -> None:
        if not self.pending:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8", newline="\n") as handle:
            for event in self.pending:
                handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        self.pending.clear()

    def replay(self) -> int:
        """Rebuild every posterior from the event log. Returns events replayed."""
        self.params.clear()
        self.events_seen = 0
        if not self.log_path.exists():
            return 0
        count = 0
        with self.log_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._apply(Event(
                    kind=row["kind"], key=row["key"], value=float(row["value"]),
                    weight=float(row.get("weight", 1.0)),
                    predicted=row.get("predicted"), ts=float(row.get("ts", 0.0)),
                    source=row.get("source", "live"),
                    context=row.get("context") or {}, meta=row.get("meta") or {},
                ))
                count += 1
        self.events_seen = count
        return count

    # -- updating ------------------------------------------------------------

    def _ensure(self, key: str, kind: str, **prior: Any) -> Posterior:
        existing = self.params.get(key)
        if existing is not None and existing.kind == kind:
            return existing
        if kind == "beta":
            created: Posterior = BetaRate(**prior)
        elif kind == "gamma":
            created = GammaRate(**prior)
        elif kind == "lognormal":
            created = LogNormalDuration(**prior)
        elif kind == "normal":
            created = NormalPosterior(**prior)
        else:
            raise ValueError(f"unknown posterior kind {kind!r}")
        self.params[key] = created
        return created

    def _accepts(self, event: Event) -> bool:
        return self.sources_allowed is None or event.source in self.sources_allowed

    def _apply(self, event: Event) -> None:
        if not self._accepts(event):
            self.rejected_events += 1
            return
        posterior = self.params.get(event.key)
        if posterior is None:
            return
        posterior.update(event.value, event.weight, event.ts)
        self.events_seen += 1

    def record(self, event: Event, **prior: Any) -> Posterior:
        """Update a parameter from one real observation, creating it if needed."""
        if not self._accepts(event):
            self.rejected_events += 1
            posterior = self._ensure(event.key, event.kind, **prior)
            return posterior
        posterior = self._ensure(event.key, event.kind, **prior)
        posterior.update(event.value, event.weight, event.ts)
        self.events_seen += 1
        self.pending.append(event)
        if len(self.pending) >= self.autosave_every:
            self.save()
        return posterior

    # -- reading -------------------------------------------------------------

    def get(self, key: str) -> Optional[Posterior]:
        return self.params.get(key)

    def estimate(self, key: str) -> Optional[float]:
        posterior = self.params.get(key)
        return None if posterior is None else posterior.mean()

    def confidence(self, key: str) -> float:
        posterior = self.params.get(key)
        return 0.0 if posterior is None else posterior.confidence()

    def interval(self, key: str, level: float = 0.9) -> Optional[Tuple[float, float]]:
        posterior = self.params.get(key)
        return None if posterior is None else posterior.interval(level)

    def summary(self, prefix: str = "", limit: int = 20) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for key, posterior in sorted(self.params.items()):
            if prefix and not key.startswith(prefix):
                continue
            low, high = posterior.interval(0.9)
            rows.append({
                "key": key,
                "kind": posterior.kind,
                "mean": round(posterior.mean(), 4),
                "p05": round(low, 4),
                "p95": round(high, 4),
                "n": round(posterior.n, 1),
                "confidence": round(posterior.confidence(), 2),
            })
            if len(rows) >= limit:
                break
        return rows

    def __len__(self) -> int:
        return len(self.params)
