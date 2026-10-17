"""Teacher-Student Knowledge Distillation & Synthetic Corpus Generator.

Uses a 7B parameter Teacher LLM (e.g. Qwen/Qwen2.5-7B-Instruct or meta-llama/Meta-Llama-3-8B-Instruct)
loaded in 4-bit quantization on a free Google Colab T4 GPU (takes only ~4.5 GB VRAM) to:
1. Synthesize thousands of edge-case combat reasoning scenarios (Chain-of-Thought).
2. Generate hyper-realistic multilingual chat, moderator checks, and slang (FR, ES, EN).
3. Distill soft probability distributions (dark knowledge) into:
   - THX-01 / ModernBERT (100M params, Singleton NLP & Moderation Sentinel)
   - TRM-ACT (150k params, sub-millisecond continuous combat reasoner)
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("dofus.distillation")

DATA_DIR = Path(__file__).resolve().parent
EXPANDED_DATASET_FILE = DATA_DIR / "dofus_deep_training_corpus.json"

TEACHER_PROMPT_TEMPLATE = """You are the ultimate Grandmaster AI of Dofus Touch and Dofus 2.x.
You possess complete encyclopedic knowledge of all 5,129 monsters, 17,006 spells, all 12 core breeds (Iop, Cra, Feca, Sram, Eniripsa, Sacrieur, Osamodas, Sadida, Enutrof, Ecaflip, Pandawa, Xelor), resource harvesting across all 5 professions, and quest walkthroughs from Papycha.fr.

Generate {count} unique, highly realistic training samples for a {task_type} task in {language}.
Format your output as a valid JSON array of objects with the following schema:
[
  {{
    "text": "The input prompt or game observation",
    "thought_process": "Your concise step-by-step reasoning trace (Chain-of-Thought)",
    "label": "The discrete action or tactical stance (BURST_LETHAL, BUFF_AND_ENGAGE, TRAP_AND_KITE, SHIELD_DEFENSE, FOLLOW, AUTO_JOIN_COMBAT, PASS_TURN, STOP, NAVIGATE_COORDS, NAVIGATE_ZAAP, FIND_RESOURCE, START_FARM_LOOP, QUEST_GUIDE, RESOURCE_LEVEL_REQ)",
    "target_name": "Target entity name if applicable",
    "rationale": "One-line tactical explanation"
  }}
]
"""


class TeacherKnowledgeDistiller:
    """Manages teacher prompting, synthetic dataset compilation, and logit temperature scaling."""

    DEFAULT_TEACHER_MODEL = "Qwen/Qwen2.5-7B-Instruct"

    @classmethod
    def get_teacher_generation_prompt(
        cls,
        task_type: str,
        language: str = "fr",
        count: int = 10,
    ) -> str:
        """Constructs an optimized synthetic generation prompt for the 7B teacher."""
        return TEACHER_PROMPT_TEMPLATE.format(
            count=count,
            task_type=task_type,
            language=language,
        )

    @classmethod
    def compute_distillation_loss(
        cls,
        student_logits: Any,
        teacher_logits: Any,
        labels: Any,
        temperature: float = 2.5,
        alpha: float = 0.65,
    ) -> Any:
        """Computes Hinton-style Knowledge Distillation Loss:

        L_total = (1 - alpha) * CrossEntropy(student, labels) + alpha * (T^2) * KL_Div(student/T, teacher/T)
        """
        import torch
        import torch.nn as nn
        import torch.nn.functional as F

        ce_loss = F.cross_entropy(student_logits, labels)

        # Soft target distillation
        p_teacher = F.softmax(teacher_logits / temperature, dim=-1)
        log_p_student = F.log_softmax(student_logits / temperature, dim=-1)
        kl_loss = F.kl_div(log_p_student, p_teacher, reduction="batchmean") * (temperature ** 2)

        return (1.0 - alpha) * ce_loss + alpha * kl_loss

    @classmethod
    def synthesize_local_seed_traces(cls) -> List[Dict[str, Any]]:
        """Generates pre-compiled distilled CoT reasoning traces ready for instant student training."""
        traces = [
            {
                "type": "DISTILLED_COT",
                "text": "Class: Sram (Lvl 140). Enemy: Bouftou Royal (HP 850, Weakness: Earth -15%, Fire 35%). Dist: 4 cells. Player HP: 85%.",
                "thought_process": "Enemy has -15% Earth weakness and high Fire resistance. Direct melee exposes Sram to heavy damage. Placing Piège Sournois and Piège de Masse will create a trigger cascade dealing ~520 Earth damage while keeping Sram invisible or kiting.",
                "label": "TRAP_AND_KITE",
                "stance": "TRAP_AND_KITE",
                "rotation": ["Invisibilité", "Piège de Masse", "Piège Sournois"],
                "target_name": "Bouftou Royal",
                "rationale": "Exploit -15% Earth weakness using trap combo and maintain safe distance.",
            },
            {
                "type": "DISTILLED_COT",
                "text": "Class: Feca (Lvl 110). Enemy: Craqueleur des Plaines (HP 1200, Earth Res 45%, Water Res -10%). Dist: 2 cells. Player HP: 42%.",
                "thought_process": "Player HP is below 50%. Craqueleur deals heavy neutral/earth damage. Priority is mitigation before damage. Cast Rempart and Bouclier Féca first, then exploit Water weakness with Bulle.",
                "label": "SHIELD_DEFENSE",
                "stance": "SHIELD_DEFENSE",
                "rotation": ["Rempart", "Bouclier Féca", "Bulle"],
                "target_name": "Craqueleur des Plaines",
                "rationale": "Emergency mitigation at 42% HP with Rempart, then leverage Water weakness.",
            },
            {
                "type": "DISTILLED_COT",
                "text": "Class: Iop (Lvl 160). Enemy: Chene Mou (HP 3200, Fire -20%, Earth 20%). Dist: 1 cell. Player HP: 95%. AP: 12.",
                "thought_process": "Player is at full HP and melee range. Oak Soft has huge Fire vulnerability (-20%). Multi-turn buff scaling via Puissance grants massive power multiplier for Epée d'Iop and Colère d'Iop.",
                "label": "BUFF_AND_ENGAGE",
                "stance": "BUFF_AND_ENGAGE",
                "rotation": ["Puissance", "Tir Puissant", "Épée du Destin"],
                "target_name": "Chêne Mou",
                "rationale": "Stack Puissance buffs to maximize lethal scaling against -20% Fire weakness.",
            },
            {
                "type": "DISTILLED_COT",
                "text": "va en [15, 28] parler au capitaine kradoc",
                "thought_process": "User requests travel to coordinates [15, 28] located at the Port of Sufokia. This is the starting location for the Otomaï Island access quest.",
                "label": "NAVIGATE_COORDS",
                "target_name": "Capitaine Kradoc",
                "rationale": "Plotting multi-modal path to [15, 28] via nearest Zaap.",
            },
            {
                "type": "DISTILLED_COT",
                "text": "comment obtenir le dofus cawotte ?",
                "thought_process": "Papycha walkthrough identifies Dofus Cawotte requires 3 meta-achievements on Wabbit Island: 'Rusé comme une Lenalde', 'Un Wa de pacotille', and completing the Wa Wabbit Warren dungeon equipped with full Wa gear.",
                "label": "QUEST_GUIDE",
                "target_name": "Wanald le Lenalde",
                "rationale": "Cawotte Dofus guide breakdown: 3 achievements + Wa Wabbit set.",
            },
        ]
        return traces
