# -*- coding: utf-8 -*-
"""Benchmark and weight-edit experiments for Polyglot-0."""

from collections import Counter, defaultdict

import torch
import torch.nn.functional as F

from . import lexicon
from .model import (
    build_model, canonical_forms, CONCEPT_DIM, POL, NEG, INT,
    D_MODEL, HEAD_DIM, NEG as NEG_IDX,
)
from .tokenizer import encode, tokenize

# ---------------------------------------------------------------------------
# held-out compositional sentiment set
# ---------------------------------------------------------------------------

NOUNS = ["coffee", "cheese", "wine", "fish", "film", "day", "friend"]
POS_ADJ = ["good", "beautiful", "happy", "delicious", "interesting", "fast", "easy", "cheap"]
NEG_ADJ = ["bad", "ugly", "sad", "awful", "boring", "slow", "difficult", "expensive"]

ART = {"en": "The", "fr": "Le", "es": "El", "de": "Der"}
BE = {"en": "is", "fr": "est", "es": "es", "de": "ist"}
NOT = {"en": "not", "fr": "pas", "es": "no", "de": "nicht"}
VERY = {"en": "very", "fr": "très", "es": "muy", "de": "sehr"}
HERE = {"en": "here", "fr": "ici", "es": "aquí", "de": "hier"}


def canonical_display():
    out = {}
    for row in lexicon.CONCEPTS:
        cid, *rest = row
        forms = rest[3:]
        out[cid] = {
            lang: field.split(",")[0].strip()
            for lang, field in zip(lexicon.LANGS, forms) if field
        }
    return out


DISPLAY = canonical_display()
POLARITY = {c[0]: c[2] for c in lexicon.CONCEPTS}


def make_sentence(lang, noun, adj=None, negated=False, intensified=False, neutral=False):
    art = ART[lang]
    be = BE[lang]
    n = DISPLAY[noun][lang]
    if neutral:
        return f"{art} {n} {be} {HERE[lang]}."
    a = DISPLAY[adj][lang]
    if lang == "fr":
        if negated:
            return f"{art} {n} n'est {NOT[lang]} {a}."
        if intensified:
            return f"{art} {n} {be} {VERY[lang]} {a}."
        return f"{art} {n} {be} {a}."
    if negated:
        return f"{art} {n} {be} {NOT[lang]} {a}."
    if intensified:
        return f"{art} {n} {be} {VERY[lang]} {a}."
    return f"{art} {n} {be} {a}."


def stress_data():
    data = []
    for lang in lexicon.LANGS:
        for noun in NOUNS:
            data.append(dict(text=make_sentence(lang, noun, neutral=True),
                             lang=lang, y="neu", score=0.0, cond="neutral",
                             noun=noun, adj=None))
            for adj in POS_ADJ + NEG_ADJ:
                p = POLARITY[adj]
                for cond, negated, intensified in [
                    ("plain", False, False),
                    ("negated", True, False),
                    ("intensified", False, True),
                ]:
                    score = (p / 2.0) * (-1.0 if negated else 1.0) * (1.5 if intensified else 1.0)
                    y = "pos" if score > 0.10 else ("neg" if score < -0.10 else "neu")
                    data.append(dict(
                        text=make_sentence(lang, noun, adj, negated, intensified),
                        lang=lang, y=y, score=score, cond=cond, noun=noun, adj=adj,
                    ))
    return data


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------

def encode_texts(model, texts):
    ids = [encode(t, model.stoi) for t in texts]
    return model.encode(ids)


def lang_accuracy(model, data):
    out = encode_texts(model, [d["text"] for d in data])
    pred = out["lang_logits"].argmax(-1).tolist()
    gold = [lexicon.LANG_INDEX[d["lang"]] for d in data]
    acc = sum(int(p == g) for p, g in zip(pred, gold)) / len(gold)
    per = defaultdict(lambda: [0, 0])
    for p, g in zip(pred, gold):
        per[g][0] += int(p == g)
        per[g][1] += 1
    return acc, {lexicon.LANGS[k]: v[0] / v[1] for k, v in per.items()}


def sentiment_metrics(model, data, subset=None):
    if subset is not None:
        data = [d for d in data if subset(d)]
    out = encode_texts(model, [d["text"] for d in data])
    logits = out["sent_logits"]
    pred = logits.argmax(-1).tolist()
    gold = [{"neg": 0, "neu": 1, "pos": 2}[d["y"]] for d in data]
    acc = sum(int(p == g) for p, g in zip(pred, gold)) / len(gold)
    mae = float((out["sent_score"] - torch.tensor([d["score"] for d in data])).abs().mean())
    f1s = []
    for c in range(3):
        tp = sum(1 for p, g in zip(pred, gold) if p == c and g == c)
        fp = sum(1 for p, g in zip(pred, gold) if p == c and g != c)
        fn = sum(1 for p, g in zip(pred, gold) if p != c and g == c)
        f1s.append(0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn))
    return dict(acc=acc, mae=mae, macro_f1=sum(f1s) / 3, n=len(data),
                pred=Counter(pred), gold=Counter(gold))


def word_translation(model):
    forms = canonical_forms()
    tgt = {}
    for lang in lexicon.LANGS:
        rows, cids = [], []
        for cid in model.concept_ids:
            form = forms[cid].get(lang)
            if form and form in model.stoi:
                rows.append(model.embed.weight[model.stoi[form], :CONCEPT_DIM])
                cids.append(cid)
        tgt[lang] = (F.normalize(torch.stack(rows), dim=-1), cids)

    total = correct = exact = 0
    by_pair = defaultdict(lambda: [0, 0])
    for src in lexicon.LANGS:
        for dst in lexicon.LANGS:
            if src == dst:
                continue
            mat, cids = tgt[dst]
            for cid in model.concept_ids:
                sform = forms[cid].get(src)
                dform = forms[cid].get(dst)
                if not sform or not dform or sform not in model.stoi:
                    continue
                q = F.normalize(model.embed.weight[model.stoi[sform], :CONCEPT_DIM], dim=0)
                pred_i = int((mat @ q).argmax())
                ok = cids[pred_i] == cid
                total += 1
                correct += int(ok)
                exact += int(ok)
                by_pair[(src, dst)][0] += int(ok)
                by_pair[(src, dst)][1] += 1
    return correct / total, exact / total, total, {f"{a}->{b}": v[0] / v[1] for (a, b), v in by_pair.items()}


def retrieval_vectors(model, sentences):
    texts = [s["text"][lang] for s in sentences for lang in lexicon.LANGS]
    out = encode_texts(model, texts)
    c = out["cls"]
    v = torch.cat([
        c[:, :CONCEPT_DIM],
        2.0 * c[:, POL:POL + 1],
        2.0 * c[:, NEG:NEG + 1],
        1.0 * c[:, INT:INT + 1],
    ], dim=-1)
    return F.normalize(v, dim=-1), [(s["id"], lang) for s in sentences for lang in lexicon.LANGS]


def retrieval_accuracy(model, sentences):
    V, keys = retrieval_vectors(model, sentences)
    correct = total = 0
    ranks = []
    for i, (sid, slang) in enumerate(keys):
        for dst in lexicon.LANGS:
            if dst == slang:
                continue
            cand = [j for j, (s2, l2) in enumerate(keys) if l2 == dst]
            scores = V[cand] @ V[i]
            order = scores.argsort(descending=True).tolist()
            pred_sid = keys[cand[order[0]]][0]
            correct += int(pred_sid == sid)
            total += 1
            rank = 1 + next(k for k, oi in enumerate(order) if keys[cand[oi]][0] == sid)
            ranks.append(rank)
    return correct / total, sum(ranks) / len(ranks), total


PUNCT_CHARS = set(".,!?;:¿¡\"()[]")


def semantic_vector(model, text):
    out = encode_texts(model, [text])
    c = out["cls"][0]
    v = torch.cat([
        c[:CONCEPT_DIM],
        2.0 * c[POL:POL + 1],
        2.0 * c[NEG:NEG + 1],
        1.0 * c[INT:INT + 1],
    ])
    return F.normalize(v, dim=0)


def translate_text(model, text, target_lang):
    """Per-token lexical translation: source token -> shared concept ->
    nearest target-language canonical form.  The mapping lives in the
    embedding weights; this is a word-order-preserving gloss, not fluent MT."""
    rows, cids = [], []
    forms = canonical_forms()
    for cid in model.concept_ids:
        form = forms[cid].get(target_lang)
        if form and form in model.stoi:
            rows.append(model.embed.weight[model.stoi[form], :CONCEPT_DIM])
            cids.append(cid)
    mat = F.normalize(torch.stack(rows), dim=-1)
    toks = tokenize(text)
    # French marks negation twice (ne ... pas).  For a lexical gloss, keep
    # only the meaningful post-verbal negator; otherwise the target sentence
    # would contain two copies of "not".
    french_double_neg = any(
        t in ("pas", "jamais", "plus", "rien", "personne") for t in toks
    )
    out = []
    for tok in toks:
        if tok in PUNCT_CHARS:
            out.append(tok)
            continue
        if tok not in model.stoi:
            continue
        if french_double_neg and tok in ("ne", "n'"):
            continue
        q = F.normalize(model.embed.weight[model.stoi[tok], :CONCEPT_DIM], dim=0)
        pred = cids[int((mat @ q).argmax())]
        form = DISPLAY[pred].get(target_lang)
        if form:
            out.append(form)
    s = " ".join(out)
    for p in PUNCT_CHARS:
        s = s.replace(" " + p, p)
    return s


# ---------------------------------------------------------------------------
# experiments
# ---------------------------------------------------------------------------

def evaluate(model, data, curated, label):
    curated_flat = []
    for s in curated:
        toks = tokenize(s["text"]["en"])
        neg = any(t in ("not", "never", "n't") for t in toks)
        inten = any(t in ("very", "really", "so") for t in toks)
        p = sum(POLARITY[c] for c in s["concepts"]) / len(s["concepts"])
        score = p * (-1.0 if neg else 1.0) * (1.5 if inten else 1.0)
        for lang in lexicon.LANGS:
            curated_flat.append(dict(text=s["text"][lang], lang=lang, y=s["y"], score=score))
    lang_acc, per_lang = lang_accuracy(model, data + curated_flat)
    sent_all = sentiment_metrics(model, data)
    sent_plain = sentiment_metrics(model, data, lambda d: d["cond"] in ("plain", "neutral"))
    sent_neg = sentiment_metrics(model, data, lambda d: d["cond"] == "negated")
    sent_int = sentiment_metrics(model, data, lambda d: d["cond"] == "intensified")
    cur = sentiment_metrics(model, curated_flat)
    wt_concept, wt_exact, wt_n, _ = word_translation(model)
    ret, mean_rank, ret_n = retrieval_accuracy(model, curated)
    print(f"\n[{label}]")
    print(f"  language ID       {lang_acc:.3f}  ({', '.join(f'{k}={v:.3f}' for k, v in per_lang.items())})")
    print(f"  sentiment all     acc={sent_all['acc']:.3f} f1={sent_all['macro_f1']:.3f} mae={sent_all['mae']:.3f}")
    print(f"    plain+neutral   acc={sent_plain['acc']:.3f} mae={sent_plain['mae']:.3f}")
    print(f"    negated         acc={sent_neg['acc']:.3f} mae={sent_neg['mae']:.3f}")
    print(f"    intensified     acc={sent_int['acc']:.3f} mae={sent_int['mae']:.3f}")
    print(f"  curated human     acc={cur['acc']:.3f} f1={cur['macro_f1']:.3f} mae={cur['mae']:.3f}")
    print(f"  word translation  concept={wt_concept:.3f} exact={wt_exact:.3f} (n={wt_n})")
    print(f"  sentence retrieval acc={ret:.3f} mean_rank={mean_rank:.2f} (n={ret_n})")
    return dict(lang=lang_acc, sent=sent_all, plain=sent_plain, neg=sent_neg, inten=sent_int,
                curated=cur, wt=wt_concept, wt_exact=wt_exact, ret=ret, rank=mean_rank)


def set_gate(model, enabled):
    with torch.no_grad():
        model.sent_scale.weight.copy_(
            torch.tensor([[-2.0, 1.0]]) if enabled else torch.tensor([[0.0, 1.0]])
        )


def set_intensity(model, enabled):
    with torch.no_grad():
        model.sent_boost.weight.fill_(0.5 if enabled else 0.0)


def zero_neg_head(model):
    with torch.no_grad():
        model.attn.in_proj_weight[D_MODEL + 2 * HEAD_DIM, NEG_IDX] = 0.0
        model.attn.in_proj_weight[2 * D_MODEL + 2 * HEAD_DIM, NEG_IDX] = 0.0
        model.attn.out_proj.weight[NEG_IDX, 2 * HEAD_DIM] = 0.0


def randomized(model):
    with torch.no_grad():
        for name, p in model.named_parameters():
            if name == "embed.weight":
                p.normal_(0.0, 0.02)
                p[0].zero_()
            else:
                p.normal_(0.0, 0.02)
        # Remove the shared [CLS] constant so retrieval is not helped by
        # a common residual vector / length artefact.
        model.embed.weight[model.stoi["[CLS]"]].zero_()
    return model


def main():
    data = stress_data()
    curated = lexicon.CORPUS
    torch.manual_seed(0)

    base = build_model()
    n_params = sum(p.numel() for p in base.parameters())
    print(f"Polyglot-0 parameters: {n_params:,}")
    print(f"held-out stress set: {len(data)} sentences "
          f"({len(data)//4} per language); curated: {len(curated)*4}")

    results = {}
    results["final"] = evaluate(base, data, curated, "hand-written final")

    v0 = build_model()
    set_gate(v0, False)
    set_intensity(v0, False)
    results["v0"] = evaluate(v0, data, curated, "v0: no negation gate, no intensity")

    v1 = build_model()
    set_gate(v1, True)
    set_intensity(v1, False)
    results["v1"] = evaluate(v1, data, curated, "edit 1: +1 negation weight")

    v2 = build_model()
    set_gate(v2, True)
    set_intensity(v2, True)
    results["v2"] = evaluate(v2, data, curated, "edit 2: +1 intensity weight")

    ab = build_model()
    zero_neg_head(ab)
    results["no_head"] = evaluate(ab, data, curated, "ablation: negation head zeroed")

    rnd = randomized(build_model())
    results["random"] = evaluate(rnd, data, curated, "control: random weights")

    print("\n=== edit summary (sentiment stress set) ===")
    for k in ["v0", "v1", "v2"]:
        r = results[k]
        print(f"{k:>3}: all={r['sent']['acc']:.3f}  plain={r['plain']['acc']:.3f}  "
              f"neg={r['neg']['acc']:.3f}  int={r['inten']['acc']:.3f}  "
              f"mae_int={r['inten']['mae']:.3f}")

    print("\n=== targeted-edit footprint ===")
    edited = 1 + 1
    print(f"edited {edited} / {n_params:,} weights = {edited/n_params:.8%}")
    print("language ID and word translation are unchanged because those tensors were not touched.")

    print("\n=== lexical translation (word-order-preserving gloss) ===")
    for text, dst in [
        ("I like the good coffee.", "fr"),
        ("Le chat n'est pas heureux.", "en"),
        ("El perro no es feliz.", "de"),
        ("Der Kaffee ist nicht gut.", "es"),
    ]:
        print(f"  {text!r} -> [{dst}] {translate_text(base, text, dst)!r}")


if __name__ == "__main__":
    main()
