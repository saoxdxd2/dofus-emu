# 30M encoder — NLP benchmark, four languages

Date: 2026-10-21. Hardware: local CPU, 4 threads, fp32 torch. Splits: `core_sft_v4`
(leakage-free, language-stratified).

## Verdict

**Both 30M checkpoints are degenerate and must not be shipped.** They are at or
below the random floor, and their tool heads have collapsed onto a handful of
classes. This is not a close call that more evaluation would settle — it is a
checkpoint that learned the marginal distribution of tools and nothing about the
text.

| model | gold tool / call | val tool / call | top-3 (gold) | diagnosis |
| --- | --- | --- | --- | --- |
| random init (30M, current code) | 5.6% / 5.6% | — | 18.3% | the floor |
| **gpu_155** (Kaggle T4, 155 steps) | **4.2% / 4.2%** | **1.2% / 0.9%** | 15.5% | **below random**, 4-class collapse |
| **cpu_100** (local, 100 steps) | **11.3% / 0.0%** | 21.7%\* / 0.0% | — | 1-class collapse (`nav.goto` 100%) |

\* sample-specific: the model answers `nav.goto` for every input, so this is just
that tool's share of the sample, not a skill.

Random for 16 tools is 6.25%. `gpu_155` is *below* it — the collapse is not
uniform, it is biased toward rare tools.

## Per language

`gold` (hand-written, 16–20 cases per language):

| | fr | en | es | de |
| --- | --- | --- | --- | --- |
| gpu_155 tool | 6% | 10% | **0%** | **0%** |
| gpu_155 call | 6% | 10% | 0% | 0% |
| cpu_100 tool | — | — | — | — |

`val` (frame-held-out, 635–756 cases per language):

| | fr (682) | en (756) | es (635) | de (733) | auto (484) |
| --- | --- | --- | --- | --- | --- |
| gpu_155 tool | 1% | 1% | 1% | **0%** | 2% |
| gpu_155 call | 1% | 1% | 1% | 0% | 2% |

There is no per-language story yet: **every language fails, German hardest**. The
one thing this table does prove is that the harness for measuring per-language
accuracy works — which it did not before today.

## Why it collapsed

The prediction histograms say it directly:

```
gpu_155  val -> safe.stop 27% | farm.stop 25% | knowledge.search 24% | quest.list 15%
cpu_100  val -> nav.goto 100%
```

`cpu_100` is classic mode collapse to one class. `gpu_155` is worse: it spread
across four *rare* tools and never predicts the frequent ones. That pattern points
at the training recipe, not the data:

1. **155 steps** on 4,932 examples at lr 3e-4 with a cosine schedule and **no
   warmup** — the head moves before the encoder has any signal, and never recovers;
2. **inverse-frequency tool weighting** (`sqrt(total/count/16)`) upweights exactly
   the rare classes the head collapsed onto, and with this few steps the bias is
   never corrected;
3. `label_smoothing=0.05` plus span/presence losses that dominate early — the
   reported loss fell from 8.6 to 4.0 while tool accuracy stayed flat at ~10%,
   which is the signature of learning everything *except* the task.

## Confidence cannot be used as a safety gate

| | mean max-prob on correct | on wrong | separation |
| --- | --- | --- | --- |
| gpu_155 (gold) | 0.138 | 0.106 | **0.032** |
| random init (gold) | 0.129 | 0.129 | 0.000 |

1/16 = 0.0625 is uniform. A max-prob of 0.13 means the distribution is still
nearly flat: the model is not "unsure", it is unformed. Gating on confidence
(e.g. "refuse below 0.6") would refuse everything, and thresholding at 0.15 would
accept everything.

## Cost (the part that *is* fine)

| | value |
| --- | --- |
| params | 30.74M |
| disk | 123.4 MB fp32 |
| load | 0.66–0.89 s |
| latency | **118–125 ms/example** (fp32, 4 threads, batch 16) |
| earlier INT8 ONNX measurement | 55 ms p50 / 61 ms p95, 31.5 MB |

So the architecture and the export path are sound; the fp32 torch path is ~2.2×
slower than the INT8 ONNX artifact, which is expected. Nothing about the *design*
is wrong — the weights are.

## The bar it has to clear

Measured model-free baselines on the same splits (`python -m ai.release_gate`):

| split | guard (regex only) | rule policy |
| --- | --- | --- |
| `val` | **71.1% tool / 13.6% call** | 47.5% / 9.4% |
| `gold` | **73.2% tool / 56.3% call** | 57.7% / 46.5% |

A trained encoder must beat **71% tool and 40% call on `val`**, and **56% call on
`gold`**, to be worth a single megabyte. The current checkpoints are 13× short on
gold call accuracy and 44× short on val call accuracy.

## What to change before the next run

1. **Steps, not epochs.** 155 steps was *exactly one epoch* of the old 4,932-row
   corpus. The planned run is 12 epochs of the 20,984-row corpus ≈ **7,900 steps —
   51× more optimizer steps** than the run that produced these weights.
2. **Warmup** (5% linear) + lower peak lr (1e-4) for the first run; the head should
   not move before the trunk does.
3. **Drop the inverse-frequency weighting** for the first run, or cap it at 2×.
   It was designed to help rare tools and instead produced a bias toward them.
4. **Early stop on `val` tool accuracy**, not val loss — loss improved monotonically
   while accuracy stayed at chance.
5. **Log per-language accuracy every eval**, using the stratified `val` split.
6. **Sanity gate before any long run:** 200 steps must beat the majority-class
   baseline on `val_indist`. If it does not, the recipe is broken, not the data.

## Reproduce

```bash
python -m ai.bench_encoder --checkpoint <model.pt> --allow-legacy \
    --splits gold val val_indist --out docs/bench.json

python -m ai.release_gate          # baselines on the same splits
```

`--allow-legacy` zeroes heads that did not exist when the checkpoint was trained
(`lex_tool` here) and records that in the report, so the measurement is of *those
weights* under the current architecture.

Artifacts: `docs/bench_30m_gold.json`, `docs/bench_30m_val.json`.
