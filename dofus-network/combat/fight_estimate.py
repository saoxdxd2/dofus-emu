"""Can a fight be won unattended? An honest v0, and the gate that uses it.

What this is
------------
The advisor can price a spell rotation against a dummy. This module turns that
into an encounter estimate for one monster group on the current map:

    player damage per turn  ->  turns to kill the group
    monster AP and level    ->  incoming damage per turn (heuristic)
    player HP and heals     ->  turns to die

The output has two numbers that must not be confused:

* ``raw_score`` - a bounded heuristic score from turns-to-kill vs turns-to-die;
* ``p_win``     - a probability, and therefore **only** produced by a fitted
  calibrator over live fight outcomes.

The cached monster database carries HP, AP/MP and resistances but **no monster
spell damage**, so incoming damage is an explicit level/AP assumption until the
Touch client database is harvested. That is why an uncalibrated estimate has
``p_win: null`` and cannot authorise an unattended fight. A missing input is
never read as zero: it is named in ``missing`` and blocks the call.

The data-loading half (``FightAdvisor``) and the arithmetic half
(``estimate_from_facts``) are separate so the latter can be tested with fixtures
instead of a live client.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STORE = REPO_ROOT / "data" / "knowledge" / "dofus_knowledge.sqlite"
DEFAULT_CALIBRATOR = REPO_ROOT / "models" / "fight_calibrator.json"

MODEL_VERSION = "fight-v0-analytic"
CALIBRATOR_VERSION = 1

# Incoming damage is not in the cached monster rows. This is a labelled
# assumption: a level-100 monster with the reference AP deals about 49 before
# resistances. It is deliberately slightly pessimistic.
INCOMING_BASE = 4.0
INCOMING_PER_LEVEL = 0.45
INCOMING_AP_REFERENCE = 6.0

MIN_P_WIN = 0.90
MAX_HP_LOSS_FRACTION = 0.50
# A completed challenge is a nudge, never a second objective: worth a quarter of
# a win, and a missed challenge costs exactly nothing. The game offers challenges
# per fight and the client reports each result; only the page used to drop them.
CHALLENGE_BONUS = 0.25
ALLOW_BOSS = False
MAX_TURNS = 100.0
# The M3 gate: the fitted probability may misstate the observed win rate by at
# most this much on average before the calibrator is refused.
MAX_CALIBRATION_ERROR = 0.10

# Bootstrap authorisation: there is no calibrator until 20-50 live fights have
# been labelled, and the tutorial cannot be skipped while we wait. These bounds
# are deliberately tighter than the calibrated gate, and every fight they
# authorise is tagged `authorization: "bootstrap"` so the calibration set can
# exclude or reweight it. A missing monster catalogue row is tolerated because
# the group itself reports each member's level and boss flag; its HP costs a
# pessimistic level heuristic, never zero.
BOOTSTRAP_MAX_MEMBERS = 3
BOOTSTRAP_MAX_LEVEL_GAP = 0        # the player may not be below the group
BOOTSTRAP_MIN_HP_RATIO = 0.80
BOOTSTRAP_MIN_RAW_SCORE = 0.65
BOOTSTRAP_MAX_HP_LOSS_FRACTION = 0.30
BOOTSTRAP_MAX_EXPECTED_TURNS = 20.0
BOOTSTRAP_TOLERATED_MISSING = (
    "monster:",          # no cached row for this monster id
    "monster_hp:",       # row exists but has no HP
    "monster_grade:",    # row exists but no grade for this level
    "group.scale_level", # the group was scaled; its level is still reported
)

# Scripted tutorial fights are mandatory and hand-tuned by the game, so the
# scenario is the authority. This is narrower than the general bootstrap: two
# members at most, no boss, and it exists only while `tutorial.active`.
TUTORIAL_MAX_MEMBERS = 2
TUTORIAL_LEVEL_MARGIN = 2
TUTORIAL_MIN_HP_RATIO = 0.50

# A missing monster row still needs a size. Pessimistic on purpose: too much HP
# blocks a fight, too little authorises one the estimator underprices.
FALLBACK_MONSTER_HP_BASE = 30.0
FALLBACK_MONSTER_HP_PER_LEVEL = 15.0

FEATURE_NAMES: Tuple[str, ...] = (
    "level_gap",
    "group_size",
    "turns_to_kill",
    "turns_to_die",
    "hp_ratio",
    "hp_loss_fraction",
    "has_boss",
    "unknown_members",
)

_HEAL_WEIGHT = 0.8  # advisor.spells.HEAL_WEIGHT, kept local to avoid a cycle


# ---------------------------------------------------------------------------
# facts


@dataclass
class PlayerFacts:
    level: int = 0
    current_hp: float = 0.0
    max_hp: float = 0.0
    ap: int = 0
    dpt_by_element: Dict[str, float] = field(default_factory=dict)
    damage_lines: int = 0
    heal_per_turn: float = 0.0
    resist_percent: Dict[str, float] = field(default_factory=dict)
    missing: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def damage_per_turn(self) -> float:
        return sum(self.dpt_by_element.values())


@dataclass
class EnemyMember:
    monster_id: int
    name: str
    level: int
    hp: float
    ap: float
    resist_percent: Dict[str, float] = field(default_factory=dict)
    fixed_res: float = 0.0
    is_boss: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "monster_id": self.monster_id,
            "name": self.name,
            "level": self.level,
            "hp": round(self.hp, 1),
            "ap": round(self.ap, 1),
            "resist_percent": {k: round(v, 1) for k, v in self.resist_percent.items()},
            "fixed_res": round(self.fixed_res, 1),
            "is_boss": self.is_boss,
        }


@dataclass
class EnemyFacts:
    group_id: Optional[int] = None
    members: List[EnemyMember] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    truncated: bool = False

    @property
    def has_boss(self) -> bool:
        return any(member.is_boss for member in self.members)

    @property
    def total_hp(self) -> float:
        return sum(member.hp for member in self.members)


# ---------------------------------------------------------------------------
# calibrated probability


@dataclass
class FightCalibrator:
    """A standardised logistic model over the estimate's own features.

    A raw heuristic score is not a probability. Until this file exists, the
    planner sees ``p_win: null`` and refuses unattended fights.
    """

    features: List[str] = field(default_factory=lambda: list(FEATURE_NAMES))
    mean: List[float] = field(default_factory=list)
    scale: List[float] = field(default_factory=list)
    coef: List[float] = field(default_factory=list)
    intercept: float = 0.0
    n: int = 0
    source: str = "unknown"
    trained_at: Optional[float] = None
    metrics: Dict[str, Any] = field(default_factory=dict)

    def predict(self, values: Dict[str, Any]) -> Optional[float]:
        if not self.features or len(self.mean) != len(self.features):
            return None
        if len(self.scale) != len(self.features) or len(self.coef) != len(self.features):
            return None
        z = float(self.intercept)
        for index, name in enumerate(self.features):
            raw = values.get(name)
            if not isinstance(raw, (int, float)) or isinstance(raw, bool):
                return None
            if not math.isfinite(float(raw)):
                return None
            scale = self.scale[index] or 1.0
            z += self.coef[index] * ((float(raw) - self.mean[index]) / scale)
        if z >= 0:
            return 1.0 / (1.0 + math.exp(-min(z, 60.0)))
        exp_z = math.exp(max(z, -60.0))
        return exp_z / (1.0 + exp_z)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": CALIBRATOR_VERSION,
            "model_version": MODEL_VERSION,
            "features": list(self.features),
            "mean": list(self.mean),
            "scale": list(self.scale),
            "coef": list(self.coef),
            "intercept": self.intercept,
            "n": self.n,
            "source": self.source,
            "trained_at": self.trained_at,
            "metrics": dict(self.metrics),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> Optional["FightCalibrator"]:
        if not isinstance(payload, dict):
            return None
        features = [str(x) for x in payload.get("features") or []]
        mean = _float_list(payload.get("mean"))
        scale = _float_list(payload.get("scale"))
        coef = _float_list(payload.get("coef"))
        if not features or mean is None or scale is None or coef is None:
            return None
        if not (len(features) == len(mean) == len(scale) == len(coef)):
            return None
        return cls(
            features=features,
            mean=mean,
            scale=scale,
            coef=coef,
            intercept=float(payload.get("intercept") or 0.0),
            n=int(payload.get("n") or 0),
            source=str(payload.get("source") or "unknown"),
            trained_at=payload.get("trained_at"),
            metrics=dict(payload.get("metrics") or {})
            if isinstance(payload.get("metrics"), dict) else {},
        )

    @classmethod
    def load(cls, path: Path = DEFAULT_CALIBRATOR) -> Optional["FightCalibrator"]:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return cls.from_dict(payload)


def _float_list(raw: Any) -> Optional[List[float]]:
    if not isinstance(raw, list):
        return None
    out: List[float] = []
    for value in raw:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        out.append(float(value))
    return out


def _labelled_samples(
    rows: Iterable[Dict[str, Any]], names: Sequence[str]
) -> List[Tuple[List[float], float]]:
    samples: List[Tuple[List[float], float]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        won = row.get("won")
        if not isinstance(won, bool):
            continue
        values = row.get("features")
        if not isinstance(values, dict):
            continue
        vector: List[float] = []
        usable = True
        for name in names:
            raw = values.get(name)
            if not isinstance(raw, (int, float)) or isinstance(raw, bool):
                usable = False
                break
            number = float(raw)
            if not math.isfinite(number):
                usable = False
                break
            vector.append(number)
        if usable:
            samples.append((vector, 1.0 if won else 0.0))
    return samples


def fit_calibrator(
    rows: Iterable[Dict[str, Any]],
    features: Sequence[str] = FEATURE_NAMES,
    l2: float = 1.0,
    iterations: int = 3000,
    learning_rate: float = 0.15,
) -> Optional[FightCalibrator]:
    """Fit a logistic calibrator from rows shaped ``{"features": {...}, "won": bool}``.

    Pure stdlib on purpose: calibration data is a few hundred rows, and adding a
    scientific stack to the host process for this would cost more than it saves.
    Rows with an unknown label or a missing feature are skipped and counted in
    the returned model's ``n`` only when used.
    """
    names = [str(name) for name in features]
    samples = _labelled_samples(rows, names)
    if len(samples) < 20:
        return None
    positives = sum(label for _vector, label in samples)
    if positives <= 0 or positives >= len(samples):
        # One class cannot be calibrated; a constant would look like knowledge.
        return None

    count = len(samples)
    mean = [sum(vector[i] for vector, _label in samples) / count for i in range(len(names))]
    scale: List[float] = []
    for i in range(len(names)):
        variance = sum((vector[i] - mean[i]) ** 2 for vector, _label in samples) / count
        scale.append(math.sqrt(variance) or 1.0)

    weights = [0.0] * len(names)
    # Start from the base rate instead of zero, so an imbalanced set does not
    # need thousands of iterations just to learn its prior.
    intercept = math.log(positives / (count - positives))
    for _ in range(max(1, int(iterations))):
        grad_w = [0.0] * len(names)
        grad_b = 0.0
        for vector, label in samples:
            z = intercept + sum(
                weights[i] * ((vector[i] - mean[i]) / scale[i]) for i in range(len(names))
            )
            prediction = 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, z))))
            error = prediction - label
            grad_b += error
            for i in range(len(names)):
                grad_w[i] += error * ((vector[i] - mean[i]) / scale[i])
        step = learning_rate / count
        intercept -= step * grad_b
        for i in range(len(names)):
            weights[i] -= step * (grad_w[i] + l2 * weights[i])
    return FightCalibrator(
        features=names,
        mean=mean,
        scale=scale,
        coef=weights,
        intercept=intercept,
        n=count,
        source="fit",
    )


def calibration_report(
    model: FightCalibrator,
    rows: Iterable[Dict[str, Any]],
    bins: Optional[int] = None,
) -> Dict[str, Any]:
    """Score a calibrator on its own outcome rows.

    The gate is stated in the plan as "calibration error < 0.1"; this is the
    expected calibration error (ECE): bin the predictions, compare each bin's
    mean probability with the observed win rate, and weight by bin size. Brier
    and log loss are reported because ECE alone can hide a confidently wrong
    model.

    In-sample ECE is optimistic, so it is a floor on the real error, not proof.
    It exists to catch a calibrator that is grossly wrong before the guard is
    allowed to trust its probabilities.
    """
    names = list(model.features)
    scored: List[Tuple[float, float]] = []
    for vector, label in _labelled_samples(rows, names):
        prediction = model.predict(dict(zip(names, vector)))
        if prediction is not None:
            scored.append((prediction, label))
    n = len(scored)
    if n == 0:
        return {"n": 0, "wins": 0, "losses": 0, "ece": None,
                "brier": None, "log_loss": None, "accuracy": None,
                "bin_count": 0, "bins": []}

    wins = int(sum(label for _prediction, label in scored))
    brier = sum((p - label) ** 2 for p, label in scored) / n
    eps = 1e-6
    log_loss = -sum(
        label * math.log(max(eps, p)) + (1.0 - label) * math.log(max(eps, 1.0 - p))
        for p, label in scored
    ) / n
    accuracy = sum(1 for p, label in scored if (p >= 0.5) == (label >= 0.5)) / n

    bin_count = int(bins) if bins else max(3, min(10, n // 10))
    buckets: List[List[Tuple[float, float]]] = [[] for _ in range(bin_count)]
    for p, label in scored:
        buckets[min(bin_count - 1, int(p * bin_count))].append((p, label))
    ece = 0.0
    detail: List[Dict[str, Any]] = []
    for index, bucket in enumerate(buckets):
        if not bucket:
            continue
        mean_p = sum(p for p, _label in bucket) / len(bucket)
        observed = sum(label for _prediction, label in bucket) / len(bucket)
        gap = abs(mean_p - observed)
        ece += (len(bucket) / n) * gap
        detail.append({"bin": index, "n": len(bucket),
                       "mean_p": round(mean_p, 4),
                       "observed": round(observed, 4), "gap": round(gap, 4)})

    return {
        "n": n,
        "wins": wins,
        "losses": n - wins,
        "base_rate": round(wins / n, 4),
        "brier": round(brier, 4),
        "log_loss": round(log_loss, 4),
        "accuracy": round(accuracy, 4),
        "ece": round(ece, 4),
        "bin_count": bin_count,
        "bins": detail,
    }


# ---------------------------------------------------------------------------
# pure estimate


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _resist_factor(resist_percent: Dict[str, float], element: str) -> float:
    try:
        value = float(resist_percent.get(element, 0.0))
    except (TypeError, ValueError):
        value = 0.0
    return _clamp(1.0 - value / 100.0, 0.05, 2.5)


def _tolerated_missing(name: str) -> bool:
    return any(str(name).startswith(prefix) for prefix in BOOTSTRAP_TOLERATED_MISSING)


def _member_levels(enemy: EnemyFacts) -> Optional[List[int]]:
    levels: List[int] = []
    for member in enemy.members:
        if member.level is None:
            return None
        levels.append(int(member.level))
    return levels


def _tutorial_refusal(player: PlayerFacts, enemy: EnemyFacts,
                      hp_ratio: float, boss: bool) -> Optional[str]:
    """Why a scripted tutorial fight should still be refused, or None."""
    if boss:
        return "a boss needs the explicit opt-in"
    if int(player.level or 0) <= 0:
        return "player level is unknown"
    if player.damage_per_turn <= 0:
        return "no known damaging spell"
    if not enemy.members:
        return "no group members"
    if len(enemy.members) > TUTORIAL_MAX_MEMBERS:
        return ("%d members is more than the %d the tutorial envelope allows"
                % (len(enemy.members), TUTORIAL_MAX_MEMBERS))
    levels = _member_levels(enemy)
    if levels is None:
        return "a member has no level"
    if max(levels) > int(player.level) + TUTORIAL_LEVEL_MARGIN:
        return ("group level %d is above player level %d + %d"
                % (max(levels), int(player.level), TUTORIAL_LEVEL_MARGIN))
    if hp_ratio < TUTORIAL_MIN_HP_RATIO:
        return ("HP %.0f%% is below the tutorial floor of %.0f%%"
                % (hp_ratio * 100.0, TUTORIAL_MIN_HP_RATIO * 100.0))
    return None


def _bootstrap_refusal(player: PlayerFacts, enemy: EnemyFacts, hp_ratio: float,
                       raw_score: float, hp_loss_fraction: float,
                       expected_turns: float, boss: bool) -> Optional[str]:
    """Why the conservative no-calibrator envelope should refuse, or None."""
    if boss:
        return "a boss needs the explicit opt-in"
    if int(player.level or 0) <= 0:
        return "player level is unknown"
    if player.damage_per_turn <= 0:
        return "player damage is unknown"
    if not enemy.members:
        return "no group members"
    if len(enemy.members) > BOOTSTRAP_MAX_MEMBERS:
        return ("%d members is more than the %d the bootstrap envelope allows"
                % (len(enemy.members), BOOTSTRAP_MAX_MEMBERS))
    levels = _member_levels(enemy)
    if levels is None:
        return "a member has no level"
    if max(levels) > int(player.level) + BOOTSTRAP_MAX_LEVEL_GAP:
        return ("group level %d is above player level %d"
                % (max(levels), int(player.level)))
    if hp_ratio < BOOTSTRAP_MIN_HP_RATIO:
        return ("HP %.0f%% is below the bootstrap floor of %.0f%%"
                % (hp_ratio * 100.0, BOOTSTRAP_MIN_HP_RATIO * 100.0))
    if raw_score < BOOTSTRAP_MIN_RAW_SCORE:
        return ("raw score %.3f is below the bootstrap floor of %.2f"
                % (raw_score, BOOTSTRAP_MIN_RAW_SCORE))
    if hp_loss_fraction > BOOTSTRAP_MAX_HP_LOSS_FRACTION:
        return ("expected HP loss %.0f%% exceeds the bootstrap limit of %.0f%%"
                % (hp_loss_fraction * 100.0, BOOTSTRAP_MAX_HP_LOSS_FRACTION * 100.0))
    if expected_turns > BOOTSTRAP_MAX_EXPECTED_TURNS:
        return ("expected %.1f turns exceeds the bootstrap limit of %.0f"
                % (expected_turns, BOOTSTRAP_MAX_EXPECTED_TURNS))
    return None


def estimate_from_facts(
    player: PlayerFacts,
    enemy: EnemyFacts,
    calibrator: Optional[FightCalibrator] = None,
    min_p_win: float = MIN_P_WIN,
    max_hp_loss_fraction: float = MAX_HP_LOSS_FRACTION,
    allow_boss: bool = ALLOW_BOSS,
    bootstrap: bool = False,
    tutorial: bool = False,
) -> Dict[str, Any]:
    """Turn facts into a verdict. No client, no database, no I/O.

    `bootstrap` and `tutorial` are authorisation modes, not shortcuts around
    the arithmetic: every bound below is still evaluated, and the returned
    `authorization` names which mode let the fight through. A fitted
    calibrator always outranks them.
    """
    missing = list(dict.fromkeys(list(player.missing) + list(enemy.missing)))
    notes = list(dict.fromkeys(list(player.notes) + list(enemy.notes)))

    player_hp = max(0.0, float(player.current_hp or player.max_hp or 0.0))
    if player_hp <= 0:
        missing.append("player.current_hp")
    if enemy.truncated:
        missing.append("group.members_truncated")
    if not enemy.members:
        missing.append("group.members")

    # Damage per member, after its own resistances. Sequential kills: the time
    # to clear the group is the sum, which is conservative for area spells.
    dpt_by_member: Dict[int, float] = {}
    turn_cost = 0.0
    for index, member in enumerate(enemy.members):
        raw_damage = sum(
            amount * _resist_factor(member.resist_percent, element)
            for element, amount in player.dpt_by_element.items()
        )
        raw_damage -= max(0.0, float(member.fixed_res)) * max(0, int(player.damage_lines))
        dpt = max(0.0, raw_damage)
        dpt_by_member[index] = dpt
        turn_cost += (max(0.0, member.hp) / dpt) if dpt > 0 else MAX_TURNS
    if turn_cost <= 0:
        turn_cost = MAX_TURNS
    turn_cost = _clamp(turn_cost, 0.1, MAX_TURNS)

    if player.damage_per_turn <= 0:
        missing.append("player.no_damaging_spell")

    incoming = 0.0
    for member in enemy.members:
        level = max(0.0, float(member.level))
        ap = _clamp(float(member.ap or INCOMING_AP_REFERENCE), 1.0, 20.0)
        incoming += (INCOMING_BASE + INCOMING_PER_LEVEL * level) * (ap / INCOMING_AP_REFERENCE)
    resist_values = [float(value) for value in player.resist_percent.values()]
    average_resist = sum(resist_values) / len(resist_values) if resist_values else 0.0
    incoming *= _clamp(1.0 - average_resist / 100.0, 0.05, 2.5)
    net_incoming = max(0.0, incoming - max(0.0, float(player.heal_per_turn)))
    turns_to_die = (player_hp / net_incoming) if net_incoming > 0 else MAX_TURNS
    turns_to_die = _clamp(turns_to_die, 0.1, MAX_TURNS)

    expected_turns = min(turn_cost, turns_to_die)
    expected_hp_loss = min(player_hp, net_incoming * expected_turns)
    hp_loss_fraction = expected_hp_loss / player_hp if player_hp > 0 else 1.0
    raw_score = turns_to_die / (turns_to_die + turn_cost) if (turns_to_die + turn_cost) > 0 else 0.0

    level_known = [member.level for member in enemy.members if member.level is not None]
    max_level = max(level_known) if level_known else player.level
    total_hp = max(1.0, enemy.total_hp)
    features: Dict[str, float] = {
        "level_gap": _clamp(float(player.level) - float(max_level), -100.0, 100.0),
        "group_size": float(len(enemy.members)),
        "turns_to_kill": round(turn_cost, 4),
        "turns_to_die": round(turns_to_die, 4),
        "hp_ratio": round(_clamp(player_hp / total_hp, 0.01, 100.0), 4),
        "hp_loss_fraction": round(_clamp(hp_loss_fraction, 0.0, 1.0), 4),
        "has_boss": 1.0 if enemy.has_boss else 0.0,
        "unknown_members": float(len(enemy.missing)),
    }


    confidence = "low" if missing else "medium"
    p_win: Optional[float] = None
    if calibrator is not None and confidence != "low":
        p_win = calibrator.predict(features)
        if p_win is None:
            missing.append("calibrator.features")
            confidence = "low"

    boss = enemy.has_boss
    player_max_hp = float(player.max_hp or 0.0)
    hp_ratio = (player_hp / player_max_hp) if player_max_hp > 0 else 0.0
    critical_missing = [name for name in missing if not _tolerated_missing(name)]
    tutorial_reason = (_tutorial_refusal(player, enemy, hp_ratio, boss)
                       if tutorial else None)
    blocked_reason: Optional[str] = None
    authorization: Optional[str] = None

    # A fitted probability that clears both limits is the only authority for a
    # normal fight, and a fitted no is final: the heuristic modes below never
    # overrule a calibrator.
    if p_win is not None and (allow_boss or not boss) \
            and p_win >= float(min_p_win) \
            and hp_loss_fraction <= float(max_hp_loss_fraction):
        authorization = "calibrated"
    elif boss and not allow_boss:
        blocked_reason = "boss or wanted monster: opt-in is required, never the default"
    elif tutorial and tutorial_reason is None:
        authorization = "tutorial"
    elif critical_missing or (missing and not bootstrap) or not enemy.members:
        blocked_reason = "insufficient data: " + ", ".join(
            dict.fromkeys(critical_missing or missing))
    elif p_win is not None:
        if p_win < float(min_p_win):
            blocked_reason = (f"p_win {p_win:.3f} is below the {float(min_p_win):.2f} "
                              f"threshold")
        else:
            blocked_reason = (f"expected HP loss {hp_loss_fraction:.0%} exceeds the "
                              f"{float(max_hp_loss_fraction):.0%} limit")
    elif bootstrap:
        reason = _bootstrap_refusal(player, enemy, hp_ratio, raw_score,
                                    hp_loss_fraction, expected_turns, boss)
        if reason is None:
            authorization = "bootstrap"
        else:
            blocked_reason = "bootstrap refused — " + reason
    elif tutorial_reason is not None:
        blocked_reason = "tutorial refused — " + tutorial_reason
    else:
        blocked_reason = ("uncalibrated: no fitted fight probability, so this cannot be "
                          "authorised unattended")
    allowed = blocked_reason is None and authorization is not None

    if authorization == "bootstrap":
        notes.append("bootstrap authorisation: no calibrator, strict level/HP envelope; "
                     "this row is tagged and is not a calibrated claim")
    if authorization == "tutorial":
        notes.append("tutorial authorisation: the scripted scenario guarantees this fight")

    if enemy.members:
        notes.append("incoming damage is a level/AP heuristic; the monster DB has no "
                     "spell damage")
        notes.append("one target at a time; area damage is not credited")
        if player.heal_per_turn > 0:
            notes.append("healing is applied against incoming damage, not as a shield")
    if boss and allow_boss:
        notes.append("boss opt-in is on for this call")

    return {
        "status": "estimated" if enemy.members else "unavailable",
        "model_version": MODEL_VERSION,
        "group_id": enemy.group_id,
        "player": {
            "level": player.level,
            "hp": round(player_hp, 1),
            "ap": player.ap,
            "damage_per_turn": round(player.damage_per_turn, 1),
            "heal_per_turn": round(player.heal_per_turn, 1),
            "damage_by_element": {k: round(v, 1) for k, v in player.dpt_by_element.items()},
        },
        "enemies": {
            "count": len(enemy.members),
            "total_hp": round(enemy.total_hp, 1),
            "has_boss": boss,
            "members": [member.to_dict() for member in enemy.members],
        },
        "p_win": round(p_win, 4) if p_win is not None else None,
        "raw_score": round(raw_score, 4),
        "expected_hp_loss": round(expected_hp_loss, 1),
        "hp_loss_fraction": round(hp_loss_fraction, 4),
        "hp_ratio": round(hp_ratio, 4),
        "expected_turns": round(expected_turns, 2),
        "incoming_per_turn": round(incoming, 1),
        "confidence": confidence,
        "missing": list(dict.fromkeys(missing)),
        "assumptions": list(dict.fromkeys(notes)),
        "features": features,
        "authorization": authorization,
        "bootstrap": authorization == "bootstrap",
        "tutorial_fight": authorization == "tutorial",
        "thresholds": {
            "min_p_win": float(min_p_win),
            "max_hp_loss_fraction": float(max_hp_loss_fraction),
            "allow_boss": bool(allow_boss),
        },
        "allowed": allowed,
        "blocked_reason": blocked_reason,
    }


def unavailable(group_id: Optional[int], reason: str,
                missing: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """A refused estimate that still names why, instead of an exception."""
    return {
        "status": "unavailable",
        "model_version": MODEL_VERSION,
        "group_id": group_id,
        "p_win": None,
        "raw_score": None,
        "expected_hp_loss": None,
        "hp_loss_fraction": None,
        "expected_turns": None,
        "confidence": "low",
        "missing": list(missing or [reason]),
        "assumptions": [],
        "authorization": None,
        "bootstrap": False,
        "tutorial_fight": False,
        "allowed": False,
        "blocked_reason": reason,
    }


# ---------------------------------------------------------------------------
# data loading


_SPELLBOOK: Optional[Tuple[Dict[int, Any], Dict[int, List[Any]]]] = None


def _spellbook() -> Tuple[Dict[int, Any], Dict[int, List[Any]]]:
    """The official class spellbook, loaded once per process."""
    global _SPELLBOOK
    if _SPELLBOOK is None:
        from data.official_spell_repository import load_official_database
        from advisor.repository import OfficialSpell

        document = load_official_database()
        spells: Dict[int, Any] = {}
        by_breed: Dict[int, List[Any]] = {}
        for breed in document.get("classes") or []:
            breed_id = int(breed.get("breed_id") or 0)
            for raw in breed.get("spells") or []:
                spell = OfficialSpell.from_raw(raw)
                if spell.id <= 0:
                    continue
                spells[spell.id] = spell
                by_breed.setdefault(breed_id, []).append(spell)
        _SPELLBOOK = (spells, by_breed)
    return _SPELLBOOK


def _store(path: Path = DEFAULT_STORE):
    from knowledge.store import CanonicalStore

    return CanonicalStore(path)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def _grade_for_level(monster: Dict[str, Any], level: int) -> Tuple[Optional[Dict[str, Any]], bool]:
    grades = [row for row in monster.get("grades") or [] if isinstance(row, dict)]
    if not grades:
        return None, False
    exact = [row for row in grades if int(row.get("level") or 0) == int(level)]
    if exact:
        return exact[0], True
    def distance(row: Dict[str, Any]) -> Tuple[int, int]:
        return (abs(int(row.get("level") or 0) - int(level)), int(row.get("grade") or 0))
    return min(grades, key=distance), False


def _player_facts(character, spells: Dict[int, Any]) -> PlayerFacts:
    """Build the player's side from a `CharacterSnapshot`."""
    from advisor.spells import candidate_for, greedy_rotation, parse_spell_power

    stats = character.to_character_stats()
    missing: List[str] = []
    notes: List[str] = []
    if not character.level:
        missing.append("player.level")
    raw_ap = character.stat("actionPoints", "action_points")
    if raw_ap is None:
        missing.append("player.action_points")
    current_hp = character.stat("lifePoints") or character.max_hp
    if current_hp <= 0:
        missing.append("player.current_hp")
    if character.max_hp <= 0:
        missing.append("player.max_hp")

    resist_percent: Dict[str, float] = {}
    for element in ("earth", "fire", "water", "air"):
        value = character.stat(element + "ElementResistPercent")
        if value is None:
            notes.append("player resistances missing; assumed 0")
        resist_percent[element] = float(value or 0.0)

    focus = character.dominant_element
    candidates = []
    grades: Dict[int, int] = {}
    live = [state for state in character.spells if not state.is_item and state.level > 0]
    if not live:
        missing.append("player.spellbook")
    unscored: List[int] = []
    for state in live:
        spell = spells.get(state.spell_id)
        if spell is None:
            unscored.append(state.spell_id)
            continue
        cap = max(spell.grades) if spell.grades else 0
        if state.max_grade_for_level:
            cap = min(cap, state.max_grade_for_level)
        grade = max(1, min(int(state.level), cap)) if cap >= 1 else 0
        if grade < 1:
            continue
        candidate = candidate_for(None, spell, grade, stats, focus, character.max_hp)
        if candidate is not None:
            candidates.append(candidate)
            grades[spell.id] = grade
    if unscored:
        notes.append("unpriced spells: " + ", ".join(str(x) for x in unscored))
    # Utility and healing spells are priced above damage on value (Roulette
    # alone scored 205 on the level-1 sheet), and greedy_rotation then spent
    # every AP without casting a damaging spell. That made the estimator read a
    # usable character as "no damaging spell" and block the tutorial fight.
    # Damage capability and healing capability are separate questions.
    rotation = greedy_rotation([c for c in candidates if c.damage > 0],
                               character.ap_budget)
    heal_rotation = greedy_rotation([c for c in candidates if c.heal > 0],
                                    character.ap_budget)

    dpt_by_element: Dict[str, float] = {}
    damage_lines = 0
    for cast in rotation.casts:
        spell = spells.get(cast.spell_id)
        if spell is None:
            continue
        power = parse_spell_power(
            spell, grades.get(cast.spell_id, cast.grade), stats, focus, character.max_hp
        )
        for element, amount in power.element_damage.items():
            dpt_by_element[element] = dpt_by_element.get(element, 0.0) + amount * cast.casts
        damage_lines += max(0, power.damage_lines) * cast.casts
    if rotation.damage <= 0:
        dpt_by_element = {}

    return PlayerFacts(
        level=int(character.level or 0),
        current_hp=float(current_hp or 0.0),
        max_hp=float(character.max_hp or 0.0),
        ap=int(character.ap_budget),
        dpt_by_element=dpt_by_element,
        damage_lines=damage_lines,
        heal_per_turn=float(heal_rotation.heal) / _HEAL_WEIGHT,
        resist_percent=resist_percent,
        missing=missing,
        notes=notes,
    )


def _fallback_hp(level: int) -> float:
    return FALLBACK_MONSTER_HP_BASE + FALLBACK_MONSTER_HP_PER_LEVEL * max(0, int(level))


def _fallback_member(monster_id: int, level: int, is_boss: bool) -> EnemyMember:
    """A member built from the group row when the catalogue has no monster row.

    The client reports the level and boss flag for every member of a visible
    group, so those are facts. HP and AP are not, and they are the pessimistic
    side of the estimate: an overestimated monster refuses a fight, an
    underestimated one authorises a loss.
    """
    return EnemyMember(
        monster_id=monster_id,
        name="#%d" % monster_id,
        level=int(level),
        hp=FALLBACK_MONSTER_HP_BASE + FALLBACK_MONSTER_HP_PER_LEVEL * max(0, int(level)),
        ap=INCOMING_AP_REFERENCE,
        resist_percent={},
        fixed_res=0.0,
        is_boss=bool(is_boss),
    )


def _enemy_facts(group: Dict[str, Any], canonical,
                 monster_cache: Optional[Dict[int, Any]] = None) -> EnemyFacts:
    """Build the monster side from one `map.groups` entry and the canonical store."""
    members: List[EnemyMember] = []
    missing: List[str] = []
    notes: List[str] = []
    group_id = _as_int(group.get("group_id", group.get("contextualId")))
    raw_members = [row for row in group.get("members") or [] if isinstance(row, dict)]
    if not raw_members:
        return EnemyFacts(group_id=group_id, missing=["group.members"])
    member_count = _as_int(group.get("member_count")) or len(raw_members)
    truncated = len(raw_members) < member_count
    if truncated:
        missing.append("group.members_truncated")

    for row in raw_members:
        monster_id = _as_int(row.get("monster_id"))
        level = _as_int(row.get("level"))
        if monster_id is None:
            missing.append("member.monster_id")
            continue
        if level is None:
            missing.append("member.level:%d" % monster_id)
            continue
        if monster_cache is not None and monster_id in monster_cache:
            monster = monster_cache[monster_id]
        else:
            monster = canonical.monster(monster_id) if canonical is not None else None
            if monster_cache is not None:
                monster_cache[monster_id] = monster
                if len(monster_cache) > 2048:
                    monster_cache.clear()
        if monster is None:
            missing.append("monster:%d" % monster_id)
            members.append(_fallback_member(monster_id, level, bool(row.get("is_boss"))))
            notes.append("monster %d has no cached row; HP and AP are pessimistic "
                         "level heuristics" % monster_id)
            continue
        grade, exact = _grade_for_level(monster, level)
        if grade is None:
            missing.append("monster_grade:%d" % monster_id)
            members.append(_fallback_member(
                monster_id, level,
                bool(row.get("is_boss") or monster.get("is_boss"))))
            notes.append("monster %d has no grade for level %d; HP and AP are "
                         "pessimistic level heuristics" % (monster_id, level))
            continue
        if not exact:
            notes.append("monster %d matched to the nearest grade level" % monster_id)
        resist_percent = {
            "earth": float(grade.get("earth_res") or 0.0),
            "fire": float(grade.get("fire_res") or 0.0),
            "water": float(grade.get("water_res") or 0.0),
            "air": float(grade.get("air_res") or 0.0),
        }
        names = monster.get("names") or {}
        hp = float(grade.get("life_points") or 0.0)
        if hp <= 0:
            hp = _fallback_hp(int(grade.get("level") or level))
            missing.append("monster_hp:%d" % monster_id)
            notes.append("monster %d has no HP in its grade; using the pessimistic "
                         "level heuristic" % monster_id)
        members.append(EnemyMember(
            monster_id=monster_id,
            name=str(names.get("fr") or names.get("en") or "#%d" % monster_id),
            level=int(grade.get("level") or level),
            hp=hp,
            ap=float(grade.get("action_points") or INCOMING_AP_REFERENCE),
            resist_percent=resist_percent,
            fixed_res=float(grade.get("fixed_res") or 0.0),
            is_boss=bool(row.get("is_boss") or monster.get("is_boss")),
        ))
    if group.get("scale_level") is not None:
        notes.append("group scale_level is not modelled in v0")
    return EnemyFacts(
        group_id=group_id,
        members=members,
        missing=missing,
        notes=notes,
        truncated=truncated,
    )


def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# the advisor the planner and executor share


class FightAdvisor:
    """Read-only answers for `fight.estimate` and the autopilot guard."""

    def __init__(
        self,
        store=None,
        calibrator: Optional[FightCalibrator] = None,
        calibrator_path: Path = DEFAULT_CALIBRATOR,
        min_p_win: float = MIN_P_WIN,
        max_hp_loss_fraction: float = MAX_HP_LOSS_FRACTION,
        allow_boss: bool = ALLOW_BOSS,
        spellbook=None,
        bootstrap: bool = True,
    ) -> None:
        self._store = store
        self._store_failed = False
        self.calibrator_path = Path(calibrator_path)
        self.calibrator = calibrator if calibrator is not None else FightCalibrator.load(
            self.calibrator_path)
        self.min_p_win = float(min_p_win)
        self.max_hp_loss_fraction = float(max_hp_loss_fraction)
        self.allow_boss = bool(allow_boss)
        # Bootstrap authorisation only fills the gap until a calibrator exists:
        # once fitted probabilities are available they decide every fight.
        self.bootstrap = bool(bootstrap) and self.calibrator is None
        self._spellbook = spellbook
        self.character = None
        self.last_estimate: Optional[Dict[str, Any]] = None
        self._group_cache_key = None
        self._group_cache: List[Dict[str, Any]] = []
        # The spell rotation only changes with the sheet, not with the group;
        # re-pricing it once per group on a 20-group map is wasted work.
        self._player_cache: Optional[PlayerFacts] = None
        # Monster rows come from a read-only canonical store; the same monster
        # usually appears in several groups and every group re-query was a
        # separate SQLite round trip.
        self._monster_cache: Dict[int, Any] = {}

    # -- inputs ------------------------------------------------------------

    @property
    def store(self):
        if self._store is None and not self._store_failed:
            try:
                self._store = _store()
            except Exception:  # noqa: BLE001 - a broken DB must not kill the loop
                self._store_failed = True
        return self._store

    def warm(self) -> None:
        """Load the class spellbook before the first map that has monsters."""
        if self._spellbook is None:
            self._spellbook = _spellbook()

    def use_character_report(self, payload: Any) -> bool:
        from advisor.models import CharacterSnapshot

        snapshot = CharacterSnapshot.from_bridge(payload)
        if snapshot.level <= 0 or snapshot.max_hp <= 0:
            return False
        self.character = snapshot
        self._group_cache_key = None
        self._group_cache = []
        self._player_cache = None
        return True

    def character_summary(self) -> Dict[str, Any]:
        if self.character is None:
            return {"ready": False}
        return {
            "ready": True,
            "level": self.character.level,
            "hp": self.character.max_hp,
            "ap": self.character.ap_budget,
            "spells": len([s for s in self.character.spells if not s.is_item and s.level > 0]),
        }

    def ready(self) -> bool:
        return self.character is not None and bool(self.store)

    # -- estimates ---------------------------------------------------------

    def groups(self, observation: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        map_block = (observation or {}).get("map")
        if isinstance(map_block, dict):
            rows = map_block.get("groups")
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
        rows = (observation or {}).get("groups")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def resolve_monster(self, value: Any) -> Optional[int]:
        monster_id = _as_int(value)
        if monster_id is not None:
            return monster_id
        text = str(value or "").strip()
        if not text or self.store is None:
            return None
        try:
            hits = self.store.find_entity("monster", text, limit=1)
        except Exception:  # noqa: BLE001 - a lookup must not kill the loop
            return None
        return int(hits[0]["id"]) if hits else None

    def estimate(
        self,
        observation: Optional[Dict[str, Any]],
        group_id: Optional[int] = None,
        monster: Any = None,
    ) -> Dict[str, Any]:
        groups = self.groups(observation)
        if not groups:
            return unavailable(None, "no monster group on the current map",
                                ["map.groups"])
        selected: Optional[Dict[str, Any]] = None
        if group_id is not None:
            target_id = _as_int(group_id)
            selected = next(
                (row for row in groups
                 if _as_int(row.get("group_id", row.get("contextualId"))) == target_id),
                None,
            )
            if selected is None:
                return unavailable(target_id, "group %s is not visible on this map" % group_id)
        elif monster is not None:
            monster_id = self.resolve_monster(monster)
            if monster_id is None:
                return unavailable(None, "cannot resolve monster %r" % (monster,),
                                    ["monster:%s" % monster])
            matches = [
                row for row in groups
                if monster_id in [_as_int(x) for x in row.get("monster_ids") or []]
            ]
            if not matches:
                return unavailable(
                    None,
                    "no visible group contains monster %d" % monster_id,
                    ["group.%d" % monster_id],
                )
            # The client joins the nearest match. We cannot compute distance
            # here, so show the least favourable visible match; the guard
            # requires every match to pass.
            estimates = [self.estimate_group(row, observation) for row in matches]
            return min(estimates, key=self._risk_key)
        else:
            if len(groups) != 1:
                return unavailable(None, "specify group_id or monster: %d groups visible"
                                    % len(groups), ["group.selection"])
            selected = groups[0]
        return self.estimate_group(selected, observation)

    def _player(self, observation: Optional[Dict[str, Any]] = None) -> PlayerFacts:
        """The player's side, priced once per character sheet.

        Current HP comes from the live snapshot, not from the sheet: a sheet
        refreshed minutes ago still says full HP after a fight has already cost
        half of it, and the HP-loss rule is measured against what is left.
        """
        if self._player_cache is None:
            self.warm()
            spells, _by_breed = self._spellbook  # type: ignore[misc]
            self._player_cache = _player_facts(self.character, spells)
        player = self._player_cache
        live_hp = _hp_of(observation)
        if live_hp is not None and live_hp > 0 and player.max_hp > 0:
            player.current_hp = min(float(live_hp), player.max_hp)
        return player

    def estimate_group(self, group: Dict[str, Any],
                       observation: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if self.character is None:
            return unavailable(
                _as_int(group.get("group_id")), "no character sheet: fights cannot be estimated",
                ["character.sheet"],
            )
        if self.store is None:
            return unavailable(
                _as_int(group.get("group_id")), "knowledge store is missing",
                ["knowledge.store"],
            )
        try:
            player = self._player(observation)
            enemy = _enemy_facts(group, self.store, self._monster_cache)
        except Exception as exc:  # noqa: BLE001 - report, do not crash the loop
            return unavailable(_as_int(group.get("group_id")),
                               "estimate failed: %s: %s" % (type(exc).__name__, exc))
        return estimate_from_facts(
            player, enemy, calibrator=self.calibrator,
            min_p_win=self.min_p_win,
            max_hp_loss_fraction=self.max_hp_loss_fraction,
            allow_boss=self.allow_boss,
            bootstrap=self.bootstrap,
            tutorial=bool(((observation or {}).get("tutorial") or {}).get("active")),
        )

    def annotate(self, observation: Optional[Dict[str, Any]],
                 groups: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Attach a compact estimate for every group on the map.

        The planner reads this on every observation; the actor is never given it.
        Cached by map and group composition, because the character sheet and the
        group levels do not change between two snapshots of the same fight.
        `groups` lets a caller that already parsed the snapshot pass its list
        instead of taking the same snapshot apart twice.
        """
        if not isinstance(observation, dict):
            return observation  # type: ignore[return-value]
        if not self.ready() or observation.get("in_fight"):
            return observation
        if groups is None:
            groups = self.groups(observation)
        if not groups:
            return observation
        map_id = observation.get("map_id")
        if map_id is None:
            position = observation.get("position")
            map_id = position.get("map_id") if isinstance(position, dict) else None
        # HP belongs in the key: the same group is a different risk at 900 HP
        # than at 200, and the HP-loss rule reads the current value.
        key = (_hp_of(observation), map_id, tuple(
            (row.get("group_id"), tuple(row.get("monster_ids") or ()),
             tuple(member.get("level") for member in row.get("members") or []
                   if isinstance(member, dict)))
            for row in groups
        ))
        if key == self._group_cache_key:
            summaries = self._group_cache
        else:
            summaries = []
            for group in groups:
                estimate = self.estimate_group(group, observation)
                summaries.append(self._summary(estimate))
            self._group_cache_key = key
            self._group_cache = summaries
        out = dict(observation)
        out["fight_estimates"] = list(summaries)
        return out

    @staticmethod
    def _summary(estimate: Dict[str, Any]) -> Dict[str, Any]:
        enemies = estimate.get("enemies") or {}
        return {
            "group_id": estimate.get("group_id"),
            "status": estimate.get("status"),
            "confidence": estimate.get("confidence"),
            "p_win": estimate.get("p_win"),
            "raw_score": estimate.get("raw_score"),
            "expected_turns": estimate.get("expected_turns"),
            "expected_hp_loss": estimate.get("expected_hp_loss"),
            "hp_loss_fraction": estimate.get("hp_loss_fraction"),
            "count": enemies.get("count"),
            "total_hp": enemies.get("total_hp"),
            "has_boss": enemies.get("has_boss"),
            "allowed": estimate.get("allowed"),
            "authorization": estimate.get("authorization"),
            "bootstrap": bool(estimate.get("bootstrap")),
            "tutorial_fight": bool(estimate.get("tutorial_fight")),
            "blocked_reason": estimate.get("blocked_reason"),
            "missing": estimate.get("missing") or [],
        }

    @staticmethod
    def _risk_key(estimate_or_group: Any) -> Tuple[float, float]:
        """Sort groups worst-first: blocked, then lowest raw score."""
        if not isinstance(estimate_or_group, dict):
            return (0.0, 0.0)
        return (
            1.0 if estimate_or_group.get("allowed") else 0.0,
            float(estimate_or_group.get("raw_score")
                  if isinstance(estimate_or_group.get("raw_score"), (int, float))
                  else -1.0),
        )

    # -- the guard ---------------------------------------------------------

    def assess_call(self, tool: str, args: Dict[str, Any],
                    observation: Optional[Dict[str, Any]]) -> Optional[str]:
        """Return a refusal reason for an unsafe fight call, or None.

        `fight.join` needs an explicit target so the estimate is about the group
        the client will actually attack; the sole exception is a map with exactly
        one visible group, where the target is unambiguous. `farm.start` may
        roam: when the target is not visible there is nothing to estimate and
        the call is allowed, but a visible matching group must pass first.
        """
        self.last_estimate = None
        if tool != "fight.join" and tool != "farm.start":
            return None
        if isinstance(observation, dict) and observation.get("in_fight"):
            return None

        if tool == "fight.join":
            group_id = args.get("group_id")
            monster = args.get("monster")
            if group_id is None and monster is None:
                # The client joins the nearest group. That is only unambiguous
                # when the map shows exactly one, so refuse rather than guess.
                visible = self.groups(observation)
                if len(visible) != 1:
                    return ("refused: fight.join needs a group_id or monster so the "
                            "fight can be estimated before it starts (%d groups "
                            "visible)" % len(visible))
            estimate = self.estimate(observation, group_id=group_id, monster=monster)
            self.last_estimate = estimate
            if estimate.get("allowed"):
                return None
            return "refused: fight blocked — " + str(estimate.get("blocked_reason")
                                                      or "estimate unavailable")

        raw = args.get("monster")
        if raw is None:
            # Harvest-only or resource farming: no fight is promised.
            return None
        monster_id = self.resolve_monster(raw)
        if monster_id is None:
            return ("refused: farm.start target %r cannot be resolved to a monster id, "
                    "so no fight can be estimated" % (raw,))
        groups = [
            row for row in self.groups(observation)
            if monster_id in [_as_int(x) for x in row.get("monster_ids") or []]
        ]
        if not groups:
            self.last_estimate = {
                "status": "roaming",
                "group_id": None,
                "p_win": None,
                "allowed": False,
                "blocked_reason": None,
                "missing": [],
                "note": ("target is not visible on this map; the farm loop will roam, "
                         "and each join it makes is outside this estimate"),
            }
            return None
        estimates = [self.estimate_group(group, observation) for group in groups]
        estimate = min(estimates, key=self._risk_key)
        self.last_estimate = estimate
        if estimate.get("allowed"):
            return None
        return "refused: farm fight blocked — " + str(estimate.get("blocked_reason")
                                                      or "estimate unavailable")


# ---------------------------------------------------------------------------
# outcome log: the other half of a falsifiable claim


class FightOutcomeLog:
    """Pairs the estimate written before a fight with what actually happened."""

    def __init__(self, path: Path = REPO_ROOT / "trajectories" / "fight_outcomes.jsonl") -> None:
        self.path = Path(path)
        self.pending: Optional[Dict[str, Any]] = None

    def start(self, estimate: Optional[Dict[str, Any]],
              observation: Optional[Dict[str, Any]], session: str = "",
              started_by: Optional[str] = None) -> Dict[str, Any]:
        hp = _hp_of(observation)
        fight_id = None
        # Only an in-fight observation carries *this* fight's id. The page keeps
        # the last fight id in its snapshot after the fight is over, so reading
        # it unconditionally made the next fight start with the previous id and
        # resolve with a mismatched end (measured 2026-10-28: row said 124, the
        # GameFightEndMessage said 125).
        if isinstance(observation, dict) and observation.get("in_fight"):
            fight_id = observation.get("fight_id")
            if fight_id is None and isinstance(observation.get("fight"), dict):
                fight_id = observation["fight"].get("fight_id")
        self.pending = {
            "kind": "fight_start",
            "session": session,
            "at": _now(),
            "group_id": (estimate or {}).get("group_id"),
            "estimate": estimate,
            "hp_before": hp,
            "model_version": (estimate or {}).get("model_version"),
            # The call that opened the fight, and the server's fight id. Without
            # them an outcome row cannot say "this fight.join produced a win".
            "started_by": started_by,
            "fight_id": fight_id,
        }
        # Written before the fight, not only when it resolves: a session that
        # dies mid-fight still leaves the claim the estimate made.
        self._write(self.pending)
        return self.pending

    def resolve(self, observation: Optional[Dict[str, Any]],
                session: str = "") -> Optional[Dict[str, Any]]:
        pending = self.pending
        if pending is None:
            return None
        if isinstance(observation, dict) and observation.get("in_world") is False:
            return None  # a disconnected session is not a fight outcome
        hp_after = _hp_of(observation)
        alive = observation.get("alive") if isinstance(observation, dict) else None
        if alive is False or (hp_after is not None and hp_after <= 0):
            won: Optional[bool] = False
        elif alive is True and hp_after is not None:
            won = True
        elif hp_after is not None:
            won = True
        else:
            won = None
        hp_before = pending.get("hp_before")
        hp_loss = (max(0.0, float(hp_before) - float(hp_after))
                   if isinstance(hp_before, (int, float))
                   and isinstance(hp_after, (int, float)) else None)
        end = observation.get("last_fight_end") if isinstance(observation, dict) else None
        fight_id = pending.get("fight_id")
        end_matches = False
        if isinstance(end, dict):
            end_at = end.get("at")
            started = pending.get("at")
            fresh = (not isinstance(end_at, (int, float))
                     or not isinstance(started, (int, float))
                     or float(end_at) / 1000.0 >= float(started) - 1.0)
            end_fight_id = end.get("fight_id")
            same_fight = (fight_id is None or end_fight_id is None
                          or str(fight_id) == str(end_fight_id))
            end_matches = bool(fresh and same_fight)
            if end_matches:
                if fight_id is None:
                    fight_id = end_fight_id
                if end.get("won") is not None:
                    won = bool(end.get("won"))
        # Challenges belong to the end message of *this* fight. A completed one
        # adds CHALLENGE_BONUS; a missed one, or an empty list, adds nothing.
        challenge_rows: List[Dict[str, Any]] = []
        if end_matches and isinstance(end, dict) and isinstance(end.get("challenges"), list):
            challenge_rows = [row for row in end["challenges"] if isinstance(row, dict)]
        challenges_done = sum(1 for row in challenge_rows if row.get("success") is True)
        challenge_bonus = round(CHALLENGE_BONUS * challenges_done, 3)
        reward = round((1.0 if won else 0.0) + challenge_bonus, 3)
        estimate = pending.get("estimate") or {}
        row = {
            "kind": "fight_outcome",
            "session": session or pending.get("session") or "",
            "at": _now(),
            "started_at": pending.get("at"),
            "group_id": pending.get("group_id"),
            "started_by": pending.get("started_by"),
            "fight_id": fight_id,
            "won": won,
            "reward": reward,
            "challenges": challenge_rows,
            "challenges_done": challenges_done,
            "challenge_bonus": challenge_bonus,
            "end": end if isinstance(end, dict) else None,
            "hp_before": hp_before,
            "hp_after": hp_after,
            "hp_loss": round(hp_loss, 1) if hp_loss is not None else None,
            "turns": None,
            "features": estimate.get("features"),
            "estimate": {
                "p_win": estimate.get("p_win"),
                "raw_score": estimate.get("raw_score"),
                "expected_hp_loss": estimate.get("expected_hp_loss"),
                "expected_turns": estimate.get("expected_turns"),
                "allowed": estimate.get("allowed"),
                "model_version": estimate.get("model_version"),
            },
            "source": "live",
            "model_version": pending.get("model_version") or MODEL_VERSION,
        }
        self.pending = None
        self._write(row)
        return row

    def flush(self, status: str = "unresolved") -> None:
        """Record a fight that the session ended before resolving."""
        pending = self.pending
        if pending is None:
            return
        row = {
            "kind": "fight_outcome",
            "session": pending.get("session") or "",
            "at": _now(),
            "started_at": pending.get("at"),
            "group_id": pending.get("group_id"),
            "started_by": pending.get("started_by"),
            "fight_id": pending.get("fight_id"),
            "won": None,
            "status": status,
            "hp_before": pending.get("hp_before"),
            "hp_after": None,
            "hp_loss": None,
            "turns": None,
            "features": (pending.get("estimate") or {}).get("features"),
            "estimate": {
                "p_win": (pending.get("estimate") or {}).get("p_win"),
                "model_version": (pending.get("estimate") or {}).get("model_version"),
            },
            "source": "live",
            "model_version": pending.get("model_version") or MODEL_VERSION,
        }
        self.pending = None
        self._write(row)

    def _write(self, row: Dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass


def _hp_of(observation: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(observation, dict):
        return None
    hp = observation.get("hp")
    if isinstance(hp, dict):
        value = hp.get("life")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    for key in ("lifePoints", "hp"):
        value = observation.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def _now() -> float:
    import time

    return time.time()


def load_calibrator(path: Path = DEFAULT_CALIBRATOR) -> Optional[FightCalibrator]:
    return FightCalibrator.load(path)


# ---------------------------------------------------------------------------
# CLI: fit the probability from live outcomes


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="combat.fight_estimate",
        description="Fit the fight probability from trajectories/fight_outcomes.jsonl.")
    parser.add_argument("--rows", default=str(REPO_ROOT / "trajectories" / "fight_outcomes.jsonl"))
    parser.add_argument("--out", default=str(DEFAULT_CALIBRATOR))
    parser.add_argument("--l2", type=float, default=1.0)
    parser.add_argument("--max-ece", type=float, default=MAX_CALIBRATION_ERROR,
                        help="refuse to install a calibrator above this error")
    parser.add_argument("--force", action="store_true",
                        help="install even when the calibration gate fails")
    parser.add_argument("--source", choices=("live", "sim"), default="live",
                        help="provenance written into the calibrator; sim rows "
                             "are self-play labels, not live evidence")
    args = parser.parse_args(list(argv) if argv is not None else None)

    rows: List[Dict[str, Any]] = []
    path = Path(args.rows)
    if not path.exists():
        raise SystemExit("no outcome rows at %s" % path)
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    model = fit_calibrator(rows, l2=args.l2)
    if model is None:
        raise SystemExit(
            "cannot calibrate: need at least 20 resolved outcomes with both wins and "
            "losses (read %d rows)" % len(rows))
    report = calibration_report(model, rows)
    passed = report["ece"] is not None and float(report["ece"]) <= args.max_ece
    model.metrics = {**report, "max_ece": args.max_ece,
                     "gate_passed": passed, "forced": bool(not passed and args.force)}
    print("outcomes: %d (wins %d, losses %d), ECE %s, Brier %s, accuracy %s"
          % (report["n"], report["wins"], report["losses"],
             report["ece"], report["brier"], report["accuracy"]))
    if not passed and not args.force:
        raise SystemExit(
            "calibration gate failed: ECE %s > %.3f; nothing written. "
            "Collect more fights, or pass --force to install anyway."
            % (report["ece"], args.max_ece))
    model.trained_at = _now()
    model.source = args.source
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(model.to_dict(), indent=1), encoding="utf-8")
    print("calibrated on %d outcomes -> %s" % (model.n, out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
