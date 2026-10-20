"""Held-out paraphrases for the THX-01 policy.

These goals do not appear in ``agent/tool_eval.py`` or in the SFT corpus. They
exercise the same 16 tools with new phrasing, connectors and languages, plus
quantity/price arguments. Run from ``dofus-network``::

    python ai/thx_holdout.py --show-failures
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
    {"id": "en-h01", "lang": "en", "goal": "halt the bot immediately",
     "tool": "safe.stop", "args": {}},
    {"id": "en-h02", "lang": "en", "goal": "walk to [-7, 22]",
     "tool": "nav.goto", "args": {"x": -7, "y": 22}},
    {"id": "en-h03", "lang": "en", "goal": "take the zaap to Bonta",
     "tool": "nav.goto", "args": {"zaap": "Bonta"}},
    {"id": "en-h04", "lang": "en", "goal": "which quests are available around Amakna?",
     "tool": "quest.list", "args": {"area": "Amakna"}},
    {"id": "en-h05", "lang": "en", "goal": "how much does Gobball Wool sell for?",
     "tool": "hdv.scan", "args": {"item": "Gobball Wool"}},
    {"id": "en-h06", "lang": "en", "goal": "sell 2 Gold Ingots",
     "tool": "inv.sell", "args": {"item": "Gold Ingot", "quantity": 2}},
    {"id": "en-h07", "lang": "en", "goal": "tell the team: ready",
     "tool": "chat.say", "args": {"text": "ready", "channel": "team"}},
    {"id": "en-h08", "lang": "en", "goal": "who is Gobeefy?",
     "tool": "knowledge.search", "args": {"query": "Gobeefy"}},
    {"id": "en-h09", "lang": "en", "goal": "put 2 Gobball Wool on sale for 600 kamas",
     "tool": "hdv.list",
     "args": {"item": "Gobball Wool", "quantity": 2, "price_rule": "fixed", "price": 600}},
    {"id": "en-h10", "lang": "en", "goal": "farm boars",
     "tool": "farm.start", "args": {"monster": "boars"}},
    {"id": "en-h11", "lang": "en", "goal": "join the battle",
     "tool": "fight.join", "args": {}},
    {"id": "en-h12", "lang": "en", "goal": "skip my turn",
     "tool": "fight.pass", "args": {}},
    {"id": "en-h13", "lang": "en", "goal": "what am I carrying?",
     "tool": "inv.list", "args": {}},
    {"id": "en-h14", "lang": "en", "goal": "where am I right now?",
     "tool": "state.snapshot", "args": {}},
    {"id": "en-h15", "lang": "en", "goal": "follow my character",
     "tool": "nav.follow", "args": {}},
    {"id": "en-h16", "lang": "en", "goal": "do the next step of quest 2001",
     "tool": "quest.advance", "args": {"quest_id": 2001}},

    {"id": "fr-h01", "lang": "fr", "goal": "coupe tout",
     "tool": "safe.stop", "args": {}},
    {"id": "fr-h02", "lang": "fr", "goal": "prends le zaap de Bonta",
     "tool": "nav.goto", "args": {"zaap": "Bonta"}},
    {"id": "fr-h03", "lang": "fr", "goal": "quêtes disponibles vers Astrub",
     "tool": "quest.list", "args": {"area": "Astrub"}},
    {"id": "fr-h04", "lang": "fr", "goal": "combien vaut la Laine de Bouftou ?",
     "tool": "hdv.scan", "args": {"item": "Laine de Bouftou"}},
    {"id": "fr-h05", "lang": "fr", "goal": "mets en vente 4 Lingots d'Or à 800 kamas",
     "tool": "hdv.list",
     "args": {"item": "Lingot d'Or", "quantity": 4, "price_rule": "fixed", "price": 800}},
    {"id": "fr-h06", "lang": "fr", "goal": "dis à l'équipe : prêt",
     "tool": "chat.say", "args": {"text": "prêt", "channel": "team"}},
    {"id": "fr-h07", "lang": "fr", "goal": "qui est Bouftou ?",
     "tool": "knowledge.search", "args": {"query": "Bouftou"}},
    {"id": "fr-h08", "lang": "fr", "goal": "va en [-5, 9]",
     "tool": "nav.goto", "args": {"x": -5, "y": 9}},

    {"id": "es-h01", "lang": "es", "goal": "detén todo ya",
     "tool": "safe.stop", "args": {}},
    {"id": "es-h02", "lang": "es", "goal": "camina hasta [2, -6]",
     "tool": "nav.goto", "args": {"x": 2, "y": -6}},
    {"id": "es-h03", "lang": "es", "goal": "toma el zaap de Bonta",
     "tool": "nav.goto", "args": {"zaap": "Bonta"}},
    {"id": "es-h04", "lang": "es", "goal": "qué misiones hay cerca de Astrub",
     "tool": "quest.list", "args": {"area": "Astrub"}},
    {"id": "es-h05", "lang": "es", "goal": "cuánto cuesta la Lana de Bouftou",
     "tool": "hdv.scan", "args": {"item": "Lana de Bouftou"}},
    {"id": "es-h06", "lang": "es", "goal": "pon 3 Lingotes de oro a la venta por 900 kamas",
     "tool": "hdv.list",
     "args": {"item": "Lingote de oro", "quantity": 3, "price_rule": "fixed", "price": 900}},
    {"id": "es-h07", "lang": "es", "goal": "di al equipo: listo",
     "tool": "chat.say", "args": {"text": "listo", "channel": "team"}},
    {"id": "es-h08", "lang": "es", "goal": "qué es un Bouftou",
     "tool": "knowledge.search", "args": {"query": "Bouftou"}},

    {"id": "de-h01", "lang": "de", "goal": "stoppe alles sofort",
     "tool": "safe.stop", "args": {}},
    {"id": "de-h02", "lang": "de", "goal": "gehe zu [-8, 14]",
     "tool": "nav.goto", "args": {"x": -8, "y": 14}},
    {"id": "de-h03", "lang": "de", "goal": "nimm den Zaap nach Bonta",
     "tool": "nav.goto", "args": {"zaap": "Bonta"}},
    {"id": "de-h04", "lang": "de", "goal": "welche Quests gibt es in Amakna",
     "tool": "quest.list", "args": {"area": "Amakna"}},
    {"id": "de-h05", "lang": "de", "goal": "was kostet Fresssackwolle",
     "tool": "hdv.scan", "args": {"item": "Fresssackwolle"}},
    {"id": "de-h06", "lang": "de", "goal": "verkaufe 2 Goldbarren für 700 Kamas",
     "tool": "inv.sell",
     "args": {"item": "Goldbarren", "quantity": 2, "price_rule": "fixed", "price": 700}},
    {"id": "de-h07", "lang": "de", "goal": "sag dem Team: bereit",
     "tool": "chat.say", "args": {"text": "bereit", "channel": "team"}},
    {"id": "de-h08", "lang": "de", "goal": "was ist ein Bouftou",
     "tool": "knowledge.search", "args": {"query": "Bouftou"}},
]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Held-out THX-01 policy benchmark")
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
    print(f"THX-01 held-out - {totals['cases']} cases in {elapsed:.1f}s")
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
