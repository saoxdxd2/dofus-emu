"""Fresh held-out test set for the THX-01 policy.

This set was written after the dev set in ``ai/thx_holdout.py`` and must not be
used for tuning. It uses new phrasing, entities and word orders across the four
target languages. Run from ``dofus-network``::

    python ai/thx_test.py --show-failures
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
    {"id": "en-t01", "lang": "en", "goal": "cancel everything right now",
     "tool": "safe.stop", "args": {}},
    {"id": "en-t02", "lang": "en", "goal": "travel to [-3, 18]",
     "tool": "nav.goto", "args": {"x": -3, "y": 18}},
    {"id": "en-t03", "lang": "en", "goal": "zaap to Astrub",
     "tool": "nav.goto", "args": {"zaap": "Astrub"}},
    {"id": "en-t04", "lang": "en", "goal": "find quests around Bonta for level 30",
     "tool": "quest.list", "args": {"area": "Bonta", "level_min": 30}},
    {"id": "en-t05", "lang": "en", "goal": "what's the market price for Bouftou Wool?",
     "tool": "hdv.scan", "args": {"item": "Bouftou Wool"}},
    {"id": "en-t06", "lang": "en", "goal": "sell 4 Bouftou Wool",
     "tool": "inv.sell", "args": {"item": "Bouftou Wool", "quantity": 4}},
    {"id": "en-t07", "lang": "en", "goal": "send a message to the team: ready",
     "tool": "chat.say", "args": {"text": "ready", "channel": "team"}},
    {"id": "en-t08", "lang": "en", "goal": "what is a Gobball?",
     "tool": "knowledge.search", "args": {"query": "Gobball"}},
    {"id": "en-t09", "lang": "en", "goal": "put 1 Bouftou Wool up for 750 kamas",
     "tool": "hdv.list",
     "args": {"item": "Bouftou Wool", "quantity": 1, "price_rule": "fixed", "price": 750}},
    {"id": "en-t10", "lang": "en", "goal": "start a farm loop on Kanigs",
     "tool": "farm.start", "args": {"monster": "Kanigs"}},
    {"id": "en-t11", "lang": "en", "goal": "enter combat",
     "tool": "fight.join", "args": {}},
    {"id": "en-t12", "lang": "en", "goal": "end my turn",
     "tool": "fight.pass", "args": {}},
    {"id": "en-t13", "lang": "en", "goal": "show me my inventory",
     "tool": "inv.list", "args": {}},
    {"id": "en-t14", "lang": "en", "goal": "report my position",
     "tool": "state.snapshot", "args": {}},
    {"id": "en-t15", "lang": "en", "goal": "come with me",
     "tool": "nav.follow", "args": {}},
    {"id": "en-t16", "lang": "en", "goal": "continue quest 1696",
     "tool": "quest.advance", "args": {"quest_id": 1696}},

    {"id": "fr-t01", "lang": "fr", "goal": "annule tout maintenant",
     "tool": "safe.stop", "args": {}},
    {"id": "fr-t02", "lang": "fr", "goal": "va aux coordonnées [-6, 3]",
     "tool": "nav.goto", "args": {"x": -6, "y": 3}},
    {"id": "fr-t03", "lang": "fr", "goal": "zaap vers Astrub",
     "tool": "nav.goto", "args": {"zaap": "Astrub"}},
    {"id": "fr-t04", "lang": "fr", "goal": "trouve des quêtes à Bonta niveau 25",
     "tool": "quest.list", "args": {"area": "Bonta", "level_min": 25}},
    {"id": "fr-t05", "lang": "fr", "goal": "quel est le prix de la Laine de Bouftou",
     "tool": "hdv.scan", "args": {"item": "Laine de Bouftou"}},
    {"id": "fr-t06", "lang": "fr", "goal": "vends 3 Laines de Bouftou",
     "tool": "inv.sell", "args": {"item": "Laine de Bouftou", "quantity": 3}},
    {"id": "fr-t07", "lang": "fr", "goal": "envoie un message à l'équipe : salut",
     "tool": "chat.say", "args": {"text": "salut", "channel": "team"}},
    {"id": "fr-t08", "lang": "fr", "goal": "qu'est-ce qu'un Bouftou ?",
     "tool": "knowledge.search", "args": {"query": "Bouftou"}},

    {"id": "es-t01", "lang": "es", "goal": "cancela todo ahora",
     "tool": "safe.stop", "args": {}},
    {"id": "es-t02", "lang": "es", "goal": "ve a las coordenadas [-2, 7]",
     "tool": "nav.goto", "args": {"x": -2, "y": 7}},
    {"id": "es-t03", "lang": "es", "goal": "zaap a Astrub",
     "tool": "nav.goto", "args": {"zaap": "Astrub"}},
    {"id": "es-t04", "lang": "es", "goal": "busca misiones en Bonta de nivel 35",
     "tool": "quest.list", "args": {"area": "Bonta", "level_min": 35}},
    {"id": "es-t05", "lang": "es", "goal": "cuál es el precio de la Lana de Bouftou",
     "tool": "hdv.scan", "args": {"item": "Lana de Bouftou"}},
    {"id": "es-t06", "lang": "es", "goal": "vende 4 Lanas de Bouftou",
     "tool": "inv.sell", "args": {"item": "Lana de Bouftou", "quantity": 4}},
    {"id": "es-t07", "lang": "es", "goal": "envía un mensaje al equipo: hola",
     "tool": "chat.say", "args": {"text": "hola", "channel": "team"}},
    {"id": "es-t08", "lang": "es", "goal": "¿qué es un Bouftou?",
     "tool": "knowledge.search", "args": {"query": "Bouftou"}},
    {"id": "es-t09", "lang": "es", "goal": "comienza a farmear Kanigs",
     "tool": "farm.start", "args": {"monster": "Kanigs"}},
    {"id": "es-t10", "lang": "es", "goal": "termina mi turno",
     "tool": "fight.pass", "args": {}},

    {"id": "de-t01", "lang": "de", "goal": "brich alles ab",
     "tool": "safe.stop", "args": {}},
    {"id": "de-t02", "lang": "de", "goal": "gehe zu den Koordinaten [4, -2]",
     "tool": "nav.goto", "args": {"x": 4, "y": -2}},
    {"id": "de-t03", "lang": "de", "goal": "zaap nach Astrub",
     "tool": "nav.goto", "args": {"zaap": "Astrub"}},
    {"id": "de-t04", "lang": "de", "goal": "finde Quests in Bonta für Stufe 45",
     "tool": "quest.list", "args": {"area": "Bonta", "level_min": 45}},
    {"id": "de-t05", "lang": "de", "goal": "wie viel kostet Bouftou-Wolle",
     "tool": "hdv.scan", "args": {"item": "Bouftou-Wolle"}},
    {"id": "de-t06", "lang": "de", "goal": "verkaufe 2 Bouftou-Wolle",
     "tool": "inv.sell", "args": {"item": "Bouftou-Wolle", "quantity": 2}},
    {"id": "de-t07", "lang": "de", "goal": "schicke eine Nachricht an das Team: hallo",
     "tool": "chat.say", "args": {"text": "hallo", "channel": "team"}},
    {"id": "de-t08", "lang": "de", "goal": "was ist ein Bouftou",
     "tool": "knowledge.search", "args": {"query": "Bouftou"}},
    {"id": "de-t09", "lang": "de", "goal": "beginne mit dem Farmen von Kanigs",
     "tool": "farm.start", "args": {"monster": "Kanigs"}},
    {"id": "de-t10", "lang": "de", "goal": "beende meinen Zug",
     "tool": "fight.pass", "args": {}},
]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Fresh THX-01 policy test set")
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
    print(f"THX-01 test - {totals['cases']} cases in {elapsed:.1f}s")
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
