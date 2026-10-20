"""Large, generated held-out benchmark for the Dofus THX-01 policy.

The benchmark is deliberately about the *skill* (intent -> tool + typed arguments),
not about memorised game knowledge: templates are new, and half of the item /
knowledge / farming cases use invented names that are absent from the cache.

Usage (from ``dofus-network``)::

    python ai/thx_bench_large.py --model <checkpoint-or-repo> --out bench.json
    python ai/thx_bench_large.py --generate-only --cases-out cases.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai.thx_policy import TOOL_QUESTION, ThxPolicy, _guard_tool
from agent.schema import validate_call
from agent.tool_eval import evaluate

CACHE = REPO_ROOT / "data" / "cache"
LANGS = ("en", "fr", "es", "de")
TOOLS = (
    "chat.say", "farm.start", "farm.stop", "fight.join", "fight.pass",
    "hdv.list", "hdv.scan", "inv.list", "inv.sell", "knowledge.search",
    "nav.follow", "nav.goto", "quest.advance", "quest.list", "safe.stop",
    "state.snapshot",
)

# ---------------------------------------------------------------------------
# language material
# ---------------------------------------------------------------------------

SUFFIXES = {
    "en": ["", " now", " please", " immediately", " right now"],
    "fr": ["", " maintenant", " s'il te plaît", " tout de suite", " maintenant s'il te plaît"],
    "es": ["", " ahora", " por favor", " ahora mismo", " por favor ahora"],
    "de": ["", " jetzt", " bitte", " sofort", " jetzt bitte"],
}

TEMPLATES: Dict[str, Dict[str, List[str]]] = {
    "inv.sell": {
        "en": ["sell {qty} {item}", "sell {item}", "sell {qty} {item} for {price} kamas",
               "sell {item} from my inventory"],
        "fr": ["vends {qty} {item}", "vends {item}", "vends {qty} {item} pour {price} kamas",
               "vends {item} de mon inventaire"],
        "es": ["vende {qty} {item}", "vende {item}", "vende {qty} {item} por {price} kamas",
               "vende {item} de mi inventario"],
        "de": ["verkaufe {qty} {item}", "verkaufe {item}", "verkaufe {qty} {item} für {price} Kamas",
               "verkaufe {item} aus meinem Inventar"],
    },
    "hdv.scan": {
        "en": ["scan {item}", "what is the price of {item}?", "check the market for {item}",
               "how much does {item} cost?"],
        "fr": ["scanne {item}", "quel est le prix de {item} ?", "vérifie le marché pour {item}",
               "combien coûte {item} ?"],
        "es": ["escanea {item}", "¿cuál es el precio de {item}?", "consulta el mercado para {item}",
               "¿cuánto cuesta {item}?"],
        "de": ["scanne {item}", "was kostet {item}?", "prüfe den Markt für {item}",
               "wie viel kostet {item}?"],
    },
    "hdv.list": {
        "en": ["list {qty} {item} at {price} kamas", "put {qty} {item} on the market for {price} kamas",
               "offer {qty} {item} for {price} kamas", "put {item} up for sale at {price} kamas"],
        "fr": ["mets {qty} {item} en vente à {price} kamas",
               "mets {qty} {item} sur le marché pour {price} kamas",
               "propose {qty} {item} pour {price} kamas", "mets {item} en vente à {price} kamas"],
        "es": ["pon {qty} {item} en venta por {price} kamas",
               "pon {qty} {item} en el mercado por {price} kamas",
               "ofrece {qty} {item} por {price} kamas", "pon {item} a la venta por {price} kamas"],
        "de": ["biete {qty} {item} für {price} Kamas an",
               "setze {qty} {item} für {price} Kamas auf den Markt",
               "verkaufe {qty} {item} auf dem Markt für {price} Kamas",
               "biete {item} für {price} Kamas zum Verkauf an"],
    },
    "knowledge.search": {
        "en": ["what is {entity}?", "look up {entity}", "tell me about {entity}", "who is {entity}?"],
        "fr": ["qu'est-ce que {entity} ?", "cherche {entity}", "parle-moi de {entity}",
               "qui est {entity} ?"],
        "es": ["¿qué es {entity}?", "busca {entity}", "háblame de {entity}", "¿quién es {entity}?"],
        "de": ["was ist {entity}?", "suche {entity}", "erzähl mir von {entity}", "wer ist {entity}?"],
    },
    "farm.start": {
        "en": ["farm {monster}", "start farming {monster}", "hunt {monster}", "go farm {monster}"],
        "fr": ["farm {monster}", "commence à farmer {monster}", "chasse {monster}",
               "va farmer {monster}"],
        "es": ["farmea {monster}", "empieza a farmear {monster}", "caza {monster}",
               "ve a farmear {monster}"],
        "de": ["farme {monster}", "fang an {monster} zu farmen", "jage {monster}",
               "geh {monster} farmen"],
    },
    "farm.stop": {
        "en": ["stop farming", "stop the farm", "end the farm loop", "finish farming"],
        "fr": ["arrête de farmer", "stop le farm", "termine la boucle de farm", "finis le farm"],
        "es": ["para de farmear", "detén el farmeo", "termina el bucle de farmeo", "fin del farmeo"],
        "de": ["hör auf zu farmen", "stoppe den Farm", "beende die Farm-Schleife", "beende das Farmen"],
    },
    "fight.join": {
        "en": ["join the fight", "enter the fight", "join combat", "help in this fight"],
        "fr": ["rejoins le combat", "entre dans le combat", "participe au combat",
               "aide dans ce combat"],
        "es": ["únete al combate", "entra en el combate", "participa en el combate",
               "ayuda en esta pelea"],
        "de": ["tritt dem Kampf bei", "geh in den Kampf", "nimm am Kampf teil",
               "hilf in diesem Kampf"],
    },
    "fight.pass": {
        "en": ["pass the turn", "pass my turn", "skip this turn", "wait for the next turn"],
        "fr": ["passe le tour", "passe mon tour", "saute ce tour", "attends le prochain tour"],
        "es": ["pasa el turno", "pasa mi turno", "salta este turno", "espera el siguiente turno"],
        "de": ["gib den Zug weiter", "überspringe meinen Zug", "warte auf den nächsten Zug",
               "lass den Zug aus"],
    },
    "inv.list": {
        "en": ["list my inventory", "what am I carrying?", "show my items", "what do I have?"],
        "fr": ["liste mon inventaire", "qu'est-ce que je porte ?", "montre mes objets",
               "qu'ai-je ?"],
        "es": ["lista mi inventario", "¿qué llevo?", "muestra mis objetos", "¿qué tengo?"],
        "de": ["liste mein Inventar", "was trage ich?", "zeige meine Gegenstände",
               "was habe ich?"],
    },
    "nav.follow": {
        "en": ["follow {npc}", "follow {npc} closely", "stay with {npc}", "keep following {npc}"],
        "fr": ["suis {npc}", "suis {npc} de près", "reste avec {npc}", "continue de suivre {npc}"],
        "es": ["sigue a {npc}", "sigue a {npc} de cerca", "quédate con {npc}",
               "continúa siguiendo a {npc}"],
        "de": ["folge {npc}", "folge {npc} dicht", "bleib bei {npc}", "folge {npc} weiter"],
    },
    "nav.goto": {
        "en": ["go to [{x}, {y}]", "walk to [{x}, {y}]", "take me to [{x}, {y}]",
               "move to [{x}, {y}]"],
        "fr": ["va à [{x}, {y}]", "marche vers [{x}, {y}]", "emmène-moi à [{x}, {y}]",
               "déplace-toi à [{x}, {y}]"],
        "es": ["ve a [{x}, {y}]", "camina a [{x}, {y}]", "llévame a [{x}, {y}]",
               "muévete a [{x}, {y}]"],
        "de": ["geh zu [{x}, {y}]", "laufe zu [{x}, {y}]", "bring mich zu [{x}, {y}]",
               "bewege dich zu [{x}, {y}]"],
    },
    "quest.advance": {
        "en": ["advance quest {quest_id}", "progress quest {quest_id}",
               "continue quest {quest_id}", "work on quest {quest_id}"],
        "fr": ["avance la quête {quest_id}", "progresse dans la quête {quest_id}",
               "continue la quête {quest_id}", "travaille sur la quête {quest_id}"],
        "es": ["avanza la misión {quest_id}", "progresa en la misión {quest_id}",
               "continúa la misión {quest_id}", "trabaja en la misión {quest_id}"],
        "de": ["mache mit Quest {quest_id} weiter", "komme bei Quest {quest_id} voran",
               "setze Quest {quest_id} fort", "arbeite an Quest {quest_id}"],
    },
    "quest.list": {
        "en": ["list quests in {area}", "quests in {area}", "show quests around {area}",
               "dungeon quests in {area}"],
        "fr": ["liste les quêtes à {area}", "quêtes à {area}",
               "montre les quêtes autour de {area}", "quêtes de donjon à {area}"],
        "es": ["lista las misiones en {area}", "misiones en {area}",
               "muestra las misiones cerca de {area}", "misiones de mazmorra en {area}"],
        "de": ["liste die Quests in {area}", "Quests in {area}",
               "zeige Quests in der Nähe von {area}", "Dungeon-Quests in {area}"],
    },
    "safe.stop": {
        "en": ["stop everything", "emergency stop", "halt all actions", "abort the bot"],
        "fr": ["arrête tout", "arrêt d'urgence", "coupe toutes les actions", "abandonne le bot"],
        "es": ["para todo", "parada de emergencia", "detén todas las acciones", "abandona el bot"],
        "de": ["stoppe alles", "Notstopp", "brich alle Aktionen ab", "beende den Bot"],
    },
    "state.snapshot": {
        "en": ["where am I?", "what is my position?", "show my current map",
               "where is my character?"],
        "fr": ["où suis-je ?", "quelle est ma position ?", "montre ma carte actuelle",
               "où est mon personnage ?"],
        "es": ["¿dónde estoy?", "¿cuál es mi posición?", "muestra mi mapa actual",
               "¿dónde está mi personaje?"],
        "de": ["wo bin ich?", "was ist meine Position?", "zeige meine aktuelle Karte",
               "wo ist mein Charakter?"],
    },
    "chat.say": {
        "en": ["tell the team: {text}", "say {text}", "message the guild: {text}",
               "inform the group: {text}"],
        "fr": ["dis à l'équipe : {text}", "dis {text}",
               "envoie un message à la guilde : {text}", "informe le groupe : {text}"],
        "es": ["dile al equipo: {text}", "di {text}", "envía un mensaje al gremio: {text}",
               "informa al grupo: {text}"],
        "de": ["sag dem Team: {text}", "sag {text}",
               "schicke eine Nachricht an die Gilde: {text}", "informiere die Gruppe: {text}"],
    },
}

CHAT_TEXT = {
    "en": ["ready", "on my way", "need help", "wait for me", "attack now"],
    "fr": ["prêt", "j'arrive", "besoin d'aide", "attends-moi", "attaque maintenant"],
    "es": ["listo", "voy en camino", "necesito ayuda", "espérame", "ataca ahora"],
    "de": ["bereit", "bin unterwegs", "brauche Hilfe", "warte auf mich", "greif jetzt an"],
}

COORDS = [(x, y) for x, y in (
    (5, -9), (-12, 4), (18, 21), (-7, -16), (30, 2), (-25, 14), (9, -3), (16, -22),
    (-4, 27), (11, 11), (-19, -8), (23, 6), (-14, 19), (2, -28), (26, -11), (-30, 9),
    (7, 24), (-8, -2), (20, 17), (-16, -19),
)]

QUEST_IDS = [1940, 1696, 18, 55, 101, 202, 333, 414, 505, 606, 707, 808, 909, 1001,
             1112, 1213, 1314, 1415, 1516, 1617]

FAKE_SYLLABLES = [
    "Zor", "Vex", "Quor", "Bla", "Tor", "Fen", "Rhu", "Drak", "Kri", "Shar",
    "Mor", "Omi", "Yin", "Vash", "Ulm", "Nim", "Pex", "Gor", "Lun", "Xel",
    "Fro", "Bri", "Cae", "Dun", "Esk", "Gal", "Hel", "Iri", "Jor", "Kel",
]

FAKE_SUFFIX = {
    "item": {
        "en": ["Wool", "Ore", "Shard", "Fiber", "Essence"],
        "fr": ["Laine", "Minerai", "Éclat", "Fibre", "Essence"],
        "es": ["Lana", "Mineral", "Fragmento", "Fibra", "Esencia"],
        "de": ["Wolle", "Erz", "Splitter", "Faser", "Essenz"],
    },
    "monster": {
        "en": ["Goblin", "Beast", "Larva", "Spirit", "Guard"],
        "fr": ["Gobelin", "Bête", "Larve", "Esprit", "Garde"],
        "es": ["Goblin", "Bestia", "Larva", "Espíritu", "Guardián"],
        "de": ["Goblin", "Bestie", "Larve", "Geist", "Wächter"],
    },
    "npc": {
        "en": ["Merchant", "Guard", "Guide", "Elder", "Scout"],
        "fr": ["Marchand", "Garde", "Guide", "Ancien", "Éclaireur"],
        "es": ["Mercader", "Guardián", "Guía", "Anciano", "Explorador"],
        "de": ["Händler", "Wächter", "Führer", "Ältester", "Späher"],
    },
    "area": {
        "en": ["Island", "Forest", "Desert", "Valley", "Coast"],
        "fr": ["Île", "Forêt", "Désert", "Vallée", "Côte"],
        "es": ["Isla", "Bosque", "Desierto", "Valle", "Costa"],
        "de": ["Insel", "Wald", "Wüste", "Tal", "Küste"],
    },
    "dungeon": {
        "en": ["Dungeon", "Lair", "Crypt", "Sanctum", "Ruin"],
        "fr": ["Donjon", "Antre", "Crypte", "Sanctuaire", "Ruine"],
        "es": ["Mazmorra", "Guarida", "Cripta", "Santuario", "Ruina"],
        "de": ["Dungeon", "Höhle", "Gruft", "Heiligtum", "Ruine"],
    },
}


def _load(name: str) -> Any:
    return json.loads((CACHE / name).read_text(encoding="utf-8"))


def _name(record: Dict[str, Any], lang: str, fallback: str = "fr") -> Optional[str]:
    value = record.get("name")
    if isinstance(value, dict):
        name = value.get(lang) or value.get(fallback) or value.get("en")
        if isinstance(name, str) and name.strip():
            return name.strip()
    for key in (f"name_{lang}", f"name_{fallback}", "name_en", "name_fr", "name_es"):
        name = record.get(key)
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def _unique(values: Iterable[str], limit: int) -> List[str]:
    seen = set()
    result = []
    for value in values:
        norm = re.sub(r"\s+", " ", value).strip()
        key = norm.casefold()
        if len(norm) < 3 or len(norm) > 48 or key in seen:
            continue
        if any(ch in norm for ch in "{}[]<>|\\"):
            continue
        seen.add(key)
        result.append(norm)
        if len(result) >= limit:
            break
    return result


def _fake_pool(kind: str, lang: str, limit: int = 20) -> List[str]:
    suffix = FAKE_SUFFIX[kind][lang]
    result = []
    for i, syllable in enumerate(FAKE_SYLLABLES):
        word = f"{syllable}{FAKE_SYLLABLES[(i + 7) % len(FAKE_SYLLABLES)].lower()}"
        tail = suffix[i % len(suffix)]
        if kind == "item":
            name = f"{word} {tail}"
        elif kind == "monster":
            name = f"{word} {tail}"
        elif kind == "npc":
            name = f"{word} the {tail}" if lang == "en" else f"{word} {tail}"
        elif kind == "area":
            name = f"{tail} of {word}" if lang == "en" else f"{tail} de {word}"
        else:
            name = f"{tail} of {word}" if lang == "en" else f"{tail} de {word}"
        result.append(name)
        if len(result) >= limit:
            break
    return result


def _pools() -> Dict[str, Dict[str, List[str]]]:
    items = _load("items_full.json")
    monsters = _load("monsters_full.json")
    npcs = _load("npcs_full.json")
    areas = _load("areas_full.json")
    dungeons = _load("dungeons_full.json")

    # deterministic spread through the cache, not just the first few records
    item_records = [r for r in items if isinstance(r, dict) and r.get("is_saleable", True)]
    item_records = item_records[:: max(1, len(item_records) // 400)]
    monster_records = monsters[:: max(1, len(monsters) // 200)]
    npc_records = npcs[:: max(1, len(npcs) // 200)]
    area_records = areas[:: max(1, len(areas) // 50)]
    dungeon_records = dungeons[:: max(1, len(dungeons) // 50)]

    pools: Dict[str, Dict[str, List[str]]] = {}
    for lang in LANGS:
        items_l = _unique((_name(r, lang) for r in item_records), 20)
        monsters_l = _unique((_name(r, lang, "fr") for r in monster_records), 20)
        npcs_l = _unique((_name(r, lang) for r in npc_records), 20)
        areas_l = _unique((_name(r, lang) for r in area_records), 20)
        dungeons_l = _unique((_name(r, lang) for r in dungeon_records), 20)
        pools[lang] = {
            "item": items_l + _fake_pool("item", lang, 20),
            "monster": monsters_l + _fake_pool("monster", lang, 20),
            "npc": npcs_l + _fake_pool("npc", lang, 20),
            "area": areas_l,
            "dungeon": dungeons_l + _fake_pool("dungeon", lang, 20),
        }
        # keep generated names unique and cap at 40 per kind
        for kind in pools[lang]:
            pools[lang][kind] = _unique(pools[lang][kind], 40)
    return pools


def _is_fake(value: str, pools: Dict[str, Dict[str, List[str]]], kind: str, lang: str) -> bool:
    return value in set(_fake_pool(kind, lang, 20))


def _case(index: int, lang: str, tool: str, goal: str, args: Dict[str, Any],
          family: str, synthetic: bool) -> Dict[str, Any]:
    return {
        "id": f"{lang}-{tool.replace('.', '-')}-{index:03d}",
        "lang": lang,
        "tool": tool,
        "goal": goal,
        "args": args,
        "family": family,
        "synthetic_entity": synthetic,
    }


def generate() -> List[Dict[str, Any]]:
    pools = _pools()
    cases: List[Dict[str, Any]] = []
    price_values = [350, 800, 1200, 2400, 5000]
    qty_values = [1, 2, 4, 8, 12]
    for tool in TOOLS:
        for lang in LANGS:
            templates = TEMPLATES[tool][lang]
            suffixes = SUFFIXES[lang]
            for ti, template in enumerate(templates):
                for vi in range(5):
                    idx = len(cases)
                    synthetic = False
                    if "{entity}" in template:
                        kind = ("npc", "monster", "item", "dungeon")[ti]
                        value = pools[lang][kind][(ti * 5 + vi) % 20]
                        synthetic = _is_fake(value, pools, kind, lang)
                        goal = template.format(entity=value)
                        args = {} if synthetic else {"query": value}
                    elif "{item}" in template:
                        kind = "item"
                        value = pools[lang][kind][(ti * 5 + vi) % 20]
                        synthetic = _is_fake(value, pools, kind, lang)
                        qty = qty_values[(ti + vi) % len(qty_values)]
                        price = price_values[(ti + vi) % len(price_values)]
                        goal = template.format(item=value, qty=qty, price=price)
                        if tool == "inv.sell":
                            args = {"quantity": qty} if synthetic else {
                                "item": value, "quantity": qty}
                            if "{price}" in template:
                                args.update({"price_rule": "fixed", "price": price})
                            if "{qty}" not in template:
                                args.pop("quantity", None)
                                args["all"] = True
                        elif tool == "hdv.scan":
                            args = {} if synthetic else {"item": value}
                        else:
                            args = {"quantity": qty, "price_rule": "fixed", "price": price}
                            if not synthetic:
                                args["item"] = value
                            if "{qty}" not in template:
                                args.pop("quantity", None)
                    elif "{monster}" in template:
                        kind = "monster"
                        value = pools[lang][kind][(ti * 5 + vi) % 20]
                        synthetic = _is_fake(value, pools, kind, lang)
                        goal = template.format(monster=value)
                        args = {}
                    elif "{npc}" in template:
                        kind = "npc"
                        value = pools[lang][kind][(ti * 5 + vi) % 20]
                        synthetic = _is_fake(value, pools, kind, lang)
                        goal = template.format(npc=value)
                        args = {}
                    elif "{area}" in template:
                        kind = "area"
                        value = pools[lang][kind][(ti * 5 + vi) % 20]
                        goal = template.format(area=value)
                        args = {"area": value}
                    elif "{quest_id}" in template:
                        qid = QUEST_IDS[(ti * 5 + vi) % len(QUEST_IDS)]
                        goal = template.format(quest_id=qid)
                        args = {"quest_id": qid}
                    elif "{x}" in template:
                        x, y = COORDS[(ti * 5 + vi) % len(COORDS)]
                        goal = template.format(x=x, y=y)
                        args = {"x": x, "y": y}
                    elif "{text}" in template:
                        text = CHAT_TEXT[lang][vi]
                        goal = template.format(text=text)
                        args = {"text": text}
                    else:
                        goal = template.format()
                        suffix = suffixes[vi].strip()
                        if suffix:
                            goal = f"{goal.rstrip(' ?!')} {suffix}"
                            if "?" in template:
                                goal += "?"
                        args = {}
                    cases.append(_case(idx, lang, tool, goal, args,
                                       family=f"{tool}:{ti}", synthetic=synthetic))
    return cases


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="1,280-case held-out THX-01 benchmark")
    parser.add_argument("--model", default=None, help="THX-01 checkpoint or repo id")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--limit", type=int, default=0, help="evaluate only the first N cases")
    parser.add_argument("--generate-only", action="store_true")
    parser.add_argument("--cases-out", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--show-failures", type=int, default=0,
                        help="print at most N failures (0 = none)")
    args = parser.parse_args(argv)

    cases = generate()
    if args.limit:
        cases = cases[: args.limit]
    print(f"generated {len(cases)} cases "
          f"({len(set(c['goal'] for c in cases))} unique goals)")
    if args.cases_out:
        path = Path(args.cases_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cases) + "\n",
                        encoding="utf-8")
        print(f"cases: {path}")
    if args.generate_only:
        return 0

    policy = ThxPolicy(model=args.model, device=args.device)
    agent = policy._load()
    started = time.perf_counter()
    answers = agent.predict_batch([c["goal"] for c in cases], TOOL_QUESTION,
                                  batch_size=args.batch_size)
    elapsed = time.perf_counter() - started

    raw: Dict[str, Any] = {}
    hybrid: Dict[str, Any] = {}
    guard_count = 0
    for case, result in zip(cases, answers):
        tool_answer = (result.get("answers") or {}).get("tool") or {}
        raw_tool = tool_answer.get("choice")
        guard = _guard_tool(case["goal"])
        if guard:
            guard_count += 1
        hybrid_tool = guard or raw_tool

        def _args(tool: Optional[str]) -> Dict[str, Any]:
            if not tool or not case.get("args"):
                return {}
            try:
                args, _ = policy._arguments(tool, case["goal"])
                ok, coerced = validate_call(tool, args)
                return coerced if ok else {}
            except Exception:
                return {}

        raw[case["id"]] = {"tool": raw_tool, "args": _args(raw_tool)}
        hybrid[case["id"]] = {"tool": hybrid_tool, "args": _args(hybrid_tool)}

    raw_report = evaluate(raw, cases)
    hybrid_report = evaluate(hybrid, cases)
    for name, report in (("raw THX-01", raw_report), ("hybrid guard+THX", hybrid_report)):
        totals = report["totals"]
        print(f"\n{name}:")
        print(f"  tool {totals['tool_ok']}/{totals['cases']} "
              f"({100 * totals['tool_ok'] / max(totals['cases'], 1):.1f}%)  "
              f"call {totals['call_ok']}/{totals['cases']} "
              f"({100 * totals['call_ok'] / max(totals['cases'], 1):.1f}%)")
        for lang, stats in report["by_lang"].items():
            print(f"  [{lang}] tool {stats['tool_ok']}/{stats['cases']}  "
                  f"call {stats['call_ok']}/{stats['cases']}")
    print(f"\nguard coverage: {guard_count}/{len(cases)}")
    print(f"model batch: {elapsed:.1f}s ({elapsed / max(len(cases), 1):.2f}s/case)")
    if args.show_failures:
        for report_name, report in (("raw", raw_report), ("hybrid", hybrid_report)):
            print(f"\n{report_name} failures (first {args.show_failures}):")
            for failure in report["failures"][: args.show_failures]:
                print(f"  {failure['id']}: {failure['goal']}")
                print(f"    expected {failure['expected']}  got {failure['predicted']}")
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "model": args.model,
            "cases": len(cases),
            "guard_coverage": guard_count,
            "model_seconds": round(elapsed, 2),
            "raw": raw_report,
            "hybrid": hybrid_report,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
