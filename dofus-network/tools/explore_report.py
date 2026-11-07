"""Read the randomized arm: does taking the planner's second objective pay?

The exploration arm (`agent/explore.py`) records, on every state it was offered,
which arm was taken and the exact propensity `p_explore` it used. That makes the
comparison here a plain randomized one, not an observational guess:

  * the difference in outcome rate between the explore and teacher arms among the
    randomized rows, with a CI that resamples *segments* (actions inside one run are
    not independent);
  * the propensity audit - the observed explore share must match the mean
    `p_explore`, and a state with no second plan must carry `p_explore == 0`;
  * a balance check - the two arms must look alike on objective type and map reach,
    because the coin is the only thing that assigned them;
  * the inverse-propensity value of the "always explore" policy on the same rows.

With no randomized rows yet it prints the pre-registered plan instead: how many
eligible steps each epsilon needs before the difference is measured to a given
half-width. The arithmetic uses the corpus's own base rate, so the plan is priced in
the units the run will actually produce.

    python tools/explore_report.py
    python tools/explore_report.py --data data/transitions_v2 --json
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

OUTCOMES = ("advance", "progress", "moved")


def load_rows(paths: Sequence[Path]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in paths:
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    return rows


def randomized(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rows the arm actually randomized: an alternative existed and the coin ran."""
    out = []
    for row in rows:
        if row.get("was_read") or not row.get("has_pair"):
            continue
        p = row.get("p_explore")
        if isinstance(p, (int, float)) and 0.0 < float(p) < 1.0 and row.get("arm"):
            out.append(row)
    return out


def rate(rows: List[Dict[str, Any]], key: str) -> Optional[float]:
    if not rows:
        return None
    return sum(1 for row in rows if row.get(key)) / len(rows)


def difference(a: List[Dict[str, Any]], b: List[Dict[str, Any]],
               key: str) -> Optional[float]:
    pa, pb = rate(a, key), rate(b, key)
    if pa is None or pb is None:
        return None
    return pa - pb


def cluster_bootstrap(rows: List[Dict[str, Any]], key: str, rounds: int = 400,
                      seed: int = 0) -> Optional[Tuple[float, float]]:
    by_seg: Dict[Any, List[Dict[str, Any]]] = {}
    for row in rows:
        by_seg.setdefault(row.get("segment"), []).append(row)
    groups = list(by_seg.values())
    if len(groups) < 3:
        return None
    rng = random.Random(seed)
    vals: List[float] = []
    for _ in range(rounds):
        picked = [rng.choice(groups) for _ in groups]
        sample = [row for group in picked for row in group]
        delta = difference([r for r in sample if r.get("arm") == "explore"],
                           [r for r in sample if r.get("arm") == "teacher"], key)
        if delta is not None:
            vals.append(delta)
    if len(vals) < 30:
        return None
    vals.sort()
    return (round(vals[int(0.025 * len(vals))], 4),
            round(vals[int(0.975 * len(vals)) - 1], 4))


def always_explore_value(rows: List[Dict[str, Any]], key: str) -> Optional[float]:
    """Inverse-propensity value of taking the alternative at every eligible state.

    w = 1/p for explore rows, 0 for teacher rows: the standard Horvitz-Thompson
    estimator with the propensities the policy actually used.
    """
    total = 0.0
    weight = 0.0
    for row in rows:
        p = row.get("p_explore")
        if not isinstance(p, (int, float)) or not 0.0 < float(p) < 1.0:
            continue
        if row.get("arm") != "explore":
            continue
        w = 1.0 / float(p)
        total += w * (1.0 if row.get(key) else 0.0)
        weight += w
    if not weight:
        return None
    return total / weight


def balance(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    explore = [r for r in rows if r.get("arm") == "explore"]
    teacher = [r for r in rows if r.get("arm") == "teacher"]
    out: Dict[str, Any] = {}
    for name, key in (("objective_type", "objective_type"),
                      ("map", lambda r: (r.get("context") or {}).get("map"))):
        pick = key if callable(key) else (lambda r, k=key: r.get(k))
        a = {str(pick(r)) for r in explore}
        b = {str(pick(r)) for r in teacher}
        if not a or not b:
            continue
        out[name] = {"explore_levels": len(a), "teacher_levels": len(b),
                     "shared": len(a & b)}
    return out


def plan_table(base_rate: float, targets: Sequence[float] = (0.005, 0.01, 0.02),
               epsilons: Sequence[float] = (0.05, 0.1, 0.2, 0.5),
               step_s: float = 2.0) -> Dict[str, Any]:
    """Eligible steps each epsilon needs for a two-sided 95% half-width target.

    The difference of two independent proportions has variance
    p(1-p)(1/n_e + 1/n_t); with the explore arm a fraction eps of the eligible
    steps, n_e = eps*n and n_t = (1-eps)*n, so n = p(1-p)(1/eps + 1/(1-eps))/h^2
    for a half-width h (z=1.96). Reported per epsilon and target.
    """
    out: Dict[str, Any] = {}
    for eps in epsilons:
        row: Dict[str, Any] = {}
        for target in targets:
            n = (1.96 ** 2) * base_rate * (1 - base_rate) * (
                1.0 / eps + 1.0 / (1.0 - eps)) / (target ** 2)
            row["half_width_%.3f" % target] = {
                "eligible_steps": int(math.ceil(n)),
                "explored_rows": int(math.ceil(n * eps)),
                "hours": round(n * step_s / 3600.0, 1),
            }
        out["epsilon_%.2f" % eps] = row
    return out


def measured_step_s(data: Path, default: float = 2.0) -> float:
    """The corpus's own decision cadence, from the manifest the builder wrote."""
    path = data / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    value = manifest.get("mean_step_s")
    return float(value) if isinstance(value, (int, float)) and value > 0 else default


def analyse(data: Path, step_s: Optional[float] = None) -> Dict[str, Any]:
    paths = [data / ("%s.jsonl" % name) for name in ("train", "val", "test")]
    rows = load_rows(paths)
    randomized_rows = randomized(rows)
    cadence = float(step_s) if step_s else measured_step_s(data)
    result: Dict[str, Any] = {"data": str(data), "rows": len(rows),
                              "randomized_rows": len(randomized_rows),
                              "step_s": cadence, "base_rates": {}}
    actions = [r for r in rows if not r.get("was_read") and r.get("has_pair")]
    for key in OUTCOMES:
        result["base_rates"][key] = round(rate(actions, key) or 0.0, 5)
    if not randomized_rows:
        result["status"] = "no randomized rows yet"
        result["plan"] = plan_table(result["base_rates"]["progress"] or 0.008,
                                    step_s=cadence)
        return result
    explore = [r for r in randomized_rows if r.get("arm") == "explore"]
    teacher = [r for r in randomized_rows if r.get("arm") == "teacher"]
    props = [float(r["p_explore"]) for r in randomized_rows]
    result["arms"] = {"explore": len(explore), "teacher": len(teacher)}
    result["propensity"] = {
        "explore_share": round(len(explore) / len(randomized_rows), 4),
        "mean_p_explore": round(sum(props) / len(props), 4),
        "segments": len({r.get("segment") for r in randomized_rows}),
    }
    result["balance"] = balance(randomized_rows)
    result["outcomes"] = {}
    for key in OUTCOMES:
        delta = difference(explore, teacher, key)
        entry = {
            "explore_rate": round(rate(explore, key) or 0.0, 5),
            "teacher_rate": round(rate(teacher, key) or 0.0, 5),
            "delta": None if delta is None else round(delta, 5),
            "delta_ci95": cluster_bootstrap(randomized_rows, key),
            "always_explore_value": _round(always_explore_value(randomized_rows, key)),
        }
        result["outcomes"][key] = entry
    result["status"] = "measured" if len(randomized_rows) >= 200 else (
        "too few randomized rows to read")
    return result


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 5)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=REPO_ROOT / "data" / "transitions_v2")
    ap.add_argument("--step-s", type=float, default=None,
                    help="seconds per decision (default: the manifest's mean_step_s)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    result = analyse(args.data, args.step_s)
    if args.json:
        print(json.dumps(result, indent=1))
        return 0
    print("data %s  rows %d  randomized %d" % (result["data"], result["rows"],
                                               result["randomized_rows"]))
    print("status: %s" % result["status"])
    if result["status"] == "no randomized rows yet":
        print("pre-registered plan (primary metric: progress per action, base %.4f, "
              "%.1f s per decision):"
              % (result["base_rates"]["progress"], result["step_s"]))
        for eps, rows_needed in result["plan"].items():
            print("  %-12s %s" % (eps, rows_needed))
        return 0
    prop = result["propensity"]
    print("propensity: explore share %.3f vs mean p %.3f over %d segments"
          % (prop["explore_share"], prop["mean_p_explore"], prop["segments"]))
    print("balance: %s" % result["balance"])
    for key, entry in result["outcomes"].items():
        print("  %-9s explore %-7s teacher %-7s delta %-8s CI %-16s constant-policy %s"
              % (key, entry["explore_rate"], entry["teacher_rate"], entry["delta"],
                 str(entry["delta_ci95"]), entry["always_explore_value"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
