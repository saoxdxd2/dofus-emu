"""Domain-Native AI Modules for Dofus Farm.
"""

from typing import Any

_MODEL_EXPORTS = {
    "DofusBrainTransformer",
    "DofusDomainDistiller",
    "DofusDomainTokenizer",
    "DofusTransformerBlock",
}
_TRAIN_EXPORTS = {
    "DofusNativeDataset",
    "TrainReport",
    "evaluate",
    "split_samples",
    "train_dofus_ai_from_scratch",
}

__all__ = sorted(_MODEL_EXPORTS | _TRAIN_EXPORTS)


def __getattr__(name: str) -> Any:
    if name in _MODEL_EXPORTS:
        from . import dofus_native_model

        return getattr(dofus_native_model, name)
    if name in _TRAIN_EXPORTS:
        from . import train_from_scratch

        return getattr(train_from_scratch, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
