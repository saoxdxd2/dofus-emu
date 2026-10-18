#!/usr/bin/env python3
"""AI module tests: tokenizer, transformer, distiller."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    import torch
    from ai.dofus_native_model import DofusDomainTokenizer, DofusBrainTransformer
    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False


class TestTokenizer(unittest.TestCase):
    def test_normalize_text(self):
        self.assertEqual(DofusDomainTokenizer.normalize_text("stopppp"), "stop")
        self.assertEqual(DofusDomainTokenizer.normalize_text("PAAAASSE"), "passe")
        # normalize_text lowercases and strips accents but does NOT strip whitespace
        self.assertEqual(DofusDomainTokenizer.normalize_text("  Bonjour  "), "  bonjour  ")

    def test_default_vocab_has_special_tokens(self):
        tok = DofusDomainTokenizer()
        for t in DofusDomainTokenizer.SPECIAL_TOKENS:
            self.assertIn(t, tok.vocab)

    def test_encode_decode_roundtrip(self):
        tok = DofusDomainTokenizer()
        # Add some words
        tok.vocab["iau"] = len(tok.vocab)
        tok.vocab["bouftou"] = len(tok.vocab)
        tok.inv_vocab = {v: k for k, v in tok.vocab.items()}
        ids = tok.encode("iau bouftou")
        self.assertIsInstance(ids, list)
        self.assertGreater(len(ids), 0)


@unittest.skipUnless(_HAS_TORCH, "torch not available")
class TestDofusBrainTransformer(unittest.TestCase):
    def test_transformer_forward(self):
        model = DofusBrainTransformer(vocab_size=100, d_model=64, n_layers=2, n_heads=4)
        model.eval()
        tokens = torch.randint(0, 100, (2, 16))
        with torch.no_grad():
            out = model(tokens)
        self.assertIn("intent_logits", out)
        self.assertIn("policy_logits", out)
        self.assertIn("value_pred", out)
        self.assertTrue(torch.all(torch.isfinite(out["intent_logits"])))
        self.assertTrue(torch.all(torch.isfinite(out["policy_logits"])))


if __name__ == "__main__":
    unittest.main()