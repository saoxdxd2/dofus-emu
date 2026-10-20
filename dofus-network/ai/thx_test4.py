"""Fourth, blind test set for the THX-01 policy.

Written after the SFT-val guard work and not used for tuning. It uses official
localized item names and new sentence shapes. Run from ``dofus-network``::

    python ai/thx_test4.py --show-failures
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai.thx_policy import ThxPolicy
from agent.tool_eval import evaluate

CASES: List[Dict[str, Any]] = [
    {"id": "en-b01", "lang": "en", "goal": "emergency halt, low hp",
     "tool": "safe.stop", "args": {}},
    {"id": "en-b02", "lang": "en", "goal": "look up the spell Cawwot",
     "tool": "knowledge.search", "args": {}},
    {"id": "en-b03", "lang": "en", "goal": "check the market for Gobball Wool",
     "tool": "hdv.scan", "args": {"item": "Gobball Wool"}},
    {"id": "en-b04", "lang": "en", "goal": "offer 4 Gobball Wool for 800 kamas",
     "tool": "hdv.list", "args": {"item": "Gobball Wool", "quantity": 4,
                                  "price_rule": "fixed", "price": 800}},
    {"id": "en-b05", "lang": "en", "goal": "sell 8 Gobball Wool",
     "tool": "inv.sell", "args": {"item": "Gobball Wool", "quantity": 8}},
    {"id": "en-b06", "lang": "en", "goal": "put 3 Gobball Wool on the market for 1200 kamas",
     "tool": "hdv.list", "args": {"item": "Gobball Wool", "quantity": 3,
                                  "price_rule": "fixed", "price": 1200}},
    {"id": "en-b07", "lang": "en", "goal": "take me to [5, -9]",
     "tool": "nav.goto", "args": {"x": 5, "y": -9}},
    {"id": "en-b08", "lang": "en", "goal": "quests in the Bonta area",
     "tool": "quest.list", "args": {"area": "Bonta"}},
    {"id": "en-b09", "lang": "en", "goal": "dungeon quests near Amakna",
     "tool": "quest.list", "args": {"area": "Amakna"}},
    {"id": "en-b10", "lang": "en", "goal": "list all my items",
     "tool": "inv.list", "args": {}},
    {"id": "en-b11", "lang": "en", "goal": "what is the Cawwot spell?",
     "tool": "knowledge.search", "args": {}},
    {"id": "en-b12", "lang": "en", "goal": "who is Gobeefy?",
     "tool": "knowledge.search", "args": {}},
    {"id": "en-b13", "lang": "en", "goal": "start farming Gobballs",
     "tool": "farm.start", "args": {}},
    {"id": "en-b14", "lang": "en", "goal": "pass the turn",
     "tool": "fight.pass", "args": {}},
    {"id": "en-b15", "lang": "en", "goal": "enter the fight",
     "tool": "fight.join", "args": {}},
    {"id": "en-b16", "lang": "en", "goal": "follow me",
     "tool": "nav.follow", "args": {}},
    {"id": "en-b17", "lang": "en", "goal": "advance quest 1940",
     "tool": "quest.advance", "args": {"quest_id": 1940}},

    {"id": "fr-b01", "lang": "fr", "goal": "urgence, potions épuisées",
     "tool": "safe.stop", "args": {}},
    {"id": "fr-b02", "lang": "fr", "goal": "cherche la Fonderie des Waddicts",
     "tool": "knowledge.search", "args": {}},
    {"id": "fr-b03", "lang": "fr", "goal": "vérifie le marché pour la Laine de Bouftou",
     "tool": "hdv.scan", "args": {"item": "Laine de Bouftou"}},
    {"id": "fr-b04", "lang": "fr", "goal": "vends 8 Laines de Bouftou",
     "tool": "inv.sell", "args": {"item": "Laine de Bouftou", "quantity": 8}},
    {"id": "fr-b05", "lang": "fr", "goal": "mets 3 Laines de Bouftou en vente à 1200 kamas",
     "tool": "hdv.list", "args": {"item": "Laine de Bouftou", "quantity": 3,
                                  "price_rule": "fixed", "price": 1200}},
    {"id": "fr-b06", "lang": "fr", "goal": "quêtes de donjon vers Amakna",
     "tool": "quest.list", "args": {"area": "Amakna"}},
    {"id": "fr-b07", "lang": "fr", "goal": "liste mes objets",
     "tool": "inv.list", "args": {}},

    {"id": "es-b01", "lang": "es", "goal": "parada de emergencia",
     "tool": "safe.stop", "args": {}},
    {"id": "es-b02", "lang": "es", "goal": "consulta el mercado para Lana de jalatí",
     "tool": "hdv.scan", "args": {"item": "Lana de jalatí"}},
    {"id": "es-b03", "lang": "es", "goal": "vende 8 Lanas de jalatí",
     "tool": "inv.sell", "args": {"item": "Lana de jalatí", "quantity": 8}},
    {"id": "es-b04", "lang": "es", "goal": "pon 3 Lanas de jalatí a la venta por 1200 kamas",
     "tool": "hdv.list", "args": {"item": "Lana de jalatí", "quantity": 3,
                                  "price_rule": "fixed", "price": 1200}},
    {"id": "es-b05", "lang": "es", "goal": "misiones de mazmorra cerca de Amakna",
     "tool": "quest.list", "args": {"area": "Amakna"}},
    {"id": "es-b06", "lang": "es", "goal": "busca la Fonderie des Waddicts",
     "tool": "knowledge.search", "args": {}},
    {"id": "es-b07", "lang": "es", "goal": "ve a [5, -9]",
     "tool": "nav.goto", "args": {"x": 5, "y": -9}},

    {"id": "de-b01", "lang": "de", "goal": "Notfall, keine Tränke",
     "tool": "safe.stop", "args": {}},
    {"id": "de-b02", "lang": "de", "goal": "prüfe den Markt für Fresssackwolle",
     "tool": "hdv.scan", "args": {"item": "Fresssackwolle"}},
    {"id": "de-b03", "lang": "de", "goal": "verkaufe 8 Fresssackwolle",
     "tool": "inv.sell", "args": {"item": "Fresssackwolle", "quantity": 8}},
    {"id": "de-b04", "lang": "de", "goal": "biete 3 Fresssackwolle für 1200 Kamas an",
     "tool": "hdv.list", "args": {"item": "Fresssackwolle", "quantity": 3,
                                  "price_rule": "fixed", "price": 1200}},
    {"id": "de-b05", "lang": "de", "goal": "Dungeon-Quests in der Nähe von Amakna",
     "tool": "quest.list", "args": {"area": "Amakna"}},
    {"id": "de-b06", "lang": "de", "goal": "suche den Dungeon der Schattenschmiede",
     "tool": "knowledge.search", "args": {}},
    {"id": "de-b07", "lang": "de", "goal": "gehe zu [5, -9]",
     "tool": "nav.goto", "args": {"x": 5, "y": -9}},
]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Fourth blind THX-01 test set")
    parser.add_argument("--model", default=None)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--show-failures", action="store_true")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    policy = ThxPolicy(model=args.model, device=args.device)
    predictions: Dict[str, Any] = {}
    started = time.perf_counter()
    for case in CASES:
        call = policy.choose(case["goal"])
        predictions[case["id"]] = (
            {"tool": call.tool, "args": call.args} if call is not None else None)
    elapsed = time.perf_counter() - started

    report = evaluate(predictions, CASES)
    totals = report["totals"]
    print(f"THX-01 blind - {totals['cases']} cases in {elapsed:.1f}s")
    print(f"  tool accuracy: {totals['tool_ok']}/{totals['cases']} "
          f"({100 * totals['tool_ok'] / max(totals['cases'], 1):.1f}%)")
    print(f"  call accuracy: {totals['call_ok']}/{totals['cases']} "
          f"({100 * totals['call_ok'] / max(totals['cases'], 1):.1f}%)")
    for lang, stats in report["by_lang"].items():
        print(f"  [{lang}] tool {stats['tool_ok']}/{stats['cases']}  "
              f"call {stats['call_ok']}/{stats['cases']}")
    if args.show_failures:
        for failure in report["failures"]:
            print(f"  MISS {failure['id']}: {failure['goal']}")
            print(f"       expected {failure['expected']}")
            print(f"       predicted {failure['predicted']}")
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
