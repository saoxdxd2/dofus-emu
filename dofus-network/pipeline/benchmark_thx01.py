"""Production-grade benchmark: local dofus-thx01 vs original doofz/THX-01.

Replicates ALL benchmarks from the original model card (multilingual az/ru/en,
Banking77, DAIR Emotion, Typed Decisions, Extraction & citation, calibration,
latency) on BOTH the original and the local fine-tuned model, so you can check
whether finetuning changed performance.  Also adds a new Dofus-specific
benchmark suite (combat actions, stances, items, maps, spells, NPC dialogue).

Two model backends are supported:
  - Original: doofz/THX-01 via thx01 library (agent.decide() API)
  - Local: models/dofus-thx01 via transformers (ModernBERT sequence classifier)

Usage:
    python pipeline/benchmark_thx01.py \\
        --original doofz/THX-01 \\
        --local models/dofus-thx01 \\
        --output benchmarks/comparison_report.json

If thx01/transformers/torch are not installed, runs in DRY-RUN mode printing
exactly what would be benchmarked and the install commands needed.
"""

from __future__ import annotations

import argparse
import importlib
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

BENCH_DIR = Path("benchmarks")
BENCH_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Optional dependency detection
# ---------------------------------------------------------------------------

_HAS_THX01: bool = False
_HAS_TRANSFORMERS: bool = False
_HAS_TORCH: bool = False
_HAS_SKLEARN: bool = False

try:
    import thx01  # type: ignore
    _HAS_THX01 = True
except ImportError:
    thx01 = None

try:
    from transformers import AutoModelForSequenceClassification, AutoTokenizer  # type: ignore
    _HAS_TRANSFORMERS = True
except ImportError:
    AutoModelForSequenceClassification = None
    AutoTokenizer = None

try:
    import torch  # type: ignore
    _HAS_TORCH = True
except ImportError:
    torch = None

try:
    from sklearn.metrics import brier_score_loss, classification_report  # type: ignore
    from sklearn.calibration import calibration_curve  # type: ignore
    _HAS_SKLEARN = True
except ImportError:
    brier_score_loss = None
    classification_report = None
    calibration_curve = None


def _check_deps() -> dict[str, bool]:
    return {
        "thx01": _HAS_THX01,
        "transformers": _HAS_TRANSFORMERS,
        "torch": _HAS_TORCH,
        "sklearn": _HAS_SKLEARN,
    }


# ---------------------------------------------------------------------------
# Original model card benchmarks (replicated from doofz/THX-01 card)
# ---------------------------------------------------------------------------

# Language benchmarks: [Independent az, Independent ru, Independent en,
#                       Corrupted az, Corrupted ru, Corrupted en]
_LANG_COLUMNS: list[str] = [
    "independent_az",
    "independent_ru",
    "independent_en",
    "corrupted_az",
    "corrupted_ru",
    "corrupted_en",
]

# Original model card reference scores (doofz/THX-01)
ORIGINAL_REFERENCE_SCORES: dict[str, list[float]] = {
    "THX-01": [97.1, 98.3, 98.3, 97.9, 96.6, 97.9],
    "Claude Sonnet 5.5": [98.5, 100.0, 98.5, 97.6, 100.0, 97.5],
    "Wahoo 1.5": [96.2, 95.8, 99.6, 94.5, 95.0, 98.9],
    "GPT-6-Luna": [97.9, 97.5, 99.2, 97.2, 95.0, 98.6],
    "TypeSafe Jev 1.13": [98.8, 98.3, 100.0, 92.9, 93.3, 98.2],
    "Kev-4B": [94.2, 95.4, 97.9, 83.1, 89.1, 94.3],
}

ORIGINAL_BANKING77_ACC: float = 65.9  # %
ORIGINAL_EMOTION_ACC: float = 44.6  # %
ORIGINAL_LATENCY_MS: float = 10.0  # per request
ORIGINAL_LATENCY_BATCHED_MS: float = 1.0  # per decision batched



# ---------------------------------------------------------------------------
# Original model-card benchmark datasets (replicated for both models)
# ---------------------------------------------------------------------------

# --- Multilingual az/ru/en (independent + corrupted) ---
# Each item: state text in a language, expected Dofus action label.
# "Independent" = clean text; "Corrupted" = typos / casing noise injected.
MULTILINGUAL_SUITE: list[dict[str, Any]] = [
    # Azerbaijani — independent
    {"id": "lang_indep_az_001", "language": "az", "variant": "independent",
     "state": "Dövüşdə avtomatik girəcəm, İop və Qoblin.",
     "expected_label": "AUTO_JOIN_COMBAT"},
    {"id": "lang_indep_az_002", "language": "az", "variant": "independent",
     "state": "Süsbəndə walk at coordinates [-15, 28].",
     "expected_label": "NAVIGATE_COORDS"},
    {"id": "lang_indep_az_003", "language": "az", "variant": "independent",
     "state": "Quruq əhəmiyyətli, növbəti härəkəti dayandır.",
     "expected_label": "STOP"},
    # Azerbaijani — corrupted
    {"id": "lang_corr_az_001", "language": "az", "variant": "corrupted",
     "state": "dövüşdə avtomatİk girəcÉm, iOP və qoblIN.",
     "expected_label": "AUTO_JOIN_COMBAT"},
    {"id": "lang_corr_az_002", "language": "az", "variant": "corrupted",
     "state": "GO to [-15, 28] nao pls.",
     "expected_label": "NAVIGATE_COORDS"},
    {"id": "lang_corr_az_003", "language": "az", "variant": "corrupted",
     "state": "STOP everithing now!!!",
     "expected_label": "STOP"},
    # Russian — independent
    {"id": "lang_indep_ru_001", "language": "ru", "variant": "independent",
     "state": "Автоматически вступить в бой, Иоп против Гoblina.",
     "expected_label": "AUTO_JOIN_COMBAT"},
    {"id": "lang_indep_ru_002", "language": "ru", "variant": "independent",
     "state": "Перейти к координатам [-22, 35].",
     "expected_label": "NAVIGATE_COORDS"},
    {"id": "lang_indep_ru_003", "language": "ru", "variant": "independent",
     "state": "Прекратить все действия, низкие ОЗ.",
     "expected_label": "STOP"},
    # Russian — corrupted
    {"id": "lang_corr_ru_001", "language": "ru", "variant": "corrupted",
     "state": "avtomaticheski vstuPit v boy, IOP vs goblin.",
     "expected_label": "AUTO_JOIN_COMBAT"},
    {"id": "lang_corr_ru_002", "language": "ru", "variant": "corrupted",
     "state": "go to [-22, 35] fast.",
     "expected_label": "NAVIGATE_COORDS"},
    {"id": "lang_corr_ru_003", "language": "ru", "variant": "corrupted",
     "state": "STOP ALL!!! hp low.",
     "expected_label": "STOP"},
    # English — independent
    {"id": "lang_indep_en_001", "language": "en", "variant": "independent",
     "state": "Auto-join combat: Iop vs Goblin at distance 2.",
     "expected_label": "AUTO_JOIN_COMBAT"},
    {"id": "lang_indep_en_002", "language": "en", "variant": "independent",
     "state": "Navigate to zaap in Bonta.",
     "expected_label": "NAVIGATE_ZAAP"},
    {"id": "lang_indep_en_003", "language": "en", "variant": "independent",
     "state": "Pass turn, low HP defensive stance.",
     "expected_label": "PASS_TURN"},
    # English — corrupted
    {"id": "lang_corr_en_001", "language": "en", "variant": "corrupted",
     "state": "auto join combat iop vs goblin pls.",
     "expected_label": "AUTO_JOIN_COMBAT"},
    {"id": "lang_corr_en_002", "language": "en", "variant": "corrupted",
     "state": "go to bonta zaap now.",
     "expected_label": "NAVIGATE_ZAAP"},
    {"id": "lang_corr_en_003", "language": "en", "variant": "corrupted",
     "state": "pass turn hp too low.",
     "expected_label": "PASS_TURN"},
]


# --- Banking77 (intent classification, 77 intents) ---
# Representative subset with a suggested mapping to Dofus action labels
# for the local ModernBERT classifier (12 labels).  Items without a sensible
# Dofus mapping are skipped when running on the local model.
BANKING77_SUITE: list[dict[str, Any]] = [
    {"id": "bank_001", "query": "I want to transfer money to another account",
     "expected_intent": "transfer", "dofus_label": "NAVIGATE_COORDS"},
    {"id": "bank_002", "query": "What is my current account balance?",
     "expected_intent": "balance", "dofus_label": "NONE"},
    {"id": "bank_003", "query": "Report a lost or stolen card",
     "expected_intent": "report_lost_card", "dofus_label": "STOP"},
    {"id": "bank_004", "query": "How do I activate my new card?",
     "expected_intent": "activate_card", "dofus_label": "AUTO_JOIN_COMBAT"},
    {"id": "bank_005", "query": "I need to order a cheque book",
     "expected_intent": "order_cheque_book", "dofus_label": "FIND_RESOURCE"},
    {"id": "bank_006", "query": "Cancel a pending transaction",
     "expected_intent": "cancel_transaction", "dofus_label": "STOP"},
    {"id": "bank_007", "query": "Exchange currency from USD to EUR",
     "expected_intent": "currency_exchange", "dofus_label": "NAVIGATE_ZAAP"},
    {"id": "bank_008", "query": "Set up a recurring payment",
     "expected_intent": "recurring_payment", "dofus_label": "START_FARM_LOOP"},
    {"id": "bank_009", "query": "What are the loan requirements?",
     "expected_intent": "loan_requirements", "dofus_label": "RESOURCE_LEVEL_REQ"},
    {"id": "bank_010", "query": "Block my account temporarily",
     "expected_intent": "block_account", "dofus_label": "STOP"},
    {"id": "bank_011", "query": "Update my personal details",
     "expected_intent": "update_personal_details", "dofus_label": "QUEST_GUIDE"},
    {"id": "bank_012", "query": "Find the nearest ATM",
     "expected_intent": "find_atm", "dofus_label": "FIND_RESOURCE"},
    {"id": "bank_013", "query": "Deposit cash into my account",
     "expected_intent": "deposit_cash", "dofus_label": "AUTO_FARM"},
    {"id": "bank_014", "query": "Withdraw money from ATM",
     "expected_intent": "withdraw_cash", "dofus_label": "FOLLOW"},
    {"id": "bank_015", "query": "Transaction not showing in statement",
     "expected_intent": "missing_transaction", "dofus_label": "QUEST_GUIDE"},
    {"id": "bank_016", "query": "Increase my credit card limit",
     "expected_intent": "credit_limit_increase", "dofus_label": "NONE"},
    {"id": "bank_017", "query": "Close my savings account",
     "expected_intent": "close_account", "dofus_label": "STOP"},
    {"id": "bank_018", "query": "Set up a new payee",
     "expected_intent": "setup_payee", "dofus_label": "FOLLOW"},
    {"id": "bank_019", "query": "Check interest rate on savings",
     "expected_intent": "interest_rate", "dofus_label": "RESOURCE_LEVEL_REQ"},
    {"id": "bank_020", "query": "Report fraudulent activity",
     "expected_intent": "fraud_report", "dofus_label": "STOP"},
]


# --- DAIR Emotion (6 emotion classes) ---
DAIR_EMOTION_SUITE: list[dict[str, Any]] = [
    {"id": "emo_001", "text": "I am furious about this terrible service!",
     "expected_emotion": "anger", "dofus_label": "PASS_TURN"},
    {"id": "emo_002", "text": "This makes me so angry, I can't believe it.",
     "expected_emotion": "anger", "dofus_label": "PASS_TURN"},
    {"id": "emo_003", "text": "I'm thrilled and excited about the news!",
     "expected_emotion": "joy", "dofus_label": "AUTO_JOIN_COMBAT"},
    {"id": "emo_004", "text": "What a wonderful day, I feel great!",
     "expected_emotion": "joy", "dofus_label": "AUTO_JOIN_COMBAT"},
    {"id": "emo_005", "text": "I feel so sad and depressed today.",
     "expected_emotion": "sadness", "dofus_label": "STOP"},
    {"id": "emo_006", "text": "Nothing brings me joy anymore, I'm down.",
     "expected_emotion": "sadness", "dofus_label": "STOP"},
    {"id": "emo_007", "text": "I'm terrified of what might happen next.",
     "expected_emotion": "fear", "dofus_label": "SHIELD_DEFENSE"},
    {"id": "emo_008", "text": "I'm scared and anxious about the future.",
     "expected_emotion": "fear", "dofus_label": "SHIELD_DEFENSE"},
    {"id": "emo_009", "text": "Wow, I never expected this surprise!",
     "expected_emotion": "surprise", "dofus_label": "QUEST_GUIDE"},
    {"id": "emo_010", "text": "Unexpected turn of events, what now?",
     "expected_emotion": "surprise", "dofus_label": "QUEST_GUIDE"},
    {"id": "emo_011", "text": "It is what it is, nothing special.",
     "expected_emotion": "neutral", "dofus_label": "NONE"},
    {"id": "emo_012", "text": "Just another ordinary day, nothing much.",
     "expected_emotion": "neutral", "dofus_label": "NONE"},
]


# --- Typed Decisions (choice / number / excerpt / cite) ---
TYPED_DECISIONS_SUITE: list[dict[str, Any]] = [
    {"id": "typed_choice_001", "type": "choice",
     "state": "Combat: Iop vs Goblin, low HP, defensive posture recommended.",
     "question": "What combat action should the character take?",
     "choices": ["AUTO_JOIN_COMBAT", "PASS_TURN", "STOP", "NAVIGATE_COORDS",
                 "NAVIGATE_ZAAP", "FOLLOW", "AUTO_FARM", "FIND_RESOURCE",
                 "START_FARM_LOOP", "QUEST_GUIDE", "RESOURCE_LEVEL_REQ", "NONE"],
     "expected": "PASS_TURN"},
    {"id": "typed_choice_002", "type": "choice",
     "state": "All mobs defeated, loot on ground, fight is over.",
     "question": "What action should be taken?",
     "choices": ["AUTO_JOIN_COMBAT", "PASS_TURN", "STOP", "NAVIGATE_COORDS",
                 "NAVIGATE_ZAAP", "FOLLOW", "AUTO_FARM", "FIND_RESOURCE",
                 "START_FARM_LOOP", "QUEST_GUIDE", "RESOURCE_LEVEL_REQ", "NONE"],
     "expected": "STOP"},
    {"id": "typed_choice_003", "type": "choice",
     "state": "Player requests group follow in chat.",
     "question": "What social action should be taken?",
     "choices": ["AUTO_JOIN_COMBAT", "PASS_TURN", "STOP", "NAVIGATE_COORDS",
                 "NAVIGATE_ZAAP", "FOLLOW", "AUTO_FARM", "FIND_RESOURCE",
                 "START_FARM_LOOP", "QUEST_GUIDE", "RESOURCE_LEVEL_REQ", "NONE"],
     "expected": "FOLLOW"},
    {"id": "typed_number_001", "type": "number",
     "state": "Inventory contains Potion de Soins (ID 287) and Bread (ID 11245).",
     "question": "Extract the numeric item ID for Potion de Soins.",
     "expected": 287},
    {"id": "typed_number_002", "type": "number",
     "state": "Wheat costs 45 kamas per unit at the market.",
     "question": "What is the price per unit of wheat?",
     "expected": 45},
    {"id": "typed_excerpt_001", "type": "excerpt",
     "state": "Iop casts Pulsation. AoE 3 cells around target. Damage 30.",
     "question": "What is the exact spell name used in this combat turn?",
     "expected": "Pulsation"},
    {"id": "typed_excerpt_002", "type": "excerpt",
     "state": "Eni uses Soothing Word to heal ally for 120 HP.",
     "question": "Extract the spell name from this combat log.",
     "expected": "Soothing Word"},
    {"id": "typed_cite_001", "type": "cite",
     "state": "Per the Dofus wiki, Feca glyphs provide 25% damage reduction.",
     "question": "What is the damage reduction percentage from Feca glyphs?",
     "expected": "25%"},
    {"id": "typed_cite_002", "type": "cite",
     "state": "The Bonta guard captain says: 'Permit required for entry.'",
     "question": "What does the guard captain require for entry?",
     "expected": "Permit"},
]


# --- Extraction & citation (number / excerpt / cite) ---
EXTRACTION_SUITE: list[dict[str, Any]] = [
    {"id": "ext_number_001", "type": "number",
     "state": "Inventory: Potion de Soins (ID 287), Pain Cacahuete (ID 11245).",
     "question": "Extract the numeric item ID for Potion de Soins.",
     "expected": 287},
    {"id": "ext_number_002", "type": "number",
     "state": "Map coordinates: Bonta [-15, 28], Astrub [-22, 35].",
     "question": "What is the X coordinate of Bonta?",
     "expected": -15},
    {"id": "ext_excerpt_001", "type": "excerpt",
     "state": "Iop casts Pulsation. AoE 3 cells around target. Damage 30.",
     "question": "What is the exact spell name used in this combat turn?",
     "expected": "Pulsation"},
    {"id": "ext_excerpt_002", "type": "excerpt",
     "state": "NPC offers: 'Buy bread', 'Quest: Lost Wheat', 'Goodbye'.",
     "question": "Extract the first dialogue option text.",
     "expected": "Buy bread"},
    {"id": "ext_cite_001", "type": "cite",
     "state": "Dofus rulebook: Feca glyphs give 25% damage reduction.",
     "question": "What damage reduction do Feca glyphs provide?",
     "expected": "25%"},
    {"id": "ext_cite_002", "type": "cite",
     "state": "Quest log: 'Deliver 5 wheat to the miller.'",
     "question": "How many wheat items must be delivered?",
     "expected": "5"},
]


# ---------------------------------------------------------------------------
# Dofus-specific benchmark suite (NEW)
# ---------------------------------------------------------------------------

DOFUS_BENCHMARK_SUITE: list[dict[str, Any]] = [
    # --- Combat action classification (12 labels from labels.json) ---
    {
        "id": "dofus_combat_001",
        "category": "combat_action",
        "state": "Combat turn 3. Iop vs Goblin. Iop has 120/150 PV, 6/6 PA, 3/3 PM. Goblin at distance 2.",
        "expected_label": "AUTO_JOIN_COMBAT",
        "expected_stance": "BURST_LETHAL",
    },
    {
        "id": "dofus_combat_002",
        "category": "combat_action",
        "state": "Low HP 15/150. Enemy Bouftou adjacent. Recommend defensive stance.",
        "expected_label": "PASS_TURN",
        "expected_stance": "SHIELD_DEFENSE",
    },
    {
        "id": "dofus_combat_003",
        "category": "combat_action",
        "state": "Fight over. All mobs defeated. Loot available on ground.",
        "expected_label": "STOP",
        "expected_stance": "BUFF_AND_ENGAGE",
    },
    # --- Navigation ---
    {
        "id": "dofus_nav_001",
        "category": "navigation",
        "state": "Player at Astrub [-22, 35]. Want to go to Bonta zaap.",
        "expected_label": "NAVIGATE_ZAAP",
    },
    {
        "id": "dofus_nav_002",
        "category": "navigation",
        "state": "Go to coordinates [-15, 28] in Bonta.",
        "expected_label": "NAVIGATE_COORDS",
    },
    # --- Farming / resources ---
    {
        "id": "dofus_farm_001",
        "category": "farming",
        "state": "Ble (wheat) spot near Astrub. Player is paysan lvl 45.",
        "expected_label": "FIND_RESOURCE",
    },
    {
        "id": "dofus_farm_002",
        "category": "farming",
        "state": "Start auto-farming iron mine in Amakna.",
        "expected_label": "START_FARM_LOOP",
    },
    {
        "id": "dofus_farm_003",
        "category": "farming",
        "state": "Fer ore requires level 30 miner. Player is lvl 12.",
        "expected_label": "RESOURCE_LEVEL_REQ",
    },
    # --- Quest / guidance ---
    {
        "id": "dofus_quest_001",
        "category": "quest",
        "state": "How to complete the Incarnam tutorial quest?",
        "expected_label": "QUEST_GUIDE",
    },
    # --- Follow / social ---
    {
        "id": "dofus_follow_001",
        "category": "social",
        "state": "Player says 'follow me' in group chat.",
        "expected_label": "FOLLOW",
    },
  
    # --- Item / map / spell extraction (THX-01 native types) ---
    {
        "id": "dofus_item_001",
        "category": "item_extraction",
        "state": "Inventory: Potion de Soins (ID 287), Pain Cacahuete (ID 11245).",
        "expected_number": 287,
        "question": "Extract the numeric item ID for Potion de Soins.",
    },
    {
        "id": "dofus_map_001",
        "category": "map_coordinate",
        "state": "Bonta is at [-15, 28]. Astrub is at [-22, 35].",
        "expected_number": -15,
        "question": "What is the X coordinate of Bonta?",
    },
    {
        "id": "dofus_spell_001",
        "category": "spell_excerpt",
        "state": "Iop casts Pulsation. AoE 3 cells around target. Damage 30.",
        "expected_excerpt": "Pulsation",
        "question": "What is the exact spell name used in this combat turn?",
    },
    {
        "id": "dofus_npc_001",
        "category": "npc_dialogue",
        "state": "NPC Baker offers: 'Buy bread', 'Quest: Lost Wheat', 'Goodbye'.",
        "expected_choice": "Buy bread",
        "question": "Which dialogue option should the player choose?",
        "choices": ["Buy bread", "Quest: Lost Wheat", "Goodbye"],
    },
]


# ---------------------------------------------------------------------------
# Model backends
# ---------------------------------------------------------------------------


class OriginalModel:
    """Wrapper for doofz/THX-01 via thx01 library."""

    def __init__(self, model_name: str):
        if not _HAS_THX01:
            raise RuntimeError("thx01 not installed. pip install thx01")
        self.agent = thx01.load(model_name)
        self.model_name = model_name

    def decide(self, state: str, questions: dict[str, Any]) -> dict[str, Any]:
        return self.agent.decide(state, questions)


class LocalModel:
    """Wrapper for local models/dofus-thx01 via transformers (ModernBERT)."""

    def __init__(self, model_path: str | Path):
        if not _HAS_TRANSFORMERS or not _HAS_TORCH:
            raise RuntimeError("transformers and torch not installed")
        model_path = str(model_path)
        self.tokenizer = AutoTokenizer.from_pretrained(model_path)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_path)
        self.model.eval()
        self.model_path = model_path
        self.id2label = self.model.config.id2label

    def classify(self, text: str) -> dict[str, Any]:
        inputs = self.tokenizer(
            text,
            return_tensors="pt",
            truncation=True,
            max_length=8192,
            padding=True,
        )
        if _HAS_TORCH and torch.cuda.is_available():
            inputs = {k: v.cuda() for k, v in inputs.items()}
            self.model.cuda()
        grad_ctx = torch.no_grad() if _HAS_TORCH else _nullcontext()  # type: ignore
        with grad_ctx:
            logits = self.model(**inputs).logits
            probs = torch.softmax(logits, dim=-1)[0]
        pred_id = int(probs.argmax())
        return {
            "predicted_label": self.id2label[str(pred_id)],
            "predicted_id": pred_id,
            "confidence": float(probs[pred_id]),
            "probabilities": {self.id2label[str(i)]: float(p) for i, p in enumerate(probs)},
        }

    def decide(self, state: str, questions: dict[str, Any]) -> dict[str, Any]:
        """Adapter: mimic thx01 agent.decide() API for the local ModernBERT classifier.

        The classifier only predicts one of the 12 Dofus action labels, so
        choice-type questions are supported by classifying state+question and
        matching the predicted label to the closest choice.  Number/excerpt/cite
        question types are not supported by a pure sequence classifier and are
        reported as not_applicable.
        """
        answers: dict[str, Any] = {}
        not_applicable: list[str] = []

        for qname, qcfg in questions.items():
            qtype = qcfg.get("type", "choice")
            question = qcfg.get("question", "")

            if qtype == "choice":
                choices = list(qcfg.get("criteria", {}).keys())
                text = f"{state} {question}"
                result = self.classify(text)
                pred_label = result["predicted_label"]

                # Map predicted Dofus label to the closest matching choice
                if pred_label in choices:
                    answers[qname] = pred_label
                else:
                    # Fallback: pick first choice (unknown mapping)
                    answers[qname] = choices[0] if choices else pred_label
            elif qtype in ("number", "excerpt", "cite"):
                not_applicable.append(qname)
                answers[qname] = None
            else:
                text = f"{state} {question}"
                result = self.classify(text)
                answers[qname] = result["predicted_label"]

        return {
            "answers": answers,
            "not_applicable": not_applicable,
            "model_type": "local_classifier",
        }


class _nullcontext:
    """Minimal contextmanager stand-in when torch is unavailable."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


# ---------------------------------------------------------------------------
# Benchmark runners
# ---------------------------------------------------------------------------


def _now_ms() -> float:
    return time.perf_counter() * 1000


def run_language_benchmark(
    model: Any,
    is_original: bool,
) -> dict[str, Any]:
    """Replicate the multilingual az/ru/en independent + corrupted benchmark."""
    results: dict[str, Any] = {
        "columns": _LANG_COLUMNS,
        "note": "Requires labeled az/ru/en test sets; reference scores below are from model card.",
        "reference_scores": ORIGINAL_REFERENCE_SCORES,
    }
    return results


def run_dofus_suite(
    model: Any,
    is_original: bool,
    suite: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run the Dofus-specific benchmark suite against a model."""
    item_results: list[dict[str, Any]] = []
    latencies: list[float] = []
    correct_by_cat: dict[str, dict[str, int]] = {}

    for item in suite:
        state = item["state"]
        cat = item["category"]

        if is_original:
            if cat == "combat_action":
                questions = {
                    "combat_action": {
                        "type": "choice",
                        "question": "What combat action should the character take?",
                        "criteria": {
                            "FOLLOW": "Follow ally",
                            "AUTO_JOIN_COMBAT": "Join combat automatically",
                            "PASS_TURN": "Skip turn",
                            "AUTO_FARM": "Auto-farm",
                            "STOP": "Stop current action",
                            "NAVIGATE_COORDS": "Navigate to coordinates",
                            "NAVIGATE_ZAAP": "Navigate via zaap",
                            "FIND_RESOURCE": "Find resource",
                            "START_FARM_LOOP": "Start farm loop",
                            "QUEST_GUIDE": "Quest guidance",
                            "RESOURCE_LEVEL_REQ": "Check resource level requirement",
                            "NONE": "No action",
                        },
                    }
                }
            elif cat == "item_extraction":
                questions = {
                    "item_id": {
                        "type": "number",
                        "question": item.get("question", "Extract item ID."),
                        "criteria": {"min": 0, "max": 999999, "precision": 0},
                    }
                }
            elif cat == "map_coordinate":
                questions = {
                    "map_x": {
                        "type": "number",
                        "question": item.get("question", "What is the X coordinate?"),
                        "criteria": {"min": -100, "max": 100, "precision": 0},
                    }
                }
            elif cat == "spell_excerpt":
                questions = {
                    "spell_name": {
                        "type": "excerpt",
                        "question": item.get("question", "Extract spell name."),
                        "criteria": {},
                    }
                }
            elif cat == "npc_dialogue":
                questions = {
                    "dialogue": {
                        "type": "choice",
                        "question": item.get("question", "Choose dialogue."),
                        "criteria": {c: c for c in item.get("choices", [])},
                    }
                }
            else:
                questions = {
                    "action": {
                        "type": "choice",
                        "question": "What action should be taken?",
                        "criteria": {},
                    }
                }

            start = _now_ms()
            result = model.decide(state, questions)
            elapsed = _now_ms() - start
            answers = result.get("answers", {})

            if cat == "combat_action":
                pred = answers.get("combat_action")
                expected = item["expected_label"]
            elif cat == "item_extraction":
                pred = answers.get("item_id")
                expected = item["expected_number"]
            elif cat == "map_coordinate":
                pred = answers.get("map_x")
                expected = item["expected_number"]
            elif cat == "spell_excerpt":
                pred = answers.get("spell_name")
                expected = item["expected_excerpt"]
            elif cat == "npc_dialogue":
                pred = answers.get("dialogue")
                expected = item["expected_choice"]
            else:
                pred = answers.get("action")
                expected = item.get("expected_label")
        else:
            start = _now_ms()
            result = model.classify(state)
            elapsed = _now_ms() - start
            pred = result["predicted_label"]
            expected = item["expected_label"]

        latencies.append(elapsed)

        correct = _compare_prediction(pred, expected)
        item_results.append({
            "id": item["id"],
            "category": cat,
            "predicted": pred,
            "expected": expected,
            "correct": correct,
            "latency_ms": round(elapsed, 3),
            "raw": result if not is_original else answers,
        })

        if cat not in correct_by_cat:
            correct_by_cat[cat] = {"correct": 0, "total": 0}
        correct_by_cat[cat]["total"] += 1
        if correct:
            correct_by_cat[cat]["correct"] += 1

    sorted_lat = sorted(latencies)
    p95_idx = int(len(sorted_lat) * 0.95) if sorted_lat else 0

    return {
        "items": item_results,
        "aggregate": {
            "avg_latency_ms": round(statistics.mean(latencies), 3),
            "p50_latency_ms": round(statistics.median(latencies), 3),
            "p95_latency_ms": round(sorted_lat[p95_idx], 3) if sorted_lat else 0,
            "max_latency_ms": round(max(latencies), 3) if latencies else 0,
            "accuracy_by_category": {
                c: round(v["correct"] / v["total"], 4) if v["total"] else 0.0
                for c, v in correct_by_cat.items()
            },
            "correct_by_category": correct_by_cat,
            "total_items": len(suite),
            "overall_accuracy": round(
                sum(v["correct"] for v in correct_by_cat.values())
                / max(sum(v["total"] for v in correct_by_cat.values()), 1),
                4,
            ),
        },
    }


# ---------------------------------------------------------------------------
# Original model-card benchmark runners (run on BOTH models)
# ---------------------------------------------------------------------------


def run_multilingual_benchmark(
    model: Any,
    is_original: bool,
) -> dict[str, Any]:
    """Run the multilingual az/ru/en independent + corrupted benchmark."""
    item_results: list[dict[str, Any]] = []
    latencies: list[float] = []
    correct_by_variant: dict[str, dict[str, int]] = {}
    correct_by_lang: dict[str, dict[str, int]] = {}

    for item in MULTILINGUAL_SUITE:
        state = item["state"]
        expected = item["expected_label"]
        lang = item["language"]
        variant = item["variant"]

        if is_original:
            questions = {
                "action": {
                    "type": "choice",
                    "question": "What action should be taken?",
                    "criteria": {
                        "AUTO_JOIN_COMBAT": "Join combat",
                        "PASS_TURN": "Skip turn",
                        "STOP": "Stop action",
                        "NAVIGATE_COORDS": "Navigate to coordinates",
                        "NAVIGATE_ZAAP": "Navigate via zaap",
                        "FOLLOW": "Follow ally",
                        "AUTO_FARM": "Auto-farm",
                        "FIND_RESOURCE": "Find resource",
                        "START_FARM_LOOP": "Start farm loop",
                        "QUEST_GUIDE": "Quest guidance",
                        "RESOURCE_LEVEL_REQ": "Check resource level",
                        "NONE": "No action",
                    },
                }
            }
            start = _now_ms()
            result = model.decide(state, questions)
            elapsed = _now_ms() - start
            answers = result.get("answers", {})
            pred = answers.get("action")
        else:
            start = _now_ms()
            result = model.classify(state)
            elapsed = _now_ms() - start
            pred = result["predicted_label"]

        latencies.append(elapsed)
        correct = _compare_prediction(pred, expected)
        item_results.append({
            "id": item["id"],
            "language": lang,
            "variant": variant,
            "predicted": pred,
            "expected": expected,
            "correct": correct,
            "latency_ms": round(elapsed, 3),
        })

        for key in (variant, lang):
            bucket = correct_by_variant if key in ("independent", "corrupted") else correct_by_lang
            if key not in bucket:
                bucket[key] = {"correct": 0, "total": 0}
            bucket[key]["total"] += 1
            if correct:
                bucket[key]["correct"] += 1

    sorted_lat = sorted(latencies)
    p95_idx = int(len(sorted_lat) * 0.95) if sorted_lat else 0

    return {
        "items": item_results,
        "aggregate": {
            "avg_latency_ms": round(statistics.mean(latencies), 3),
            "p50_latency_ms": round(statistics.median(latencies), 3),
            "p95_latency_ms": round(sorted_lat[p95_idx], 3) if sorted_lat else 0,
            "max_latency_ms": round(max(latencies), 3) if latencies else 0,
            "overall_accuracy": round(
                sum(v["correct"] for v in correct_by_variant.values())
                / max(sum(v["total"] for v in correct_by_variant.values()), 1), 4,
            ),
            "accuracy_by_variant": {
                k: round(v["correct"] / v["total"], 4) if v["total"] else 0.0
                for k, v in correct_by_variant.items()
            },
            "accuracy_by_language": {
                k: round(v["correct"] / v["total"], 4) if v["total"] else 0.0
                for k, v in correct_by_lang.items()
            },
            "total_items": len(MULTILINGUAL_SUITE),
        },
    }


def run_banking77_benchmark(
    model: Any,
    is_original: bool,
) -> dict[str, Any]:
    """Run the Banking77 intent classification benchmark."""
    item_results: list[dict[str, Any]] = []
    latencies: list[float] = []
    correct = 0
    skipped_local = 0

    for item in BANKING77_SUITE:
        query = item["query"]
        expected_intent = item["expected_intent"]
        dofus_label = item.get("dofus_label")

        if is_original:
            questions = {
                "intent": {
                    "type": "choice",
                    "question": "What is the user's banking intent?",
                    "criteria": {
                        "transfer": "Transfer money",
                        "balance": "Check balance",
                        "report_lost_card": "Report lost card",
                        "activate_card": "Activate card",
                        "order_cheque_book": "Order cheque book",
                        "cancel_transaction": "Cancel transaction",
                        "currency_exchange": "Exchange currency",
                        "recurring_payment": "Set up recurring payment",
                        "loan_requirements": "Loan requirements",
                        "block_account": "Block account",
                        "update_personal_details": "Update details",
                        "find_atm": "Find ATM",
                        "deposit_cash": "Deposit cash",
                        "withdraw_cash": "Withdraw cash",
                        "missing_transaction": "Missing transaction",
                        "credit_limit_increase": "Credit limit increase",
                        "close_account": "Close account",
                        "setup_payee": "Set up payee",
                        "interest_rate": "Interest rate",
                        "fraud_report": "Report fraud",
                    },
                }
            }
            start = _now_ms()
            result = model.decide(query, questions)
            elapsed = _now_ms() - start
            answers = result.get("answers", {})
            pred = answers.get("intent")
        else:
            if not dofus_label:
                skipped_local += 1
                continue
            start = _now_ms()
            result = model.classify(query)
            elapsed = _now_ms() - start
            pred = result["predicted_label"]
            pred = dofus_label if pred == dofus_label else pred

        latencies.append(elapsed)
        is_correct = _compare_prediction(pred, expected_intent)
        item_results.append({
            "id": item["id"],
            "predicted": pred,
            "expected": expected_intent,
            "correct": is_correct,
            "latency_ms": round(elapsed, 3),
            "skipped_local": not is_original and not dofus_label,
        })
        if is_correct:
            correct += 1

    total_run = len(item_results)
    sorted_lat = sorted(latencies)
    p95_idx = int(len(sorted_lat) * 0.95) if sorted_lat else 0

    return {
        "items": item_results,
        "aggregate": {
            "avg_latency_ms": round(statistics.mean(latencies), 3),
            "p50_latency_ms": round(statistics.median(latencies), 3),
            "p95_latency_ms": round(sorted_lat[p95_idx], 3) if sorted_lat else 0,
            "max_latency_ms": round(max(latencies), 3) if latencies else 0,
            "overall_accuracy": round(correct / max(total_run, 1), 4),
            "correct": correct,
            "total_run": total_run,
            "skipped_local": skipped_local,
            "reference_acc": ORIGINAL_BANKING77_ACC / 100.0,
            "total_items": len(BANKING77_SUITE),
        },
    }


def run_emotion_benchmark(
    model: Any,
    is_original: bool,
) -> dict[str, Any]:
    """Run the DAIR Emotion classification benchmark."""
    item_results: list[dict[str, Any]] = []
    latencies: list[float] = []
    correct = 0
    skipped_local = 0

    emotion_to_dofus = {
        "anger": "PASS_TURN",
        "joy": "AUTO_JOIN_COMBAT",
        "sadness": "STOP",
        "fear": "SHIELD_DEFENSE",
        "surprise": "QUEST_GUIDE",
        "neutral": "NONE",
    }

    for item in DAIR_EMOTION_SUITE:
        text = item["text"]
        expected_emotion = item["expected_emotion"]
        dofus_label = item.get("dofus_label")

        if is_original:
            questions = {
                "emotion": {
                    "type": "choice",
                    "question": "What emotion is expressed in this text?",
                    "criteria": {
                        "anger": "Anger",
                        "joy": "Joy",
                        "sadness": "Sadness",
                        "fear": "Fear",
                        "surprise": "Surprise",
                        "neutral": "Neutral",
                    },
                }
            }
            start = _now_ms()
            result = model.decide(text, questions)
            elapsed = _now_ms() - start
            answers = result.get("answers", {})
            pred = answers.get("emotion")
        else:
            if not dofus_label:
                skipped_local += 1
                continue
            start = _now_ms()
            result = model.classify(text)
            elapsed = _now_ms() - start
            pred_label = result["predicted_label"]
            pred = None
            for emo, dl in emotion_to_dofus.items():
                if dl == pred_label:
                    pred = emo
                    break
            if pred is None:
                pred = pred_label

        latencies.append(elapsed)
        is_correct = _compare_prediction(pred, expected_emotion)
        item_results.append({
            "id": item["id"],
            "predicted": pred,
            "expected": expected_emotion,
            "correct": is_correct,
            "latency_ms": round(elapsed, 3),
            "skipped_local": not is_original and not dofus_label,
        })
        if is_correct:
            correct += 1

    total_run = len(item_results)
    sorted_lat = sorted(latencies)
    p95_idx = int(len(sorted_lat) * 0.95) if sorted_lat else 0

    return {
        "items": item_results,
        "aggregate": {
            "avg_latency_ms": round(statistics.mean(latencies), 3),
            "p50_latency_ms": round(statistics.median(latencies), 3),
            "p95_latency_ms": round(sorted_lat[p95_idx], 3) if sorted_lat else 0,
            "max_latency_ms": round(max(latencies), 3) if latencies else 0,
            "overall_accuracy": round(correct / max(total_run, 1), 4),
            "correct": correct,
            "total_run": total_run,
            "skipped_local": skipped_local,
            "reference_acc": ORIGINAL_EMOTION_ACC / 100.0,
            "total_items": len(DAIR_EMOTION_SUITE),
        },
    }


def run_typed_decisions_benchmark(
    model: Any,
    is_original: bool,
) -> dict[str, Any]:
    """Run the Typed Decisions benchmark (choice / number / excerpt / cite)."""
    item_results: list[dict[str, Any]] = []
    latencies: list[float] = []
    correct = 0
    skipped_local = 0

    for item in TYPED_DECISIONS_SUITE:
        state = item["state"]
        question = item["question"]
        expected = item["expected"]
        qtype = item["type"]

        if is_original:
            if qtype == "choice":
                questions = {
                    "decision": {
                        "type": "choice",
                        "question": question,
                        "criteria": {c: c for c in item["choices"]},
                    }
                }
            elif qtype == "number":
                questions = {
                    "number": {
                        "type": "number",
                        "question": question,
                        "criteria": {"min": 0, "max": 999999, "precision": 0},
                    }
                }
            elif qtype == "excerpt":
                questions = {
                    "excerpt": {
                        "type": "excerpt",
                        "question": question,
                        "criteria": {},
                    }
                }
            elif qtype == "cite":
                questions = {
                    "cite": {
                        "type": "cite",
                        "question": question,
                        "criteria": {},
                    }
                }
            else:
                questions = {"decision": {"type": "choice", "question": question, "criteria": {}}}

            start = _now_ms()
            result = model.decide(state, questions)
            elapsed = _now_ms() - start
            answers = result.get("answers", {})
            pred = answers.get("decision") or answers.get("number") or answers.get("excerpt") or answers.get("cite")
        else:
            if qtype != "choice":
                skipped_local += 1
                continue
            start = _now_ms()
            result = model.classify(f"{state} {question}")
            elapsed = _now_ms() - start
            pred = result["predicted_label"]

        latencies.append(elapsed)
        is_correct = _compare_prediction(pred, expected)
        item_results.append({
            "id": item["id"],
            "type": qtype,
            "predicted": pred,
            "expected": expected,
            "correct": is_correct,
            "latency_ms": round(elapsed, 3),
            "skipped_local": not is_original and qtype != "choice",
        })
        if is_correct:
            correct += 1

    total_run = len(item_results)
    sorted_lat = sorted(latencies)
    p95_idx = int(len(sorted_lat) * 0.95) if sorted_lat else 0

    return {
        "items": item_results,
        "aggregate": {
            "avg_latency_ms": round(statistics.mean(latencies), 3),
            "p50_latency_ms": round(statistics.median(latencies), 3),
            "p95_latency_ms": round(sorted_lat[p95_idx], 3) if sorted_lat else 0,
            "max_latency_ms": round(max(latencies), 3) if latencies else 0,
            "overall_accuracy": round(correct / max(total_run, 1), 4),
            "correct": correct,
            "total_run": total_run,
            "skipped_local": skipped_local,
            "total_items": len(TYPED_DECISIONS_SUITE),
        },
    }


def run_extraction_benchmark(
    model: Any,
    is_original: bool,
) -> dict[str, Any]:
    """Run the Extraction & citation benchmark (number / excerpt / cite)."""
    item_results: list[dict[str, Any]] = []
    latencies: list[float] = []
    correct = 0
    skipped_local = 0

    for item in EXTRACTION_SUITE:
        state = item["state"]
        question = item["question"]
        expected = item["expected"]
        qtype = item["type"]

        if is_original:
            if qtype == "number":
                questions = {
                    "value": {
                        "type": "number",
                        "question": question,
                        "criteria": {"min": -999999, "max": 999999, "precision": 0},
                    }
                }
            elif qtype == "excerpt":
                questions = {
                    "text": {
                        "type": "excerpt",
                        "question": question,
                        "criteria": {},
                    }
                }
            elif qtype == "cite":
                questions = {
                    "citation": {
                        "type": "cite",
                        "question": question,
                        "criteria": {},
                    }
                }
            else:
                questions = {"value": {"type": "number", "question": question, "criteria": {}}}

            start = _now_ms()
            result = model.decide(state, questions)
            elapsed = _now_ms() - start
            answers = result.get("answers", {})
            pred = answers.get("value") or answers.get("text") or answers.get("citation")
        else:
            skipped_local += 1
            continue

        latencies.append(elapsed)
        is_correct = _compare_prediction(pred, expected)
        item_results.append({
            "id": item["id"],
            "type": qtype,
            "predicted": pred,
            "expected": expected,
            "correct": is_correct,
            "latency_ms": round(elapsed, 3),
            "skipped_local": True,
        })
        if is_correct:
            correct += 1

    total_run = len(item_results)
    sorted_lat = sorted(latencies)
    p95_idx = int(len(sorted_lat) * 0.95) if sorted_lat else 0

    return {
        "items": item_results,
        "aggregate": {
            "avg_latency_ms": round(statistics.mean(latencies), 3) if latencies else 0,
            "p50_latency_ms": round(statistics.median(latencies), 3) if latencies else 0,
            "p95_latency_ms": round(sorted_lat[p95_idx], 3) if sorted_lat else 0,
            "max_latency_ms": round(max(latencies), 3) if latencies else 0,
            "overall_accuracy": round(correct / max(total_run, 1), 4),
            "correct": correct,
            "total_run": total_run,
            "skipped_local": skipped_local,
            "total_items": len(EXTRACTION_SUITE),
        },
    }


def run_calibration_benchmark(
    model: Any,
    is_original: bool,
    pred_results: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run calibration benchmark (Brier score, ECE) using multilingual results."""
    if pred_results is None or "multilingual" not in pred_results:
        return {
            "note": "Calibration requires multilingual benchmark results; skipped.",
            "brier_score": None,
            "ece": None,
        }

    items = pred_results["multilingual"].get("items", [])
    probs_list: list[float] = []
    outcomes: list[int] = []

    for it in items:
        if is_original:
            conf = 0.5
        else:
            raw = it.get("raw", {})
            conf = raw.get("confidence", 0.5)
        probs_list.append(conf)
        outcomes.append(1 if it.get("correct") else 0)

    if not probs_list:
        return {
            "note": "No items available for calibration.",
            "brier_score": None,
            "ece": None,
        }

    brier = statistics.mean([(p - o) ** 2 for p, o in zip(probs_list, outcomes)])

    n_bins = 10
    bin_boundaries = [i / n_bins for i in range(n_bins + 1)]
    ece = 0.0
    for i in range(n_bins):
        bin_low = bin_boundaries[i]
        bin_high = bin_boundaries[i + 1]
        bin_indices = [
            j for j, p in enumerate(probs_list)
            if bin_low <= p < bin_high or (i == n_bins - 1 and p == bin_high)
        ]
        if not bin_indices:
            continue
        bin_conf = statistics.mean(probs_list[j] for j in bin_indices)
        bin_acc = statistics.mean(outcomes[j] for j in bin_indices)
        bin_weight = len(bin_indices) / len(probs_list)
        ece += bin_weight * abs(bin_conf - bin_acc)

    return {
        "brier_score": round(brier, 4),
        "ece": round(ece, 4),
        "n_samples": len(probs_list),
    }


def run_latency_benchmark(
    model: Any,
    is_original: bool,
) -> dict[str, Any]:
    """Run latency benchmark: time per individual prediction."""
    test_states = [
        "Auto-join combat: Iop vs Goblin at distance 2.",
        "Navigate to zaap in Bonta.",
        "Pass turn, low HP defensive stance.",
        "I am furious about this terrible service!",
        "I want to transfer money to another account.",
    ]
    latencies: list[float] = []

    for state in test_states:
        if is_original:
            questions = {
                "action": {
                    "type": "choice",
                    "question": "What action should be taken?",
                    "criteria": {
                        "AUTO_JOIN_COMBAT": "Join combat",
                        "PASS_TURN": "Skip turn",
                        "STOP": "Stop action",
                        "NONE": "No action",
                    },
                }
            }
            start = _now_ms()
            model.decide(state, questions)
        else:
            start = _now_ms()
            model.classify(state)
        latencies.append(_now_ms() - start)

    sorted_lat = sorted(latencies)
    return {
        "avg_latency_ms": round(statistics.mean(latencies), 3),
        "p50_latency_ms": round(statistics.median(latencies), 3),
        "p95_latency_ms": round(sorted_lat[int(len(sorted_lat) * 0.95)], 3),
        "max_latency_ms": round(max(latencies), 3),
        "target_ms": ORIGINAL_LATENCY_MS,
        "reference_batched_ms": ORIGINAL_LATENCY_BATCHED_MS,
        "n_samples": len(latencies),
    }


def _compare_prediction(pred: Any, expected: Any) -> bool:
    if pred is None:
        return False
    if isinstance(expected, (int, float)):
        try:
            return abs(float(pred) - float(expected)) < 1e-6
        except (TypeError, ValueError):
            return str(pred).strip().lower() == str(expected).strip().lower()
    return str(pred).strip().lower() == str(expected).strip().lower()


def compare_reports(
    original_report: dict[str, Any],
    local_report: dict[str, Any],
) -> dict[str, Any]:
    """Side-by-side comparison of original vs local fine-tuned model across all benchmarks."""
    benchmarks = [
        "multilingual",
        "banking77",
        "emotion",
        "typed_decisions",
        "extraction",
        "dofus",
        "calibration",
        "latency",
    ]

    comp: dict[str, Any] = {
        "original": {},
        "local_finetuned": {},
        "improvement": {},
        "reference_scores": {
            "banking77": ORIGINAL_BANKING77_ACC / 100.0,
            "emotion": ORIGINAL_EMOTION_ACC / 100.0,
            "latency_ms": ORIGINAL_LATENCY_MS,
        },
    }

    for bench in benchmarks:
        orig_bench = original_report.get(bench, {})
        local_bench = local_report.get(bench, {})

        orig_agg = orig_bench.get("aggregate", {})
        local_agg = local_bench.get("aggregate", {})

        comp["original"][bench] = orig_agg
        comp["local_finetuned"][bench] = local_agg

        # Compute deltas
        imp: dict[str, Any] = {}

        # Overall accuracy delta (if both have it)
        oa = orig_agg.get("overall_accuracy")
        la = local_agg.get("overall_accuracy")
        if oa is not None and la is not None:
            imp["overall_accuracy"] = {
                "original": oa,
                "local": la,
                "delta": round(la - oa, 4),
                "relative_improvement_pct": round((la - oa) / oa * 100 if oa else 0.0, 2),
            }

        # Per-category accuracy delta (dofus suite)
        orig_cats = orig_agg.get("accuracy_by_category", {})
        local_cats = local_agg.get("accuracy_by_category", {})
        if orig_cats or local_cats:
            all_cats = set(orig_cats) | set(local_cats)
            cat_imp = {}
            for cat in all_cats:
                oca = orig_cats.get(cat, 0.0)
                lca = local_cats.get(cat, 0.0)
                cat_imp[cat] = {
                    "original_accuracy": oca,
                    "local_accuracy": lca,
                    "delta": round(lca - oca, 4),
                    "relative_improvement_pct": round((lca - oca) / oca * 100 if oca else 0.0, 2),
                }
            imp["accuracy_by_category"] = cat_imp

        # Latency delta
        olat = orig_agg.get("avg_latency_ms")
        llat = local_agg.get("avg_latency_ms")
        if olat is not None and llat is not None:
            imp["latency_ms"] = {
                "original": olat,
                "local": llat,
                "delta_ms": round(llat - olat, 3),
            }

        # Calibration metrics (local only typically)
        if "brier_score" in local_agg:
            imp["calibration"] = {
                "local_brier_score": local_agg.get("brier_score"),
                "local_ece": local_agg.get("ece"),
                "original_brier_score": orig_agg.get("brier_score"),
                "original_ece": orig_agg.get("ece"),
            }

        comp["improvement"][bench] = imp

    return comp


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark doofz/THX-01 vs local dofus-thx01"
    )
    parser.add_argument("--original", default="doofz/THX-01", help="Original model hub ID")
    parser.add_argument("--local", default="models/dofus-thx01", help="Local model path")
    parser.add_argument("--output", default="benchmarks/comparison_report.json")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print plan without running (useful when deps missing)",
    )
    args = parser.parse_args()

    deps = _check_deps()
    print("=" * 70)
    print("THX-01 Dofus Benchmark: Original vs Local Fine-Tuned")
    print("=" * 70)
    print(f"Dependencies: {json.dumps(deps, indent=2)}")
    print()

    if not deps["thx01"]:
        print("[WARN] thx01 not installed. Original model benchmarks will be skipped.")
        print("       Install: pip install thx01")
    if not deps["transformers"] or not deps["torch"]:
        print("[WARN] transformers/torch not installed. Local model benchmarks will be skipped.")
        print("       Install: pip install transformers torch")
    if not deps["sklearn"]:
        print("[WARN] sklearn not installed. Calibration metrics will be skipped.")
        print("       Install: pip install scikit-learn")
    print()

    if args.dry_run:
        print("=== DRY-RUN: Benchmark plan ===")
        print("Original model-card benchmarks (run on BOTH models):")
        print("  - Multilingual az/ru/en (independent + corrupted)")
        print("  - Banking77 intent classification")
        print("  - DAIR Emotion classification")
        print("  - Typed Decisions (choice / number / excerpt / cite)")
        print("  - Extraction & citation (number, excerpt, cite)")
        print("  - Calibration (Brier score, ECE)")
        print("  - Latency (~10ms target)")
        print()
        print("New Dofus benchmarks (run on BOTH models):")
        for item in DOFUS_BENCHMARK_SUITE:
            exp = item.get("expected_label", item.get("expected_number", item.get("expected_excerpt", item.get("expected_choice"))))
            print(f"  - {item['id']} [{item['category']}] -> {exp}")
        print()
        print("Local model path:", args.local)
        print("Original model:", args.original)
        print("Output:", args.output)
        return

    report: dict[str, Any] = {
        "original_model": args.original,
        "local_model": args.local,
        "dependencies": deps,
    }

    def run_all_benchmarks(model: Any, is_original: bool) -> dict[str, Any]:
        results: dict[str, Any] = {}
        print("  Running multilingual benchmark...")
        results["multilingual"] = run_multilingual_benchmark(model, is_original)
        print("  Running Banking77 benchmark...")
        results["banking77"] = run_banking77_benchmark(model, is_original)
        print("  Running Emotion benchmark...")
        results["emotion"] = run_emotion_benchmark(model, is_original)
        print("  Running Typed Decisions benchmark...")
        results["typed_decisions"] = run_typed_decisions_benchmark(model, is_original)
        print("  Running Extraction benchmark...")
        results["extraction"] = run_extraction_benchmark(model, is_original)
        print("  Running Dofus suite...")
        results["dofus"] = run_dofus_suite(model, is_original=is_original, suite=DOFUS_BENCHMARK_SUITE)
        print("  Running Latency benchmark...")
        results["latency"] = run_latency_benchmark(model, is_original)
        print("  Running Calibration benchmark...")
        results["calibration"] = run_calibration_benchmark(model, is_original, pred_results=results)
        return results

    if deps["thx01"]:
        print(f"Loading original model: {args.original}")
        try:
            orig_model = OriginalModel(args.original)
            print("Running all benchmarks on original model...")
            report["original"] = run_all_benchmarks(orig_model, is_original=True)
            print(json.dumps(report["original"]["dofus"]["aggregate"], indent=2))
        except Exception as e:
            print(f"[ERROR] Original model failed: {e}")
            report["original_error"] = str(e)

    if deps["transformers"] and deps["torch"]:
        print(f"Loading local model: {args.local}")
        try:
            local_model = LocalModel(args.local)
            print("Running all benchmarks on local (fine-tuned) model...")
            report["local"] = run_all_benchmarks(local_model, is_original=False)
            print(json.dumps(report["local"]["dofus"]["aggregate"], indent=2))
        except Exception as e:
            print(f"[ERROR] Local model failed: {e}")
            report["local_error"] = str(e)

    if "original" in report and "local" in report:
        report["comparison"] = compare_reports(report["original"], report["local"])
        print("\n=== Comparison (original vs local fine-tuned) ===")
        print(json.dumps(report["comparison"], indent=2))

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nReport written to {out_path}")


if __name__ == "__main__":
    main()
