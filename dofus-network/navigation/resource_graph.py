"""Dofus Resource Knowledge Graph and Automated Farm Loop Generator.

Provides:
1. Complete encyclopedia of all 5 harvesting professions:
   - Peasant (Paysan / Campesino)
   - Lumberjack (Bûcheron / Leñador)
   - Miner (Mineur / Minero)
   - Alchemist (Alchimiste / Alquimista)
   - Fisherman (Pêcheur / Pescador)
2. Trilingual resource names (FR, EN, ES) and harvest level requirements.
3. Accurate coordinate mapping for high-density gathering spots across Amakna, Astrub, Cania, etc.
4. Optimal circular farm loop generator using nearest-neighbor traveling salesperson heuristic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from .world_graph import DofusWorldGraph, NavigationStep


@dataclass
class ResourceNode:
    id: int
    name_fr: str
    name_en: str
    name_es: str
    profession: str  # PEASANT, LUMBERJACK, MINER, ALCHEMIST, FISHERMAN
    level_req: int
    item_type_id: int
    interactive_skill_id: int
    xp_gain: int = 15

    def get_name(self, lang: str = "fr") -> str:
        lang = lang.lower()
        if lang == "en":
            return self.name_en or self.name_fr
        elif lang == "es":
            return self.name_es or self.name_fr
        return self.name_fr


@dataclass
class ResourceSpot:
    resource: ResourceNode
    map_coord: Tuple[int, int]
    subarea_name: str
    density: int  # number of harvestable nodes on this map
    distance_to_player: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resource_id": self.resource.id,
            "resource_name_fr": self.resource.name_fr,
            "resource_name_en": self.resource.name_en,
            "resource_name_es": self.resource.name_es,
            "profession": self.resource.profession,
            "level_req": self.resource.level_req,
            "map_coord": list(self.map_coord),
            "subarea_name": self.subarea_name,
            "density": self.density,
            "distance_to_player": self.distance_to_player,
        }


@dataclass
class FarmLoop:
    resource_name: str
    profession: str
    start_coord: Tuple[int, int]
    map_sequence: List[Tuple[int, int]]
    total_maps: int
    total_nodes_est: int
    total_travel_distance: int
    cycle_steps: List[NavigationStep] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resource_name": self.resource_name,
            "profession": self.profession,
            "start_coord": list(self.start_coord),
            "map_sequence": [list(m) for m in self.map_sequence],
            "total_maps": self.total_maps,
            "total_nodes_est": self.total_nodes_est,
            "total_travel_distance": self.total_travel_distance,
            "cycle_steps": [s.to_dict() for s in self.cycle_steps],
        }


class ResourceKnowledgeGraph:
    """Encyclopedia and spatial indexing of all harvestable Dofus resources."""

    ALL_RESOURCES: List[ResourceNode] = [
        # --- PAYSAN (PEASANT / CAMPESINO) ---
        ResourceNode(289, "Blé", "Wheat", "Trigo", "PEASANT", 1, 34, 122, 10),
        ResourceNode(400, "Orge", "Barley", "Cebada", "PEASANT", 20, 34, 123, 15),
        ResourceNode(533, "Avoine", "Oats", "Avena", "PEASANT", 30, 34, 124, 20),
        ResourceNode(401, "Houblon", "Hops", "Lúpulo", "PEASANT", 40, 34, 125, 25),
        ResourceNode(423, "Lin", "Flax", "Lino", "PEASANT", 50, 34, 126, 30),
        ResourceNode(532, "Seigle", "Rye", "Centeno", "PEASANT", 60, 34, 127, 35),
        ResourceNode(7018, "Riz", "Rice", "Arroz", "PEASANT", 70, 34, 128, 40),
        ResourceNode(405, "Malt", "Malt", "Malta", "PEASANT", 80, 34, 129, 45),
        ResourceNode(425, "Chanvre", "Hemp", "Cáñamo", "PEASANT", 90, 34, 130, 50),
        ResourceNode(11103, "Maïs", "Corn", "Maíz", "PEASANT", 100, 34, 131, 55),
        ResourceNode(11116, "Frostiz", "Frostiz", "Frostiz", "PEASANT", 130, 34, 132, 70),

        # --- BÛCHERON (LUMBERJACK / LEÑADOR) ---
        ResourceNode(303, "Bois de Frêne", "Ash Wood", "Madera de fresno", "LUMBERJACK", 1, 38, 45, 10),
        ResourceNode(473, "Bois de Châtaignier", "Chestnut Wood", "Madera de castaño", "LUMBERJACK", 20, 38, 46, 15),
        ResourceNode(476, "Bois de Noyer", "Walnut Wood", "Madera de nogal", "LUMBERJACK", 35, 38, 47, 20),
        ResourceNode(460, "Bois de Chêne", "Oak Wood", "Madera de roble", "LUMBERJACK", 50, 38, 48, 25),
        ResourceNode(2358, "Bois de Bombu", "Bombu Wood", "Madera de bombu", "LUMBERJACK", 60, 38, 49, 30),
        ResourceNode(2357, "Bois d'Oliviolet", "Oliviolet Wood", "Madera de olivioleta", "LUMBERJACK", 60, 38, 50, 30),
        ResourceNode(471, "Bois d'Érable", "Maple Wood", "Madera de arce", "LUMBERJACK", 70, 38, 51, 35),
        ResourceNode(461, "Bois d'If", "Yew Wood", "Madera de tejo", "LUMBERJACK", 75, 38, 52, 40),
        ResourceNode(7013, "Bambou", "Bamboo Wood", "Madera de bambú", "LUMBERJACK", 80, 38, 53, 45),
        ResourceNode(474, "Bois de Merisier", "Wild Cherry Wood", "Madera de cerezo salvaje", "LUMBERJACK", 85, 38, 54, 50),
        ResourceNode(449, "Bois d'Ébène", "Ebony Wood", "Madera de ébano", "LUMBERJACK", 90, 38, 55, 55),
        ResourceNode(7925, "Bois de Kalyptus", "Kalyptus Wood", "Madera de kalipto", "LUMBERJACK", 95, 38, 56, 60),
        ResourceNode(470, "Bois de Charme", "Hornbeam Wood", "Madera de carpe", "LUMBERJACK", 100, 38, 57, 65),
        ResourceNode(472, "Bois d'Orme", "Elm Wood", "Madera de olmo", "LUMBERJACK", 120, 38, 58, 75),
        ResourceNode(11107, "Bois de Tremble", "Aspen Wood", "Madera de álamo temblón", "LUMBERJACK", 140, 38, 59, 85),

        # --- MINEUR (MINER / MINERO) ---
        ResourceNode(312, "Fer", "Iron", "Hierro", "MINER", 1, 39, 24, 10),
        ResourceNode(441, "Cuivre", "Copper", "Cobre", "MINER", 20, 39, 25, 15),
        ResourceNode(442, "Bronze", "Bronze", "Bronce", "MINER", 40, 39, 26, 20),
        ResourceNode(443, "Kobalte", "Cobalt", "Cobalto", "MINER", 50, 39, 27, 25),
        ResourceNode(445, "Manganèse", "Manganese", "Manganeso", "MINER", 60, 39, 28, 30),
        ResourceNode(444, "Étain", "Tin", "Estaño", "MINER", 70, 39, 29, 35),
        ResourceNode(350, "Argent", "Silver", "Plata", "MINER", 75, 39, 30, 40),
        ResourceNode(446, "Bauxite", "Bauxite", "Bauxita", "MINER", 80, 39, 31, 45),
        ResourceNode(313, "Or", "Gold", "Oro", "MINER", 90, 39, 32, 50),
        ResourceNode(7014, "Dolomite", "Dolomite", "Dolomía", "MINER", 100, 39, 33, 60),
        ResourceNode(11110, "Obsidienne", "Obsidian", "Obsidiana", "MINER", 120, 39, 34, 75),

        # --- ALCHIMISTE (ALCHEMIST / ALQUIMISTA) ---
        ResourceNode(421, "Ortie", "Nettles", "Ortiga", "ALCHEMIST", 1, 36, 1, 10),
        ResourceNode(428, "Sauge", "Sage", "Salvia", "ALCHEMIST", 20, 36, 2, 15),
        ResourceNode(395, "Trèfle à 5 feuilles", "Five-Leaf Clover", "Trébol de 5 hojas", "ALCHEMIST", 40, 36, 3, 20),
        ResourceNode(380, "Menthe sauvage", "Wild Mint", "Menta salvaje", "ALCHEMIST", 60, 36, 4, 25),
        ResourceNode(593, "Orchidée freyesque", "Freyesque Orchid", "Orquídea freyesca", "ALCHEMIST", 80, 36, 5, 35),
        ResourceNode(594, "Edelweiss", "Edelweiss", "Edelweiss", "ALCHEMIST", 100, 36, 6, 45),
        ResourceNode(7059, "Pandouille", "Pandula", "Pandanina", "ALCHEMIST", 120, 36, 7, 55),
        ResourceNode(11109, "Perce-Neige", "Snowdrop", "Campanilla de invierno", "ALCHEMIST", 140, 36, 8, 70),

        # --- PÊCHEUR (FISHERMAN / PESCADOR) ---
        ResourceNode(1757, "Goujon", "Gudgeon", "Gobio", "FISHERMAN", 1, 35, 133, 10),
        ResourceNode(1750, "Truite", "Trout", "Trucha", "FISHERMAN", 20, 35, 134, 15),
        ResourceNode(1782, "Brochet", "Pike", "Lucio", "FISHERMAN", 40, 35, 135, 20),
        ResourceNode(1794, "Carpe", "Carp", "Carpa", "FISHERMAN", 60, 35, 136, 25),
        ResourceNode(1805, "Barbeau", "Barbel", "Barbo", "FISHERMAN", 70, 35, 137, 30),
        ResourceNode(1844, "Requin", "Shark", "Tiburón", "FISHERMAN", 80, 35, 138, 40),
        ResourceNode(603, "Raie", "Ray", "Raya", "FISHERMAN", 90, 35, 139, 50),
        ResourceNode(607, "Espadon", "Swordfish", "Pez espada", "FISHERMAN", 100, 35, 140, 60),
    ]

    # Pre-indexed known dense harvest coordinate spots
    RESOURCE_MAPS: Dict[int, List[Tuple[Tuple[int, int], str, int]]] = {
        # Blé (Wheat, ID 289)
        289: [
            ((7, -25), "Champs d'Astrub", 18),
            ((7, -24), "Champs d'Astrub", 15),
            ((6, -24), "Champs d'Astrub", 14),
            ((5, -24), "Champs d'Astrub", 12),
            ((4, -25), "Champs d'Astrub", 16),
            ((3, -25), "Champs d'Amakna", 14),
            ((5, -26), "Champs d'Amakna", 17),
            ((2, -24), "Champs d'Amakna", 11),
            ((-1, -21), "Plaine des Scarahorses", 9),
        ],
        # Orge (Barley, ID 400)
        400: [
            ((-29, -40), "Champs de Cania", 14),
            ((-28, -40), "Champs de Cania", 16),
            ((-27, -40), "Champs de Cania", 15),
            ((5, -24), "Champs d'Amakna", 10),
            ((6, -24), "Champs d'Amakna", 11),
        ],
        # Bois de Frêne (Ash Wood, ID 303)
        303: [
            ((2, -24), "Forêt d'Astrub", 12),
            ((3, -24), "Forêt d'Astrub", 15),
            ((4, -24), "Forêt d'Astrub", 14),
            ((1, -23), "Forêt d'Astrub", 10),
            ((2, -23), "Forêt d'Astrub", 11),
            ((4, 13), "Forêt d'Amakna", 14),
            ((5, 13), "Forêt d'Amakna", 16),
            ((3, 14), "Forêt d'Amakna", 13),
        ],
        # Bois de Chêne (Oak Wood, ID 460)
        460: [
            ((-8, -14), "Forêt des Abraknydes", 14),
            ((-7, -14), "Forêt des Abraknydes", 12),
            ((-9, -13), "Forêt des Abraknydes", 15),
            ((2, 15), "Forêt d'Amakna", 9),
            ((3, 15), "Forêt d'Amakna", 11),
            ((4, 16), "Forêt d'Amakna", 10),
        ],
        # Fer (Iron, ID 312)
        312: [
            ((1, -17), "Mine d'Astrub", 19),
            ((-3, 9), "Mine Dédale", 16),
            ((-3, 12), "Mine Sadida", 14),
            ((-2, 4), "Mine d'Haktam", 18),
            ((-4, -5), "Mine Bellus Gallus", 15),
            ((-1, 33), "Mine Porcos", 12),
            ((-25, 33), "Mine Brâkmar", 21),
            ((-2, -18), "Mine d'Amakna", 14),
        ],
        # Cuivre (Copper, ID 441)
        441: [
            ((1, -17), "Mine d'Astrub", 11),
            ((-3, -4), "Mine des Craqueleurs", 14),
            ((-2, 4), "Mine d'Haktam", 12),
            ((-24, 32), "Mine Brâkmar", 15),
            ((-3, 9), "Mine Dédale", 10),
        ],
        # Ortie (Nettles, ID 421)
        421: [
            ((5, -21), "Bordure d'Astrub", 12),
            ((6, -21), "Bordure d'Astrub", 14),
            ((4, -22), "Bordure d'Astrub", 11),
            ((5, -23), "Bordure d'Astrub", 15),
            ((-2, 23), "Plaine des Scarahorses", 9),
            ((3, 10), "Campagne d'Amakna", 10),
        ],
        # Trèfle à 5 feuilles (Five-Leaf Clover, ID 395)
        395: [
            ((-8, -12), "Forêt des Abraknydes", 11),
            ((-7, -12), "Forêt des Abraknydes", 13),
            ((4, 8), "Coin des Bouftous", 9),
            ((5, 8), "Coin des Bouftous", 10),
            ((1, 1), "Village d'Amakna", 8),
        ],
        # Goujon (Gudgeon, ID 1757)
        1757: [
            ((7, -4), "Port de Madrestam", 15),
            ((8, -4), "Port de Madrestam", 12),
            ((9, -4), "Port de Madrestam", 14),
            ((13, 26), "Rivage sufokien", 16),
            ((14, 26), "Rivage sufokien", 13),
        ],
    }

    def __init__(self) -> None:
        self.resources = list(self.ALL_RESOURCES)
        self.by_id: Dict[int, ResourceNode] = {r.id: r for r in self.resources}
        self.world_graph = DofusWorldGraph()
        # Precompute the lowercase name variants (plus the wood-prefix
        # aliases) once. The old find_resource_by_name() re-lowercased and
        # re-stripped these prefixes for every resource on every query,
        # which dominated the NLP resource lookup path.
        self._resource_aliases: List[Tuple[ResourceNode, Tuple[str, ...]]] = [
            (
                r,
                (
                    r.name_fr.lower(),
                    r.name_en.lower(),
                    r.name_es.lower(),
                    # Wood-prefix aliases (e.g. "Bois de Chêne" -> "chêne")
                    r.name_fr.lower().replace("bois de ", "").replace("bois d'", ""),
                    r.name_en.lower().replace(" wood", ""),
                    r.name_es.lower().replace("madera de ", ""),
                ),
            )
            for r in self.resources
        ]

    def find_resource_by_name(self, name_query: str) -> Optional[ResourceNode]:
        """Fuzzy trilingual matching for resource name (e.g. 'fer', 'iron', 'hierro', 'blé', 'wheat')."""
        q = name_query.strip().lower()
        # Direct exact or substring match against precomputed lowercase names
        for r, aliases in self._resource_aliases:
            for name in aliases:
                if q == name or q in name:
                    return r
        return None

    def search_spots(
        self,
        query: str,
        near_pos: Optional[Tuple[int, int]] = None,
        max_results: int = 8,
    ) -> List[ResourceSpot]:
        """Searches spots for a resource, optionally sorted by distance to `near_pos`."""
        res_node = self.find_resource_by_name(query)
        if not res_node:
            return []

        spot_tuples = self.RESOURCE_MAPS.get(res_node.id, [])
        spots: List[ResourceSpot] = []

        for coord, subarea, density in spot_tuples:
            dist = None
            if near_pos:
                dist = abs(coord[0] - near_pos[0]) + abs(coord[1] - near_pos[1])
            spots.append(
                ResourceSpot(
                    resource=res_node,
                    map_coord=coord,
                    subarea_name=subarea,
                    density=density,
                    distance_to_player=dist,
                )
            )

        if near_pos:
            spots.sort(key=lambda s: (s.distance_to_player if s.distance_to_player is not None else 999, -s.density))
        else:
            spots.sort(key=lambda s: -s.density)

        return spots[:max_results]

    def generate_farm_loop(
        self,
        resource_query: str,
        start_coord: Tuple[int, int] = (4, -19),
        max_maps: int = 6,
    ) -> Optional[FarmLoop]:
        """Generates an optimal circular gathering circuit of consecutive maps.

        Uses a nearest-neighbor traveling salesperson algorithm to order the spots,
        then connects them back to the starting map to form a closed looping route.
        """
        spots = self.search_spots(resource_query, near_pos=start_coord, max_results=max_maps)
        if not spots:
            return None

        res_node = spots[0].resource
        unvisited = [s.map_coord for s in spots]

        # Greedy nearest neighbor ordering
        ordered_maps: List[Tuple[int, int]] = []
        curr = start_coord

        while unvisited:
            # Inline the argmin instead of min(..., key=lambda): avoids a
            # Python-level call per candidate. Ties still resolve to the
            # first-listed candidate, matching min()'s behaviour.
            cx, cy = curr
            best_i = 0
            best_d = abs(unvisited[0][0] - cx) + abs(unvisited[0][1] - cy)
            for i in range(1, len(unvisited)):
                c = unvisited[i]
                d = abs(c[0] - cx) + abs(c[1] - cy)
                if d < best_d:
                    best_d = d
                    best_i = i
            closest = unvisited.pop(best_i)
            ordered_maps.append(closest)
            curr = closest

        # Generate step-by-step navigation for the cycle
        cycle_steps: List[NavigationStep] = []
        step_idx = 1
        total_dist = 0

        full_cycle = ordered_maps + [ordered_maps[0]]  # close the loop
        for i in range(len(full_cycle) - 1):
            p1 = full_cycle[i]
            p2 = full_cycle[i + 1]
            seg_path = self.world_graph.find_path(p1, p2, allow_zaap=False)
            total_dist += seg_path.walk_distance
            for s in seg_path.steps:
                s.step_index = step_idx
                cycle_steps.append(s)
                step_idx += 1

        total_nodes = sum(s.density for s in spots)

        return FarmLoop(
            resource_name=res_node.name_fr,
            profession=res_node.profession,
            start_coord=ordered_maps[0],
            map_sequence=ordered_maps,
            total_maps=len(ordered_maps),
            total_nodes_est=total_nodes,
            total_travel_distance=total_dist,
            cycle_steps=cycle_steps,
        )
