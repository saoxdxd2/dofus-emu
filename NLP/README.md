# Polyglot-0

A hand-programmed four-language NLP model for **weight-space programming (WSP)**
research: English, French, Spanish and German, with **no gradient descent, no
optimizer, no loss function, and no training**.

The weights are written directly from a hand-authored concept table. The model
is frozen after construction. See [REPORT.md](REPORT.md) for the full write-up,
including caveats and the edit/ablation experiments.

## What it does

- 4-way language identification
- 3-way sentiment classification with negation and intensifier handling
- Word-level translation across all 12 ordered language pairs
- Cross-lingual sentence retrieval
- A small interactive demo that reports language, sentiment, and lexical glosses

## Results

On its own closed-vocabulary evaluation suite:

| Task | Result |
|---|---:|
| Language ID | 100.0% |
| Sentiment (all conditions) | 100.0% accuracy, 1.000 macro-F1, 0.003 MAE |
| Negated sentiment | 100.0% |
| Intensified sentiment | 100.0%, 0.003 MAE |
| Curated 120-sentence corpus | 100.0% |
| Word translation (n=1,074) | 100.0% |
| Cross-lingual retrieval (n=360) | 100.0%, mean rank 1.00 |

These are specification tests, not natural-language benchmark results; the
evaluation data and the model's features share the same hand-written lexicon.

### External benchmark check

The inference-only harness in `polyglot0/benchmarks.py` also runs public data
without changing any weights:

| Benchmark | Result |
|---|---:|
| SST-2 validation (GLUE, binary) | **51.4%** accuracy, **0.435** macro-F1 (majority 50.9%) |
| SST-2 test, 1,821-example split | **49.9%** accuracy, **0.407** macro-F1 (majority 50.1%) |
| Language ID, 4-language subset | **98.6%** accuracy, **0.986** macro-F1 (chance 25%) |
| Multilingual sentiment (4 languages) | **38.1%** accuracy, **0.290** macro-F1 (majority 33.3%; all-neutral macro-F1 0.167) |

The language-ID circuit transfers to open text; the open-vocabulary sentiment
circuit does not. MMLU, ARC, HellaSwag, GSM8K, HumanEval, XNLI and similar
benchmarks are **not applicable**: the model has no generation head,
multiple-choice scorer, NLI head, or world knowledge. See
`benchmark_results.txt` for per-language sentiment results and diagnostics.

### Vs Llama 3.2 3B / Llama 3.1 8B

There is no 8B Llama 3.2: Llama 3.2 text models are 1B and 3B, and the 8B model
is Llama 3.1. The published comparison is:

| Benchmark | Polyglot-0 | Llama 3.2 3B Instruct | Llama 3.1 8B Instruct |
|---|---:|---:|---:|
| SST-2 test (1,821), accuracy | **49.9%** | **77.6%** | **93.4%** |
| MMLU English (5-shot) | N/A | 63.4 | 69.4 |
| MMLU French (5-shot) | N/A | 54.6 | 62.3 |
| MMLU Spanish (5-shot) | N/A | 55.1 | 62.5 |
| MMLU German (5-shot) | N/A | 53.3 | 60.6 |
| Language ID, 4-way subset | **98.6%** | not published | not published |
| Multilingual sentiment, 3-way | **38.1%** | not published | not published |

On the only benchmark with published numbers for both models on the same split,
Polyglot-0 loses by 27.7 points to Llama 3.2 3B and 43.5 points to Llama 3.1 8B.
The Llama SST-2 numbers are zero-shot; Polyglot-0 uses a fixed `score > 0` rule.
Polyglot-0 cannot be scored on MMLU at all.
Language ID is its strongest external result, but it is a narrow 4-way task and
there is no published Llama head-to-head. Its advantages are size (1.68M
parameters, 117,931 non-zero), CPU inference, interpretability, and training-free
construction, not accuracy. See REPORT.md Section 5.6 for sources and caveats.

## Quick start

From `C:\Users\sao\Documents\dofus-emu\NLP`:

```powershell
# Check the lexicon, vocabulary, and corpus
& "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe" -m polyglot0.validate

# Run all internal evaluations, controls, and the edit experiment
$env:PYTHONIOENCODING="utf-8"
& "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe" -m polyglot0.eval

# Run public external benchmarks (downloads evaluation data on first run)
$env:PYTHONIOENCODING="utf-8"
& "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe" -m polyglot0.benchmarks

# Fixed demonstration
$env:PYTHONIOENCODING="utf-8"
& "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe" -m polyglot0.demo --demo

# Interactive demo (empty line quits)
$env:PYTHONIOENCODING="utf-8"
& "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe" -m polyglot0.demo
```

`results.txt` is a captured run of the internal evaluation and
`benchmark_results.txt` is a captured run of the external checks, including the
1,821-example SST-2 test split. Python 3.14 and PyTorch are required; everything
runs on CPU. No pretrained model download is needed. The optional benchmark
harness also needs `pandas` and `pyarrow` and downloads public evaluation data
into `data/` on first run; it attaches SST-2 test labels from the labeled SetFit
copy because the GLUE test parquet stores `label=-1`.

## How it works

- **1,681,527 dense parameters**, of which **117,931 are non-zero** (7.01%).
- `d_model=544`, one self-attention layer, four heads × 136 dimensions.
- The first 128 embedding dimensions are an orthogonal Hadamard concept code,
  shared by all four languages. The remaining slots carry polarity, content and
  sentiment flags, a 4-dimensional language one-hot, negation/intensity flags,
  and position features.
- Four hand-wired attention heads pool: content/polarity, language, negators,
  and intensifiers.
- Language ID is a 4×4 identity head. Sentiment uses the hand-written gate
  `score = (1 - 2·neg) · polarity · (1 + 0.5·intensifier)`.

The parallel structure is why word translation and retrieval are exact by
construction: every language's form for a concept points at the same code.
This is a clean test of routing and editability, not evidence that the model
learned a cross-lingual representation.

## The WSP edit

Starting from a version with the negation and intensity gates disabled:

| Version | Changed weights | Sentiment all | Negated | Intensified MAE |
|---|---:|---:|---:|---:|
| v0 | 0 | 0.673 | 0.000 | 0.185 |
| + negation coefficient | 1 | **1.000** | **1.000** | 0.185 |
| + intensity coefficient | 1 | **1.000** | **1.000** | **0.003** |

The two edits are direct tensor writes, not training:

- `sent_scale.weight[0, 0]`: `0.0 → -2.0`
- `sent_boost.weight[0, 0]`: `0.0 → 0.5`

The unchanged `sent_scale.weight[0, 1] = 1.0` supplies the base polarity path.

## Honest scope

This is a **closed-vocabulary, template-bound proof of concept**, not a
general-purpose multilingual model. It has no syntax, no generation, no
open-vocabulary handling, and global (not scoped) negation/intensity. The
lexical gloss is word-by-word and often ungrammatical. The internal 100% scores
reflect that the tests were generated from the same specification as the
weights. On external data, language ID remains strong, while sentiment is at or
below the majority baseline; on the one exact shared benchmark (SST-2 test) it
loses to Llama 3.2 3B and Llama 3.1 8B. See [REPORT.md](REPORT.md) for the full
limitations and interpretation.

## Files

| File | Purpose |
|---|---|
| `polyglot0/lexicon.py` | 91 concepts, four-language forms, 30 parallel sentences |
| `polyglot0/tokenizer.py` | Tokenization, vocabulary, accent-insensitive aliases |
| `polyglot0/model.py` | Hand-written embedding, attention, and task heads |
| `polyglot0/eval.py` | Internal benchmarks, ablations, edit experiments, lexical gloss |
| `polyglot0/benchmarks.py` | External benchmark harness (SST-2, language ID, multilingual sentiment) |
| `polyglot0/demo.py` | CLI demo |
| `polyglot0/validate.py` | Lexicon and corpus consistency checks |
| `polyglot0/smoke.py` | Early circuit smoke test |
| `results.txt` | Captured internal evaluation output |
| `benchmark_results.txt` | Captured external benchmark output, including the 1,821-example SST-2 test split |
| `data/` | Downloaded public benchmark data (evaluation only) |
| `REPORT.md` | Full methodology, results, and limitations |
