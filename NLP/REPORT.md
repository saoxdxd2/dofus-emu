# Polyglot-0: hand-programming a four-language NLP model

**A small, from-scratch weight-space-programming test (no gradient descent)**  
Date: 2026-11-05  
Working directory: `C:\Users\sao\Documents\dofus-emu\NLP`

## TL;DR

I built a 1,681,527-parameter encoder for English, French, Spanish and German
whose weights were written directly from a hand-authored concept table. There
is no optimizer, no loss function, no training loop, and no pretrained
checkpoint. All parameters are frozen.

On the model's own evaluation suite it reaches:

| Task | Result |
|---|---:|
| Language ID (4-way) | **100.0%** |
| Sentiment, all conditions | **100.0%** accuracy, **1.000** macro-F1, **0.003** MAE |
| Sentiment, negated subset | **100.0%** |
| Sentiment, intensified subset | **100.0%**, MAE **0.003** |
| Curated 30-sentence parallel corpus | **100.0%** |
| Word translation (12 language pairs) | **100.0%** top-1, n=1,074 |
| Cross-lingual sentence retrieval | **100.0%**, mean rank 1.00, n=360 |

Those numbers are expected rather than miraculous: the benchmark is generated
from the same closed lexical specification that the weights encode. The useful
result is not the absolute score. It is that a transparent multilingual circuit
can be written directly into weights, that its behavior can be ablated, and
that a **two-weight edit** switches on the missing negation and intensity
behavior without retraining.

On external data the picture is mixed: **98.6%** on a 4-way language-ID subset,
**49.9%** on the 1,821-example SST-2 test split (majority 50.1%), and **38.1%**
on a four-language sentiment test. On the one benchmark with published numbers
for both models on the same split (SST-2), it loses to Llama 3.2 3B (77.6%) and
Llama 3.1 8B (93.4%); it cannot be scored on MMLU. The 98.6% language-ID result is
the one place it looks competitive, but that is a narrow 4-way task.

This is a controlled proof of concept, not a reproduction of the large-model
weight-space-programming demonstrations and not a claim that hand-programming
scales to general language. See [Limitations](#limitations).

---

## 1. What was tested

The question was:

> Can a useful multilingual NLP circuit be authored directly in weight space,
> with no gradient descent, and can targeted weight edits produce predictable
> behavior changes?

This project tests that question at a scale that can be inspected completely:
one attention layer, 91 concepts, four languages, and a few deliberately chosen
tasks. The "program" is the lexicon plus the wiring code in `model.py`; the
weights are the compiled form of that program.

Weight-space programming in the large-model sense usually means asking a strong
model to edit another model's weights. Here the programmer is a human and the
target is a tiny model. The important invariant is the same: **the behavior is
changed by writing parameters, not by fitting them.**

## 2. Why this is a genuine no-training test

- Every parameter is created by `build_model()` from tables and constants.
- The embedding table is filled from `lexicon.CONCEPTS` and `EXTRA_FORMS`.
- Attention projections are zero-initialized and then specific entries are set
  to `1.0`, `80.0`, or other hand-chosen constants.
- Task heads are set by hand: a 4x4 language identity matrix and two tiny
  sentiment gates.
- Every parameter is set to `requires_grad=False`; the builder asserts this.
- There is no `loss`, `backward()`, optimizer, training loop, checkpoint
  loading, or pretrained model anywhere in the package.
- The edit experiments in `eval.py` use `torch.no_grad()` and direct tensor
  assignments.

No external data was used to construct the weights. The only model-building
data is the hand-written lexicon; the external benchmark datasets in Section
5.5 are evaluation-only and were downloaded after the model was frozen.

## 3. Architecture

```
tokens
  │
  ▼
embedding: [ 128-d shared concept code │ polarity │ feature flags │ 4-d language │ 16-d position ]
  │
  ▼
one self-attention layer, d_model=544, 4 heads × 136
  ├─ head 0: pool content words → concept + polarity
  ├─ head 1: pool word language one-hots → language
  ├─ head 2: detect a negator
  └─ head 3: detect an intensifier
  │
  ▼
[CLS] residual state
  ├─ language head → 4-way language logits
  └─ sentiment gate → polarity score → negative / neutral / positive
```

### 3.1 Embedding layout

`d_model = 544`. The first 128 dimensions hold an orthogonal concept code. The
remaining dimensions hold scalar features and positions:

| Slice | Width | Meaning |
|---|---:|---|
| `0:128` | 128 | shared concept code (Hadamard row) |
| `128` | 1 | signed polarity |
| `129` | 1 | absolute polarity |
| `130` | 1 | content-word flag |
| `131` | 1 | sentiment-word flag |
| `132:136` | 4 | language one-hot |
| `136` | 1 | negator flag |
| `137` | 1 | intensifier flag |
| `138` | 1 | lexical-word flag |
| `139` | 1 | `[CLS]` flag |
| `140` | 1 | `[SEP]` flag |
| `141` | 1 | punctuation flag |
| `142:158` | 16 | sinusoidal position |

The concept codes are the first 91 rows of a 128×128 Sylvester Hadamard matrix,
normalized to unit length. They are orthogonal. Every surface form of a concept
receives the same code, whichever language it belongs to. That is why word
translation and cross-lingual retrieval are exact by construction: the model
does not have to learn an alignment, because the alignment is in the weight
layout.

### 3.2 Attention wiring

The model uses `torch.nn.MultiheadAttention` with four heads, but none of its
projections are learned. They are wired as follows:

| Head | Query | Key selects | Value carries | Purpose |
|---|---|---|---|---|
| 0 | `[CLS]` | content words | concept + polarity + flags | average content semantics |
| 1 | `[CLS]` | lexical words | language one-hot | language identification |
| 2 | `[CLS]` | negators | negator flag | negation presence |
| 3 | `[CLS]` | intensifiers | intensifier flag | intensity presence |

The selector sharpness is `ALPHA = 80`. This makes the attention distributions
nearly hard: for head 0 all content words share the same key activation, so the
head computes a uniform average over content words; for heads 2 and 3 the
selected token dominates. The output projection routes each head's result back
to the feature slot it came from.

There is a residual connection around attention, so the `[CLS]` state also
retains its own token embedding. Positional encodings are present in the input,
but no task circuit reads them; for these benchmarks the encoder is effectively
a bag-of-words circuit. That is a deliberate simplification, and also one of
the limitations.

### 3.3 Sentiment computation

Let:

- `p` = pooled polarity,
- `n` = negation presence,
- `i` = intensifier presence,
- `τ = 0.10`.

The hand-written gate is:

```
scale = 1 - 2n
boost = 1 + 0.5 · relu(0.5 · i)
score = scale · p · boost
```

The class logits are:

```
[-score - τ, 0, score - τ]    →    [negative, neutral, positive]
```

The sign flip for negation is a linear gate, not a learned classifier. The
intensity boost is also a single linear weight. Both are directly inspectable
and directly editable.

### 3.4 Parameter budget

| Module | Parameters | Non-zero parameters |
|---|---:|---:|
| Token embedding | 495,584 | 117,640 |
| Attention (projections + bias) | 1,185,920 | 284 |
| Language head | 20 | 4 |
| Sentiment gate weights | 3 | 3 |
| **Total** | **1,681,527** | **117,931 (7.01%)** |

Most of the 1.68M weights are structural zeros. The live circuit is a small,
sparse program embedded in a PyTorch module. The edit footprint in this report
is measured against the full parameter count; for comparison, the two changed
values are 0.00170% of the 117,931 non-zero parameters as well as 0.000119% of
the dense total.

## 4. Data and evaluation

### 4.1 Lexicon

`polyglot0/lexicon.py` defines 91 concepts across:

- pronouns, nouns, verbs, adjectives, adverbs, determiners, prepositions,
  conjunctions, negation, intensifiers, and a few function words;
- English, French, Spanish and German surface forms;
- a signed polarity and a content-word flag for each concept;
- 30 parallel sentences (120 language variants) for sentence-level checks;
- extra surface forms and elisions, including English contractions and French
  clitics.

The vocabulary contains 911 token ids (894 unique surface forms plus special
tokens and punctuation). The tokenizer lowercases, normalizes Unicode, expands
common English contractions, and adds unambiguous accent-insensitive aliases
(`cafe` → `café`). It is a closed vocabulary; unknown words become `[UNK]`.

### 4.2 Evaluation sets

**Curated corpus:** 30 meanings, each translated into four languages, for 120
sentences. It includes positive, negative and neutral examples, negation,
intensity, conjunction, and multiple content words.

**Compositional stress set:** 1,372 sentences, 343 per language. It is generated
from templates using seven nouns and sixteen adjectives:

```
7 nouns × 4 languages × (1 neutral + 16 adjectives × 3 conditions) = 1,372
```

The three adjective conditions are plain, negated (`not`/`pas`/`no`/`nicht`),
and intensified (`very`/`très`/`muy`/`sehr`). The sentiment target is computed
from the hand-written polarity table, including a factor of 1/2 for the two
content words and a factor of 1.5 for intensification.

The stress set is not used to fit anything. It is still not an independent
natural-language benchmark: it is generated from the same lexical specification
the model encodes, so a correct circuit should score near 100%. I use it to
test the circuit and the edit behavior, not to estimate generalization.

### 4.3 Metrics

- **Language ID:** four-way accuracy.
- **Sentiment:** three-way accuracy, macro-F1, and mean absolute error of the
  scalar score.
- **Curated sentiment:** the same metrics on the 120 hand-written sentences.
- **Word translation:** for each concept and each ordered language pair, use
  the source embedding to retrieve the nearest target canonical form by cosine
  similarity. There are 1,074 available pairs.
- **Cross-lingual retrieval:** encode all 120 curated sentences, then retrieve
  the matching meaning in each of the three other languages (360 queries).
  Report top-1 accuracy and mean rank among the 30 candidates in the target
  language.

## 5. Results

All numbers below are from a deterministic run; `results.txt` contains the full
console output.

### 5.1 Final hand-written model

| Task | Metric | Result |
|---|---|---:|
| Language ID | accuracy | **1.000** |
| Language ID | per language | en/fr/es/de = **1.000** each |
| Sentiment (all) | accuracy | **1.000** |
| Sentiment (all) | macro-F1 | **1.000** |
| Sentiment (all) | MAE | **0.003** |
| Sentiment (plain + neutral) | accuracy / MAE | **1.000 / 0.001** |
| Sentiment (negated) | accuracy / MAE | **1.000 / 0.005** |
| Sentiment (intensified) | accuracy / MAE | **1.000 / 0.003** |
| Curated corpus | accuracy / F1 / MAE | **1.000 / 1.000 / 0.002** |
| Word translation | top-1 accuracy | **1.000** (n=1,074) |
| Cross-lingual retrieval | top-1 / mean rank | **1.000 / 1.00** (n=360) |

The tiny MAE is due to floating-point and the soft attention average, not a
learned approximation.

### 5.2 Edit path

Starting from a version with the negation gate disabled and the intensity boost
set to zero:

| Version | Weights changed | Sentiment all | Negated | Intensified | Intensified MAE | Curated |
|---|---:|---:|---:|---:|---:|---:|
| v0: no gate, no boost | 0 | 0.673 | 0.000 | 1.000 | 0.185 | 0.800 |
| Edit 1: negation gate | +1 | **1.000** | **1.000** | 1.000 | 0.185 | **1.000** |
| Edit 2: intensity weight | +1 | **1.000** | **1.000** | **1.000** | **0.003** | **1.000** |

The two changed values are:

1. `sent_scale.weight[0, 0]`: `0.0 → -2.0`  
   The other coefficient, `sent_scale.weight[0, 1]`, is `1.0` in both v0 and
   the final model, so it is not counted as a change.
2. `sent_boost.weight[0, 0]`: `0.0 → 0.5`

In other words: **one coefficient adds negation, one coefficient adds
intensity**, for a total of 2 edited values out of 1,681,527.

This is the clearest WSP result in the project. The base model already contains
the concept, polarity and negator features; the missing behavior is supplied by
a gate that reads an existing feature. The edit does not touch the embedding,
the language head, or any attention projection, and language ID / translation /
retrieval remain at their previous values.

### 5.3 Ablations and controls

| Model | Language ID | Sentiment all | Negated | Intensified MAE | Curated | Word translation | Retrieval |
|---|---:|---:|---:|---:|---:|---:|---:|
| Final | 1.000 | 1.000 | 1.000 | 0.003 | 1.000 | 1.000 | 1.000 |
| Negation head zeroed | 1.000 | 0.673 | 0.000 | 0.003 | 0.800 | 1.000 | 1.000 |
| Random weights | 0.250 | 0.020 | 0.000 | 0.553 | 0.233 | 0.028 | 0.181 |

Zeroing the negation head removes the feature that the gate reads, reproducing
the v0 failure without changing the gate. This isolates the circuit rather than
just showing that "some weights matter."

The random control randomizes all parameters and zeroes the `[CLS]` embedding
to remove the shared residual artifact. Its retrieval score (18.1%) is above
30-way chance (3.3%) because random embeddings can still exploit accidental
surface-form overlap and sentence-length correlations, but it is far below the
hand-written model's 100%. The random language classifier collapses to one
language and gets 25% by chance.

### 5.4 What the model says, literally

Example demo output:

```text
> The coffee is not good.
  language : en  (p=0.948)
  sentiment: negative  (score=-0.492)
  gloss[fr]: le café est pas bon.
  gloss[es]: el café es no bueno.
  gloss[de]: der Kaffee ist nicht gut.

> Le café est très bon.
  language : fr  (p=0.913)
  sentiment: positive  (score=+0.746)
  gloss[en]: the coffee is very good.
  gloss[es]: el café es muy bueno.
  gloss[de]: der Kaffee ist sehr gut.

> Der Kaffee ist nicht schlecht.
  language : de  (p=0.948)
  sentiment: positive  (score=+0.492)
  gloss[en]: the coffee is not bad.
  gloss[fr]: le café est pas mauvais.
  gloss[es]: el café es no malo.
```

The "gloss" lines are word-order-preserving lexical substitutions, not fluent
machine translation. They show that each token is landing on the right shared
concept; they do not implement target-language syntax, agreement, elision, or
word order.

### 5.5 External benchmark checks

After freezing the model I added an inference-only benchmark harness,
`polyglot0/benchmarks.py`. It downloads public evaluation data, runs the model
unchanged, and records the results in `benchmark_results.txt`. No benchmark
labels were used to construct or edit any weight, and no threshold was tuned on
the test sets.

| Benchmark | n | Metric | Polyglot-0 | Trivial baseline |
|---|---:|---|---:|---:|
| SST-2 validation (GLUE, binary) | 872 | accuracy / macro-F1 | **0.514 / 0.435** | majority 0.509 / all-negative F1 0.500 |
| SST-2 test, 1,821-example split | 1,821 | accuracy / macro-F1 | **0.499 / 0.407** | majority 0.501 / all-negative F1 0.500 |
| SST-2 validation, examples with a known sentiment word (diagnostic) | 168 | accuracy | **0.667** | subset majority 0.548 |
| Language ID, 4-language subset of `papluca/language-identification` | 2,000 | accuracy / macro-F1 | **0.986 / 0.986** | chance 0.250 |
| Multilingual sentiment (SemEval/DEFT/InterTASS/SB10k) | 3,480 | accuracy / macro-F1 | **0.381 / 0.290** | majority 0.333 / all-neutral F1 0.167 |

**SST-2.** On the 872-example validation split the model scores 0.514, only
0.46 percentage points above the majority baseline, and its macro-F1 is below
the all-negative baseline. On the 1,821-example test split used for the Llama
comparison in Section 5.6, it scores 0.499, *below* the 0.501 majority baseline,
with macro-F1 0.407. 83.8% of test examples receive an exact-zero sentiment
score, the model predicts negative for 1,626/1,821 examples (89.3%), and on the
297 test examples (16.3%) containing at least one recognized sentiment word the
accuracy is 0.576 versus a 0.586 subset majority. That diagnostic subset does
not rescue the result. Open-vocabulary movie-review sentiment is outside this
model's competence.

**Language ID.** The four-way result is genuinely strong: 0.986 accuracy over
2,000 examples, versus 0.250 chance. The model gets 497/500 English, 495/500
French, 490/500 Spanish and 490/500 German. The 28 errors are mostly very short
texts such as product phrases with too few recognized function words. This is a
legitimate 4-way closed-label task, not a score on the full 20-language dataset;
the model cannot output the other 16 languages.

**Multilingual sentiment.** Overall accuracy is 0.381, but 86.6% of predictions
are neutral. Macro-F1 is 0.290, which is above the all-neutral baseline of 0.167
but still low. Per language: English 0.408, French 0.344, Spanish 0.393, German
0.378. This is a little better than the neutral prior on accuracy and still a
poor sentiment classifier on open text.

**Not applicable.** MMLU, ARC, HellaSwag, GSM8K, HumanEval, TriviaQA and similar
benchmarks cannot be scored by this model. It has no language-modeling or
generation head, no multiple-choice scorer, and no world knowledge. Giving them
a number would require inventing a new task adapter, not evaluating Polyglot-0.
The same applies to XNLI: the model has no natural-language-inference head.

The external check makes the boundary visible. The circuit transfers well when
the task is a discrete feature-detection problem over its vocabulary (language
ID), and barely transfers when the task requires open-vocabulary semantics
(sentiment). That is exactly what the architecture predicts.

### 5.6 Head-to-head with Llama 3.2 3B and Llama 3.1 8B

First, a naming correction: **there is no 8B Llama 3.2**. The Llama 3.2 text
models are 1B (1.23B) and 3B (3.21B); the 8B model is **Llama 3.1 8B**. The
comparison below therefore uses Llama 3.2 3B and Llama 3.1 8B.

| Benchmark | Polyglot-0 | Llama 3.2 3B Instruct | Llama 3.1 8B Instruct | Notes |
|---|---:|---:|---:|---|
| SST-2 test (1,821), accuracy | **0.499** | **0.7759** | **0.9341** | zero-shot Llama, same 1,821 examples |
| SST-2 test, macro-F1 | **0.407** | not reported | not reported | |
| MMLU English (5-shot) | N/A | 63.4 | 69.4 | Polyglot-0 has no MMLU interface |
| MMLU French (5-shot) | N/A | 54.59 | 62.34 | Polyglot-0 has no MMLU interface |
| MMLU Spanish (5-shot) | N/A | 55.09 | 62.45 | Polyglot-0 has no MMLU interface |
| MMLU German (5-shot) | N/A | 53.29 | 60.59 | Polyglot-0 has no MMLU interface |
| Language ID, 4-way subset | **0.986** | not published | not published | no published Llama score on this exact subset |
| Multilingual sentiment, 3-way | **0.381** | not published | not published | no published Llama score on this exact test |

Sources: the zero-shot SST-2 numbers are from *LLaMAs Have Feelings Too*
(arXiv:2505.16491v2, Table 3), which evaluates the instruction-tuned Llama 3.2
3B and Llama 3.1 8B on the 1,821-example SST-2 test split. The MMLU numbers are
from Meta's official Llama 3.2 3B and Llama 3.1 8B model cards. Polyglot-0's
SST-2 test score is the same frozen model, scored on the same 1,821 sentences;
labels were attached from the labeled SetFit copy of SST-2 after normalizing
tokenization and five British/American spelling differences, because the GLUE
test parquet ships with `label=-1`.

Source URLs:
- SST-2 zero-shot Llama numbers: <https://arxiv.org/abs/2505.16491>
- Llama 3.2 3B model card: <https://huggingface.co/meta-llama/Llama-3.2-3B-Instruct>
- Llama 3.1 8B model card: <https://huggingface.co/meta-llama/Llama-3.1-8B-Instruct>

**SST-2 is the only benchmark with published numbers for both models on the
same split.** Polyglot-0 loses by 27.7 points to Llama 3.2 3B and by 43.5
points to Llama 3.1 8B. The gap is large in absolute terms, and even Llama 3.2
3B's chain-of-thought score (0.9154) is far above Polyglot-0. This is not a
close result.

**MMLU cannot be scored for Polyglot-0.** It has no multiple-choice or
generation interface, so the Llama MMLU numbers are context, not a head-to-head.
They show what the trained models can do on the same four languages; Polyglot-0
cannot attempt the task at all.

**Language ID is the one place Polyglot-0 looks competitive.** Its 98.6%
four-way result is a real open-text result, but it is a narrow closed-label task
with only four possible outputs. No published Llama number exists for this exact
2,000-example subset, and Llama is also expected to be strong on language ID, so
there is no evidence that Polyglot-0 wins. A fair claim is only "competitive on
this narrow task, with no measured Llama head-to-head."

**Multilingual sentiment is not a win either.** Polyglot-0 is at 38.1% with
86.6% neutral predictions. Its macro-F1 is 0.290, above the all-neutral baseline
of 0.167 but still low. The exact 3,480-example test has no published Llama
number, so it is not a head-to-head, but the model's neutral collapse makes a
Llama win very likely. The honest statement is "unmeasured, and Polyglot-0's
absolute quality is poor."

**Where Polyglot-0 does win.** It is 1.68M parameters (117,931 non-zero), runs on
CPU, is fully inspectable, and was built without gradient descent, a loss, an
optimizer, or a training corpus. Those are real advantages in interpretability,
cost, and WSP research. They are not accuracy wins. On the benchmarks that can
be compared, **Polyglot-0 does not beat Llama 3.2 3B or Llama 3.1 8B.**

## 6. What this demonstrates

1. **A multilingual circuit can be written, not trained.** A shared orthogonal
   concept space plus four explicit attention heads is enough for language ID,
   lexical translation, retrieval, and sentiment on a closed domain.
2. **Behavior is localized and editable.** Adding a negation gate changes
   negated sentiment from 0% to 100% while leaving unrelated tasks untouched.
   Adding one intensity weight changes the intensity MAE from 0.185 to 0.003.
3. **Weight space can be interpretable.** The embedding layout maps directly to
   concepts, polarity, flags and language; the attention heads are named
   circuits; the sentiment head is a two-line equation.
4. **The specification is the hard part.** The model is simple because the
   lexicon and feature design already solve the semantic problem. The network
   is mostly a routing and pooling mechanism for hand-specified features.
5. **External generalization is task-dependent.** The hand-written language
   circuit reaches 98.6% on a 4-way open-text language-ID set, while the same
   model is below the majority baseline on the 1,821-example SST-2 test split
   (0.499) and only 38.1% on a four-language sentiment test. The circuit
   transfers when the task is covered by its discrete features; it does not
   transfer to open-vocabulary semantics. It also does not beat Llama 3.2 3B or
   Llama 3.1 8B on the one shared benchmark with published numbers for both
   (SST-2).

The fourth point is the main lesson for WSP at larger scale. Direct weight
editing is most credible when the target behavior can be expressed as a
local read/write circuit over features the model already has. It is much less
clear how to write a circuit for knowledge that is distributed, polysemous, or
requires multi-step reasoning.

## 7. Limitations

This is a small proof of concept, and the following caveats matter:

- **Closed vocabulary.** 911 token ids. Unknown words become `[UNK]`; there is
  no subword model, morphology, spelling generalization, or open-vocabulary
  handling.
- **No syntax.** The head circuits read lexical features, not word order,
  dependencies, or phrase structure. The encoder is effectively a bag of words
  for these tasks. It cannot distinguish "the dog bit the man" from "the man bit
  the dog" unless the content words differ.
- **Global negation and intensity.** A single negator or intensifier affects the
  whole sentence. The model does not model scope, as in "not all the food is
  good" or "the coffee is good but not cheap."
- **Template-bound internal evaluation.** The 1,372-sentence stress set and the
  120-sentence curated set are hand-authored from the same specification. A
  score of 100% there is a specification check, not evidence of
  natural-language generalization. The external checks in Sections 5.5-5.6
  show the gap: language ID remains strong, but open-vocabulary sentiment is at
  or below the majority baseline, and the model loses the only exact
  head-to-head (SST-2) to Llama 3.2 3B and Llama 3.1 8B. There is still no
  standard LLM benchmark score, because the model cannot perform those tasks.
- **Translation is exact by construction.** Every language uses the same concept
  code, so word translation and retrieval mostly test whether the routing works.
  They do not show that the model discovered a cross-lingual representation.
- **Sentiment is hand-labeled.** Polarity values are author-assigned and
  context-free: "cheap" is mildly positive, "expensive" mildly negative, "cold"
  negative. Real sentiment depends on context, domain, negation scope, and
  culture.
- **No generation.** The model classifies and retrieves. The gloss is a
  post-hoc token lookup, not a decoder. There is no language modeling head, no
  fluency objective, and no conversational ability.
- **Tiny scale and one layer.** Four languages, 91 concepts, one attention
  layer, no MLP blocks, no layer normalization. The result does not establish
  that the same method scales to a general-purpose model.
- **Weak control baseline.** The random-weight control is useful but not a
  substitute for a trained baseline of comparable size. A trained model could
  be added to test whether hand-programming is competitive on this domain, but
  that is outside this experiment.
- **The "two weights" framing needs context.** The two edits are meaningful
  because the rest of the circuit already exists. They are not two weights from
  scratch; they are the last two coefficients needed to activate features that
  the embedding and attention wiring already provide. The gradient-free build
  still required the full lexicon, architecture and wiring code.

## 8. Reproduce

From `C:\Users\sao\Documents\dofus-emu\NLP`:

```powershell
# Consistency checks: vocabulary, corpus, concept coverage
& "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe" -m polyglot0.validate

# Full internal benchmark, ablations, edit experiments, and example glosses
$env:PYTHONIOENCODING="utf-8"
& "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe" -m polyglot0.eval

# External benchmark harness (downloads public evaluation data on first run)
$env:PYTHONIOENCODING="utf-8"
& "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe" -m polyglot0.benchmarks

# Fixed demo
$env:PYTHONIOENCODING="utf-8"
& "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe" -m polyglot0.demo --demo

# Interactive demo (empty line quits)
$env:PYTHONIOENCODING="utf-8"
& "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe" -m polyglot0.demo
```

Dependencies: Python 3.14 and PyTorch (CPU is sufficient) for the model. The
external benchmark harness additionally uses `pandas` and `pyarrow` to read the
public CSV/parquet files; it downloads them into `data/` on first run. The
SST-2 test score attaches labels from the labeled SetFit copy of the same 1,821
sentences, because the GLUE test parquet stores `label=-1`. The model itself
needs no pretrained artifacts or downloads. Benchmark data is used for
evaluation only.

## 9. Files

| File | Purpose |
|---|---|
| `polyglot0/lexicon.py` | 91-concept table, parallel corpus, extra forms |
| `polyglot0/tokenizer.py` | normalization, tokenization, vocabulary, accent aliases |
| `polyglot0/model.py` | hand-written weight builder and encoder |
| `polyglot0/eval.py` | internal benchmark, ablations, edit experiments, lexical gloss |
| `polyglot0/benchmarks.py` | external benchmark harness (SST-2 validation + 1,821-example test, language ID, multilingual sentiment) |
| `polyglot0/demo.py` | CLI demo |
| `polyglot0/validate.py` | corpus/vocabulary consistency checks |
| `polyglot0/smoke.py` | early circuit smoke test |
| `results.txt` | captured output of the internal evaluation |
| `benchmark_results.txt` | captured output of the external benchmark checks |
| `data/` | downloaded public benchmark data (evaluation only) |

## 10. Bottom line

The experiment succeeded as a WSP mechanics test: a four-language NLP circuit
was written directly into weights, it behaves as specified, its parts can be
ablated, and two targeted parameter changes add two missing capabilities
without any training.

The external checks show the boundary clearly. Language ID transfers to
open-text data at 98.6% on a four-language subset, but open-vocabulary sentiment
does not: 51.4% on the 872-example SST-2 validation split (majority 50.9%) and
49.9% on the 1,821-example test split (majority 50.1%), plus 38.1% on a
four-language sentiment test (majority 33.3%, macro-F1 0.290 versus an
all-neutral baseline of 0.167). On the one shared split with published numbers
for both models, SST-2 test, Polyglot-0 loses to Llama 3.2 3B (77.6%) and Llama
3.1 8B (93.4%); there is no 8B Llama 3.2. This is not a general-purpose NLP
model, and it cannot be scored on MMLU/GSM8K/HumanEval-style benchmarks at all.

The honest summary is: **weight-space programming is a powerful way to build and
edit small, well-specified circuits; the open question is how far that approach
extends when the required features and reasoning are not already hand-designed.**
