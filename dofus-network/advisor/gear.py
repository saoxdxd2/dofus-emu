"""Gear and set scoring for a chosen element.

Scores are linear weights over the stat keys recovered by
`build_effect_map.py`. They are not a simulation: two items with the same
score can behave differently in a fight. What the scores are good for is
ranking hundreds of candidate pieces consistently and exposing the trade
(one more AP versus 300 vitality) instead of burying it.

Recipes and encyclopedia stats cover armor, jewellery, shields, trophies and
Dofus. Weapons are not in the Touch encyclopedia dump, so the equipped weapon
is kept and never scored from a missing template.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .models import ELEMENTS, PRIMARY_STAT, CharacterSnapshot, MarketSnapshot
from .repository import (
    SLOT_NAMES,
    SLOT_ORDER,
    EquipmentItem,
    ItemTemplate,
    Repository,
    SetTemplate,
)

BASE_WEIGHTS: Dict[str, float] = {
    "action_points": 45.0,
    "movement_points": 30.0,
    "range": 14.0,
    "vitality": 0.55,
    "wisdom": 0.25,
    "power": 1.4,
    "damage": 3.0,
    "damage_critical": 1.2,
    "critical_hit": 2.0,
    "heals": 1.0,
    "summons": 6.0,
    "ap_reduction": 3.0,
    "mp_reduction": 3.0,
    "dodge": 1.0,
    "tackle": 1.0,
    "dodge_ap": 1.0,
    "dodge_mp": 1.0,
    "prospecting": 0.2,
    "initiative": 0.15,
    "pods": 0.02,
    "damage_push": 1.0,
    "damage_traps": 1.0,
    "reflect_damage": 1.0,
    "reduction_physical": 1.2,
    "reduction_magic": 1.2,
    "critical_resistance": 0.8,
    "res_push": 0.8,
    "power_traps": 0.8,
    "weapon_damage": 1.0,
    "res_percent": 2.0,
    "res_fixed": 0.4,
    "pvp_res": 0.5,
    "damage_element_other": 0.4,
}


def weights_for(focus: Optional[str]) -> Dict[str, float]:
    weights = dict(BASE_WEIGHTS)
    for element, stat in PRIMARY_STAT.items():
        weights[stat] = 1.0 if element == focus else 0.1
        weights["damage_" + element] = 3.0 if element == focus else 0.4
    weights["damage_neutral"] = 0.6
    return weights


def stat_score(stats: Dict[str, float], weights: Dict[str, float]) -> float:
    total = 0.0
    for key, value in stats.items():
        weight = weights.get(key)
        if weight is None:
            if key.startswith("res_percent_"):
                weight = weights["res_percent"]
            elif key.startswith("res_fixed_"):
                weight = weights["res_fixed"]
            elif key.startswith("pvp_res_"):
                weight = weights["pvp_res"]
            elif key.startswith("damage_") and key[7:] in ELEMENTS:
                weight = weights["damage_element_other"]
            else:
                weight = 0.0
        total += float(value) * weight
    return total


def range_score(stats: Dict[str, Any], weights: Dict[str, float]) -> float:
    averaged = {}
    for key, value in stats.items():
        if hasattr(value, "average"):
            averaged[key] = value.average
        elif isinstance(value, (int, float)):
            averaged[key] = float(value)
    return stat_score(averaged, weights)


@dataclass
class Acquisition:
    action: str
    cost: Optional[float] = None
    average: Optional[float] = None
    listing_min: Optional[float] = None
    craft_cost: Optional[float] = None
    recipe_known: bool = False
    ingredient_prices_known: int = 0
    ingredient_count: int = 0
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "cost": round(self.cost, 1) if self.cost is not None else None,
            "average": round(self.average, 1) if self.average is not None else None,
            "listing_min": round(self.listing_min, 1) if self.listing_min is not None else None,
            "craft_cost": round(self.craft_cost, 1) if self.craft_cost is not None else None,
            "recipe_known": self.recipe_known,
            "ingredient_prices_known": self.ingredient_prices_known,
            "ingredient_count": self.ingredient_count,
            "note": self.note,
        }


@dataclass
class SetCandidate:
    set_id: int
    name: str
    level: int
    piece_count: int
    known_pieces: int
    coverage: float
    owned_pieces: int
    missing_pieces: int
    delta_full: float
    delta_owned: float
    delta_slots: float
    bonus_now: float
    bonus_after: float
    action: str
    acquisition: Acquisition
    equipped_assignment: Dict[int, int] = field(default_factory=dict)
    owned_assignment: Dict[int, int] = field(default_factory=dict)
    missing_gids: List[int] = field(default_factory=list)
    owned_not_equipped: List[int] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "set_id": self.set_id,
            "name": self.name,
            "level": self.level,
            "piece_count": self.piece_count,
            "known_pieces": self.known_pieces,
            "coverage": round(self.coverage, 2),
            "owned_pieces": self.owned_pieces,
            "missing_pieces": self.missing_pieces,
            "delta_full": round(self.delta_full, 1),
            "delta_owned": round(self.delta_owned, 1),
            "delta_slots": round(self.delta_slots, 1),
            "bonus_now": round(self.bonus_now, 1),
            "bonus_after": round(self.bonus_after, 1),
            "action": self.action,
            "acquisition": self.acquisition.to_dict(),
            "equipped_assignment": {SLOT_NAMES.get(k, k): v
                                    for k, v in self.equipped_assignment.items()},
            "owned_assignment": {SLOT_NAMES.get(k, k): v
                                 for k, v in self.owned_assignment.items()},
            "missing_gids": self.missing_gids,
            "owned_not_equipped": self.owned_not_equipped,
            "warnings": self.warnings,
        }


@dataclass
class SlotUpgrade:
    position: int
    slot: str
    current_gid: Optional[int]
    current_name: Optional[str]
    current_score: float
    candidate_gid: int
    candidate_name: str
    candidate_level: int
    candidate_score: float
    delta: float
    set_ids: Tuple[int, ...]
    action: str
    acquisition: Acquisition
    owned: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "position": self.position,
            "slot": self.slot,
            "current_gid": self.current_gid,
            "current_name": self.current_name,
            "current_score": round(self.current_score, 1),
            "candidate_gid": self.candidate_gid,
            "candidate_name": self.candidate_name,
            "candidate_level": self.candidate_level,
            "candidate_score": round(self.candidate_score, 1),
            "delta": round(self.delta, 1),
            "set_ids": list(self.set_ids),
            "action": self.action,
            "acquisition": self.acquisition.to_dict(),
            "owned": self.owned,
        }


class GearAdvisor:
    def __init__(
        self,
        repo: Repository,
        character: CharacterSnapshot,
        focus: Optional[str],
        market: Optional[MarketSnapshot] = None,
        level_margin: int = 5,
        top_sets: int = 10,
        top_slot_upgrades: int = 12,
    ) -> None:
        self.repo = repo
        self.character = character
        self.focus = focus
        self.market = market or MarketSnapshot()
        self.level_margin = max(0, int(level_margin))
        self.top_sets = max(1, int(top_sets))
        self.top_slot_upgrades = max(1, int(top_slot_upgrades))
        self.weights = weights_for(focus)
        self.current_items: Dict[int, EquipmentItem] = {
            item.position: item for item in character.equipment
            if item.position is not None
        }
        self.current_slot_scores: Dict[int, float] = {}
        self.current_gid_scores: Dict[int, float] = {}
        self.unknown_equipped: List[int] = []
        for position, item in self.current_items.items():
            template = repo.item(item.gid)
            if template is None:
                self.unknown_equipped.append(item.gid)
                self.current_slot_scores[position] = 0.0
                continue
            score = stat_score(repo.item_stats(template), self.weights)
            self.current_slot_scores[position] = score
            self.current_gid_scores[item.gid] = score

        self.current_set_counts: Dict[int, int] = {}
        for item in self.current_items.values():
            template = repo.item(item.gid)
            set_ids = template.set_ids if template else item.set_ids
            for set_id in set_ids:
                self.current_set_counts[set_id] = self.current_set_counts.get(set_id, 0) + 1
        self.current_bonus = self.bonus_score(self.current_set_counts)
        self.current_total = sum(self.current_slot_scores.values()) + self.current_bonus

    # -- scoring -----------------------------------------------------------
    def item_score(self, item: ItemTemplate) -> float:
        return stat_score(self.repo.item_stats(item), self.weights)

    def bonus_score(self, counts: Dict[int, int]) -> float:
        total = 0.0
        for set_id, count in counts.items():
            if count <= 0:
                continue
            template = self.repo.set(set_id)
            if template is None:
                continue
            total += range_score(template.bonus(count), self.weights)
        return total

    def acquire(self, gid: int, owned: bool) -> Acquisition:
        template = self.repo.item(gid)
        average = self.market.average(gid)
        listing = self.market.best_listing(gid)
        if owned:
            return Acquisition("equip_owned", average=average, listing_min=listing,
                               recipe_known=bool(template and template.ingredients),
                               ingredient_count=len(template.ingredients) if template else 0,
                               note="already owned; equipping is reversible")
        if template is None:
            if average is not None:
                return Acquisition("buy", cost=average, average=average,
                                   listing_min=listing,
                                   note="not in the encyclopedia dump; market price only")
            return Acquisition("unknown", note="no template and no market price")
        craft_cost = None
        known = 0
        if template.ingredients:
            prices = []
            for ingredient, quantity in template.ingredients:
                price = self.market.average(ingredient)
                if price is not None:
                    prices.append(price * quantity)
                    known += 1
            if known == len(template.ingredients):
                craft_cost = sum(prices)
        if average is None and craft_cost is None:
            action = "farm_missing" if not template.ingredients else "craft_unknown_prices"
            return Acquisition(action, recipe_known=bool(template.ingredients),
                               ingredient_prices_known=known,
                               ingredient_count=len(template.ingredients),
                               note="no market average"
                               if not template.ingredients else
                               "recipe exists but ingredient prices are incomplete")
        if average is not None and (craft_cost is None or average <= craft_cost):
            return Acquisition("buy", cost=average, average=average,
                               listing_min=listing, craft_cost=craft_cost,
                               recipe_known=bool(template.ingredients),
                               ingredient_prices_known=known,
                               ingredient_count=len(template.ingredients),
                               note="buy cheaper than known craft cost"
                               if craft_cost is not None else "market average available")
        return Acquisition("craft", cost=craft_cost, average=average,
                           listing_min=listing, craft_cost=craft_cost,
                           recipe_known=True, ingredient_prices_known=known,
                           ingredient_count=len(template.ingredients),
                           note="craft cost below market average")

    # -- current loadout ---------------------------------------------------
    def slot_summary(self) -> List[Dict[str, Any]]:
        out = []
        for position in SLOT_ORDER:
            item = self.current_items.get(position)
            if item is None:
                out.append({"position": position, "slot": SLOT_NAMES.get(position),
                            "gid": None, "score": 0.0, "known": True})
                continue
            template = self.repo.item(item.gid)
            out.append({
                "position": position,
                "slot": SLOT_NAMES.get(position),
                "gid": item.gid,
                "name": template.name if template else None,
                "level": template.level if template else item.level,
                "score": round(self.current_slot_scores.get(position, 0.0), 1),
                "known": template is not None,
            })
        return out

    # -- set candidates ----------------------------------------------------
    def _assign(
        self,
        pieces: Sequence[ItemTemplate],
        current_scores: Dict[int, float],
    ) -> Dict[int, ItemTemplate]:
        used = set()
        assignment: Dict[int, ItemTemplate] = {}
        ordered = sorted(
            pieces,
            key=lambda item: (len(self.repo.item_positions(item)), -self.item_score(item),
                              item.id))
        for piece in ordered:
            options = [position for position in self.repo.item_positions(piece)
                       if position not in used]
            if not options:
                continue
            options.sort(key=lambda position: (
                position in current_scores,
                current_scores.get(position, 0.0),
                SLOT_ORDER.index(position) if position in SLOT_ORDER else 99,
                position,
            ))
            position = options[0]
            used.add(position)
            assignment[position] = piece
        return assignment

    def _candidate_delta(
        self,
        assignment: Dict[int, ItemTemplate],
        set_id: int,
    ) -> Tuple[float, float, float]:
        if not assignment:
            return 0.0, 0.0, 0.0
        delta_slots = 0.0
        new_counts = dict(self.current_set_counts)
        for position, piece in assignment.items():
            current = self.current_items.get(position)
            if current and current.gid != piece.id:
                old_template = self.repo.item(current.gid)
                old_sets = old_template.set_ids if old_template else current.set_ids
                for old_set in old_sets:
                    new_counts[old_set] = max(0, new_counts.get(old_set, 0) - 1)
            delta_slots += self.item_score(piece) - self.current_slot_scores.get(position, 0.0)
            for target_set in piece.set_ids:
                new_counts[target_set] = new_counts.get(target_set, 0) + 1
        # A set cannot contribute more copies than it has pieces.
        template = self.repo.set(set_id)
        if template and template.piece_ids:
            new_counts[set_id] = min(new_counts.get(set_id, 0), len(template.piece_ids))
        bonus_after = self.bonus_score(new_counts)
        return delta_slots, bonus_after - self.current_bonus, bonus_after

    def _candidate(
        self,
        template: SetTemplate,
        owned: Dict[int, int],
    ) -> Optional[SetCandidate]:
        known = [self.repo.item(gid) for gid in template.piece_ids]
        known = [item for item in known if item is not None]
        if len(known) < 2:
            return None
        assignment = self._assign(known, self.current_slot_scores)
        if not assignment:
            return None
        delta_slots, bonus_delta, bonus_after = self._candidate_delta(assignment, template.id)
        owned_pieces = [item for item in known if item.id in owned]
        owned_assignment = self._assign(owned_pieces, self.current_slot_scores)
        owned_delta_slots, owned_bonus_delta, _ = self._candidate_delta(
            owned_assignment, template.id)
        missing = [item.id for item in known if item.id not in owned]
        owned_not_equipped = [item.id for item in owned_pieces
                              if not any(current.gid == item.id
                                         for current in self.current_items.values())]
        action = "keep_current"
        acquisition = Acquisition("owned")
        warnings: List[str] = []
        if (delta_slots + bonus_delta) <= 0 and (owned_delta_slots + owned_bonus_delta) <= 0:
            action = "keep_current"
        elif not missing:
            action = "equip_owned"
            acquisition = Acquisition("equip_owned",
                                      note="full set owned; equipping is reversible")
        elif owned_pieces and (owned_delta_slots + owned_bonus_delta) > 0:
            action = "equip_owned_partial"
            acquisition = self._acquisition_for(missing)
        else:
            acquisition = self._acquisition_for(missing)
            action = acquisition.action
        coverage = len(known) / max(1, len(template.piece_ids))
        if coverage < 1.0:
            warnings.append(
                "%d of %d pieces are not in the Touch encyclopedia dump; the "
                "score covers only the known pieces."
                % (len(template.piece_ids) - len(known), len(template.piece_ids)))
        return SetCandidate(
            set_id=template.id,
            name=template.name,
            level=template.level,
            piece_count=len(template.piece_ids),
            known_pieces=len(known),
            coverage=coverage,
            owned_pieces=len(owned_pieces),
            missing_pieces=len(missing),
            delta_full=delta_slots + bonus_delta,
            delta_owned=owned_delta_slots + owned_bonus_delta,
            delta_slots=delta_slots,
            bonus_now=self.current_bonus,
            bonus_after=bonus_after,
            action=action,
            acquisition=acquisition,
            equipped_assignment={position: piece.id
                                 for position, piece in assignment.items()},
            owned_assignment={position: piece.id
                              for position, piece in owned_assignment.items()},
            missing_gids=missing,
            owned_not_equipped=owned_not_equipped,
            warnings=warnings,
        )

    def _acquisition_for(self, gids: Sequence[int]) -> Acquisition:
        owned = self.character.owned_gids
        rows = [self.acquire(gid, gid in owned) for gid in gids]
        costs = [row.cost for row in rows if row.cost is not None]
        if all(row.cost is not None for row in rows):
            known_cost = sum(costs)
            if all(row.action == "craft" for row in rows):
                action = "craft_missing"
            elif all(row.action in ("buy", "craft") for row in rows):
                action = "buy_missing" if any(row.action == "buy" for row in rows) else "craft_missing"
            else:
                action = "acquire_missing"
            return Acquisition(
                action, cost=known_cost,
                average=sum(row.average for row in rows if row.average is not None) or None,
                listing_min=sum(row.listing_min for row in rows
                                if row.listing_min is not None) or None,
                craft_cost=sum(row.craft_cost for row in rows
                               if row.craft_cost is not None) or None,
                recipe_known=all(row.recipe_known for row in rows),
                ingredient_prices_known=sum(row.ingredient_prices_known for row in rows),
                ingredient_count=sum(row.ingredient_count for row in rows),
                note="cost covers %d missing piece(s)" % len(rows),
            )
        known = sum(1 for row in rows if row.cost is not None)
        return Acquisition(
            "partial_market" if known else "farm_missing",
            cost=sum(costs) if costs else None,
            recipe_known=sum(1 for row in rows if row.recipe_known) == len(rows),
            ingredient_prices_known=sum(row.ingredient_prices_known for row in rows),
            ingredient_count=sum(row.ingredient_count for row in rows),
            note="%d of %d missing pieces have a price or complete recipe cost"
                 % (known, len(rows)),
        )

    def recommend_sets(self) -> List[SetCandidate]:
        owned = self.character.owned_gids
        max_level = self.character.level + self.level_margin if self.character.level else 0
        candidates: List[SetCandidate] = []
        for template in self.repo.sets.values():
            if max_level and template.level > max_level:
                continue
            candidate = self._candidate(template, owned)
            if candidate is not None:
                candidates.append(candidate)
        candidates.sort(key=lambda row: (
            -max(row.delta_full, row.delta_owned),
            row.missing_pieces,
            row.level,
            row.set_id,
        ))
        return candidates[:self.top_sets]

    # -- individual slots --------------------------------------------------
    def slot_upgrades(self) -> Tuple[List[SlotUpgrade], List[str]]:
        warnings: List[str] = []
        warnings.append(
            "The encyclopedia dump has no weapons; the equipped weapon is kept "
            "as-is and is not scored.")
        owned = self.character.owned_gids
        max_level = self.character.level + self.level_margin if self.character.level else 0
        by_position: Dict[int, List[ItemTemplate]] = {position: [] for position in SLOT_ORDER}
        for item in self.repo.items.values():
            if max_level and item.level > max_level:
                continue
            for position in self.repo.item_positions(item):
                if position in by_position:
                    by_position[position].append(item)
        upgrades: List[SlotUpgrade] = []
        for position in SLOT_ORDER:
            if position == 1:
                continue
            current = self.current_items.get(position)
            current_score = self.current_slot_scores.get(position, 0.0)
            rows: List[SlotUpgrade] = []
            for item in by_position.get(position, []):
                if current and item.id == current.gid:
                    continue
                score = self.item_score(item)
                delta = score - current_score
                if delta <= 0:
                    continue
                owned_item = item.id in owned
                acquisition = self.acquire(item.id, owned_item)
                rows.append(SlotUpgrade(
                    position=position,
                    slot=SLOT_NAMES.get(position, str(position)),
                    current_gid=current.gid if current else None,
                    current_name=(self.repo.item_name(current.gid)
                                  if current else None),
                    current_score=current_score,
                    candidate_gid=item.id,
                    candidate_name=item.name,
                    candidate_level=item.level,
                    candidate_score=score,
                    delta=delta,
                    set_ids=item.set_ids,
                    action="equip_owned" if owned_item else acquisition.action,
                    acquisition=acquisition,
                    owned=owned_item,
                ))
            rows.sort(key=lambda row: (-row.delta, row.candidate_level, row.candidate_gid))
            upgrades.extend(rows[:3])
        upgrades.sort(key=lambda row: (-row.delta, row.position, row.candidate_gid))
        return upgrades[:self.top_slot_upgrades], warnings

    # -- active sets -------------------------------------------------------
    def active_sets(self) -> List[Dict[str, Any]]:
        out = []
        for active in self.character.sets:
            template = self.repo.set(active.set_id)
            bonus_stats = self.repo.effect_stats(active.active_effects)
            owned = self.character.owned_gids
            equipped = {item.gid for item in self.current_items.values()}
            missing = [gid for gid in (template.piece_ids if template else active.piece_ids)
                       if gid not in owned]
            owned_not_equipped = [
                gid for gid in (template.piece_ids if template else active.piece_ids)
                if gid in owned and gid not in equipped
            ]
            out.append({
                "set_id": active.set_id,
                "name": template.name if template else "#%d" % active.set_id,
                "level": template.level if template else None,
                "equipped_count": active.equipped_count,
                "piece_count": len(template.piece_ids) if template else len(active.piece_ids),
                "active_bonus_score": round(stat_score(bonus_stats, self.weights), 1),
                "active_bonus_stats": {key: round(value, 1)
                                       for key, value in bonus_stats.items()},
                "missing_ids": missing,
                "owned_not_equipped": owned_not_equipped,
                "bonus_is_secret": active.bonus_is_secret,
            })
        out.sort(key=lambda row: (row["equipped_count"], row["set_id"]))
        return out

    def to_dict(self) -> Dict[str, Any]:
        upgrades, warnings = self.slot_upgrades()
        if self.unknown_equipped:
            warnings.append(
                "%d equipped item(s) have no template in the encyclopedia dump "
                "and score as zero: %s"
                % (len(self.unknown_equipped),
                   ", ".join(str(gid) for gid in self.unknown_equipped[:10])))
        if not (self.character.inventory or {}).get("gids"):
            warnings.append(
                "The snapshot has no inventory gid map; only equipped pieces count "
                "as owned, so missing-piece advice may overstate what must be acquired.")
        return {
            "focus": self.focus,
            "weights": {key: value for key, value in self.weights.items()
                        if key in ("action_points", "movement_points", "range",
                                   "power", "damage", "vitality", "wisdom",
                                   "critical_hit", "heals")
                        or key.startswith("damage_") and key[7:] in ELEMENTS
                        or key in (PRIMARY_STAT[e] for e in ELEMENTS)},
            "level_margin": self.level_margin,
            "current_score": round(self.current_total, 1),
            "current_slots": self.slot_summary(),
            "current_bonus_score": round(self.current_bonus, 1),
            "active_sets": self.active_sets(),
            "set_candidates": [candidate.to_dict()
                               for candidate in self.recommend_sets()],
            "slot_upgrades": [upgrade.to_dict() for upgrade in upgrades],
            "warnings": warnings,
            "caveats": [
                "Scores are linear weights over the recovered stat keys, not a combat simulation.",
                "Weapons are absent from the encyclopedia dump and are not scored.",
                "Equipping gear is reversible; buying and crafting spend resources and "
                "remain human actions.",
            ],
        }
