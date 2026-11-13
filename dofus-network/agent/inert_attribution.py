"""Cross-layer attribution of inert actions in the real transition corpus.

Why this exists
---------------
Several reviews quote **"86% of actions are inert"** as evidence that the agent is
not intelligent. That number is stale, and more importantly it is a single number for
what is at least five different failures living in five different layers. Telling the
operator "the AI is bad" is not actionable; telling them "24.7% of inert rows are a
planner argument error that appears on 0% of effective rows" is.

This module attributes every inert action to the **most specific observed failure**
(exclusive, sums to 100%), measures each signal's **differential** lift against rows
that did work (a signal that appears on both sides is not a cause), stratifies by
chronological era, and computes the upper bound of what a pre-action veto could buy.

Measured on the shipped corpus (2026-11-13, 5,484 actionable rows)
-------------------------------------------------------------------
The inert rate is not one number, it is a time series. Four chronological quarters:

    era     inert     moved   progress
    1/4     65.9%     34.1%      1.7%
    2/4     79.1%     20.9%      1.4%
    3/4     88.6%     11.4%      1.5%   <- the "86%" in the reviews came from here
    4/4     39.5%     60.5%      4.0%
    all     68.3%     31.7%      2.2%

The newest 1,100 rows are **35.5%** inert. `nav.goto` started working (G127); the
reviews quoted a bad *era* as a permanent property of the agent. Any figure quoted
without its era is wrong.

Signals, inert vs effective rows (a cause has to be *differential*):

    signal              inert    effective   risk ratio   verdict
    refused             24.7%      0.0%        inf        cause (planner/client)
    error               25.4%      0.0%        inf        cause (dispatch/client)
    repeat_streak > 0   79.1%     15.8%        5.02x      pre-action risk factor
    miss_streak > 0     77.4%     41.5%        1.87x      weak pre-action factor
    obs_age > 2 s        2.2%      1.0%        2.24x      real but tiny
    obs_source=carried  95.7%     98.9%        0.97x      NOT a cause
    claim_met = False   97.0%     22.9%        4.23x      mostly the outcome itself

The last two are the trap. "Observations were carried from the previous step" sounds
like a sensing bug and is on 96% of inert rows -- but it is on **99%** of rows that
worked. It is how the harness always works. `claim_met` is derived from the same
`changed` keys as `moved`, so treating it as a cause is circular. Neither is used
as a layer here.

Exclusive first-failure attribution on the 3,745 inert rows:

    policy:repeat (no observed failure)   64.9%   (2431)
    unexplained                            9.2%   ( 344)
    decision:known_kick                    8.4%   ( 313)
    client:dialog                          6.3%   ( 236)
    client:other_refusal                   3.6%   ( 136)
    decision:missing_arg                   3.3%   ( 124)
    decision:nav_args                      1.8%   (  68)
    decision:not_adjacent                  1.3%   (  47)
    dispatch:no_client                     0.7%   (  25)
    sensing:stale                          0.5%   (  18)

The actionable result is a **pre-action** veto built on `repeat_streak` alone, not on
`miss_streak` and not on a blanket rule:

    repeat only      3,236 flagged -> 2,962 inert cut,  274 effective cost, 91.5% precision
    repeat or miss   3,982 flagged -> 3,212 inert cut,  770 effective cost, 80.7% precision

Adding `miss_streak` buys 250 more inert removals at a cost of 496 effective actions.
`miss_streak > 0` is on 584 of the 884 effective `quest.advance` rows: it mostly
marks "this quest step is still in progress", not "this action is going to fail".
Per tool, `repeat` alone reaches 93.3% precision on `nav.goto` (1,317 inert cut vs
94 effective cost) and 90.7% on `quest.advance`. These are observational upper
bounds, not interventions: a vetoed action would be replaced by another decision,
not by doing nothing. The number is reported as a bound for exactly that reason.

What this is NOT
----------------
It is not a causal estimate in the do-calculus sense. There is no randomisation and
no counterfactual rollout. Layers are assigned by observed failure precedence, and
the veto figures are upper bounds under the (false but stated) assumption that the
replacement action does nothing. The one genuinely causal-safe statement is the
*differential* one: `refused` and `error` occur on zero effective rows, so they are
sufficient conditions for an inert action in this corpus.

Usage (from ``dofus-network``)::

    python -m agent.inert_attribution                  # full report
    python -m agent.inert_attribution --json          # machine-readable
    python -m agent.inert_attribution --eras 5 --out data/inert_attribution.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

DEFAULT_DATA = Path("data") / "transitions"
STALE_MS = 2000.0
MIN_VETO_SUPPORT = 50

# Precedence: a concrete observed failure beats a pre-action risk factor, because it
# says *where* the loss happened. Within that, the planner's argument errors come
# before client-internal failures, which come before dispatch failures.
LAYERS = (
    "decision:known_kick",
    "decision:missing_arg",
    "decision:nav_args",
    "decision:not_adjacent",
    "client:dialog",
    "client:other_refusal",
    "dispatch:no_client",
    "dispatch:failed",
    "client:other_error",
    "policy:repeat",
    "sensing:stale",
    "unexplained",
)

# Pre-action signals: knowable *before* the action is issued, so a veto can use them.
PRE_ACTION = ("repeat", "miss", "veto", "stale")


# ---------------------------------------------------------------------------
# coercion: the corpus is written by several code paths and occasionally stores
# numbers as strings and the `gate` field as either a dict or a repr'd string.
# ---------------------------------------------------------------------------


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _gate(row: Dict[str, Any]) -> Dict[str, Any]:
    gate = row.get("gate")
    return gate if isinstance(gate, dict) else {}


def is_actionable(row: Dict[str, Any]) -> bool:
    """Only paired, non-read rows are decisions. `was_read` rows are sensing, not acts."""
    return bool(row.get("has_pair")) and not bool(row.get("was_read")) and bool(row.get("x"))


def is_inert(row: Dict[str, Any]) -> bool:
    """Neither the world nor a quest counter changed."""
    return not bool(row.get("moved")) and not bool(row.get("progress"))


def signals(row: Dict[str, Any]) -> Dict[str, bool]:
    """All observable failure signals, including the ones that are NOT causes."""
    refused = _text(row.get("refused"))
    error = _text(row.get("error"))
    return {
        "refused": bool(refused),
        "error": bool(error),
        "not_ok": row.get("ok") is False,
        "repeat": _num(row.get("repeat_streak")) > 0,
        "miss": _num(row.get("miss_streak")) > 0,
        "veto": bool(_gate(row).get("would_veto")),
        "stale": _num(row.get("obs_age_ms")) > STALE_MS,
        # recorded for the specificity table, deliberately excluded from LAYERS
        "carried": row.get("obs_source") == "carried",
        "claim_unmet": bool(row.get("claim")) and not bool(row.get("claim_met")),
    }


def first_failure(row: Dict[str, Any]) -> str:
    """The most specific observed failure, else the strongest pre-action risk factor.

    `refused` and `error` are usually the *same* event written twice; this function
    deduplicates them by reading `refused` first and only falling back to `error`.
    """
    refused = _text(row.get("refused")).lower()
    error = _text(row.get("error")).lower()
    text = refused or error
    if text:
        if "known_kick_signature" in text:
            return "decision:known_kick"
        if "npc_id_required" in text:
            return "decision:missing_arg"
        if "needs one of" in text or "pass slot" in text:
            return "decision:nav_args"
        if "not adjacent" in text:
            return "decision:not_adjacent"
        if "dialog" in text:
            return "client:dialog"
        if "no client instance is running" in text:
            return "dispatch:no_client"
        if refused:
            return "client:other_refusal"
        return "client:other_error"
    s = signals(row)
    if row.get("ok") is False:
        return "dispatch:failed"
    if s["repeat"] or s["miss"] or s["veto"]:
        return "policy:repeat"
    if s["stale"]:
        return "sensing:stale"
    return "unexplained"


# ---------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------


def risk_ratio(a_hits: int, a_n: int, b_hits: int, b_n: int) -> Dict[str, Any]:
    """P(signal | inert) / P(signal | effective) with a Wald CI on log RR.

    A Haldane-Anscombe 0.5 correction keeps the interval finite when a signal never
    appears on effective rows -- which is exactly the case for `refused` and `error`.
    The reported point estimate is left as ``None`` (infinite) in that case rather
    than being silently turned into a large finite number.
    """
    if a_n <= 0 or b_n <= 0:
        return {"inert": None, "effective": None, "lift_pp": None, "ratio": None}
    pa, pb = a_hits / a_n, b_hits / b_n
    out: Dict[str, Any] = {
        "inert": round(pa, 4),
        "effective": round(pb, 4),
        "lift_pp": round(100.0 * (pa - pb), 2),
        "ratio": round(pa / pb, 3) if pb > 0 else None,
    }
    ca, cb = a_hits + 0.5, b_hits + 0.5
    na, nb = a_n + 1.0, b_n + 1.0
    ra, rb = ca / na, cb / nb
    se = math.sqrt(1.0 / ca - 1.0 / na + 1.0 / cb - 1.0 / nb)
    lo, hi = math.exp(math.log(ra / rb) - 1.96 * se), math.exp(math.log(ra / rb) + 1.96 * se)
    out["corrected_ratio"] = round(ra / rb, 3)
    out["corrected_ci95"] = [round(lo, 3), round(hi, 3)]
    return out


def _ordered(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(rows, key=lambda r: _num(r.get("at")))


def load_rows(data_dir: Path = DEFAULT_DATA) -> List[Dict[str, Any]]:
    """All rows from the three chronological splits, in `at` order."""
    rows: List[Dict[str, Any]] = []
    for name in ("train", "val", "test"):
        path = data_dir / ("%s.jsonl" % name)
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    return _ordered(rows)


def _era_slices(n: int, eras: int) -> List[Tuple[str, slice]]:
    if n <= 0 or eras <= 1:
        return [("all", slice(0, n))]
    out = []
    size = n / float(eras)
    for i in range(eras):
        lo, hi = int(i * size), int((i + 1) * size)
        if i == eras - 1:
            hi = n
        if hi > lo:
            out.append(("era %d/%d" % (i + 1, eras), slice(lo, hi)))
    return out


def attribute(rows: Iterable[Dict[str, Any]], eras: int = 4) -> Dict[str, Any]:
    """Full attribution report over an iterable of transition rows."""
    act = _ordered([r for r in rows if is_actionable(r)])
    inert = [r for r in act if is_inert(r)]
    effective = [r for r in act if not is_inert(r)]
    if not act:
        return {"rows": 0, "actionable": 0, "inert": 0, "inert_rate": None}

    exclusive = Counter(first_failure(r) for r in inert)
    sig_names = ("refused", "error", "not_ok", "repeat", "miss", "veto", "stale",
                 "carried", "claim_unmet")
    specificity = {}
    for name in sig_names:
        a = sum(1 for r in inert if signals(r)[name])
        b = sum(1 for r in effective if signals(r)[name])
        specificity[name] = risk_ratio(a, len(inert), b, len(effective))

    per_era = []
    for label, sl in _era_slices(len(act), eras):
        part = act[sl]
        n_inert = sum(1 for r in part if is_inert(r))
        layers = Counter(first_failure(r) for r in part if is_inert(r))
        per_era.append({
            "era": label,
            "rows": len(part),
            "inert": n_inert,
            "inert_rate": round(n_inert / len(part), 4),
            "moved_rate": round(sum(1 for r in part if r.get("moved")) / len(part), 4),
            "progress_rate": round(sum(1 for r in part if r.get("progress")) / len(part), 4),
            "layers": {k: layers.get(k, 0) for k in LAYERS if layers.get(k, 0)},
            "at_from": _num(part[0].get("at")) if part else None,
            "at_to": _num(part[-1].get("at")) if part else None,
        })

    by_tool = []
    tools: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in act:
        tools[_text(r.get("tool"))].append(r)
    for tool, part in sorted(tools.items(), key=lambda kv: -len(kv[1])):
        n_inert = sum(1 for r in part if is_inert(r))
        by_tool.append({
            "tool": tool,
            "rows": len(part),
            "inert": n_inert,
            "inert_rate": round(n_inert / len(part), 4),
            "top_layer": Counter(first_failure(r) for r in part if is_inert(r)).most_common(1)[0][0]
            if n_inert else None,
        })

    return {
        "rows": len(act),
        "actionable": len(act),
        "inert": len(inert),
        "effective": len(effective),
        "inert_rate": round(len(inert) / len(act), 4),
        "exclusive": {
            "counts": {k: exclusive.get(k, 0) for k in LAYERS},
            "shares": {k: round(exclusive.get(k, 0) / len(inert), 4)
                       for k in LAYERS} if inert else {},
        },
        "specificity": specificity,
        "eras": per_era,
        "by_tool": by_tool,
        "veto_candidates": veto_candidates(act),
        "caveat": ("observational: layers are observed-failure precedence, and veto "
                   "yields are upper bounds assuming a vetoed action is replaced by "
                   "doing nothing. No intervention was run."),
    }


def veto_candidates(rows: Sequence[Dict[str, Any]],
                    min_support: int = MIN_VETO_SUPPORT) -> List[Dict[str, Any]]:
    """Per-tool precision of a `repeat_streak > 0` veto, measured before the action.

    A global repeat veto looks attractive (79.1% of inert rows) and is a trap: the
    same signal is on 15.8% of effective rows, concentrated in `quest.advance`.
    Stratifying by tool is what makes the estimate usable.
    """
    groups: Dict[str, List[int]] = defaultdict(list)  # tool -> list of (repeat, inert)
    for r in rows:
        if not is_actionable(r):
            continue
        groups[_text(r.get("tool"))].append(
            (1 if signals(r)["repeat"] else 0, 1 if is_inert(r) else 0))
    out = []
    for tool, pairs in groups.items():
        flagged = [p for p in pairs if p[0]]
        if len(flagged) < min_support:
            continue
        inert_hit = sum(p[1] for p in flagged)
        effective_hit = len(flagged) - inert_hit
        total_inert = sum(p[1] for p in pairs)
        out.append({
            "tool": tool,
            "flagged": len(flagged),
            "inert_removed_upper_bound": inert_hit,
            "effective_cost_upper_bound": effective_hit,
            "precision": round(inert_hit / len(flagged), 4),
            "recall_of_inert": round(inert_hit / total_inert, 4) if total_inert else None,
            "net_upper_bound": inert_hit - effective_hit,
        })
    return sorted(out, key=lambda d: -d["net_upper_bound"])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_report(rep: Dict[str, Any]) -> None:
    if not rep.get("actionable"):
        print("no actionable rows")
        return
    print("actionable %d   inert %d (%.1f%%)   effective %d" % (
        rep["actionable"], rep["inert"], 100 * rep["inert_rate"], rep["effective"]))
    print("\nera            rows   inert   moved  progress")
    for e in rep["eras"]:
        print("%-13s %5d  %5.1f%%  %5.1f%%    %5.1f%%" % (
            e["era"], e["rows"], 100 * e["inert_rate"],
            100 * e["moved_rate"], 100 * e["progress_rate"]))
    print("\nexclusive first failure (shares sum to 100%)")
    for k, v in sorted(rep["exclusive"]["counts"].items(), key=lambda kv: -kv[1]):
        if v:
            print("  %-24s %5d  %5.1f%%" % (k, v, 100 * rep["exclusive"]["shares"][k]))
    print("\nsignal               inert   effective   lift_pp   risk ratio   cause?")
    for name, s in rep["specificity"].items():
        ratio = "inf" if s["ratio"] is None else "%.2fx" % s["ratio"]
        verdict = "NOT differential" if (s["corrected_ratio"] or 0) < 1.2 else "yes"
        if name == "claim_unmet":
            verdict = "outcome, circular"
        print("  %-18s %6.1f%%     %6.1f%%   %+7.1f  %10s   %s" % (
            name, 100 * (s["inert"] or 0), 100 * (s["effective"] or 0),
            s["lift_pp"] or 0, ratio, verdict))
    print("\ntool-conditioned repeat veto (upper bounds, not an intervention)")
    print("  %-18s %8s %10s %10s %9s" % ("tool", "flagged", "inert_cut", "eff_cost", "precision"))
    for v in rep["veto_candidates"][:8]:
        print("  %-18s %8d %10d %10d %8.1f%%" % (
            v["tool"], v["flagged"], v["inert_removed_upper_bound"],
            v["effective_cost_upper_bound"], 100 * v["precision"]))
    print("\n%s" % rep["caveat"])


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA)
    ap.add_argument("--eras", type=int, default=4)
    ap.add_argument("--min-support", type=int, default=MIN_VETO_SUPPORT)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    rows = load_rows(args.data)
    rep = attribute(rows, eras=args.eras)
    if args.min_support != MIN_VETO_SUPPORT:
        rep["veto_candidates"] = veto_candidates(rows, min_support=args.min_support)
    if args.json:
        from agent.jsonio import utf8_console
        utf8_console()
        print(json.dumps(rep, indent=1, ensure_ascii=False))
    else:
        _print_report(rep)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")
        print("wrote %s" % args.out, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
