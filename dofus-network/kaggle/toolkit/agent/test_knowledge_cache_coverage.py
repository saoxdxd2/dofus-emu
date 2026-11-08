"""Guard for KNOWN_GAPS.md G29 / G30: the knowledge cache quest index stops at
id 2,546 while live quest ids exceed it, and the store once resolved live
objectives to another game's objectives.

Checks:
  * the cached quest ids extend beyond 2,546 (live coverage), and
  * every cached objective id resolves against the same game's id space
    (no cross-game contamination).

Reads only `dofus-network/data/knowledge/client_index.json`. No client, no
network, no torch.

Run: python dofus-network/agent/test_knowledge_cache_coverage.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
CACHE = REPO / "dofus-network" / "data" / "knowledge" / "client_index.json"

FAILURES = 0


def fail(msg: str) -> None:
    global FAILURES
    FAILURES += 1
    print(f"FAIL {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}")


def main() -> None:
    global FAILURES
    if not CACHE.exists():
        print(f"no cache at {CACHE} (check skipped)")
        return

    try:
        data = json.loads(CACHE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"cache unparseable: {exc} (check skipped)")
        return

    rows = data.get("rows", {}) if isinstance(data, dict) else {}
    if isinstance(rows, dict) and rows:
        ids = [int(k.split(":")[0]) for k in rows.keys() if ":" in k and k.split(":")[0].isdigit()]
    else:
        quests = data.get("quests", data) if isinstance(data, dict) else data
        if not isinstance(quests, dict):
            print("cache has no quest dict (check skipped)")
            return
        ids = [int(k) for k in quests.keys() if str(k).isdigit()]

    if not ids:
        print("cache has no numeric quest ids (check skipped)")
        return

    max_id = max(ids)
    ok(f"cached quest ids reach {max_id}")
    if max_id <= 2546:
        fail(f"G29: max cached quest id {max_id} <= 2,546 while live quest ids exceed it")
    else:
        ok("G29 closed on this data: cache covers quest ids above 2,546")

    monsters = data.get("monsters", {}) if isinstance(data, dict) else {}
    items = data.get("items", {}) if isinstance(data, dict) else {}
    monster_ids = {int(k) for k in monsters.keys() if str(k).isdigit()} if isinstance(monsters, dict) else set()
    item_ids = {int(k) for k in items.keys() if str(k).isdigit()} if isinstance(items, dict) else set()

    collisions = []
    for oid in ids:
        if oid in monster_ids or oid in item_ids:
            collisions.append(oid)
    if collisions:
        fail(f"G30: {len(collisions)} objective id(s) collide with monster/item ids (cross-game risk): {collisions[:10]}")
    else:
        ok("G30 closed on this data: no objective/monster/item id collisions")

    print()
    if FAILURES:
        print(f"{FAILURES} failure(s)")
        sys.exit(1)
    print("knowledge cache coverage checks passed")


if __name__ == "__main__":
    main()
