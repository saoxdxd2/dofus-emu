"""Make one observation legible to every consumer.

`state.snapshot` returns a nested document — `position.map_id`, `character.level`,
`inventory.items[{gid, qty}]`. The planner reads flat keys — `map_id`, `level`,
`inventory[gid]`. Both were written against a snapshot shape that no longer exists, so on
a live client the planner asked for `map_id` and got `None`:

    map_id             -> None
    level              -> None
    quests_done        -> None
    inventory_capacity -> None

It could not tell which map it was on, so `required_map` comparisons always failed; it
could not see its own inventory, so a delivery objective always believed it lacked the
item and planned to go and farm it again. That is the "it does not understand the
player's situation" complaint, in code.

Rather than teach every consumer the nested shape — and have them disagree later — the
harness normalises once, here, on the way in. The nested keys are left untouched, so
anything that already reads them keeps working.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def _dig(source: Any, *path: str) -> Any:
    cur = source
    for part in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def flatten(obs: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the observation with the flat keys the planner reads filled in.

    Only adds keys that are missing. Anything already present wins, so a caller that
    provides a flat observation is unaffected.
    """
    if not isinstance(obs, dict):
        return {}

    out = dict(obs)

    # Position: the snapshot nests it, the planner and the verifier do not.
    if out.get("map_id") is None:
        value = _dig(obs, "position", "map_id")
        if value is not None:
            out["map_id"] = value
    if out.get("cell") is None:
        value = _dig(obs, "position", "cell")
        if value is not None:
            out["cell"] = value

    # Character level lives under `character` in the snapshot.
    if out.get("level") is None:
        value = _dig(obs, "character", "level")
        if value is not None:
            out["level"] = value

    # Inventory: the snapshot lists `items: [{gid, qty}]`; the planner looks up by id
    # (`inv.get(item) or inv.get(str(item))`) and therefore never found anything.
    inventory = obs.get("inventory")
    if isinstance(inventory, dict) and isinstance(inventory.get("items"), list):
        by_id: Dict[Any, Any] = {}
        for entry in inventory["items"]:
            if not isinstance(entry, dict):
                continue
            gid = entry.get("gid")
            if gid is None:
                continue
            qty = entry.get("qty", 1)
            by_id[gid] = qty
            by_id[str(gid)] = qty
        # Keep the kamas and anything else the snapshot carried, add the id lookup.
        merged = {k: v for k, v in inventory.items() if k != "items"}
        merged.update(by_id)
        merged["items"] = inventory["items"]
        out["inventory"] = merged

    return out
