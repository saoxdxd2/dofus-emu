# NetworkHealth AI — planned repository layout

The pipeline was refactored from the single-file `networkhealth_river.py` into a
package split by *deployment boundary*, not by convenience. The split matters
because the Colab training path and the local inference path must share one
feature definition or offline metrics become meaningless online.

```
dofus-network/
├── README.md                      Phase 1/2 documentation and honest limits
├── requirements.txt               runtime deps (numpy, requests)
├── networkhealth_river.py         original single-file prototype (superseded)
│
├── networkhealth/                 SHARED CORE + LOCAL INFERENCE
│   ├── __init__.py                version and package description
│   ├── __main__.py                `python -m networkhealth <cmd>` entry point
│   ├── cli.py                     collect | clean | prepare | baselines | train | inference | all
│   ├── provenance.py              hashing, run manifests, environment capture
│   ├── data.py                    RIPE Atlas downloader with provenance metadata
│   ├── clean.py                   cleaning that preserves genuine failures
│   ├── features.py                causal feature builder + frozen Normaliser
│   ├── splits.py                  purge/embargo, series holdout, compact summaries
│   ├── baselines.py               persistence, MA, EWMA, seasonal, robust-z
│   ├── model.py                   compact causal TCN, multi-task heads
│   ├── training.py                CPU training loop + honest comparison table
│   ├── evaluation.py              metrics, calibration, detection, CPU cost
│   ├── contract.py                checkpoint contract (frozen normaliser + layout)
│   ├── selftest.py                end-to-end synthetic verification, no torch needed
│   ├── collectors/                one module per public data source
│   │   ├── atlas.py               RIPE Atlas paginated collector
│   │   └── ripe_atlas.py          backwards-compatible alias
│   └── inference/                 LOCAL CPU INFERENCE — no CUDA, no training
│       ├── __init__.py
│       └── runtime.py             rolling window, forecasts, uncertainty, caveats
│
├── colab/                         COLAB GPU PATH — T4 only
│   ├── __init__.py
│   ├── train_gpu.py               FP16 AMP driver, batch-size sweep, refuses CPU
│   └── train_networkhealth_t4.ipynb   notebook for the VS Code Colab extension
│
├── docs/
│   └── LAYOUT.md                  this file
│
└── networkhealth_artifacts/       generated outputs (not committed)
    ├── raw.jsonl                  downloaded observations
    ├── raw_provenance.json        source, window, interval statistics
    ├── clean.jsonl                cleaned observations
    ├── clean_report.json          what was dropped and why
    ├── features.npz               contiguous float32 windows for training
    ├── baselines.json             baseline metrics on the same split
    ├── baseline_split_indices.json   which rows were train vs valid
    ├── model.pt                   checkpoint (weights + frozen normaliser)
    ├── evaluation.json            metrics, cost, comparison table
    ├── REPORT.md                  human-readable summary
    └── manifest_*.json            per-stage provenance manifests
```

## Deployment boundary

| Concern | Colab path | Local path |
| --- | --- | --- |
| Collection / cleaning | either | preferred |
| Feature building | shared `features.py` | shared `features.py` |
| Split definition | shared `splits.py` | shared `splits.py` |
| Training | `colab/train_gpu.py` (CUDA, FP16 AMP) | `networkhealth/training.py` (CPU smoke only) |
| Inference | not used | `networkhealth/inference/runtime.py` |
| Model definition | shared `model.py` | shared `model.py` |
| Checkpoint contract | writes via `contract.py` | reads via `contract.py` |

The local path never imports CUDA-specific code, and the Colab path refuses to
run without a GPU. A laptop cannot accidentally launch a 40-epoch T4 job, and a
Colab VM cannot accidentally be used for production inference.

## Why `features.py` is shared and must stay shared

If the Colab script computed its own log-RTT deltas while the laptop computed
slightly different ones, the trained weights would be applied to inputs the model
never saw. The failure is silent: predictions still come out, they are just
wrong. Sharing the module is the guard, and `contract.py` validates at load time
that the checkpoint's feature order and normaliser match the running code.

## Scale reality check

`prepare` reports the actual window count. Public RIPE Atlas ping data produces
roughly **one window per observation**, so a 25-measurement pull yields tens of
thousands of windows, not millions. Scaling to 1–5M windows needs a wide Atlas
pull plus authorized local telemetry. 100M windows is not reachable from public
Atlas data alone and should not be quoted as a target.

## Security scope

Phases 1–2 train **performance** heads only: RTT, jitter, loss, degradation and
an anomaly suspicion score. They do not train a spoofing or attack classifier.
Spoofing detection requires labelled positives from authorized local telemetry —
without them, recall is unmeasurable. Anomaly scores are triage signals; security
verdicts belong to deterministic components in Phase 6.
