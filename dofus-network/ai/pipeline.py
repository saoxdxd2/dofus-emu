"""Dofus AI pipeline automation.

One entry point that chains the whole AI workflow so a run is a single command
instead of a sequence of remembered invocations:

    data generation -> training -> benchmark gate -> (optional) distillation

Design rules:

* **Stages are idempotent and skippable.** ``--skip-data`` reuses an existing
  corpus; ``--benchmark-only`` never trains. Each stage writes its own artifact
  so a partial run is still reviewable.
* **The benchmark is a gate, not a report.** ``--require-certification`` makes a
  failing benchmark a non-zero exit, so a broken model cannot quietly become the
  distillation source.
* **Everything is seeded.** Data generation, the split, and torch are all seeded
  from one ``--seed`` value, so two runs with the same flags produce the same
  numbers.

Usage::

    python -m ai.pipeline --all
    python -m ai.pipeline --benchmark-only --checkpoint models/dofus-brain-native/dofus_brain.pt
    python -m ai.pipeline --skip-data --epochs 10 --require-certification
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logger = logging.getLogger("dofus.pipeline")

DATA_DIR = REPO_ROOT / "data"
MODELS_DIR = REPO_ROOT / "models"
NATIVE_DIR = MODELS_DIR / "dofus-brain-native"
CORPUS_FILE = DATA_DIR / "dofus_deep_training_corpus.json"
COMBAT_FILE = DATA_DIR / "dofus_combat_policy_dataset.json"
PIPELINE_REPORT = MODELS_DIR / "pipeline_report.json"


@dataclass
class StageResult:
    name: str
    ok: bool
    seconds: float = 0.0
    detail: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.name,
            "ok": self.ok,
            "seconds": round(self.seconds, 2),
            "detail": self.detail,
            "error": self.error,
        }


def _corpus_stats(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"exists": False, "samples": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"exists": True, "samples": 0, "error": str(exc)}
    counts: Dict[str, int] = {}
    for sample in data:
        key = sample.get("type", "UNKNOWN")
        counts[key] = counts.get(key, 0) + 1
    return {"exists": True, "samples": len(data), "by_type": counts}


def stage_generate_data(seed: int, force: bool = False) -> StageResult:
    """Regenerate the combat and expanded corpora deterministically."""
    started = time.perf_counter()
    result = StageResult(name="generate_data", ok=True)
    try:
        from data.generate_combat_policy_dataset import generate_scenarios
        from data.generate_expanded_corpus import generate_natural_dataset

        if force or not COMBAT_FILE.exists():
            scenarios = generate_scenarios(1500, seed=seed)
            result.detail["combat_scenarios"] = len(scenarios)

        corpus = generate_natural_dataset(seed=seed)
        CORPUS_FILE.write_text(
            json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")
        result.detail["corpus_samples"] = len(corpus)
        result.detail["corpus_stats"] = _corpus_stats(CORPUS_FILE)
        logger.info(f"Generated {len(corpus):,} corpus samples (seed={seed})")
    except Exception as exc:  # noqa: BLE001 - surfaced in the report
        result.ok = False
        result.error = f"{type(exc).__name__}: {exc}"
        logger.error(f"Data generation failed: {result.error}")
    result.seconds = time.perf_counter() - started
    return result


def stage_train(
    seed: int,
    epochs: int,
    batch_size: int,
    lr: float,
    valid_fraction: float,
    patience: int,
    device: Optional[str],
) -> StageResult:
    """Train the domain-native transformer with the held-out validation split."""
    started = time.perf_counter()
    result = StageResult(name="train", ok=True)
    try:
        from ai.train_from_scratch import train_dofus_ai_from_scratch

        _, tokenizer, report = train_dofus_ai_from_scratch(
            epochs=epochs,
            batch_size=batch_size,
            lr=lr,
            device=device,
            valid_fraction=valid_fraction,
            seed=seed,
            patience=patience,
            corpus_file=CORPUS_FILE,
            output_dir=NATIVE_DIR,
            save=True,
        )
        result.detail = {
            "epochs_run": report.epochs,
            "best_epoch": report.best_epoch,
            "best_valid_loss": report.best_valid_loss,
            "train_examples": report.train_examples,
            "valid_examples": report.valid_examples,
            "shared_text_groups": report.shared_text_groups,
            "vocab_size": len(tokenizer.vocab),
            "wall_clock_seconds": report.wall_clock_seconds,
            "device": report.device,
            "final_epoch": report.history[-1] if report.history else {},
        }
        # A split that shares text groups is a bug, not a warning.
        if report.shared_text_groups:
            result.ok = False
            result.error = (
                f"{report.shared_text_groups} text groups appear in both splits; "
                "the leak-free split guarantee was violated.")
    except Exception as exc:  # noqa: BLE001
        result.ok = False
        result.error = f"{type(exc).__name__}: {exc}"
        logger.error(f"Training failed: {result.error}")
    result.seconds = time.perf_counter() - started
    return result


def stage_benchmark(
    seed: int,
    checkpoint: Optional[Path],
    threshold: float,
) -> StageResult:
    """Run the certification battery against a saved checkpoint or the live model."""
    started = time.perf_counter()
    result = StageResult(name="benchmark", ok=True)
    try:
        import torch

        from ai.dofus_knowledge_benchmark import DofusKnowledgeBenchmark
        from ai.dofus_native_model import DofusBrainTransformer, DofusDomainTokenizer

        tokenizer = DofusDomainTokenizer.load(NATIVE_DIR / "vocab.json")
        config_path = NATIVE_DIR / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}

        model = DofusBrainTransformer(
            vocab_size=config.get("vocab_size", len(tokenizer.vocab) + 16),
            d_model=config.get("d_model", 256),
            nhead=config.get("nhead", 8),
            num_layers=config.get("num_layers", 6),
            dim_feedforward=768,
            num_policy_classes=4,
            num_intent_classes=12,
        )
        ckpt = Path(checkpoint) if checkpoint else NATIVE_DIR / "dofus_brain.pt"
        if ckpt.exists():
            state = torch.load(str(ckpt), map_location="cpu", weights_only=False)
            if isinstance(state, dict) and "state_dict" in state:
                state = state["state_dict"]
            model.load_state_dict(state, strict=False)
            result.detail["checkpoint"] = str(ckpt)
        else:
            result.detail["checkpoint"] = "untrained (random weights)"
            logger.warning(f"No checkpoint at {ckpt}; benchmarking random weights.")

        bench = DofusKnowledgeBenchmark(min_passing_score=threshold)
        report = bench.evaluate(model, tokenizer)
        report.print_summary()
        result.detail.update({
            "aggregate_score": report.aggregate_score,
            "is_certified": report.is_certified,
            "domain_scores": report.domain_scores,
            "generalisation_score": report.generalisation_score,
            "generalisation_passed": report.generalisation_passed,
            "generalisation_tests": report.generalisation_tests,
            "total_tests": report.total_tests,
            "passed_tests": report.passed_tests,
            "per_class": report.per_class,
            "failure_reasons": report.failure_reasons[:20],
        })
        (NATIVE_DIR / "benchmark_report.json").write_text(
            json.dumps(result.detail, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        result.ok = False
        result.error = f"{type(exc).__name__}: {exc}"
        logger.error(f"Benchmark failed: {result.error}")
    result.seconds = time.perf_counter() - started
    return result


def run_pipeline(args: argparse.Namespace) -> Dict[str, Any]:
    stages: List[StageResult] = []

    if args.benchmark_only:
        stages.append(stage_benchmark(args.seed, args.checkpoint, args.threshold))
    else:
        if not args.skip_data:
            stages.append(stage_generate_data(args.seed, force=args.force_data))
        else:
            stages.append(StageResult(
                name="generate_data", ok=True,
                detail={"skipped": True, "corpus_stats": _corpus_stats(CORPUS_FILE)}))

        if all(s.ok for s in stages):
            stages.append(stage_train(
                seed=args.seed, epochs=args.epochs, batch_size=args.batch_size,
                lr=args.lr, valid_fraction=args.valid_fraction,
                patience=args.patience, device=args.device))

        if all(s.ok for s in stages):
            stages.append(stage_benchmark(args.seed, args.checkpoint, args.threshold))

    report = {
        "seed": args.seed,
        "stages": [s.as_dict() for s in stages],
        "all_ok": all(s.ok for s in stages),
    }
    benchmark = next((s for s in stages if s.name == "benchmark"), None)
    if benchmark is not None:
        report["certified"] = bool(benchmark.detail.get("is_certified"))
        report["aggregate_score"] = benchmark.detail.get("aggregate_score")

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    PIPELINE_REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\n" + "=" * 65)
    print("   DOFUS AI PIPELINE SUMMARY")
    print("=" * 65)
    for stage in stages:
        icon = "✅" if stage.ok else "❌"
        print(f"  {icon} {stage.name:<16} {stage.seconds:7.1f}s  "
              f"{'' if stage.ok else (stage.error or '')}")
    print("=" * 65)
    print(f"  report: {PIPELINE_REPORT}")
    print("=" * 65 + "\n")

    if args.require_certification and not report.get("certified"):
        print("Certification was required but not achieved; exiting non-zero.",
              file=sys.stderr)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai.pipeline",
        description="Automated Dofus AI pipeline: data -> train -> benchmark gate.",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--skip-data", action="store_true",
                        help="Reuse the existing corpus instead of regenerating it")
    parser.add_argument("--force-data", action="store_true",
                        help="Regenerate the combat scenarios even if they exist")
    parser.add_argument("--benchmark-only", action="store_true",
                        help="Skip data and training; only run the certification battery")
    parser.add_argument("--checkpoint", type=Path, default=None,
                        help="Checkpoint to benchmark (default: models/dofus-brain-native/dofus_brain.pt)")
    parser.add_argument("--threshold", type=float, default=90.0,
                        help="Certification threshold in percent")
    parser.add_argument("--require-certification", action="store_true",
                        help="Exit non-zero when the benchmark does not certify the model")
    parser.add_argument("--epochs", type=int, default=15)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--valid-fraction", type=float, default=0.15)
    parser.add_argument("--patience", type=int, default=5)
    parser.add_argument("--device", default=None)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = build_parser().parse_args(argv)
    report = run_pipeline(args)
    if not report["all_ok"]:
        return 1
    if args.require_certification and not report.get("certified"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
