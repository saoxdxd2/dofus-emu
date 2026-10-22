"""Tool-use benchmark — golden goals in EN/FR/ES/DE -> expected tool call.

The cases are held out from the SFT corpus by construction (they live here, not
in ``episodes.py``). Two scores matter:

* **tool accuracy** — did the policy pick the right tool at all;
* **call accuracy** — right tool *and* every expected argument correct.

Argument matching is semantic where free text is involved: accents/case are
ignored and, for text arguments, containment either way counts ("la Laine de
Bouftou" contains "Laine de Bouftou"). Numeric and boolean arguments are exact.

Run the rule-policy baseline (from ``dofus-network``)::

    python agent/tool_eval.py

Score a model's predictions instead — one JSON object per line::

    {"id": "en-06", "tool": "quest.list", "args": {"area": "Astrub"}}
    python agent/tool_eval.py --predictions preds.jsonl --show-failures
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.schema import TOOLS_BY_NAME, canonicalize_args  # noqa: E402

_TEXT_ARGS = {
    "query", "zaap", "npc", "name", "item", "area", "monster", "resource",
    "zone", "target", "text", "reason",
}


def _norm(text: Any) -> str:
    if not isinstance(text, str):
        text = str(text)
    folded = unicodedata.normalize("NFKD", text.casefold())
    stripped = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return " ".join(stripped.split()).strip(" .,!?:;\"'")


def _match_arg(key: str, expected: Any, actual: Any) -> bool:
    if isinstance(expected, bool):
        return isinstance(actual, bool) and actual is expected
    if isinstance(expected, int):
        return isinstance(actual, int) and not isinstance(actual, bool) and actual == expected
    if isinstance(expected, str):
        wanted, got = _norm(expected), _norm(actual)
        if not wanted or not got:
            return wanted == got
        if wanted == got:
            return True
        return key in _TEXT_ARGS and (wanted in got or got in wanted)
    return expected == actual


# ---------------------------------------------------------------------------
# golden cases — one per line of real player intent, all four languages
# ---------------------------------------------------------------------------

GOLD: List[Dict[str, Any]] = [
    # -- English ------------------------------------------------------------
    {"id": "en-01", "lang": "en", "goal": "stop everything now",
     "tool": "safe.stop", "args": {}},
    {"id": "en-02", "lang": "en", "goal": "follow me",
     "tool": "nav.follow", "args": {}},
    {"id": "en-03", "lang": "en", "goal": "pass the turn",
     "tool": "fight.pass", "args": {}},
    {"id": "en-04", "lang": "en", "goal": "go to [12, 8]",
     "tool": "nav.goto", "args": {"x": 12, "y": 8}},
    {"id": "en-05", "lang": "en", "goal": "zaap Astrub",
     "tool": "nav.goto", "args": {"zaap": "Astrub"}},
    {"id": "en-06", "lang": "en", "goal": "list quests in Astrub",
     "tool": "quest.list", "args": {"area": "Astrub"}},
    {"id": "en-07", "lang": "en", "goal": "quests for level 40 in Bonta",
     "tool": "quest.list", "args": {"area": "Bonta", "level_min": 40}},
    {"id": "en-08", "lang": "en", "goal": "dungeon quests in Amakna",
     "tool": "quest.list", "args": {"area": "Amakna", "dungeon_only": True}},
    {"id": "en-09", "lang": "en", "goal": "what is a Bouftou?",
     "tool": "knowledge.search", "args": {"query": "Bouftou"}},
    {"id": "en-10", "lang": "en", "goal": "farm kanigs for loot",
     "tool": "farm.start", "args": {"monster": "kanigs"}},
    {"id": "en-11", "lang": "en", "goal": "stop the farm loop",
     "tool": "farm.stop", "args": {}},
    {"id": "en-12", "lang": "en", "goal": "join the fight",
     "tool": "fight.join", "args": {}},
    {"id": "en-13", "lang": "en", "goal": "sell all the resources in my inventory",
     "tool": "inv.sell", "args": {"all": True}},
    {"id": "en-14", "lang": "en", "goal": "sell the Bouftou Wool",
     "tool": "inv.sell", "args": {"item": "Bouftou Wool"}},
    {"id": "en-15", "lang": "en", "goal": "scan the price of Bouftou Wool",
     "tool": "hdv.scan", "args": {"item": "Bouftou Wool"}},
    {"id": "en-16", "lang": "en", "goal": "list my Gold Ingot on the market for 500 kamas",
     "tool": "hdv.list",
     "args": {"item": "Gold Ingot", "price_rule": "fixed", "price": 500}},
    {"id": "en-17", "lang": "en", "goal": "advance quest 1696",
     "tool": "quest.advance", "args": {"quest_id": 1696}},
    {"id": "en-18", "lang": "en", "goal": "say hello in team chat",
     "tool": "chat.say", "args": {"text": "hello", "channel": "team"}},
    {"id": "en-19", "lang": "en", "goal": "where am I?",
     "tool": "state.snapshot", "args": {}},
    {"id": "en-20", "lang": "en", "goal": "show my inventory",
     "tool": "inv.list", "args": {}},

    # -- French -------------------------------------------------------------
    {"id": "fr-01", "lang": "fr", "goal": "arrête tout",
     "tool": "safe.stop", "args": {}},
    {"id": "fr-02", "lang": "fr", "goal": "suis-moi",
     "tool": "nav.follow", "args": {}},
    {"id": "fr-03", "lang": "fr", "goal": "passe le tour",
     "tool": "fight.pass", "args": {}},
    {"id": "fr-04", "lang": "fr", "goal": "va en [-3, 12]",
     "tool": "nav.goto", "args": {"x": -3, "y": 12}},
    {"id": "fr-05", "lang": "fr", "goal": "zaap Astrub",
     "tool": "nav.goto", "args": {"zaap": "Astrub"}},
    {"id": "fr-06", "lang": "fr", "goal": "liste les quêtes à Astrub",
     "tool": "quest.list", "args": {"area": "Astrub"}},
    {"id": "fr-07", "lang": "fr", "goal": "quêtes de niveau 60 à Bonta",
     "tool": "quest.list", "args": {"area": "Bonta", "level_min": 60}},
    {"id": "fr-08", "lang": "fr", "goal": "cherche Bouftou",
     "tool": "knowledge.search", "args": {"query": "Bouftou"}},
    {"id": "fr-09", "lang": "fr", "goal": "farm les kanigs",
     "tool": "farm.start", "args": {"monster": "kanigs"}},
    {"id": "fr-10", "lang": "fr", "goal": "rejoins le combat",
     "tool": "fight.join", "args": {}},
    {"id": "fr-11", "lang": "fr", "goal": "vends tout mon inventaire",
     "tool": "inv.sell", "args": {"all": True}},
    {"id": "fr-12", "lang": "fr", "goal": "scan le prix de la Laine de Bouftou",
     "tool": "hdv.scan", "args": {"item": "Laine de Bouftou"}},
    {"id": "fr-13", "lang": "fr", "goal": "avance la quête 1696",
     "tool": "quest.advance", "args": {"quest_id": 1696}},
    {"id": "fr-14", "lang": "fr", "goal": "dis bonjour dans le chat d'équipe",
     "tool": "chat.say", "args": {"text": "bonjour", "channel": "team"}},
    {"id": "fr-15", "lang": "fr", "goal": "où suis-je ?",
     "tool": "state.snapshot", "args": {}},
    {"id": "fr-16", "lang": "fr", "goal": "montre mon inventaire",
     "tool": "inv.list", "args": {}},
    {"id": "fr-17", "lang": "fr", "goal": "quelles quêtes puis-je faire à Amakna ?",
     "tool": "quest.list", "args": {"area": "Amakna"}},
    {"id": "fr-18", "lang": "fr", "goal": "arrête la boucle de farm",
     "tool": "farm.stop", "args": {}},

    # -- Spanish ------------------------------------------------------------
    {"id": "es-01", "lang": "es", "goal": "para todo",
     "tool": "safe.stop", "args": {}},
    {"id": "es-02", "lang": "es", "goal": "sígueme",
     "tool": "nav.follow", "args": {}},
    {"id": "es-03", "lang": "es", "goal": "pasa el turno",
     "tool": "fight.pass", "args": {}},
    {"id": "es-04", "lang": "es", "goal": "ve a [7, -4]",
     "tool": "nav.goto", "args": {"x": 7, "y": -4}},
    {"id": "es-05", "lang": "es", "goal": "zaap Astrub",
     "tool": "nav.goto", "args": {"zaap": "Astrub"}},
    {"id": "es-06", "lang": "es", "goal": "lista las misiones de Astrub",
     "tool": "quest.list", "args": {"area": "Astrub"}},
    {"id": "es-07", "lang": "es", "goal": "misiones de nivel 30 en Bonta",
     "tool": "quest.list", "args": {"area": "Bonta", "level_min": 30}},
    {"id": "es-08", "lang": "es", "goal": "busca Bouftou",
     "tool": "knowledge.search", "args": {"query": "Bouftou"}},
    {"id": "es-09", "lang": "es", "goal": "farmea kanigs",
     "tool": "farm.start", "args": {"monster": "kanigs"}},
    {"id": "es-10", "lang": "es", "goal": "únete al combate",
     "tool": "fight.join", "args": {}},
    {"id": "es-11", "lang": "es", "goal": "vende todo el inventario",
     "tool": "inv.sell", "args": {"all": True}},
    {"id": "es-12", "lang": "es", "goal": "escanea el precio de la Lana de Bouftou",
     "tool": "hdv.scan", "args": {"item": "Lana de Bouftou"}},
    {"id": "es-13", "lang": "es", "goal": "avanza la misión 1696",
     "tool": "quest.advance", "args": {"quest_id": 1696}},
    {"id": "es-14", "lang": "es", "goal": "di hola en el chat de equipo",
     "tool": "chat.say", "args": {"text": "hola", "channel": "team"}},
    {"id": "es-15", "lang": "es", "goal": "¿dónde estoy?",
     "tool": "state.snapshot", "args": {}},
    {"id": "es-16", "lang": "es", "goal": "muestra mi inventario",
     "tool": "inv.list", "args": {}},
    {"id": "es-17", "lang": "es", "goal": "misiones de mazmorra en Amakna",
     "tool": "quest.list", "args": {"area": "Amakna", "dungeon_only": True}},

    # -- German -------------------------------------------------------------
    {"id": "de-01", "lang": "de", "goal": "halt alles",
     "tool": "safe.stop", "args": {}},
    {"id": "de-02", "lang": "de", "goal": "folge mir",
     "tool": "nav.follow", "args": {}},
    {"id": "de-03", "lang": "de", "goal": "passe den Zug",
     "tool": "fight.pass", "args": {}},
    {"id": "de-04", "lang": "de", "goal": "geh zu [7, -4]",
     "tool": "nav.goto", "args": {"x": 7, "y": -4}},
    {"id": "de-05", "lang": "de", "goal": "zaap Astrub",
     "tool": "nav.goto", "args": {"zaap": "Astrub"}},
    {"id": "de-06", "lang": "de", "goal": "liste die Quests in Astrub",
     "tool": "quest.list", "args": {"area": "Astrub"}},
    {"id": "de-07", "lang": "de", "goal": "Quests für Level 80 in Bonta",
     "tool": "quest.list", "args": {"area": "Bonta", "level_min": 80}},
    {"id": "de-08", "lang": "de", "goal": "suche Bouftou",
     "tool": "knowledge.search", "args": {"query": "Bouftou"}},
    {"id": "de-09", "lang": "de", "goal": "farme Kanigs",
     "tool": "farm.start", "args": {"monster": "kanigs"}},
    {"id": "de-10", "lang": "de", "goal": "Kampf beitreten",
     "tool": "fight.join", "args": {}},
    {"id": "de-11", "lang": "de", "goal": "verkaufe das ganze Inventar",
     "tool": "inv.sell", "args": {"all": True}},
    {"id": "de-12", "lang": "de", "goal": "scanne den Preis von Bouftou-Wolle",
     "tool": "hdv.scan", "args": {"item": "Bouftou-Wolle"}},
    {"id": "de-13", "lang": "de", "goal": "avanciere Quest 1696",
     "tool": "quest.advance", "args": {"quest_id": 1696}},
    {"id": "de-14", "lang": "de", "goal": "sag Hallo im Teamchat",
     "tool": "chat.say", "args": {"text": "Hallo", "channel": "team"}},
    {"id": "de-15", "lang": "de", "goal": "wo bin ich?",
     "tool": "state.snapshot", "args": {}},
    {"id": "de-16", "lang": "de", "goal": "zeig mein Inventar",
     "tool": "inv.list", "args": {}},
]

GOLD_BY_ID = {case["id"]: case for case in GOLD}


def score_one(case: Dict[str, Any], prediction: Optional[Dict[str, Any]]) -> Tuple[bool, bool]:
    """Score one case.

    Both sides are canonicalised first (``schema.canonicalize_args``): optional
    arguments sitting at their schema default carry no information, so emitting
    them or omitting them must not change the score. Without this, a correct
    ``{"item": "X"}`` loses to a lucky ``{"item": "X", "all": false,
    "min_stock": 1, "price_rule": "median_minus_1"}``.
    """
    if not isinstance(prediction, dict):
        return False, False
    tool = prediction.get("tool")
    if tool != case["tool"]:
        return False, False
    expected = canonicalize_args(case["tool"], case.get("args") or {})
    actual = canonicalize_args(case["tool"], prediction.get("args") or {})
    if not isinstance(actual, dict):
        return True, not expected
    for key, wanted in expected.items():
        if not _match_arg(key, wanted, actual.get(key)):
            return True, False
    return True, True


def evaluate(
    predictions: Dict[str, Dict[str, Any]],
    cases: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    by_lang: Dict[str, Counter] = {}
    by_tool: Dict[str, Counter] = {}
    failures: List[Dict[str, Any]] = []
    totals: Counter = Counter()
    for case in (GOLD if cases is None else cases):
        prediction = predictions.get(case["id"])
        tool_ok, call_ok = score_one(case, prediction)
        lang = case["lang"]
        tool_name = case["tool"]
        for bucket, key in ((by_lang, lang), (by_tool, tool_name)):
            stats = bucket.setdefault(key, Counter())
            stats["cases"] += 1
            stats["tool_ok"] += int(tool_ok)
            stats["call_ok"] += int(call_ok)
        totals["cases"] += 1
        totals["tool_ok"] += int(tool_ok)
        totals["call_ok"] += int(call_ok)
        if not call_ok:
            failures.append({
                "id": case["id"],
                "lang": case["lang"],
                "goal": case["goal"],
                "expected": {"tool": case["tool"], "args": case.get("args") or {}},
                "predicted": prediction,
                "prediction": prediction,
                "tool_ok": tool_ok,
            })
    return {
        "totals": dict(totals),
        "by_lang": {key: dict(value) for key, value in sorted(by_lang.items())},
        "by_tool": {key: dict(value) for key, value in sorted(by_tool.items())},
        "failures": failures,
    }


def rule_predictions(cases: Sequence[Dict[str, Any]] = GOLD) -> Dict[str, Dict[str, Any]]:
    from agent.runtime import RulePolicy

    policy = RulePolicy()
    predictions: Dict[str, Dict[str, Any]] = {}
    for case in cases:
        call = policy.choose(case["goal"])
        predictions[case["id"]] = (
            {"tool": call.tool, "args": call.args} if call is not None else None)
    return predictions


def load_predictions(path: Path) -> Dict[str, Dict[str, Any]]:
    predictions: Dict[str, Dict[str, Any]] = {}
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            case_id = row.get("id")
            if not case_id:
                continue
            if isinstance(row.get("call"), dict):
                predictions[case_id] = row["call"]
            elif row.get("tool"):
                predictions[case_id] = {"tool": row["tool"], "args": row.get("args") or {}}
            else:
                predictions[case_id] = None
    return predictions


def _ratio(part: int, whole: int) -> str:
    return f"{part}/{whole} ({100.0 * part / whole:.1f}%)" if whole else "0/0"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Tool-use benchmark over golden goals")
    parser.add_argument("--predictions", default=None,
                        help="JSONL predictions; omit to run the rule-policy baseline")
    parser.add_argument("--out", default=None, help="write the full report as JSON")
    parser.add_argument("--show-failures", action="store_true")
    parser.add_argument("--min-tool-accuracy", type=float, default=None,
                        help="exit 1 when tool accuracy is below this fraction")
    args = parser.parse_args(argv)

    if args.predictions:
        predictions = load_predictions(Path(args.predictions))
        label = f"predictions {args.predictions}"
    else:
        predictions = rule_predictions()
        label = "rule policy baseline"

    report = evaluate(predictions)
    report["policy"] = label
    totals = report["totals"]
    print(f"tool-use benchmark — {label}")
    print(f"  cases        : {totals['cases']}")
    print(f"  tool accuracy: {_ratio(totals['tool_ok'], totals['cases'])}")
    print(f"  call accuracy: {_ratio(totals['call_ok'], totals['cases'])}")
    for lang, stats in report["by_lang"].items():
        print(f"  [{lang}] tool {_ratio(stats['tool_ok'], stats['cases'])}  "
              f"call {_ratio(stats['call_ok'], stats['cases'])}")
    print("  by tool (tool/call):")
    for tool, stats in report["by_tool"].items():
        print(f"    {tool:<17} {stats['tool_ok']}/{stats['cases']}  "
              f"{stats['call_ok']}/{stats['cases']}")

    if args.show_failures:
        for failure in report["failures"]:
            predicted = failure["predicted"]
            print(f"  MISS {failure['id']}: {failure['goal']}")
            print(f"       expected {failure['expected']}")
            print(f"       predicted {predicted}")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  report       : {args.out}")

    if args.min_tool_accuracy is not None and totals["cases"]:
        accuracy = totals["tool_ok"] / totals["cases"]
        if accuracy < args.min_tool_accuracy:
            print(f"  FAIL: tool accuracy {accuracy:.3f} < {args.min_tool_accuracy:.3f}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
