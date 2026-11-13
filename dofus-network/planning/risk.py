"""Risk-sensitive ranking: never rank by the mean of a distribution you do not trust.

The problem
-----------
`CostModel.kamas_per_minute` returns the best *expected* rate, and `Planner` picks the
node with the smallest *expected* minutes. Both are means. A mean is the right statistic
only when the outcome is symmetric and the estimate is tight, and neither holds here:

* A 0.6% drop on a 40-kill-per-minute monster has a **mean** time-to-drop that is
  reached by almost nobody. The distribution of waiting time is geometric, so its right
  tail is the whole story: a 1-in-20 dry streak costs twenty times the mean.
* An estimate built on `calibrated=False` (no measurements yet) has a mean that is
  literally a prior. Ranking by it means the planner prefers whatever it knows least
  about, because an unmeasured option's prior is usually optimistic.

Two statistics fix this without changing the model:

* **CVaR** (conditional value at risk, the mean of the worst ``tail`` fraction). For a
  duration we take the *upper* tail -- the slow runs -- because a plan is judged by how
  bad its bad days are. CVaR is coherent (subadditive, monotone, translation
  invariant), unlike VaR, which is why it is the one worth computing.
* **Thompson sampling**: draw one sample from each option's posterior and take the best
  draw. It explores in proportion to uncertainty instead of by an epsilon schedule, and
  it needs no tuning constant at all -- the exploration *is* the posterior's width, so
  it disappears on its own as evidence arrives.

What this module deliberately does not do
-----------------------------------------
It does not replace the posteriors or re-fit anything. `planning.experience` already
carries Beta / Gamma / Log-Normal / Normal posteriors with the right priors; this only
adds ``sample()`` to them (a closed-form draw, no MCMC) and the statistics on top.

Measured 2026-11-13 on the shipped KB, goal 1001 x3 at level 60: see
``planning/test_risk.py`` for the analytic checks and ``risk_report()`` for the
side-by-side on a real board.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np

# A plan is judged by its bad days. The worst tenth is a defensible default: it is the
# tail a player actually notices, and with fewer than ten samples it degrades to the
# single worst draw rather than inventing resolution it does not have.
DEFAULT_TAIL = 0.1

# How much a unit of tail cost is worth against a unit of expected cost. 1.0 means
# "I would trade one expected minute for one worst-decile minute", which is the
# neutral reading; higher values buy safety.
DEFAULT_LAMBDA = 1.0


# ---------------------------------------------------------------------------
# The two statistics
# ---------------------------------------------------------------------------

def cvar(samples: Sequence[float], tail: float = DEFAULT_TAIL,
         upper: bool = True) -> float:
    """Mean of the worst ``tail`` fraction of ``samples``.

    ``upper=True`` is the slow tail (durations, costs): the mean of the largest
    ``tail`` fraction. ``upper=False`` is the bad tail for a quantity you want to be
    large (kamas per minute, drop rate): the mean of the smallest.

    Degenerate inputs are answered rather than raised on: an empty sample returns
    ``inf`` for a duration (nothing known is not the same as nothing wrong) and ``-inf``
    for a reward, so a caller that has no evidence never wins by default.
    """
    values = np.asarray([v for v in samples if v is not None and np.isfinite(v)],
                        dtype=float)
    if values.size == 0:
        return float("inf") if upper else float("-inf")
    fraction = min(max(float(tail), 1e-9), 1.0)
    count = max(1, int(math.ceil(values.size * fraction)))
    ordered = np.sort(values)
    worst = ordered[-count:] if upper else ordered[:count]
    return float(worst.mean())


def risk_score(samples: Sequence[float], tail: float = DEFAULT_TAIL,
               lam: float = DEFAULT_LAMBDA, upper: bool = True) -> float:
    """``mean + lam * CVaR`` -- one number that punishes a fat bad tail.

    For a duration ``upper=True`` so a long tail adds cost. For a reward
    ``upper=False`` and the tail is subtracted, so a wide downside subtracts.
    """
    values = np.asarray([v for v in samples if v is not None and np.isfinite(v)],
                        dtype=float)
    if values.size == 0:
        return float("inf")
    mean = float(values.mean())
    tail_cost = cvar(values, tail=tail, upper=upper)
    if upper:
        return mean + lam * (tail_cost - mean)
    return mean - lam * (mean - tail_cost)


def thompson_pick(samplers: Iterable[Any], rng: Optional[np.random.Generator] = None,
                  lower_is_better: bool = True) -> int:
    """Index of the option whose single posterior draw is best.

    Each element is either a callable returning one float, or a ``Posterior`` with a
    ``sample`` method. Exploration comes from the width of the draw, so an option with
    no evidence gets a wide draw and wins sometimes; an option with twenty
    measurements draws near its mean and wins only on merit.
    """
    generator = rng if rng is not None else np.random.default_rng()
    draws: List[float] = []
    for option in samplers:
        value = option.sample(1, generator)[0] if hasattr(option, "sample") else option()
        draws.append(float(value))
    if not draws:
        return -1
    if not np.all(np.isfinite(draws)):
        finite = [i for i, d in enumerate(draws) if np.isfinite(d)]
        if not finite:
            return 0
        draws = [d if np.isfinite(d) else float("inf") for d in draws]
    return int(np.argmin(draws) if lower_is_better else np.argmax(draws))


# ---------------------------------------------------------------------------
# Empirical-Bayes shrinkage across level bands
# ---------------------------------------------------------------------------

def shrink(estimate: float, n: float, family: float, strength: float = 8.0) -> float:
    """Pull a thin estimate toward its family mean by its own sample count.

    This is the conjugate form of the James-Stein idea: the weight on the local
    estimate is ``n / (n + strength)``, so eight observations buy half the say and
    two buy a fifth. It is what stops a monster met twice from being ranked on two
    fights while its family -- twelve monsters at the same level band, a thousand
    fights -- is ignored.

    ``strength`` is in the same units as ``n``: it is "how many observations the
    family mean is worth".
    """
    total = max(0.0, float(n)) + max(0.0, float(strength))
    if total <= 0:
        return float(family)
    if not np.isfinite(estimate):
        return float(family)
    return (max(0.0, float(n)) * float(estimate)
            + max(0.0, float(strength)) * float(family)) / total


def band_of(level: int, width: int = 20) -> str:
    """The level band a monster belongs to, e.g. ``"40-59"``."""
    level = max(0, int(level))
    low = (level // max(1, int(width))) * max(1, int(width))
    return "%d-%d" % (low, low + max(1, int(width)) - 1)


def family_mean(values: Dict[str, float], weights: Optional[Dict[str, float]] = None) -> float:
    """Weighted mean over a family, ignoring non-finite members."""
    usable = {k: float(v) for k, v in values.items() if np.isfinite(v)}
    if not usable:
        return 0.0
    if weights is None:
        return float(np.mean(list(usable.values())))
    total = sum(max(0.0, weights.get(k, 0.0)) for k in usable)
    if total <= 0:
        return float(np.mean(list(usable.values())))
    return float(sum(usable[k] * max(0.0, weights.get(k, 0.0)) for k in usable) / total)


# ---------------------------------------------------------------------------
# A report on a real board, so the difference is a number and not an opinion
# ---------------------------------------------------------------------------

def risk_report(cost: Any, player_level: int, top_n: int = 8,
                tail: float = DEFAULT_TAIL, lam: float = DEFAULT_LAMBDA,
                draws: int = 4000, seed: int = 20261113) -> Dict[str, Any]:
    """Rank the same farms by expected minutes and by risk-adjusted minutes.

    ``cost`` is the ``CostModel`` (it owns ``farm_estimates`` and the experience
    store). The quantity compared is minutes per 1,000 kamas, because a rate is the
    reciprocal of a time and the slow tail of the rate is the fast tail of the time.

    An option with no measured kamas-per-minute posterior is drawn from a log-normal
    band around its prior rate rather than from a point -- that band *is* the
    uncertainty a `calibrated=False` estimate carries, and pretending it is a point is
    what makes the mean look safe.
    """
    rng = np.random.default_rng(seed)
    farms = cost.farm_estimates(player_level, top_n=top_n)
    if not farms:
        return {"rows": [], "by_mean": [], "by_risk": [], "disagreement": 0,
                "tail": tail, "lambda": lam}
    rows: List[Dict[str, Any]] = []
    for monster, rate in farms:
        if rate <= 0:
            continue
        posterior = None
        if getattr(cost, "experience", None) is not None:
            posterior = cost.experience.get(cost.kpm_key(monster.id, player_level))
        measured = posterior is not None and posterior.confidence() >= 0.25
        if measured:
            per_minute = np.asarray(posterior.sample(draws, rng), dtype=float)
        else:
            # Prior band: +-1 sigma multiplicative, the same shape the travel model
            # uses. Wider than any measured posterior, which is the point.
            per_minute = float(rate) * np.exp(rng.normal(0.0, 0.5, draws))
        minutes = 1000.0 / np.maximum(per_minute, 1e-9)
        rows.append({
            "monster": getattr(monster, "name", str(monster)),
            "level": getattr(monster, "level", None),
            "measured": measured,
            "kamas_per_minute": round(float(rate), 1),
            "expected_minutes_per_1000": round(float(np.mean(minutes)), 2),
            "cvar_minutes_per_1000": round(cvar(minutes, tail=tail, upper=True), 2),
            "risk_adjusted": round(risk_score(minutes, tail=tail, lam=lam, upper=True), 2),
        })
    by_mean = sorted(rows, key=lambda r: r["expected_minutes_per_1000"])
    by_risk = sorted(rows, key=lambda r: r["risk_adjusted"])
    moved = [a["monster"] for a, b in zip(by_mean, by_risk) if a["monster"] != b["monster"]]
    return {"rows": rows,
            "by_mean": [r["monster"] for r in by_mean],
            "by_risk": [r["monster"] for r in by_risk],
            "disagreement": len(moved),
            "moved": moved,
            "measured_count": sum(1 for r in rows if r["measured"]),
            "tail": tail, "lambda": lam}
