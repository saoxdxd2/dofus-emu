"""Third fresh test set for the THX-01 policy.

Written after the fixes validated by ``ai/thx_test2.py`` and not used for
tuning. It stresses new synonyms and sentence shapes. Run from
``dofus-network``::

    python ai/thx_test3.py --show-failures
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
    {"id": "en-f01", "lang": "en", "goal": "freeze all activity",
     "tool": "safe.stop", "args": {}},
    {"id": "en-f02", "lang": "en", "goal": "move to [6, 10]",
     "tool": "nav.goto", "args": {"x": 6, "y": 10}},
    {"id": "en-f03", "lang": "en", "goal": "show quests near Amakna",
     "tool": "quest.list", "args": {"area": "Amakna"}},
    {"id": "en-f04", "lang": "en", "goal": "how much is Bouftou Wool worth?",
     "tool": "hdv.scan", "args": {"item": "Bouftou Wool"}},
    {"id": "en-f05", "lang": "en", "goal": "sell 7 Bouftou Wool",
     "tool": "inv.sell", "args": {"item": "Bouftou Wool", "quantity": 7}},
    {"id": "en-f06", "lang": "en", "goal": "post 2 Bouftou Wool for 1000 kamas",
     "tool": "hdv.list",
     "args": {"item": "Bouftou Wool", "quantity": 2, "price_rule": "fixed", "price": 1000}},
    {"id": "en-f07", "lang": "en", "goal": "inform the team: ready",
     "tool": "chat.say", "args": {"text": "ready", "channel": "team"}},
    {"id": "en-f08", "lang": "en", "goal": "what can you tell me about Kanigs?",
     "tool": "knowledge.search", "args": {"query": "Kanigs"}},
    {"id": "en-f09", "lang": "en", "goal": "start hunting Boars",
     "tool": "farm.start", "args": {"monster": "Boars"}},
    {"id": "en-f10", "lang": "en", "goal": "join the current battle",
     "tool": "fight.join", "args": {}},
    {"id": "en-f11", "lang": "en", "goal": "skip this turn",
     "tool": "fight.pass", "args": {}},
    {"id": "en-f12", "lang": "en", "goal": "what's in my bag?",
     "tool": "inv.list", "args": {}},
    {"id": "en-f13", "lang": "en", "goal": "where is my character?",
     "tool": "state.snapshot", "args": {}},
    {"id": "en-f14", "lang": "en", "goal": "stick with me",
     "tool": "nav.follow", "args": {}},
    {"id": "en-f15", "lang": "en", "goal": "progress quest 1696",
     "tool": "quest.advance", "args": {"quest_id": 1696}},

    {"id": "fr-f01", "lang": "fr", "goal": "gèle toutes les actions",
     "tool": "safe.stop", "args": {}},
    {"id": "fr-f02", "lang": "fr", "goal": "rends-toi en [8, -3]",
     "tool": "nav.goto", "args": {"x": 8, "y": -3}},
    {"id": "fr-f03", "lang": "fr", "goal": "quêtes près d'Amakna",
     "tool": "quest.list", "args": {"area": "Amakna"}},
    {"id": "fr-f04", "lang": "fr", "goal": "combien vaut la Laine de Bouftou",
     "tool": "hdv.scan", "args": {"item": "Laine de Bouftou"}},
    {"id": "fr-f05", "lang": "fr", "goal": "vends 7 Laines de Bouftou",
     "tool": "inv.sell", "args": {"item": "Laine de Bouftou", "quantity": 7}},
    {"id": "fr-f06", "lang": "fr", "goal": "informe l'équipe : prêt",
     "tool": "chat.say", "args": {"text": "prêt", "channel": "team"}},

    {"id": "es-f01", "lang": "es", "goal": "congela toda actividad",
     "tool": "safe.stop", "args": {}},
    {"id": "es-f02", "lang": "es", "goal": "ve a [8, -3]",
     "tool": "nav.goto", "args": {"x": 8, "y": -3}},
    {"id": "es-f03", "lang": "es", "goal": "misiones cerca de Amakna",
     "tool": "quest.list", "args": {"area": "Amakna"}},
    {"id": "es-f04", "lang": "es", "goal": "cuánto vale la Lana de Bouftou",
     "tool": "hdv.scan", "args": {"item": "Lana de Bouftou"}},
    {"id": "es-f05", "lang": "es", "goal": "vende 7 Lanas de Bouftou",
     "tool": "inv.sell", "args": {"item": "Lana de Bouftou", "quantity": 7}},
    {"id": "es-f06", "lang": "es", "goal": "informa al equipo: listo",
     "tool": "chat.say", "args": {"text": "listo", "channel": "team"}},

    {"id": "de-f01", "lang": "de", "goal": "friere alle aktionen ein",
     "tool": "safe.stop", "args": {}},
    {"id": "de-f02", "lang": "de", "goal": "gehe zu [8, -3]",
     "tool": "nav.goto", "args": {"x": 8, "y": -3}},
    {"id": "de-f03", "lang": "de", "goal": "Quests in der Nähe von Amakna",
     "tool": "quest.list", "args": {"area": "Amakna"}},
    {"id": "de-f04", "lang": "de", "goal": "was ist Bouftou-Wolle wert",
     "tool": "hdv.scan", "args": {"item": "Bouftou-Wolle"}},
    {"id": "de-f05", "lang": "de", "goal": "verkaufe 7 Bouftou-Wolle",
     "tool": "inv.sell", "args": {"item": "Bouftou-Wolle", "quantity": 7}},
    {"id": "de-f06", "lang": "de", "goal": "informiere das Team: bereit",
     "tool": "chat.say", "args": {"text": "bereit", "channel": "team"}},
]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Third fresh THX-01 test set")
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
    print(f"THX-01 final - {totals['cases']} cases in {elapsed:.1f}s")
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
