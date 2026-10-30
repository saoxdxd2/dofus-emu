"""Relational features of the recorded observation, for a learned state encoder.

The 26-value context block kept ids and counts; the observation itself carries the
map as a set of entities with cells - NPCs, interactives, monster groups, exits with
their cell lists - which is the structure a translation-invariant encoder can learn
from. These helpers turn one observation into a global vector and a set of entity
vectors with coordinates relative to the character's cell, so the encoder sees
"this NPC is two cells away", not "NpcInformations 3888 exists on map 147062786".

Cells use the client's 14-per-row grid: x = cell % 14, y = cell // 14.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from agent.observe import flatten

ENTITY_DIM = 12
GLOBAL_DIM = 20
WORLD_KEYS = ("map_id", "cell", "in_dialog", "in_fight", "quest_steps",
              "objectives_completed", "inventory_size", "kamas",
              "ready_objectives", "quests_active")


def _cell_xy(cell: Any) -> Optional[Tuple[float, float]]:
    try:
        value = int(cell)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return float(value % 14), float(value // 14)


def _size(value: Any) -> float:
    if isinstance(value, (list, tuple, dict, set)):
        return float(len(value))
    return 0.0


def _num(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _onehot(slot: int, size: int = ENTITY_DIM) -> List[float]:
    row = [0.0] * size
    row[slot] = 1.0
    return row


def entity_vectors(obs: Dict[str, Any]) -> List[List[float]]:
    """One row per visible entity; coordinates are relative to the character."""
    if not isinstance(obs, dict):
        return []
    flat = flatten(obs)
    here = _cell_xy(flat.get("cell"))
    px, py = here if here else (0.0, 0.0)
    m = obs.get("map") if isinstance(obs.get("map"), dict) else {}
    out: List[List[float]] = []

    def add(kind: int, cell: Any, extra: Sequence[float] = ()) -> None:
        xy = _cell_xy(cell)
        row = _onehot(kind, size=5) + [
            ((xy[0] - px) / 10.0) if xy else 0.0,
            ((xy[1] - py) / 10.0) if xy else 0.0,
            (abs(xy[0] - px) + abs(xy[1] - py)) / 20.0 if xy else 1.0,
        ] + list(extra)[:ENTITY_DIM - 8]
        row += [0.0] * max(0, ENTITY_DIM - len(row))
        out.append(row[:ENTITY_DIM])

    for npc in (m.get("npcs") or [])[:12]:
        if isinstance(npc, dict):
            add(1, npc.get("cell"), (_num(npc.get("npc_id")) % 7 / 7.0,))
    for it in (m.get("interactives") or [])[:8]:
        if isinstance(it, dict):
            add(2, it.get("cell"), (_num(it.get("type_id")) % 7 / 7.0,))
    for g in (m.get("groups") or [])[:6]:
        if not isinstance(g, dict):
            continue
        monsters = g.get("monsters") if isinstance(g.get("monsters"), list) else []
        first = monsters[0] if monsters and isinstance(monsters[0], dict) else {}
        add(3, first.get("cell") or g.get("cell"),
            (len(monsters) / 8.0, _num(g.get("group_id")) % 7 / 7.0,
             _num(first.get("level")) / 200.0))
    for ex in (m.get("exits") or [])[:8]:
        if not isinstance(ex, dict):
            continue
        cells = ex.get("cells") if isinstance(ex.get("cells"), list) else []
        best = None
        for cell in cells:
            xy = _cell_xy(cell)
            if xy is None:
                continue
            dist = abs(xy[0] - px) + abs(xy[1] - py)
            if best is None or dist < best[0]:
                best = (dist, xy)
        if best is not None:
            row = _onehot(4, size=5)
            row += [(best[1][0] - px) / 10.0, (best[1][1] - py) / 10.0,
                    best[0] / 20.0, 1.0 if ex.get("blocked") else 0.0]
            row += [0.0] * max(0, ENTITY_DIM - len(row))
            out.append(row[:ENTITY_DIM])
        else:
            add(4, None, (1.0 if ex.get("blocked") else 0.0,))
    return out


def global_vector(obs: Dict[str, Any]) -> List[float]:
    if not isinstance(obs, dict):
        return [0.0] * GLOBAL_DIM
    flat = flatten(obs)
    hp = obs.get("hp") if isinstance(obs.get("hp"), dict) else {}
    dlg = obs.get("dialog") if isinstance(obs.get("dialog"), dict) else {}
    m = obs.get("map") if isinstance(obs.get("map"), dict) else {}
    life = _num(hp.get("life"))
    maximum = _num(hp.get("max"), 1.0) or 1.0
    return [
        _num(flat.get("level")) / 200.0,
        life / maximum,
        _num(hp.get("ap")) / 12.0,
        _num(hp.get("mp")) / 6.0,
        min(1.0, _num(obs.get("kamas")) / 100000.0),
        min(1.0, _size(obs.get("inventory")) / 60.0),
        min(1.0, _size(flat.get("quests_active")) / 20.0),
        min(1.0, _size(obs.get("ready_objectives")) / 10.0),
        min(1.0, _size(flat.get("quest_steps")) / 40.0),
        min(1.0, _num(obs.get("objectives_completed")) / 50.0),
        1.0 if flat.get("in_dialog") else 0.0,
        min(1.0, _size(dlg.get("replies")) / 8.0),
        1.0 if flat.get("in_fight") else 0.0,
        1.0 if obs.get("alive") is not False else 0.0,
        min(1.0, _size(m.get("npcs")) / 12.0),
        min(1.0, _size(m.get("exits")) / 8.0),
        min(1.0, _size(m.get("groups")) / 6.0),
        min(1.0, _size(m.get("interactives")) / 8.0),
        1.0 if obs.get("tutorial") else 0.0,
        min(1.0, len(entity_vectors(obs)) / 20.0),
    ]


def world_delta(before: Dict[str, Any], after: Dict[str, Any]) -> List[float]:
    """Which watched keys changed between two observations - the world-model target."""
    b = flatten(before or {})
    a = flatten(after or {})
    out: List[float] = []
    for key in WORLD_KEYS:
        out.append(0.0 if b.get(key) == a.get(key) else 1.0)
    return out
