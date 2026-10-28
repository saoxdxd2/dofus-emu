"""Synthetic 560-cell boards for the 30M grid encoder.

The 156 recorded boards have null AP/MP, so they cannot produce a cast. This tool
keeps their real map flags, fighter placement, cells and spell descriptors and
sweeps plausible AP/MP values. The solver labels each variant with the legal
stratified plans it would choose. The result is a **solver-distillation** corpus,
not live combat data:

    python -m combat.grid_synth --limit 20 --out data/combat_grid_synth
    python -m combat.grid_synth --out data/combat_grid_synth

The live gate still applies: a real fight on page build 2026-10-27s is required
before any promotion.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import random
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from combat import grid_dataset

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "combat_grid_synth"
VARIANTS = ((4, 2), (6, 3), (8, 4), (10, 3))


def _rows(path: Path) -> List[Dict[str, Any]]:
    out = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            board = row.get("board") if isinstance(row, dict) else None
            if isinstance(board, dict):
                out.append(board)
    return out


def _variant(payload: Dict[str, Any], ap: int, mp: int) -> Dict[str, Any]:
    board = copy.deepcopy(payload)
    board["my_ap"] = ap
    board["my_mp"] = mp
    board["is_my_turn"] = True
    board["in_preparation"] = False
    board["in_fight"] = True
    board["current_fighter_id"] = board.get("my_id")
    self_id = board.get("my_id")
    for fighter in board.get("fighters") or []:
        if not isinstance(fighter, dict):
            continue
        if fighter.get("id") == self_id or fighter.get("is_me"):
            fighter["ap"] = ap
            fighter["mp"] = mp
            fighter["max_ap"] = ap
            fighter["max_mp"] = mp
            fighter["hp"] = fighter.get("hp") or 60
            fighter["max_hp"] = fighter.get("max_hp") or 60
        else:
            fighter["ap"] = fighter.get("ap") or 6
            fighter["mp"] = fighter.get("mp") or 3
            fighter["max_ap"] = fighter.get("max_ap") or fighter["ap"]
            fighter["max_mp"] = fighter.get("max_mp") or fighter["mp"]
            fighter["hp"] = fighter.get("hp") or 100
            fighter["max_hp"] = fighter.get("max_hp") or fighter["hp"]
    return board


def _random_variant(payload: Dict[str, Any], rng: random.Random) -> Dict[str, Any]:
    board = _variant(payload, rng.choice((4, 6, 8, 10, 12)),
                     rng.choice((1, 2, 3, 4, 5)))
    self_id = board.get("my_id")
    for fighter in board.get("fighters") or []:
        if not isinstance(fighter, dict):
            continue
        if fighter.get("id") == self_id or fighter.get("is_me"):
            hp = rng.randint(40, 100)
        else:
            hp = rng.randint(50, 250)
        fighter["hp"] = hp
        fighter["max_hp"] = hp
    return board


def build(path: Path, limit: int = 0, random_variants: int = 0,
          seed: int = 7) -> Dict[str, Any]:
    payloads = _rows(path)
    if limit:
        payloads = payloads[:limit]
    synthetic: List[Dict[str, Any]] = []
    rng = random.Random(seed)
    for payload in payloads:
        if random_variants > 0:
            for _ in range(random_variants):
                synthetic.append(_random_variant(payload, rng))
        else:
            for ap, mp in VARIANTS:
                synthetic.append(_variant(payload, ap, mp))
    arrays, manifest = grid_dataset.build(synthetic)
    manifest.update({
        "source": str(path),
        "source_boards": len(payloads),
        "variants": ("random:%d:seed=%d" % (random_variants, seed)
                     if random_variants else list(VARIANTS)),
        "kind": "solver_distillation",
        "live": False,
    })
    return arrays, manifest


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--boards", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--random-variants", type=int, default=0,
                        help="random AP/MP/HP variants per board instead of the fixed four")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)
    path = args.boards
    if path is None:
        path = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DofusFarm" / "fight_boards.jsonl"
    if not path.is_file():
        print("boards file not found:", path)
        return 2
    arrays, manifest = build(path, args.limit, args.random_variants, args.seed)
    if not arrays:
        print("no synthetic board yielded a legal plan:", manifest)
        return 1
    manifest = grid_dataset.write_splits(arrays, manifest, args.out)
    print("synthetic rows", len(arrays["labels"]), "manifest", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
