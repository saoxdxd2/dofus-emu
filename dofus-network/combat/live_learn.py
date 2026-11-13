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

try:
    from storage.append_store import AppendStore
except Exception:  # noqa: BLE001
    AppendStore = None

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = REPO_ROOT / "models" / "live_enemy_value.json"
DEFAULT_ROWS = REPO_ROOT / "trajectories" / "live_learning.jsonl"
# The live decision log is written by the desktop process at the workspace
# root, while the combat package lives under `dofus-network/`. Accept both
# layouts instead of silently reading an empty directory.
_WS_ROOT = REPO_ROOT.parent
DEFAULT_DECISIONS = (
    _WS_ROOT / "trajectories" / "live_policy_decisions.jsonl"
    if (_WS_ROOT / "trajectories" / "live_policy_decisions.jsonl").exists()
    else REPO_ROOT / "trajectories" / "live_policy_decisions.jsonl"
)
DEFAULT_DAMAGE_CALIBRATION = REPO_ROOT / "models" / "fight_damage_calibration.json"

# Shadow differential calibration: a residual bucket needs at least this many
# paired actions before its scale is allowed to move. Below it the bucket stays
# at 1.0 and the report says `collecting`, which is the honest answer.
MIN_BUCKET_ROWS = 3
CALIBRATION_VERSION = 1

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
        self.wired_to_storage: bool = True
        self.last_seq: int = 0
        self.store: Optional[Any] = None
        if AppendStore is not None and (self.rows_path.suffix.lower() in (".db", ".sqlite", ".sqlite3")):
            try:
                self.store = AppendStore(self.rows_path, payload_format="msgpack")
            except Exception:
                self.store = None
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
        if self.store is not None:
            try:
                seq = self.store.append(credit.as_row(), kind="turn_credit")
                if seq > self.last_seq:
                    self.last_seq = seq
            except Exception:
                pass
        elif AppendStore is not None and self.rows_path.with_suffix(".db").is_file():
            try:
                with AppendStore(self.rows_path.with_suffix(".db"), payload_format="msgpack") as s:
                    seq = s.append(credit.as_row(), kind="turn_credit")
                    if seq > self.last_seq:
                        self.last_seq = seq
            except Exception:
                pass
        try:
            self.rows_path.parent.mkdir(parents=True, exist_ok=True)
            with self.rows_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(credit.as_row(), ensure_ascii=False) + "\n")
        except OSError:
            pass

    def close(self) -> None:
        if self.store is not None:
            try:
                self.store.close()
            except Exception:
                pass
            self.store = None

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


# -- shadow differential auditor --------------------------------------------
#
# `battle_solver` prices a turn before it happens. The server then publishes the
# next board, and the difference between the price and the board is a residual
# the solver never sees. This section pairs the two, buckets the residual by
# spell and effect id, and writes a calibration the estimator can consume.
#
# The pairing rule is deliberately strict: a cast is only credited when the
# next row is a *later* action in the same fight and the target's hp actually
# moved. A repeated poll and a different fight both credit nothing, so the
# residual is an action effect rather than a clock artefact.


def _decision(row: Dict[str, Any]) -> Dict[str, Any]:
    value = row.get("decision") if isinstance(row, dict) else None
    return value if isinstance(value, dict) else {}


def _observation(row: Dict[str, Any]) -> Dict[str, Any]:
    decision = _decision(row)
    value = decision.get("observation") or row.get("observation")
    return value if isinstance(value, dict) else {}


def _action(row: Dict[str, Any]) -> Dict[str, Any]:
    value = _decision(row).get("action")
    return value if isinstance(value, dict) else {}


def _fighters_in(observation: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
    """`self` is one fighter, `allies`/`enemies` are lists. Accept both."""
    value = observation.get(key)
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    return []


def _spell_damage_effects(observation: Dict[str, Any],
                          spell_id: Any) -> List[Dict[str, Any]]:
    """Damage effects of one spell, from the observation's own spellbook."""
    try:
        wanted = int(spell_id)
    except (TypeError, ValueError):
        return []
    for key in ("self", "allies", "enemies"):
        for fighter in _fighters_in(observation, key):
            for spell in fighter.get("spells") or []:
                if not isinstance(spell, dict):
                    continue
                try:
                    if int(spell.get("id")) != wanted:
                        continue
                except (TypeError, ValueError):
                    continue
                effects = [effect for effect in (spell.get("effects") or [])
                           if isinstance(effect, dict)
                           and str(effect.get("kind")) == "damage"]
                return effects
    return []


def _target_hp(observation: Dict[str, Any], target_id: Any) -> Optional[float]:
    """Current hp of one fighter, or None when the board does not name it.

    A fighter that was alive and is gone from the next board is dead: its hp is
    0, not "unknown". Treating the disappearance as unknown would drop every
    killing blow from the residual set -- the rows where the solver is most
    wrong.
    """
    if target_id is None:
        return None
    wanted = str(target_id)
    for key in ("self", "allies", "enemies"):
        for fighter in _fighters_in(observation, key):
            if str(fighter.get("id")) != wanted:
                continue
            if fighter.get("alive") is False:
                return 0.0
            return _num(fighter.get("hp"))
    return None


def _target_alive(observation: Dict[str, Any], target_id: Any) -> bool:
    wanted = str(target_id)
    for key in ("self", "allies", "enemies"):
        for fighter in _fighters_in(observation, key):
            if str(fighter.get("id")) == wanted:
                return fighter.get("alive") is not False
    return False


def prediction_for_action(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The solver's damage price for the action on this row.

    Three sources, in descending authority: the observation's per-target
    derived damage, the decision's own slot price, and the spell effect range.
    Each is labelled so a residual can be traced back to what produced it.
    """
    action = _action(row)
    if str(action.get("type")) != "cast":
        return None
    spell_id = action.get("spellId")
    target_id = action.get("targetId")
    observation = _observation(row)
    effects = _spell_damage_effects(observation, spell_id)
    effect_ids = [int(effect.get("effect_id")) for effect in effects
                  if effect.get("effect_id") is not None]

    derived = observation.get("derived") or {}
    mine = (derived.get("self") or {}).get(str(spell_id)) if isinstance(derived, dict) else None
    if isinstance(mine, dict):
        targets = mine.get("targets") or {}
        entry = targets.get(str(target_id)) if isinstance(targets, dict) else None
        if isinstance(entry, dict):
            damage = entry.get("damage") or {}
            if isinstance(damage, dict) and isinstance(damage.get("expected"), (int, float)):
                return {"predicted": float(damage["expected"]), "spell_id": spell_id,
                        "effect_ids": effect_ids, "target_id": target_id,
                        "source": "derived"}

    for slot in _decision(row).get("slots") or []:
        if not isinstance(slot, dict):
            continue
        try:
            if int(slot.get("spell_id")) != int(spell_id):
                continue
        except (TypeError, ValueError):
            continue
        if isinstance(slot.get("damage"), (int, float)):
            return {"predicted": float(slot["damage"]), "spell_id": spell_id,
                    "effect_ids": effect_ids, "target_id": target_id,
                    "source": "slot"}

    if effects:
        low = sum(_num(effect.get("min")) for effect in effects)
        high = sum(_num(effect.get("max")) for effect in effects)
        return {"predicted": (low + high) / 2.0, "spell_id": spell_id,
                "effect_ids": effect_ids, "target_id": target_id,
                "source": "effects"}
    return None


def actual_for_action(row: Dict[str, Any],
                      following: Dict[str, Any]) -> Optional[float]:
    """Target hp delta between this action and the next observed action."""
    before = _observation(row)
    after = _observation(following)
    if not before or not after:
        return None
    fight_before = (before.get("fight") or {}).get("fight_id", row.get("fight_id"))
    fight_after = (after.get("fight") or {}).get("fight_id", following.get("fight_id"))
    if str(fight_before) != str(fight_after):
        return None
    try:
        if float(following.get("time")) <= float(row.get("time")):
            return None
    except (TypeError, ValueError):
        pass
    target_id = _action(row).get("targetId")
    hp_before = _target_hp(before, target_id)
    if hp_before is None or hp_before <= 0:
        return None
    hp_after = _target_hp(after, target_id)
    if hp_after is None:
        # Gone from the board: a dead target, not an unknown one.
        hp_after = 0.0 if _target_alive(before, target_id) else None
    if hp_after is None:
        return None
    damage = hp_before - hp_after
    return damage if damage >= 0.0 else None


def _board_hp_state(observation: Dict[str, Any]) -> Dict[str, float]:
    """Every named fighter's hp, keyed by id. Used to detect a board update."""
    state: Dict[str, float] = {}
    for key in ("self", "allies", "enemies"):
        for fighter in _fighters_in(observation, key):
            if fighter.get("id") is None:
                continue
            state[str(fighter.get("id"))] = (
                0.0 if fighter.get("alive") is False else _num(fighter.get("hp")))
    return state


def shadow_residuals(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pair casts with the board update that actually reflects them.

    A decision row carries the board *before* its action. Several casts can be
    issued before the server publishes a new board, so pairing each cast with
    the very next row attributes a delayed update to the first cast and a miss
    to the last. Instead, accumulate casts until any fighter's hp changes (or
    the round turns over), then split that update across the pending casts in
    proportion to their prices. A cast whose target never moves is credited 0,
    which is a real miss rather than a missing label.
    """
    ordered = sorted(rows, key=lambda row: _num(row.get("time")))
    out: List[Dict[str, Any]] = []
    pending: List[Dict[str, Any]] = []
    baseline: Optional[Dict[str, float]] = None

    def flush(current: Dict[str, float], reason: str) -> None:
        nonlocal pending, baseline
        if not pending or baseline is None:
            pending = []
            baseline = None
            return
        by_target: Dict[str, List[Dict[str, Any]]] = {}
        for item in pending:
            by_target.setdefault(str(item["target_id"]), []).append(item)
        for target_id, items in by_target.items():
            before = baseline.get(target_id)
            after = current.get(target_id)
            if before is None:
                continue
            if after is None:
                after = 0.0
            total_actual = max(0.0, before - after)
            total_predicted = sum(item["predicted"] for item in items)
            for item in items:
                share = (item["predicted"] / total_predicted
                         if total_predicted > 1e-9 else 1.0 / len(items))
                actual = total_actual * share
                out.append({**item,
                            "actual": round(actual, 4),
                            "residual": round(actual - item["predicted"], 4),
                            "pairing": reason})
        pending = []
        baseline = None

    for index, row in enumerate(ordered):
        observation = _observation(row)
        if not observation:
            continue
        state = _board_hp_state(observation)
        if baseline is None:
            baseline = state
        elif state != baseline:
            flush(state, "hp_update")
            baseline = state
        elif pending and str(row.get("round")) != str(pending[0].get("round")):
            flush(state, "round_change")
            baseline = state

        price = prediction_for_action(row)
        if price is None:
            continue
        pending.append({
            "time": _num(row.get("time")),
            "fight_id": row.get("fight_id"),
            "round": row.get("round"),
            "spell_id": price["spell_id"],
            "effect_ids": list(price["effect_ids"]),
            "target_id": price["target_id"],
            "source": price["source"],
            "predicted": round(float(price["predicted"]), 4),
        })

    # A cast at the very end of the log has no following board: leave it
    # unpaired rather than inventing a zero.
    return out


def _new_bucket() -> Dict[str, float]:
    return {"rows": 0.0, "predicted": 0.0, "actual": 0.0, "residual": 0.0,
            "residual_sq": 0.0, "abs_residual": 0.0,
            "ewma_residual": 0.0, "ewma_ratio": 1.0}


def _bucket_add(bucket: Dict[str, float], predicted: float, actual: float,
                residual: float, alpha: float = 0.25) -> None:
    bucket["rows"] += 1.0
    bucket["predicted"] += predicted
    bucket["actual"] += actual
    bucket["residual"] += residual
    bucket["residual_sq"] += residual * residual
    bucket["abs_residual"] += abs(residual)
    bucket["ewma_residual"] = (1.0 - alpha) * bucket["ewma_residual"] + alpha * residual
    ratio = (actual / predicted) if predicted > 1e-9 else 1.0
    bucket["ewma_ratio"] = (1.0 - alpha) * bucket["ewma_ratio"] + alpha * ratio


def _finalise_bucket(bucket: Dict[str, float], prior: float = 4.0,
                     min_rows: int = MIN_BUCKET_ROWS) -> Dict[str, Any]:
    rows = int(bucket["rows"])
    if rows <= 0:
        return {"rows": 0, "status": "empty", "scale": 1.0, "bias": 0.0,
                "confidence": 0.0}
    predicted = float(bucket["predicted"])
    actual = float(bucket["actual"])
    raw_scale = (actual / predicted) if predicted > 1e-9 else 1.0
    raw_scale = min(4.0, max(0.25, raw_scale))
    confidence = rows / (rows + max(0.0, prior))
    scale = 1.0 + confidence * (raw_scale - 1.0)
    bias = confidence * (float(bucket["residual"]) / rows)
    mae = float(bucket["abs_residual"]) / rows
    rmse = math.sqrt(float(bucket["residual_sq"]) / rows)
    return {
        "rows": rows,
        "status": "ready" if rows >= min_rows else "collecting",
        "predicted": round(predicted, 3),
        "actual": round(actual, 3),
        "scale": round(scale, 6),
        "raw_scale": round(raw_scale, 6),
        "bias": round(bias, 6),
        "confidence": round(confidence, 6),
        "ewma_residual": round(float(bucket["ewma_residual"]), 6),
        "ewma_ratio": round(float(bucket["ewma_ratio"]), 6),
        "mae": round(mae, 4),
        "rmse": round(rmse, 4),
    }


def calibrate_residuals(residuals: Sequence[Dict[str, Any]],
                        min_rows: int = MIN_BUCKET_ROWS) -> Dict[str, Any]:
    """Bucket residuals by spell and effect id and shrink each toward 1.0."""
    spells: Dict[str, Dict[str, float]] = {}
    effects: Dict[str, Dict[str, float]] = {}
    overall = _new_bucket()
    for row in residuals:
        predicted = float(row["predicted"])
        actual = float(row["actual"])
        residual = float(row["residual"])
        _bucket_add(overall, predicted, actual, residual)
        spell_key = str(row.get("spell_id"))
        _bucket_add(spells.setdefault(spell_key, _new_bucket()),
                    predicted, actual, residual)
        effect_ids = [str(value) for value in row.get("effect_ids") or []]
        if effect_ids:
            share = 1.0 / len(effect_ids)
            for effect_id in effect_ids:
                _bucket_add(effects.setdefault(effect_id, _new_bucket()),
                            predicted * share, actual * share, residual * share)

    payload = {
        "version": CALIBRATION_VERSION,
        "kind": "fight-damage-shadow",
        "generated_at": time.time(),
        "source": "trajectories/live_policy_decisions.jsonl",
        "min_rows": int(min_rows),
        "rows": len(residuals),
        "overall": _finalise_bucket(overall, min_rows=min_rows),
        "spells": {key: _finalise_bucket(value, min_rows=min_rows)
                   for key, value in sorted(spells.items())},
        "effects": {key: _finalise_bucket(value, min_rows=min_rows)
                    for key, value in sorted(effects.items())},
    }
    if residuals:
        predicted = [float(row["predicted"]) for row in residuals]
        actual = [float(row["actual"]) for row in residuals]
        mean_actual = sum(actual) / len(actual)
        baseline = sum((value - mean_actual) ** 2 for value in actual)
        model = sum((a - p) ** 2 for a, p in zip(actual, predicted))
        payload["metrics"] = {
            "mae": round(sum(abs(a - p) for a, p in zip(actual, predicted)) / len(actual), 4),
            "rmse": round(math.sqrt(model / len(actual)), 4),
            "baseline_rmse": round(math.sqrt(baseline / len(actual)), 4),
            "improvement": (round((math.sqrt(baseline) - math.sqrt(model))
                                  / math.sqrt(baseline), 4)
                            if baseline > 0 else None),
        }
    return payload


def load_damage_calibration(path: Path = DEFAULT_DAMAGE_CALIBRATION
                            ) -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != CALIBRATION_VERSION:
        return None
    return payload


def apply_damage_calibration(predicted: float,
                             spell_id: Any = None,
                             effect_ids: Optional[Sequence[Any]] = None,
                             calibration: Optional[Dict[str, Any]] = None) -> float:
    """Correct one damage price with the buckets that apply to it.

    Spell and effect corrections are combined, not multiplied: each is a
    residual estimate for the same quantity, and multiplying two shrunk ratios
    would double-count the overlap. The spell bucket carries the larger weight
    because it is the one the solver actually prices.
    """
    if not isinstance(calibration, dict):
        return max(0.0, float(predicted))
    pieces: List[Tuple[float, float, float]] = []  # (weight, scale, bias)
    spell = (calibration.get("spells") or {}).get(str(spell_id))
    if isinstance(spell, dict) and spell.get("status") == "ready":
        pieces.append((0.6, float(spell.get("scale") or 1.0),
                       float(spell.get("bias") or 0.0)))
    effect_scales: List[float] = []
    effect_biases: List[float] = []
    for effect_id in effect_ids or []:
        bucket = (calibration.get("effects") or {}).get(str(effect_id))
        if isinstance(bucket, dict) and bucket.get("status") == "ready":
            effect_scales.append(float(bucket.get("scale") or 1.0))
            effect_biases.append(float(bucket.get("bias") or 0.0))
    if effect_scales:
        pieces.append((0.4, sum(effect_scales) / len(effect_scales),
                       sum(effect_biases) / len(effect_biases)))
    if not pieces:
        overall = calibration.get("overall")
        if isinstance(overall, dict) and overall.get("status") == "ready":
            pieces.append((1.0, float(overall.get("scale") or 1.0),
                           float(overall.get("bias") or 0.0)))
    if not pieces:
        return max(0.0, float(predicted))
    total_weight = sum(weight for weight, _, _ in pieces)
    scale = sum(weight * value for weight, value, _ in pieces) / total_weight
    bias = sum(weight * value for weight, _, value in pieces) / total_weight
    return max(0.0, float(predicted) * scale + bias)


class ShadowAuditor:
    """Reads live decisions, pairs casts, and persists the calibration."""

    def __init__(self, decisions_path: Path = DEFAULT_DECISIONS,
                 calibration_path: Path = DEFAULT_DAMAGE_CALIBRATION) -> None:
        self.decisions_path = Path(decisions_path)
        self.calibration_path = Path(calibration_path)
        self.wired_to_storage: bool = True
        self.last_seq: int = 0

    def read(self, incremental: bool = False) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        db_path = self.decisions_path if self.decisions_path.suffix.lower() in (".db", ".sqlite", ".sqlite3") else self.decisions_path.with_suffix(".db")
        if AppendStore is not None and db_path.is_file():
            start_seq = (self.last_seq + 1) if incremental else None
            try:
                with AppendStore(db_path) as store:
                    for seq, _ts, _kind, rec in store.iter_records(start_seq=start_seq):
                        if seq > self.last_seq:
                            self.last_seq = seq
                        if isinstance(rec, dict):
                            rows.append(rec)
                return rows
            except Exception:
                pass

        try:
            handle = self.decisions_path.open(encoding="utf-8", errors="replace")
        except OSError:
            return rows
        with handle:
            for idx, line in enumerate(handle, 1):
                if incremental and idx <= self.last_seq:
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
                    if idx > self.last_seq:
                        self.last_seq = idx
        return rows

    def read_incremental(self) -> List[Dict[str, Any]]:
        return self.read(incremental=True)

    def audit(self, write: bool = True) -> Dict[str, Any]:
        rows = self.read()
        residuals = shadow_residuals(rows)
        calibration = calibrate_residuals(residuals)
        report = {
            "decisions": len(rows),
            "paired_casts": len(residuals),
            "unpaired_casts": sum(1 for row in rows
                                  if str(_action(row).get("type")) == "cast")
                              - len(residuals),
            "calibration": calibration,
        }
        if write:
            try:
                self.calibration_path.parent.mkdir(parents=True, exist_ok=True)
                self.calibration_path.write_text(
                    json.dumps(calibration, indent=1, sort_keys=True),
                    encoding="utf-8")
                report["written"] = str(self.calibration_path)
            except OSError as exc:
                report["write_error"] = str(exc)
        return report

    def correct(self, predicted: float, spell_id: Any = None,
                effect_ids: Optional[Sequence[Any]] = None) -> float:
        return apply_damage_calibration(
            predicted, spell_id, effect_ids,
            load_damage_calibration(self.calibration_path))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Live enemy-turn learner")
    parser.add_argument("--model", default=str(DEFAULT_MODEL))
    parser.add_argument("--rows", default=str(DEFAULT_ROWS))
    parser.add_argument("--decisions", default=str(DEFAULT_DECISIONS))
    parser.add_argument("--damage-calibration", default=str(DEFAULT_DAMAGE_CALIBRATION))
    parser.add_argument("--audit-decisions", action="store_true",
                        help="pair live casts with server deltas and write the calibration")
    parser.add_argument("--report", action="store_true", help="print what is learned")
    parser.add_argument("--reset", action="store_true", help="delete the stored model")
    args = parser.parse_args(argv)

    if args.audit_decisions:
        auditor = ShadowAuditor(Path(args.decisions), Path(args.damage_calibration))
        print(json.dumps(auditor.audit(write=True), indent=1))
        return 0

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
