"""Unit Tests for Domain-Native Dofus AI Architecture (Built From Scratch).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "dofus-network"))

import torch
from ai.dofus_native_model import (
    DofusBrainTransformer,
    DofusDomainDistiller,
    DofusDomainTokenizer,
)
from combat.recursive_reasoner import TinyRecursiveReasoner


class TestDofusNativeAI(unittest.TestCase):
    def setUp(self) -> None:
        self.tokenizer = DofusDomainTokenizer()

    def test_tokenizer_encode_decode(self) -> None:
        sample_text = "va en [4, -18] et récolte le blé avec ton iop"
        token_ids = self.tokenizer.encode(sample_text, max_length=16)
        self.assertEqual(len(token_ids), 16)
        self.assertEqual(token_ids[0], self.tokenizer.vocab["[CLS]"])

        decoded = self.tokenizer.decode(token_ids)
        self.assertIn("iop", decoded)
        self.assertIn("ble", decoded)

    def test_tokenizer_build_from_corpus(self) -> None:
        corpus = [
            "Chef de Guerre Bouftou résiste au feu",
            "Capitaine Kradoc au port de Sufokia [15, 28]",
            "Dofus Cawotte Wanald le Lenalde Terrier",
        ]
        self.tokenizer.build_vocab_from_corpus(corpus, max_vocab_size=500)
        self.assertIn("bouftou", self.tokenizer.vocab)
        self.assertIn("kradoc", self.tokenizer.vocab)
        self.assertIn("cawotte", self.tokenizer.vocab)

    def test_brain_transformer_forward_pass(self) -> None:
        vocab_size = len(self.tokenizer.vocab) + 20
        model = DofusBrainTransformer(
            vocab_size=vocab_size,
            d_model=64,
            nhead=4,
            num_layers=2,
            dim_feedforward=128,
            max_seq_len=32,
            num_policy_classes=4,
            num_intent_classes=12,
        )

        input_ids = torch.randint(0, vocab_size, (2, 16))
        out = model(input_ids)

        self.assertIn("policy_logits", out)
        self.assertIn("intent_logits", out)
        self.assertIn("value_pred", out)
        self.assertIn("cls_embedding", out)

        self.assertEqual(out["policy_logits"].shape, (2, 4))
        self.assertEqual(out["intent_logits"].shape, (2, 12))
        self.assertEqual(out["value_pred"].shape, (2, 1))

    def test_domain_self_distillation_step(self) -> None:
        vocab_size = len(self.tokenizer.vocab) + 20
        teacher = DofusBrainTransformer(
            vocab_size=vocab_size,
            d_model=64,
            nhead=4,
            num_layers=2,
            dim_feedforward=128,
            max_seq_len=32,
            num_policy_classes=4,
            num_intent_classes=12,
        )
        student = TinyRecursiveReasoner(in_dim=24, hidden_dim=64, max_recursions=4)

        token_inputs = torch.randint(0, vocab_size, (4, 16))
        state_vectors = torch.randn(4, 24)
        policy_labels = torch.tensor([0, 1, 2, 3])
        value_targets = torch.tensor([[15.0], [10.0], [12.0], [8.0]])

        loss = DofusDomainDistiller.distill_step(
            teacher_model=teacher,
            student_model=student,
            token_inputs=token_inputs,
            state_vectors=state_vectors,
            policy_labels=policy_labels,
            value_targets=value_targets,
            temperature=2.0,
            alpha=0.5,
        )

        self.assertTrue(torch.is_tensor(loss))
        self.assertGreater(loss.item(), 0)


if __name__ == "__main__":
    unittest.main()
