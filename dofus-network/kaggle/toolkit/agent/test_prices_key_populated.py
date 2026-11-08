"""Guard for KNOWN_GAPS.md G108 / G109: the recorder cuts every price table
before the first price, and every observation carries an always-empty `prices`
key.

Verifies:
1. Observation contract in `scripts/mobile-disguise.js` populates `snap.prices`
   from both `__PriceCache.listings` and `__PriceCache.average`.
2. `agent/world_state.py` ingests `obs.get("prices")` into `state.prices` and
   encodes it into the feature vector (`prices_over_100`).
3. If historical trajectories are present, counts total observations and notes
   any non-empty prices.

No client, no network, no torch.

Run: python dofus-network/agent/test_prices_key_populated.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
DISGUISE = REPO / "scripts" / "mobile-disguise.js"
WORLD_STATE = REPO / "dofus-network" / "agent" / "world_state.py"
TRAJECTORIES = REPO / "trajectories"


def fail(msg: str) -> None:
    print(f"FAIL {msg}")
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"ok   {msg}")


def main() -> None:
    # 1. Disguise snapshot check
    if not DISGUISE.exists():
        fail(f"missing {DISGUISE}")
    disguise_src = DISGUISE.read_text(encoding="utf-8", errors="replace")
    if "snap.prices = priceRows" not in disguise_src:
        fail("scripts/mobile-disguise.js does not populate snap.prices from PriceCache")
    ok("scripts/mobile-disguise.js populates snap.prices with priceRows")

    # 2. WorldState ingestion check
    sys.path.insert(0, str(REPO / "dofus-network"))
    from agent.world_state import from_observation, encode, GLOBAL_FEATURES

    test_obs = {
        "version": 1,
        "prices": [{"gid": 18844, "avg": 1200, "listings": [{"uid": 1, "price": 1200}]}],
    }
    ws = from_observation(test_obs)
    if ws.prices != 1:
        fail(f"WorldState.prices expected 1, got {ws.prices}")
    ok("WorldState correctly ingests non-empty prices")

    enc = encode(ws)
    g_row = enc["global"]
    p_idx = list(GLOBAL_FEATURES).index("prices_over_100")
    if g_row[p_idx] <= 0:
        fail(f"global.prices_over_100 expected >0, got {g_row[p_idx]}")
    ok(f"WorldState feature vector includes prices metric ({g_row[p_idx]:.4f} at index {p_idx})")

    # 3. Trajectory scan report
    checked = 0
    non_empty = 0
    if TRAJECTORIES.exists():
        for path in sorted(TRAJECTORIES.glob("*.jsonl")):
            try:
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    obs = row.get("observation") or row.get("snapshot") or {}
                    if not isinstance(obs, dict):
                        continue
                    if "prices" in obs:
                        checked += 1
                        if obs["prices"]:
                            non_empty += 1
            except OSError:
                continue
    ok(f"trajectory scan: {checked} observations scanned ({non_empty} historical non-empty)")
    print()
    print("G108 / G109 price-population checks passed")


if __name__ == "__main__":
    main()
