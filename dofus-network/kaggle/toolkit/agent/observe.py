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

import re
from typing import Any, Dict, Optional


def _dig(source: Any, *path: str) -> Any:
    cur = source
    for part in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


# Argument names whose values are credentials, not diagnostics. The page also
# filters these at the ring, but a client running an older page build still
# sends the ticket, and every snapshot is written to the trajectory corpus.
_SECRET_ARG = re.compile(r"^(ticket|password|pass|secret|token|salt|apikey|api_key|key)$",
                         re.IGNORECASE)


def redact_packet_secrets(obs: Any) -> Any:
    """Replace credential-shaped packet arguments in a snapshot, in place.

    The map-transition rings record packet arguments so a refused map change
    can name the map it asked for. The login handshake's
    ``AuthenticationTicketMessage`` carries the live session ticket under
    ``ticket``, so the persisted observation used to contain a credential.
    The rings keep their shape and their dates; only the values go.
    """
    if not isinstance(obs, dict):
        return obs
    block = obs.get("map")
    transition = block.get("transition") if isinstance(block, dict) else None
    if not isinstance(transition, dict):
        return obs
    for side in ("outgoing", "incoming"):
        rows = transition.get(side)
        if not isinstance(rows, list):
            continue
        for row in rows:
            args = row.get("args") if isinstance(row, dict) else None
            if not isinstance(args, dict):
                continue
            for key in list(args):
                if _SECRET_ARG.match(str(key)):
                    args.pop(key, None)
    return obs


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
