"""THX-01 policy: non-autoregressive text -> tool call.

Tool selection is one 16-way ``choice`` question. Arguments are typed
questions: ``excerpt`` for names copied verbatim from the goal, ``number`` for
ids/coordinates/levels/prices, ``choice`` for enums, ``noul`` for booleans.

THX-01 is a 322M encoder-only decision model (mmBERT + decision head). It does
not generate JSON; malformed calls and invented tool names are impossible by
construction. A few deterministic extractors cover patterns the excerpt head
handles poorly: coordinate pairs, chat text, and language-mixed item names.

Run from ``dofus-network``::

    python ai/thx_policy.py decide "sell the Bouftou Wool"
    python ai/thx_policy.py bench --show-failures
    python ai/thx_policy.py bench --out predictions.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.runtime import RulePolicy, ToolCall
from agent.schema import TOOLS_BY_NAME, tool_names, validate_call

logger = __import__("logging").getLogger("dofus.ai.thx_policy")

CACHE_DIR = REPO_ROOT / "data" / "cache"

TOOL_CRITERIA: Dict[str, str] = {
    "knowledge.search": (
        "look up facts about a monster, spell, quest, item, NPC, achievement or "
        "dungeon; answer a 'what is X' question; chercher, busca, suche"
    ),
    "nav.goto": (
        "travel or move to a place: coordinates like [x, y], a zaap, an NPC, or a "
        "map id; go to, va en, ve a, geh zu"
    ),
    "nav.follow": (
        "follow another character or the player who gave the order; follow me, "
        "suis-moi, sígueme, folge mir"
    ),
    "quest.list": (
        "list, find or show quests for a level, an area or dungeons; what quests "
        "can I do; quêtes, misiones, Quests"
    ),
    "quest.advance": (
        "perform the next step of a specific quest, identified by a quest id; "
        "avance la quête, avanza la misión, avanciere Quest"
    ),
    "farm.start": (
        "start a farming loop to kill monsters or harvest resources; farm, farme, "
        "farmea, farmear"
    ),
    "farm.stop": "stop the active farming loop; arrête la boucle de farm, detén el farmeo",
    "fight.join": "join a combat or fight; rejoins le combat, únete al combate, Kampf beitreten",
    "fight.pass": "pass the current combat turn; passe le tour, pasa el turno, passe den Zug",
    "inv.list": (
        "list or show the items currently in the player's inventory; what am I "
        "carrying, montre mon inventaire, muestra mi inventario, zeig mein Inventar"
    ),
    "inv.sell": "sell items from the inventory on the market; vends, vende, verkaufe",
    "hdv.scan": (
        "scan or check the market price of an item; scan le prix, escanea el "
        "precio, scanne den Preis"
    ),
    "hdv.list": (
        "put an item up for sale on the market at a price; list on the market, "
        "mettre en vente, poner en venta"
    ),
    "chat.say": "send a chat message; dis, di, sag",
    "state.snapshot": (
        "report the player's current position, map, HP or active fight; use for "
        "questions like 'where am I', 'où suis-je', 'dónde estoy', 'wo bin ich'; "
        "not the inventory list"
    ),
    "safe.stop": (
        "emergency stop all actions and loops immediately; stop, halt, arrête "
        "tout, para todo, halt alles"
    ),
}

TOOL_QUESTION = {
    "tool": {
        "type": "choice",
        "instructions": "Which tool should be called to carry out the goal in `state`?",
        "criteria": TOOL_CRITERIA,
    },
}

_ENTITY_FILES = {
    "item": ("items_full.json",),
    "monster": ("monsters_full.json",),
    "area": ("areas_full.json", "subareas_full.json"),
    "npc": ("npcs_full.json",),
    "quest": ("quests_full.json",),
    "spell": ("spells_full.json",),
    "dungeon": ("dungeons_full.json",),
    "achievement": ("achievements_full.json",),
}

_ENTITY_INDEX: Dict[str, Dict[str, str]] = {}

_ARTICLES = (
    "the", "my", "mon", "ma", "mes", "le", "la", "les", "l'", "un", "une", "des",
    "el", "los", "las", "mi", "mis", "mein", "meine", "der", "die", "das", "dem",
    "den", "du", "de la", "de l'", "des",
)

_MARKET_TAILS = (
    "on the market", "at the market", "in my inventory", "from my inventory",
    "for loot", "for kamas", "at market price", "market price", "for sale",
    "au marché", "sur le marché", "dans mon inventaire", "pour du butin",
    "en vente", "à la vente", "en el mercado", "en mi inventario", "por botín",
    "a la venta", "im markt", "in meinem inventar", "für beute", "zum verkauf",
)

_RE_COORDS = re.compile(r"\[\s*(-?\d+)\s*,\s*(-?\d+)\s*\]")
_RE_COORDS_NATURAL = re.compile(
    r"\b(?:go\s+to|va\s+en|ve\s+a|geh\s+zu|coords?|pos(?:ition)?)\s+"
    r"(-?\d+)\s*[, ]\s*(-?\d+)\b",
    re.IGNORECASE,
)
_RE_ZAAP = re.compile(r"\bzaap\s+([A-Za-zÀ-ÿ' -]{2,40})", re.IGNORECASE)
_RE_QUEST_ID = re.compile(r"\b(?:quest|qu[eê]te|misi[oó]n|quests?)\D{0,12}(\d{1,9})\b",
                          re.IGNORECASE)
_RE_PRICE = re.compile(
    r"\b(?:for|at|price|prix|precio|f�r|zu|�|a|por|para|per|pour)\s+"
    r"(\d{1,12})(?:\s*(?:kamas?|k|gold|or|oro|goldbarren))?\b",
    re.IGNORECASE,
)
_RE_PRICE_CURRENCY = re.compile(
    r"\b(\d{1,12})\s*(?:kamas?|k|gold|or|oro|goldbarren)\b",
    re.IGNORECASE,
)
_RE_LEVEL = re.compile(
    r"\b(?:level|niveau|nivel|level|stufe)\s+(\d{1,3})\b", re.IGNORECASE)
_RE_DUNGEON = re.compile(r"\b(dungeon|donjon|mazmorra|donjons|dungeons)\b", re.IGNORECASE)
_RE_ALL = re.compile(r"\b(all|tout|tous|toute|everything|todo|toda|ganze|ganzen)\b",
                     re.IGNORECASE)
_RE_CHAT_PREFIX = re.compile(
    r"^\s*(?:say|dis|dites|di|diga|sag|sagt|tell|inform(?:a|e|iere)?)\b[\s:,-]*",
    re.IGNORECASE)
_RE_CHAT_TO = re.compile(
    r"^\s*(?:tell|say\s+to|send\s+(?:a\s+)?message\s+to|message|"
    r"envoie\s+(?:un\s+)?message\s+(?:à|a)|"
    r"env[ií]a\s+(?:un\s+)?mensaje\s+(?:a|al)|"
    r"schicke\s+(?:eine\s+)?nachricht\s+(?:an|an\s+das)|"
    r"pr[ée]viens|avisa|inform(?:a|e|iere)?|dile\s+al|"
    r"dis\s+à|dis\s+a|di\s+a|di\s+al|sag\s+dem|sag\s+der|sag\s+zu)\s+"
    r"(?:the\s+|le\s+|la\s+|les\s+|l'|el\s+|al\s+|los\s+|las\s+|"
    r"dem\s+|der\s+|das\s+|an\s+das\s+|à\s+l'|a\s+la\s+|au\s+)?"
    r"(?:team|guild|guilde|gremio|group|gruppe|équipe|equipe|equipo|grupo|gilde)"
    r"\s*[:,;-]\s*(.+)$",
    re.IGNORECASE,
)
_RE_CHAT_CHANNEL = re.compile(
    r"\s+(?:in|on|dans|en|im|au|à|a|al|zum|zur)\s+"
    r"(?:the\s+|le\s+|la\s+|les\s+|l'|el\s+|los\s+|las\s+|dem\s+|der\s+|die\s+|das\s+)?"
    r"[^,.;!?]*\b(?:chat|canal|channel|team|guild|équipe|equipe|equipo|gremio|grupo|gilde)\b.*$",
    re.IGNORECASE,
)
_RE_ARTICLE = re.compile(
    r"^\s*(?:" + "|".join(re.escape(a) for a in _ARTICLES) + r")\s+", re.IGNORECASE)
_RE_FARM_VERB = re.compile(
    r"^\s*(?:farm(?:e|er|ing)?|farmea|farmear|r[eé]colte[rz]?|gather|harvest|minar|"
    r"collecte[rz]?|farme|kill|hunt|chasse[rz]?|caza(?:r)?|tue|tuer|matar|t[oö]te|jage|"
    r"va\s+farmer|ve\s+a\s+farmear|geh\s+\S+\s+farmen)\b[\s:,-]*",
    re.IGNORECASE,
)
_RE_SAFE_STOP_GUARD = re.compile(
    r"\b(?:stop|halt)\s+(?:everything|all|the\s+bot|immediately|now)\b|"
    r"^\s*halt\b|"
    r"\bemergency\s+stop\b|"
    r"\barr[eê]t\s+d'urgence\b|"
    r"\b(?:notstopp|notfall)\b|"
    r"\b(?:coupe|arrête|arrete)\s+tout\b|"
    r"\b(?:detén|deten|para)\s+todo\b|"
    r"\b(?:stoppe|halt)\s+alles\b",
    re.IGNORECASE,
)
_RE_KNOWLEDGE_VERB_GUARD = re.compile(
    r"^\s*(?:look\s+up|search\s+for|busca(?:r)?|suche(?:n)?|trouve[rz]?|"
    r"encuentra|encontrar|parle[- ]moi\s+de|h[áa]blame\s+de|erz[äa]hl\s+mir\s+von)\b",
    re.IGNORECASE,
)
_RE_MARKET_SCAN_GUARD = re.compile(
    r"\b(?:pr[üu]fe|v[ée]rifie|consulta|check)\s+"
    r"(?:den|le|el|the|au|im)?\s*(?:markt|march[ée]|mercado|market)\b|"
    r"^\s*(?:scan|scanne|escanea)\b",
    re.IGNORECASE,
)
_RE_MARKET_LIST_GUARD = re.compile(
    r"^\s*(?:sell|vends|vendre|vend|vender|vende|verkaufe|mets|mettre|put|"
    r"pon|poner|list|auction)\b.*"
    r"\b(?:on\s+the\s+market|auf\s+dem\s+markt|en\s+el\s+mercado|"
    r"sur\s+le\s+march[ée]|au\s+march[ée]|en\s+vente|a\s+la\s+venta|"
    r"for\s+sale|zum\s+verkauf)\b",
    re.IGNORECASE,
)
_RE_BID_LIST_GUARD = re.compile(r"^\s*biete\b.*\ban\b", re.IGNORECASE)
_RE_DUNGEON_QUEST_GUARD = re.compile(
    r"\b(?:quests?|quêtes?|quete|misiones?|misión)\b.*"
    r"\b(?:dungeons?|donjons?|mazmorras?)\b|"
    r"\b(?:dungeons?|donjons?|mazmorras?)\b.*"
    r"\b(?:quests?|quêtes?|quete|misiones?|misión)\b",
    re.IGNORECASE,
)
_RE_LIST_QUESTS_GUARD = re.compile(
    r"^\s*(?:list|lista|liste|zeige|show|find|trouve|busca)\b.*"
    r"\b(?:quests?|quêtes?|quete|misiones?|misión)\b",
    re.IGNORECASE,
)
_RE_STOP_WORD = re.compile(
    r"\b(?:stop|arrête|arrete|detén|deten|stoppe|halt)\b", re.IGNORECASE)
_RE_CHAT_GUARD = re.compile(
    r"^\s*(?:tell|dis|dites|dile|di|diga|sag|sagt|message|envoie|env[ií]a|"
    r"send\s+(?:a\s+)?message\s+to|pr[ée]viens|avisa|inform(?:a|e|iere)?|schicke)\b.*\b"
    r"(?:team|guild|guilde|gremio|group|gruppe|[eé]quipe|equipo|grupo|gilde)\b",
    re.IGNORECASE,
)
_RE_CHAT_SAY_GUARD = re.compile(
    r"^\s*(?:say|di|dile)\b|"
    r"^\s*sag\s+(?!mir\b)|"
    r"^\s*dis\s+(?!moi\b|où\b|ou\b)",
    re.IGNORECASE,
)
_RE_FIGHT_JOIN_GUARD = re.compile(
    r"\b(?:join|enter|help\s+in)\b.*\b(?:fight|combat)\b|"
    r"\b(?:rejoins|entre|aide)\b.*\bcombat\b|"
    r"\b(?:únete|entra|participa|ayuda)\b.*\b(?:combate|pelea)\b|"
    r"\b(?:tritt|geh|nimm|hilf)\b.*\bkampf\b",
    re.IGNORECASE,
)
_RE_FIGHT_PASS_GUARD = re.compile(
    r"\b(?:pass|skip|wait\s+for)\b.*\b(?:turn|round)\b|"
    r"\b(?:passe|saute|attends)\b.*\b(?:tour)\b|"
    r"\b(?:pasa|salta|espera)\b.*\b(?:turno)\b|"
    r"\b(?:warte|überspringe|lass)\b.*\b(?:zug)\b",
    re.IGNORECASE,
)
_RE_FOLLOW_GUARD = re.compile(
    r"^\s*(?:follow|stay\s+with|keep\s+following|suis|reste\s+avec|"
    r"continue\s+de\s+suivre|sigue\s+a|qu[eé]date\s+con|contin[úu]a\s+siguiendo|"
    r"folge|bleib\s+bei|folge\s+weiter)\b",
    re.IGNORECASE,
)
_RE_FARM_PHRASE_GUARD = re.compile(
    r"\b(?:fang\s+an|beginne|starte)\b.*\b(?:farmen|farm)\b|"
    r"\bcommence[rz]?\s+[àa]\s+farmer\b|"
    r"\b(?:empieza|comienza)\s+a\s+farmear\b|"
    r"^\s*start\s+farming\b",
    re.IGNORECASE,
)
_RE_PROPOSE_GUARD = re.compile(
    r"^\s*(?:propose|propose\s+de|offre|ofrece|biete)\b.*"
    r"\b(?:pour|por|f[üu]r|for)\s+\d{1,7}\s*(?:kamas?|k)\b",
    re.IGNORECASE,
)
_RE_ABORT_GUARD = re.compile(
    r"^\s*(?:abort|cancel|annule[rz]?|cancela[rz]?|beende|brich|abandonne[rz]?|abandona[rz]?)\b.*\b"
    r"(?:all|everything|plan|bot|tout|todo|alles|el\s+bot|le\s+bot|den\s+bot)\b",
    re.IGNORECASE,
)
_RE_LIST_ITEM_GUARD = re.compile(
    r"^\s*list\s+(?:\d{1,4}\s+)?(.+?)\s+(?:at|for|à|a|por|für)\s+\d",
    re.IGNORECASE,
)
_RE_SELL_IMPERATIVE = re.compile(
    r"^\s*(?:sell|vends|vendre|vend|vender|vende|verkaufe)\b", re.IGNORECASE)
_RE_PRICE_EXCLUDE = re.compile(
    r"\b(?:quest|quête|quete|misi[oó]n|level|niveau|nivel|stufe|xp)\b",
    re.IGNORECASE,
)
_RE_INV_LIST_GUARD = re.compile(
    r"\b(?:what\s+am\s+i\s+carrying|what\s+do\s+i\s+have|what'?s\s+in\s+my\s+inventory|"
    r"show\s+my\s+(?:inventory|items)|list\s+my\s+inventory|"
    r"qu'ai[- ]?je|que\s+dois[- ]?je|mon\s+inventaire|mes\s+objets|"
    r"[¿]?qu[eé]\s+llevo|[¿]?qu[eé]\s+tengo|mi\s+inventario|mis\s+objetos|"
    r"was\s+trage\s+ich|was\s+habe\s+ich|mein\s+inventar|meine\s+gegenst[aä]nde)\b",
    re.IGNORECASE,
)
_RE_SELL_VERB = re.compile(
    r"\b(?:sell|vends|vendre|vend|vender|vende|verkaufe)\b", re.IGNORECASE)
_RE_ITEM_SELL = re.compile(
    r"^\s*(?:sell|vends|vendre|vend|vender|vende|verkaufe)\s+"
    r"(?:\d{1,4}\s+)?(.+)$",
    re.IGNORECASE,
)
_RE_ITEM_LIST = re.compile(
    r"^\s*(?:put|mets|mettre|pon|poner|auction|list|propose|offre|ofrece|biete)\s+"
    r"(?:(?:up|en\s+vente|à\s+la\s+vente|a\s+la\s+venta)\s+)?"
    r"(?:\d{1,4}\s+)?(.+?)\s+(?:(?:up)\s+)?"
    r"(?:for|à|a|por|für|at|on|pour)\s+",
    re.IGNORECASE,
)
_CURRENCY_ITEM_WORDS = {"kama", "kamas", "k"}
_VERB_FORMS = {
    "mets", "vends", "vend", "vendre", "sell", "vende", "vender",
    "propose", "proposes", "offre", "offres", "ofrece", "ofreces", "biete", "bietest",
    "list", "liste", "listes", "pon", "pone", "poner", "scan", "scanne", "escanea",
}
_RE_STATE_GUARD = re.compile(
    r"\b(?:where\s+am\s+i|où\s+suis-je|dónde\s+estoy|wo\s+bin\s+ich|"
    r"my\s+position|ma\s+position|mi\s+posici[oó]n|meine\s+position|"
    r"my\s+current\s+map|ma\s+carte\s+actuelle|mi\s+mapa\s+actual|"
    r"meine\s+aktuelle\s+karte|where\s+is\s+my\s+character|"
    r"où\s+est\s+mon\s+personnage|dónde\s+est[aá]\s+mi\s+personaje|"
    r"wo\s+ist\s+mein\s+charakter)\b",
    re.IGNORECASE,
)
_RE_KNOWLEDGE_GUARD = re.compile(
    r"^\s*(?:(?:what|who)\s+is|tell\s+me\s+about|qu'est[- ]?ce\s+que|qui\s+est|"
    r"qu[eé]\s+es|was\s+ist|wer\s+ist|parle[- ]moi\s+de|h[áa]blame\s+de|"
    r"erz[äa]hl\s+mir\s+von)\b",
    re.IGNORECASE,
)
_RE_PRICE_QUESTION = re.compile(
    r"\b(?:price|prix|precio|preis|cost|coûte|coute|cuesta|kostet)\b", re.IGNORECASE)
_RE_QUANTITY = re.compile(
    r"(?:\b(\d{1,4})\s*(?:x|×)\b|\b(?:x|×)\s*(\d{1,4})\b|"
    r"\b(\d{1,4})\s+(?=[A-Za-zÀ-ÿ]))",
    re.IGNORECASE,
)
_PRICE_WORDS = {
    "kamas", "kama", "k", "euro", "euros", "level", "niveau", "nivel", "stufe",
    "quest", "quete", "quête", "mision", "misión",
}


def _normalize(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    folded = unicodedata.normalize("NFKD", text.casefold())
    stripped = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return " ".join(stripped.split())


def _record_names(record: Dict[str, Any]) -> List[str]:
    name = record.get("name")
    if isinstance(name, dict):
        return [str(v) for v in name.values() if isinstance(v, str) and v.strip()]
    if isinstance(name, str) and name.strip():
        return [name]
    return [str(record[key]) for key in ("name_fr", "name_en", "name_es", "name_de")
            if isinstance(record.get(key), str) and record[key].strip()]


def _entity_index(kind: str) -> Dict[str, str]:
    cached = _ENTITY_INDEX.get(kind)
    if cached is not None:
        return cached
    index: Dict[str, str] = {}
    for filename in _ENTITY_FILES.get(kind, ()):
        path = CACHE_DIR / filename
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, list):
            continue
        for record in payload:
            if not isinstance(record, dict):
                continue
            for name in _record_names(record):
                norm = _normalize(name)
                if len(norm) >= 3 and norm not in index:
                    index[norm] = name
    _ENTITY_INDEX[kind] = index
    return index


def _find_entity(goal: str, kind: str) -> Optional[str]:
    index = _entity_index(kind)
    if not index:
        return None
    tokens = [token.strip("?!.,;:\"'()[]«»“”") for token in _normalize(goal).split()]
    tokens = [token for token in tokens if token]
    if not tokens:
        return None
    for size in range(min(8, len(tokens)), 0, -1):
        for start in range(len(tokens) - size + 1):
            phrase = " ".join(tokens[start:start + size])
            if kind == "item" and phrase in _CURRENCY_ITEM_WORDS:
                continue
            if phrase in index:
                return index[phrase]
            for variant in _singular_variants(phrase):
                if kind == "item" and variant in _CURRENCY_ITEM_WORDS:
                    continue
                if kind == "item" and phrase in _VERB_FORMS and variant != phrase:
                    continue
                if variant in index:
                    return index[variant]
    return None


def _find_any_entity(goal: str) -> Optional[str]:
    for kind in ("monster", "item", "npc", "quest", "spell", "dungeon", "achievement"):
        found = _find_entity(goal, kind)
        if found:
            return found
    return None


def _guard_tool(goal: str) -> Optional[str]:
    if _RE_CHAT_GUARD.search(goal) or _RE_CHAT_SAY_GUARD.search(goal):
        return "chat.say"
    if _RE_SAFE_STOP_GUARD.search(goal):
        return "safe.stop"
    if _RE_ABORT_GUARD.search(goal):
        return "safe.stop"
    if _RE_FIGHT_JOIN_GUARD.search(goal):
        return "fight.join"
    if _RE_FIGHT_PASS_GUARD.search(goal):
        return "fight.pass"
    if _extract_coords(goal):
        return "nav.goto"
    if _RE_FOLLOW_GUARD.search(goal):
        return "nav.follow"
    if _RE_FARM_PHRASE_GUARD.search(goal) or (
            _RE_FARM_VERB.search(goal) and not _RE_STOP_WORD.search(goal)):
        return "farm.start"
    if _RE_INV_LIST_GUARD.search(goal) and not _RE_SELL_VERB.search(goal):
        return "inv.list"
    if _RE_STATE_GUARD.search(goal):
        return "state.snapshot"
    if (_RE_MARKET_LIST_GUARD.search(goal) or _RE_BID_LIST_GUARD.search(goal)
            or _RE_PROPOSE_GUARD.search(goal)):
        return "hdv.list"
    if _RE_MARKET_SCAN_GUARD.search(goal):
        return "hdv.scan"
    if _RE_LIST_ITEM_GUARD.search(goal):
        return "hdv.list"
    if (_RE_SELL_IMPERATIVE.search(goal)
            and not _RE_MARKET_LIST_GUARD.search(goal)
            and not _RE_BID_LIST_GUARD.search(goal)):
        return "inv.sell"
    if (_RE_DUNGEON_QUEST_GUARD.search(goal)
            and not re.search(r"\b(?:advance|avance|avanza|avanciere|progress)\b",
                              goal, re.IGNORECASE)):
        return "quest.list"
    if _RE_LIST_QUESTS_GUARD.search(goal):
        return "quest.list"
    if (_RE_PRICE_QUESTION.search(goal)
            and not _RE_SELL_IMPERATIVE.search(goal)
            and not _RE_PRICE_EXCLUDE.search(goal)):
        return "hdv.scan"
    if _RE_KNOWLEDGE_VERB_GUARD.search(goal) and _find_any_entity(goal):
        return "knowledge.search"
    if (_RE_KNOWLEDGE_GUARD.search(goal)
            and not _RE_PRICE_QUESTION.search(goal)
            and _find_any_entity(goal)):
        return "knowledge.search"
    return None


def _clean_phrase(text: Any) -> Optional[str]:
    if not isinstance(text, str):
        return None
    value = text.strip(" .,!?:;\"'«»“”()[]")
    for _ in range(3):
        stripped = _RE_ARTICLE.sub("", value).strip()
        if stripped == value:
            break
        value = stripped
    lowered = value.casefold()
    for tail in _MARKET_TAILS:
        if lowered.endswith(tail):
            value = value[: len(value) - len(tail)].strip(" .,!?:;\"'")
            lowered = value.casefold()
            break
    return value or None


def _singular_variants(text: str) -> List[str]:
    tokens = text.split()
    candidates = [tokens]
    for index, token in enumerate(tokens):
        if len(token) > 3 and token.endswith("s"):
            variant = list(tokens)
            variant[index] = token[:-1]
            candidates.append(variant)
    for length in (2, 3):
        if len(tokens) <= length:
            continue
        for start in range(len(tokens) - length + 1):
            chunk = tokens[start:start + length]
            if all(len(t) > 3 and t.endswith("s") for t in chunk):
                variant = list(tokens)
                variant[start:start + length] = [t[:-1] for t in chunk]
                candidates.append(variant)
    return [" ".join(v) for v in candidates]


def _singularize_item(text: str) -> str:
    tokens = text.split()
    for index in (0, len(tokens) - 1):
        if 0 <= index < len(tokens):
            token = tokens[index]
            if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
                tokens[index] = token[:-1]
    return " ".join(tokens)


def _canonical_item(candidate: Optional[str]) -> Optional[str]:
    if not candidate:
        return None
    index = _entity_index("item")
    norm = _normalize(candidate)
    if norm in index:
        return index[norm]
    for variant in _singular_variants(norm):
        if variant in index:
            return index[variant]
    return _singularize_item(candidate)


def _clean_item(text: Any) -> Optional[str]:
    value = _clean_phrase(text)
    if not value:
        return None
    value = re.sub(r"^\s*(?:\d{1,4}\s*(?:x|×)?\s+)", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+(?:à|a|por|for|für|zu|at)\s+\d+.*$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+(?:a la venta|en vente|à la vente|for sale|zum verkauf)\b.*$",
                   "", value, flags=re.IGNORECASE)
    value = re.sub(
        r"\s+(?:sur le marché|sur le marche|on the market|en el mercado|"
        r"auf dem markt|de mon inventaire|de mi inventario|"
        r"aus meinem inventar|from my inventory)\b.*$",
        "", value, flags=re.IGNORECASE)
    return _canonical_item(_clean_phrase(value))


def _extract_quantity(goal: str) -> Optional[int]:
    for match in _RE_QUANTITY.finditer(goal):
        value = match.group(1) or match.group(2) or match.group(3)
        if value is None:
            continue
        before = goal[max(0, match.start() - 24):match.start()]
        following = re.match(r"\s*([A-Za-zÀ-ÿ]+)", goal[match.end():])
        word = following.group(1).casefold() if following else ""
        if word in _PRICE_WORDS:
            continue
        if re.search(r"\b(?:level|niveau|nivel|stufe|quest|qu[eê]te|misi[oó]n)\s*$",
                     before, re.IGNORECASE):
            continue
        return int(value)
    return None


def _after_prefix(goal: str, prefixes: Sequence[str]) -> Optional[str]:
    pattern = re.compile(
        r"(?:\b(?:" + "|".join(re.escape(p) for p in prefixes) + r")\b)[\s:,-]*(.+)$",
        re.IGNORECASE,
    )
    match = pattern.search(goal)
    if not match:
        return None
    return _clean_phrase(match.group(1))


class ThxPolicy:
    """THX-01 backed policy implementing the runtime ``Policy`` protocol."""

    def __init__(
        self,
        model: Optional[str] = None,
        device: str = "cpu",
        fallback: Optional[Any] = None,
        min_tool_confidence: float = 0.0,
        use_guards: bool = True,
    ) -> None:
        self.model = model or _default_model_path()
        self.device = device
        self.fallback = fallback
        self.min_tool_confidence = min_tool_confidence
        self.use_guards = use_guards
        self._agent: Any = None
        self.last: Dict[str, Any] = {}

    def warmup(self) -> None:
        self._load()

    def _load(self) -> Any:
        if self._agent is not None:
            return self._agent
        try:
            import thx01
        except ImportError as exc:
            raise RuntimeError("thx01 is not installed; pip install thx01") from exc
        started = time.perf_counter()
        self._agent = thx01.load(self.model, device=self.device)
        logger.info(
            "THX-01 loaded from %s on %s in %.1fs",
            self.model, getattr(self._agent, "device", "?"),
            time.perf_counter() - started,
        )
        return self._agent

    def _decide(self, goal: str, questions: Dict[str, Any]) -> Dict[str, Any]:
        agent = self._load()
        result = agent.decide(goal, questions)
        return result.get("answers", {})

    def choose(
        self,
        goal: str,
        observation: Optional[Dict[str, Any]] = None,
        tools: Optional[List[str]] = None,
    ) -> Optional[ToolCall]:
        goal = (goal or "").strip()
        if not goal:
            return None
        started = time.perf_counter()
        guarded = _guard_tool(goal) if self.use_guards else None
        if guarded and (not tools or guarded in tools):
            name = guarded
            confidence = 1.0
            probabilities = {guarded: 1.0}
            source = "guard"
        else:
            answers = self._decide(goal, TOOL_QUESTION)
            tool_answer = answers.get("tool") or {}
            name = tool_answer.get("choice")
            confidence = float(tool_answer.get("confidence") or 0.0)
            probabilities = tool_answer.get("probabilities") or {}
            source = "thx01"
        if tools and name not in tools:
            name = None
        if name is None:
            return self._fallback(goal, observation, tools, started)

        args, arg_meta = self._arguments(name, goal)
        ok, coerced = validate_call(name, args)
        if not ok:
            logger.info("THX-01 produced invalid args for %s: %s", name, coerced)
            if self.fallback is not None:
                return self._fallback(goal, observation, tools, started)
            return None

        elapsed_ms = (time.perf_counter() - started) * 1000
        self.last = {
            "tool": name,
            "tool_source": source,
            "tool_confidence": confidence,
            "tool_probabilities": probabilities,
            "args": coerced,
            "args_confidence": arg_meta,
            "latency_ms": round(elapsed_ms, 1),
            "source": "thx01",
        }
        if source == "thx01" and confidence < self.min_tool_confidence and self.fallback is not None:
            return self._fallback(goal, observation, tools, started)
        return ToolCall(name, coerced, goal)

    def _fallback(
        self,
        goal: str,
        observation: Optional[Dict[str, Any]],
        tools: Optional[List[str]],
        started: float,
    ) -> Optional[ToolCall]:
        if self.fallback is None:
            self.last = {
                "tool": None,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "source": "thx01",
            }
            return None
        call = self.fallback.choose(goal, observation or {}, tools)
        self.last = {
            "tool": getattr(call, "tool", None),
            "args": getattr(call, "args", None),
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "source": "fallback",
        }
        return call

    def _arguments(self, tool: str, goal: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if tool == "knowledge.search":
            return self._knowledge_args(goal)
        if tool == "nav.goto":
            return self._nav_args(goal)
        if tool == "nav.follow":
            return {}, {}
        if tool == "quest.list":
            return self._quest_list_args(goal)
        if tool == "quest.advance":
            return self._quest_advance_args(goal)
        if tool == "farm.start":
            return self._farm_args(goal)
        if tool in ("farm.stop", "fight.pass", "state.snapshot", "safe.stop", "fight.join"):
            return {}, {}
        if tool == "inv.list":
            return {}, {}
        if tool == "inv.sell":
            return self._sell_args(goal)
        if tool == "hdv.scan":
            return self._scan_args(goal)
        if tool == "hdv.list":
            return self._list_args(goal)
        if tool == "chat.say":
            return self._chat_args(goal)
        return {}, {}

    def _knowledge_args(self, goal: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        query = self._query(goal)
        args: Dict[str, Any] = {"query": query} if query else {}
        return args, {"query": "entity/excerpt"}

    def _nav_args(self, goal: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        coords = _extract_coords(goal)
        if coords:
            return {"x": coords[0], "y": coords[1]}, {"coords": "regex"}
        zaap = _extract_zaap(goal)
        if zaap:
            return {"zaap": zaap}, {"zaap": "excerpt/regex"}
        npc = _find_entity(goal, "npc") or _after_prefix(goal, ("npc", "pnj", "pnj" ,"personnage"))
        if npc and "npc" in _normalize(goal):
            return {"npc": npc}, {"npc": "entity"}
        if re.search(r"\b(map|map_id|carte)\b", goal, re.IGNORECASE):
            number = self._number(goal, "What map id is mentioned in the goal?",
                                  minimum=1, maximum=2**31)
            if number is not None:
                return {"map_id": int(number)}, {"map_id": "number"}
        return {}, {}

    def _quest_list_args(self, goal: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        args: Dict[str, Any] = {}
        meta: Dict[str, Any] = {}
        area = _find_entity(goal, "area")
        if area is None:
            area = _excerpt(
                self._decide(goal, {"area": {
                    "type": "excerpt",
                    "instructions": "What is the exact area name in the goal? Return only the place name.",
                }}), "area")
            area = _clean_phrase(area)
        if area:
            args["area"] = area
            meta["area"] = "entity/excerpt"
        level = None
        match = _RE_LEVEL.search(goal)
        if match:
            level = int(match.group(1))
        elif re.search(r"\d", goal):
            level = self._number(goal, "What player level is required by the goal?",
                                 minimum=1, maximum=200)
        if level is not None:
            args["level_min"] = int(level)
            meta["level_min"] = "number/regex"
        if _RE_DUNGEON.search(goal):
            args["dungeon_only"] = True
            meta["dungeon_only"] = "regex"
        return args, meta

    def _quest_advance_args(self, goal: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        quest_id = None
        match = _RE_QUEST_ID.search(goal)
        if match:
            quest_id = int(match.group(1))
        elif re.search(r"\d", goal):
            quest_id = self._number(goal, "What quest id is mentioned in the goal?",
                                    minimum=1, maximum=10**9)
        if quest_id is None:
            match = re.search(r"\b(\d{1,9})\b", goal)
            if match:
                quest_id = int(match.group(1))
        return ({"quest_id": int(quest_id)} if quest_id is not None else {}), {"quest_id": "number/regex"}

    def _farm_args(self, goal: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        monster = _find_entity(goal, "monster")
        if monster is None:
            monster = _excerpt(
                self._decide(goal, {"monster": {
                    "type": "excerpt",
                    "instructions": "Which exact monster or monster family name is mentioned in the goal? Return only the name.",
                }}), "monster")
            monster = _clean_phrase(monster)
        if monster is None:
            monster = _after_farm_verb(goal)
        args: Dict[str, Any] = {}
        meta: Dict[str, Any] = {}
        if monster:
            args["monster"] = monster
            meta["monster"] = "entity/excerpt/regex"
        return args, meta

    def _sell_args(self, goal: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        args: Dict[str, Any] = {}
        meta: Dict[str, Any] = {}
        explicit_all = bool(_RE_ALL.search(goal))
        item = None if explicit_all else self._item(goal)
        if item:
            args["item"] = item
            meta["item"] = "entity/excerpt/regex"
            quantity = _extract_quantity(goal)
            if quantity is not None:
                args["quantity"] = quantity
                meta["quantity"] = "regex"
            else:
                args["all"] = True
        else:
            sell_all = explicit_all
            if not sell_all:
                sell_all = bool(self._bool(
                    goal, "Does the goal ask to sell everything or the whole inventory?"))
            if sell_all:
                args["all"] = True
                meta["all"] = "regex/noul"
        price = self._price(goal)
        if price is not None:
            args["price_rule"] = "fixed"
            args["price"] = int(price)
            meta["price"] = "number/regex"
        return args, meta

    def _scan_args(self, goal: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        item = self._item(goal)
        return ({"item": item} if item else {}), {"item": "entity/excerpt/regex"}

    def _list_args(self, goal: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        args: Dict[str, Any] = {}
        meta: Dict[str, Any] = {}
        item = self._item(goal)
        if item:
            args["item"] = item
            meta["item"] = "entity/excerpt/regex"
        quantity = _extract_quantity(goal)
        if quantity is not None:
            args["quantity"] = quantity
            meta["quantity"] = "regex"
        price = self._price(goal)
        if price is not None:
            args["price_rule"] = "fixed"
            args["price"] = int(price)
            meta["price"] = "number/regex"
        return args, meta

    def _chat_args(self, goal: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        args: Dict[str, Any] = {}
        meta: Dict[str, Any] = {}
        text = _extract_chat_text(goal)
        if text:
            args["text"] = text
            meta["text"] = "regex"
        channel = self._channel(goal)
        if channel:
            args["channel"] = channel
            meta["channel"] = "choice/regex"
        return args, meta

    def _query(self, goal: str) -> Optional[str]:
        found = _find_any_entity(goal)
        if found:
            return found
        answer = _excerpt(
            self._decide(goal, {"query": {
                "type": "excerpt",
                "instructions": "Which exact name or keyword should be looked up? Return only the name, not the question words.",
            }}), "query")
        answer = _strip_question_words(_clean_phrase(answer))
        if answer and _normalize(answer) != _normalize(goal):
            return answer
        for prefixes in (
            ("parle-moi de", "parle moi de", "háblame de", "hablame de",
             "erzähl mir von", "erzahl mir von", "who is", "qui est",
             "quién es", "quien es", "wer ist", "look up", "busca", "suche",
             "trouve", "encuentra"),
        ):
            candidate = _strip_question_words(_after_prefix(goal, prefixes))
            if candidate and _normalize(candidate) != _normalize(goal):
                return candidate
        cleaned = _strip_question_words(_clean_phrase(goal))
        return cleaned or goal

    def _item(self, goal: str) -> Optional[str]:
        found = _find_entity(goal, "item")
        if found:
            return found
        for pattern in (_RE_ITEM_SELL, _RE_ITEM_LIST):
            match = pattern.search(goal)
            if match:
                candidate = _clean_item(match.group(1))
                if candidate:
                    return candidate
        answer = _excerpt(
            self._decide(goal, {"item": {
                "type": "excerpt",
                "instructions": "What is the exact item name in the goal? Return only the item name, not the verb or the market phrase.",
            }}), "item")
        answer = _clean_item(answer)
        if answer and _normalize(answer) != _normalize(goal):
            return answer
        for prefixes in (
            ("sell", "vends", "vendre", "vend", "vender", "vende", "verkaufe"),
            ("scan", "scanne", "escanea", "check the market for", "vérifie le marché pour",
             "verifie le marche pour", "consulta el mercado para", "consulta el mercado por",
             "prüfe den markt für", "pruefe den markt fuer",
             "price of", "prix de", "prix du", "prix d'", "precio de", "preis von"),
            ("list", "put", "mettre en vente", "poner en venta", "list my"),
            ("mets en vente", "pon en venta", "poner a la venta", "put up for sale"),
            ("cuánto cuesta", "cuanto cuesta", "combien vaut", "combien coûte",
             "was kostet", "how much does", "how much is"),
        ):
            candidate = _clean_item(_after_prefix(goal, prefixes))
            if candidate:
                return candidate
        return None

    def _price(self, goal: str) -> Optional[int]:
        match = _RE_PRICE_CURRENCY.search(goal)
        if match:
            return int(match.group(1))
        match = _RE_PRICE.search(goal)
        if match:
            return int(match.group(1))
        value = self._number(goal, "What price per unit is stated in the goal?",
                             minimum=1, maximum=10**12)
        if value is not None:
            return int(value)
        return None

    def _channel(self, goal: str) -> Optional[str]:
        folded = _normalize(goal)
        if any(word in folded for word in (
                "team", "equipe", "equipo", "group", "gruppe", "grupo", "party")):
            return "team"
        if any(word in folded for word in ("guild", "guilde", "gremio", "gilde")):
            return "guild"
        if any(word in folded for word in ("whisper", "private", "prive", "privado")):
            return "whisper"
        if re.match(r"^\s*(?:say|dis|di|sag)\b", goal, re.IGNORECASE):
            return "say"
        answer = self._decide(goal, {"channel": {
            "type": "choice",
            "instructions": "Which chat channel does the goal ask for?",
            "criteria": {
                "say": "public say chat",
                "team": "team or party chat",
                "guild": "guild chat",
                "whisper": "private message to one person",
            },
        }}).get("channel") or {}
        choice = answer.get("choice")
        if choice in ("say", "team", "guild", "whisper"):
            return choice
        return "say"

    def _number(self, goal: str, question: str, minimum: float,
                maximum: float) -> Optional[float]:
        answer = self._decide(goal, {"n": {
            "type": "number",
            "instructions": question,
            "criteria": {"min": minimum, "max": maximum, "precision": 1},
        }}).get("n") or {}
        value = answer.get("value")
        return float(value) if value is not None else None

    def _bool(self, goal: str, question: str) -> Optional[bool]:
        answer = self._decide(goal, {"b": {"type": "noul", "instructions": question}}).get("b") or {}
        value = answer.get("noul")
        if value is None:
            return None
        return float(value) >= 0.5


def _default_model_path() -> str:
    env = os.environ.get("THX01_MODEL")
    if env:
        return env
    home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    snapshots = home / "hub" / "models--doofz--THX-01" / "snapshots"
    for candidate in sorted(snapshots.glob("*"), reverse=True):
        if (candidate / "rl_agent_config.json").exists() and (candidate / "model.safetensors").exists():
            return str(candidate)
    return "doofz/THX-01"


def _excerpt(answers: Dict[str, Any], key: str) -> Optional[str]:
    answer = answers.get(key) or {}
    value = answer.get("text")
    return value if isinstance(value, str) and value.strip() else None


def _extract_coords(goal: str) -> Optional[Tuple[int, int]]:
    match = _RE_COORDS.search(goal) or _RE_COORDS_NATURAL.search(goal)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _extract_zaap(goal: str) -> Optional[str]:
    match = _RE_ZAAP.search(goal)
    if not match:
        return None
    value = _clean_phrase(match.group(1))
    if not value:
        return None
    value = re.sub(
        r"^(?:to|de|du|au|aux|à|a|al|nach|zum|zur|vers|para|per)\s+",
        "", value, flags=re.IGNORECASE,
    )
    return _clean_phrase(value)


def _extract_chat_text(goal: str) -> Optional[str]:
    to_match = _RE_CHAT_TO.match(goal)
    if to_match:
        return _clean_phrase(to_match.group(1))
    quoted = re.search(r"(?<![A-Za-z])[\"“«](.+?)[\"”»](?![A-Za-z])", goal)
    if quoted:
        return _clean_phrase(quoted.group(1))
    cleaned = _RE_CHAT_CHANNEL.sub("", goal).strip()
    cleaned = _RE_CHAT_PREFIX.sub("", cleaned).strip()
    return _clean_phrase(cleaned)


def _after_farm_verb(goal: str) -> Optional[str]:
    match = _RE_FARM_VERB.search(goal)
    if not match:
        return None
    return _clean_phrase(goal[match.end():])


def _strip_question_words(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    patterns = (
        r"^(?:what|who|which)\s+(?:is|are|was|were)\s+(?:a|an|the)?\s*",
        r"^(?:tell me about|informations? sur|qu'est-ce que|qu'est ce que|"
        r"qué es|que es|was ist)\s+(?:un|une|le|la|les|a|an|the)?\s*",
        r"^(?:look up|search for|find|cherche|recherche|busca|buscar|suche|"
        r"zeig|montre|muestra)\s+",
    )
    value = text.strip(" .,!?:;\"'")
    for pattern in patterns:
        value = re.sub(pattern, "", value, flags=re.IGNORECASE).strip(" .,!?:;\"'")
    return value or None


def _predictions(policy: ThxPolicy, cases: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    predictions: Dict[str, Any] = {}
    for case in cases:
        call = policy.choose(case["goal"])
        if call is None:
            predictions[case["id"]] = None
        else:
            predictions[case["id"]] = {"tool": call.tool, "args": call.args}
    return predictions


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="THX-01 text-to-action policy")
    parser.add_argument("--model", default=None, help="model path or HF hub id")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--fallback", action="store_true",
                        help="fall back to RulePolicy on invalid or low-confidence calls")
    parser.add_argument("--min-tool-confidence", type=float, default=0.0)
    sub = parser.add_subparsers(dest="command")

    bench = sub.add_parser("bench", help="run the 71-case tool benchmark")
    bench.add_argument("--out", default=None, help="write JSONL predictions")
    bench.add_argument("--show-failures", action="store_true")

    decide = sub.add_parser("decide", help="resolve one goal")
    decide.add_argument("goal")

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2

    fallback = RulePolicy() if args.fallback else None
    policy = ThxPolicy(
        model=args.model,
        device=args.device,
        fallback=fallback,
        min_tool_confidence=args.min_tool_confidence,
    )

    if args.command == "decide":
        call = policy.choose(args.goal)
        print(json.dumps({
            "call": {"tool": call.tool, "args": call.args} if call else None,
            "decision": policy.last,
        }, ensure_ascii=False, indent=2))
        return 0

    from agent.tool_eval import GOLD, evaluate

    started = time.perf_counter()
    predictions = _predictions(policy, GOLD)
    elapsed = time.perf_counter() - started
    report = evaluate(predictions)
    totals = report["totals"]
    print(f"THX-01 policy - {totals['cases']} cases in {elapsed:.1f}s "
          f"({1000 * elapsed / max(totals['cases'], 1):.0f} ms/case)")
    print(f"  tool accuracy: {totals['tool_ok']}/{totals['cases']} "
          f"({100 * totals['tool_ok'] / max(totals['cases'], 1):.1f}%)")
    print(f"  call accuracy: {totals['call_ok']}/{totals['cases']} "
          f"({100 * totals['call_ok'] / max(totals['cases'], 1):.1f}%)")
    for lang, stats in report["by_lang"].items():
        print(f"  [{lang}] tool {stats['tool_ok']}/{stats['cases']}  "
              f"call {stats['call_ok']}/{stats['cases']}")
    if args.show_failures:
        for failure in report["failures"]:
            print(f"  MISS {failure['id']}: {failure['goal']}")
            print(f"       expected {failure['expected']}")
            print(f"       predicted {failure['predicted']}")
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for case in GOLD:
                row = {"id": case["id"], **(predictions.get(case["id"]) or {})}
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"  predictions: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
