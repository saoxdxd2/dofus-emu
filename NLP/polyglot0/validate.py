# -*- coding: utf-8 -*-
"""Sanity checks for the hand-written lexicon and corpus."""

from collections import Counter

from . import lexicon
from .tokenizer import build_vocab, tokenize

itos, stoi, form_to_concept, concept_to_row, form_langs = build_vocab()
concept_forms = {c[0]: c[4:] for c in lexicon.CONCEPTS}
print(f"vocab={len(itos)}  unique forms={len(form_to_concept)}  concepts={len(concept_to_row)}")

errors = []
for sent in lexicon.CORPUS:
    for cid in sent["concepts"]:
        if cid not in concept_to_row:
            errors.append(f"{sent['id']}: unknown gold concept {cid!r}")
            continue
        for lang in lexicon.LANGS:
            if not concept_forms[cid][lexicon.LANG_INDEX[lang]]:
                errors.append(f"{sent['id']}: concept {cid!r} has no {lang} form")
    for lang in lexicon.LANGS:
        toks = tokenize(sent["text"][lang])
        unknown = [t for t in toks if t not in stoi]
        if unknown:
            errors.append(f"{sent['id']}/{lang}: unknown tokens {unknown}")
        got = Counter(
            form_to_concept[t]
            for t in toks
            if t in form_to_concept and concept_to_row[form_to_concept[t]][2]
        )
        want = Counter(sent["concepts"])
        if got != want:
            errors.append(f"{sent['id']}/{lang}: content concepts {dict(got)} != {dict(want)}")

if errors:
    print(f"\n{len(errors)} ERROR(S):")
    for e in errors:
        print(" -", e)
    raise SystemExit(1)

print("OK: every corpus token is known and every language has matching content concepts.")
