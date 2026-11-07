# -*- coding: utf-8 -*-
"""Quick smoke test: does the hand-written circuit do what the layout says?"""

import torch
import torch.nn.functional as F

from . import lexicon
from .model import build_model, CONCEPT_DIM, POL, NEG, INT, LANG, canonical_forms
from .tokenizer import encode

torch.set_printoptions(precision=3, sci_mode=False)
m = build_model()

print("=== per-sentence readout ===")
for sent in lexicon.CORPUS[:4]:
    for lang in lexicon.LANGS:
        ids = encode(sent["text"][lang], m.stoi)
        out = m.encode([ids])
        pred_lang = lexicon.LANGS[int(out["lang_logits"].argmax(-1))]
        pred_sent = ["neg", "neu", "pos"][int(out["sent_logits"].argmax(-1))]
        print(f"{sent['id']} {lang}: lang={pred_lang} sent={pred_sent} "
              f"score={out['sent_score'].item():+.3f} "
              f"cls_pol={out['cls'][0, POL].item():+.3f} "
              f"neg={out['cls'][0, NEG].item():.3f} "
              f"int={out['cls'][0, INT].item():.3f}")

print("\n=== word translation (concept top-1) ===")
forms = canonical_forms()
# target concept matrix per language
tgt = {}
for lang in lexicon.LANGS:
    rows, cids = [], []
    for cid in m.concept_ids:
        form = forms[cid].get(lang)
        if form and form in m.stoi:
            rows.append(m.embed.weight[m.stoi[form], :CONCEPT_DIM])
            cids.append(cid)
    tgt[lang] = (F.normalize(torch.stack(rows), dim=-1), cids)

total = correct = 0
for src in lexicon.LANGS:
    for dst in lexicon.LANGS:
        if src == dst:
            continue
        mat, cids = tgt[dst]
        for cid in m.concept_ids:
            form = forms[cid].get(src)
            dform = forms[cid].get(dst)
            if not form or not dform or form not in m.stoi:
                continue
            q = F.normalize(m.embed.weight[m.stoi[form], :CONCEPT_DIM], dim=0)
            pred = cids[int((mat @ q).argmax())]
            total += 1
            correct += int(pred == cid)
print(f"{correct}/{total} = {correct/total:.3%}")

print("\n=== 30-way cross-lingual sentence retrieval ===")
vecs, keys = [], []
for sent in lexicon.CORPUS:
    for lang in lexicon.LANGS:
        ids = encode(sent["text"][lang], m.stoi)
        out = m.encode([ids])
        c = out["cls"][0]
        v = torch.cat([c[:CONCEPT_DIM], 2 * c[POL:POL + 1],
                       2 * c[NEG:NEG + 1], c[INT:INT + 1]])
        vecs.append(F.normalize(v, dim=0))
        keys.append((sent["id"], lang))
V = torch.stack(vecs)
for src in [0, 1, 2]:
    scores = V @ V[src]
    scores[src] = -9
    best = int(scores.argmax())
    print(f"{keys[src]} -> {keys[best]}  sim={scores[best]:.3f}")
