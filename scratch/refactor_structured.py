"""Structure and reduce boilerplate while maintaining 100% contract equivalence."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

def refactor_damage_calculator():
    path = REPO_ROOT / "dofus-network" / "combat" / "damage_calculator.py"
    content = path.read_text(encoding="utf-8")
    is_crlf = "\r\n" in content
    content_norm = content.replace("\r\n", "\n")

    old_char_stats = """    def get_stat_for_element(self, element: str) -> int:
        elem = element.upper()
        if elem in ("EARTH", "NEUTRAL"):
            return self.strength
        elif elem == "FIRE":
            return self.intelligence
        elif elem == "WATER":
            return self.chance
        elif elem == "AIR":
            return self.agility
        return 0

    def get_flat_for_element(self, element: str) -> int:
        elem = element.upper()
        flat = self.damage
        if elem == "NEUTRAL":
            flat += self.damage_neutral
        elif elem == "EARTH":
            flat += self.damage_earth
        elif elem == "FIRE":
            flat += self.damage_fire
        elif elem == "WATER":
            flat += self.damage_water
        elif elem == "AIR":
            flat += self.damage_air
        return flat"""

    new_char_stats = """    def get_stat_for_element(self, element: str) -> int:
        return {"EARTH": self.strength, "NEUTRAL": self.strength, "FIRE": self.intelligence,
                "WATER": self.chance, "AIR": self.agility}.get(element.upper(), 0)

    def get_flat_for_element(self, element: str) -> int:
        elem = element.upper()
        flat_map = {"NEUTRAL": self.damage_neutral, "EARTH": self.damage_earth,
                    "FIRE": self.damage_fire, "WATER": self.damage_water, "AIR": self.damage_air}
        return self.damage + flat_map.get(elem, 0)"""

    old_target_stats = """    def get_res_percent(self, element: str) -> int:
        elem = element.upper()
        if elem == "NEUTRAL": return self.res_percent_neutral
        if elem == "EARTH": return self.res_percent_earth
        if elem == "FIRE": return self.res_percent_fire
        if elem == "WATER": return self.res_percent_water
        if elem == "AIR": return self.res_percent_air
        return 0

    def get_res_fixed(self, element: str) -> int:
        elem = element.upper()
        if elem == "NEUTRAL": return self.res_fixed_neutral
        if elem == "EARTH": return self.res_fixed_earth
        if elem == "FIRE": return self.res_fixed_fire
        if elem == "WATER": return self.res_fixed_water
        if elem == "AIR": return self.res_fixed_air
        return 0"""

    new_target_stats = """    def get_res_percent(self, element: str) -> int:
        return {"NEUTRAL": self.res_percent_neutral, "EARTH": self.res_percent_earth,
                "FIRE": self.res_percent_fire, "WATER": self.res_percent_water,
                "AIR": self.res_percent_air}.get(element.upper(), 0)

    def get_res_fixed(self, element: str) -> int:
        return {"NEUTRAL": self.res_fixed_neutral, "EARTH": self.res_fixed_earth,
                "FIRE": self.res_fixed_fire, "WATER": self.res_fixed_water,
                "AIR": self.res_fixed_air}.get(element.upper(), 0)

    get_res_percent_for_element = get_res_percent
    get_res_fixed_for_element = get_res_fixed"""

    assert old_char_stats in content_norm, "old_char_stats not found"
    assert old_target_stats in content_norm, "old_target_stats not found"

    content_norm = content_norm.replace(old_char_stats, new_char_stats)
    content_norm = content_norm.replace(old_target_stats, new_target_stats)

    if is_crlf:
        content_norm = content_norm.replace("\n", "\r\n")
    path.write_text(content_norm, encoding="utf-8")
    print("Refactored damage_calculator.py")


def refactor_world_graph():
    path = REPO_ROOT / "dofus-network" / "navigation" / "world_graph.py"
    content = path.read_text(encoding="utf-8")
    is_crlf = "\r\n" in content
    content_norm = content.replace("\r\n", "\n")

    old_step = """    def step_towards(self, target: MapCoord) -> Tuple[MapCoord, Direction]:
        \"\"\"Calculates immediate next coordinate step towards target.\"\"\"
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
        return self, Direction.RIGHT"""

    new_step = """    def step_towards(self, target: MapCoord) -> Tuple[MapCoord, Direction]:
        \"\"\"Calculates immediate next coordinate step towards target.\"\"\"
        dx, dy = target.x - self.x, target.y - self.y
        if abs(dx) >= abs(dy) and dx != 0:
            return (MapCoord(self.x + 1, self.y), Direction.RIGHT) if dx > 0 else (MapCoord(self.x - 1, self.y), Direction.LEFT)
        if dy != 0:
            return (MapCoord(self.x, self.y + 1), Direction.DOWN) if dy > 0 else (MapCoord(self.x, self.y - 1), Direction.UP)
        return self, Direction.RIGHT"""

    old_zaap_name = """    def get_name(self, lang: str = "fr") -> str:
        lang = lang.lower()
        if lang == "en":
            return self.name_en or self.name_fr
        elif lang == "es":
            return self.name_es or self.name_fr
        return self.name_fr"""

    new_zaap_name = """    def get_name(self, lang: str = "fr") -> str:
        return {"en": self.name_en, "es": self.name_es}.get(lang.lower()) or self.name_fr"""

    old_border = """    @classmethod
    def get_border_cells_for_direction(cls, direction: Direction) -> Set[int]:
        \"\"\"Returns the set of cell IDs that trigger a map change in the specified direction.\"\"\"
        if direction == Direction.UP:
            return cls.TOP_BORDER_CELLS
        elif direction == Direction.DOWN:
            return cls.BOTTOM_BORDER_CELLS
        elif direction == Direction.LEFT:
            return cls.LEFT_BORDER_CELLS
        elif direction == Direction.RIGHT:
            return cls.RIGHT_BORDER_CELLS
        return set()"""

    new_border = """    @classmethod
    def get_border_cells_for_direction(cls, direction: Direction) -> Set[int]:
        \"\"\"Returns the set of cell IDs that trigger a map change in the specified direction.\"\"\"
        return {
            Direction.UP: cls.TOP_BORDER_CELLS,
            Direction.DOWN: cls.BOTTOM_BORDER_CELLS,
            Direction.LEFT: cls.LEFT_BORDER_CELLS,
            Direction.RIGHT: cls.RIGHT_BORDER_CELLS,
        }.get(direction, set())"""

    assert old_step in content_norm, "old_step not found"
    assert old_zaap_name in content_norm, "old_zaap_name not found"
    assert old_border in content_norm, "old_border not found"

    content_norm = content_norm.replace(old_step, new_step)
    content_norm = content_norm.replace(old_zaap_name, new_zaap_name)
    content_norm = content_norm.replace(old_border, new_border)

    if is_crlf:
        content_norm = content_norm.replace("\n", "\r\n")
    path.write_text(content_norm, encoding="utf-8")
    print("Refactored world_graph.py")


def refactor_resource_graph():
    path = REPO_ROOT / "dofus-network" / "navigation" / "resource_graph.py"
    content = path.read_text(encoding="utf-8")
    is_crlf = "\r\n" in content
    content_norm = content.replace("\r\n", "\n")

    old_res_name = """    def get_name(self, lang: str = "fr") -> str:
        lang = lang.lower()
        if lang == "en":
            return self.name_en or self.name_fr
        elif lang == "es":
            return self.name_es or self.name_fr
        return self.name_fr"""

    new_res_name = """    def get_name(self, lang: str = "fr") -> str:
        return {"en": self.name_en, "es": self.name_es}.get(lang.lower()) or self.name_fr"""

    assert old_res_name in content_norm, "old_res_name not found"
    content_norm = content_norm.replace(old_res_name, new_res_name)

    if is_crlf:
        content_norm = content_norm.replace("\n", "\r\n")
    path.write_text(content_norm, encoding="utf-8")
    print("Refactored resource_graph.py")


if __name__ == "__main__":
    refactor_damage_calculator()
    refactor_world_graph()
    refactor_resource_graph()
