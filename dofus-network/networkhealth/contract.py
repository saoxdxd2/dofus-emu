"""Checkpoint contract shared by the Colab training path and local inference.

Both sides import this module. That is deliberate: if the Colab training script
and the local inference module each defined their own feature layout, offline
metrics would be meaningless online. One definition, two consumers.

``load_checkpoint`` is the only sanctioned way to reconstruct a model. It
returns a bundle that carries the frozen normaliser, feature order, horizons and
model config, so the inference side physically cannot guess them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .features import FEATURE_NAMES, HORIZONS, TARGET_BLOCK, Normaliser

CHECKPOINT_FORMAT_VERSION = "2.0"


@dataclass
class ModelBundle:
    """Everything needed to run inference, and nothing that needs torch to load."""

    state_dict: dict[str, Any]
    model_config: dict[str, Any]
    normaliser: Normaliser
    feature_names: tuple[str, ...]
    horizons: tuple[int, ...]
    target_block: int
    metadata: dict[str, Any]

    def validate(self) -> list[str]:
        """Return a list of contract violations; empty means safe to use."""
        problems: list[str] = []
        if tuple(self.feature_names) != tuple(FEATURE_NAMES):
            problems.append(
                f"feature order mismatch: checkpoint has {len(self.feature_names)} "
                f"features, code expects {len(FEATURE_NAMES)}")
        if len(self.normaliser.mean) != len(self.feature_names):
            problems.append("normaliser mean length does not match feature count")
        if len(self.normaliser.std) != len(self.feature_names):
            problems.append("normaliser std length does not match feature count")
        if tuple(self.horizons) != tuple(HORIZONS):
            problems.append(f"horizons mismatch: checkpoint {self.horizons} vs code {HORIZONS}")
        if self.target_block != TARGET_BLOCK:
            problems.append("target block layout mismatch")
        if not self.model_config.get("features") == len(FEATURE_NAMES):
            problems.append("model config feature count mismatch")
        return problems

    def as_metadata(self) -> dict[str, Any]:
        return {
            "checkpoint_format_version": CHECKPOINT_FORMAT_VERSION,
            "feature_names": list(self.feature_names),
            "horizons": list(self.horizons),
            "target_block": self.target_block,
            "model_config": self.model_config,
            "training": self.metadata,
        }


def load_checkpoint(path: Path) -> ModelBundle:
    """Load and validate a checkpoint without requiring torch on this machine."""
    import torch

    raw = torch.load(Path(path), map_location="cpu", weights_only=False)
    bundle = ModelBundle(
        state_dict=raw["state_dict"],
        model_config=raw.get("model_config", {}),
        normaliser=Normaliser.from_dict(raw["normaliser"]),
        feature_names=tuple(raw.get("feature_names", FEATURE_NAMES)),
        horizons=tuple(raw.get("horizons", HORIZONS)),
        target_block=int(raw.get("target_block", TARGET_BLOCK)),
        metadata={
            "best_valid_loss": raw.get("best_valid_loss"),
            "epochs_run": raw.get("epochs_run"),
            "train_config": raw.get("train_config"),
            "split_summary": raw.get("split_summary"),
            "checkpoint_format_version": raw.get("checkpoint_format_version",
                                                 CHECKPOINT_FORMAT_VERSION),
        },
    )
    problems = bundle.validate()
    if problems:
        raise ValueError("Checkpoint failed validation: " + "; ".join(problems))
    return bundle
