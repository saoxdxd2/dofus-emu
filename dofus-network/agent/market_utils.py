"""Pure market utilities extracted from QuestPlanner.

These functions read price listings from observations without depending on
planner instance state. Extracted from agent/planner.py to reduce its size
and improve separation of concerns.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent.observe import flatten


def market_listing(obs: Dict[str, Any], gid: Any) -> Optional[Dict[str, Any]]:
    """The cheapest cached listing for a gid, or None.

    `state.snapshot.prices` only contains rows the page built from server
    messages. A row without a listing uid is not buyable, so it is not
    returned; the caller falls back to farming.
    """
    try:
        wanted = int(gid)
    except (TypeError, ValueError):
        return None
    rows = obs.get("prices")
    if not isinstance(rows, list):
        rows = flatten(obs or {}).get("prices")
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            if int(row.get("gid")) != wanted:
                continue
        except (TypeError, ValueError):
            continue
        candidates: List[Dict[str, Any]] = []
        for listing in row.get("listings") or []:
            if not isinstance(listing, dict):
                continue
            try:
                price = int(listing.get("price"))
                uid = int(listing.get("uid"))
            except (TypeError, ValueError):
                continue
            if uid > 0 and price > 0:
                candidates.append({"uid": uid, "price": price,
                                   "quantity": int(listing.get("quantity") or 1)})
        if candidates:
            return min(candidates, key=lambda item: item["price"])
    return None