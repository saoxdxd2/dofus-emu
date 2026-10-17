"""Benchmark-gated distillation runner.

The distillation engine (`DofusDomainDistiller`) has always been callable, but
nothing enforced the rule the benchmark was built for: *distillation must not
start unless the teacher is certified*. This module wires the two together.

It also fixes a real shape bug in the existing distillation path: the teacher is
a transformer that expects token ids, while the TRM-ACT student consumes a
24-dim state vector. The old code passed the same tensor to both, so a student
"distillation" step could only run if the two shapes happened to coincide.

Usage::

    python -m ai.distill --checkpoint models/dofus-brain-native/dofus_brain.pt --dry-run
    python -m ai.distill --student trm_act --epochs 5
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logger = logging.getLogger("dofus.distill")

DATA_DIR = REPO_ROOT / "data"
NATIVE_DIR = REPO_ROOT / "models" / "dofus-brain-native"
CORPUS_FILE = DATA_DIR / "dofus_deep_training_corpus.json"

INTENT_MAP = {
    "NONE": 0, "FOLLOW": 1, "AUTO_JOIN_COMBAT": 2, "PASS_TURN": 3, "AUTO_FARM": 4,
    "STOP": 5, "NAVIGATE_COORDS": 6, "NAVIGATE_ZAAP": 7, "FIND_RESOURCE": 8,
    "START_FARM_LOOP": 9, "QUEST_GUIDE": 10, "RESOURCE_LEVEL_REQ": 11,
}
STANCE_MAP = {"BURST_LETHAL": 0, "BUFF_AND_ENGAGE": 1, "TRAP_AND_KITE": 2, "SHIELD_DEFENSE": 3}


def load_teacher(checkpoint: Optional[Path] = None):
    """Load the domain teacher plus its tokenizer and config."""
    import torch

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
        num_policy_classes=len(STANCE_MAP),
        num_intent_classes=len(INTENT_MAP),
    )
    ckpt = Path(checkpoint) if checkpoint else NATIVE_DIR / "dofus_brain.pt"
    if ckpt.exists():
        state = torch.load(str(ckpt), map_location="cpu", weights_only=False)
        if isinstance(state, dict) and "state_dict" in state:
            state = state["state_dict"]
        model.load_state_dict(state, strict=False)
        logger.info(f"Loaded teacher weights from {ckpt}")
    else:
        logger.warning(f"No teacher checkpoint at {ckpt}; using random weights.")
    model.eval()
    return model, tokenizer, config


def certification_passed() -> Tuple[bool, Dict[str, Any]]:
    """Read the last benchmark report and decide whether distillation may run."""
    report_path = NATIVE_DIR / "benchmark_report.json"
    if not report_path.exists():
        return False, {"reason": f"no benchmark report at {report_path}"}
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, {"reason": f"unreadable benchmark report: {exc}"}
    return bool(report.get("is_certified")), report


def build_distillation_batch(limit: int = 64) -> Dict[str, Any]:
    """Assemble a real distillation batch from the corpus.

    Returns token ids for the teacher, the matching labels, and 24-dim state
    vectors for the TRM-ACT student. The two views are built side by side so the
    teacher and student are always looking at the same example.
    """
    import torch

    from ai.dofus_native_model import DofusDomainTokenizer

    tokenizer = DofusDomainTokenizer.load(NATIVE_DIR / "vocab.json")
    samples: List[Dict[str, Any]] = []
    if CORPUS_FILE.exists():
        samples = json.loads(CORPUS_FILE.read_text(encoding="utf-8"))

    token_rows: List[List[int]] = []
    intent_labels: List[int] = []
    stance_labels: List[int] = []
    value_targets: List[float] = []
    state_rows: List[List[float]] = []

    for sample in samples:
        text = sample.get("text", "").strip()
        if not text:
            continue
        if sample.get("type") == "TACTICAL_COMBAT":
            stance = sample.get("stance") or sample.get("label") or "BURST_LETHAL"
            if stance not in STANCE_MAP:
                continue
            token_rows.append(tokenizer.encode(text, max_length=64))
            stance_labels.append(STANCE_MAP[stance])
            intent_labels.append(-100)
            value_targets.append(1.0 if stance == "BURST_LETHAL" else 0.5)
        else:
            label = sample.get("label", "NONE")
            if label not in INTENT_MAP:
                continue
            token_rows.append(tokenizer.encode(text, max_length=64))
            intent_labels.append(INTENT_MAP[label])
            stance_labels.append(-100)
            value_targets.append(0.0)

        vec = list(sample.get("state_vector", []))
        while len(vec) < 24:
            vec.append(0.0)
        state_rows.append(vec[:24])

        if len(token_rows) >= limit:
            break

    if not token_rows:
        raise SystemExit("No usable distillation samples found in the corpus.")

    return {
        "teacher_tokens": torch.tensor(token_rows, dtype=torch.long),
        "intent_labels": torch.tensor(intent_labels, dtype=torch.long),
        "stance_labels": torch.tensor(stance_labels, dtype=torch.long),
        "value_targets": torch.tensor(value_targets, dtype=torch.float32).unsqueeze(-1),
        "state_vectors": torch.tensor(state_rows, dtype=torch.float32),
    }


def run_distillation(
    checkpoint: Optional[Path] = None,
    steps: int = 3,
    batch_limit: int = 64,
    dry_run: bool = False,
    require_certification: bool = True,
) -> int:
    certified, report = certification_passed()
    if require_certification and not certified:
        logger.error(
            "Refusing to distil: the teacher is not certified. "
            f"Benchmark detail: {report.get('reason') or report.get('aggregate_score')}")
        return 3

    if dry_run:
        print("Certification gate: "
              f"{'PASSED' if certified else 'not verified (dry run)'}")
        print(f"Benchmark report: {report.get('aggregate_score')} "
              f"(certified={report.get('is_certified')})")
        print("Dry run complete; no distillation step executed.")
        return 0

    import torch

    from ai.dofus_native_model import DofusDomainDistiller
    from networkhealth.sentinel import TinyRecursiveReasoner

    teacher, tokenizer, _ = load_teacher(checkpoint)
    student = TinyRecursiveReasoner(in_dim=24, hidden_dim=64)
    student.train()

    optimiser = torch.optim.AdamW(student.parameters(), lr=1e-3)
    batch = build_distillation_batch(batch_limit)
    logger.info(f"Distilling on {batch['teacher_tokens'].shape[0]} samples for {steps} steps.")

    losses: List[float] = []
    for step in range(1, steps + 1):
        optimiser.zero_grad()
        loss = DofusDomainDistiller.distill_to_trm_combat(
            teacher_model=teacher,
            student_model=student,
            token_inputs=batch["teacher_tokens"],
            state_vectors=batch["state_vectors"],
            policy_labels=batch["stance_labels"].clamp(min=0),
            value_targets=batch["value_targets"],
        )
        loss.backward()
        optimiser.step()
        losses.append(float(loss.item()))
        print(f"  distillation step {step}/{steps} loss={losses[-1]:.5f}")

    out_path = NATIVE_DIR / "trm_act_combat.pt"
    torch.save(student.state_dict(), out_path)
    logger.info(f"Saved distilled TRM-ACT student to {out_path}")

    (NATIVE_DIR / "distillation_report.json").write_text(json.dumps({
        "steps": steps,
        "samples": int(batch["teacher_tokens"].shape[0]),
        "losses": losses,
        "teacher_checkpoint": str(checkpoint or NATIVE_DIR / "dofus_brain.pt"),
        "certified": certified,
        "student_checkpoint": str(out_path),
    }, indent=2), encoding="utf-8")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai.distill",
        description="Benchmark-gated distillation of the domain teacher into TRM-ACT.",
    )
    parser.add_argument("--checkpoint", type=Path, default=None)
    parser.add_argument("--steps", type=int, default=3)
    parser.add_argument("--batch-limit", type=int, default=64)
    parser.add_argument("--dry-run", action="store_true",
                        help="Check the certification gate without running a step")
    parser.add_argument("--allow-uncertified", action="store_true",
                        help="Distil even if the benchmark gate has not passed")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = build_parser().parse_args(argv)
    return run_distillation(
        checkpoint=args.checkpoint,
        steps=args.steps,
        batch_limit=args.batch_limit,
        dry_run=args.dry_run,
        require_certification=not args.allow_uncertified,
    )


if __name__ == "__main__":
    raise SystemExit(main())
