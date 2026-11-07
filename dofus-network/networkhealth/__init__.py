"""NetworkHealth AI — a small, honest network-intelligence stack.

Layout:

    networkhealth/
        __init__.py       public surface + version
        provenance.py     hashing, run manifests, dataset versioning
        collectors/       one module per data source (RIPE Atlas first)
        clean.py          cleaning that preserves genuine failures
        datasets.py       dataset registry + source metadata
        features.py       shared causal feature builder (train/inference parity)
        splits.py         time- and probe-aware, leakage-resistant splits
        baselines.py      persistence / MA / EWMA / robust-z reference models
        model.py          compact causal TCN (CPU-friendly)
        training.py       Phase 1/2 training loop
        evaluation.py     metrics + artifact writing
        cli.py            command line entry point
"""

from __future__ import annotations

__version__ = "0.2.0"

__all__ = ["__version__"]
