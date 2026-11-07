"""What a spell *does*, as the client's own tables say -- in a shape a network can read.

The encounter board used to carry seven numbers per spell slot (AP cost, two ranges, damage,
heal, element, ally-side). Everything a boss spell does besides putting a number on a health bar
was discarded before it reached the model: `touch_source.slot()` returns `None` unless the level
has a positive damage or heal line, which measured on the real kits drops 949 of 1,775 boss spell
levels (53.5%) and takes at least one spell away from 456 of 519 boss species. A policy cannot be
trained to react to a mechanic it is never told exists, so no amount of weight editing gets a summon,
a state or a pushback into the fight.

This module turns a `SpellLevel` row into a typed feature record:

  * `kind`  -- what the cast puts on the board, classified from the client's *own* fields
               (`category`, `bonusType`, `characteristic`) and, where those do not name it, the
               description template of the effect id. Not a hand-picked id list: the vocabulary
               is computed over all 700 published effect definitions.
  * `stat`  -- which characteristic a modifier moves, keyed by the client's characteristic id
               rather than by a name I would have to guess.
  * `when`  -- duration, delay, triggers, stack limit, casts per turn/target/fight, cooldown.
  * `where` -- range, line-of-sight, line/diagonal casting, cells that must be free or taken,
               and the raw target mask string, kept as an opaque categorical code.

`where` and `when` are deliberately not interpreted here. The mask alphabet (`a,A`, `h,m,i,s`,
`E869,A`, ...) is the client's private notation and I have no published decode for it; inventing
one is how a "summon" became a "hit" in the last pass. It ships as an input the network learns
from, and only the kinds whose *physics* `combat.encounter.env` actually simulates are marked
`MODELLED`. The rest are visible, countable and labelled as not-yet-simulated.

    python -m combat.encounter.mechanics            # coverage of the real boss kits
    python -m combat.encounter.mechanics --json
"""
from __future__ import annotations

import json
import re
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# The kinds a cast can put on the board. Order is the feature layout: never renumber, append.
KINDS = (
    "damage", "heal", "kill", "summon", "glyph", "state_add", "cleanse",
    "push", "pull", "swap", "teleport", "ap_gain", "ap_loss", "mp_gain", "mp_loss",
    "stat_up", "stat_down", "other",
)
KIND_INDEX = {name: i for i, name in enumerate(KINDS)}

# Mechanics the environment applies for real. A kind that is not here reaches the network as an
# input and reaches the fight as nothing: the corpus has to say so rather than imply otherwise.
MODELLED = frozenset({"damage", "heal", "kill", "push", "pull", "ap_gain", "ap_loss",
                      "mp_gain", "mp_loss", "stat_up", "stat_down"})

# Effect ids whose semantics are settled by the client's own text, not by inference.
NAMED_KIND = {
    141: "kill", 181: "summon", 1011: "summon", 400: "glyph", 401: "glyph", 1091: "glyph",
    950: "state_add", 951: "cleanse", 132: "cleanse",
    4: "teleport", 5: "push", 1041: "push", 6: "pull", 1042: "pull", 8: "swap",
}
ELEMENT_DAMAGE = {96: "water", 97: "earth", 98: "air", 99: "fire", 100: "neutral"}

# The `{~1~2 to }` slot marker and `#N` substitutions carry the dice, not the meaning.
_TEMPLATE = re.compile(r"#\d|\{~1~2 to\s*-?\}|~1~2|\{\}")
_AP = re.compile(r"\baction points?\b|\bAP\b", re.I)
_MP = re.compile(r"\bmovement points?\b|\bMP\b|\bPO\b|\bPM\b", re.I)
_HEAL = re.compile(r"hp restored|life|heal|soin", re.I)
_STOLEN = re.compile(r"steal|volent", re.I)
_STATE = re.compile(r"\bstate\b|\betat\b", re.I)


def _template_words(text: str) -> str:
    return _TEMPLATE.sub(" ", text or "").strip()


def _signed(text: str) -> Tuple[int, str]:
    """(sign, body) from a template that begins or ends with a minus."""
    body = _template_words(text)
    return (-1 if body.lstrip().startswith("-") else 1), body.strip("-+ ").strip()


def classify_effect(defn: Dict[str, Any]) -> str:
    """One kind per published effect definition, from the client's fields and wording."""
    eid = int(defn.get("id") or -1)
    if eid in NAMED_KIND:
        return NAMED_KIND[eid]
    if eid in ELEMENT_DAMAGE:
        return "damage"
    text = defn.get("descriptionId") or ""
    sign, body = _signed(text)
    category = int(defn.get("category") or 0)
    characteristic = int(defn.get("characteristic") or 0)
    bonus = int(defn.get("bonusType") or 0) or sign

    if _AP.search(body):
        return "ap_gain" if bonus > 0 else "ap_loss"
    if _MP.search(body):
        return "mp_gain" if bonus > 0 else "mp_loss"
    if _STATE.search(body) and "removes" not in body.lower():
        return "state_add"
    if _HEAL.search(body):
        return "heal"
    if "damage" in body.lower():
        return "damage"
    # `characteristic` is the client's own enum of which stat a modifier moves, and it is set on
    # most of them -- including several the `category` field leaves at 0, which is why typing on
    # category alone mislabelled 200+ boss effect instances as `other`.
    if characteristic or category == 2:
        return "stat_up" if bonus > 0 else "stat_down"
    return "other"


# The client's `characteristic` field is a stat *slot*, not a mechanic. All eight characteristic-16
# effects share the slot "damage", and the wording says three different things: 105 and 265 reduce
# the damage the recipient *takes*, 112/121/137/142 raise the damage the recipient *deals*, 145
# lowers it. Typing the slot would have collapsed a shield into a buff -- the same class of error
# as the 9,999 kill sentinel -- so the state is read off the sentence the client publishes, and an
# effect whose sentence does not match stays untyped rather than guessed.
_STATE_NAME = (
    (re.compile(r"dommages? (?:r[ée]duits?(?: de)?|diminu[ée]s?(?: de)?|absorb[ée]?s? de?)"
                r"|damage(?:s)? (?:reduced? by|absorbed? by|diminished by)", re.I), "shield"),
    (re.compile(r"^[\s\-]*#?\d?.*dommages?.*(?:augment[ée]s?|multipli)"
                r"|^-\s*#?\d?.*\bdommages?\b"
                r"|(?:physical )?damage(?:s)? (?:increased by|multiplied by)", re.I), "power"),
)


def classify_state(defn: Dict[str, Any]) -> str:
    """'shield' | 'power' | '' -- from the client's own sentence about the effect."""
    text = str(defn.get("name") or defn.get("description") or "")
    for pattern, state in _STATE_NAME:
        if pattern.search(text):
            return state
    return ""


class MechanicVocabulary:
    """effect id -> kind, for every definition the client publishes."""

    def __init__(self, effects: Dict[str, Any]):
        self.kind: Dict[int, str] = {}
        self.characteristic: Dict[int, int] = {}
        self.state: Dict[int, str] = {}
        for row in effects.values():
            eid = int(row.get("id") or -1)
            self.kind[eid] = classify_effect(row)
            self.characteristic[eid] = int(row.get("characteristic") or 0)
            self.state[eid] = classify_state(row)

    def __getitem__(self, effect_id: int) -> str:
        return self.kind.get(int(effect_id), "other")


# `EffectInstanceDice` reuses `diceNum`/`diceSide` as payload for effects that do not roll: a
# summon writes the monster id, a glyph writes a four-digit zone code. Real dice are <=100 sides.
def roll_expectation(effect: Dict[str, Any]) -> Tuple[float, bool]:
    dice = int(effect.get("diceNum") or 0)
    sides = int(effect.get("diceSide") or 0)
    base = float(effect.get("value") or 0)
    if dice and 1 <= sides <= 100 and dice <= 100:
        return base + dice * (sides + 1) / 2.0, True
    if base:
        return base, False
    return 0.0, False


MASK_CODES = 32          # opaque target-mask categories, hashed into this many buckets


def mask_code(target_mask: Any) -> int:
    return zlib.crc32(str(target_mask or "").encode()) % MASK_CODES if target_mask else -1


def encode_level(level: Dict[str, Any], vocab: MechanicVocabulary,
                elements: Tuple[str, ...] = ("neutral", "earth", "fire", "water",
                                             "air")) -> Dict[str, Any]:
    """The full typed record of one spell level, before any policy decision is made about it."""
    kinds = [0.0] * len(KINDS)
    stat = [0.0] * 2                     # [+ on that characteristic, - on it]
    stat_chars = [0] * 2
    damage = heal = 0.0
    per_element = [0.0] * len(elements)
    duration = 0
    delayed = 0
    summoned: List[int] = []
    masks = []
    for effect in (level.get("effects") or []):
        eid = int(effect.get("effectId") or -1)
        kind = vocab[eid]
        amount, is_dice = roll_expectation(effect)
        has_duration = int(effect.get("duration") or 0) != 0
        if kind == "damage":
            damage += amount
            element = ELEMENT_DAMAGE.get(eid)
            if element:
                per_element[elements.index(element)] += amount
        elif kind == "heal":
            heal += amount
        elif kind == "summon":
            summoned.append(int(effect.get("diceNum") or 0))
        elif kind in ("stat_up", "stat_down"):
            slot = 0 if kind == "stat_up" else 1
            stat[slot] += amount
            stat_chars[slot] = vocab.characteristic.get(eid, 0)
        kinds[KIND_INDEX[kind]] += 1.0
        if has_duration:
            duration = max(duration, int(effect.get("duration") or 0))
        if int(effect.get("delay") or 0):
            delayed += 1
        masks.append(mask_code(effect.get("targetMask")))

    dominant = max(range(len(elements)), key=lambda i: per_element[i]) if damage > 0 else 0
    return {
        "ap": float(level.get("apCost") or 0),
        "rmin": float(level.get("minRange") or 0),
        "rmax": float(level.get("range") or 0),
        "damage": damage,
        "heal": heal,
        "element": dominant,
        "per_element": per_element,
        "kinds": kinds,
        "kind_total": sum(kinds),
        "stat": stat,
        "stat_characteristic": stat_chars,
        "duration": duration,
        "delayed": delayed,
        "summon_ids": summoned,
        "mask_codes": masks,
        "casts_per_turn": int(level.get("maxCastPerTurn") or 0),
        "casts_per_target": int(level.get("maxCastPerTarget") or 0),
        "casts_per_fight": int(level.get("maxCastPerFight") or 0),
        "stack": int(level.get("maxStack") or 0),
        "cooldown": int(level.get("globalCooldown") or 0) or int(level.get("minCastInterval") or 0),
        "los": bool(level.get("castTestLos")),
        "line": bool(level.get("castInLine")),
        "diagonal": bool(level.get("castInDiagonal")),
        "needs_free_cell": bool(level.get("needFreeCell")),
        "needs_taken_cell": bool(level.get("needTakenCell")),
        "critical": float(level.get("criticalHitProbability") or 0),
        "failure": float(level.get("criticalFailureProbability") or 0),
        "states_required": list(level.get("statesRequired") or []),
        "states_forbidden": list(level.get("statesForbidden") or []),
    }


# --------------------------------------------------------------------------- features
# The network's spell token. Every number here is a fact about the spell, not a verdict about
# whether it is worth casting: `damage` used to be the only magnitude the model saw, which made a
# dispel and a debuff indistinguishable from a pass.
FEATURE_WIDTH = (
    5                                   # ap, rmin, rmax, log damage, log heal
    + 5                                 # element one-hot
    + len(KINDS)                        # what the cast puts on the board
    + 2 + 2                             # stat magnitudes and their characteristic ids
    + 7                                 # duration, delay, cast caps, stack, cooldown
    + 5                                 # los, line, diagonal, free cell, taken cell
    + 2                                 # crit chance, failure chance
    + MASK_CODES + 1                    # target mask bucket one-hot (+ "no mask" slot)
)


def feature_row(rec: Dict[str, Any], max_dist: float = 12.0) -> List[float]:
    import math
    out: List[float] = []
    out += [rec["ap"] / 12.0, rec["rmin"] / max_dist, rec["rmax"] / max_dist,
            math.log1p(max(rec["damage"], 0.0)) / math.log(301.0),
            math.log1p(max(rec["heal"], 0.0)) / math.log(301.0)]
    element = [0.0] * 5
    element[int(rec["element"]) % 5] = 1.0
    out += element
    out += [min(1.0, k / 4.0) for k in rec["kinds"]]
    out += [rec["stat"][0] / 50.0, -rec["stat"][1] / 50.0,
            rec["stat_characteristic"][0] / 128.0, rec["stat_characteristic"][1] / 128.0]
    out += [rec["duration"] / 6.0, float(rec["delayed"]) / 2.0,
            rec["casts_per_turn"] / 4.0, rec["casts_per_target"] / 2.0,
            rec["casts_per_fight"] / 2.0, rec["stack"] / 3.0, rec["cooldown"] / 6.0]
    out += [float(rec["los"]), float(rec["line"]), float(rec["diagonal"]),
            float(rec["needs_free_cell"]), float(rec["needs_taken_cell"])]
    out += [rec["critical"] / 100.0, rec["failure"] / 100.0]
    mask = [0.0] * (MASK_CODES + 1)
    for code in rec["mask_codes"]:
        mask[MASK_CODES if code < 0 else code] = 1.0
    out += mask
    return out


def modelled(rec: Dict[str, Any]) -> bool:
    return any(v > 0 for k, v in zip(KINDS, rec["kinds"]) if k in MODELLED)


def unmodelled(rec: Dict[str, Any]) -> List[str]:
    return [k for k, v in zip(KINDS, rec["kinds"]) if v > 0 and k not in MODELLED]


_OFFICIAL_KIND = (
    (re.compile(r"damage\)", re.I), "damage"),
    # "Steals" is not one mechanic. The client writes "Steals 2 AP" and "Steals 10 Intelligence"
    # with the same verb, and the environment treats them differently: one removes action points
    # for a round, the other moves a characteristic. Keyed off the noun the sentence steals *of*.
    (re.compile(r"steals?[^\(]*?\b(action points?|ap)\b", re.I), "ap_loss"),
    (re.compile(r"steals?[^\(]*?\b(movement points?|mp|pm)\b", re.I), "mp_loss"),
    (re.compile(r"heal|life points", re.I), "heal"),
    (re.compile(r"summon|invoke", re.I), "summon"),
    (re.compile(r"pushes? (back|away)|repel", re.I), "push"),
    (re.compile(r"\bpull|draw|attract", re.I), "pull"),
    (re.compile(r"teleport", re.I), "teleport"),
    (re.compile(r"state|curse|bless", re.I), "state_add"),
    (re.compile(r"removes? (all|the)", re.I), "cleanse"),
    (re.compile(r"steals?", re.I), "stat_down"),
    (re.compile(r"action points?", re.I), "ap_gain"),
    (re.compile(r"movement points?", re.I), "mp_gain"),
)
_TURNS = re.compile(r"\((\d+) ?turns?\)", re.I)
# The number a sentence pays with: "Steals 2 AP", "Pushback 4 cells". The "(3 turns)" tail is
# duration, not magnitude, so it is removed before the first digit is taken.
_AMOUNT = re.compile(r"(\d+)")


def encode_official(spell: Dict[str, Any]) -> Dict[str, Any]:
    """The same typed record, built from the encyclopedia's own wording.

    The client's monster tables give effect ids; the class-spell database gives sentences and a
    normalised characteristics block, nested under `levels` -- one entry per spell level, of which
    the first is what the game casts. Both describe the same cast, and the network must not care
    which source a spell came from, so this returns the identical dict shape, with duration read
    out of the "(3 turns)" the encyclopedia writes into the effect text.

    A record that yields no cost, no range and no effect is refused rather than encoded as zeros:
    the first version of this function read the top-level spell record, where none of those fields
    live, and silently produced a zero feature row for all 330 player spells.
    """
    levels = spell.get("levels")
    if isinstance(levels, list) and levels:
        spell = levels[0]
    kinds = [0.0] * len(KINDS)
    amounts = [0.0] * len(KINDS)
    per_element = [0.0] * len(ELEMENT_INDEX_LIST)
    damage = heal = 0.0
    duration = 0
    for group in ("normal_effects",):
        # One record describes one cast. The encyclopedia's `critical_effects` line is the same
        # cast on a crit, with its own numbers ("13 to 15" against "10 to 12", "Steals 20
        # Intelligence" against 10), and all 1,122 levels that have one word it differently. Adding
        # the two lines made a pushback of 1 cell read as 2 and doubled the damage of every crit
        # spell -- while the corpus reads normal-only, so the two encoders of the same spell
        # disagreed by exactly that factor.
        for effect in (spell.get(group) or []):
            text = (effect.get("text") or "").strip()
            roll = effect.get("damage") or {}
            amount = 0.0
            if roll:
                lo, hi = float(roll.get("min_roll") or 0), float(roll.get("max_roll") or 0)
                amount = (lo + hi) / 2.0
                element = str(roll.get("element") or "NEUTRAL").lower()
                if element in ELEMENTS:
                    per_element[ELEMENTS.index(element)] += amount
                    damage += amount
                    kinds[KIND_INDEX["damage"]] += 1.0
            turns = _TURNS.search(text)
            if turns:
                duration = max(duration, int(turns.group(1)))
            if not roll:
                for pattern, kind in _OFFICIAL_KIND:
                    if pattern.search(text):
                        kinds[KIND_INDEX[kind]] += 1.0
                        body = _TURNS.sub(" ", text)
                        digits = _AMOUNT.search(body)
                        amounts[KIND_INDEX[kind]] += float(digits.group(1)) if digits else 1.0
                        if kind == "heal":
                            heal += (float(digits.group(1)) if digits else 0.0)
                        break
    # A spell with lines in several elements hits with one of them per cast, chosen by the
    # character's build -- 24 Sram and Ecaflip levels publish three equal rolls. Summing them
    # priced a 4-7 Air/Earth/Fire spell as 21 damage, three times what the same spell costs in
    # the corpus generator, which takes its strongest element.
    damage = max(per_element) if per_element else 0.0
    dominant = max(range(len(per_element)), key=lambda i: per_element[i]) if damage > 0 else 0
    chars = spell.get("raw_characteristics") or {}

    def _yes(key: str) -> float:
        return 1.0 if str(chars.get(key, "")).lower().startswith("y") else 0.0

    rec = {"ap": float(spell.get("ap_cost") or spell.get("actionPoints") or 0),
           "rmin": float(spell.get("min_range") or 0),
           "rmax": float(spell.get("max_range") or 0),
           "damage": damage, "heal": heal, "element": dominant,
           "per_element": per_element, "kinds": kinds, "kind_total": sum(kinds),
           "amounts": amounts,
           "stat": [0.0, 0.0], "stat_characteristic": [0, 0], "duration": duration,
           "delayed": 0, "summon_ids": [], "mask_codes": [],
           "casts_per_turn": int(spell.get("max_cast_per_turn") or 0),
           "casts_per_target": 0, "casts_per_fight": 0, "stack": 0, "cooldown": 0,
           "los": bool(_yes("Line of sight")), "line": bool(_yes("Cast in a straight line only")),
           "diagonal": False, "needs_free_cell": False, "needs_taken_cell": False,
           "critical": 0.0, "failure": 0.0, "states_required": [], "states_forbidden": []}
    if not (rec["ap"] or rec["rmax"] or rec["kind_total"] or damage or heal):
        raise ValueError("this encyclopedia spell exposes no cost, no range and no recognised "
                         "effect -- the fields are being read from the wrong level of the record, "
                         "and encoding it as zeros would put a fake spell on the board")
    return rec


# `observe` and the corpus keep elements as an index; `encode_official` needs the same tuple.
ELEMENTS = ("neutral", "earth", "fire", "water", "air")
ELEMENT_INDEX_LIST = ELEMENTS


def mechanic_columns(level: Dict[str, Any],
                     vocab: Optional[MechanicVocabulary] = None) -> Dict[str, float]:
    """The five numbers the environment needs to *apply* a mechanic, not just describe it.

    This is the single place that decides which effect ids drain action points, which push, and
    which kill: `combat/encounter/touch_source.py` used to carry its own id map, and two lists of
    ids that can drift apart is how a "summon" ended up priced as a hit. A spell can now reach the
    board with no damage line at all and still change the fight, which is the difference between
    typing a mechanic and simulating it.
    """
    vocab = vocab or MechanicVocabulary({})
    out = {"kill": 0.0, "push": 0.0, "ap_delta": 0.0, "mp_delta": 0.0, "duration": 0.0}
    for effect in (level.get("effects") or []):
        eid = int(effect.get("effectId") or -1)
        kind = vocab.kind.get(eid) or classify_effect(
            {"id": eid, "descriptionId": "", "category": 0, "bonusType": 0,
             "characteristic": 0})
        amount, _ = roll_expectation(effect)
        duration = int(effect.get("duration") or 0)
        if duration:
            out["duration"] = max(out["duration"], float(duration))
        if kind == "kill":
            out["kill"] = 1.0
        elif kind == "push":
            out["push"] += amount
        elif kind == "pull":
            out["push"] -= amount
        elif kind == "ap_loss":
            out["ap_delta"] -= amount
        elif kind == "ap_gain":
            out["ap_delta"] += amount
        elif kind == "mp_loss":
            out["mp_delta"] -= amount
        elif kind == "mp_gain":
            out["mp_delta"] += amount
    return out


def columns_from_record(rec: Dict[str, Any]) -> Dict[str, float]:
    """The five environment columns, read off a typed record instead of effect ids.

    `mechanic_columns` walks a monster spell's `effects` list; the class-spell encyclopedia
    describes the same cast in prose, which `encode_official` has already typed into `kinds`.
    Deriving the columns from the record keeps one mapping from semantics to physics: a spell that
    the encoder says drains AP drains AP in the fight, whichever table it came from.
    """
    k = rec.get("kinds") or []
    a = rec.get("amounts") or []

    def count(name: str) -> float:
        i = KIND_INDEX[name]
        return float(a[i]) if i < len(a) else (float(k[i]) if i < len(k) else 0.0)

    return {
        "kill": 1.0 if (k[KIND_INDEX["kill"]] if KIND_INDEX["kill"] < len(k) else 0) else 0.0,
        "push": count("push") - count("pull"),
        "ap_delta": count("ap_gain") - count("ap_loss"),
        "mp_delta": count("mp_gain") - count("mp_loss"),
        "duration": float(rec.get("duration") or 0.0),
    }


# --------------------------------------------------------------------------- report
def _load(root: Path, name: str) -> Dict[str, Any]:
    return json.loads((root / name).read_text(encoding="utf-8"))


def report(root: Path) -> Dict[str, Any]:
    vocab = MechanicVocabulary(_load(root, "effects_en.json"))
    levels = _load(root, "spelllevels_en.json")
    monsters = _load(root, "monsters_en.json")
    by_spell: Dict[int, List[Dict[str, Any]]] = {}
    for row in levels.values():
        by_spell.setdefault(int(row["spellId"]), []).append(row)
    for rows in by_spell.values():
        rows.sort(key=lambda r: int(r.get("id") or 0))

    boss_ids = [int(m["id"]) for m in monsters.values()
                if m.get("isBoss") or m.get("isMiniBoss")]
    kinds = {k: 0 for k in KINDS}
    total = is_modelled = 0
    species_with_state = species_with_summon = 0
    for mid in boss_ids:
        monster = monsters[str(mid)]
        had_state = had_summon = False
        for spell_id in (monster.get("spells") or []):
            rows = by_spell.get(int(spell_id)) or []
            if not rows:
                continue
            rec = encode_level(rows[0], vocab)
            total += 1
            for k, v in zip(KINDS, rec["kinds"]):
                if v > 0:
                    kinds[k] += 1
            if rec["kinds"][KIND_INDEX["state_add"]] > 0:
                had_state = True
            if rec["kinds"][KIND_INDEX["summon"]] > 0:
                had_summon = True
            if modelled(rec):
                is_modelled += 1
        species_with_state += int(had_state)
        species_with_summon += int(had_summon)
    return {"boss_species": len(boss_ids), "boss_spell_levels": total,
            "levels_with_a_modelled_effect": is_modelled,
            "levels_with_only_unmodelled_effects": total - is_modelled,
            "species_that_put_a_state_on_the_board": species_with_state,
            "species_that_summon": species_with_summon,
            "effect_instances_by_kind": dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
            "feature_width": FEATURE_WIDTH}


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    import os
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=os.environ.get(
        "DOFUS_EVIDENCE",
        str(Path.home() / "Documents" / "dofus-evidence")) + os.sep + "touch-data")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    out = report(Path(args.root))
    if args.json:
        print(json.dumps(out, indent=1, sort_keys=True))
    else:
        for key, value in out.items():
            print("%-42s %s" % (key, value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
