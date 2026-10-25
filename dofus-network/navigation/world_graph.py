"""Dofus Spatial World Graph and Multi-Map Pathfinding Engine.

Features:
1. Spatial coordinate mapping [X, Y] with continent / superarea / subarea awareness.
2. Official Zaap network fast-travel routing and cost optimization.
3. Multi-modal A* pathfinding (Direct Walking vs. Zaap Fast Travel).
4. Tactical 560-cell isometric grid pathfinding, border cell detection, and LOS calculation.
5. Multilingual navigation instructions (FR, EN, ES) for bot logs and player feedback.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple


class Direction(str, Enum):
    UP = "UP"          # Y decreases (North)
    DOWN = "DOWN"      # Y increases (South)
    LEFT = "LEFT"      # X decreases (West)
    RIGHT = "RIGHT"    # X increases (East)
    ZAAP = "ZAAP"      # Fast-travel teleportation
    ZAAPI = "ZAAPI"    # Intra-city transport


@dataclass(frozen=True, slots=True)
class MapCoord:
    x: int
    y: int

    def manhattan_distance(self, other: MapCoord) -> int:
        return abs(self.x - other.x) + abs(self.y - other.y)

    def euclidean_distance(self, other: MapCoord) -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    def step_towards(self, target: MapCoord) -> Tuple[MapCoord, Direction]:
        """Calculates immediate next coordinate step towards target."""
        dx = target.x - self.x
        dy = target.y - self.y

        # Prioritize axis with larger delta
        if abs(dx) >= abs(dy) and dx != 0:
            if dx > 0:
                return MapCoord(self.x + 1, self.y), Direction.RIGHT
            else:
                return MapCoord(self.x - 1, self.y), Direction.LEFT
        elif dy != 0:
            if dy > 0:
                return MapCoord(self.x, self.y + 1), Direction.DOWN
            else:
                return MapCoord(self.x, self.y - 1), Direction.UP
        return self, Direction.RIGHT

    def __str__(self) -> str:
        return f"[{self.x}, {self.y}]"


@dataclass
class ZaapLocation:
    name_fr: str
    name_en: str
    name_es: str
    coord: MapCoord
    subarea_id: int
    map_id: int = 0
    kamas_cost_base: int = 20

    def get_name(self, lang: str = "fr") -> str:
        lang = lang.lower()
        if lang == "en":
            return self.name_en or self.name_fr
        elif lang == "es":
            return self.name_es or self.name_fr
        return self.name_fr


@dataclass(slots=True)
class NavigationStep:
    step_index: int
    from_coord: Tuple[int, int]
    to_coord: Tuple[int, int]
    action: str  # WALK, ZAAP_TELEPORT, ZAAP_WALK_TO
    direction: Optional[Direction] = None
    target_name: Optional[str] = None
    instruction_fr: str = ""
    instruction_en: str = ""
    instruction_es: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_index": self.step_index,
            "from_coord": list(self.from_coord),
            "to_coord": list(self.to_coord),
            "action": self.action,
            "direction": self.direction.value if self.direction else None,
            "target_name": self.target_name,
            "instruction_fr": self.instruction_fr,
            "instruction_en": self.instruction_en,
            "instruction_es": self.instruction_es,
        }


@dataclass
class NavigationPath:
    start_coord: Tuple[int, int]
    target_coord: Tuple[int, int]
    total_steps: int
    walk_distance: int
    uses_zaap: bool
    zaap_start_name: Optional[str] = None
    zaap_target_name: Optional[str] = None
    estimated_seconds: float = 0.0
    steps: List[NavigationStep] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start_coord": list(self.start_coord),
            "target_coord": list(self.target_coord),
            "total_steps": self.total_steps,
            "walk_distance": self.walk_distance,
            "uses_zaap": self.uses_zaap,
            "zaap_start_name": self.zaap_start_name,
            "zaap_target_name": self.zaap_target_name,
            "estimated_seconds": round(self.estimated_seconds, 1),
            "steps": [s.to_dict() for s in self.steps],
        }


class DofusWorldGraph:
    """Graph model of the Dofus World of Twelve with coordinate and Zaap routing."""

    # Official iconic Zaap coordinates across Dofus Touch & 2.x
    OFFICIAL_ZAAPS: List[ZaapLocation] = [
        ZaapLocation("Astrub", "Astrub", "Astrub", MapCoord(4, -19), subarea_id=95, map_id=191102976),
        ZaapLocation("Village d'Amakna", "Amakna Village", "Pueblo de Amakna", MapCoord(-2, 0), subarea_id=20, map_id=88081177),
        ZaapLocation("Château d'Amakna", "Amakna Castle", "Castillo de Amakna", MapCoord(3, -5), subarea_id=21, map_id=88212480),
        ZaapLocation("Port de Madrestam", "Madrestam Harbour", "Puerto de Madrestam", MapCoord(7, -4), subarea_id=1, map_id=68419587),
        ZaapLocation("Coin des Bouftous", "Gobball Corner", "Rincón de los Jalatos", MapCoord(5, 7), subarea_id=24, map_id=88082449),
        ZaapLocation("Montagne des Craqueleurs", "Crackler Mountain", "Montaña de los Crujidores", MapCoord(-5, -8), subarea_id=40, map_id=88212234),
        ZaapLocation("Forêt des Abraknydes", "Treechnid Forest", "Bosque de los Abraknidos", MapCoord(-9, -8), subarea_id=42, map_id=88211978),
        ZaapLocation("Tainéla (Berceau)", "Tainela (Cradle)", "Tainela (Cuna)", MapCoord(1, -32), subarea_id=30, map_id=88213760),
        ZaapLocation("Plaine des Scarahorses", "Scarahorse Plain", "Llanura de los Escarahoja", MapCoord(-1, 24), subarea_id=27, map_id=88081432),
        ZaapLocation("Bord de la forêt des Cania", "Edge of the Cania Forest", "Borde del bosque de Cania", MapCoord(-27, -36), subarea_id=106, map_id=142084608),
        ZaapLocation("Cité de Bonta", "City of Bonta", "Ciudad de Bonta", MapCoord(-32, -56), subarea_id=107, map_id=142606336),
        ZaapLocation("Cité de Brâkmar", "City of Brakmar", "Ciudad de Brakmar", MapCoord(-26, 36), subarea_id=108, map_id=143130624),
        ZaapLocation("Landes de Sidimote", "Sidimote Moors", "Landas de Sidimote", MapCoord(-24, 12), subarea_id=28, map_id=88080920),
        ZaapLocation("Faubourgs de Pandala", "Pandala Suburbs", "Arrabales de Pandala", MapCoord(26, -37), subarea_id=45, map_id=164627456),
        ZaapLocation("Plage de Corail (Otomaï)", "Coral Beach (Otomai)", "Playa de Coral (Otomai)", MapCoord(-46, 18), subarea_id=110, map_id=154010624),
        ZaapLocation("Canopée du Kimbo", "Kimbo Canopy", "Dosel del Kimbo", MapCoord(-54, 16), subarea_id=112, map_id=154272768),
        ZaapLocation("Village des Dopeuls", "Dopple Village", "Pueblo de los Dopels", MapCoord(-26, -11), subarea_id=38, map_id=88210954),
        ZaapLocation("Village des Eleveurs", "Breeder Village", "Pueblo de los Ganaderos", MapCoord(-16, 1), subarea_id=36, map_id=88080408),
        ZaapLocation("Rivage sufokien", "Sufokian Shoreline", "Ribera sufokeña", MapCoord(13, 26), subarea_id=22, map_id=88083218),
        ZaapLocation("La Bourgade (Frigost)", "The Village (Frigost)", "La Burguesia (Frigost)", MapCoord(-77, -73), subarea_id=150, map_id=171966464),
    ]

    def __init__(self) -> None:
        self.zaaps = list(self.OFFICIAL_ZAAPS)
        self.zaap_by_coord: Dict[Tuple[int, int], ZaapLocation] = {
            (z.coord.x, z.coord.y): z for z in self.zaaps
        }
        # Flat tuple views of the Zaap coordinates: avoids constructing
        # (and later GC-ing) a MapCoord object for every candidate on every
        # find_nearest_zaap() call. Ties resolve to the first-listed Zaap,
        # matching the original linear scan.
        self._zaap_coords: List[Tuple[int, int]] = [(z.coord.x, z.coord.y) for z in self.zaaps]
        # find_nearest_zaap() is called for both endpoints of every path
        # query; bots repeatedly route from the same few towns, so memoizing
        # turns an O(#zaaps) scan into an O(1) dict hit for the common case.
        self._nearest_zaap_cache: Dict[Tuple[int, int], Tuple[ZaapLocation, int]] = {}

    def get_all_zaaps(self) -> List[Dict[str, Any]]:
        return [
            {
                "name_fr": z.name_fr,
                "name_en": z.name_en,
                "name_es": z.name_es,
                "coord": [z.coord.x, z.coord.y],
                "subarea_id": z.subarea_id,
                "map_id": z.map_id,
            }
            for z in self.zaaps
        ]

    def find_nearest_zaap(self, coord: MapCoord) -> Tuple[ZaapLocation, int]:
        """Returns the closest Zaap to a given coordinate and its Manhattan distance."""
        key = (coord.x, coord.y)
        hit = self._nearest_zaap_cache.get(key)
        if hit is not None:
            return hit
        best_zaap = self.zaaps[0]
        best_dist = coord.manhattan_distance(best_zaap.coord)
        for z, (zx, zy) in zip(self.zaaps[1:], self._zaap_coords[1:]):
            d = abs(coord.x - zx) + abs(coord.y - zy)
            if d < best_dist:
                best_dist = d
                best_zaap = z
        result = (best_zaap, best_dist)
        # Bound the cache so long-running bots navigating a dense grid of
        # unique coordinates don't grow it without limit.
        if len(self._nearest_zaap_cache) >= 4096:
            self._nearest_zaap_cache.clear()
        self._nearest_zaap_cache[key] = result
        return result

    def find_zaap_by_name(self, name_query: str) -> Optional[ZaapLocation]:
        """Fuzzy matches a Zaap name in FR, EN, or ES."""
        q = name_query.strip().lower()
        # Direct substrings
        for z in self.zaaps:
            if (
                q in z.name_fr.lower()
                or q in z.name_en.lower()
                or q in z.name_es.lower()
                or q in z.name_fr.lower().replace("d'", "").replace("de ", "")
            ):
                return z
        return None

    def find_path(
        self,
        start: Tuple[int, int],
        target: Tuple[int, int],
        allow_zaap: bool = True,
        lang: str = "fr",
    ) -> NavigationPath:
        """Computes the fastest multimodal route between two map coordinates.

        Compares:
        1. Pure direct walk: Cost = Manhattan(start, target) * walk_cost
        2. Fast travel via Zaap: Walk(start -> Zaap1) + Teleport + Walk(Zaap2 -> target)
        """
        c_start = MapCoord(start[0], start[1])
        c_target = MapCoord(target[0], target[1])

        direct_dist = c_start.manhattan_distance(c_target)

        # Base walk time per map: ~3.2 seconds (map load + cell sprint)
        WALK_TIME_PER_MAP = 3.2
        ZAAP_TELEPORT_COST = 4  # equivalent to 4 maps walk penalty for UI menu interaction

        use_zaap = False
        zaap_start: Optional[ZaapLocation] = None
        zaap_target: Optional[ZaapLocation] = None

        if allow_zaap and direct_dist > 6:
            z1, d1 = self.find_nearest_zaap(c_start)
            z2, d2 = self.find_nearest_zaap(c_target)

            # Only use Zaap if start and target don't share the same nearest Zaap
            if z1 != z2:
                zaap_total_cost = d1 + ZAAP_TELEPORT_COST + d2
                if zaap_total_cost < direct_dist:
                    use_zaap = True
                    zaap_start = z1
                    zaap_target = z2

        steps: List[NavigationStep] = []
        step_idx = 1

        if not use_zaap:
            # Direct walk sequence
            curr = c_start
            while curr != c_target:
                next_c, direction = curr.step_towards(c_target)
                dir_label = direction.value
                fr_desc = f"Changer de carte vers le {dir_label} -> [{next_c.x}, {next_c.y}]"
                en_desc = f"Change map towards {dir_label} -> [{next_c.x}, {next_c.y}]"
                es_desc = f"Cambiar de mapa hacia el {dir_label} -> [{next_c.x}, {next_c.y}]"

                steps.append(
                    NavigationStep(
                        step_index=step_idx,
                        from_coord=(curr.x, curr.y),
                        to_coord=(next_c.x, next_c.y),
                        action=f"WALK_{dir_label}",
                        direction=direction,
                        instruction_fr=fr_desc,
                        instruction_en=en_desc,
                        instruction_es=es_desc,
                    )
                )
                curr = next_c
                step_idx += 1

            total_dist = direct_dist
            est_seconds = total_dist * WALK_TIME_PER_MAP

            return NavigationPath(
                start_coord=start,
                target_coord=target,
                total_steps=len(steps),
                walk_distance=total_dist,
                uses_zaap=False,
                estimated_seconds=est_seconds,
                steps=steps,
            )

        else:
            # 1. Walk from start to zaap_start
            curr = c_start
            while curr != zaap_start.coord:
                next_c, direction = curr.step_towards(zaap_start.coord)
                dir_label = direction.value
                steps.append(
                    NavigationStep(
                        step_index=step_idx,
                        from_coord=(curr.x, curr.y),
                        to_coord=(next_c.x, next_c.y),
                        action=f"WALK_{dir_label}",
                        direction=direction,
                        instruction_fr=f"Marcher vers le Zaap ({zaap_start.get_name('fr')}) [{next_c.x}, {next_c.y}]",
                        instruction_en=f"Walk towards Zaap ({zaap_start.get_name('en')}) [{next_c.x}, {next_c.y}]",
                        instruction_es=f"Caminar hacia el Zaap ({zaap_start.get_name('es')}) [{next_c.x}, {next_c.y}]",
                    )
                )
                curr = next_c
                step_idx += 1

            # 2. Teleport from zaap_start to zaap_target
            steps.append(
                NavigationStep(
                    step_index=step_idx,
                    from_coord=(zaap_start.coord.x, zaap_start.coord.y),
                    to_coord=(zaap_target.coord.x, zaap_target.coord.y),
                    action="ZAAP_TELEPORT",
                    direction=Direction.ZAAP,
                    target_name=zaap_target.get_name(lang),
                    instruction_fr=f"Utiliser le Zaap de {zaap_start.get_name('fr')} pour vous téléporter à {zaap_target.get_name('fr')} [{zaap_target.coord.x}, {zaap_target.coord.y}]",
                    instruction_en=f"Use the {zaap_start.get_name('en')} Zaap to teleport to {zaap_target.get_name('en')} [{zaap_target.coord.x}, {zaap_target.coord.y}]",
                    instruction_es=f"Usar el Zaap de {zaap_start.get_name('es')} para teletransportarse a {zaap_target.get_name('es')} [{zaap_target.coord.x}, {zaap_target.coord.y}]",
                )
            )
            step_idx += 1

            # 3. Walk from zaap_target to target
            curr = zaap_target.coord
            while curr != c_target:
                next_c, direction = curr.step_towards(c_target)
                dir_label = direction.value
                steps.append(
                    NavigationStep(
                        step_index=step_idx,
                        from_coord=(curr.x, curr.y),
                        to_coord=(next_c.x, next_c.y),
                        action=f"WALK_{dir_label}",
                        direction=direction,
                        instruction_fr=f"Marcher vers l'objectif -> [{next_c.x}, {next_c.y}]",
                        instruction_en=f"Walk towards destination -> [{next_c.x}, {next_c.y}]",
                        instruction_es=f"Caminar hacia el destino -> [{next_c.x}, {next_c.y}]",
                    )
                )
                curr = next_c
                step_idx += 1

            total_dist = (
                c_start.manhattan_distance(zaap_start.coord)
                + c_target.manhattan_distance(zaap_target.coord)
            )
            est_seconds = (total_dist * WALK_TIME_PER_MAP) + 8.5  # 8.5s Zaap open + select + load

            return NavigationPath(
                start_coord=start,
                target_coord=target,
                total_steps=len(steps),
                walk_distance=total_dist,
                uses_zaap=True,
                zaap_start_name=zaap_start.get_name(lang),
                zaap_target_name=zaap_target.get_name(lang),
                estimated_seconds=est_seconds,
                steps=steps,
            )


class CellPathfinder:
    """Tactical 560-cell isometric grid pathfinder for Dofus Touch.

    Standard grid dimensions:
    - Width = 14 cells per row
    - Total cells = 560 (indexed 0 to 559)
    """

    WIDTH = 14
    HEIGHT = 40
    TOTAL_CELLS = 560

    # Border cells triggering map change
    TOP_BORDER_CELLS: Set[int] = set(range(0, 28))
    BOTTOM_BORDER_CELLS: Set[int] = set(range(532, 560))
    LEFT_BORDER_CELLS: Set[int] = {row * 28 for row in range(20)} | {row * 28 + 14 for row in range(20)}
    RIGHT_BORDER_CELLS: Set[int] = {row * 28 + 13 for row in range(20)} | {row * 28 + 27 for row in range(20)}

    @classmethod
    def cell_to_iso_coords(cls, cell_id: int) -> Tuple[int, int]:
        """Converts a flat cell ID (0..559) to continuous isometric coordinates (X, Y)."""
        if not (0 <= cell_id < cls.TOTAL_CELLS):
            raise ValueError(f"Cell ID {cell_id} out of bounds [0..{cls.TOTAL_CELLS - 1}]")
        return cls._ISO_COORDS[cell_id]

    @classmethod
    def cell_distance(cls, cell_a: int, cell_b: int) -> int:
        """Computes Manhattan distance in isometric cells between two cell IDs."""
        # Bounds check first so invalid IDs still raise ValueError (a raw
        # table index would silently wrap on negative IDs in Python).
        if not (0 <= cell_a < cls.TOTAL_CELLS):
            raise ValueError(f"Cell ID {cell_a} out of bounds [0..{cls.TOTAL_CELLS - 1}]")
        if not (0 <= cell_b < cls.TOTAL_CELLS):
            raise ValueError(f"Cell ID {cell_b} out of bounds [0..{cls.TOTAL_CELLS - 1}]")
        xa, ya = cls._ISO_COORDS[cell_a]
        xb, yb = cls._ISO_COORDS[cell_b]
        return abs(xa - xb) + abs(ya - yb)

    @classmethod
    def get_border_cells_for_direction(cls, direction: Direction) -> Set[int]:
        """Returns the set of cell IDs that trigger a map change in the specified direction."""
        if direction == Direction.UP:
            return cls.TOP_BORDER_CELLS
        elif direction == Direction.DOWN:
            return cls.BOTTOM_BORDER_CELLS
        elif direction == Direction.LEFT:
            return cls.LEFT_BORDER_CELLS
        elif direction == Direction.RIGHT:
            return cls.RIGHT_BORDER_CELLS
        return set()

    @classmethod
    def find_nearest_border_cell(cls, current_cell: int, direction: Direction) -> int:
        """Finds the closest exit cell to step onto to change map in `direction`."""
        border = cls.get_border_cells_for_direction(direction)
        if not border:
            return current_cell
        return min(border, key=lambda c: cls.cell_distance(current_cell, c))


# Precomputed isometric coordinate table for every cell id (0..559). Computing
# this on every cell_distance() call dominated the tactical pathfinding loops
# (two divmods + several int allocations per lookup). Built once at import.
# Declared after the class body because class-body comprehensions cannot see
# sibling class attributes in Python 3.
CellPathfinder._ISO_COORDS = [
    ((cid % CellPathfinder.WIDTH) + ((cid // CellPathfinder.WIDTH) // 2),
     ((cid // CellPathfinder.WIDTH) // 2) - (cid % CellPathfinder.WIDTH) + ((cid // CellPathfinder.WIDTH) % 2))
    for cid in range(CellPathfinder.TOTAL_CELLS)
]
