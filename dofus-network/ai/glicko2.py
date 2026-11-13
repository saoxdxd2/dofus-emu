"""Glicko-2 rating for release-gate policy comparisons.

Why a rating system in a release gate
-------------------------------------
`ai/release_gate.py` reports one accuracy number per split. A single number hides a
regression on a small but important family: a model can gain 2 points overall by
getting better at `hdv.scan` (1,088 rows) while getting much worse at `fight.join`
(37 rows). "Beat the baseline on average" is not a release criterion; "beat it on
every archetype we can measure" is.

Glicko-2 supplies the second half of that: a rating **and a rating deviation** per
archetype. The deviation is what makes a small archetype honest -- 12 matches on
`fight.join` must not look as certain as 1,088 on `hdv.scan`. A gate on the *lower*
confidence bound `r - k*RD` automatically demands more evidence from small families.

Match protocol
--------------
Each evaluation case is one match between the challenger policy and a fixed
reference player (the `rule` baseline), whose rating is pinned at 1500 with a small
RD. The outcome is:

    1.0   challenger's call is correct, reference's is not
    0.0   reference's is correct, challenger's is not
    0.5   both correct or both wrong

so ties are retained instead of discarded (Glicko-2 accepts half-points). One rating
period is one archetype; all matches in that archetype are applied simultaneously,
exactly as the Glicko-2 period model requires. Opponent ratings are not updated --
they are a fixed measuring stick, not a competitor being trained.

This is a rating over *cases*, not over time, so the volatility term is not a claim
about a player's form changing between sessions. It is used because the full
Glicko-2 update (including the volatility solve) is the well-specified version of
the algorithm; with τ = 0.5 and one period it barely moves σ.

Reference
---------
Mark E. Glickman, "Example of the Glicko-2 system"
(http://www.glicko.net/glicko/glicko2.pdf), used verbatim as
`ai/test_glicko2.py::test_the_published_example`:

    r = 1500, RD = 200, sigma = 0.06, tau = 0.5
    opponents (r, RD, score): (1400, 30, 1), (1550, 100, 0), (1700, 300, 0)
    ->  r' = 1464.06,  RD' = 151.52,  sigma' = 0.05999
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Iterable, List, Sequence, Tuple

SCALE = 173.7178
DEFAULT_RATING = 1500.0
DEFAULT_RD = 350.0
DEFAULT_SIGMA = 0.06
DEFAULT_TAU = 0.5
EPSILON = 1e-6
MAX_ITERATIONS = 100

Match = Tuple[float, float, float]  # (opponent rating, opponent RD, score in [0, 1])


@dataclass(frozen=True)
class Rating:
    """A Glicko-2 rating in the published (not internal) units."""

    r: float = DEFAULT_RATING
    rd: float = DEFAULT_RD
    sigma: float = DEFAULT_SIGMA

    def lower(self, k: float = 2.0) -> float:
        """The `k`-sigma lower confidence bound used by the release gate."""
        return self.r - k * self.rd


def expected(mu: float, mu_j: float, phi_j: float) -> float:
    """E(mu, mu_j, phi_j) from step 3 of the paper."""
    g = 1.0 / math.sqrt(1.0 + 3.0 * phi_j * phi_j / (math.pi * math.pi))
    return 1.0 / (1.0 + math.exp(-g * (mu - mu_j)))


def _g(phi: float) -> float:
    return 1.0 / math.sqrt(1.0 + 3.0 * phi * phi / (math.pi * math.pi))


def _new_sigma(sigma: float, phi: float, v: float, delta: float,
               tau: float = DEFAULT_TAU) -> float:
    """Step 5: the Illinois/Newton solve for the new volatility.

    The bracketing branch (`delta^2 > phi^2 + v` vs. the `a - k*tau` search) is
    required; starting Newton at `a` alone diverges for large `|delta|`.
    """
    a = math.log(sigma * sigma)
    d2 = delta * delta
    phi2 = phi * phi

    def f(x: float) -> float:
        # rewritten as (ex/s) * (d2 - s) / (2*s) rather than ex*(...)/(2*s^2):
        # squaring a large s overflows even though the ratio itself is bounded
        ex = math.exp(min(x, 700.0))
        s = phi2 + v + ex
        return (ex / s) * (d2 - s) / (2.0 * s) - (x - a) / (tau * tau)

    if d2 > phi2 + v:
        B = math.log(d2 - phi2 - v)
    else:
        k = 1
        while f(a - k * tau) < 0.0:
            k += 1
            if k > MAX_ITERATIONS:
                break
        B = a - k * tau
    A = a
    fA = f(A)
    fB = f(B)
    for _ in range(MAX_ITERATIONS):
        if abs(B - A) <= EPSILON:
            break
        # Illinois variant: halve the secant step when it stalls
        denom = fB - fA
        if abs(denom) < 1e-15:
            break
        C = A + (A - B) * fA / denom
        fC = f(C)
        if fC * fB <= 0.0:
            A, fA = B, fB
        else:
            fA = fA / 2.0
        B, fB = C, fC
    return math.exp(A / 2.0)


def rate(rating: Rating, matches: Iterable[Match],
         tau: float = DEFAULT_TAU) -> Rating:
    """One rating period. `matches` may be empty: then only the RD inflates."""
    matches: List[Match] = list(matches)
    mu = (rating.r - DEFAULT_RATING) / SCALE
    phi = rating.rd / SCALE
    if not matches:
        phi_star = math.sqrt(phi * phi + rating.sigma * rating.sigma)
        return replace(rating, rd=SCALE * phi_star)

    v_inv = 0.0
    delta_sum = 0.0
    for r_j, rd_j, score in matches:
        if not 0.0 <= score <= 1.0:
            raise ValueError("score must be in [0, 1], got %r" % (score,))
        mu_j = (r_j - DEFAULT_RATING) / SCALE
        phi_j = rd_j / SCALE
        g = _g(phi_j)
        E = expected(mu, mu_j, phi_j)
        v_inv += g * g * E * (1.0 - E)
        delta_sum += g * (score - E)
    if v_inv <= 1e-10:
        # Every expectation has saturated (E*(1-E) underflowed), so the period carries
        # no Fisher information. Glicko-2's own answer to an uninformative period is
        # to inflate the deviation, not to divide by ~0 and produce a huge delta that
        # overflows the volatility solve. Measured with a 100,000-rated opponent.
        phi_star = math.sqrt(phi * phi + rating.sigma * rating.sigma)
        return replace(rating, rd=SCALE * phi_star)
    v = 1.0 / v_inv
    delta = v * delta_sum

    sigma_new = _new_sigma(rating.sigma, phi, v, delta, tau)
    phi_star = math.sqrt(phi * phi + sigma_new * sigma_new)
    phi_new = 1.0 / math.sqrt(1.0 / (phi_star * phi_star) + 1.0 / v)
    mu_new = mu + phi_new * phi_new * delta_sum
    return Rating(r=SCALE * mu_new + DEFAULT_RATING,
                  rd=SCALE * phi_new,
                  sigma=sigma_new)


def rate_periods(rating: Rating, periods: Sequence[Iterable[Match]],
                 tau: float = DEFAULT_TAU) -> List[Rating]:
    """Independent period ratings, one per archetype, all from the same prior."""
    return [rate(rating, matches, tau) for matches in periods]


def win_rate(matches: Sequence[Match]) -> float:
    """Plain score share, reported alongside the rating so the two can be compared."""
    if not matches:
        return 0.5
    return sum(m[2] for m in matches) / len(matches)
