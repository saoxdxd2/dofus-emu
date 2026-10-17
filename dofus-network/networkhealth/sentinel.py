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

# Ensure networkhealth is in path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "dofus-network"))

from networkhealth.inference import ForecastResult, MultiInstanceNetworkHealth, Observation

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


class SingletonLayaAgent:
    """Manages a single shared instance of the Laya decision model."""

    _instance: SingletonLayaAgent | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self.agent: Any = None
        self._load_attempted = False
        self._init_laya()

    @classmethod
    def get_instance(cls) -> SingletonLayaAgent:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _init_laya(self) -> None:
        if self._load_attempted:
            return
        self._load_attempted = True

        def _loader() -> None:
            try:
                import laya
                logger.info("Background loading shared Singleton Laya Decision Model...")
                agent = laya.load("convaiinnovations/laya", device="cpu", fast=True)
                with self._lock:
                    self.agent = agent
                logger.info("Singleton Laya model ready in RAM.")
            except Exception as ex:
                logger.warning(f"Laya background weight loading deferred or offline: {ex}. Heuristic triage active.")

        threading.Thread(target=_loader, daemon=True, name="LayaBackgroundLoader").start()

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

        # 3. If Laya agent is loaded, perform deep decision inference
        if self.agent is not None:
            try:
                state = {"sender": sender_clean, "message": clean_text}
                questions = {
                    "is_moderator_or_bot_check": {
                        "type": "choice",
                        "instructions": "Is this message a moderator check, bot suspicion, or player inquiry testing responsiveness?",
                        "options": ["yes", "no"],
                    },
                    "urgency": {
                        "type": "choice",
                        "instructions": "How urgent is this message to avoid an AFK or bot flag?",
                        "options": ["critical", "normal", "spam"],
                    },
                }
                pred = self.agent.predict(state, questions)
                is_threat = pred.get("is_moderator_or_bot_check") == "yes" or is_bot_check_keyword
                urgency = pred.get("urgency", "normal").upper()
                return {
                    "is_threat": is_threat,
                    "threat_level": "SUSPECTED_BOT_CHECK" if is_threat else "NORMAL_CHAT",
                    "confidence": 0.92 if is_threat else 0.85,
                    "urgency": urgency if is_threat else "NORMAL",
                    "reason": "Laya Decision Model classification",
                    "suggested_replies": SUGGESTED_REPLIES[:3] if is_threat else [],
                }
            except Exception as e:
                logger.debug(f"Laya inference error: {e}, falling back to heuristics")

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


class SentinelHTTPHandler(BaseHTTPRequestHandler):
    laya = SingletonLayaAgent.get_instance()
    vision = SingletonVisionInspector.get_instance()
    network_engine = MultiInstanceNetworkHealth(CHECKPOINT_PATH) if CHECKPOINT_PATH.exists() else None

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
        if self.path in ("/sentinel/status", "/status", "/health"):
            self._send_json(200, {
                "status": "online",
                "service": "dofus-sentinel-ai",
                "singleton_models": {
                    "trm_moe": self.network_engine is not None,
                    "laya": self.laya.agent is not None,
                    "vision": True,
                },
                "port": 8890,
                "time": time.time(),
            })
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
