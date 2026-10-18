"""Production-grade benchmarking: base THX-01 vs fine-tuned THX-01 on Dofus tasks.

Runs both models on the same Dofus decision suite and produces:
  - accuracy per question type (combat_action, item_lookup, map_coordinate, ...)
  - latency per request (target: ~10ms single, ~1ms batched)
  - side-by-side comparison report in benchmarks/comparison_report.json
"""

from __future__ import annotations

import argparse
import statistics
import time
from pathlib import Path
from typing import Any

BENCH_DIR = Path("benchmarks")
BENCH_DIR.mkdir(parents=True, exist_ok=True)

try:
    import thx01
except ImportError:
    thx01 = None


DOFUS_BENCHMARK_SUITE: list[dict[str, Any]] = [
    {
        "id": "dofus_combat_001",
        "state": "Combat turn 3. Iop vs Goblin. Iop has 120/150 PV, 6/6 PA, 3/3 PM. Goblin at distance 2. Available spells: Coup d'Epee (1 PA, 15-20 dmg), Pulsation (2 PA, 30 dmg, AoE).",
        "questions": {
            "combat_action": {
                "type": "choice",
                "question": "What combat action should the character take next?",
                "criteria": {
                    "attack": "Use an offensive spell or weapon",
                    "defend": "Use a defensive or healing action",
                    "move": "Change position on the map",
                    "item": "Use an item from inventory",
                    "pass": "Skip turn / wait",
                },
            },
        },
        "expected": {"combat_action": "attack"},
    },
    {
        "id": "dofus_item_001",
        "state": "Inventory: Dofus Eliatrope (ID 14287), Pain Cacahuète (ID 11245), Potion de Soins (ID 287). Player needs to heal 80 PV.",
        "questions": {
            "item_id_extraction": {
                "type": "number",
                "question": "Extract the numeric item ID for Potion de Soins.",
                "criteria": {"min": 0, "max": 999999, "precision": 0},
            },
        },
        "expected": {"item_id_extraction": 287},
    },
    {
        "id": "dofus_map_001",
        "state": "Player is at Astrub [-22, 35]. Needs to travel to Bonta [-15, 28].",
        "questions": {
            "map_coordinate": {
                "type": "number",
                "question": "What is the X coordinate of Bonta?",
                "criteria": {"min": -100, "max": 100, "precision": 0},
            },
        },
        "expected": {"map_coordinate": -15},
    },
    {
        "id": "dofus_npc_001",
        "state": "NPC Baker in Astrub offers: 'Buy bread', 'Quest: Lost Wheat', 'Goodbye'. Player is hungry (30/100 hunger).",
        "questions": {
            "npc_dialogue": {
                "type": "choice",
                "question": "Which dialogue option should the player choose?",
                "criteria": {
                    "Buy bread": "Purchase food to restore hunger",
                    "Quest: Lost Wheat": "Accept a quest",
                    "Goodbye": "End conversation",
                },
            },
        },
        "expected": {"npc_dialogue": "Buy bread"},
    },
    {
        "id": "dofus_spell_001",
        "state": "Iop casts Pulsation. AoE 3 cells around target. Damage 30.",
        "questions": {
            "spell_name_excerpt": {
                "type": "excerpt",
                "question": "What is the exact spell name used in this combat turn?",
                "criteria": {},
            },
        },
    },
]
def load_model(model_name: str):
    """Load a THX-01 model (base or fine-tuned)."""
    if thx01 is None:
        raise RuntimeError("thx01 library not installed: pip install thx01")
    return thx01.load(model_name)


def benchmark_model(agent, suite: list[dict[str, Any]]) -> dict[str, Any]:
    """Run benchmark suite against a single agent. Returns per-item results + aggregates."""
    results: list[dict[str, Any]] = []
    latencies: list[float] = []
    correct_by_type: dict[str, dict[str, int]] = {}

    for item in suite:
        state = item["state"]
        questions = item["questions"]
        expected = item["expected"]

        start = time.perf_counter()
        result = agent.decide(state, questions)
        elapsed_ms = (time.perf_counter() - start) * 1000
        latencies.append(elapsed_ms)

        answers = result.get("answers", {})

        item_result: dict[str, Any] = {
            "id": item["id"],
            "latency_ms": round(elapsed_ms, 3),
            "answers": answers,
            "expected": expected,
            "per_question": {},
        }

        for qtype, exp_val in expected.items():
            pred_val = answers.get(qtype)
            correct = _compare(pred_val, exp_val)
            item_result["per_question"][qtype] = {
                "predicted": pred_val,
                "expected": exp_val,
                "correct": correct,
            }

            if qtype not in correct_by_type:
                correct_by_type[qtype] = {"correct": 0, "total": 0}
            correct_by_type[qtype]["total"] += 1
            if correct:
                correct_by_type[qtype]["correct"] += 1

        results.append(item_result)

    sorted_lat = sorted(latencies)
    p95_idx = int(len(sorted_lat) * 0.95) if sorted_lat else 0
    aggregate: dict[str, Any] = {
        "avg_latency_ms": round(statistics.mean(latencies), 3),
        "p50_latency_ms": round(statistics.median(latencies), 3),
        "p95_latency_ms": round(sorted_lat[p95_idx], 3) if sorted_lat else 0,
        "max_latency_ms": round(max(latencies), 3) if latencies else 0,
        "accuracy_by_type": {
            qt: round(c["correct"] / c["total"], 4) if c["total"] else 0.0
            for qt, c in correct_by_type.items()
        },
        "correct_by_type": correct_by_type,
        "total_items": len(suite),
    }
    return {"items": results, "aggregate": aggregate}


def _compare(pred: Any, exp: Any) -> bool:
    """Compare prediction to expected (handles numbers, strings, lists)."""
    if pred is None:
        return False
    if isinstance(exp, (int, float)):
        try:
            return abs(float(pred) - float(exp)) < 1e-6
        except (TypeError, ValueError):
            return str(pred).strip().lower() == str(exp).strip().lower()
    if isinstance(exp, list):
        return set(map(str, pred)) == set(map(str, exp))
    return str(pred).strip().lower() == str(exp).strip().lower()


def compare_reports(base_report: dict[str, Any], ft_report: dict[str, Any]) -> dict[str, Any]:
    """Produce a side-by-side comparison of base vs fine-tuned."""
    comp: dict[str, Any] = {
        "base": base_report["aggregate"],
        "finetuned": ft_report["aggregate"],
        "improvement": {},
    }
    for qt in base_report["aggregate"]["accuracy_by_type"]:
        base_acc = base_report["aggregate"]["accuracy_by_type"].get(qt, 0.0)
        ft_acc = ft_report["aggregate"]["accuracy_by_type"].get(qt, 0.0)
        comp["improvement"][qt] = {
            "base_accuracy": base_acc,
            "finetuned_accuracy": ft_acc,
            "delta": round(ft_acc - base_acc, 4),
            "relative_improvement_pct": round(
                (ft_acc - base_acc) / base_acc * 100 if base_acc else 0.0, 2
            ),
        }
    base_lat = base_report["aggregate"]["avg_latency_ms"]
    ft_lat = ft_report["aggregate"]["avg_latency_ms"]
    comp["improvement"]["latency_ms"] = {
        "base": base_lat,
        "finetuned": ft_lat,
        "delta_ms": round(ft_lat - base_lat, 3),
    }
    return comp


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark THX-01 models on Dofus tasks")
    parser.add_argument("--base-model", default="doofz/THX-01")
    parser.add_argument("--ft-model", default=None, help="Fine-tuned model path or HF hub ID")
    parser.add_argument("--output", default="benchmarks/comparison_report.json")
    args = parser.parse_args()

    suite = DOFUS_BENCHMARK_SUITE
    print(f"Loaded {len(suite)} Dofus benchmark items")

    base_agent = load_model(args.base_model)
    print("Benchmarking base model...")
    base_report = benchmark_model(base_agent, suite)

    reports: dict[str, Any] = {"base": base_report}

    if args.ft_model:
        print(f"Loading fine-tuned model: {args.ft_model}")
        ft_agent = load_model(args.ft_model)
        print("Benchmarking fine-tuned model...")
        ft_report = benchmark_model(ft_agent, suite)
        reports["finetuned"] = ft_report
        reports["comparison"] = compare_reports(base_report, ft_report)
        print(json.dumps(reports["comparison"], indent=2))

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(reports, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nReport written to {out_path}")


if __name__ == "__main__":
    main()