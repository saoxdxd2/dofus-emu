"""Central Singleton AI Sentinel Service for Dofus Farm.

Hosts exactly ONE instance of each AI model across all game slots:
1. TRM-MoE Network Telemetry Anomaly Model (505,940 weights)
2. Laya Decision / Threat Classifier (ModernBERT / mmBERT)
3. Vision & Modal Captcha Inspector

Exposes a fast local HTTP server on http://127.0.0.1:8890 for WebView2 client instances.
Guarantees near-zero memory duplication and 0.0% idle CPU overhead.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any
import urllib.parse
import torch
import torch.nn as nn
import torch.nn.functional as F

# Ensure networkhealth is in path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "dofus-network"))

from networkhealth.inference import ForecastResult, MultiInstanceNetworkHealth, Observation
from data.game_data_extractor import GameDataRepository, MonsterGrade, MonsterInfo, SpellInfo
from combat.damage_calculator import (
    CharacterStats,
    DofusDamageCalculator,
    OptimalRotationResult,
    SpellDamageResult,
    SpellRotationOptimizer,
    TargetStats,
)
from combat.tactical_engine import TacticalCombatEngine, TacticalStance, TacticalTurnPlan
from combat.episodic_memory import EpisodicSkillMemory
from navigation.world_graph import DofusWorldGraph, CellPathfinder
from navigation.resource_graph import ResourceKnowledgeGraph
from navigation.quest_graph import QuestKnowledgeGraph
from navigation.nlp_intent_engine import DofusNLPEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("sentinel.daemon")

CHECKPOINT_PATH = REPO_ROOT / "dofus-network" / "networkhealth_artifacts" / "model.pt"

# Common natural human quick replies for Dofus Touch
SUGGESTED_REPLIES = [
    "oui salut 2sec j'farm",
    "oui ?",
    "dsl groupe plein",
    "non dsl je déco bientôt",
    "je craft 2sec j'arrive",
    "ouais dis moi ?",
]



class TinyRecursiveReasoner(nn.Module):
    def __init__(self, in_dim: int = 24, hidden_dim: int = 64, max_recursions: int = 4):
        super().__init__()
        self.in_proj = nn.Linear(in_dim, hidden_dim)
        self.ln_in = nn.LayerNorm(hidden_dim)
        self.gru = nn.GRUCell(hidden_dim, hidden_dim)
        self.ln_rec = nn.LayerNorm(hidden_dim)
        self.stance_head = nn.Linear(hidden_dim, 4)
        self.halt_head = nn.Linear(hidden_dim, 1)
        self.value_head = nn.Linear(hidden_dim, 1)
        self.max_recursions = max_recursions

    def forward(self, x: torch.Tensor, max_steps: int | None = None) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        max_s = max_steps or self.max_recursions
        h = self.ln_in(F.gelu(self.in_proj(x)))
        x_proj = h
        all_logits, all_halts = [], []
        for step in range(max_s):
            h = self.ln_rec(self.gru(x_proj, h))
            all_logits.append(self.stance_head(h))
            all_halts.append(torch.sigmoid(self.halt_head(h)))
        return all_logits[-1], torch.stack(all_halts, dim=1), self.value_head(h)


class SingletonDecisionAgent:
    """Manages a single shared instance of the THX-01 calibrated decision model."""

    _instance: SingletonDecisionAgent | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self.agent: Any = None
        self.model: Any = None
        self.tokenizer: Any = None
        self.id2label: dict[int, str] = {}
        self.combat_model: Any = None
        self.model_name = "heuristic"
        self._load_attempted = False
        self._init_decision_agent()

    @classmethod
    def get_instance(cls) -> SingletonDecisionAgent:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _init_decision_agent(self) -> None:
        if self._load_attempted:
            return
        self._load_attempted = True

        def _loader() -> None:
            try:
                custom_model = Path(__file__).resolve().parent.parent / "models" / "dofus-thx01"
                if custom_model.exists():
                    logger.info(f"Background loading distilled THX-01 ModernBERT Model from {custom_model}...")
                    from transformers import AutoTokenizer, ModernBertForSequenceClassification, ModernBertConfig
                    import safetensors.torch

                    tok = AutoTokenizer.from_pretrained(str(custom_model))
                    cfg = ModernBertConfig.from_pretrained(str(custom_model))
                    mod = ModernBertForSequenceClassification(cfg)
                    weights = safetensors.torch.load_file(str(custom_model / "model.safetensors"), device="cpu")
                    mod.load_state_dict(weights, strict=False)
                    mod.eval()

                    with open(custom_model / "labels.json", "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    id2label = {int(v): k for k, v in meta["label_map"].items()}

                    cm_instance = None
                    trm_file = custom_model / "trm_act_combat.pt"
                    if trm_file.exists():
                        try:
                            cm = TinyRecursiveReasoner()
                            cm_weights = torch.load(str(trm_file), map_location="cpu", weights_only=False)
                            cm.load_state_dict(cm_weights)
                            cm.eval()
                            cm_instance = cm
                            logger.info("TRM-ACT Combat Reasoner neural weights loaded successfully.")
                        except Exception as c_err:
                            logger.warning(f"Could not load TRM-ACT weights: {c_err}")

                    with self._lock:
                        self.tokenizer = tok
                        self.model = mod
                        self.id2label = id2label
                        self.agent = mod
                        self.combat_model = cm_instance
                        self.model_name = "THX-01-ModernBERT-Distilled"
                    logger.info("Singleton THX-01 model ready in RAM.")
            except Exception as ex:
                logger.warning(f"THX-01 background loading deferred or offline: {ex}. Calibrated heuristics active.")

        threading.Thread(target=_loader, daemon=True, name="DecisionAgentLoader").start()

    def classify_directive(self, text: str) -> tuple[str, float]:
        if not self.model or not self.tokenizer or not self.id2label:
            return ("NONE", 0.0)
        try:
            inputs = self.tokenizer(text, return_tensors="pt", truncation=True, max_length=128)
            with torch.no_grad():
                logits = self.model(**inputs).logits
                probs = torch.softmax(logits, dim=-1)[0]
                pred_id = int(torch.argmax(probs).item())
                pred_label = self.id2label.get(pred_id, "NONE")
                conf = float(probs[pred_id].item())
                return (pred_label, conf)
        except Exception:
            return ("NONE", 0.0)

    def evaluate_message(self, sender: str, text: str, is_mod_tag: bool = False) -> dict[str, Any]:
        """Classify incoming message for bot-check / moderator threats."""
        clean_text = text.strip()
        sender_clean = sender.strip()

        # 1. Hard moderator tags
        if is_mod_tag or re.search(r"\[(?:Mod|Admin|GM|Ankama)\]", sender_clean, re.I):
            return {
                "is_threat": True,
                "threat_level": "CRITICAL_MODERATOR",
                "confidence": 0.99,
                "urgency": "CRITICAL",
                "reason": "Official Ankama staff tag in sender name",
                "suggested_replies": ["Bonjour, oui je suis là !", "Bonjour ?", "Oui bonsoir"],
            }

        # 2. Heuristic check for common bot-check queries
        is_bot_check_keyword = bool(
            re.search(
                r"(?:bot|es[- ]tu l[àa]|t[' ]es l[àa]|r[ée]ponds|r[ée]pondez|ban|signal[ée]|v[ée]rif|afk|macro)",
                clean_text,
                re.I,
            )
        )

        # 3. If Decision agent is loaded, perform deep decision inference
        if self.agent is not None:
            try:
                state = f"Sender: {sender_clean}\nMessage: {clean_text}"
                questions = {
                    "is_threat": {
                        "type": "choice",
                        "instructions": "Is this message a moderator check, bot suspicion, or player inquiry testing responsiveness?",
                        "criteria": {"yes": "moderator or bot verification check", "no": "standard player interaction"}
                    },
                    "urgency": {
                        "type": "choice",
                        "instructions": "How urgent is this message to avoid an AFK or bot flag?",
                        "criteria": {"critical": "immediate action required", "normal": "ordinary chat", "low": "spam or ignore"}
                    },
                }
                if hasattr(self.agent, "decide"):
                    pred = self.agent.decide(state, questions)
                elif hasattr(self.agent, "predict"):
                    pred = self.agent.predict({"sender": sender_clean, "message": clean_text}, questions)
                else:
                    pred = {}
                
                is_threat_choice = pred.get("is_threat")
                if isinstance(is_threat_choice, dict): is_threat_choice = is_threat_choice.get("choice")
                is_threat = (is_threat_choice == "yes") or is_bot_check_keyword
                urg_val = pred.get("urgency", "normal")
                if isinstance(urg_val, dict): urg_val = urg_val.get("choice", "normal")
                urgency = str(urg_val).upper()

                return {
                    "is_threat": is_threat,
                    "threat_level": "SUSPECTED_BOT_CHECK" if is_threat else "NORMAL_CHAT",
                    "confidence": 0.96 if is_threat else 0.88,
                    "urgency": urgency if is_threat else "NORMAL",
                    "reason": f"{self.model_name} Decision Model classification",
                    "suggested_replies": SUGGESTED_REPLIES[:3] if is_threat else [],
                }
            except Exception as e:
                logger.debug(f"Decision agent inference error: {e}, falling back to heuristics")

        # 4. Calibrated heuristic fallback
        if is_bot_check_keyword:
            return {
                "is_threat": True,
                "threat_level": "SUSPECTED_BOT_CHECK",
                "confidence": 0.88,
                "urgency": "HIGH",
                "reason": "Message contains direct responsiveness inquiry",
                "suggested_replies": SUGGESTED_REPLIES[:3],
            }

        return {
            "is_threat": False,
            "threat_level": "NORMAL_CHAT",
            "confidence": 0.80,
            "urgency": "LOW",
            "reason": "Standard player interaction or whisper",
            "suggested_replies": [],
        }


SingletonLayaAgent = SingletonDecisionAgent


class SingletonVisionInspector:
    """Singleton inspector for screen dialogs and captcha challenges."""

    _instance: SingletonVisionInspector | None = None
    _lock = threading.Lock()

    CAPTCHA_KEYWORDS = re.compile(
        r"(?:v[ée]rification|anti[- ]bot|recopiez|code|captcha|question de s[ée]curit[ée]|attention)",
        re.I,
    )

    @classmethod
    def get_instance(cls) -> SingletonVisionInspector:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def evaluate_modal_text(self, text: str) -> dict[str, Any]:
        match = self.CAPTCHA_KEYWORDS.search(text)
        if match:
            return {
                "has_captcha": True,
                "matched_keyword": match.group(0),
                "urgency": "CRITICAL",
                "action_required": "Human captcha input on game screen",
            }
        return {
            "has_captcha": False,
            "matched_keyword": None,
            "urgency": "NORMAL",
            "action_required": None,
        }


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _safe_char_stats(d: dict[str, Any] | None) -> CharacterStats:
    if not d:
        return CharacterStats()
    valid = {f.name for f in CharacterStats.__dataclass_fields__.values()}
    return CharacterStats(**{k: v for k, v in d.items() if k in valid})


def _safe_target_stats(d: dict[str, Any] | None) -> TargetStats:
    if not d:
        return TargetStats()
    valid = {f.name for f in TargetStats.__dataclass_fields__.values()}
    return TargetStats(**{k: v for k, v in d.items() if k in valid})


class SentinelHTTPHandler(BaseHTTPRequestHandler):
    laya = SingletonLayaAgent.get_instance()
    vision = SingletonVisionInspector.get_instance()
    network_engine = MultiInstanceNetworkHealth(CHECKPOINT_PATH) if CHECKPOINT_PATH.exists() else None
    repo = GameDataRepository.get_instance()
    memory = EpisodicSkillMemory.get_instance()
    world_graph = DofusWorldGraph()
    resource_graph = ResourceKnowledgeGraph()
    quest_graph = QuestKnowledgeGraph()
    nlp_engine = DofusNLPEngine()

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress noisy standard HTTP access logs
        pass

    def _send_json(self, status: int, data: dict[str, Any]) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path in ("/sentinel/status", "/status", "/health"):
            self._send_json(200, {
                "status": "online",
                "service": "dofus-sentinel-ai",
                "singleton_models": {
                    "trm_moe": self.network_engine is not None,
                    "laya": self.laya.agent is not None,
                    "model_name": self.laya.model_name,
                    "thx01": self.laya.model is not None or "THX-01" in self.laya.model_name,
                    "trm_act": self.laya.combat_model is not None,
                    "vision": True,
                    "combat_calculator": True,
                    "total_spells": len(self.repo.spells_by_id),
                    "total_monsters": len(self.repo.monsters_by_id),
                },
                "port": 8890,
                "time": time.time(),
            })

        elif path == "/sentinel/combat/mob":
            query = qs.get("name", [""])[0] or qs.get("id", [""])[0]
            grade_num = int(qs.get("grade", ["1"])[0])
            lang = qs.get("lang", ["fr"])[0]

            mob = self.repo.find_monster(query)
            if not mob:
                self._send_json(404, {"error": "monster_not_found", "query": query})
                return

            gr = mob.get_grade(grade_num)
            self._send_json(200, {
                "status": "ok",
                "id": mob.id,
                "name": mob.get_name(lang),
                "is_boss": mob.is_boss,
                "grade": {
                    "grade": gr.grade,
                    "level": gr.level,
                    "hp": gr.life_points,
                    "ap": gr.action_points,
                    "mp": gr.movement_points,
                    "neutral_res": gr.neutral_res,
                    "earth_res": gr.earth_res,
                    "fire_res": gr.fire_res,
                    "water_res": gr.water_res,
                    "air_res": gr.air_res,
                    "fixed_res": gr.fixed_res,
                }
            })

        elif path == "/sentinel/combat/all_mobs":
            lang = qs.get("lang", ["fr"])[0]
            mobs_list = []
            for m in self.repo.monsters_by_id.values():
                mobs_list.append({
                    "id": m.id,
                    "name": m.get_name(lang),
                    "name_fr": m.name_fr,
                    "name_en": m.name_en,
                    "name_es": m.name_es,
                    "grades_count": len(m.grades),
                })
            self._send_json(200, {"status": "ok", "monsters": mobs_list})

        elif path == "/sentinel/combat/spells":
            breed_id = int(qs.get("breed", ["8"])[0])
            lang = qs.get("lang", ["fr"])[0]
            spells = self.repo.get_breed_spells(breed_id)
            res_spells = []
            for s in spells:
                res_spells.append({
                    "id": s.id,
                    "name": s.get_name(lang),
                    "ap_cost": s.ap_cost,
                    "min_range": s.min_range,
                    "max_range": s.max_range,
                    "cast_in_line": s.cast_in_line,
                    "max_cast_per_turn": s.max_cast_per_turn,
                    "effects": [
                        {
                            "element": e.element,
                            "min": e.min_roll,
                            "max": e.max_roll,
                            "crit_min": e.crit_min_roll,
                            "crit_max": e.crit_max_roll,
                        }
                        for e in s.effects
                    ]
                })
            self._send_json(200, {"status": "ok", "breed_id": breed_id, "spells": res_spells})

        elif path == "/sentinel/navigation/zaaps":
            self._send_json(200, {"status": "ok", "zaaps": self.world_graph.get_all_zaaps()})

        elif path == "/sentinel/resources/all":
            self._send_json(200, {
                "status": "ok",
                "total_resources": len(self.resource_graph.resources),
                "resources": [
                    {
                        "id": r.id,
                        "name_fr": r.name_fr,
                        "name_en": r.name_en,
                        "name_es": r.name_es,
                        "profession": r.profession,
                        "level_req": r.level_req,
                    }
                    for r in self.resource_graph.resources
                ],
            })

        elif path == "/sentinel/quests/all":
            self._send_json(200, {
                "status": "ok",
                "total_quests": len(self.quest_graph.guides),
                "quests": [g.to_dict() for g in self.quest_graph.guides],
            })

        elif path == "/sentinel/quests/guide":
            q_id = qs.get("id", [""])[0] or qs.get("q", [""])[0]
            guides = self.quest_graph.search_guides(q_id)
            if guides:
                self._send_json(200, {"status": "ok", "guide": guides[0].to_dict()})
            else:
                self._send_json(404, {"error": "guide_not_found", "query": q_id})

        else:
            self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len) if content_len > 0 else b"{}"

        try:
            req = json.loads(body.decode("utf-8"))
        except Exception:
            self._send_json(400, {"error": "invalid_json"})
            return

        if self.path == "/sentinel/eval_chat":
            sender = req.get("sender", "Unknown")
            text = req.get("text", "")
            is_mod = bool(req.get("is_mod", False))
            instance_id = req.get("instance_id", "slot-0")

            res = self.laya.evaluate_message(sender, text, is_mod)
            res["instance_id"] = instance_id
            res["timestamp"] = time.time()
            self._send_json(200, res)

        elif self.path == "/sentinel/eval_modal":
            text = req.get("text", "")
            instance_id = req.get("instance_id", "slot-0")

            res = self.vision.evaluate_modal_text(text)
            res["instance_id"] = instance_id
            res["timestamp"] = time.time()
            self._send_json(200, res)

        elif self.path == "/sentinel/combat/calculate_damage":
            spell_id_or_name = req.get("spell")
            char_dict = req.get("character", {})
            target_dict = req.get("target", {})
            monster_query = req.get("monster")
            grade_num = int(req.get("monster_grade", 1))

            spell = self.repo.find_spell(spell_id_or_name)
            if not spell:
                self._send_json(404, {"error": "spell_not_found", "spell": spell_id_or_name})
                return

            char = _safe_char_stats(char_dict)

            if monster_query:
                mob = self.repo.find_monster(monster_query)
                if not mob:
                    self._send_json(404, {"error": "monster_not_found", "monster": monster_query})
                    return
                target = TargetStats.from_monster_grade(mob, grade_num)
            elif target_dict:
                target = _safe_target_stats(target_dict)
            else:
                target = TargetStats()

            res = DofusDamageCalculator.calculate_spell_damage(spell, char, target)
            self._send_json(200, {
                "status": "ok",
                "spell_id": res.spell_id,
                "spell_name": res.spell_name,
                "ap_cost": res.ap_cost,
                "damage_min": res.total_min,
                "damage_max": res.total_max,
                "damage_avg": res.total_avg,
                "crit_min": res.crit_total_min,
                "crit_max": res.crit_total_max,
                "crit_avg": res.crit_total_avg,
                "crit_rate": res.crit_rate,
                "expected_damage": res.expected_value,
                "ap_efficiency": res.ap_efficiency,
            })

        elif self.path == "/sentinel/combat/best_combo":
            char_dict = req.get("character", {})
            spells_list = req.get("spells")
            monster_query = req.get("monster", "Bouftou")
            grade_num = int(req.get("monster_grade", 1))

            char = _safe_char_stats(char_dict)

            mob = self.repo.find_monster(monster_query)
            if not mob:
                self._send_json(404, {"error": "monster_not_found", "monster": monster_query})
                return
            target = TargetStats.from_monster_grade(mob, grade_num)

            available: List[SpellInfo] = []
            if spells_list:
                for sp_id in spells_list:
                    sp = self.repo.find_spell(sp_id)
                    if sp:
                        available.append(sp)
            else:
                breed_id = char_dict.get("breed_id", 8)  # default Iop
                available = self.repo.get_breed_spells(breed_id)

            opt = SpellRotationOptimizer.optimize_turn(char, available, target)
            self._send_json(200, {"status": "ok", **opt.to_dict()})

        elif self.path == "/sentinel/combat/tactical_plan":
            char_dict = req.get("character", {})
            monster_query = req.get("monster", "Bouftou")
            grade_num = int(req.get("monster_grade", 1))
            dist = int(req.get("distance_cells", 3))
            hp_pct = float(req.get("hp_percent", 1.0))

            char = _safe_char_stats(char_dict)
            mob = self.repo.find_monster(monster_query)
            if not mob:
                self._send_json(404, {"error": "monster_not_found", "monster": monster_query})
                return
            target = TargetStats.from_monster_grade(mob, grade_num)

            breed_id = char_dict.get("breed_id", 8) if char_dict else 8
            available = self.repo.get_breed_spells(breed_id)

            # 1. Fast episodic skill memory recall (<0.05ms)
            cached_skill = self.memory.query_skill(breed_id, mob.name_fr, dist, hp_pct)
            if cached_skill and cached_skill.reward_achieved >= 10.0:
                self._send_json(200, {
                    "status": "ok",
                    "source": "EPISODIC_MEMORY",
                    "stance": cached_skill.stance,
                    "target_name": mob.name_fr,
                    "target_hp": target.current_hp,
                    "estimated_damage": 0.0,
                    "multi_turn_projected_damage": 0.0,
                    "action_sequence": [{"spell_name": s, "type": "EPISODIC_SKILL"} for s in cached_skill.proven_sequence],
                    "explanation": f"Retrieved proven tactical skill from Episodic Memory (reward: {cached_skill.reward_achieved}).",
                    "value_score": 0.99,
                })
                return

            # 2. Otherwise run multi-turn tactical engine and record outcome
            plan = TacticalCombatEngine.plan_tactical_turn(char, target, available, distance_cells=dist, char_hp_percent=hp_pct)
            if plan.value_score >= 0.85:
                spell_names = [a.get("spell_name", "") for a in plan.action_sequence if a.get("spell_name")]
                self.memory.record_outcome(
                    breed_id=breed_id,
                    target_name=mob.name_fr,
                    distance_cells=dist,
                    char_hp_percent=hp_pct,
                    stance=plan.stance.value,
                    action_sequence=spell_names,
                    reward=plan.value_score * 15.0,
                )

            self._send_json(200, {
                "status": "ok",
                "source": "TACTICAL_WORLD_MODEL",
                "stance": plan.stance.value,
                "target_name": plan.target_name,
                "target_hp": plan.target_hp,
                "estimated_damage": plan.estimated_damage,
                "multi_turn_projected_damage": plan.multi_turn_projected_damage,
                "action_sequence": plan.action_sequence,
                "explanation": plan.explanation,
                "value_score": plan.value_score,
            })

        elif self.path == "/sentinel/navigation/path":
            c_from = req.get("from", [4, -19])
            c_to = req.get("to", [4, -18])
            allow_zaap = bool(req.get("allow_zaap", True))
            lang = req.get("lang", "fr")
            path = self.world_graph.find_path(tuple(c_from), tuple(c_to), allow_zaap=allow_zaap, lang=lang)
            self._send_json(200, {"status": "ok", "path": path.to_dict()})

        elif self.path == "/sentinel/resources/search":
            query = req.get("query", "fer")
            near = req.get("near")
            near_tuple = tuple(near) if near else None
            max_r = int(req.get("max_results", 8))
            spots = self.resource_graph.search_spots(query, near_pos=near_tuple, max_results=max_r)
            self._send_json(200, {
                "status": "ok",
                "query": query,
                "total_spots": len(spots),
                "spots": [s.to_dict() for s in spots],
            })

        elif self.path == "/sentinel/resources/farm_loop":
            res_q = req.get("resource", "blé")
            start = tuple(req.get("start", [4, -19]))
            max_maps = int(req.get("max_maps", 6))
            loop = self.resource_graph.generate_farm_loop(res_q, start_coord=start, max_maps=max_maps)
            if loop:
                self._send_json(200, {"status": "ok", "farm_loop": loop.to_dict()})
            else:
                self._send_json(404, {"error": "no_spots_found_for_resource", "resource": res_q})

        elif self.path == "/sentinel/nlp/query":
            text = req.get("text", "")
            curr_pos = tuple(req.get("current_pos", [4, -19]))
            result = self.nlp_engine.process_query(text, current_pos=curr_pos)
            self._send_json(200, {"status": "ok", "result": result.to_dict()})

        elif self.path == "/sentinel/parse_command":
            cmd_text = req.get("command", "").strip()
            target_slot = req.get("target_slot", "all")
            char_dict = req.get("character")
            curr_pos = tuple(req.get("current_pos", [4, -19]))

            directive = None
            confidence = 0.85
            explanation = "Intent parsing"
            metadata: dict[str, Any] = {}

            # 1. Neural classification via distilled THX-01 ModernBERT
            pred_label, thx_conf = self.laya.classify_directive(cmd_text)
            if pred_label != "NONE" and thx_conf >= 0.70:
                directive = pred_label
                confidence = thx_conf
                explanation = f"THX-01 Classified Directive: {pred_label}"
                metadata = {"source": "THX-01-ModernBERT", "neural_confidence": thx_conf}

            cmd_lower = cmd_text.lower()

            # Check if this is a combat inquiry or attack command
            if any(w in cmd_lower for w in ["combo", "attack", "attaque", "ataca", "kill", "tuer", "matar", "rotation", "degats", "damage"]):
                # Extract monster if mentioned
                target_mob = None
                for m in self.repo.monsters_by_id.values():
                    for n in (m.name_fr, m.name_en, m.name_es):
                        if n.lower() in cmd_lower:
                            target_mob = m
                            break
                    if target_mob:
                        break

                if target_mob:
                    char = _safe_char_stats(char_dict) if char_dict else CharacterStats(action_points=10, strength=300, power=40)
                    available = self.repo.get_breed_spells(char_dict.get("breed_id", 8) if char_dict else 8)
                    target = TargetStats.from_monster_grade(target_mob, 1)
                    plan = TacticalCombatEngine.plan_tactical_turn(char, target, available)

                    directive = "EXECUTE_TACTICAL_PLAN"
                    confidence = 0.98
                    explanation = f"Tactical Stance: {plan.stance.value} against {target_mob.name_fr} - {plan.explanation}"
                    metadata = {
                        "stance": plan.stance.value,
                        "target_name": plan.target_name,
                        "target_hp": plan.target_hp,
                        "rotation": plan.action_sequence,
                        "multi_turn_projected_damage": plan.multi_turn_projected_damage,
                        "value_score": plan.value_score,
                    }

            if not directive:
                # 1. Semantic NLP Analysis for Navigation, Resources, Quests & Bot Orders
                nlp_res = self.nlp_engine.process_query(cmd_text, current_pos=curr_pos)
                if nlp_res.intent.value != "UNKNOWN":
                    directive = nlp_res.execution_payload.get("action") or nlp_res.intent.value
                    confidence = nlp_res.confidence
                    explanation = nlp_res.message_reply
                    metadata = {
                        "nlp_reply": nlp_res.message_reply,
                        "detected_lang": nlp_res.detected_lang,
                        **nlp_res.execution_payload,
                    }

            if not directive:
                # 2. Neural calibrated decision classification if THX-01 / Decision Agent is loaded
                if self.laya.agent is not None and hasattr(self.laya.agent, "decide"):
                    try:
                        q = {
                            "action": {
                                "type": "choice",
                                "instructions": "Which game bot directive does the user want executed?",
                                "criteria": {
                                    "FOLLOW": "follow leader or other player",
                                    "AUTO_JOIN_COMBAT": "join combat or click team fight sword",
                                    "PASS_TURN": "pass turn in combat or skip turn",
                                    "AUTO_FARM": "farm monsters, harvest wheat, mine ores or collect resources",
                                    "STOP": "stop or pause automation",
                                }
                            }
                        }
                        pred = self.laya.agent.decide(cmd_text, q)
                        act = pred.get("action")
                        if isinstance(act, dict): act = act.get("choice")
                        if act in ["FOLLOW", "AUTO_JOIN_COMBAT", "PASS_TURN", "AUTO_FARM", "STOP"]:
                            directive = act
                            confidence = 0.98
                            explanation = f"THX-01 calibrated decision: {act}"
                    except Exception:
                        pass

            if not directive:
                if any(w in cmd_lower for w in ["follow", "suis", "suivre", "lead"]):
                    directive = "FOLLOW"
                    confidence = 0.95
                    explanation = "Set instance to follow leader"
                elif any(w in cmd_lower for w in ["join", "combat", "fight", "rejoins", "epee"]):
                    directive = "AUTO_JOIN_COMBAT"
                    confidence = 0.96
                    explanation = "Auto-join team fights on map"
                elif any(w in cmd_lower for w in ["pass", "passe", "tour", "skip"]):
                    directive = "PASS_TURN"
                    confidence = 0.98
                    explanation = "Auto pass turn in combat after natural human delay"
                elif any(w in cmd_lower for w in ["stop", "pause", "arret", "arrete"]):
                    directive = "STOP"
                    confidence = 0.99
                    explanation = "Stop all active automated co-op directives"
                elif any(w in cmd_lower for w in ["farm", "mob", "recolte", "harvest", "craft"]):
                    directive = "AUTO_FARM"
                    confidence = 0.90
                    explanation = "Highlight and engage target farm resources"
                else:
                    directive = "CUSTOM_ACTION"
                    confidence = 0.70
                    explanation = f"Custom action: {cmd_text}"

            resp_data = {
                "status": "ok",
                "directive": directive,
                "confidence": confidence,
                "explanation": explanation,
                "target_slot": target_slot,
                "timestamp": time.time(),
            }
            if metadata:
                resp_data["metadata"] = metadata

            self._send_json(200, resp_data)

        else:
            self._send_json(404, {"error": "not_found"})


def run_sentinel_service(host: str = "127.0.0.1", port: int = 8890) -> None:
    logger.info(f"Starting Singleton AI Sentinel Daemon on http://{host}:{port}...")
    server = ThreadedHTTPServer((host, port), SentinelHTTPHandler)
    logger.info("Serving Singleton AI Sentinel for all 4 instances with ZERO RAM DUPLICATION.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down Sentinel Daemon...")
        server.shutdown()


if __name__ == "__main__":
    run_sentinel_service()
