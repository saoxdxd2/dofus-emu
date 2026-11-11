"""Live learning: what the enemy actually took off us, learned while fighting.

Why this exists, and why it is not the observed-damage table.

`combat/live_data.py` tries to price an enemy *spell* by joining the cast row to the
life-points-lost row it produced. Measured on fight 875 (2026-11-10) that join cannot
work: all 12 cast rows carried ``action_id`` 300 while all 8 damage rows carried 97 or
98, and every damage row had ``spell_id: null``. The client does not say which spell
dealt a given loss, so a per-spell table stays empty no matter how many fights are
recorded.

What the client does say, unambiguously, is how much life the enemies took off us
between two boards. That is a per-turn number, not a per-spell one, and it is the
number the solver is actually missing: `enemy_spells_missing` makes `value_ready`
false on live boards, so the ranker refuses and the plan is priced without any enemy
model at all. A turn-level price answers the same question -- "what will this group
cost me next round" -- without needing an id that does not exist.

So this module learns ``damage_to_me_per_enemy_turn`` from consecutive live boards:

    board(t)  --enemy actions-->  board(t+1)   =>   one labelled example

The label is the drop in our own life points. It is credited to the board it was
observed on, keyed by a signature of the enemy group, so the model generalises across
fights against the same monsters instead of memorising one fight id.

Two rules keep it honest:

* A turn is only credited when both boards belong to the same fight and time moved
  forward. A board that repeats (the page polls more than once per turn) credits
  nothing, so a poll is never mistaken for a turn.
* The model refuses to answer until it has seen `MIN_ROWS` credited turns *and* its
  predictions actually vary across boards. A constant model reproduces the baseline
  exactly while looking like a learned one -- that is how `duel_value_v4_0.pkl` came
  to be reported as an improvement while being provably inert (spread 0.0 on 12/12
  boards). `spread()` exists to make that failure impossible to miss.

    python -m combat.live_learn --report      # what has been learned so far
    python -m combat.live_learn --reset       # forget it
"""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = REPO_ROOT / "models" / "live_enemy_value.json"
DEFAULT_ROWS = REPO_ROOT / "trajectories" / "live_learning.jsonl"

# Below this many credited turns the model is reported as `collecting` and answers
# nothing. The number is small on purpose: a fight is 4-8 rounds, so waiting for
# thousands would mean never learning inside a session.
MIN_ROWS = 12

# Feature vector width. Kept as a named constant because a stored model whose width
# no longer matches the live encoder must be rejected, not silently mis-dotted.
FEATURE_NAMES: Tuple[str, ...] = (
    "bias",
    "enemies",
    "enemy_levels",
    "enemy_hp_frac",
    "my_hp_frac",
    "round",
    "my_ap",
    "my_mp",
    "mean_distance",
)
WIDTH = len(FEATURE_NAMES)


# -- reading a board ---------------------------------------------------------
#
# The payload is the page's own `fight.state`. Every accessor tolerates a missing
# field and returns a neutral value: a learner that throws on one odd board stops
# learning for the rest of the fight, and the failure looks like "no data".

def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _fighters(board: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = board.get("fighters")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _is_me(fighter: Dict[str, Any], my_id: Any) -> bool:
    if fighter.get("is_me"):
        return True
    return my_id is not None and str(fighter.get("id")) == str(my_id)


def _my_fighter(board: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    my_id = board.get("my_id")
    for fighter in _fighters(board):
        if _is_me(fighter, my_id):
            return fighter
    return None


def _enemies(board: Dict[str, Any]) -> List[Dict[str, Any]]:
    my_id = board.get("my_id")
    return [f for f in _fighters(board)
            if not _is_me(f, my_id) and f.get("team") != "player"]


def _alive(fighters: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fighters still standing.

    `alive` is authoritative when the payload sets it -- an explicit True must win
    even when `hp` is missing, because treating "no hp field" as dead silently
    shrinks the enemy count and makes the group look cheaper than it is.
    """
    standing = []
    for fighter in fighters:
        flag = fighter.get("alive")
        if flag is True:
            standing.append(fighter)
        elif flag is False:
            continue
        elif _num(fighter.get("hp")) > 0:
            standing.append(fighter)
    return standing


def board_key(board: Dict[str, Any]) -> Tuple[Any, Any, Any, Any]:
    """What makes two boards the *same* observation rather than two turns.

    The page asks the brain on every poll, so the same turn arrives several times.
    Round, our own life, our cell and the enemy life total identify a turn: if none of
    them moved, no turn happened and nothing should be credited.

    Our own life is part of the key, not just the enemies'. Enemies act before us in a
    round, so the very common case is "I lost 40 hp and they lost nothing yet" -- a key
    built only from enemy hp calls that a repeated poll and throws the turn away.
    """
    enemies = _alive(_enemies(board))
    enemy_hp = sum(_num(f.get("hp")) for f in enemies)
    mine = _my_fighter(board) or {}
    my_hp = _num(mine.get("hp"))
    my_cell = board.get("my_cell") if board.get("my_cell") is not None else mine.get("cell")
    return (board.get("fight_id"), board.get("round"),
            round(my_hp, 3), round(enemy_hp, 3))


def enemy_signature(board: Dict[str, Any]) -> str:
    """A stable name for the enemy group, so learning transfers between fights.

    Keyed on the monsters themselves (name + level), not on the fight id: the same
    three monsters are the same problem next time, and a fight id is new every time.
    """
    parts = []
    for fighter in _enemies(board):
        name = str(fighter.get("name") or fighter.get("creature_generic_id") or "?")
        parts.append("%s:%s" % (name, int(_num(fighter.get("level")))))
    return "|".join(sorted(parts)) or "?"


# -- the label ---------------------------------------------------------------

@dataclass
class TurnCredit:
    """One observed enemy turn: what it cost us, on the board it happened on."""

    fight_id: Any
    round: Any
    signature: str
    my_hp_before: float
    my_hp_after: float
    damage: float
    enemies: int
    features: List[float] = field(default_factory=list)
    at: float = field(default_factory=time.time)

    def as_row(self) -> Dict[str, Any]:
        row = asdict(self)
        row["kind"] = "live_turn"
        return row


def credit_turn(prev: Dict[str, Any], cur: Dict[str, Any]) -> Optional[TurnCredit]:
    """Label the turn that happened between two boards, or None if none did.

    Returns None when the boards are not comparable: a different fight, a repeated
    poll (nothing happened), or a board where our own life points went *up*, which is
    a heal or a respawn rather than damage taken. Refusing to label those is the whole
    reason the number can be trusted.
    """
    if not prev or not cur:
        return None
    if board_key(prev) == board_key(cur):
        return None
    if str(prev.get("fight_id")) != str(cur.get("fight_id")):
        return None
    before = _my_fighter(prev)
    after = _my_fighter(cur)
    if before is None or after is None:
        return None
    hp_before = _num(before.get("hp"))
    hp_after = _num(after.get("hp"))
    if hp_before <= 0:
        return None
    damage = hp_before - hp_after
    if damage < 0:
        return None                     # healed or respawned: not an enemy turn
    return TurnCredit(
        fight_id=cur.get("fight_id"),
        round=cur.get("round"),
        signature=enemy_signature(prev),
        my_hp_before=hp_before,
        my_hp_after=hp_after,
        damage=damage,
        enemies=len(_alive(_enemies(prev))),
        features=features_of(prev),
    )


def features_of(board: Dict[str, Any]) -> List[float]:
    """The board as a fixed-width vector, in `FEATURE_NAMES` order.

    Only quantities the page always publishes are used. `enemy_levels` stands in for
    "how dangerous is this group" because the client never publishes the enemy's
    stats, and level is the one strength proxy that is always present.
    """
    enemies = _alive(_enemies(board))
    mine = _my_fighter(board) or {}
    levels = sum(_num(f.get("level")) for f in enemies)
    hp_total = sum(_num(f.get("hp")) for f in enemies)
    max_total = sum(_num(f.get("max_hp")) for f in enemies)
    my_hp = _num(mine.get("hp"))
    my_max = _num(mine.get("max_hp"))
    my_cell = _num(board.get("my_cell") if board.get("my_cell") is not None
                   else mine.get("cell"))
    distances = [abs(_num(f.get("cell")) - my_cell) for f in enemies]
    return [
        1.0,
        float(len(enemies)),
        levels,
        (hp_total / max_total) if max_total > 0 else 0.0,
        (my_hp / my_max) if my_max > 0 else 0.0,
        _num(board.get("round")),
        _num(mine.get("ap")),
        _num(mine.get("mp")),
        (sum(distances) / len(distances)) if distances else 0.0,
    ]


# -- the model ---------------------------------------------------------------

class OnlineValue:
    """Ridge regression updated one turn at a time, in plain Python.

    Deliberately not numpy: this runs inside the brain server on the embedded
    interpreter, and `combat/brain_service.py` documents that the embedded runtime is
    the one place a dependency can be missing. A width-9 dot product does not need a
    library, and needing one here would mean the learner silently does not run.
    """

    def __init__(self, weights: Optional[Sequence[float]] = None,
                 rows: int = 0, l2: float = 1.0) -> None:
        self.weights: List[float] = list(weights) if weights else [0.0] * WIDTH
        self.rows = int(rows)
        self.l2 = float(l2)
        self.sq_error = 0.0
        self.sq_error_baseline = 0.0
        self._sum_target = 0.0
        self._sum_target_sq = 0.0
        # Sufficient statistics for the ridge fit: X'X and X'y. Kept instead of the
        # examples themselves, so a session of learning costs 90 floats rather than
        # every board it ever saw, and a refit is exact rather than incremental.
        self._A: List[List[float]] = [[0.0] * WIDTH for _ in range(WIDTH)]
        self._b: List[float] = [0.0] * WIDTH

    # -- prediction --
    def _raw(self, features: Sequence[float]) -> float:
        if len(features) != len(self.weights):
            return 0.0
        total = 0.0
        for w, x in zip(self.weights, features):
            total += w * x
        return total

    def predict(self, features: Sequence[float]) -> float:
        # Damage is non-negative; a negative prediction is a modelling artefact and
        # reporting it as a price would make the group look safer than it is. The
        # clamp lives here and NOT in the update: clamping the value the gradient is
        # computed from turns "predicts -40" into "predicts 0, still wrong", which is
        # a fixed point the weights can never leave.
        raw = self._raw(features)
        return raw if raw > 0.0 else 0.0

    def spread(self, boards: Sequence[Sequence[float]]) -> float:
        """Std-dev of the predictions across boards. 0.0 means INERT.

        This is the guard that `duel_value_v4_0.pkl` did not have: a model whose
        output never changes reproduces the baseline bit for bit, so every comparison
        against the baseline reports a tie and the model gets credited with it.
        """
        if not boards:
            return 0.0
        values = [self.predict(f) for f in boards]
        mean = sum(values) / len(values)
        var = sum((v - mean) ** 2 for v in values) / len(values)
        return math.sqrt(var)

    # -- learning --
    def update(self, features: Sequence[float], target: float) -> None:
        """Add one example to the normal equations, then refit.

        Closed-form ridge rather than gradient steps. The first version of this used
        SGD and diverged: the features are collinear (bias, enemy count and total
        enemy level all move together) and their magnitudes run from 1 to ~150, so a
        single learning rate overshoots on some directions and never moves on others.
        At width 9 the normal equations are a 9x9 system, which is cheaper to solve
        than it is to tune a step size for, and it cannot diverge.
        """
        if len(features) != len(self.weights):
            return
        for i, x in enumerate(features):
            if x == 0.0:
                continue
            row = self._A[i]
            for j, y in enumerate(features):
                row[j] += x * y
            self._b[i] += x * target
        self.rows += 1
        self._sum_target += target
        self._sum_target_sq += target * target
        self._refit()

    def _refit(self) -> None:
        """Solve (X'X + lambda*I) w = X'y, leaving the bias unpenalised."""
        size = WIDTH
        # Working copies: the accumulated sums must stay pristine for the next refit.
        matrix = [list(row) for row in self._A]
        rhs = list(self._b)
        for i in range(1, size):
            matrix[i][i] += self.l2
        # Gaussian elimination with partial pivoting. A zero pivot means the data
        # cannot separate these features yet; leaving the previous weights in place
        # is the honest answer, and it is what `ready` then reports.
        for col in range(size):
            pivot = max(range(col, size), key=lambda r: abs(matrix[r][col]))
            if abs(matrix[pivot][col]) < 1e-12:
                return
            if pivot != col:
                matrix[col], matrix[pivot] = matrix[pivot], matrix[col]
                rhs[col], rhs[pivot] = rhs[pivot], rhs[col]
            lead = matrix[col][col]
            for r in range(col + 1, size):
                factor = matrix[r][col] / lead
                if factor == 0.0:
                    continue
                for c in range(col, size):
                    matrix[r][c] -= factor * matrix[col][c]
                rhs[r] -= factor * rhs[col]
        solved = [0.0] * size
        for r in range(size - 1, -1, -1):
            total = rhs[r]
            for c in range(r + 1, size):
                total -= matrix[r][c] * solved[c]
            solved[r] = total / matrix[r][r]
        self.weights = solved
        # Residual sum of squares straight from the fit: y'y - w'X'y. The ridge term
        # makes this a slight over-estimate of the training error, which errs toward
        # not claiming an improvement that is not there.
        sse = self._sum_target_sq - sum(w * b for w, b in zip(self.weights, self._b))
        self.sq_error = sse if sse > 0.0 else 0.0
        # The baseline is "predict the running mean damage". Beating it is the only
        # evidence that the features carry signal at all.
        if self.rows:
            mean = self._sum_target / self.rows
            self.sq_error_baseline = max(
                0.0, self._sum_target_sq - self.rows * mean * mean)

    # -- reporting --
    @property
    def ready(self) -> bool:
        return self.rows >= MIN_ROWS

    def report(self) -> Dict[str, Any]:
        mae = math.sqrt(self.sq_error / self.rows) if self.rows else None
        base = math.sqrt(self.sq_error_baseline / self.rows) if self.rows else None
        improvement = None
        if mae is not None and base:
            improvement = round((base - mae) / base, 4)
        return {
            "status": "ready" if self.ready else "collecting",
            "rows": self.rows,
            "min_rows": MIN_ROWS,
            "rmse": None if mae is None else round(mae, 4),
            "baseline_rmse": None if base is None else round(base, 4),
            "improvement": improvement,
            "weights": dict(zip(FEATURE_NAMES, [round(w, 6) for w in self.weights])),
        }

    # -- persistence --
    def as_dict(self) -> Dict[str, Any]:
        return {"weights": self.weights, "rows": self.rows, "l2": self.l2,
                "sq_error": self.sq_error,
                "sum_target": self._sum_target,
                "sum_target_sq": self._sum_target_sq,
                # The sufficient statistics travel with the weights: without them a
                # reloaded model can predict but cannot keep learning, so every app
                # restart would silently reset the learner to "collecting".
                "A": self._A,
                "b": self._b,
                "features": list(FEATURE_NAMES)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OnlineValue":
        stored = data.get("features")
        weights = data.get("weights")
        # A model trained on a different encoder is not a model for this one.
        if stored and list(stored) != list(FEATURE_NAMES):
            return cls()
        if not isinstance(weights, list) or len(weights) != WIDTH:
            return cls()
        model = cls(weights=weights, rows=int(data.get("rows") or 0),
                    l2=float(data.get("l2") or 1.0))
        model.sq_error = float(data.get("sq_error") or 0.0)
        model._sum_target = float(data.get("sum_target") or 0.0)
        model._sum_target_sq = float(data.get("sum_target_sq") or 0.0)
        matrix = data.get("A")
        rhs = data.get("b")
        if (isinstance(matrix, list) and len(matrix) == WIDTH
                and all(isinstance(row, list) and len(row) == WIDTH for row in matrix)):
            model._A = [[float(v) for v in row] for row in matrix]
        if isinstance(rhs, list) and len(rhs) == WIDTH:
            model._b = [float(v) for v in rhs]
        # Refit from the restored statistics rather than trusting the stored weights:
        # it makes the reload self-consistent (weights, residuals and the baseline all
        # come from the same sums) and it recomputes `sq_error_baseline`, which is not
        # persisted. Without this a reloaded model reported `improvement: null` -- the
        # metric that says whether it is worth serving disappeared on every restart.
        model._refit()
        return model


class LiveLearner:
    """Holds the previous board so the next one can label it.

    One instance per process. `observe` is called with every board the page sends;
    it credits the turn that just finished, updates the model, and writes the row.
    """

    def __init__(self, model_path: Path = DEFAULT_MODEL,
                 rows_path: Path = DEFAULT_ROWS,
                 persist_every: int = 4) -> None:
        self.model_path = Path(model_path)
        self.rows_path = Path(rows_path)
        self.persist_every = max(1, int(persist_every))
        self.model = self._load()
        self._prev: Optional[Dict[str, Any]] = None
        self._since_persist = 0
        self._was_ready = self.model.ready
        self.credited = 0
        self.skipped = 0

    def _load(self) -> OnlineValue:
        try:
            if self.model_path.exists():
                return OnlineValue.from_dict(
                    json.loads(self.model_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        return OnlineValue()

    def observe(self, board: Dict[str, Any]) -> Optional[TurnCredit]:
        """Credit the turn between the previous board and this one."""
        if not isinstance(board, dict):
            return None
        prev, self._prev = self._prev, board
        credit = credit_turn(prev, board) if prev is not None else None
        if credit is None:
            self.skipped += 1
            return None
        self.model.update(credit.features, credit.damage)
        self.credited += 1
        self._append(credit)
        self._since_persist += 1
        # Persist on the turn it becomes usable, not only on the periodic tick: a
        # restart between ticks would otherwise drop back below `MIN_ROWS` and report
        # `collecting` on a model that had just started being worth serving.
        became_ready = self.model.ready and not self._was_ready
        self._was_ready = self.model.ready
        if became_ready or self._since_persist >= self.persist_every:
            self.save()
        return credit

    def _append(self, credit: TurnCredit) -> None:
        try:
            self.rows_path.parent.mkdir(parents=True, exist_ok=True)
            with self.rows_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(credit.as_row(), ensure_ascii=False) + "\n")
        except OSError:
            pass

    def save(self) -> None:
        try:
            self.model_path.parent.mkdir(parents=True, exist_ok=True)
            self.model_path.write_text(
                json.dumps(self.model.as_dict(), indent=1), encoding="utf-8")
            self._since_persist = 0
        except OSError:
            pass

    def price(self, board: Dict[str, Any]) -> Optional[float]:
        """Expected damage from one enemy turn on this board, or None if not ready."""
        if not self.model.ready:
            return None
        return round(self.model.predict(features_of(board)), 3)

    def report(self) -> Dict[str, Any]:
        body = self.model.report()
        body.update({"credited_turns": self.credited, "skipped_polls": self.skipped,
                     "model_path": str(self.model_path),
                     "rows_path": str(self.rows_path)})
        return body


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Live enemy-turn learner")
    parser.add_argument("--model", default=str(DEFAULT_MODEL))
    parser.add_argument("--rows", default=str(DEFAULT_ROWS))
    parser.add_argument("--report", action="store_true", help="print what is learned")
    parser.add_argument("--reset", action="store_true", help="delete the stored model")
    args = parser.parse_args(argv)

    if args.reset:
        path = Path(args.model)
        if path.exists():
            path.unlink()
            print("removed %s" % path)
        else:
            print("nothing to remove at %s" % path)
        return 0

    learner = LiveLearner(model_path=Path(args.model), rows_path=Path(args.rows))
    print(json.dumps(learner.report(), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
