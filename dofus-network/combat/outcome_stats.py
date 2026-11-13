"""Honest numbers for "did it win", and for "does it know whether it will".

Why this exists
---------------
`data/fight_head_production.json` reports ``network_win_rate: 0.4062`` over 200
matchups. The same file records 1 win, 157 draws and 42 losses. 0.4062 is not a
win rate: it is the mean of a score that awards 0.5 for a draw, so a policy that
draws every game scores 0.5 and a policy that never loses cannot be told from a
policy that never wins. The bootstrap interval published beside it,
``[0.3787, 0.4325]``, is the interval of that scoring convention, not of
winning, and its width says nothing about how often the fight was actually won.

Three separate quantities were being collapsed into one number:

    win rate      wins / decided games. Draws are not half-wins; they are games
                  where the outcome is unknown, and including them at half
                  weight silently answers the question nobody asked.
    draw rate     a first-class result. A policy that draws 78% of its fights
                  has a specific, diagnosable failure -- it cannot finish -- and
                  that failure disappears when draws are scored as half a win.
    calibration   whether a stated probability means anything. This is the one
                  that matters for deciding whether to start a fight unattended,
                  and it needs a proper scoring rule, not an accuracy count.

A probability is scored with the Brier score (mean squared error against the
outcome), and compared against the base rate with the Brier skill score, which
is 0 for "always predict the base rate" and 1 for perfect. An interval on a
proportion is Wilson's, because the normal approximation fails exactly where
these counts live -- near 0 and near 1, with a handful of games.

Nothing here fits or tunes anything; it is arithmetic on results.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
import logging
logger = logging.getLogger(__name__)

DATA = Path(__file__).resolve().parent.parent / "data"


def wilson_interval(successes: int, total: int, z: float = 1.96) -> Tuple[float, float]:
    """Wilson score interval for a proportion.

    The textbook normal interval is wrong near the boundary: with 1 win in 1
    game it gives [1, 1], and with 0 in 20 it gives [0, 0]. Wilson does not.
    """
    if total <= 0:
        return (0.0, 1.0)
    p = successes / total
    denominator = 1.0 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = (z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
              / denominator)
    return (max(0.0, centre - margin), min(1.0, centre + margin))


@dataclass
class Tally:
    """Wins, losses and draws, kept apart on purpose.

    ``unresolved`` is the fourth bucket and it is not a draw. A row with no
    verdict means the session ended while the fight was still open
    (``status: session_end`` in `trajectories/fight_outcomes.jsonl`); counting it
    as a draw would inflate the draw rate with fights that may have been won, and
    counting it as a win is what an xp-delta verdict used to do.
    """

    wins: int = 0
    losses: int = 0
    draws: int = 0
    unresolved: int = 0

    @classmethod
    def from_outcome_rows(cls, rows: Iterable[Dict[str, Any]]) -> "Tally":
        """From `trajectories/fight_outcomes.jsonl`, one row per resolved fight.

        Only ``kind == "fight_outcome"`` rows carry a verdict; the ``fight_start``
        rows are the estimate written before the fight and would double-count.
        """
        tally = cls()
        for row in rows:
            if not isinstance(row, dict) or row.get("kind") != "fight_outcome":
                continue
            won = row.get("won")
            if won is True:
                tally.wins += 1
            elif won is False:
                tally.losses += 1
            elif won is None:
                tally.unresolved += 1
            else:
                tally.draws += 1
        return tally

    @classmethod
    def from_scores(cls, scores: Iterable[float], win_at: float = 0.75,
                    loss_at: float = 0.25) -> "Tally":
        """Classify matchup scores with the same thresholds the file uses.

        ``network_score`` is four-level, not three: 0.75 is a partial win and
        0.25 a partial loss, and the published summary counts them as wins and
        losses respectively (1 + 157 + 42 = 200 over the recorded scores).
        Using a 0.5 boundary instead reported 0 wins and 34 losses for the same
        file, which is a different claim about the same games.
        """
        tally = cls()
        for score in scores:
            if score >= win_at:
                tally.wins += 1
            elif score <= loss_at:
                tally.losses += 1
            else:
                tally.draws += 1
        return tally

    @property
    def games(self) -> int:
        return self.wins + self.losses + self.draws

    @property
    def decided(self) -> int:
        return self.wins + self.losses

    @property
    def win_rate(self) -> Optional[float]:
        """Of the games that ended. ``None`` when none did."""
        return None if not self.decided else self.wins / self.decided

    @property
    def draw_rate(self) -> float:
        return 0.0 if not self.games else self.draws / self.games

    @property
    def score(self) -> float:
        """The half-point-per-draw convention, named for what it is."""
        return 0.0 if not self.games else (self.wins + 0.5 * self.draws) / self.games

    def summary(self) -> Dict[str, Any]:
        low, high = wilson_interval(self.wins, self.decided)
        return {
            "games": self.games,
            "wins": self.wins,
            "losses": self.losses,
            "draws": self.draws,
            "unresolved": self.unresolved,
            "decided": self.decided,
            "win_rate_decided": self.win_rate,
            "win_rate_ci95": [round(low, 4), round(high, 4)] if self.decided else None,
            "draw_rate": round(self.draw_rate, 4),
            "score_half_point_per_draw": round(self.score, 4),
        }

    def line(self) -> str:
        """One line that cannot be misread as a bare win rate."""
        if not self.decided:
            return ("no decided fight yet (%d open, %d drawn)"
                    % (self.unresolved, self.draws))
        low, high = wilson_interval(self.wins, self.decided)
        return ("%d/%d decided fights won = %.1f%% (95%% CI %.1f-%.1f%%), "
                "%d drawn, %d open"
                % (self.wins, self.decided, 100.0 * self.wins / self.decided,
                   100.0 * low, 100.0 * high, self.draws, self.unresolved))


def brier(probabilities: Sequence[float], outcomes: Sequence[int]) -> Optional[float]:
    """Mean squared error of a probability against a 0/1 outcome. Lower is better."""
    if not probabilities or len(probabilities) != len(outcomes):
        return None
    total = 0.0
    for p, y in zip(probabilities, outcomes):
        total += (float(p) - float(y)) ** 2
    return total / len(probabilities)


def brier_skill_score(probabilities: Sequence[float],
                      outcomes: Sequence[int]) -> Optional[float]:
    """1 - Brier / Brier(base rate). Zero means "no better than the base rate"."""
    if not outcomes:
        return None
    reference = brier([sum(outcomes) / len(outcomes)] * len(outcomes), outcomes)
    score = brier(probabilities, outcomes)
    if reference is None or score is None or reference <= 0:
        return None
    return 1.0 - score / reference


def calibration_table(probabilities: Sequence[float], outcomes: Sequence[int],
                      bins: int = 5) -> List[Dict[str, Any]]:
    """Observed frequency against predicted probability, per bucket.

    A calibrated model sits on the diagonal. The table is what makes
    "``mean_probability: 0.9237``" checkable: a mean probability of 0.92 over
    fights it mostly draws is a claim about the world, and this says whether the
    world agreed.
    """
    if not probabilities:
        return []
    rows: List[Dict[str, Any]] = []
    for index in range(bins):
        low = index / bins
        high = (index + 1) / bins
        members = [(p, y) for p, y in zip(probabilities, outcomes)
                   if (low <= p < high) or (index == bins - 1 and p == 1.0)]
        if not members:
            continue
        rows.append({
            "bucket": "[%.1f, %.1f)" % (low, high),
            "n": len(members),
            "predicted": round(sum(p for p, _y in members) / len(members), 4),
            "observed": round(sum(y for _p, y in members) / len(members), 4),
        })
    return rows


def report_live(path: Path) -> int:
    """The fights this account actually fought, from `fight_outcomes.jsonl`.

    The live log and the production benchmark disagree by two orders of
    magnitude, and both were being called "the success rate". On the recorded
    log the live policy won 13 of 13 decided fights against low-level monsters
    while the self-play benchmark scored 1 win in 200 -- different questions,
    and a bare percentage makes them look like a contradiction.

    The interval is the point. 13 of 13 is not 100%: Wilson puts it at
    0.77-1.00, and that lower bound is what an unattended session should be
    planned against.
    """
    rows = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    tally = Tally.from_outcome_rows(rows)
    logger.debug("=== %s (fights actually fought)" % path.name)
    logger.debug("  %s" % tally.line())
    logger.debug("  resolved rows       : %d" % tally.games)
    if tally.unresolved:
        logger.debug("  open at session end : %d  (no verdict -- not draws, not wins)"
              % tally.unresolved)
    if tally.decided:
        low, high = wilson_interval(tally.wins, tally.decided)
        logger.debug("  win rate            : %.4f   95%% CI [%.4f, %.4f]"
              % (tally.win_rate, low, high))
        logger.debug("  ...which is not 100%% even at %d/%d: the lower bound is the"
              % (tally.wins, tally.decided))
        logger.debug("  number to plan an unattended session against.")
    return 0


def report(path: Optional[Path] = None) -> int:
    path = path or (DATA / "fight_head_production.json")
    if not path.exists():
        logger.debug("no %s" % path)
        return 1
    if path.suffix == ".jsonl":
        return report_live(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    summary = payload.get("summary") or {}
    results = payload.get("results") or []
    scores = [float(row.get("network_score") or 0.0) for row in results]
    tally = Tally.from_scores(scores)

    histogram: Dict[float, int] = {}
    for score in scores:
        histogram[score] = histogram.get(score, 0) + 1

    logger.debug("=== %s" % path.name)
    logger.debug("  as published        : network_win_rate=%s  ci95=%s"
          % (summary.get("network_win_rate"), summary.get("win_rate_bootstrap_ci95")))
    logger.debug("  the file's own tally: %s wins, %s draws, %s losses"
          % (summary.get("wins"), summary.get("draws"), summary.get("losses")))
    logger.debug()
    logger.debug("  matchup scores      : %s" % ", ".join(
        "%g x%d" % (score, count) for score, count in sorted(histogram.items())))
    logger.debug("  (0.75 is a partial win, 0.25 a partial loss -- the score is four-level)")
    logger.debug()
    logger.debug("  games               : %d" % tally.games)
    logger.debug("  wins / losses       : %d / %d   (decided: %d)"
          % (tally.wins, tally.losses, tally.decided))
    logger.debug("  draws               : %d  (%.1f%% of games)"
          % (tally.draws, 100 * tally.draw_rate))
    if tally.win_rate is None:
        logger.debug("  win rate (decided)  : undefined -- no game was decided")
    else:
        low, high = wilson_interval(tally.wins, tally.decided)
        logger.debug("  win rate (decided)  : %.4f   95%% CI [%.4f, %.4f] over %d decided games"
              % (tally.win_rate, low, high, tally.decided))
    mean_score = (sum(scores) / len(scores)) if scores else 0.0
    logger.debug("  mean matchup score  : %.4f   <- the number published as a win rate"
          % mean_score)
    logger.debug()
    logger.debug("  The published figure is the score, not the win rate: a policy that")
    logger.debug("  draws every fight scores 0.5 and never wins one. Of %d decided games"
          % tally.decided)
    logger.debug("  the policy won %d, and the draw rate above is the diagnosable fact the"
          % tally.wins)
    logger.debug("  score hides -- a fight that neither side finishes is not a half-win.")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, default=None)
    return report(parser.parse_args(argv).file)


if __name__ == "__main__":
    raise SystemExit(main())
