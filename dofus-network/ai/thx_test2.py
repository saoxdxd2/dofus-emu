"""Second fresh test set for the THX-01 policy.

Written after the arg fixes in ``ai/thx_test.py`` and not used for tuning. It
contains new verbs, word orders and entity names. Run from ``dofus-network``::

    python ai/thx_test2.py --show-failures
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
    {"id": "en-c01", "lang": "en", "goal": "abort the current plan",
     "tool": "safe.stop", "args": {}},
    {"id": "en-c02", "lang": "en", "goal": "head to [9, -1]",
     "tool": "nav.goto", "args": {"x": 9, "y": -1}},
    {"id": "en-c03", "lang": "en", "goal": "which quests can I take in Amakna?",
     "tool": "quest.list", "args": {"area": "Amakna"}},
    {"id": "en-c04", "lang": "en", "goal": "what does Bouftou Wool cost on the market?",
     "tool": "hdv.scan", "args": {"item": "Bouftou Wool"}},
    {"id": "en-c05", "lang": "en", "goal": "list 5 Bouftou Wool at 900 kamas",
     "tool": "hdv.list",
     "args": {"item": "Bouftou Wool", "quantity": 5, "price_rule": "fixed", "price": 900}},
    {"id": "en-c06", "lang": "en", "goal": "sell 6 Bouftou Wool now",
     "tool": "inv.sell", "args": {"item": "Bouftou Wool", "quantity": 6}},
    {"id": "en-c07", "lang": "en", "goal": "message the group: on my way",
     "tool": "chat.say", "args": {"text": "on my way", "channel": "team"}},
    {"id": "en-c08", "lang": "en", "goal": "what is a Kanig?",
     "tool": "knowledge.search", "args": {"query": "Kanig"}},
    {"id": "en-c09", "lang": "en", "goal": "begin farming Boars",
     "tool": "farm.start", "args": {"monster": "Boars"}},
    {"id": "en-c10", "lang": "en", "goal": "join this fight",
     "tool": "fight.join", "args": {}},
    {"id": "en-c11", "lang": "en", "goal": "pass my turn",
     "tool": "fight.pass", "args": {}},
    {"id": "en-c12", "lang": "en", "goal": "list what I own",
     "tool": "inv.list", "args": {}},
    {"id": "en-c13", "lang": "en", "goal": "where exactly am I?",
     "tool": "state.snapshot", "args": {}},
    {"id": "en-c14", "lang": "en", "goal": "walk beside me",
     "tool": "nav.follow", "args": {}},
    {"id": "en-c15", "lang": "en", "goal": "advance quest 2001",
     "tool": "quest.advance", "args": {"quest_id": 2001}},

    {"id": "fr-c01", "lang": "fr", "goal": "abandonne tout",
     "tool": "safe.stop", "args": {}},
    {"id": "fr-c02", "lang": "fr", "goal": "rends-toi à [1, 1]",
     "tool": "nav.goto", "args": {"x": 1, "y": 1}},
    {"id": "fr-c03", "lang": "fr", "goal": "quêtes disponibles à Amakna",
     "tool": "quest.list", "args": {"area": "Amakna"}},
    {"id": "fr-c04", "lang": "fr", "goal": "prix de la Laine de Bouftou sur le marché",
     "tool": "hdv.scan", "args": {"item": "Laine de Bouftou"}},
    {"id": "fr-c05", "lang": "fr", "goal": "vends 5 Laines de Bouftou",
     "tool": "inv.sell", "args": {"item": "Laine de Bouftou", "quantity": 5}},
    {"id": "fr-c06", "lang": "fr", "goal": "préviens l'équipe : j'arrive",
     "tool": "chat.say", "args": {"text": "j'arrive", "channel": "team"}},

    {"id": "es-c01", "lang": "es", "goal": "para todo inmediatamente",
     "tool": "safe.stop", "args": {}},
    {"id": "es-c02", "lang": "es", "goal": "dirígete a [0, 5]",
     "tool": "nav.goto", "args": {"x": 0, "y": 5}},
    {"id": "es-c03", "lang": "es", "goal": "misiones disponibles en Amakna",
     "tool": "quest.list", "args": {"area": "Amakna"}},
    {"id": "es-c04", "lang": "es", "goal": "precio de la Lana de Bouftou en el mercado",
     "tool": "hdv.scan", "args": {"item": "Lana de Bouftou"}},
    {"id": "es-c05", "lang": "es", "goal": "vende 5 Lanas de Bouftou",
     "tool": "inv.sell", "args": {"item": "Lana de Bouftou", "quantity": 5}},
    {"id": "es-c06", "lang": "es", "goal": "avisa al equipo: voy",
     "tool": "chat.say", "args": {"text": "voy", "channel": "team"}},

    {"id": "de-c01", "lang": "de", "goal": "beende alles",
     "tool": "safe.stop", "args": {}},
    {"id": "de-c02", "lang": "de", "goal": "gehe nach [2, 2]",
     "tool": "nav.goto", "args": {"x": 2, "y": 2}},
    {"id": "de-c03", "lang": "de", "goal": "Quests in Amakna",
     "tool": "quest.list", "args": {"area": "Amakna"}},
    {"id": "de-c04", "lang": "de", "goal": "Preis von Bouftou-Wolle auf dem Markt",
     "tool": "hdv.scan", "args": {"item": "Bouftou-Wolle"}},
    {"id": "de-c05", "lang": "de", "goal": "verkaufe 5 Bouftou-Wolle",
     "tool": "inv.sell", "args": {"item": "Bouftou-Wolle", "quantity": 5}},
    {"id": "de-c06", "lang": "de", "goal": "sag der Gruppe: gleich da",
     "tool": "chat.say", "args": {"text": "gleich da", "channel": "team"}},
]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Second fresh THX-01 test set")
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
    print(f"THX-01 confirm - {totals['cases']} cases in {elapsed:.1f}s")
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
