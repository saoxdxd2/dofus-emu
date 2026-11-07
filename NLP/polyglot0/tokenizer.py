# -*- coding: utf-8 -*-
"""Tokenisation and vocabulary builder for Polyglot-0."""

import re
import unicodedata

from . import lexicon

SPECIALS = ["[PAD]", "[UNK]", "[CLS]", "[SEP]"]
PUNCT = set(".,!?;:¿¡\"()[]")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = text.replace("’", "'").replace("‘", "'")
    text = text.lower()
    # Expand English contractions so "not" is a real token.
    contractions = {
        "don't": "do not", "doesn't": "does not", "didn't": "did not",
        "isn't": "is not", "aren't": "are not", "wasn't": "was not",
        "weren't": "were not", "can't": "can not", "won't": "will not",
        "couldn't": "could not", "shouldn't": "should not",
    }
    for k, v in contractions.items():
        text = text.replace(k, v)
    return text


def tokenize(text: str):
    text = normalize(text)
    text = re.sub(r"([.,!?;:¿¡\"()\[\]])", r" \1 ", text)
    raw = text.split()
    out = []
    for tok in raw:
        if tok == "aujourd'hui" or "'" not in tok:
            out.append(tok)
            continue
        # j'aime -> j' + aime ; n'est -> n' + est ; l'eau -> l' + eau
        parts = tok.split("'")
        for i, part in enumerate(parts):
            if i < len(parts) - 1:
                out.append(part + "'")
            elif part:
                out.append(part)
    return [t for t in out if t]


def fold(text: str) -> str:
    """Lowercase, ß->ss, strip combining accents."""
    text = text.replace("ß", "ss")
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def build_vocab():
    """Return (itos, stoi, form_to_concept, concept_to_row, form_langs)."""
    itos = list(SPECIALS)
    stoi = {s: i for i, s in enumerate(itos)}
    for p in sorted(PUNCT):
        if p not in stoi:
            stoi[p] = len(itos)
            itos.append(p)
    form_to_concept = {}
    concept_to_row = {}
    form_langs = {}

    def add_form(form, cid, lang_i):
        form = form.strip().lower()
        if not form:
            return
        if form in form_to_concept and form_to_concept[form] != cid:
            raise ValueError(
                f"surface form {form!r} maps to both "
                f"{form_to_concept[form]!r} and {cid!r}"
            )
        form_to_concept[form] = cid
        form_langs.setdefault(form, {})
        form_langs[form][lang_i] = form_langs[form].get(lang_i, 0) + 1
        if form not in stoi:
            stoi[form] = len(itos)
            itos.append(form)

    for cid, pos, pol, content, *forms in lexicon.CONCEPTS:
        concept_to_row[cid] = (pos, pol, content)
        for lang, field in zip(lexicon.LANGS, forms):
            if not field:
                continue
            lang_i = lexicon.LANG_INDEX[lang]
            for form in field.split(","):
                add_form(form, cid, lang_i)

    for lang, forms in lexicon.EXTRA_FORMS.items():
        lang_i = lexicon.LANG_INDEX[lang]
        for form, cid in forms.items():
            add_form(form, cid, lang_i)

    # Accent-insensitive aliases, only when unambiguous.
    aliases = {}
    for form, cid in list(form_to_concept.items()):
        folded = fold(form)
        if folded == form:
            continue
        if folded in form_to_concept and form_to_concept[folded] != cid:
            continue
        if folded in aliases and aliases[folded][0] != cid:
            continue
        if folded not in aliases:
            aliases[folded] = [cid, dict(form_langs[form])]
        else:
            for li, n in form_langs[form].items():
                aliases[folded][1][li] = aliases[folded][1].get(li, 0) + n
    for folded, (cid, langs) in aliases.items():
        form_to_concept[folded] = cid
        form_langs[folded] = langs
        if folded not in stoi:
            stoi[folded] = len(itos)
            itos.append(folded)

    return itos, stoi, form_to_concept, concept_to_row, form_langs


def encode(text, stoi):
    ids = []
    for tok in tokenize(text):
        ids.append(stoi.get(tok, stoi["[UNK]"]))
    return ids


if __name__ == "__main__":
    itos, stoi, f2c, c2r, fl = build_vocab()
    print(f"vocab={len(itos)} forms={len(f2c)} concepts={len(c2r)}")
