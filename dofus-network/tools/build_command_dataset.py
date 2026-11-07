"""Build the command/intent dataset from the human gold set and the chat corpus.

The gold set (`data/core_sft_v4/gold.jsonl`) is 71 human-written commands across 17
tools and four languages; the commander corpus adds chat/moderation rows with action
labels; `CORE_ROWS` adds schema-consistent phrasings for intents the gold set covers
only once. They are small, so this builder:

- keeps the human phrasings and splits them per intent: the last two origins by
  origin id become val and test (held-out *phrasings*), while the `aa-core-*`
  origins sort first and stay in train;
- excludes every exact val/test text from train, base and augmented alike: the
  gold set repeats "zaap Astrub" once per language, and an origin split alone
  used to put that phrasing on both sides;
- augments train rows with entities from a **train pool** and val/test rows with a
  disjoint **eval pool** (held-out *entities*: unseen items, mobs, areas, quest ids,
  prices, quantities, channels, messages);
- adds polite frames ("can you ...?") to train only, so the command body, not the
  framing, has to decide the intent;
- writes one row per (text, intent, slots) with the command schema from
  `agent/commands.py`, and one label per text (conflicts are reported, not taught).

The rule baseline is measured on the same held-out rows by the training scripts; the
learned command encoder must beat it there before it replaces a rule.

    python tools/build_command_dataset.py --out data/commands --variants 8
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
GOLD = ROOT / "data" / "core_sft_v4" / "gold.jsonl"
COMMANDER = ROOT / "data" / "dofus_decision_dataset.json"
DEFAULT_OUT = ROOT / "data" / "commands"

TRAIN_POOLS: Dict[str, List[Any]] = {
    "item": ["Bouftou Wool", "Gold Ingot", "blé", "fer", "bois de chêne"],
    "mob": ["kanigs", "pious", "bouftous", "wabbits"],
    "area": ["Astrub", "Bonta", "Amakna"],
    "quest_id": [1696, 1935, 3841],
    "price": [500, 1200, 250],
    "quantity": [5, 10, 2],
    "zaap": ["Astrub", "Bonta"],
    "channel": ["team", "guild"],
    "message": ["hello", "bonjour", "gg"],
    "query": ["Bouftou", "kanigs", "Gold Ingot"],
}

# Disjoint from the train pool *and* from the text of the gold/commander rows, so a
# held-out entity really is unseen when a val/test row uses it.
EVAL_POOLS: Dict[str, List[Any]] = {
    "item": ["Plume de Tofu", "Cuir de Sanglier", "Minerai de Cuivre"],
    "mob": ["tofus", "sangliers", "dragoeufs"],
    "area": ["Pandala", "Otomaï", "Frigost"],
    "quest_id": [5001, 5002, 5003],
    "price": [1700, 900, 330],
    "quantity": [7, 20, 3],
    "zaap": ["Pandala", "Frigost"],
    "channel": ["general", "trade"],
    "message": ["salut", "bravo", "bien joué"],
    "query": ["Tofu", "Sanglier", "Dragoeuf"],
}

COMMANDER_ACTIONS = {
    "FOLLOW": "nav.follow",
    "STOP": "safe.stop",
    "PASS_TURN": "fight.pass",
    "AUTO_JOIN_COMBAT": "fight.join",
    # AUTO_FARM is deliberately excluded: the action label is intent-only, while a
    # farm command's target (mob/resource/zone) is a slot. The gold rows carry that
    # label properly; teaching the model an empty target would be worse than skipping
    # a handful of chat rows.
}

# A few intents have no human gold row (auto, generic "do the quests", a bare
# "farm"). They are still commands, and the learned parser must know them; the
# rule parser's `_goal_mode` is the only current source for them.
SEED_ROWS = [
    {"text": "auto", "lang": "en", "intent": "auto", "slots": {}},
    {"text": "full auto", "lang": "en", "intent": "auto", "slots": {}},
    {"text": "automatique", "lang": "fr", "intent": "auto", "slots": {}},
    {"text": "mode automatique", "lang": "fr", "intent": "auto", "slots": {}},
    {"text": "do the quests", "lang": "en", "intent": "quest", "slots": {"action": "run"}},
    {"text": "fais les quêtes", "lang": "fr", "intent": "quest", "slots": {"action": "run"}},
    {"text": "farm", "lang": "en", "intent": "farm", "slots": {"action": "start"}},
    {"text": "farmear", "lang": "es", "intent": "farm", "slots": {"action": "start"}},
]

# Training-only phrasings, authored against the same schema. Their origins sort
# first ("aa-core-*"), so the per-intent split keeps them in train and the 26
# base eval phrasings above stay exactly as they were. They exist because the
# first held-out evaluation exposed intents with one phrasing only: "farmear"
# and "mode automatique" were unlearnable, and no train row carried the
# quest "run" action at all.
CORE_ROWS = [
    # auto
    {"text": "run auto", "lang": "en", "intent": "auto", "slots": {}},
    {"text": "auto mode", "lang": "en", "intent": "auto", "slots": {}},
    {"text": "full-auto", "lang": "en", "intent": "auto", "slots": {}},
    {"text": "start autopilot", "lang": "en", "intent": "auto", "slots": {}},
    {"text": "passe en auto", "lang": "fr", "intent": "auto", "slots": {}},
    {"text": "mode auto", "lang": "fr", "intent": "auto", "slots": {}},
    {"text": "modo auto", "lang": "es", "intent": "auto", "slots": {}},
    {"text": "activar auto", "lang": "es", "intent": "auto", "slots": {}},
    {"text": "auto modus", "lang": "de", "intent": "auto", "slots": {}},
    {"text": "automatisch", "lang": "de", "intent": "auto", "slots": {}},
    # quest: run / list / advance
    {"text": "run the quests", "lang": "en", "intent": "quest", "slots": {"action": "run"}},
    {"text": "start the quest chain", "lang": "en", "intent": "quest", "slots": {"action": "run"}},
    {"text": "quest run", "lang": "en", "intent": "quest", "slots": {"action": "run"}},
    {"text": "lance les quêtes", "lang": "fr", "intent": "quest", "slots": {"action": "run"}},
    {"text": "commence les quêtes", "lang": "fr", "intent": "quest", "slots": {"action": "run"}},
    {"text": "haz las misiones", "lang": "es", "intent": "quest", "slots": {"action": "run"}},
    {"text": "empieza las misiones", "lang": "es", "intent": "quest", "slots": {"action": "run"}},
    {"text": "starte die Quests", "lang": "de", "intent": "quest", "slots": {"action": "run"}},
    {"text": "list my quests", "lang": "en", "intent": "quest", "slots": {"action": "list"}},
    {"text": "liste mes quêtes", "lang": "fr", "intent": "quest", "slots": {"action": "list"}},
    {"text": "lista mis misiones", "lang": "es", "intent": "quest", "slots": {"action": "list"}},
    {"text": "advance quest 1935", "lang": "en", "intent": "quest",
     "slots": {"action": "advance", "quest_id": 1935}},
    {"text": "avance la quête 1935", "lang": "fr", "intent": "quest",
     "slots": {"action": "advance", "quest_id": 1935}},
    {"text": "avanza la misión 1935", "lang": "es", "intent": "quest",
     "slots": {"action": "advance", "quest_id": 1935}},
    # farm: start / stop
    {"text": "start farming", "lang": "en", "intent": "farm", "slots": {"action": "start"}},
    {"text": "go farm", "lang": "en", "intent": "farm", "slots": {"action": "start"}},
    {"text": "farm mobs", "lang": "en", "intent": "farm", "slots": {"action": "start"}},
    {"text": "farm resources", "lang": "en", "intent": "farm", "slots": {"action": "start"}},
    {"text": "grind monsters", "lang": "en", "intent": "farm", "slots": {"action": "start"}},
    {"text": "commence à farmer", "lang": "fr", "intent": "farm", "slots": {"action": "start"}},
    {"text": "lance le farm", "lang": "fr", "intent": "farm", "slots": {"action": "start"}},
    {"text": "farme les bouftous", "lang": "fr", "intent": "farm",
     "slots": {"action": "start", "mob": "bouftous"}},
    {"text": "empieza a farmear", "lang": "es", "intent": "farm", "slots": {"action": "start"}},
    {"text": "farmea kanigs", "lang": "es", "intent": "farm",
     "slots": {"action": "start", "mob": "kanigs"}},
    {"text": "farmen starten", "lang": "de", "intent": "farm", "slots": {"action": "start"}},
    {"text": "stop farming", "lang": "en", "intent": "farm", "slots": {"action": "stop"}},
    {"text": "arrête de farmer", "lang": "fr", "intent": "farm", "slots": {"action": "stop"}},
    {"text": "para de farmear", "lang": "es", "intent": "farm", "slots": {"action": "stop"}},
    {"text": "farm stoppen", "lang": "de", "intent": "farm", "slots": {"action": "stop"}},
    # fight: join / pass
    {"text": "join combat", "lang": "en", "intent": "fight", "slots": {"action": "join"}},
    {"text": "entre dans le combat", "lang": "fr", "intent": "fight", "slots": {"action": "join"}},
    {"text": "entra al combate", "lang": "es", "intent": "fight", "slots": {"action": "join"}},
    {"text": "Kampf beitreten jetzt", "lang": "de", "intent": "fight",
     "slots": {"action": "join"}},
    {"text": "pass turn", "lang": "en", "intent": "fight", "slots": {"action": "pass"}},
    {"text": "passe ton tour", "lang": "fr", "intent": "fight", "slots": {"action": "pass"}},
    {"text": "pasa turno", "lang": "es", "intent": "fight", "slots": {"action": "pass"}},
    {"text": "Zug passen", "lang": "de", "intent": "fight", "slots": {"action": "pass"}},
    # follow
    {"text": "follow", "lang": "en", "intent": "follow", "slots": {}},
    {"text": "follow the group", "lang": "en", "intent": "follow", "slots": {}},
    {"text": "suis le groupe", "lang": "fr", "intent": "follow", "slots": {}},
    {"text": "sigue al grupo", "lang": "es", "intent": "follow", "slots": {}},
    {"text": "folge der Gruppe", "lang": "de", "intent": "follow", "slots": {}},
    # inventory: list / sell
    {"text": "show inventory", "lang": "en", "intent": "inventory", "slots": {"action": "list"}},
    {"text": "what is in my bag", "lang": "en", "intent": "inventory",
     "slots": {"action": "list"}},
    {"text": "affiche l'inventaire", "lang": "fr", "intent": "inventory",
     "slots": {"action": "list"}},
    {"text": "abre el inventario", "lang": "es", "intent": "inventory",
     "slots": {"action": "list"}},
    {"text": "Inventar zeigen", "lang": "de", "intent": "inventory",
     "slots": {"action": "list"}},
    {"text": "sell my junk", "lang": "en", "intent": "inventory", "slots": {"action": "sell"}},
    {"text": "vends mes affaires", "lang": "fr", "intent": "inventory",
     "slots": {"action": "sell"}},
    {"text": "vende la basura", "lang": "es", "intent": "inventory",
     "slots": {"action": "sell"}},
    {"text": "verkaufe den Müll", "lang": "de", "intent": "inventory",
     "slots": {"action": "sell"}},
    # market: scan / list
    {"text": "check the price of Gold Ingot", "lang": "en", "intent": "market",
     "slots": {"action": "scan", "item": "Gold Ingot"}},
    {"text": "vérifie le prix du fer", "lang": "fr", "intent": "market",
     "slots": {"action": "scan", "item": "fer"}},
    {"text": "mira el precio del blé", "lang": "es", "intent": "market",
     "slots": {"action": "scan", "item": "blé"}},
    {"text": "Preis von Eisen prüfen", "lang": "de", "intent": "market",
     "slots": {"action": "scan", "item": "Eisen"}},
    {"text": "list my fer on the market for 450 kamas", "lang": "en",
     "intent": "market", "slots": {"action": "list", "item": "fer", "price": 450,
                                             "price_rule": "fixed"}},
    {"text": "mets le fer en vente à 650", "lang": "fr", "intent": "market",
     "slots": {"action": "list", "item": "fer", "price": 650,
                                                "price_rule": "fixed"}},
    {"text": "pon el blé a la venta por 800", "lang": "es", "intent": "market",
     "slots": {"action": "list", "item": "blé", "price": 800,
                                                "price_rule": "fixed"}},
    {"text": "verkaufe Eisen für 500 Kamas", "lang": "de", "intent": "market",
     "slots": {"action": "list", "item": "Eisen", "price": 500,
                                                "price_rule": "fixed"}},
    # chat
    {"text": "say hi in team chat", "lang": "en", "intent": "chat",
     "slots": {"channel": "team", "message": "hi"}},
    {"text": "send gg to the team", "lang": "en", "intent": "chat",
     "slots": {"channel": "team", "message": "gg"}},
    {"text": "dis gg dans le chat de guilde", "lang": "fr", "intent": "chat",
     "slots": {"channel": "guild", "message": "gg"}},
    {"text": "envoie bonjour à l'équipe", "lang": "fr", "intent": "chat",
     "slots": {"channel": "team", "message": "bonjour"}},
    {"text": "di gracias en el chat de equipo", "lang": "es", "intent": "chat",
     "slots": {"channel": "team", "message": "gracias"}},
    {"text": "sag Hallo im Gildenchat", "lang": "de", "intent": "chat",
     "slots": {"channel": "guild", "message": "Hallo"}},
    # knowledge
    {"text": "look up Kanig", "lang": "en", "intent": "knowledge", "slots": {"query": "Kanig"}},
    {"text": "tell me about Kanig", "lang": "en", "intent": "knowledge",
     "slots": {"query": "Kanig"}},
    {"text": "c'est quoi un Bouftou", "lang": "fr", "intent": "knowledge",
     "slots": {"query": "Bouftou"}},
    {"text": "qué es un jalato", "lang": "es", "intent": "knowledge",
     "slots": {"query": "jalato"}},
    {"text": "was ist ein Bouftou", "lang": "de", "intent": "knowledge",
     "slots": {"query": "Bouftou"}},
    {"text": "info on Gold Ingot", "lang": "en", "intent": "knowledge",
     "slots": {"query": "Gold Ingot"}},
    # state
    {"text": "where am i", "lang": "en", "intent": "state", "slots": {}},
    {"text": "my status", "lang": "en", "intent": "state", "slots": {}},
    {"text": "what is my hp", "lang": "en", "intent": "state", "slots": {}},
    {"text": "mon état", "lang": "fr", "intent": "state", "slots": {}},
    {"text": "mi estado", "lang": "es", "intent": "state", "slots": {}},
    {"text": "mein Status", "lang": "de", "intent": "state", "slots": {}},
    # stop
    {"text": "stop", "lang": "en", "intent": "stop", "slots": {}},
    {"text": "halt", "lang": "en", "intent": "stop", "slots": {}},
    {"text": "cancel everything", "lang": "en", "intent": "stop", "slots": {}},
    {"text": "arrête", "lang": "fr", "intent": "stop", "slots": {}},
    {"text": "para", "lang": "es", "intent": "stop", "slots": {}},
    {"text": "stopp", "lang": "de", "intent": "stop", "slots": {}},
    # unknown / social
    {"text": "hello", "lang": "en", "intent": "unknown", "slots": {}},
    {"text": "how are you", "lang": "en", "intent": "unknown", "slots": {}},
    {"text": "what's up", "lang": "en", "intent": "unknown", "slots": {}},
    {"text": "thanks", "lang": "en", "intent": "unknown", "slots": {}},
    {"text": "lol", "lang": "en", "intent": "unknown", "slots": {}},
    {"text": "tu es un bot ?", "lang": "fr", "intent": "unknown", "slots": {}},
    {"text": "merci", "lang": "fr", "intent": "unknown", "slots": {}},
    {"text": "eres real ?", "lang": "es", "intent": "unknown", "slots": {}},
    {"text": "gracias", "lang": "es", "intent": "unknown", "slots": {}},
    {"text": "wer bist du", "lang": "de", "intent": "unknown", "slots": {}},
    # travel
    {"text": "walk to [5, 10]", "lang": "en", "intent": "travel", "slots": {"x": 5, "y": 10}},
    {"text": "rends-toi en [2, 4]", "lang": "fr", "intent": "travel", "slots": {"x": 2, "y": 4}},
    {"text": "ve a [6, 7]", "lang": "es", "intent": "travel", "slots": {"x": 6, "y": 7}},
    {"text": "gehe zu [8, -2]", "lang": "de", "intent": "travel", "slots": {"x": 8, "y": -2}},
    {"text": "use zaap Bonta", "lang": "en", "intent": "travel", "slots": {"zaap": "Bonta"}},
    {"text": "zaap à Bonta", "lang": "fr", "intent": "travel", "slots": {"zaap": "Bonta"}},
    # Targeted at the first held-out failures: the intent must follow the verb,
    # not the entity, so each verb appears with several different names.
    {"text": "busca un kanig", "lang": "es", "intent": "knowledge", "slots": {"query": "kanig"}},
    {"text": "busca un jalato", "lang": "es", "intent": "knowledge", "slots": {"query": "jalato"}},
    {"text": "cherche un kanig", "lang": "fr", "intent": "knowledge", "slots": {"query": "kanig"}},
    {"text": "cherche du fer", "lang": "fr", "intent": "knowledge", "slots": {"query": "fer"}},
    {"text": "look up a jalato", "lang": "en", "intent": "knowledge", "slots": {"query": "jalato"}},
    {"text": "what is a kanig", "lang": "en", "intent": "knowledge", "slots": {"query": "kanig"}},
    {"text": "tell me about the blé", "lang": "en", "intent": "knowledge", "slots": {"query": "blé"}},
    {"text": "info sur le kanig", "lang": "fr", "intent": "knowledge", "slots": {"query": "kanig"}},
    {"text": "qué es un kanig", "lang": "es", "intent": "knowledge", "slots": {"query": "kanig"}},
    {"text": "was ist ein jalato", "lang": "de", "intent": "knowledge", "slots": {"query": "jalato"}},
    {"text": "écris gg dans le chat d'équipe", "lang": "fr", "intent": "chat",
     "slots": {"channel": "team", "message": "gg"}},
    {"text": "dis bonjour dans le chat de guilde", "lang": "fr", "intent": "chat",
     "slots": {"channel": "guild", "message": "bonjour"}},
    {"text": "di hola en el chat de gremio", "lang": "es", "intent": "chat",
     "slots": {"channel": "guild", "message": "hola"}},
    {"text": "sag gg im Teamchat", "lang": "de", "intent": "chat",
     "slots": {"channel": "team", "message": "gg"}},
    {"text": "dis à plus dans le chat d'équipe", "lang": "fr", "intent": "chat",
     "slots": {"channel": "team", "message": "à plus"}},
    {"text": "dis c'est bon dans le chat d'équipe", "lang": "fr", "intent": "chat",
     "slots": {"channel": "team", "message": "c'est bon"}},
    {"text": "tell the team gg", "lang": "en", "intent": "chat",
     "slots": {"channel": "team", "message": "gg"}},
    {"text": "say bonjour in team chat", "lang": "en", "intent": "chat",
     "slots": {"channel": "team", "message": "bonjour"}},
    {"text": "arrête tout le bot", "lang": "fr", "intent": "stop", "slots": {"action": "all"}},
    {"text": "stop everything please", "lang": "en", "intent": "stop",
     "slots": {"action": "all"}},
    {"text": "para todo ahora", "lang": "es", "intent": "stop", "slots": {"action": "all"}},
    {"text": "halt alles jetzt", "lang": "de", "intent": "stop", "slots": {"action": "all"}},
    {"text": "vends tout", "lang": "fr", "intent": "inventory",
     "slots": {"action": "sell", "all": True}},
    {"text": "vende todo", "lang": "es", "intent": "inventory",
     "slots": {"action": "sell", "all": True}},
    {"text": "sell everything in my bag", "lang": "en", "intent": "inventory",
     "slots": {"action": "sell", "all": True}},
    {"text": "tout vendre", "lang": "fr", "intent": "inventory",
     "slots": {"action": "sell", "all": True}},
    {"text": "use zaap Astrub", "lang": "en", "intent": "travel", "slots": {"zaap": "Astrub"}},
    {"text": "go zaap Amakna", "lang": "en", "intent": "travel", "slots": {"zaap": "Amakna"}},
    {"text": "zaap Amakna", "lang": "fr", "intent": "travel", "slots": {"zaap": "Amakna"}},
    {"text": "zaap à Amakna", "lang": "fr", "intent": "travel", "slots": {"zaap": "Amakna"}},
    {"text": "téléporte à Bonta", "lang": "fr", "intent": "travel", "slots": {"zaap": "Bonta"}},
    {"text": "ve al zaap Bonta", "lang": "es", "intent": "travel", "slots": {"zaap": "Bonta"}},
    {"text": "va en [-5, 8]", "lang": "fr", "intent": "travel", "slots": {"x": -5, "y": 8}},
    {"text": "go to [-2, 9]", "lang": "en", "intent": "travel", "slots": {"x": -2, "y": 9}},
    {"text": "ve a [-6, 3]", "lang": "es", "intent": "travel", "slots": {"x": -6, "y": 3}},
    {"text": "gehe zu [-4, 10]", "lang": "de", "intent": "travel", "slots": {"x": -4, "y": 10}},
    {"text": "va en [3, -7]", "lang": "fr", "intent": "travel", "slots": {"x": 3, "y": -7}},
    {"text": "fais la quête 1935", "lang": "fr", "intent": "quest",
     "slots": {"action": "advance", "quest_id": 1935}},
    {"text": "fais les quêtes de niveau 40", "lang": "fr", "intent": "quest",
     "slots": {"action": "list", "level_min": 40}},
    {"text": "fais les quêtes à Bonta", "lang": "fr", "intent": "quest",
     "slots": {"action": "list", "area": "Bonta"}},
    {"text": "fais toutes les quêtes", "lang": "fr", "intent": "quest",
     "slots": {"action": "run"}},
    {"text": "estás ahí amigo ?", "lang": "es", "intent": "unknown", "slots": {}},
    {"text": "me oyes ?", "lang": "es", "intent": "unknown", "slots": {}},
    {"text": "contesta rápido", "lang": "es", "intent": "unknown", "slots": {}},
    {"text": "tu m'écoutes ?", "lang": "fr", "intent": "unknown", "slots": {}},
    {"text": "réponds vite", "lang": "fr", "intent": "unknown", "slots": {}},
    {"text": "are you there ?", "lang": "en", "intent": "unknown", "slots": {}},
    {"text": "can you hear me ?", "lang": "en", "intent": "unknown", "slots": {}},
    {"text": "bist du da ?", "lang": "de", "intent": "unknown", "slots": {}},
    {"text": "hola amigo", "lang": "es", "intent": "unknown", "slots": {}},
    # Second failure pass: bare farm verbs must carry action=start; the all cue
    # must survive at the end of the sentence; Spanish social questions and
    # French "équipe" need more than one occurrence to tag.
    {"text": "farm now", "lang": "en", "intent": "farm", "slots": {"action": "start"}},
    {"text": "go farming", "lang": "en", "intent": "farm", "slots": {"action": "start"}},
    {"text": "farmear ya", "lang": "es", "intent": "farm", "slots": {"action": "start"}},
    {"text": "farmear ahora", "lang": "es", "intent": "farm", "slots": {"action": "start"}},
    {"text": "a farmear", "lang": "es", "intent": "farm", "slots": {"action": "start"}},
    {"text": "farmer", "lang": "fr", "intent": "farm", "slots": {"action": "start"}},
    {"text": "arrête tout maintenant", "lang": "fr", "intent": "stop",
     "slots": {"action": "all"}},
    {"text": "para todo ya", "lang": "es", "intent": "stop", "slots": {"action": "all"}},
    {"text": "stop all now", "lang": "en", "intent": "stop", "slots": {"action": "all"}},
    {"text": "vends tout maintenant", "lang": "fr", "intent": "inventory",
     "slots": {"action": "sell", "all": True}},
    {"text": "vende todo ya", "lang": "es", "intent": "inventory",
     "slots": {"action": "sell", "all": True}},
    {"text": "busca un dragón", "lang": "es", "intent": "knowledge",
     "slots": {"query": "dragón"}},
    {"text": "busca un gobelin", "lang": "es", "intent": "knowledge",
     "slots": {"query": "gobelin"}},
    {"text": "busca un loup", "lang": "es", "intent": "knowledge",
     "slots": {"query": "loup"}},
    {"text": "eres un robot", "lang": "es", "intent": "unknown", "slots": {}},
    {"text": "tu es un robot", "lang": "fr", "intent": "unknown", "slots": {}},
    {"text": "are you a bot", "lang": "en", "intent": "unknown", "slots": {}},
    {"text": "je suis où", "lang": "fr", "intent": "state", "slots": {}},
    {"text": "ma position", "lang": "fr", "intent": "state", "slots": {}},
    {"text": "estas ahi amigo", "lang": "es", "intent": "unknown", "slots": {}},
    {"text": "responde ya", "lang": "es", "intent": "unknown", "slots": {}},
    {"text": "me escuchas", "lang": "es", "intent": "unknown", "slots": {}},
    {"text": "dis merci dans le chat d'équipe", "lang": "fr", "intent": "chat",
     "slots": {"channel": "team", "message": "merci"}},
    {"text": "dis bonne nuit dans le chat d'équipe", "lang": "fr", "intent": "chat",
     "slots": {"channel": "team", "message": "bonne nuit"}},
    {"text": "dis bonjour dans le chat de groupe", "lang": "fr", "intent": "chat",
     "slots": {"channel": "team", "message": "bonjour"}},
    {"text": "envoie gg à l'équipe", "lang": "fr", "intent": "chat",
     "slots": {"channel": "team", "message": "gg"}},
]

# Polite wrappers applied to train rows only. They teach that the command body
# decides the intent, not the framing; eval phrasings stay unadorned.
FRAMES = {
    "en": ["please {body}", "can you {body}?", "{body} please"],
    "fr": ["peux-tu {body} ?", "stp {body}", "{body} s'il te plaît"],
    "es": ["puedes {body}?", "por favor {body}", "{body} por favor"],
    "de": ["kannst du {body}?", "bitte {body}", "{body} bitte"],
}

_TEXT_POOL_KEYS = ("item", "mob", "area", "zaap", "channel", "message", "query")

PREFIXES = {
    "en": ["please ", "now ", "quickly "],
    "fr": ["stp ", "maintenant ", "vite "],
    "es": ["por favor ", "ahora ", "rápido "],
    "de": ["bitte ", "jetzt ", "schnell "],
}
SUFFIXES = {
    "en": [" please", " now", " quickly"],
    "fr": [" stp", " maintenant", " vite"],
    "es": [" por favor", " ahora", " rápido"],
    "de": [" bitte", " jetzt", " schnell"],
}


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _replace(text: str, old: Any, new: Any) -> Optional[str]:
    old_s, new_s = str(old), str(new)
    if not old_s or old_s == new_s or old_s not in text:
        return None
    return text.replace(old_s, new_s)


def _augment(text: str, slots: Dict[str, Any], pool: Dict[str, List[Any]],
             rng: random.Random) -> Optional[Dict[str, Any]]:
    """Replace every replaceable slot with a value from the pool.

    A slot whose original value is not literally in the text is left alone; the row
    still carries the label, but the text cannot be rewritten safely.
    """
    out_text = text
    out_slots = dict(slots)
    changed = False
    for key, value in list(slots.items()):
        choices = pool.get(key)
        if not choices:
            continue
        pick = rng.choice(choices)
        replaced = _replace(out_text, value, pick)
        if replaced is None:
            continue
        out_text = replaced
        out_slots[key] = pick
        changed = True
    return {"text": out_text, "slots": out_slots} if changed else None


def _gold_rows() -> List[Dict[str, Any]]:
    from agent.commands import from_tool

    rows: List[Dict[str, Any]] = []
    with GOLD.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except ValueError:
                continue
            command = from_tool(raw.get("tool", ""), raw.get("args"),
                                raw.get("goal", ""), lang=raw.get("lang"),
                                source="gold", confidence=1.0)
            rows.append({**command, "origin": str(raw.get("id") or command["text"])})
    return rows


def _commander_rows() -> List[Dict[str, Any]]:
    from agent.commands import from_tool

    if not COMMANDER.is_file():
        return []
    raw_rows = json.loads(COMMANDER.read_text(encoding="utf-8"))
    rows: List[Dict[str, Any]] = []
    for index, raw in enumerate(raw_rows if isinstance(raw_rows, list) else []):
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        action = raw.get("action")
        if action == "NONE":
            command = {
                "text": text, "lang": raw.get("lang") or "auto",
                "intent": "unknown", "slots": {}, "source": "commander",
                "confidence": 1.0,
            }
        elif action in COMMANDER_ACTIONS:
            command = from_tool(COMMANDER_ACTIONS[action], {}, text,
                                lang=raw.get("lang"), source="commander",
                                confidence=1.0)
        else:
            continue
        rows.append({**command, "origin": "commander-%d" % index})
    return rows


def _seed_rows() -> List[Dict[str, Any]]:
    return [{
        "text": row["text"], "lang": row["lang"], "intent": row["intent"],
        "slots": dict(row["slots"]), "source": "seed", "confidence": 1.0,
        "origin": "seed-%d" % index,
    } for index, row in enumerate(SEED_ROWS)]


def _core_rows() -> List[Dict[str, Any]]:
    return [{
        "text": row["text"], "lang": row["lang"], "intent": row["intent"],
        "slots": dict(row["slots"]), "source": "core", "confidence": 1.0,
        "origin": "aa-core-%03d" % index,
    } for index, row in enumerate(CORE_ROWS)]


def _framed(text: str, lang: str) -> List[str]:
    body = text[:1].lower() + text[1:] if text else text
    return [frame.format(body=body) for frame in FRAMES.get(lang) or FRAMES["en"]]


def _leaks(train_rows: List[Dict[str, Any]], train_text: str) -> List[Dict[str, Any]]:
    """Held-out values the train split still exposes.

    Named entities are checked in the text (a train sentence that says "Plume de
    Tofu" leaks it even if that row's own slot is different). Numbers are checked in
    the **labels** only: a coordinate `7` in a travel row is not the held-out
    quantity `7`, and treating it as one flags a false leak.
    """
    out: List[Dict[str, Any]] = []
    for key, values in EVAL_POOLS.items():
        for value in values:
            if key in _TEXT_POOL_KEYS and str(value) in train_text:
                out.append({"slot": key, "value": value, "where": "text"})
    for row in train_rows:
        for key, value in (row.get("slots") or {}).items():
            if key in EVAL_POOLS and value in EVAL_POOLS[key]:
                out.append({"slot": key, "value": value, "where": "slot"})
    unique = {(item["slot"], str(item["value"]), item["where"]) for item in out}
    return [{"slot": slot, "value": value, "where": where}
            for slot, value, where in sorted(unique)]


def _affixed(text: str, lang: str, rng: random.Random) -> List[str]:
    """Natural prefix/suffix variants for a train phrasing."""
    out: List[str] = []
    body = text[:1].lower() + text[1:] if text else text
    for prefix in (PREFIXES.get(lang) or PREFIXES["en"])[:2]:
        out.append(prefix + body)
    for suffix in (SUFFIXES.get(lang) or SUFFIXES["en"])[:2]:
        out.append(text + suffix)
    return out


def build(out: Path = DEFAULT_OUT, variants: int = 8, seed: int = 7) -> Dict[str, Any]:
    rng = random.Random(seed)
    base = _gold_rows() + _commander_rows() + _seed_rows() + _core_rows()

    # Stratify by intent: every intent with >=3 phrasings holds one out for val and
    # one for test. A random row split (the previous version) left whole intents out
    # of the evaluation and made the learned parser look better or worse by luck.
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in base:
        groups.setdefault(row["intent"], []).append(row)
    assigned: List[Any] = []
    for intent, rows in sorted(groups.items()):
        rows.sort(key=lambda item: str(item.get("origin")))
        if len(rows) >= 3:
            train_rows, val_row, test_row = rows[:-2], rows[-2], rows[-1]
        elif len(rows) == 2:
            train_rows, val_row, test_row = rows[:1], None, rows[-1]
        else:
            train_rows, val_row, test_row = rows, None, None
        for split, members in (("train", train_rows),
                               ("val", [val_row] if val_row else []),
                               ("test", [test_row] if test_row else [])):
            for row in members:
                assigned.append((split, row))

    def command_of(row: Dict[str, Any]) -> Dict[str, Any]:
        return {key: row[key] for key in
                ("text", "lang", "intent", "slots", "source", "confidence", "origin")}

    splits: Dict[str, List[Dict[str, Any]]] = {"train": [], "val": [], "test": []}

    def emit(split: str, command: Dict[str, Any], text: str,
             slots: Dict[str, Any], origin: str) -> None:
        splits[split].append({**command, "text": text, "slots": slots,
                              "origin": origin})

    # Evaluation first: its exact texts (base and augmented) are then excluded from
    # train. The previous build split by origin only, so "zaap Astrub" -- one gold
    # row in each language -- could sit in train and test at the same time.
    eval_texts: set = set()
    for split, row in assigned:
        if split == "train":
            continue
        command = command_of(row)
        emit(split, command, command["text"], dict(command["slots"]),
             command["origin"])
        eval_texts.add(_norm(command["text"]))
        seen = {command["text"]}
        for _ in range(max(2, variants // 2)):
            augmented = _augment(command["text"], command["slots"], EVAL_POOLS, rng)
            if augmented is None or augmented["text"] in seen:
                continue
            seen.add(augmented["text"])
            emit(split, command, augmented["text"], augmented["slots"],
                 command["origin"] + "~aug")
            eval_texts.add(_norm(augmented["text"]))

    # A train row whose tokens contain an eval phrasing in full teaches the eval.
    # Filtering full-token containment (for eval rows of >=2 tokens) removes the
    # near-copies without dropping legitimate entity/verb variations like
    # "busca un kanig" against the held-out "busca Bouftou".
    eval_token_sets = [set(text.split()) for text in eval_texts]
    eval_token_sets = [tokens for tokens in eval_token_sets if len(tokens) >= 2]

    def contains_eval(text: str) -> bool:
        tokens = set(_norm(text).split())
        return any(eval_tokens <= tokens for eval_tokens in eval_token_sets)

    skipped = near_copies = 0
    for split, row in assigned:
        if split != "train":
            continue
        command = command_of(row)
        if _norm(command["text"]) in eval_texts:
            skipped += 1
            continue
        if contains_eval(command["text"]):
            near_copies += 1
            continue
        emit(split, command, command["text"], dict(command["slots"]),
             command["origin"])
        seen = {command["text"]}

        def fresh(text: str) -> bool:
            return (text not in seen and _norm(text) not in eval_texts
                    and not contains_eval(text))

        for _ in range(variants):
            augmented = _augment(command["text"], command["slots"], TRAIN_POOLS, rng)
            if augmented is None or not fresh(augmented["text"]):
                continue
            seen.add(augmented["text"])
            emit(split, command, augmented["text"], augmented["slots"],
                 command["origin"] + "~aug")
        if command["intent"] != "unknown":
            for affixed in _affixed(command["text"], command["lang"], rng):
                if not fresh(affixed):
                    continue
                seen.add(affixed)
                emit(split, command, affixed, dict(command["slots"]),
                     command["origin"] + "~aff")
            for framed in _framed(command["text"], command["lang"]):
                if not fresh(framed):
                    continue
                seen.add(framed)
                emit(split, command, framed, dict(command["slots"]),
                     command["origin"] + "~frame")

    # One text, one label. Duplicate phrasings are dropped; a duplicate with a
    # different label is a corpus bug and is reported rather than silently taught.
    seen_train: Dict[str, Any] = {}
    clean: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, Any]] = []
    for row in splits["train"]:
        key = _norm(row["text"])
        label = (row["intent"], json.dumps(row["slots"], sort_keys=True,
                                          ensure_ascii=False))
        if key in eval_texts:
            skipped += 1
            continue
        if key in seen_train:
            if seen_train[key] != label:
                conflicts.append({"text": row["text"],
                                  "kept": seen_train[key][0],
                                  "dropped": label[0]})
            continue
        seen_train[key] = label
        clean.append(row)
    splits["train"] = clean

    out.mkdir(parents=True, exist_ok=True)
    for name, rows in splits.items():
        with (out / (name + ".jsonl")).open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    # A held-out entity that appears in a train row is not held out.
    train_text = " ".join(row["text"] for row in splits["train"])
    leaked = _leaks(splits["train"], train_text)
    leaked_texts = [row["text"] for row in splits["train"]
                    if _norm(row["text"]) in eval_texts]
    manifest = {
        "schema": "command-intent-slots-v1",
        "generated_from": [str(GOLD.name), str(COMMANDER.name)],
        "seed": seed,
        "variants": variants,
        "split": "per-intent stratified: one val, one test phrasing; core "
                 "phrasings are train-only; eval texts are excluded from train",
        "core_phrasings": len(CORE_ROWS),
        "counts": {name: len(rows) for name, rows in splits.items()},
        "intents": {name: dict(Counter(row["intent"] for row in rows).most_common())
                    for name, rows in splits.items()},
        "languages": {name: dict(Counter(row["lang"] for row in rows).most_common())
                      for name, rows in splits.items()},
        "sources": {name: dict(Counter(row["source"] for row in rows).most_common())
                    for name, rows in splits.items()},
        "held_out_entities": EVAL_POOLS,
        "eval_entity_leaks_in_train": leaked,
        "train_rows_skipped_for_eval_text": skipped,
        "train_near_copies_removed": near_copies,
        "eval_texts_in_train": leaked_texts,
        "train_text_conflicts": conflicts,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True)
                                      + "\n", encoding="utf-8")
    return manifest


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--variants", type=int, default=8)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)
    manifest = build(args.out, args.variants, args.seed)
    print("command dataset: %s" % ", ".join(
        "%s %d" % (name, count) for name, count in manifest["counts"].items()))
    print("intents:", manifest["intents"]["train"])
    print("eval-entity leaks in train:", manifest["eval_entity_leaks_in_train"] or "none")
    print("train rows skipped for eval text:",
          manifest["train_rows_skipped_for_eval_text"])
    print("eval texts found in train:", manifest["eval_texts_in_train"] or "none")
    print("train text conflicts:", manifest["train_text_conflicts"] or "none")
    bad = (manifest["eval_entity_leaks_in_train"]
           or manifest["eval_texts_in_train"]
           or manifest["train_text_conflicts"])
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
