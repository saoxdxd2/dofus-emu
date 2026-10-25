"""Rank the client's dialog replies against the active French quest context."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(value: Any) -> set[str]:
    if not isinstance(value, str):
        return set()
    plain = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return set(_WORD.findall(plain.casefold()))


def _quest_context(obs: Dict[str, Any]) -> str:
    knowledge = obs.get("knowledge")
    knowledge = knowledge if isinstance(knowledge, dict) else {}
    objective = knowledge.get("objective")
    objective = objective if isinstance(objective, dict) else {}
    parts = [
        knowledge.get("quest_name"), knowledge.get("step_name"),
        knowledge.get("step_description"), objective.get("text"),
    ]
    if not any(parts):
        tracker = obs.get("current_objective")
        tracker = tracker if isinstance(tracker, dict) else {}
        objective_id = tracker.get("objective_id")
        parts.append(tracker.get("text"))
        for entry in obs.get("step_objectives") or []:
            if isinstance(entry, dict) and str(entry.get("id")) == str(objective_id):
                parts.append(entry.get("text"))
    return " ".join(str(part) for part in parts if isinstance(part, str) and part)


def _quest_id(obs: Dict[str, Any], knowledge: Dict[str, Any]) -> Optional[int]:
    tracker = obs.get("current_objective")
    tracker = tracker if isinstance(tracker, dict) else {}
    for raw in (knowledge.get("quest_id"), tracker.get("quest_id")):
        try:
            quest_id = int(raw)
        except (TypeError, ValueError):
            continue
        if quest_id > 0:
            return quest_id
    ready = obs.get("ready_objectives") or []
    if ready and isinstance(ready[0], dict):
        try:
            quest_id = int(ready[0].get("quest_id"))
            return quest_id if quest_id > 0 else None
        except (TypeError, ValueError):
            pass
    return None


def rank_dialog(obs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return a reply choice, wait, or abstention for an open dialog."""
    if not isinstance(obs, dict):
        return None
    dialog = obs.get("dialog")
    if not isinstance(dialog, dict) or not dialog.get("open"):
        return None

    knowledge = obs.get("knowledge")
    knowledge = knowledge if isinstance(knowledge, dict) else {}
    tracker = obs.get("current_objective")
    tracker = tracker if isinstance(tracker, dict) else {}
    decision: Dict[str, Any] = {
        "quest_id": _quest_id(obs, knowledge),
        "objective_id": knowledge.get("objective_id", tracker.get("objective_id")),
        "scores": [],
    }
    replies = dialog.get("replies")
    if (not dialog.get("text_ready") or not dialog.get("ids_ready")
            or not isinstance(replies, list) or not replies):
        return {**decision, "status": "wait", "reason": "dialog text or reply ids are not ready"}

    candidates: List[Dict[str, Any]] = []
    for position, reply in enumerate(replies):
        if not isinstance(reply, dict) or not isinstance(reply.get("text"), str):
            continue
        try:
            index = int(reply.get("index", position))
        except (TypeError, ValueError):
            continue
        candidates.append({"index": index, "text": reply["text"],
                           "reply_id": reply.get("reply_id")})
    if not candidates:
        return {**decision, "status": "abstain", "reason": "no usable reply rows"}
    if len(candidates) == 1:
        return {**decision, "status": "choose", "reason": "only reply", "reply": candidates[0]}

    context = _tokens(_quest_context(obs))
    scored = []
    for reply in candidates:
        tokens = _tokens(reply["text"])
        score = len(tokens & context) / len(tokens) if tokens else 0.0
        scored.append({**reply, "score": score})
    scored.sort(key=lambda reply: -reply["score"])
    decision["scores"] = [{"index": reply["index"], "text": reply["text"],
                           "score": reply["score"]} for reply in scored]
    if scored[0]["score"] == scored[1]["score"]:
        return {**decision, "status": "abstain", "reason": "top replies tie"}
    return {**decision, "status": "choose", "reason": "objective token overlap",
            "reply": scored[0]}
