"""Production-grade benchmark: local dofus-thx01 vs original doofz/THX-01."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import statistics
import sys
import time
from pathlib import Path

if os.environ.get("PYTHONUNBUFFERED") != "1":
    sys.stdout.reconfigure(line_buffering=True, write_through=True, encoding="utf-8")

BENCH_DIR = Path("benchmarks")
BENCH_DIR.mkdir(parents=True, exist_ok=True)

_PROGRESS = {
    "phase": "init",
    "phase_pct": 0.0,
    "overall_pct": 0.0,
    "current": "",
    "start_time": time.time(),
}


def _status(msg: str) -> None:
    elapsed = time.time() - _PROGRESS["start_time"]
    sys.stdout.write(f"[{elapsed:6.1f}s] {msg}\n")
    sys.stdout.flush()


def _progress(phase: str, phase_pct: float, current: str, overall_pct: float) -> None:
    _PROGRESS["phase"] = phase
    _PROGRESS["phase_pct"] = phase_pct
    _PROGRESS["current"] = current
    _PROGRESS["overall_pct"] = overall_pct
    bar_width = 40
    filled = int(bar_width * overall_pct / 100.0)
    bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
    phase_bar = "\u2588" * int(20 * phase_pct / 100.0) + "\u2591" * (20 - int(20 * phase_pct / 100.0))
    sys.stdout.write(
        f"\r  Overall: |{bar}| {overall_pct:5.1f}%  |  "
        f"Phase: {phase} |{phase_bar}| {phase_pct:5.1f}%  |  {current}"
    )
    sys.stdout.flush()


def _done() -> None:
    sys.stdout.write("\n")
    sys.stdout.flush()


_HAS_THX01 = False
_HAS_TRANSFORMERS = False
_HAS_TORCH = False
_HAS_SKLEARN = False

try:
    import thx01
    _HAS_THX01 = True
except ImportError:
    thx01 = None

try:
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    _HAS_TRANSFORMERS = True
except ImportError:
    AutoModelForSequenceClassification = None
    AutoTokenizer = None

try:
    import torch
    _HAS_TORCH = True
except ImportError:
    torch = None

try:
    from sklearn.metrics import brier_score_loss
    from sklearn.calibration import calibration_curve
    _HAS_SKLEARN = True
except ImportError:
    brier_score_loss = None
    calibration_curve = None


def _check_deps():
    return {
        "thx01": _HAS_THX01,
        "transformers": _HAS_TRANSFORMERS,
        "torch": _HAS_TORCH,
        "sklearn": _HAS_SKLEARN,
    }

_LANG_COLUMNS = ["independent_az", "independent_ru", "independent_en", "corrupted_az", "corrupted_ru", "corrupted_en"]
ORIGINAL_REFERENCE_SCORES = {"THX-01": [97.1, 98.3, 98.3, 97.9, 96.6, 97.9]}
ORIGINAL_BANKING77_ACC = 94.2
ORIGINAL_EMOTION_ACC = 91.7
ORIGINAL_TYPED_DECISIONS_ACC = 89.4
ORIGINAL_EXTRACTION_ACC = 92.1

DAIR_EMOTION_SUITE = [{"id": "emo_001", "text": "I am so angry about this situation!", "expected_emotion": "anger", "dofus_label": "PASS_TURN"}, {"id": "emo_002", "text": "What a wonderful day to explore the world.", "expected_emotion": "joy", "dofus_label": "AUTO_JOIN_COMBAT"}, {"id": "emo_003", "text": "I feel so sad and alone right now.", "expected_emotion": "sadness", "dofus_label": "STOP"}, {"id": "emo_004", "text": "Something dangerous is approaching!", "expected_emotion": "fear", "dofus_label": "SHIELD_DEFENSE"}, {"id": "emo_005", "text": "I can't believe this is happening!", "expected_emotion": "surprise", "dofus_label": "QUEST_GUIDE"}, {"id": "emo_006", "text": "The weather seems fine today.", "expected_emotion": "neutral", "dofus_label": "NONE"}]
BANKING77_SUITE = [{"id": "bank_001", "text": "I want to transfer money to my savings account.", "expected_intent": "transfer"}, {"id": "bank_002", "text": "What is my current account balance?", "expected_intent": "balance_check"}, {"id": "bank_003", "text": "I need to report a lost credit card.", "expected_intent": "report_lost_card"}, {"id": "bank_004", "text": "How do I open a new checking account?", "expected_intent": "open_account"}, {"id": "bank_005", "text": "I want to apply for a personal loan.", "expected_intent": "loan_application"}]
TYPED_DECISIONS_SUITE = [{"id": "td_001", "type": "choice", "question": "Should the character attack or defend?", "criteria": {"ATTACK": "Attack", "DEFEND": "Defend", "PASS_TURN": "Skip turn"}, "expected": "ATTACK"}, {"id": "td_002", "type": "number", "question": "How many potions are in the inventory?", "criteria": {"min": 0, "max": 999, "precision": 0}, "expected": 5}, {"id": "td_003", "type": "excerpt", "question": "What is the enemy name?", "criteria": {}, "expected": "Goblin"}, {"id": "td_004", "type": "cite", "question": "Where is the quest item located?", "criteria": {}, "expected": "Forest of Secrets"}]
EXTRACTION_SUITE = [{"id": "ext_001", "type": "number", "question": "Extract the gold amount from: 'You found 250 gold.'", "expected": 250}, {"id": "ext_002", "type": "excerpt", "question": "Extract the item name: 'You found a Rusty Sword.'", "expected": "Rusty Sword"}, {"id": "ext_003", "type": "cite", "question": "Cite the source: 'According to the Ancient Tome...'", "expected": "Ancient Tome"}]
DOFUS_BENCHMARK_SUITE = [{"id": "dofus_001", "category": "combat_action", "state": "Enemy goblin nearby, 50 HP", "expected_label": "ATTACK"}, {"id": "dofus_002", "category": "combat_action", "state": "Low health, ally needs help", "expected_label": "FOLLOW"}, {"id": "dofus_003", "category": "combat_action", "state": "No enemies in range", "expected_label": "PASS_TURN"}, {"id": "dofus_004", "category": "item_extraction", "state": "Inventory contains Iron Ore", "expected_number": 1}, {"id": "dofus_005", "category": "item_extraction", "state": "Found Health Potion x3", "expected_number": 3}, {"id": "dofus_006", "category": "stance", "state": "Defensive stance recommended", "expected_label": "SHIELD_DEFENSE"}, {"id": "dofus_007", "category": "stance", "state": "Aggressive stance recommended", "expected_label": "ATTACK"}, {"id": "dofus_008", "category": "map", "state": "Need to go to Bonta via Zaap", "expected_label": "NAVIGATE_ZAAP"}, {"id": "dofus_009", "category": "map", "state": "Navigate to coordinates 12,-34", "expected_label": "NAVIGATE_COORDS"}, {"id": "dofus_010", "category": "spell", "state": "Cast healing spell on ally", "expected_label": "QUEST_GUIDE"}, {"id": "dofus_011", "category": "npc_dialogue", "state": "Talk to the quest NPC", "expected_label": "QUEST_GUIDE"}, {"id": "dofus_012", "category": "npc_dialogue", "state": "No NPC nearby", "expected_label": "NONE"}]

class OriginalModel:
    def __init__(self, model_name: str):
        if not _HAS_THX01:
            raise RuntimeError("thx01 not installed. pip install thx01")
        self.agent = thx01.load(model_name)
        self.model_name = model_name

    def decide(self, state: str, questions: dict) -> dict:
        return self.agent.decide(state, questions)


class LocalModel:
    def __init__(self, model_path):
        if not _HAS_TRANSFORMERS or not _HAS_TORCH:
            raise RuntimeError("transformers and torch not installed")
        model_path = str(model_path)
        self.tokenizer = AutoTokenizer.from_pretrained(model_path)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_path)
        self.model.eval()
        self.model_path = model_path
        self.id2label = self.model.config.id2label
        # Load labels.json for proper label name mapping (LABEL_X -> actual label names)
        labels_path = os.path.join(model_path, "labels.json")
        if os.path.exists(labels_path):
            try:
                with open(labels_path, "r", encoding="utf-8") as f:
                    labels_meta = json.load(f)
                label_map = labels_meta.get("label_map", {})
                if label_map:
                    # Build mapping from LABEL_X -> actual label name
                    # labels.json: {"NONE": 0, "FOLLOW": 1, ...} -> {0: "NONE", 1: "FOLLOW", ...}
                    self.id2label = {int(v): k for k, v in label_map.items()}
            except Exception:
                pass  # Fall back to model.config.id2label

    def classify(self, text: str) -> dict:
        inputs = self.tokenizer(text, return_tensors="pt", truncation=True, max_length=8192, padding=True)
        if _HAS_TORCH and torch.cuda.is_available():
            inputs = {k: v.cuda() for k, v in inputs.items()}
            self.model.cuda()
        grad_ctx = torch.no_grad() if _HAS_TORCH else _nullcontext()
        with grad_ctx:
            logits = self.model(**inputs).logits
            probs = torch.softmax(logits, dim=-1)[0]
        pred_id = int(probs.argmax())
        return {"predicted_label": self.id2label[pred_id], "predicted_id": pred_id, "confidence": float(probs[pred_id]), "probabilities": {self.id2label[i]: float(p) for i, p in enumerate(probs)}}

    def decide(self, state: str, questions: dict) -> dict:
        answers = {}
        not_applicable = []
        for qname, qcfg in questions.items():
            qtype = qcfg.get("type", "choice")
            question = qcfg.get("question", "")
            if qtype == "choice":
                choices = list(qcfg.get("criteria", {}).keys())
                text = f"{state} {question}"
                result = self.classify(text)
                pred_label = result["predicted_label"]
                answers[qname] = pred_label if pred_label in choices else (choices[0] if choices else pred_label)
            elif qtype in ("number", "excerpt", "cite"):
                not_applicable.append(qname)
                answers[qname] = None
            else:
                text = f"{state} {question}"
                result = self.classify(text)
                answers[qname] = result["predicted_label"]
        return {"answers": answers, "not_applicable": not_applicable, "model_type": "local_classifier"}


class _nullcontext:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

def _now_ms():
    return time.perf_counter() * 1000


def _compare_prediction(pred, expected):
    if isinstance(expected, (int, float)):
        try:
            return float(pred) == float(expected)
        except (TypeError, ValueError):
            return False
    if pred is None:
        return False
    return str(pred).strip().lower() == str(expected).strip().lower()


def run_language_benchmark(model, is_original):
    return {"columns": _LANG_COLUMNS, "note": "Requires labeled test sets", "reference_scores": ORIGINAL_REFERENCE_SCORES}


def run_banking77_benchmark(model, is_original):
    item_results = []
    latencies = []
    correct = 0
    for item in BANKING77_SUITE:
        text = item["text"]
        expected = item["expected_intent"]
        if is_original:
            questions = {"intent": {"type": "choice", "question": "What is the banking intent?", "criteria": {"transfer": "Transfer", "balance_check": "Balance", "report_lost_card": "Lost card", "open_account": "Open account", "loan_application": "Loan"}}}
            start = _now_ms()
            result = model.decide(text, questions)
            elapsed = _now_ms() - start
            pred = result.get("answers", {}).get("intent")
        else:
            start = _now_ms()
            result = model.classify(text)
            elapsed = _now_ms() - start
            pred = result["predicted_label"]
        latencies.append(elapsed)
        ok = _compare_prediction(pred, expected)
        item_results.append({"id": item["id"], "predicted": pred, "expected": expected, "correct": ok, "latency_ms": round(elapsed, 3)})
        if ok:
            correct += 1
    sl = sorted(latencies)
    p95 = sl[int(len(sl) * 0.95)] if sl else 0
    return {"items": item_results, "aggregate": {"avg_latency_ms": round(statistics.mean(latencies), 3) if latencies else 0, "p50_latency_ms": round(statistics.median(latencies), 3) if latencies else 0, "p95_latency_ms": round(p95, 3), "max_latency_ms": round(max(latencies), 3) if latencies else 0, "overall_accuracy": round(correct / max(len(item_results), 1), 4), "correct": correct, "total_run": len(item_results), "reference_acc": ORIGINAL_BANKING77_ACC / 100.0, "total_items": len(BANKING77_SUITE)}}

def run_emotion_benchmark(model, is_original):
    item_results = []
    latencies = []
    correct = 0
    skipped = 0
    e2d = {"anger": "PASS_TURN", "joy": "AUTO_JOIN_COMBAT", "sadness": "STOP", "fear": "SHIELD_DEFENSE", "surprise": "QUEST_GUIDE", "neutral": "NONE"}
    for item in DAIR_EMOTION_SUITE:
        text = item["text"]
        expected = item["expected_emotion"]
        dl = item.get("dofus_label")
        if is_original:
            questions = {"emotion": {"type": "choice", "question": "What emotion?", "criteria": {"anger": "Anger", "joy": "Joy", "sadness": "Sadness", "fear": "Fear", "surprise": "Surprise", "neutral": "Neutral"}}}
            start = _now_ms()
            result = model.decide(text, questions)
            elapsed = _now_ms() - start
            pred = result.get("answers", {}).get("emotion")
        else:
            if not dl:
                skipped += 1
                continue
            start = _now_ms()
            result = model.classify(text)
            elapsed = _now_ms() - start
            pl = result["predicted_label"]
            pred = None
            for emo, dlab in e2d.items():
                if dlab == pl:
                    pred = emo
                    break
            if pred is None:
                pred = pl
        latencies.append(elapsed)
        ok = _compare_prediction(pred, expected)
        item_results.append({"id": item["id"], "predicted": pred, "expected": expected, "correct": ok, "latency_ms": round(elapsed, 3), "skipped_local": not is_original and not dl})
        if ok:
            correct += 1
    sl = sorted(latencies)
    p95 = sl[int(len(sl) * 0.95)] if sl else 0
    return {"items": item_results, "aggregate": {"avg_latency_ms": round(statistics.mean(latencies), 3) if latencies else 0, "p50_latency_ms": round(statistics.median(latencies), 3) if latencies else 0, "p95_latency_ms": round(p95, 3), "max_latency_ms": round(max(latencies), 3) if latencies else 0, "overall_accuracy": round(correct / max(len(item_results), 1), 4), "correct": correct, "total_run": len(item_results), "skipped_local": skipped, "reference_acc": ORIGINAL_EMOTION_ACC / 100.0, "total_items": len(DAIR_EMOTION_SUITE)}}


def _run_qa_suite(model, is_original, suite, reference_acc, use_item_criteria):
    item_results = []
    latencies = []
    correct = 0
    for item in suite:
        qtype = item["type"]
        question = item["question"]
        expected = item["expected"]
        if is_original:
            criteria = item.get("criteria", {}) if use_item_criteria else {}
            questions = {qtype: {"type": qtype, "question": question, "criteria": criteria}}
            start = _now_ms()
            result = model.decide(question, questions)
            elapsed = _now_ms() - start
            pred = result.get("answers", {}).get(qtype)
        else:
            if qtype in ("number", "excerpt", "cite"):
                continue
            start = _now_ms()
            result = model.classify(question)
            elapsed = _now_ms() - start
            pred = result["predicted_label"]
        latencies.append(elapsed)
        ok = _compare_prediction(pred, expected)
        item_results.append({"id": item["id"], "type": qtype, "predicted": pred, "expected": expected, "correct": ok, "latency_ms": round(elapsed, 3)})
        if ok:
            correct += 1
    sl = sorted(latencies)
    p95 = sl[int(len(sl) * 0.95)] if sl else 0
    return {"items": item_results, "aggregate": {"avg_latency_ms": round(statistics.mean(latencies), 3) if latencies else 0, "p50_latency_ms": round(statistics.median(latencies), 3) if latencies else 0, "p95_latency_ms": round(p95, 3), "max_latency_ms": round(max(latencies), 3) if latencies else 0, "overall_accuracy": round(correct / max(len(item_results), 1), 4), "correct": correct, "total_run": len(item_results), "reference_acc": reference_acc / 100.0, "total_items": len(suite)}}


def run_typed_decisions_benchmark(model, is_original):
    return _run_qa_suite(model, is_original, TYPED_DECISIONS_SUITE,
                         ORIGINAL_TYPED_DECISIONS_ACC, True)


def run_extraction_benchmark(model, is_original):
    return _run_qa_suite(model, is_original, EXTRACTION_SUITE,
                         ORIGINAL_EXTRACTION_ACC, False)

def run_dofus_suite(model, is_original, suite):
    item_results = []
    latencies = []
    correct = 0
    correct_by_cat = {}
    for item in suite:
        state = item["state"]
        cat = item["category"]
        if cat not in correct_by_cat:
            correct_by_cat[cat] = {"correct": 0, "total": 0}
        if is_original:
            if cat == "combat_action":
                questions = {"combat_action": {"type": "choice", "question": "What combat action?", "criteria": {"FOLLOW": "Follow", "AUTO_JOIN_COMBAT": "Join", "PASS_TURN": "Pass", "AUTO_FARM": "Farm", "STOP": "Stop", "NAVIGATE_COORDS": "Coords", "NAVIGATE_ZAAP": "Zaap", "FIND_RESOURCE": "Resource", "START_FARM_LOOP": "Farm loop", "QUEST_GUIDE": "Quest", "RESOURCE_LEVEL_REQ": "Level req", "NONE": "None"}}}
            elif cat == "item_extraction":
                questions = {"item_id": {"type": "number", "question": item.get("question", "Extract ID."), "criteria": {"min": 0, "max": 999999, "precision": 0}}}
            elif cat == "stance":
                questions = {"stance": {"type": "choice", "question": "What stance?", "criteria": {"SHIELD_DEFENSE": "Shield", "ATTACK": "Attack", "NONE": "None"}}}
            elif cat == "map":
                questions = {"navigation": {"type": "choice", "question": "Where navigate?", "criteria": {"NAVIGATE_COORDS": "Coords", "NAVIGATE_ZAAP": "Zaap", "NONE": "None"}}}
            elif cat == "spell":
                questions = {"spell_action": {"type": "choice", "question": "What spell?", "criteria": {"QUEST_GUIDE": "Cast", "NONE": "None"}}}
            elif cat == "npc_dialogue":
                questions = {"npc_action": {"type": "choice", "question": "What NPC?", "criteria": {"QUEST_GUIDE": "Talk", "NONE": "None"}}}
            else:
                questions = {"action": {"type": "choice", "question": "What action?", "criteria": {}}}
            start = _now_ms()
            result = model.decide(state, questions)
            elapsed = _now_ms() - start
            answers = result.get("answers", {})
            pred = None
            for v in answers.values():
                if v is not None:
                    pred = v
                    break
        else:
            start = _now_ms()
            result = model.classify(state)
            elapsed = _now_ms() - start
            pred = result["predicted_label"]
        latencies.append(elapsed)
        expected = item.get("expected_label", item.get("expected_number", item.get("expected_choice")))
        ok = _compare_prediction(pred, expected)
        item_results.append({"id": item["id"], "category": cat, "predicted": pred, "expected": expected, "correct": ok, "latency_ms": round(elapsed, 3)})
        if ok:
            correct += 1
        correct_by_cat[cat]["correct"] += 1 if ok else 0
        correct_by_cat[cat]["total"] += 1
    sl = sorted(latencies)
    p95 = sl[int(len(sl) * 0.95)] if sl else 0
    return {"items": item_results, "aggregate": {"avg_latency_ms": round(statistics.mean(latencies), 3) if latencies else 0, "p50_latency_ms": round(statistics.median(latencies), 3) if latencies else 0, "p95_latency_ms": round(p95, 3), "max_latency_ms": round(max(latencies), 3) if latencies else 0, "overall_accuracy": round(correct / max(len(item_results), 1), 4), "correct": correct, "total_run": len(item_results), "correct_by_cat": correct_by_cat, "total_items": len(suite)}}

def run_latency_benchmark(model, is_original):
    latencies = []
    warmup = 5
    runs = 50
    test_text = "Benchmark latency test for Dofus THX-01 model evaluation."
    for _ in range(warmup):
        if is_original:
            model.decide(test_text, {"latency": {"type": "choice", "question": "test", "criteria": {}}})
        else:
            model.classify(test_text)
    for _ in range(runs):
        start = _now_ms()
        if is_original:
            model.decide(test_text, {"latency": {"type": "choice", "question": "test", "criteria": {}}})
        else:
            model.classify(test_text)
        latencies.append(_now_ms() - start)
    sl = sorted(latencies)
    p95 = sl[int(len(sl) * 0.95)] if sl else 0
    p99 = sl[int(len(sl) * 0.99)] if sl else 0
    return {"warmup": warmup, "runs": runs, "avg_latency_ms": round(statistics.mean(latencies), 3), "p50_latency_ms": round(statistics.median(latencies), 3), "p95_latency_ms": round(p95, 3), "p99_latency_ms": round(p99, 3), "min_latency_ms": round(min(latencies), 3) if latencies else 0, "max_latency_ms": round(max(latencies), 3) if latencies else 0, "target_ms": 10.0, "meets_target": round(statistics.mean(latencies), 3) <= 10.0}


def run_calibration_benchmark(model, is_original, pred_results):
    if not _HAS_SKLEARN:
        return {"skipped": True, "reason": "scikit-learn not installed"}
    confs = []
    accs = []
    for br in pred_results.values():
        if not isinstance(br, dict):
            continue
        for it in br.get("items", []):
            if not isinstance(it, dict):
                continue
            if it.get("correct") is not None:
                accs.append(1 if it["correct"] else 0)
            confs.append(float(it.get("confidence", 1.0 if is_original else 0.8)))
    if not confs:
        return {"skipped": True, "reason": "No data"}
    try:
        brier = brier_score_loss(accs, confs)
    except Exception:
        brier = float("nan")
    try:
        fp, mp = calibration_curve(accs, confs, n_bins=10)
        ece = sum(abs(a - b) for a, b in zip(fp, mp)) / len(fp)
    except Exception:
        ece = float("nan")
    return {"brier_score": round(brier, 4) if brier == brier else None, "ece": round(ece, 4) if ece == ece else None, "num_samples": len(confs)}

def compare_reports(original, local):
    comp = {}
    for k in original:
        if k not in local:
            continue
        oa = original[k].get("aggregate", {})
        la = local[k].get("aggregate", {})
        if not oa or not la:
            continue
        comp[k] = {"original_accuracy": oa.get("overall_accuracy", 0), "local_accuracy": la.get("overall_accuracy", 0), "accuracy_delta": round(la.get("overall_accuracy", 0) - oa.get("overall_accuracy", 0), 4), "original_avg_latency_ms": oa.get("avg_latency_ms", 0), "local_avg_latency_ms": la.get("avg_latency_ms", 0), "latency_delta_ms": round(la.get("avg_latency_ms", 0) - oa.get("avg_latency_ms", 0), 3)}
    return comp


def _load_original(model_name):
    _status(f"Downloading/loading original model: {model_name}")
    _progress("loading_original", 50.0, f"thx01.load('{model_name}')", 12.5)
    m = OriginalModel(model_name)
    _progress("loading_original", 100.0, f"Original '{model_name}' ready", 12.5)
    return m


def _load_local(model_path):
    _status(f"Downloading/loading local model: {model_path}")
    _progress("loading_local", 0.0, f"AutoTokenizer.from_pretrained('{model_path}')", 25.0)
    m = LocalModel(model_path)
    _progress("loading_local", 100.0, f"Local '{model_path}' ready", 25.0)
    return m


def load_models_parallel(oname, lpath):
    res = {}
    _status("Starting parallel model loading...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        futs = {}
        if _HAS_THX01 and oname:
            futs[ex.submit(_load_original, oname)] = "original"
        if _HAS_TRANSFORMERS and _HAS_TORCH and lpath:
            futs[ex.submit(_load_local, lpath)] = "local"
        for f in concurrent.futures.as_completed(futs):
            mt = futs[f]
            try:
                res[mt] = f.result()
                _status(f"{mt} model loaded")
            except Exception as e:
                import traceback
                tb_file = open(f"C:/temp/{mt}_tb.txt", "w")
                traceback.print_exc(file=tb_file)
                tb_file.close()
                _status(f"{mt} failed: {e}")
                res[f"{mt}_error"] = str(e)
    return res

def main():
    p = argparse.ArgumentParser(description="Benchmark doofz/THX-01 vs local dofus-thx01")
    p.add_argument("--original", default=None)
    p.add_argument("--local", default="models/dofus-thx01")
    p.add_argument("--output", default="benchmarks/comparison_report.json")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    deps = _check_deps()
    print("=" * 70)
    print("THX-01 Dofus Benchmark: Original vs Local Fine-Tuned")
    print("=" * 70)
    print(f"Deps: {json.dumps(deps, indent=2)}")
    print()
    if not deps["thx01"]:
        print("[WARN] thx01 not installed. Original skipped.")
    if not deps["transformers"] or not deps["torch"]:
        print("[WARN] transformers/torch not installed. Local skipped.")
    if not deps["sklearn"]:
        print("[WARN] sklearn not installed. Calibration skipped.")
    print()
    if a.dry_run:
        print("=== DRY-RUN ===")
        print("Benchmarks: multilingual, banking77, emotion, typed_decisions, extraction, dofus, latency, calibration")
        print(f"Original: {a.original}")
        print(f"Local: {a.local}")
        print(f"Output: {a.output}")
        return

    _progress("loading_models", 0.0, "Initializing downloads...", 0.0)
    _done()
    models = load_models_parallel(a.original, a.local)
    _done()
    om = models.get("original")
    lm = models.get("local")
    report = {"original_model": a.original, "local_model": a.local, "dependencies": deps}
    phases = [("multilingual", "Running multilingual..."), ("banking77", "Running Banking77..."), ("emotion", "Running Emotion..."), ("typed_decisions", "Running Typed Decisions..."), ("extraction", "Running Extraction..."), ("dofus", "Running Dofus suite..."), ("latency", "Running Latency..."), ("calibration", "Running Calibration...")]
    n = len(phases)
    wp = 75.0 / n

    def run_all(m, iso):
        r = {}
        bp = 25.0
        for i, (bk, bm) in enumerate(phases):
            _status(bm)
            _progress(f"bench:{bk}", 0.0, bm, bp + i * wp)
            try:
                if bk == "multilingual":
                    r[bk] = run_language_benchmark(m, iso)
                elif bk == "banking77":
                    r[bk] = run_banking77_benchmark(m, iso)
                elif bk == "emotion":
                    r[bk] = run_emotion_benchmark(m, iso)
                elif bk == "typed_decisions":
                    r[bk] = run_typed_decisions_benchmark(m, iso)
                elif bk == "extraction":
                    r[bk] = run_extraction_benchmark(m, iso)
                elif bk == "dofus":
                    r[bk] = run_dofus_suite(m, is_original=iso, suite=DOFUS_BENCHMARK_SUITE)
                elif bk == "latency":
                    r[bk] = run_latency_benchmark(m, iso)
                elif bk == "calibration":
                    r[bk] = run_calibration_benchmark(m, iso, pred_results=r)
            except Exception as e:
                _status(f"[ERROR] Benchmark {bk} failed: {e}")
                r[bk] = {"error": str(e)}
            _progress(f"bench:{bk}", 100.0, f"{bm} done", bp + (i + 1) * wp)
        _done()
        return r

    if om:
        _status(f"Running benchmarks on original: {a.original}")
        try:
            report["original"] = run_all(om, True)
            _status("Original benchmarks complete")
        except Exception as e:
            _status(f"[ERROR] Original failed: {e}")
            report["original_error"] = str(e)
    if lm:
        _status(f"Running benchmarks on local: {a.local}")
        try:
            report["local"] = run_all(lm, False)
            _status("Local benchmarks complete")
        except Exception as e:
            import traceback
            tb_file = open("C:/temp/local_tb.txt", "w")
            traceback.print_exc(file=tb_file)
            tb_file.close()
            _status(f"[ERROR] Local failed: {e}")
            report["local_error"] = str(e)
    if "original" in report and "local" in report:
        _status("Computing comparison...")
        _progress("comparison", 0.0, "Computing deltas...", 95.0)
        report["comparison"] = compare_reports(report["original"], report["local"])
        _progress("comparison", 100.0, "Comparison complete", 100.0)
        print()
        print("=== Comparison ===")
        print(json.dumps(report["comparison"], indent=2))
    op = Path(a.output)
    op.parent.mkdir(parents=True, exist_ok=True)
    op.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    _progress("complete", 100.0, f"Report: {op}", 100.0)
    _done()
    print(f"\nReport written to {op}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        with open("C:/temp/main_tb.txt", "w") as f:
            traceback.print_exc(file=f)
        print(f"FATAL: {e}")
        raise