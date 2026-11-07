# -*- coding: utf-8 -*-
"""Interactive demo for the hand-programmed Polyglot-0 model."""

import sys

import torch
import torch.nn.functional as F

from . import lexicon
from .eval import DISPLAY, semantic_vector, translate_text
from .model import build_model
from .tokenizer import encode

SENT_NAMES = ["negative", "neutral", "positive"]


def analyze(model, text):
    ids = encode(text, model.stoi)
    known = [t for t in ids if t != model.stoi["[UNK]"]]
    if not known:
        print("  (no known tokens)")
        return
    out = model.encode([ids])
    lang_p = F.softmax(out["lang_logits"], dim=-1)[0]
    lang_i = int(lang_p.argmax())
    sent_i = int(out["sent_logits"].argmax())
    print(f"  language : {lexicon.LANGS[lang_i]}  "
          f"(p={lang_p[lang_i]:.3f})")
    print(f"  sentiment: {SENT_NAMES[sent_i]}  "
          f"(score={out['sent_score'].item():+.3f})")
    src = lexicon.LANGS[lang_i]
    for dst in lexicon.LANGS:
        if dst == src:
            continue
        gloss = translate_text(model, text, dst)
        print(f"  gloss[{dst}]: {gloss}")
    v = semantic_vector(model, text)
    print(f"  concept-vector norm={v.norm().item():.3f}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    torch.set_grad_enabled(False)
    model = build_model()
    print("Polyglot-0: a 1.7M-weight, hand-programmed 4-language model.")
    print("Tasks: language ID, sentiment (with negation/intensity), lexical glossing.")
    print("Type a sentence in English, French, Spanish or German. Empty line quits.\n")
    samples = [
        "The coffee is not good.",
        "Le café est très bon.",
        "El perro no es feliz.",
        "Der Kaffee ist nicht schlecht.",
    ]
    if "--demo" in sys.argv:
        for s in samples:
            print(f"> {s}")
            analyze(model, s)
            print()
        return
    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            break
        analyze(model, text)
        print()


if __name__ == "__main__":
    main()
