"""Rigorous Dofus Knowledge Certification Benchmark.

Acts as an objective quality gate before any distillation into THX-01 or TRM-ACT.
Tests whether the domain specialist model possesses genuine, comprehensive mastery
over Dofus mechanics:
1. Combat Stance Policy & Elemental Weakness Exploitation (using official monster stats).
2. Spatial Coordinate Routing & Official Zaap Knowledge.
3. Harvesting Resource Level Requirements & Farming Directives.
4. Moderator Threat Detection & Anti-Bot Sentinel Triage.
5. In-Game Operational Command Discrimination (Pass turn, follow, stop, join fight).

Distillation MUST NOT proceed unless the teacher achieves certified passing grade (>= 90%).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F

from .dofus_native_model import (
    DofusBrainTransformer,
    DofusDomainTokenizer,
)

logger = logging.getLogger("dofus.knowledge_benchmark")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_DIR = DATA_DIR / "cache"

INTENT_MAP = {
    "NONE": 0,
    "FOLLOW": 1,
    "AUTO_JOIN_COMBAT": 2,
    "PASS_TURN": 3,
    "AUTO_FARM": 4,
    "STOP": 5,
    "NAVIGATE_COORDS": 6,
    "NAVIGATE_ZAAP": 7,
    "FIND_RESOURCE": 8,
    "START_FARM_LOOP": 9,
    "QUEST_GUIDE": 10,
    "RESOURCE_LEVEL_REQ": 11,
}
INV_INTENT_MAP = {v: k for k, v in INTENT_MAP.items()}

STANCES = ["BURST_LETHAL", "BUFF_AND_ENGAGE", "TRAP_AND_KITE", "SHIELD_DEFENSE"]
STANCE_MAP = {s: i for i, s in enumerate(STANCES)}


@dataclass
class BenchmarkResult:
    domain_scores: Dict[str, float]
    aggregate_score: float
    total_tests: int
    passed_tests: int
    is_certified: bool
    failure_reasons: List[str] = field(default_factory=list)

    @property
    def passes_certification(self) -> bool:
        return self.is_certified

    @property
    def overall_accuracy(self) -> float:
        return self.aggregate_score / 100.0

    def print_summary(self) -> None:
        print("\n" + "=" * 65)
        print("   🏅 DOFUS TEACHER KNOWLEDGE CERTIFICATION REPORT")
        print("=" * 65)
        for domain, score in self.domain_scores.items():
            icon = "✅" if score >= 88.0 else "❌"
            print(f"  {icon} {domain:<35}: {score:.1f}%")
        print("-" * 65)
        status = "PASSED (CERTIFIED FOR DISTILLATION)" if self.is_certified else "FAILED (DISTILLATION PROHIBITED)"
        icon = "🎉" if self.is_certified else "🚫"
        print(f"  {icon} Aggregate Domain Mastery: {self.aggregate_score:.1f}% | Status: {status}")
        if self.failure_reasons:
            print("\n  ⚠️ Failure Diagnostics:")
            for r in self.failure_reasons:
                print(f"    - {r}")
        print("=" * 65 + "\n")


class DofusKnowledgeBenchmark:
    """Rigorous evaluation suite assessing domain understanding before distillation."""

    # 1. Combat Stance Matchups grounded in real game physics
    COMBAT_MATCHUPS = [
        # Lethal burst situations (Enemy HP low, player has full AP)
        {"text": "Class: Iop (Lvl 100). Enemy: Bouftou (HP 45, Weakness: FIRE). Dist: 2 cells. Player HP: 95%.", "expected_stance": "BURST_LETHAL"},
        {"text": "Class: Cra (Lvl 120). Enemy: Pious (HP 30, Weakness: AIR). Dist: 6 cells. Player HP: 100%.", "expected_stance": "BURST_LETHAL"},
        {"text": "Class: Ecaflip (Lvl 90). Enemy: Sanglier (HP 80, Weakness: EARTH). Dist: 3 cells. Player HP: 80%.", "expected_stance": "BURST_LETHAL"},
        # Multi-turn engagement with turn-1 buffing (High HP boss/tank)
        {"text": "Class: Feca (Lvl 100). Enemy: Kanigrou (HP 1200, Weakness: FIRE). Dist: 7 cells. Player HP: 85%.", "expected_stance": "BUFF_AND_ENGAGE"},
        {"text": "Class: Iop (Lvl 140). Enemy: Craqueleur (HP 1800, Weakness: WATER). Dist: 5 cells. Player HP: 90%.", "expected_stance": "BUFF_AND_ENGAGE"},
        {"text": "Class: Sacrieur (Lvl 110). Enemy: Chafer Archer (HP 950, Weakness: NEUTRAL). Dist: 8 cells. Player HP: 88%.", "expected_stance": "BUFF_AND_ENGAGE"},
        # Shield triage (Player HP critical)
        {"text": "Class: Feca (Lvl 100). Enemy: Kanigrou (HP 800, Weakness: FIRE). Dist: 1 cells. Player HP: 22%.", "expected_stance": "SHIELD_DEFENSE"},
        {"text": "Class: Eniripsa (Lvl 90). Enemy: Trool (HP 1500, Weakness: EARTH). Dist: 2 cells. Player HP: 18%.", "expected_stance": "SHIELD_DEFENSE"},
        {"text": "Class: Iop (Lvl 105). Enemy: Meulou (HP 2200, Weakness: FIRE). Dist: 1 cells. Player HP: 25%.", "expected_stance": "SHIELD_DEFENSE"},
        # Kite & Trap (Long range kiter vs lethal close-combat mob)
        {"text": "Class: Sram (Lvl 100). Enemy: Blop Coco Royal (HP 1100, Weakness: EARTH). Dist: 6 cells. Player HP: 60%.", "expected_stance": "TRAP_AND_KITE"},
        {"text": "Class: Cra (Lvl 130). Enemy: Koulosse (HP 3500, Weakness: AIR). Dist: 8 cells. Player HP: 55%.", "expected_stance": "TRAP_AND_KITE"},
    ]

    # 2. Official Zaap & Spatial Coordinates
    SPATIAL_CASES = [
        ("va au zaap d'astrub", "NAVIGATE_ZAAP"),
        ("téléporte au zaap amakna stp", "NAVIGATE_ZAAP"),
        ("prends le zaap de bonta", "NAVIGATE_ZAAP"),
        ("zaap brakmar direct", "NAVIGATE_ZAAP"),
        ("ve al zaap de otomai xfa", "NAVIGATE_ZAAP"),
        ("teleport to frigost zaap", "NAVIGATE_ZAAP"),
        ("take zaap to wabbit island", "NAVIGATE_ZAAP"),
        ("zaap sufokia rapido", "NAVIGATE_ZAAP"),
        ("va en [4, -18]", "NAVIGATE_COORDS"),
        ("go [-2, 0] stp", "NAVIGATE_COORDS"),
        ("direction [10, 22]", "NAVIGATE_COORDS"),
        ("marche vers [-31, -55]", "NAVIGATE_COORDS"),
        ("ves a [1, -24] xfa", "NAVIGATE_COORDS"),
        ("head to [5, -19] please", "NAVIGATE_COORDS"),
    ]

    # 3. Harvesting Resources & Profession Level Requirements
    RESOURCE_CASES = [
        ("où trouver du fer ?", "FIND_RESOURCE"),
        ("spot de bois de frêne", "FIND_RESOURCE"),
        ("dónde encontrar trigo ?", "FIND_RESOURCE"),
        ("where to find copper ore ?", "FIND_RESOURCE"),
        ("cherche des filons d'argent", "FIND_RESOURCE"),
        ("spot de trèfle à 5 feuilles", "FIND_RESOURCE"),
        ("farm du blé en boucle autour d'astrub", "START_FARM_LOOP"),
        ("lance la boucle de récolte de fer", "START_FARM_LOOP"),
        ("inicia ruta de farmeo de roble", "START_FARM_LOOP"),
        ("circular farm loop for barley", "START_FARM_LOOP"),
        ("boucle de farm d'ortie", "START_FARM_LOOP"),
        ("quel niveau pour miner le kobalte ?", "RESOURCE_LEVEL_REQ"),
        ("niveau paysan requis pour le seigle ?", "RESOURCE_LEVEL_REQ"),
        ("qué nivel de leñador para roble ?", "RESOURCE_LEVEL_REQ"),
        ("what level required to gather elm wood ?", "RESOURCE_LEVEL_REQ"),
        ("niveau alchimiste requis mandragore", "RESOURCE_LEVEL_REQ"),
    ]

    # 4. Quest Walkthrough Directives
    QUEST_CASES = [
        ("comment faire la quête du dofus cawotte ?", "QUEST_GUIDE"),
        ("guide papycha île d'otomaï", "QUEST_GUIDE"),
        ("étapes pour la quête dofus ocre", "QUEST_GUIDE"),
        ("comment passer le labyrinthe du koulosse ?", "QUEST_GUIDE"),
        ("guía de la misión dofus plateado", "QUEST_GUIDE"),
        ("walkthrough guide for dokoko quest", "QUEST_GUIDE"),
        ("quête panoplie skeunk papycha", "QUEST_GUIDE"),
    ]

    # 5. Operational Commands & Moderator Threat Triage
    OPERATIONAL_CASES = [
        ("passe ton tour vite", "PASS_TURN"),
        ("skip stp", "PASS_TURN"),
        ("pasa turno ya", "PASS_TURN"),
        ("pass turn please", "PASS_TURN"),
        ("rejoins le combat", "AUTO_JOIN_COMBAT"),
        ("unete a la pelea espada titila", "AUTO_JOIN_COMBAT"),
        ("join the fight quickly", "AUTO_JOIN_COMBAT"),
        ("suis moi on va dj", "FOLLOW"),
        ("sígueme vamos a levear", "FOLLOW"),
        ("follow my character", "FOLLOW"),
        ("stop tout arrête le bot", "STOP"),
        ("para el bot de inmediato", "STOP"),
        ("halt all actions right now", "STOP"),
        ("récolte toutes les ressources de la map", "AUTO_FARM"),
        ("farmea todos los recursos aqui", "AUTO_FARM"),
        ("gather all nodes on this map", "AUTO_FARM"),
        # Moderator checks & Security threats -> MUST BE 'NONE' (NO BOT TRIGGER)
        ("[Mod] Ankama: Bonjour, veuillez répondre immédiatement sous peine de sanction.", "NONE"),
        ("[Mod-HispaniA]: Responde por favor en 60 segundos.", "NONE"),
        ("[Admin]: Contrôle anti-bot en cours, veuillez répondre.", "NONE"),
        ("[Support]: Player check, confirm your presence.", "NONE"),
        # Normal player chatter -> MUST BE 'NONE' (IGNORED COMPLETELY)
        ("salut tu vends ton dofus ?", "NONE"),
        ("combien pour 100 bois de frêne ?", "NONE"),
        ("recrute team pour donjon bouftou", "NONE"),
        ("alguien vende set prespic ?", "NONE"),
        ("selling 500 wheat pm me", "NONE"),
    ]

    def __init__(self, min_passing_score: float = 90.0, certification_threshold: Optional[float] = None) -> None:
        self.certification_threshold = certification_threshold if certification_threshold is not None else min_passing_score

    @property
    def battery(self) -> List[Any]:
        return (
            self.COMBAT_MATCHUPS
            + self.SPATIAL_CASES
            + self.RESOURCE_CASES
            + self.QUEST_CASES
            + self.OPERATIONAL_CASES
        )

    def __call__(
        self,
        model: DofusBrainTransformer,
        tokenizer: DofusDomainTokenizer,
        device: Optional[torch.device] = None,
    ) -> BenchmarkResult:
        return self.evaluate_teacher(model, tokenizer, device=device, certification_threshold=self.certification_threshold)

    @classmethod
    def evaluate_teacher(
        cls,
        model: DofusBrainTransformer,
        tokenizer: DofusDomainTokenizer,
        device: Optional[torch.device] = None,
        certification_threshold: float = 90.0,
    ) -> BenchmarkResult:
        """Runs the complete certification test battery on the Teacher model."""
        if certification_threshold <= 1.0:
            certification_threshold = certification_threshold * 100.0

        if device is None:
            device = next(model.parameters()).device

        model.eval()
        scores: Dict[str, float] = {}
        failure_reasons: List[str] = []
        total_tests = 0
        passed_tests = 0

        max_len = getattr(getattr(model, "pos_emb", None), "num_embeddings", 64)

        # Domain 1: Tactical Combat Stance Reasoning
        st_correct = 0
        with torch.no_grad():
            for item in cls.COMBAT_MATCHUPS:
                input_ids = torch.tensor([tokenizer.encode(item["text"], max_length=max_len)], dtype=torch.long, device=device)
                out = model(input_ids)
                pred_idx = torch.argmax(out["policy_logits"], dim=-1).item()
                expected_idx = STANCE_MAP[item["expected_stance"]]
                if pred_idx == expected_idx:
                    st_correct += 1
                else:
                    failure_reasons.append(
                        f"Combat Stance Error: For '{item['text'][:40]}...' predicted {STANCES[pred_idx]}, expected {item['expected_stance']}"
                    )
        st_score = (st_correct / len(cls.COMBAT_MATCHUPS)) * 100
        scores["Tactical Combat Stances"] = st_score
        total_tests += len(cls.COMBAT_MATCHUPS)
        passed_tests += st_correct

        # Helper for Intent evaluation
        def eval_intent_domain(domain_name: str, test_items: List[Tuple[str, str]]) -> None:
            nonlocal total_tests, passed_tests
            correct = 0
            with torch.no_grad():
                for text, expected in test_items:
                    input_ids = torch.tensor([tokenizer.encode(text, max_length=max_len)], dtype=torch.long, device=device)
                    out = model(input_ids)
                    pred_idx = torch.argmax(out["intent_logits"], dim=-1).item()
                    expected_idx = INTENT_MAP[expected]
                    if pred_idx == expected_idx:
                        correct += 1
                    else:
                        pred_label = INV_INTENT_MAP.get(pred_idx, "UNKNOWN")
                        failure_reasons.append(
                            f"{domain_name} Error: '{text}' -> predicted {pred_label}, expected {expected}"
                        )
            dom_score = (correct / len(test_items)) * 100
            scores[domain_name] = dom_score
            total_tests += len(test_items)
            passed_tests += correct

        eval_intent_domain("Spatial Zaaps & Coordinates", cls.SPATIAL_CASES)
        eval_intent_domain("Resources & Profession Levels", cls.RESOURCE_CASES)
        eval_intent_domain("Quest Knowledge (Papycha)", cls.QUEST_CASES)
        eval_intent_domain("Operational Directives & Threats", cls.OPERATIONAL_CASES)

        agg_score = (passed_tests / total_tests) * 100
        is_certified = agg_score >= certification_threshold

        if not is_certified:
            failure_reasons.insert(
                0, f"Aggregate score ({agg_score:.1f}%) is below the certification threshold ({certification_threshold:.1f}%)."
            )

        return BenchmarkResult(
            domain_scores=scores,
            aggregate_score=agg_score,
            total_tests=total_tests,
            passed_tests=passed_tests,
            is_certified=is_certified,
            failure_reasons=failure_reasons,
        )
