# -*- coding: utf-8 -*-
"""Evaluate Polyglot-0 on external, public benchmark data.

This is an inference-only adapter.  The model was not trained on any of these
datasets.  It has closed-vocabulary and task interfaces that do not cover
general-purpose LLM benchmarks such as MMLU or GSM8K; those are reported as
not applicable rather than given a fake score.

Run:
    python -m polyglot0.benchmarks
"""

from collections import Counter
import json
from pathlib import Path
import re
import unicodedata
import urllib.request

import pandas as pd

from . import lexicon
from .eval import PUNCT_CHARS
from .model import build_model, SENT
from .tokenizer import encode, tokenize

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

SST2_PATH = DATA_DIR / "sst2_validation.parquet"
SST2_TEST_PATH = DATA_DIR / "sst2_test.parquet"
SST2_TEST_LABELED_PATH = DATA_DIR / "sst2_test_labeled.jsonl"
LANGID_PATH = DATA_DIR / "language_id_test.csv"
MS_PATHS = {
    "en": DATA_DIR / "ms_english.csv",
    "fr": DATA_DIR / "ms_french.csv",
    "es": DATA_DIR / "ms_spanish.csv",
    "de": DATA_DIR / "ms_german.csv",
}
MS_NAMES = {"en": "english", "fr": "french", "es": "spanish", "de": "german"}

URLS = {
    SST2_PATH: (
        "https://huggingface.co/datasets/nyu-mll/glue/resolve/main/"
        "sst2/validation-00000-of-00001.parquet"
    ),
    # GLUE's test parquet supplies the official 1,821 sentences but has
    # label=-1.  The SetFit copy supplies labels for the same sentences.
    SST2_TEST_PATH: (
        "https://huggingface.co/datasets/nyu-mll/glue/resolve/main/"
        "sst2/test-00000-of-00001.parquet"
    ),
    SST2_TEST_LABELED_PATH: (
        "https://huggingface.co/datasets/SetFit/sst2/resolve/main/test.jsonl"
    ),
    LANGID_PATH: (
        "https://huggingface.co/datasets/papluca/language-identification/"
        "resolve/main/test.csv"
    ),
}
for lang, path in MS_PATHS.items():
    URLS[path] = (
        "https://raw.githubusercontent.com/tyqiangz/"
        f"multilingual-sentiment-datasets/main/data/{MS_NAMES[lang]}/test.csv"
    )


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------

def accuracy(gold, pred):
    return sum(int(g == p) for g, p in zip(gold, pred)) / len(gold)


def macro_f1(gold, pred, n_classes):
    scores = []
    for c in range(n_classes):
        tp = sum(1 for g, p in zip(gold, pred) if g == c and p == c)
        fp = sum(1 for g, p in zip(gold, pred) if g != c and p == c)
        fn = sum(1 for g, p in zip(gold, pred) if g == c and p != c)
        scores.append(0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn))
    return sum(scores) / n_classes


def confusion(gold, pred, n_classes):
    matrix = [[0] * n_classes for _ in range(n_classes)]
    for g, p in zip(gold, pred):
        matrix[g][p] += 1
    return matrix


def print_confusion(matrix, names):
    width = max(8, *(len(n) for n in names))
    print(" " * (width + 1) + " ".join(f"{n:>{width}}" for n in names))
    for name, row in zip(names, matrix):
        print(f"{name:>{width}} " + " ".join(f"{v:>{width}}" for v in row))


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------

def ensure_data():
    DATA_DIR.mkdir(exist_ok=True)
    for path, url in URLS.items():
        if path.exists():
            continue
        print(f"downloading {path.name} ...")
        urllib.request.urlretrieve(url, path)


# ---------------------------------------------------------------------------
# inference helpers
# ---------------------------------------------------------------------------

def batch_outputs(model, texts, batch_size=128):
    """Yield model outputs in batches, preserving input order."""
    for start in range(0, len(texts), batch_size):
        chunk = texts[start:start + batch_size]
        ids = [encode(text, model.stoi) for text in chunk]
        yield model.encode(ids)


def coverage(model, texts):
    """Token and sentiment-word coverage diagnostics."""
    total_tokens = known_tokens = 0
    has_sentiment = 0
    for text in texts:
        toks = tokenize(text)
        total_tokens += len(toks)
        found = False
        for tok in toks:
            if tok in PUNCT_CHARS:
                continue
            if tok in model.stoi:
                known_tokens += 1
                if float(model.embed.weight[model.stoi[tok], SENT]) > 0.5:
                    found = True
        if found:
            has_sentiment += 1
    n = len(texts)
    return {
        "token_coverage": known_tokens / total_tokens if total_tokens else 0.0,
        "sentiment_word_coverage": has_sentiment / n if n else 0.0,
        "sentences_with_sentiment_word": has_sentiment,
        "n": n,
        "known_tokens": known_tokens,
        "total_tokens": total_tokens,
    }


def score_batch(model, texts):
    scores = []
    for out in batch_outputs(model, texts):
        scores.extend(out["sent_score"].tolist())
    return scores


def lang_batch(model, texts):
    preds = []
    for out in batch_outputs(model, texts):
        preds.extend(out["lang_logits"].argmax(-1).tolist())
    return preds


# ---------------------------------------------------------------------------
# benchmarks
# ---------------------------------------------------------------------------

def _sst2_key(text):
    """Normalize an SST-2 sentence for label matching across tokenizations.

    The GLUE copy and the SetFit copy use slightly different PTB conventions
    and a handful of British/American spellings, so exact string matching
    misses 90/1,821 examples.  This key deliberately throws away punctuation
    and normalizes those spelling differences.  It is used only to attach an
    existing label to the correct sentence; no model input is changed.
    """
    text = unicodedata.normalize("NFKD", text).lower()
    for src, dst in (
        ("-lrb-", "("), ("-rrb-", ")"),
        ("-lsb-", "["), ("-rsb-", "]"),
        ("-lcb-", "{"), ("-rcb-", "}"),
    ):
        text = text.replace(src, dst)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = " ".join(text.split())
    spelling = {
        "humour": "humor",
        "learnt": "learned",
        "favourite": "favorite",
        "favour": "favor",
        "wanna": "wan na",
    }
    pattern = r"\b(" + "|".join(map(re.escape, spelling)) + r")\b"
    return re.sub(pattern, lambda m: spelling[m.group(0)], text)


def load_sst2_test():
    """Load the 1,821-example SST-2 test split with labels.

    GLUE's test parquet has label=-1.  SetFit/sst2 ships the same sentences
    with labels in a different tokenization.  Match them by ``_sst2_key`` and
    fail loudly if any sentence cannot be matched unambiguously.
    """
    df = pd.read_parquet(SST2_TEST_PATH)
    with open(SST2_TEST_LABELED_PATH, encoding="utf-8") as handle:
        labeled = [json.loads(line) for line in handle if line.strip()]

    label_by_key = {}
    for row in labeled:
        label_by_key.setdefault(_sst2_key(row["text"]), set()).add(int(row["label"]))

    texts = df["sentence"].tolist()
    gold = []
    for text in texts:
        labels = label_by_key.get(_sst2_key(text))
        if labels is None or len(labels) != 1:
            raise ValueError(f"could not match SST-2 test sentence: {text!r}")
        gold.append(next(iter(labels)))
    return texts, gold


def _report_sst2(model, title, texts, gold, note=None):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)
    if note:
        print(note)
    scores = score_batch(model, texts)

    # Binary adapter: positive iff the scalar sentiment is strictly positive.
    # A neutral score is mapped to negative here because SST-2 has no neutral
    # class; this is a fixed rule, not a tuned threshold.
    pred = [1 if s > 0.0 else 0 for s in scores]
    acc = accuracy(gold, pred)
    f1 = macro_f1(gold, pred, 2)
    cov = coverage(model, texts)
    zeros = sum(1 for s in scores if s == 0.0)

    by_gold = {}
    for label in (0, 1):
        idx = [i for i, g in enumerate(gold) if g == label]
        by_gold[label] = accuracy([gold[i] for i in idx], [pred[i] for i in idx])

    sent_idx = [
        i for i, text in enumerate(texts)
        if any(
            tok in model.stoi
            and float(model.embed.weight[model.stoi[tok], SENT]) > 0.5
            for tok in tokenize(text)
        )
    ]
    sent_acc = accuracy([gold[i] for i in sent_idx], [pred[i] for i in sent_idx]) if sent_idx else 0.0

    print(f"examples: {len(texts)}  (negative={gold.count(0)}, positive={gold.count(1)})")
    print(f"accuracy: {acc:.4f}   macro-F1: {f1:.4f}")
    print(f"majority baseline: {max(gold.count(0), gold.count(1)) / len(gold):.4f}")
    print(f"per class recall: negative={by_gold[0]:.4f}  positive={by_gold[1]:.4f}")
    print(f"known-token coverage: {cov['token_coverage']:.4f} "
          f"({cov['known_tokens']:,}/{cov['total_tokens']:,} non-punctuation tokens)")
    print(f"sentences with a known sentiment word: {cov['sentences_with_sentiment_word']}/{cov['n']} "
          f"({cov['sentiment_word_coverage']:.2%})")
    print(f"accuracy on that diagnostic subset: {sent_acc:.4f}  (n={len(sent_idx)})")
    print(f"exact-zero sentiment scores: {zeros}/{len(scores)} ({zeros/len(scores):.2%})")
    print("\nconfusion matrix (rows=gold, cols=pred):")
    print_confusion(confusion(gold, pred, 2), ["negative", "positive"])
    return dict(accuracy=acc, f1=f1, coverage=cov["token_coverage"])


def run_sst2(model):
    df = pd.read_parquet(SST2_PATH)
    texts = df["sentence"].tolist()
    gold = [int(x) for x in df["label"].tolist()]
    return _report_sst2(
        model,
        "SST-2 validation (GLUE) - binary movie-review sentiment",
        texts,
        gold,
    )


def run_sst2_test(model):
    texts, gold = load_sst2_test()
    return _report_sst2(
        model,
        "SST-2 test (1,821 examples) - exact split used for Llama comparison",
        texts,
        gold,
        note="sentences: GLUE test parquet; labels: SetFit/sst2 copy",
    )


def run_language_id(model):
    print("\n" + "=" * 78)
    print("Language identification test set (papluca/language-identification)")
    print("4-language subset: English, French, Spanish, German")
    print("=" * 78)
    df = pd.read_csv(LANGID_PATH, encoding="utf-8")
    df = df[df["labels"].isin(lexicon.LANGS)].reset_index(drop=True)
    texts = df["text"].tolist()
    gold = [lexicon.LANG_INDEX[x] for x in df["labels"].tolist()]
    pred = lang_batch(model, texts)
    acc = accuracy(gold, pred)
    f1 = macro_f1(gold, pred, 4)
    cov = coverage(model, texts)

    print(f"examples: {len(texts)}  (500 per language)")
    print(f"accuracy: {acc:.4f}   macro-F1: {f1:.4f}   chance: 0.2500")
    for i, lang in enumerate(lexicon.LANGS):
        idx = [j for j, g in enumerate(gold) if g == i]
        print(f"  {lang}: {accuracy([gold[j] for j in idx], [pred[j] for j in idx]):.4f} "
              f"(n={len(idx)})")
    print(f"known-token coverage: {cov['token_coverage']:.4f} "
          f"({cov['known_tokens']:,}/{cov['total_tokens']:,} non-punctuation tokens)")
    print("\nconfusion matrix (rows=gold, cols=pred):")
    print_confusion(confusion(gold, pred, 4), lexicon.LANGS)
    return dict(accuracy=acc, f1=f1, coverage=cov["token_coverage"])


def run_multilingual_sentiment(model):
    print("\n" + "=" * 78)
    print("Multilingual sentiment test (SemEval/DEFT/InterTASS/SB10k)")
    print("3 classes, 870 examples per language")
    print("=" * 78)
    names = ["negative", "neutral", "positive"]
    label_to_i = {"negative": 0, "neutral": 1, "positive": 2}
    all_scores, all_gold, all_pred = [], [], []
    per_language = {}

    for lang in lexicon.LANGS:
        df = pd.read_csv(MS_PATHS[lang], encoding="utf-8")
        texts = df["text"].tolist()
        gold = [label_to_i[x] for x in df["label"].tolist()]
        scores = score_batch(model, texts)
        # Native 3-way decision rule, with tau=0.10.
        pred = [2 if s > 0.10 else (0 if s < -0.10 else 1) for s in scores]
        acc = accuracy(gold, pred)
        f1 = macro_f1(gold, pred, 3)
        cov = coverage(model, texts)
        sent_idx = [
            i for i, text in enumerate(texts)
            if any(
                tok in model.stoi
                and float(model.embed.weight[model.stoi[tok], SENT]) > 0.5
                for tok in tokenize(text)
            )
        ]
        sent_acc = accuracy([gold[i] for i in sent_idx], [pred[i] for i in sent_idx]) if sent_idx else 0.0
        pred_counts = Counter(pred)
        per_language[lang] = dict(acc=acc, f1=f1, cov=cov, sent_acc=sent_acc,
                                  n_sent=len(sent_idx), pred_counts=pred_counts)
        all_scores.extend(scores)
        all_gold.extend(gold)
        all_pred.extend(pred)

        print(f"\n[{lang}]  n={len(texts)}")
        print(f"  accuracy={acc:.4f}  macro-F1={f1:.4f}  majority=0.3333")
        print(f"  predicted counts: neg={pred_counts[0]} neu={pred_counts[1]} pos={pred_counts[2]}")
        print(f"  known-token coverage={cov['token_coverage']:.4f}  "
              f"sentences with sentiment word={cov['sentences_with_sentiment_word']}/{cov['n']} "
              f"({cov['sentiment_word_coverage']:.2%})")
        print(f"  accuracy with a known sentiment word={sent_acc:.4f} (n={len(sent_idx)})")
        print("  confusion (rows=gold, cols=pred):")
        print_confusion(confusion(gold, pred, 3), names)

    overall = accuracy(all_gold, all_pred)
    overall_f1 = macro_f1(all_gold, all_pred, 3)
    all_neutral_f1 = macro_f1(all_gold, [1] * len(all_gold), 3)
    print("\n[all four languages]")
    print(f"  n={len(all_gold)}  accuracy={overall:.4f}  macro-F1={overall_f1:.4f}  "
          "majority=0.3333")
    print(f"  all-neutral baseline: accuracy=0.3333  macro-F1={all_neutral_f1:.4f}")
    return dict(accuracy=overall, f1=overall_f1)


def print_not_applicable():
    print("\n" + "=" * 78)
    print("Benchmarks that cannot be scored by this model")
    print("=" * 78)
    print("MMLU, ARC, HellaSwag, GSM8K, HumanEval, TriviaQA ...  N/A")
    print("Reason: Polyglot-0 has no language-modeling/generation head, no")
    print("multiple-choice scorer, and no world knowledge.  It has only a")
    print("4-way language head, a 3-way sentiment head, and a 91-concept")
    print("closed vocabulary.  Reporting a number on these would require")
    print("inventing a task adapter, not evaluating the model.")


def main():
    ensure_data()
    model = build_model()
    run_sst2(model)
    run_sst2_test(model)
    run_language_id(model)
    run_multilingual_sentiment(model)
    print_not_applicable()
    print("\nDone.  All scores are inference-only; no benchmark labels were used")
    print("to construct or edit any weight.")


if __name__ == "__main__":
    main()
