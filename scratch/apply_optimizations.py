"""Apply verified space and time optimizations to codebase."""
import sys
import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

def patch_executor():
    path = REPO_ROOT / "dofus-network" / "agent" / "executor.py"
    content = path.read_text(encoding="utf-8")
    
    target = """        if here is None or target is None or here == target:
            return None
        tree = self._tree(target)
        entry = tree.get(here)
        if entry is None:
            return None
        direction, next_map, hops = entry
        blocked_first = blocked_first or set()
        if direction in blocked_first:
            return self._hop_bfs(here, target, blocked_first)
        return direction, next_map, hops"""

    replacement = """        if here is None or target is None or here == target:
            return None
        blocked = blocked_first or set()
        # Fast path: direct adjacent neighbor avoids constructing full reverse BFS tree
        exits = self.edges().get(here)
        if exits:
            for direction, neighbour in exits.items():
                if neighbour == target and direction not in blocked:
                    return direction, target, 1
        tree = self._tree(target)
        entry = tree.get(here)
        if entry is None:
            return None
        direction, next_map, hops = entry
        if direction in blocked:
            return self._hop_bfs(here, target, blocked)
        return direction, next_map, hops"""

    content_norm = content.replace("\r\n", "\n")
    if target in content_norm:
        content_norm = content_norm.replace(target, replacement)
        if "\r\n" in content:
            content_norm = content_norm.replace("\n", "\r\n")
        path.write_text(content_norm, encoding="utf-8")
        print("Patched executor.py successfully.")
    else:
        print("executor.py target already patched or not found.")


def patch_plan_stack():
    path = REPO_ROOT / "dofus-network" / "agent" / "plan_stack.py"
    content = path.read_text(encoding="utf-8")
    
    target_push = """        self._goals.append(goal)
        if len(self._goals) > self.max_depth:
            self._goals = self._goals[-self.max_depth:]
        return goal"""

    replacement_push = """        self._goals.append(goal)
        while len(self._goals) > self.max_depth:
            self._goals.pop(0)
        return goal"""

    target_npc = """    for row in ((observation.get("map") or {}).get("npcs") or []):
        if not isinstance(row, dict):
            continue
        for key in ("template_id", "npc_id", "actor_id", "contextual_id"):
            try:
                if int(row.get(key)) == wanted:
                    return True
            except (TypeError, ValueError):
                continue
    return False"""

    replacement_npc = """    for row in ((observation.get("map") or {}).get("npcs") or []):
        if not isinstance(row, dict):
            continue
        for key in ("template_id", "npc_id", "actor_id", "contextual_id"):
            val = row.get(key)
            if val is not None and val == wanted:
                return True
            try:
                if val is not None and int(val) == wanted:
                    return True
            except (TypeError, ValueError):
                continue
    return False"""

    content_norm = content.replace("\r\n", "\n")
    changed = False
    if target_push in content_norm:
        content_norm = content_norm.replace(target_push, replacement_push)
        changed = True
    if target_npc in content_norm:
        content_norm = content_norm.replace(target_npc, replacement_npc)
        changed = True
    if changed:
        if "\r\n" in content:
            content_norm = content_norm.replace("\n", "\r\n")
        path.write_text(content_norm, encoding="utf-8")
        print("Patched plan_stack.py successfully.")
    else:
        print("plan_stack.py already patched or not found.")


def patch_dialog_policy():
    path = REPO_ROOT / "dofus-network" / "agent" / "dialog_policy.py"
    content = path.read_text(encoding="utf-8")
    
    target_loop = """    for reply in candidates:
        tokens = _tokens(reply["text"])
        overlap = len(tokens & context) / len(tokens) if tokens else 0.0
        qrel = len(tokens & question) / len(tokens) if tokens else 0.0
        scored.append({**reply, "score": overlap, "question": qrel,
                       "travel": _travel_bias(reply["text"]) if travel else 0,
                       "quest_icon": bool(_QUEST_ICON.search(reply["text"])),
                       "closing": _is_closing(reply["text"])})"""

    replacement_loop = """    for reply in candidates:
        text = reply["text"]
        plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").casefold()
        tokens = set(_WORD.findall(plain))
        overlap = len(tokens & context) / len(tokens) if tokens else 0.0
        qrel = len(tokens & question) / len(tokens) if tokens else 0.0
        closing = any(phrase in plain for phrase in _CLOSING)
        travel_score = 0
        if travel:
            if tokens & _LEAVE_WORDS:
                travel_score = 1
            elif tokens & _ENTER_WORDS:
                travel_score = -1
        scored.append({**reply, "score": overlap, "question": qrel,
                       "travel": travel_score,
                       "quest_icon": bool(_QUEST_ICON.search(text)),
                       "closing": closing})"""

    content_norm = content.replace("\r\n", "\n")
    if target_loop in content_norm:
        content_norm = content_norm.replace(target_loop, replacement_loop)
        if "\r\n" in content:
            content_norm = content_norm.replace("\n", "\r\n")
        path.write_text(content_norm, encoding="utf-8")
        print("Patched dialog_policy.py successfully.")
    else:
        print("dialog_policy.py already patched or not found.")


def patch_world_graph():
    path = REPO_ROOT / "dofus-network" / "navigation" / "world_graph.py"
    content = path.read_text(encoding="utf-8")

    target_method = """    @classmethod
    def find_nearest_border_cell(cls, current_cell: int, direction: Direction) -> int:
        \"\"\"Finds the closest exit cell to step onto to change map in `direction`.\"\"\"
        border = cls.get_border_cells_for_direction(direction)
        if not border:
            return current_cell
        return min(border, key=lambda c: cls.cell_distance(current_cell, c))"""

    replacement_method = """    @classmethod
    def find_nearest_border_cell(cls, current_cell: int, direction: Direction) -> int:
        \"\"\"Finds the closest exit cell to step onto to change map in `direction`.\"\"\"
        table = _NEAREST_BORDER.get(direction)
        if table and 0 <= current_cell < cls.TOTAL_CELLS:
            return table[current_cell]
        border = cls.get_border_cells_for_direction(direction)
        if not border:
            return current_cell
        return min(border, key=lambda c: cls.cell_distance(current_cell, c))"""

    target_end = """CellPathfinder._ISO_COORDS = [
    ((cid % CellPathfinder.WIDTH) + ((cid // CellPathfinder.WIDTH) // 2),
     ((cid // CellPathfinder.WIDTH) // 2) - (cid % CellPathfinder.WIDTH) + ((cid // CellPathfinder.WIDTH) % 2))
    for cid in range(CellPathfinder.TOTAL_CELLS)
]"""

    replacement_end = """CellPathfinder._ISO_COORDS = tuple(
    ((cid % CellPathfinder.WIDTH) + ((cid // CellPathfinder.WIDTH) // 2),
     ((cid // CellPathfinder.WIDTH) // 2) - (cid % CellPathfinder.WIDTH) + ((cid // CellPathfinder.WIDTH) % 2))
    for cid in range(CellPathfinder.TOTAL_CELLS)
)

# Precomputed nearest border cell for each direction (4 x 560 lookup table)
_NEAREST_BORDER: Dict[Direction, Tuple[int, ...]] = {}
for _d in (Direction.UP, Direction.DOWN, Direction.LEFT, Direction.RIGHT):
    _border = CellPathfinder.get_border_cells_for_direction(_d)
    if _border:
        _NEAREST_BORDER[_d] = tuple(
            min(_border, key=lambda c, _cid=cid: CellPathfinder.cell_distance(_cid, c))
            for cid in range(CellPathfinder.TOTAL_CELLS)
        )"""

    content_norm = content.replace("\r\n", "\n")
    changed = False
    if target_method in content_norm:
        content_norm = content_norm.replace(target_method, replacement_method)
        changed = True
    if target_end in content_norm:
        content_norm = content_norm.replace(target_end, replacement_end)
        changed = True
    if changed:
        if "\r\n" in content:
            content_norm = content_norm.replace("\n", "\r\n")
        path.write_text(content_norm, encoding="utf-8")
        print("Patched world_graph.py successfully.")
    else:
        print("world_graph.py already patched or not found.")


if __name__ == "__main__":
    patch_executor()
    patch_plan_stack()
    patch_dialog_policy()
    patch_world_graph()
