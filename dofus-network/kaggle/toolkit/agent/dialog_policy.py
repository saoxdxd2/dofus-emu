"""Rank the client's dialog replies against the active quest context.

Primary signal is quest-objective token overlap (proven). The NPC's own question
is consulted only to break a tie that the objective overlap could not resolve —
see ``rank_dialog``. This is deliberately a *heuristic* for now: the loop has
never completed a talk objective, so there are no (question -> correct reply)
labels to train a ranker on. When such labels exist (criterion 4, "replace
hardcoded where data supports it") this tie-break is what a learned scorer
should subsume, not merely tune.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional

_WORD = re.compile(r"[a-z0-9]+")

# The client marks the replies that advance a quest with its own icon tokens
# (`{icon:iconUpdateQuestMain}`, `{icon:iconNewQuestMain}`, `{icon:iconStartFight}`).
# These are UI semantics, not quest ids: the same markers appear on every quest
# conversation, which is why they can rank a reply without naming a quest.
_QUEST_ICON = re.compile(
    r"\{icon:icon(?:Update|New)QuestMain\}|\{icon:iconStartFight\}", re.I)

# A reply that moves the character between maps, split by direction. Both sets are
# the words the client's own dialogs use in the three shipped languages; they only
# matter when the objective needs a map other than this one (see `_needs_travel`).
_LEAVE_WORDS = {"leave", "leaving", "exit", "out", "outside", "depart", "head",
                "sortir", "partir", "dehors", "salir", "fuera", "irse", "salida"}
_ENTER_WORDS = {"enter", "entering", "inside", "entrer", "entrez",
                "entrar", "entro"}

# Courtesy lines that end a conversation. While an objective is open they are the
# worst choice: they close the only surface that can still offer the next step.
# The live run answered "Wish him good luck." to a quest NPC, closed the dialog and
# spent four wait windows on a map with nothing else to talk to.
_CLOSING = ("good luck", "goodbye", "good bye", "farewell", "see you", "take care",
            "bonne chance", "au revoir", "adieu", "à bientôt", "buena suerte",
            "hasta luego", "nos vemos", "adiós")

# Elemental keywords for multilingual dialogue options.
# Maps each element to its characteristic keywords in French, English, Spanish, German.
_ELEMENT_KEYWORDS: Dict[str, set[str]] = {
    "earth": {
        "terre", "force", "terrestre", "rocher", "pierre",
        "earth", "strength", "stone", "rock",
        "tierra", "fuerza",
        "erde", "starke", "kraft",
    },
    "fire": {
        "feu", "flamme", "flammes", "intelligence", "pyro",
        "fire", "flame", "flames",
        "fuego", "llama", "llamas", "inteligencia",
        "feuer", "flamme", "intelligenz",
    },
    "water": {
        "eau", "chance", "aquatique", "onde", "vague", "fluide",
        "water", "luck", "fluid", "wave",
        "agua", "suerte",
        "wasser", "gluck",
    },
    "air": {
        "air", "agilite", "vent", "souffle", "aerien",
        "agility", "wind", "breath", "aerial",
        "aire", "agilidad", "viento",
        "luft", "wendigkeit", "wind",
    },
}

_STAT_TO_ELEMENT: Dict[str, str] = {
    "strength": "earth",
    "intelligence": "fire",
    "chance": "water",
    "agility": "air",
}

_BREED_DEFAULT_ELEMENT: Dict[str, str] = {
    "iop": "earth",
    "cra": "earth",
    "ecaflip": "earth",
    "sadida": "earth",
    "sram": "earth",
    "feca": "fire",
    "eniripsa": "fire",
    "osamodas": "fire",
    "xelor": "fire",
    "enutrof": "water",
    "sacrier": "earth",
    "pandawa": "earth",
    "rogue": "air",
    "roublard": "air",
    "masqueraider": "earth",
    "zobal": "earth",
    "steamer": "water",
    "fogger": "water",
    "foggernaut": "water",
    "eliotrop": "air",
    "eliotrope": "air",
    "huppermage": "fire",
    "ouginak": "earth",
}


def _reply_element(reply_text: str) -> Optional[str]:
    """Detect if a reply matches an elemental path / build keyword."""
    if not isinstance(reply_text, str):
        return None
    plain = unicodedata.normalize("NFKD", reply_text).encode("ascii", "ignore").decode("ascii").casefold()
    tokens = set(_WORD.findall(plain))
    matched = []
    for elem, keywords in _ELEMENT_KEYWORDS.items():
        if tokens & keywords:
            matched.append(elem)
    if len(matched) == 1:
        return matched[0]
    return None


def _character_element(obs: Dict[str, Any]) -> str:
    """Dynamically determine the character's primary element / build.

    Inspects live stats (strength, intelligence, chance, agility), character element
    annotations, and breed defaults without hardcoding to any single build.
    """
    if not isinstance(obs, dict):
        return "earth"

    char = obs.get("character")
    char = char if isinstance(char, dict) else {}
    for source in (char.get("element"), obs.get("element")):
        if isinstance(source, str) and source.lower() in _ELEMENT_KEYWORDS:
            return source.lower()

    stats_dict = char.get("stats") or obs.get("stats") or {}
    if isinstance(stats_dict, dict):
        stat_scores: Dict[str, float] = {}
        for stat_key, elem in _STAT_TO_ELEMENT.items():
            val = stats_dict.get(stat_key)
            num = 0.0
            if isinstance(val, (int, float)):
                num = float(val)
            elif isinstance(val, dict):
                tot = val.get("total") or val.get("base") or 0
                if isinstance(tot, (int, float)):
                    num = float(tot)
            stat_scores[elem] = max(stat_scores.get(elem, 0.0), num)

        max_stat = max(stat_scores.values()) if stat_scores else 0.0
        if max_stat > 0.0:
            top_elems = [e for e, s in stat_scores.items() if s == max_stat]
            if len(top_elems) == 1:
                return top_elems[0]

    breed_val = char.get("breed")
    if breed_val:
        b_str = unicodedata.normalize("NFKD", str(breed_val)).encode("ascii", "ignore").decode("ascii").casefold().strip()
        for b_name, elem in _BREED_DEFAULT_ELEMENT.items():
            if b_name in b_str:
                return elem

    return "earth" 


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


def _question_tokens(dialog: Dict[str, Any]) -> set:
    """Tokens of the NPC's own prompt. The client field has been named
    ``message``/``text``/``question`` across builds, so read whichever exists."""
    raw = dialog.get("message") or dialog.get("text") or dialog.get("question")
    return _tokens(raw)


def _needs_travel(obs: Dict[str, Any]) -> bool:
    """Whether an open objective names a map other than the one the player is on."""
    try:
        here = int(obs.get("map_id"))
    except (TypeError, ValueError):
        here = None
    entries = [obs.get("current_objective")] + list(obs.get("ready_objectives") or [])
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            need = int(entry.get("map_id"))
        except (TypeError, ValueError):
            need = None
        if need is not None and need > 0 and (here is None or need != here):
            return True
        # A discover-map objective always asks for another map even when the
        # observation does not carry the destination yet.
        if str(entry.get("type")) == "QuestObjectiveDiscoverMap":
            return True
    return False


def _travel_bias(text: str) -> int:
    """+1 for a reply that leaves, -1 for one that goes further inside."""
    words = _tokens(text)
    if words & _LEAVE_WORDS:
        return 1
    if words & _ENTER_WORDS:
        return -1
    return 0


def _is_closing(text: str) -> bool:
    plain = unicodedata.normalize("NFKD", text or "").encode(
        "ascii", "ignore").decode("ascii").casefold()
    return any(phrase in plain for phrase in _CLOSING)


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
    question = _question_tokens(dialog)
    travel = _needs_travel(obs)
    scored = []
    for reply in candidates:
        text = reply["text"]
        plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").casefold()
        tokens = set(_WORD.findall(plain))
        overlap = len(tokens & context) / len(tokens) if tokens else 0.0
        qrel = len(tokens & question) / len(tokens) if tokens else 0.0
        closing = any(phrase in plain for phrase in _CLOSING)
        travel_score = 0
        if travel:
            if tokens & _LEAVE_WORDS:
                travel_score = 1
            elif tokens & _ENTER_WORDS:
                travel_score = -1
        reply_elem = _reply_element(text)
        scored.append({**reply, "score": overlap, "question": qrel,
                       "travel": travel_score,
                       "quest_icon": bool(_QUEST_ICON.search(text)),
                       "closing": closing,
                       "element": reply_elem})
    # Primary: objective overlap. The other signals only reorder inside a tie, so
    # a case the objective already decided is decided exactly as before.
    scored.sort(key=lambda reply: (-reply["score"], -reply["question"]))
    decision["scores"] = [{"index": reply["index"], "text": reply["text"],
                           "score": reply["score"], "question": reply["question"],
                           "travel": reply["travel"], "quest_icon": reply["quest_icon"],
                           "closing": reply["closing"], "element": reply.get("element")}
                          for reply in scored]
    if scored[0]["score"] == scored[1]["score"]:
        tied = [reply for reply in scored if reply["score"] == scored[0]["score"]]
        # Inside a tie, the objective's own needs decide in this order: the reply
        # that actually moves between maps (when the objective is on another map),
        # then the reply the client marks as quest progress, then the reply that
        # keeps the conversation alive (a courtesy line closes the only surface
        # that can still offer the next step), and only then the question text.
        # Live 2026-10-29: the old order answered "Wish him good luck." and closed
        # a quest NPC on a map whose only exit is that NPC's dialog.
        leaving = [reply for reply in tied if reply["travel"] > 0]
        if len(leaving) == 1:
            return {**decision, "status": "choose",
                    "reason": "travel reply", "reply": leaving[0]}
        if leaving:
            tied = leaving
        marked = [reply for reply in tied if reply["quest_icon"]]
        if len(marked) == 1:
            return {**decision, "status": "choose",
                    "reason": "quest-progress reply", "reply": marked[0]}
        if marked:
            tied = marked
        open_ended = [reply for reply in tied if not reply["closing"]]
        if open_ended:
            tied = open_ended

        # Check elemental build choices matching the character's primary build
        elemental_replies = [reply for reply in tied if reply.get("element")]
        if elemental_replies:
            target_element = _character_element(obs)
            matching = [r for r in elemental_replies if r.get("element") == target_element]
            if len(matching) == 1:
                return {**decision, "status": "choose",
                        "reason": f"character build element ({target_element})",
                        "reply": matching[0]}
            if matching:
                tied = matching

        best_q = max(reply["question"] for reply in tied)
        winners = [reply for reply in tied if reply["question"] == best_q]
        if len(winners) == 1 and best_q > 0.0:
            return {**decision, "status": "choose", "reason": "question tie-break",
                    "reply": winners[0]}
        if len(tied) == 1:
            reason = ("travel reply" if tied[0]["travel"] > 0
                      else ("quest-progress reply" if tied[0]["quest_icon"]
                            else (f"character build element ({tied[0].get('element')})" if tied[0].get("element")
                                  else "keeps the dialog open")))
            return {**decision, "status": "choose", "reason": reason,
                    "reply": tied[0]}
        return {**decision, "status": "choose",
                "reason": "first open-ended reply in tie",
                "reply": tied[0]}
    return {**decision, "status": "choose", "reason": "objective token overlap",
            "reply": scored[0]}
