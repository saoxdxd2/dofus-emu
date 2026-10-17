# NetworkHealth AI — Phase 1/2 pipeline

Lightweight network-intelligence system: predicts connection degradation from
timestamped telemetry, with honest baselines and reproducible artifacts.
Training happens on a Colab T4; inference runs locally on CPU.

## Layout

```
networkhealth/                 SHARED CORE — imported by both paths
    features.py                causal feature builder + frozen normaliser
    splits.py                  leakage-resistant time/series splits
    baselines.py               persistence, MA, EWMA, seasonal, robust-z anomaly
    clean.py                   cleaning that preserves genuine failures
    model.py                   compact causal TCN, multi-task heads
    training.py                CPU training loop (smoke runs, no GPU needed)
    evaluation.py              metrics, calibration, detection, CPU cost
    contract.py                checkpoint contract shared by both paths
    provenance.py              hashing + run manifests
    data.py                    RIPE Atlas downloader with provenance
    cli.py                     command line entry point
    inference/                 LOCAL CPU INFERENCE — no CUDA, no training
        runtime.py             rolling window, forecasts, uncertainty, caveats

colab/                         COLAB GPU PATH — T4 only
    train_gpu.py               FP16 AMP driver, batch-size sweep, refuses CPU
    train_networkhealth_t4.ipynb   notebook for the VS Code Colab extension

networkhealth_artifacts/       outputs (gitignored): raw, clean, features,
                               baselines, model.pt, evaluation.json, manifests
```

**Why the split matters.** `colab/` and `networkhealth/inference/` both import
the *same* `features.py`, `contract.py` and `splits.py`. Duplicating feature code
across the two paths would be the fastest way to make offline metrics meaningless
online. The checkpoint carries the frozen normaliser and feature order, so the
inference side physically cannot mis-parse it.

## Quick start

```bash
python -m pip install -r requirements.txt

# Verify the pipeline without network access or PyTorch:
python -m networkhealth.selftest

# Real run: collect -> clean -> prepare -> baselines
python -m networkhealth collect  --days 7 --measurements 25
python -m networkhealth clean    --input networkhealth_artifacts/raw.jsonl
python -m networkhealth prepare  --input networkhealth_artifacts/clean.jsonl
python -m networkhealth baselines --input networkhealth_artifacts/features.npz
```

Then train on the T4 using `colab/train_networkhealth_t4.ipynb`, copy `model.pt`
back, and verify locally:

```bash
python -m networkhealth inference --checkpoint networkhealth_artifacts/model.pt
```

## Environment matrix

| Task | Python | Needs PyTorch | Where |
| --- | --- | --- | --- |
| collect / clean / prepare | 3.11+ | no | local |
| baselines | 3.11+ | no | local |
| GPU training | 3.11–3.12 | yes, CUDA | Colab T4 |
| CPU inference | 3.11+ | yes, CPU | local |

This machine runs Python 3.15, for which PyTorch wheels may not exist yet. The
collection, cleaning, feature, baseline and self-test stages all run here without
torch. Training needs Colab or a Python 3.11–3.13 venv.

## What changed from the original script

| Issue in `networkhealth_river.py` | Fix |
| --- | --- |
| Scaler fitted on the full dataset before splitting | Normaliser fitted on **train only**, frozen into the checkpoint |
| Single timestamp quantile split; overlapping windows straddled the boundary | Purge + embargo gap; `time_and_series` mode holds out whole probes |
| `(measurement_id, probe_id)` series key could merge sources | Series key is `(source, measurement_id, probe_id)` |
| Single API page per measurement, then index subsampling | Full pagination; explicit budget; documented interval statistics |
| No baselines; metrics reported in isolation | persistence / MA / EWMA / seasonal_naive / robust-z, same split, same log space |
| Causal conv blocks left-padded without trimming | Padding trimmed so no future timestep enters the receptive field |
| No calibration reporting | Brier score and ECE per horizon |
| `None` RTT risked being imputed as zero | Masked losses; `None` is preserved as a real packet-loss event |
| Index lists embedded in metrics JSON | Indices written to a separate artifact |

## Honest limits — read before interpreting results

1. **Dataset scale.** Public RIPE Atlas ping data yields roughly **one training
   window per observation**. 100M windows would require ~110M raw observations.
   A wide Atlas pull plus authorized local telemetry realistically gives
   **1–5M windows**. Report the real number; do not inflate it.

2. **Public data cannot characterise your client.** RIPE Atlas measures
   third-party probes. It says nothing about how the game client behaves on this
   machine. Phase 5 authorized local telemetry is required before trusting any
   prediction about the actual environment.

3. **The anomaly head is not an attack detector.** It produces a suspicion score
   in [0,1] trained on loss rate and RTT deviation. Spoofing, DoS and congestion
   all produce similar timing signatures. An unusual RTT or a missing ping must
   never be classified as an attack on that basis alone. Phase 6 keeps security
   decisions in deterministic, separately testable components.

4. **No spoofing/attack labels exist in this data.** Recall cannot be measured
   without labelled positives. Any security classifier trained here would be
   unmeasurable, which is worse than not having one.

## Metrics reported

Per horizon, in log1p RTT space:

- RTT: MAE, RMSE, bias (masked to observations that actually had a reply)
- Jitter: MAE
- Loss: Brier score, ECE, precision/recall/F1, false-alarm rate
- Degradation: Brier score, ECE, precision/recall/F1, false-alarm rate
- Cost: parameter count, state-dict size, CPU latency (median/p95), measured on CPU

## Adaptation and policy

Phases 7–10 are **not** implemented. Phase 7's bounded online adaptation must not
start until the Phase 2 gate passes: the model must beat persistence/EWMA on the
metrics that matter without unacceptable latency. Current results do not yet
clear that gate — see `networkhealth_artifacts/REPORT.md` after a real run.
