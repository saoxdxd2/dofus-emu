"""Where do call errors actually come from? Error budget + solvability audit.

Why this exists
---------------
``call accuracy`` mixes two very different things: cases the model got wrong,
and cases where the gold label is not recoverable from the input. Measured on
run v8 (``val``, 3,290 cases): call accuracy is 52.4% overall and **74.2% on the
solvable subset**, because 978 cases (29.7%) carry an argument value that appears
nowhere in the goal text and nowhere in any learnable table:

* ``inv.sell`` 744 cases where ``quantity`` is absent from the goal. In fr/de val
  the dominant frame is ``vends Cadeau de Compensation`` with gold ``quantity: 20``
  and the quantity is uniform over {1,5,10,20,50} -- 152/140/140/132/123 of 718.
  660 of those item names never appear in train. Zero signal.
* ``quest.advance`` 234 cases where ``quest_id`` is absent from the goal. The
  corpus pairs 2,937 quest names with 2,126 ids, 1,546 of them unique.

A plan that targets "raise call accuracy" without separating these two is
targeting a number that cannot move.

Usage::

    python -m ai.diag_call_budget --eval kaggle_outputs/encoder30/eval_val.json
    python -m ai.diag_call_budget --eval ... --split val --postprocess

``--eval`` is any report produced by ``ai/bench_encoder`` (it reads the
``predictions`` list). The corpus rows are joined by id from
``data/core_sft_v4/<split>.jsonl``.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai.data_splits import bounded_match, load_jsonl, normalize  # noqa: E402

SFT_DIR = REPO_ROOT / "data" / "core_sft_v4"


# ---------------------------------------------------------------------------
# solvability
# ---------------------------------------------------------------------------

def _numbers(text: str) -> List[int]:
    # The sign must be kept: ``déplace-toi à [-12, 4]`` labels x=-12, and a
    # bare ``\\d+`` would report it as an invented number. 113 nav.goto rows
    # were false positives for exactly this reason.
    return [int(n) for n in re.findall(r"-?\d+", text or "")]


# Fields whose *value* is deliberately not spelled out in the goal: the goal
# renders them through a per-language word map or an enum phrase, so a literal
# containment test would report a false positive. Their value is still learnable
# -- it is a function of the rendered word -- so they are exempt from the
# solvability test. Defined once in agent.schema so this audit and the corpus
# builder cannot drift apart.
_TRANSLATED_FIELDS = None  # populated from agent.schema.DERIVED_FIELDS on first use


def _derived_fields() -> frozenset:
    global _TRANSLATED_FIELDS
    if _TRANSLATED_FIELDS is None:
        try:
            from agent.schema import DERIVED_FIELDS
            _TRANSLATED_FIELDS = frozenset(DERIVED_FIELDS)
        except Exception:
            _TRANSLATED_FIELDS = frozenset({"channel", "kind", "price_rule", "lang"})
    return _TRANSLATED_FIELDS


def unsolvable_reason(row: Dict[str, Any]) -> Optional[str]:
    """Return why this case cannot be learned from its input, or ``None``.

    A case is unsolvable when a label is **present** in the args but its value is
    recoverable from nothing the model can see. An *absent* optional field is not
    a defect -- it is the canonical form, and ``validate_call`` re-applies the
    schema default at execution time.

    Two checks, applied to every tool rather than a hand-picked few:

    * **numeric** -- the value must appear as a number in the goal text. This is
      what caught ``inv.sell``/``hdv.list`` quantity (744 of the 978 original
      unsolvable val cases) and ``quest.advance`` quest_id (234).
    * **string** -- the value must occur in the goal at a word boundary, or be a
      surface form the gazetteer can resolve from the goal. A string label the
      goal never mentions is a hallucination request; ``safe.stop``'s bare
      templates ("arrête tout") used to emit an invented ``reason`` and only
      surfaced once this check was generalised.

    Checking every field is the point: the original version tested two fields of
    two tools and therefore could not see a third unsolvable family at all.
    """
    goal = str(row.get("goal") or "")
    args = row.get("args") or {}
    tool = row.get("tool")
    # Canonicalise first: a field sitting at its schema default carries no
    # information, so it is not a label and must not be tested. Without this,
    # ``min_stock: 1`` (the default) reads as an invented number on 226 rows.
    try:
        from agent.schema import canonicalize_args
        args = canonicalize_args(tool, args)
    except Exception:
        pass
    nums = _numbers(goal)
    folded = normalize(goal)
    for key, value in args.items():
        if key in _derived_fields():
            continue
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, int):
            if value not in nums:
                return f"{key}_not_in_goal"
            continue
        if isinstance(value, str):
            if not value:
                continue
            if bounded_match(folded, normalize(value)):
                continue
            # Not spelled out verbatim. An entity reference is still solvable
            # when the gazetteer can resolve a surface form from the goal.
            from ai.gazetteer import Gazetteer
            gaz = _gazetteer()
            if gaz is not None and gaz.lookup_name(key, goal):
                continue
            return f"{key}_not_in_goal"
        if isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str) and item and not bounded_match(folded, normalize(item)):
                    return f"{key}_not_in_goal"
    return None


_GAZ: List[Any] = []


def _gazetteer() -> Any:
    if not _GAZ:
        try:
            from ai.gazetteer import Gazetteer
            _GAZ.append(Gazetteer())
        except Exception:
            _GAZ.append(None)
    return _GAZ[0]


    start = haystack.find(needle)
    while start != -1:
        end = start + len(needle)
        before_ok = start == 0 or not haystack[start - 1].isalnum()
        after_ok = end >= len(haystack) or not haystack[end].isalnum()
        if before_ok and after_ok:
            return True
        start = haystack.find(needle, start + 1)
    return False


def failure_class(expected: Dict[str, Any], got: Dict[str, Any]) -> str:
    """Classify a wrong call: missing / extra / both / wrong value."""
    missing = sorted(k for k in expected if got.get(k) in (None, "", []))
    extra = sorted(k for k in got if k not in expected)
    if missing and not extra:
        return "missing:" + ",".join(missing)
    if extra and not missing:
        return "extra:" + ",".join(extra)
    if missing and extra:
        return "both:" + ",".join(missing) + "|" + ",".join(extra)
    wrong = sorted(k for k in expected if normalize(expected[k]) != normalize(got.get(k)))
    return "wrong_value:" + ",".join(wrong) if wrong else "unclassified"


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------

def audit(predictions: List[Dict[str, Any]], rows: Dict[str, Dict[str, Any]],
          postprocess: bool = False) -> Dict[str, Any]:
    """Score predictions under **both** comparators.

    ``strict`` is raw exact-dict equality. ``canonical`` is the release gate
    (``agent.tool_eval.score_one``): both sides canonicalised, and text arguments
    accepted on containment either way. The gap between them is how much of the
    headline number is comparator leniency rather than real agreement -- measured
    on v8 it is worth about 2 pp, and it is what lets a truncated span
    ("Eklat de Kroktail la Desalt" for "...Desalterante") score as correct.
    Reporting only the lenient number is how a per-tool regression stays hidden.
    """
    from ai.postprocess import clean_call
    from agent.tool_eval import score_one

    cases = collections.Counter()
    tool_ok = collections.Counter()
    call_ok = collections.Counter()
    call_canon = collections.Counter()
    classes: Dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    unsolvable = collections.Counter()
    total = ok_all = ok_canon = ok_solv = ok_solv_canon = solv = n_uns = 0

    for pred in predictions:
        row = rows.get(str(pred.get("id")))
        if row is None:
            continue
        tool = row["tool"]
        cases[tool] += 1
        total += 1
        predicted = pred.get("predicted")
        got = dict(predicted.get("args") or {}) if predicted else {}
        if postprocess and predicted:
            got = clean_call(tool, got, str(row.get("goal") or ""), None)
        good = bool(predicted and predicted.get("tool") == tool and got == (row.get("args") or {}))
        # Score the prediction's OWN tool, never the gold one: substituting the
        # gold tool here would count a wrong-tool call with lucky arguments as
        # correct, which inflated the gate number by 48 cases (1.5 pp).
        canon = bool(predicted and score_one(row, {"tool": predicted.get("tool"),
                                                   "args": got})[1])
        ok_all += good
        ok_canon += canon

        reason = unsolvable_reason(row)
        if reason:
            n_uns += 1
            unsolvable[reason] += 1
        else:
            solv += 1
            ok_solv += good
            ok_solv_canon += canon

        if predicted is None:
            classes[tool]["no_prediction"] += 1
            continue
        if predicted.get("tool") != tool:
            classes[tool]["wrong_tool"] += 1
            continue
        tool_ok[tool] += 1
        expected = row.get("args") or {}
        if got == expected:
            call_ok[tool] += 1
        elif canon:
            call_canon[tool] += 1
        else:
            classes[tool][failure_class(expected, got)] += 1

    return {
        "cases": cases, "tool_ok": tool_ok, "call_ok": call_ok,
        "call_canon": call_canon, "classes": classes,
        "total": total, "ok_all": ok_all, "ok_canon": ok_canon,
        "ok_solv": ok_solv, "ok_solv_canon": ok_solv_canon, "solvable": solv,
        "unsolvable": unsolvable, "n_unsolvable": n_uns,
    }


def report(a: Dict[str, Any], postprocess: bool) -> str:
    out: List[str] = []
    total = max(a["total"], 1)
    out.append(f"cases {a['total']}   tool {sum(a['tool_ok'].values())}/{a['total']} "
               f"= {sum(a['tool_ok'].values()) / total:.4f}")
    out.append(f"call  {a['ok_all']}/{a['total']} = {a['ok_all'] / total:.4f}   (all cases, strict)")
    out.append(f"call  {a['ok_canon']}/{a['total']} = {a['ok_canon'] / total:.4f}   "
               f"(all cases, release gate)")
    if a["solvable"]:
        out.append(f"call  {a['ok_solv']}/{a['solvable']} = {a['ok_solv'] / a['solvable']:.4f}"
                   f"   (solvable subset, strict)")
        out.append(f"call  {a['ok_solv_canon']}/{a['solvable']} = {a['ok_solv_canon'] / a['solvable']:.4f}"
                   f"   (solvable subset, release gate)")
    out.append(f"leniency (gate - strict) {a['ok_canon'] / total - a['ok_all'] / total:+.4f}")
    out.append(f"unsolvable by construction {a['n_unsolvable']}/{a['total']} "
               f"= {a['n_unsolvable'] / total:.1%}  {dict(a['unsolvable'])}")
    out.append("")
    out.append(f"{'tool':18s} {'cases':>6s} {'tool%':>6s} {'strict':>7s} {'gate':>7s}   dominant failure classes")
    for tool, count in a["cases"].most_common():
        dom = "  ".join(f"{k}x{v}" for k, v in a["classes"][tool].most_common(3))
        out.append(f"{tool:18s} {count:6d} {a['tool_ok'][tool] / count:6.3f} "
                   f"{a['call_ok'][tool] / count:7.3f} "
                   f"{(a['call_ok'][tool] + a['call_canon'][tool]) / count:7.3f}   {dom}")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--eval", required=True, help="bench_encoder report with a 'predictions' list")
    ap.add_argument("--split", default="val", help="corpus split to join against")
    ap.add_argument("--corpus", default=None,
                    help="corpus directory the predictions were produced on "
                         "(default: data/core_sft_v4). Joining against a "
                         "different corpus than the one scored silently "
                         "mismatches ids and produces meaningless numbers.")
    ap.add_argument("--postprocess", action="store_true",
                    help="apply ai.postprocess.clean_call before scoring")
    args = ap.parse_args(argv)

    report_blob = json.loads(Path(args.eval).read_text(encoding="utf-8"))
    predictions = report_blob.get("predictions") or []
    if not predictions:
        print(f"no 'predictions' in {args.eval}", file=sys.stderr)
        return 2
    sft_dir = Path(args.corpus) if args.corpus else SFT_DIR
    rows = {str(r["id"]): r for r in load_jsonl(sft_dir / f"{args.split}.jsonl")}
    audit_result = audit(predictions, rows, postprocess=args.postprocess)
    print(f"corpus: {sft_dir}   joined {audit_result['total']}/{len(predictions)} predictions")
    print(report(audit_result, args.postprocess))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
