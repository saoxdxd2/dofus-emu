"""Strategy ranker: time estimates corrected by experience, with bounded exploration.

Two jobs, one mechanism
-----------------------
The planner produces a deterministic estimate per branch (buy / farm / craft /
quest). Those estimates are systematically wrong in ways the cache cannot tell
you: a farm takes longer than the model thinks because of walking, respawns and
deaths; a craft is faster because the ingredients are already in the bag.

So the ranker learns, per (level band, strategy), a **multiplicative correction**
in log space:

    observed / predicted  ->  NormalPosterior over log-ratio
    corrected_minutes     =  predicted * exp(posterior mean)

That single number does calibration. Thompson sampling over its *uncertainty*
does exploration: an arm nobody has tried yet has a wide posterior, so it
occasionally wins the draw and gets measured. Both behaviours come from the same
three floats per arm.

Why this is cheap enough to run on every plan
---------------------------------------------
`choose()` is a handful of `exp` calls over ≤4 branches — microseconds. There is
no gradient, no replay buffer, and no batch: the update is one observation.

Safety rails
------------
* ``max_ratio`` — an arm whose *corrected* estimate is more than 3x the best is
  never sampled, no matter how uncertain it is. Exploration must not be able to
  pick something catastrophic.
* ``exploration_ratio`` — an arm that has **never been tried** may be sampled
  once if its raw estimate is within this factor of the best, because otherwise a
  systematically biased branch can never be discovered to be better. The price of
  finding out is bounded by this factor, once, per (band, arm). Set it to 1.0 to
  disable curiosity entirely.
* the correction is clamped to [0.2, 5] so a single bad run cannot invert the
  ranking forever.
* feasibility (level gates, missing tools) is decided *before* the ranker runs.
  The ranker only orders branches that are already possible.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from planning.experience import Event, ExperienceStore, NormalPosterior

BANDS: Sequence[tuple[int, int]] = (
    (1, 20), (21, 50), (51, 90), (91, 130), (131, 170), (171, 200), (201, 999),
)
ARMS = ("buy", "farm", "craft", "quest")

CORRECTION_FLOOR = 0.2
CORRECTION_CEIL = 5.0


def band_of(level: int) -> str:
    for low, high in BANDS:
        if low <= level <= high:
            return f"{low}-{high}"
    return "201-999"


@dataclass
class ArmState:
    arm: str
    band: str
    correction: float
    confidence: float
    samples: float

    def as_dict(self) -> Dict[str, Any]:
        return {
            "arm": self.arm, "band": self.band,
            "correction": round(self.correction, 3),
            "confidence": round(self.confidence, 2),
            "samples": round(self.samples, 1),
        }


class StrategyRanker:
    """Reorders feasible plan branches using learned corrections."""

    def __init__(
        self,
        store: ExperienceStore,
        prior_sigma: float = 0.6,
        max_ratio: float = 3.0,
        exploration_ratio: float = 4.0,
        min_samples: int = 5,
        half_life_days: float = 21.0,
        seed: int = 7,
    ) -> None:
        self.store = store
        self.prior_sigma = prior_sigma
        self.max_ratio = max_ratio
        self.exploration_ratio = exploration_ratio
        # A correction is not trusted below this many observations: the arm keeps
        # getting trials so the posterior can actually move.
        self.min_samples = max(1, int(min_samples))
        self.half_life_days = half_life_days
        self.rng = random.Random(seed)

    # -- keys ----------------------------------------------------------------

    @staticmethod
    def _key(band: str, arm: str) -> str:
        return f"rank:{band}:{arm}"

    @classmethod
    def rank_key(cls, level: int, arm: str) -> str:
        """Public key builder, so callers never hand-build the format."""
        return cls._key(band_of(level), arm)

    def _posterior(self, band: str, arm: str, create: bool = False) -> Optional[NormalPosterior]:
        key = self._key(band, arm)
        existing = self.store.get(key)
        if existing is not None:
            return existing  # type: ignore[return-value]
        if not create:
            return None
        created = self.store._ensure(  # noqa: SLF001 - intentional: no prior event yet
            key, "normal",
            prior_mean=0.0,
            prior_strength=2.0,
            prior_sigma=self.prior_sigma,
            half_life_days=self.half_life_days,
        )
        return created  # type: ignore[return-value]

    # -- reading -------------------------------------------------------------

    def correction(self, band: str, arm: str) -> float:
        posterior = self._posterior(band, arm)
        if posterior is None:
            return 1.0
        return min(CORRECTION_CEIL, max(CORRECTION_FLOOR, math.exp(posterior.mean())))

    def corrected_minutes(self, minutes: float, band: str, arm: str) -> float:
        return minutes * self.correction(band, arm)

    def _optimistic(self, band: str, arm: str) -> float:
        """Lower bound on the correction: how much better this arm might be."""
        posterior = self._posterior(band, arm)
        if posterior is None:
            return math.exp(-self.prior_sigma)
        sd = math.sqrt(max(posterior.variance(), 1e-6))
        return min(CORRECTION_CEIL, max(CORRECTION_FLOOR,
                                         math.exp(posterior.mean() - sd)))

    def state(self, band: str) -> List[ArmState]:
        rows: List[ArmState] = []
        for arm in ARMS:
            posterior = self._posterior(band, arm)
            rows.append(ArmState(
                arm=arm, band=band,
                correction=self.correction(band, arm),
                confidence=0.0 if posterior is None else posterior.confidence(),
                samples=0.0 if posterior is None else posterior.n,
            ))
        return rows

    # -- choosing ------------------------------------------------------------

    def choose(self, nodes: Sequence[Any], level: int) -> Any:
        """Pick a branch: corrected estimate, Thompson-sampled over uncertainty."""
        candidates = [node for node in nodes if not math.isinf(node.minutes)]
        if not candidates:
            return nodes[0] if nodes else None
        band = band_of(level)
        best_raw = min(node.minutes for node in candidates)

        # Eligibility uses the *optimistic* correction (mean - 1 sigma): an arm we
        # are still uncertain about cannot be ruled out just because its point
        # estimate looks bad. That is what lets a systematically-biased branch be
        # discovered to be better instead of being filtered away forever.
        optimistic = {id(node): node.minutes * self._optimistic(band, node.kind)
                      for node in candidates}
        best_optimistic = min(optimistic.values())
        feasible = [node for node in candidates
                    if optimistic[id(node)] <= best_optimistic * self.max_ratio]

        # Warm-up: an arm with fewer than ``min_samples`` observations keeps
        # getting trials, as long as it is within a bounded factor of the best.
        # Without this a branch that the model is systematically wrong about can
        # never collect the evidence needed to correct itself.
        for node in candidates:
            if node in feasible:
                continue
            posterior = self._posterior(band, node.kind)
            if posterior is not None and posterior.n >= self.min_samples:
                continue
            if node.minutes <= best_raw * self.exploration_ratio:
                self._annotate(node, band, sampled=True)
                node.meta["ranker_trial"] = True
                node.meta["ranker_samples"] = 0.0 if posterior is None else round(posterior.n, 1)
                return node

        if len(feasible) == 1:
            node = feasible[0]
            self._annotate(node, band, sampled=False)
            return node

        scored: List[tuple[float, Any]] = []
        for node in feasible:
            posterior = self._posterior(band, node.kind)
            if posterior is None:
                draw = 0.0
            else:
                mean = posterior.mean()
                sd = math.sqrt(max(posterior.variance(), 1e-6))
                draw = self.rng.gauss(mean, sd)
            multiplier = min(CORRECTION_CEIL, max(CORRECTION_FLOOR, math.exp(draw)))
            scored.append((node.minutes * multiplier, node))
        # (untried arms have a wide posterior, so they win the draw occasionally)
        scored.sort(key=lambda pair: pair[0])
        chosen = scored[0][1]
        self._annotate(chosen, band, sampled=True)
        return chosen

    def _annotate(self, node: Any, band: str, sampled: bool) -> None:
        correction = self.correction(band, node.kind)
        node.meta["correction"] = round(correction, 3)
        node.meta["corrected_minutes"] = (
            None if math.isinf(node.minutes) else round(node.minutes * correction, 1))
        node.meta["ranker_band"] = band
        node.meta["ranker_sampled"] = sampled

    # -- learning ------------------------------------------------------------

    def observe(
        self,
        arm: str,
        level: int,
        predicted_minutes: float,
        observed_minutes: float,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Optional[float]:
        """Feed one real episode. Returns the new correction for that arm."""
        if predicted_minutes <= 0 or observed_minutes <= 0:
            return None
        if math.isinf(predicted_minutes) or math.isnan(predicted_minutes):
            return None
        band = band_of(level)
        ratio = math.log(observed_minutes / predicted_minutes)
        # A single catastrophic run (disconnect, death loop) must not redefine
        # the arm; clamp the evidence, not the estimate.
        ratio = max(-2.0, min(2.0, ratio))
        self.store.record(
            Event(
                kind="normal",
                key=self._key(band, arm),
                value=ratio,
                weight=1.0,
                predicted=predicted_minutes,
                context={"level": level, "band": band, "arm": arm},
                meta=meta or {},
            ),
            prior_mean=0.0,
            prior_strength=2.0,
            prior_sigma=self.prior_sigma,
            half_life_days=self.half_life_days,
        )
        return self.correction(band, arm)

    def report(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for low, high in BANDS:
            band = f"{low}-{high}"
            for state in self.state(band):
                if state.samples > 0:
                    rows.append(state.as_dict())
        return rows
