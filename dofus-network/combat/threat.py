"""What the enemies can put on a cell before the character acts again.

The solver priced only the half of a turn it *deals*. Nothing priced the half a
turn *invites*: a cast from the open and the same cast from behind a wall scored
identically, so the beam had no reason to hold range or use cover, and the move
head -- trained in a simulator where distance was the only positional signal --
walked the character in. The live evidence is blunt about the cost: the last
completed fight (1666, four level-8 monsters against a level-12 character) ended
`loss` with **140 HP taken while three enemies were barely touched**, and two of
the four enemies had no line of sight to the character at the moment it decided
to close.

This module prices the other half. For a cell it answers one question: if the
character stands there, how much damage can the living enemies put on it before
its next turn? Everything the solver needs is already on the board -- enemy
cells, enemy AP/MP, and the enemy's own spells when the client publishes them.

Where the client publishes nothing (measured: one of four monsters carried a
spell list on 2026-11-10) the model falls back in two steps, in this order:

1. **learned** -- `models/threat_model.json`, filled by `observe()` from the
   damage the monsters actually dealt in fights already fought, keyed by name
   because a fighter id (-1, -2, ...) is per-fight and means nothing tomorrow;
2. **prior** -- one hit worth ``level * 1.5`` at range ``5 + MP``, which is the
   level-8 group's measured output rather than a guess about the client.

The number is used as a *relative* price between cells on the same board, so an
error in the absolute scale moves every candidate together and cancels. That is
why this can ship before the learned table has any rows.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Sequence, Tuple

from combat.damage_calculator import CharacterStats, DofusDamageCalculator

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime import cycle
    from combat.battle_solver import Board, Fighter, LiveSpell

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MEMORY = Path(
    os.environ.get("DOFUS_THREAT_MODEL")
    or (REPO_ROOT / "models" / "threat_model.json")
)

# A monster that publishes no spells at all.
#
# Measured, not assumed: fight 1666 put four level-8 monsters against a
# level-12 character and took 140 HP in one round, so one hit from a level-8
# monster is worth roughly twelve points and the group fits inside a turn.
PRIOR_DAMAGE_PER_LEVEL = 1.5
PRIOR_RANGE = 5
PRIOR_MIN_DAMAGE = 4.0
# How many hits before the learned range replaces the prior, and how far past the
# prior it is allowed to go.
#
# Damage is trusted from the first sample -- it is a life total the client
# publishes. Range is derived from a cast message's cell fields, which is one
# misread away from turning a melee monster into a sniper, and under-pricing
# reach is the exact bug this module exists to fix. So the learned range needs a
# pattern behind it and a ceiling above it.
RANGE_SAMPLES = 3
RANGE_SLACK = 2
# A monster with 6 AP and a 3 AP spell can hit twice; nothing observed hits
# more than twice in a turn, and an unbounded count would price a boss as an
# execution and make every cell look lethal.
MAX_HITS_PER_TURN = 2

# Weight of one point of expected incoming damage against one point of damage
# dealt, used by the solver's positional term.
#
# Below 1.0 on purpose: this is a tie-break, not a strategy. A cast that lands
# must still outrank a retreat, or the character kites in circles and never
# kills anything -- the failure mode measured on 355 of 850 live turns, which
# ended in a pass while holding a castable spell.
POSITION_WEIGHT = float(os.environ.get("DOFUS_COMBAT_POSITION_WEIGHT") or 0.45)
# How much the price rises as health runs out.
#
# At full health the trade is "a cast must outrank a retreat", because a character
# that declines to attack never finishes a fight. At a quarter health the same trade
# is wrong: 40 more incoming damage is death, and 20 less damage dealt is a slower
# death. So the weight scales with the health actually missing -- 0.45 at full,
# 1.35 when nearly dead -- which is the "do damage *and* do not get hit" trade
# shifting to the second half as the first becomes impossible.
DESPERATION_GAIN = float(os.environ.get("DOFUS_COMBAT_DESPERATION_GAIN") or 2.0)


def weight_for(me: Any) -> float:
    """The price of one point of incoming damage for this fighter, right now."""
    if me is None:
        return POSITION_WEIGHT
    max_hp = float(getattr(me, "max_hp", 0) or 0)
    if max_hp <= 0:
        return POSITION_WEIGHT
    remaining = max(0.0, min(1.0, float(getattr(me, "hp", 0) or 0) / max_hp))
    return POSITION_WEIGHT * (1.0 + DESPERATION_GAIN * (1.0 - remaining))
# One turn's exposure is capped before it is priced.
#
# The cap exists so the term cannot veto a whole turn, not to hide a real number.
# Measured on the live boards of 2026-11-10: a two-monster turn is 57 points
# (Marbmour 33 + Dolomanus 24, both learned from the fight itself) and the
# character walked from a covered cell into exactly that, which is the behaviour
# being fixed. A cap of 30 priced half of it; 60 covers the realistic turn and
# still leaves a 44-damage cast (the measured output of spell 61) winning at the
# shipped weight.
MAX_PRICED_SWING = 60.0


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _cell_distance(first: int, second: int) -> float:
    """Manhattan distance between two cells, in the client's own coordinates.

    Imported lazily: `combat.battle_solver` imports this module, and the cell
    geometry lives there. Duplicating the coordinate formula here would let the
    two drift apart on an upgrade, which is the one thing a threat range must
    never do.
    """
    from combat.battle_solver import cell_point

    ax, ay = cell_point(first)
    bx, by = cell_point(second)
    return float(abs(ax - bx) + abs(ay - by))


# ---------------------------------------------------------------------------
# the client's own spellbook, read back from recorded fights
# ---------------------------------------------------------------------------

_CORPUS_REACH: Optional[Dict[str, Optional[int]]] = None


def _corpus_reach(name: Optional[str], level: Any) -> Optional[int]:
    """How far a named monster reaches, from the recorded fights, or ``None``.

    Loaded once: `from_live_corpus` walks the board corpus, while `_profile` runs
    once per enemy per priced cell, so a scan inside this function would turn one
    decision into thousands of file reads.

    Every failure returns ``None`` on purpose. This is an optional refinement of a
    prior that already works, so a missing corpus, an unreadable file or an unknown
    monster must all fall through to the prior rather than break a live decision --
    a fight that cannot price its cells has no fallback at all.
    """
    global _CORPUS_REACH
    if _CORPUS_REACH is None:
        _CORPUS_REACH = {}
        try:
            from combat.monster_mech import MonsterMechanics
            table = MonsterMechanics.from_live_corpus()
        except (OSError, ValueError, ImportError):
            table = None
        if table is not None:
            # `reach` takes a monster *reference* and keys it itself, so it must be
            # handed the name, not the table's already-prefixed key. The loop variable
            # is `grade_level` and not `level`: it shadowed the parameter, so the
            # lookup below was keyed on whichever grade happened to be visited last
            # (`Ayin Spyr` at 50) and every other monster missed.
            for entry in getattr(table, "_monsters", {}).values():
                label = str(entry.get("name") or "").strip().lower()
                for grade in entry.get("grades") or ():
                    grade_level = grade.get("level") or 1
                    reach = table.reach(entry.get("name"), grade_level)
                    if reach is not None and label:
                        _CORPUS_REACH["%s@%s" % (label, grade_level)] = reach
    if not name:
        return None
    return _CORPUS_REACH.get("%s@%s" % (str(name).strip().lower(), int(level or 1)))


def reach_of(fighter: Any, memory: Optional["ThreatMemory"] = None) -> Optional[int]:
    """How far this fighter can damage in one turn, in the order the price uses.

    Board spells first (the client published them, so they are exact), then the
    learned range once there is a pattern behind it, then the corpus, then the
    level prior. ``None`` only when the fighter is not a threat at all.

    This exists so the encoder and the price cannot disagree: `_fighters` in
    `combat/grid_dataset` writes this into a feature slot that used to be a
    hardcoded zero, and `ThreatModel._profile` prices cells from the same
    decision. Two copies of this ladder would drift, and the drift would look
    like a policy that walks into a reach the price already knew about.
    """
    mp = max(0, int(getattr(fighter, "mp", 0) or 0))
    known = [s for s in (getattr(fighter, "spells", None) or ())
             if getattr(s, "has_damage", False) and getattr(s, "castable", False)]
    if known:
        return int(max(int(s.max_range) for s in known) + mp)
    row = memory.profile(getattr(fighter, "name", None)) if memory is not None else None
    if row is not None:
        seen = float(row.get("range", 0.0))
        if float(row.get("samples", 0.0)) >= RANGE_SAMPLES and seen > 0:
            return int(min(seen, PRIOR_RANGE + RANGE_SLACK) + mp)
    observed = _corpus_reach(getattr(fighter, "name", None),
                             getattr(fighter, "level", 1))
    if observed is not None:
        return int(observed)
    return PRIOR_RANGE + mp


# ---------------------------------------------------------------------------
# learned per-monster damage
# ---------------------------------------------------------------------------


@dataclass
class ThreatMemory:
    """Damage per monster, learned from fights that were actually fought.

    Keyed by name. A fighter id is negative and per-fight on this client
    (``-1``, ``-2``, ...) and the generic id is not published for most
    monsters, so the name is the only identity that survives a new fight.

    Each row keeps the sample count and the running mean rather than a sum, so
    the file stays small and a single outlier cannot rewrite the profile.
    """

    monsters: Dict[str, Dict[str, float]] = field(default_factory=dict)

    # -- learning ---------------------------------------------------------
    def record(self, name: str, damage: float, distance: Optional[float] = None,
               hits: int = 1) -> None:
        key = (name or "").strip()
        if not key or damage <= 0:
            return
        row = self.monsters.setdefault(
            key, {"samples": 0.0, "damage": 0.0, "range": 0.0, "hits": 0.0})
        samples = row.get("samples", 0.0) + 1.0
        row["samples"] = samples
        row["damage"] = row.get("damage", 0.0) + (damage - row.get("damage", 0.0)) / samples
        row["hits"] = row.get("hits", 0.0) + (float(max(1, hits)) - row.get("hits", 0.0)) / samples
        if distance is not None and distance >= 0:
            # The range is a *maximum* seen, not a mean: a monster that once hit
            # from six cells away can hit from six cells away again, and the
            # cheapest mistake here is believing a threat is out of reach.
            row["range"] = max(row.get("range", 0.0), float(distance))

    def observe(self, battle_log: Iterable[Any], my_id: Any,
                names: Dict[int, str]) -> int:
        """Fold one fight's message log in. Returns the number of hits learned.

        The client numbers the *effect*, not the spell, so a cast and its damage
        do not share an id (`tools/damage_calibration.py` documents the same
        trap). Order is the only thing the messages guarantee: a loss belongs to
        the most recent cast by the same source before it.
        """
        try:
            my = int(my_id)
        except (TypeError, ValueError):
            return 0
        learned = 0
        pending: Dict[int, Dict[str, Any]] = {}
        for row in battle_log or ():
            if not isinstance(row, dict):
                continue
            kind = row.get("type")
            source = _int(row.get("source"), 0)
            if kind == "GameActionFightSpellCastMessage":
                if source in names:
                    cell = row.get("cell")
                    dest = row.get("destination_cell")
                    pending[source] = {"cell": cell, "destination_cell": dest}
                continue
            if kind != "GameActionFightLifePointsLostMessage":
                continue
            if source not in names or _int(row.get("target"), 0) != my:
                continue
            loss = _num(row.get("loss"))
            if loss <= 0:
                continue
            cast = pending.get(source) or {}
            distance = None
            if cast.get("cell") is not None and cast.get("destination_cell") is not None:
                distance = _cell_distance(_int(cast["cell"]),
                                          _int(cast["destination_cell"]))
            self.record(names[source], loss, distance)
            learned += 1
        return learned

    def profile(self, name: str) -> Optional[Dict[str, float]]:
        row = self.monsters.get((name or "").strip())
        if not row or row.get("samples", 0.0) <= 0:
            return None
        return row

    # -- persistence ------------------------------------------------------
    def as_dict(self) -> Dict[str, Any]:
        return {"version": 1, "monsters": self.monsters}

    @classmethod
    def from_dict(cls, data: Any) -> "ThreatMemory":
        if not isinstance(data, dict):
            return cls()
        rows = data.get("monsters")
        if not isinstance(rows, dict):
            return cls()
        clean: Dict[str, Dict[str, float]] = {}
        for key, value in rows.items():
            if not isinstance(value, dict):
                continue
            clean[str(key)] = {
                "samples": _num(value.get("samples")),
                "damage": _num(value.get("damage")),
                "range": _num(value.get("range")),
                "hits": _num(value.get("hits"), 1.0),
            }
        return cls(clean)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "ThreatMemory":
        target = Path(path or DEFAULT_MEMORY)
        try:
            return cls.from_dict(json.loads(target.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return cls()

    def save(self, path: Optional[Path] = None) -> None:
        target = Path(path or DEFAULT_MEMORY)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(self.as_dict(), indent=1, sort_keys=True),
                              encoding="utf-8")
        except OSError:
            pass


# ---------------------------------------------------------------------------
# per-board threat
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EnemyThreat:
    """One enemy's output against the character, as far as we can tell."""

    fighter_id: int
    name: str
    cell: int
    damage: float
    reach: int
    hits: int
    requires_los: bool
    source: str
    mp: int = 0

    @property
    def total(self) -> float:
        return self.damage * self.hits


class ThreatModel:
    """Expected incoming damage per cell for one board.

    Cheap by construction: the enemy profiles are built once, and each cell is
    priced once and memoised, so a beam that visits the same cell from ten
    nodes pays for it once. A board has at most 560 cells.
    """

    def __init__(self, board: "Board", memory: Optional[ThreatMemory] = None,
                 occupied: Optional[frozenset] = None) -> None:
        self.board = board
        self.cells = board.cells
        me = board.me
        self.my_id = board.my_id
        self.occupied = occupied if occupied is not None else frozenset(
            f.cell for f in board.fighters if f.alive and f.id != board.my_id)
        self.enemies: List[EnemyThreat] = []
        for fighter in board.fighters:
            if not fighter.alive or fighter.id == board.my_id:
                continue
            if fighter.team == "player":
                continue
            self.enemies.append(self._profile(fighter, me, memory))
        self._cache: Dict[int, float] = {}
        self._open = 0.0

    # -- construction -----------------------------------------------------
    def _profile(self, fighter: "Fighter", me: Optional["Fighter"],
                 memory: Optional[ThreatMemory]) -> EnemyThreat:
        mp = max(0, int(fighter.mp))
        known = [spell for spell in (fighter.spells or ())
                 if spell.has_damage and spell.castable]
        if known and me is not None:
            best = 0.0
            reach = 0
            cheapest: Optional[int] = None
            for spell in known:
                best = max(best, self._hit(spell, fighter, me))
                reach = max(reach, int(spell.max_range))
                cost = max(1, int(spell.ap_cost))
                cheapest = cost if cheapest is None else min(cheapest, cost)
            if best > 0:
                ap = max(1, int(fighter.ap))
                hits = min(MAX_HITS_PER_TURN, max(1, ap // (cheapest or 1)))
                return EnemyThreat(
                    fighter_id=fighter.id, name=fighter.name, cell=fighter.cell,
                    damage=best, reach=reach + mp, hits=hits,
                    requires_los=all(bool(s.requires_los) for s in known),
                    source="spells", mp=mp)
        row = memory.profile(fighter.name) if memory is not None else None
        if row is not None:
            seen = float(row.get("range", 0.0))
            samples = float(row.get("samples", 0.0))
            reach = PRIOR_RANGE
            if samples >= RANGE_SAMPLES and seen > 0:
                reach = int(min(seen, PRIOR_RANGE + RANGE_SLACK))
            return EnemyThreat(
                fighter_id=fighter.id, name=fighter.name, cell=fighter.cell,
                damage=max(PRIOR_MIN_DAMAGE, row.get("damage", 0.0)),
                reach=reach + mp,
                hits=int(max(1.0, min(MAX_HITS_PER_TURN, row.get("hits", 1.0)))),
                # A monster that publishes nothing is assumed free to cast
                # through obstacles, which is the *safer* assumption: it keeps
                # cover from being priced as free.
                requires_los=False, source="learned", mp=mp)
        # The client's own spellbook, read back out of the fights already recorded
        # (`monster_mech.from_live_corpus`). This is the only step whose reach is
        # *observed* rather than inferred: the client states the spell's range and the
        # fighter's MP. Measured on the recorded corpus, `Merkaptan Leader` -- a
        # level-5 monster with 0 MP -- reaches ten cells, where the level prior below
        # assumed five. Only the reach is taken from here; the payload's damage scale
        # could not be verified against a measured hit, so damage keeps its prior.
        observed = _corpus_reach(fighter.name, fighter.level)
        if observed is not None:
            level = max(1, int(fighter.level or 1))
            return EnemyThreat(
                fighter_id=fighter.id, name=fighter.name, cell=fighter.cell,
                damage=max(PRIOR_MIN_DAMAGE, level * PRIOR_DAMAGE_PER_LEVEL),
                reach=int(observed), hits=1, requires_los=False,
                source="corpus", mp=mp)
        level = max(1, int(fighter.level or 1))
        return EnemyThreat(
            fighter_id=fighter.id, name=fighter.name, cell=fighter.cell,
            damage=max(PRIOR_MIN_DAMAGE, level * PRIOR_DAMAGE_PER_LEVEL),
            reach=PRIOR_RANGE + mp, hits=1, requires_los=False,
            source="prior", mp=mp)

    @staticmethod
    def _hit(spell: "LiveSpell", caster: "Fighter", me: "Fighter") -> float:
        info = spell.damage_info
        if info is None:
            return 0.0
        stats = CharacterStats(
            name=caster.name or str(caster.id),
            level=max(1, int(caster.level)),
            action_points=max(0, int(caster.ap)),
            movement_points=max(0, int(caster.mp)),
        )
        try:
            result = DofusDamageCalculator.calculate_spell_damage(
                info, stats, me.target_stats())
        except Exception:  # noqa: BLE001 - an unpriced spell is not a priced zero
            return 0.0
        return float(getattr(result, "expected_value", None)
                     or getattr(result, "total_avg", 0.0) or 0.0)

    # -- pricing ----------------------------------------------------------
    def at(self, cell: int) -> float:
        """Expected damage on ``cell`` from every enemy that can reach it."""
        cached = self._cache.get(cell)
        if cached is not None:
            return cached
        total = 0.0
        for threat in self.enemies:
            if threat.hits <= 0 or threat.damage <= 0:
                continue
            distance = self.cells.distance(threat.cell, cell)
            if distance > threat.reach:
                continue
            if threat.requires_los and threat.mp <= 0 \
                    and not self.cells.has_los(threat.cell, cell, self.occupied):
                # It would have to walk for the line, and it has no movement
                # left. This is the case cover is supposed to win.
                continue
            total += threat.total
        self._cache[cell] = total
        return total

    def swing(self, before: int, after: int) -> float:
        """Price of moving ``before`` -> ``after``, capped and signed.

        Positive means the move invites more damage than it leaves behind.
        Capped because a four-monster turn is worth 80 points of exposure while
        a good cast is worth 50: uncapped, the term stops being a tie-break and
        the character refuses to fight at all.
        """
        if before == after:
            return 0.0
        delta = self.at(after) - self.at(before)
        if delta > MAX_PRICED_SWING:
            return MAX_PRICED_SWING
        if delta < -MAX_PRICED_SWING:
            return -MAX_PRICED_SWING
        return delta

    def open_cells(self) -> List[Tuple[int, float]]:
        """Every priced cell, most exposed first. Diagnostics and tests."""
        cells = [c for c in range(len(self.cells.flags)) if self.cells.walkable(c)]
        return sorted(((cell, self.at(cell)) for cell in cells),
                      key=lambda item: (-item[1], item[0]))

    def describe(self) -> Dict[str, Any]:
        return {
            "enemies": [
                {"id": t.fighter_id, "name": t.name, "cell": t.cell,
                 "damage": round(t.damage, 2), "reach": t.reach,
                 "hits": t.hits, "requires_los": t.requires_los,
                 "source": t.source}
                for t in self.enemies
            ],
            "here": round(self.at(self.board.my_cell), 2) if self.board.my_cell is not None else None,
            "total_output": round(sum(t.total for t in self.enemies), 2),
        }


def build(board: "Board", memory: Optional[ThreatMemory] = None,
          occupied: Optional[frozenset] = None) -> ThreatModel:
    return ThreatModel(board, memory=memory, occupied=occupied)


_MEMORY: Optional[ThreatMemory] = None
_MEMORY_PATH: Optional[Path] = None
_MEMORY_STAMP: Optional[float] = None


def default_memory() -> ThreatMemory:
    """The process-wide learned table, re-read when the file changes on disk.

    The solver asks for it on every decision and a fight can ask four times a
    second, so the table is cached. But the same process also *writes* it -- the
    combat server's observer appends every enemy hit -- and a cache that never
    looked again would keep pricing a fight with the table from before the fight
    started. That is the failure that matters here: the learned numbers are
    2-3x the level prior (measured: Marbmour 33, Dolomanus 24, Clobberstone 21,
    against a prior of 12), so a stale copy under-prices every cell by exactly
    the factor that made the character stand in the open.

    A `stat` per call is the price of noticing, and it is microseconds.
    """
    global _MEMORY, _MEMORY_PATH, _MEMORY_STAMP
    stamp: Optional[float] = None
    try:
        stamp = DEFAULT_MEMORY.stat().st_mtime
    except OSError:
        stamp = None
    if _MEMORY is None or _MEMORY_PATH != DEFAULT_MEMORY or stamp != _MEMORY_STAMP:
        _MEMORY = ThreatMemory.load(DEFAULT_MEMORY)
        _MEMORY_PATH = DEFAULT_MEMORY
        _MEMORY_STAMP = stamp
    return _MEMORY


def reload_memory(path: Optional[Path] = None) -> ThreatMemory:
    global _MEMORY, _MEMORY_PATH, _MEMORY_STAMP
    _MEMORY = ThreatMemory.load(path or DEFAULT_MEMORY)
    _MEMORY_PATH = Path(path or DEFAULT_MEMORY)
    try:
        _MEMORY_STAMP = _MEMORY_PATH.stat().st_mtime
    except OSError:
        _MEMORY_STAMP = None
    return _MEMORY


def load_memory(path: Optional[Path] = None) -> ThreatMemory:
    return ThreatMemory.load(path)


def risk(before: int, after: int, model: Optional[ThreatModel],
         weight: float = POSITION_WEIGHT) -> float:
    """The score change for ending a plan on ``after`` instead of ``before``."""
    if model is None:
        return 0.0
    return -weight * model.swing(before, after)
