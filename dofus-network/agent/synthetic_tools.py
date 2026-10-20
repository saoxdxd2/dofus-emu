"""Synthetic full-tool demonstration generator.

The local collector (``agent/episodes.py``) covers the three tools that run
without a game client. This module fills the rest of the action space with
constructed, schema-valid demonstrations so the student sees every tool before
the first live session: goals in EN/FR/ES/DE, labelled with the exact call the
template encodes, using real cache entities plus novel names that force the
model to copy values from the goal instead of memorising the cache.

Honesty rules:

* every label is validated with ``schema.validate_call`` before it is written;
* the record is marked ``source="synthetic"`` and ``result.constructed=true`` —
  these calls were *not* executed against the client;
* ``tool_dataset.py`` keeps them out of the SFT corpus unless explicitly asked
  for (``--sources scripted,live,synthetic``), so verified and constructed data
  never mix by accident.

Usage (from ``dofus-network``)::

    python agent/synthetic_tools.py --count 1500
    python agent/synthetic_tools.py --count 200 --dry-run
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.episodes import (  # noqa: E402
    LANGS, _NAV_COORD_TEMPLATES, _NAV_ZAAP_TEMPLATES, _QUEST_TEMPLATES,
    _Sampler, _names, _usable,
)
from agent.executor import ToolExecutor  # noqa: E402
from agent.recorder import TrajectoryRecorder  # noqa: E402
from agent.schema import validate_call  # noqa: E402

SYNTHETIC_TOOLS = (
    "safe.stop", "nav.goto", "nav.follow", "quest.list", "farm.start",
    "farm.stop", "fight.join", "fight.pass", "inv.list", "inv.sell",
    "hdv.scan", "hdv.list", "chat.say", "state.snapshot", "quest.advance",
)

_WEIGHTS = {
    "safe.stop": 4, "nav.goto": 10, "nav.follow": 4, "quest.list": 12,
    "farm.start": 12, "farm.stop": 4, "fight.join": 6, "fight.pass": 4,
    "inv.list": 8, "inv.sell": 12, "hdv.scan": 10, "hdv.list": 8,
    "chat.say": 8, "state.snapshot": 4, "quest.advance": 12,
}

_ZAAP_NAMES = (
    "Astrub", "Bonta", "Brakmar", "Amakna", "Sufokia", "Frigost",
    "Otomai", "Pandala", "Cania", "Sidimote", "Roches", "Breeder",
)

_NOVEL_SYLLABLES = (
    "zor", "bla", "kri", "mo", "tal", "vex", "ni", "quor", "shar", "lun",
    "fen", "drak", "omi", "pix", "rhu", "ski", "tor", "ulm", "vash", "yin",
)

_KIND_WORDS: Dict[str, Dict[str, str]] = {
    "en": {"all": "everything", "resources": "resources", "equipment": "equipment",
           "consumables": "consumables", "quest_items": "quest items"},
    "fr": {"all": "tout", "resources": "ressources", "equipment": "équipement",
           "consumables": "consommables", "quest_items": "objets de quête"},
    "es": {"all": "todo", "resources": "recursos", "equipment": "equipo",
           "consumables": "consumibles", "quest_items": "objetos de misión"},
    "de": {"all": "alles", "resources": "Ressourcen", "equipment": "Ausrüstung",
           "consumables": "Verbrauchsgüter", "quest_items": "Questgegenstände"},
}

_TEMPLATES: Dict[str, Dict[str, List[str]]] = {
    "safe.stop": {
        "en": ["stop everything now", "halt, {reason}!", "emergency stop: {reason}"],
        "fr": ["arrête tout", "stop, {reason} !", "arrêt d'urgence : {reason}"],
        "es": ["para todo", "¡detente, {reason}!", "parada de emergencia: {reason}"],
        "de": ["halt alles", "stopp, {reason}!", "Notstopp: {reason}"],
    },
    "nav.follow": {
        "en": ["follow me", "stay on me", "follow the order giver"],
        "fr": ["suis-moi", "reste avec moi", "suis le donneur d'ordre"],
        "es": ["sígueme", "quédate conmigo", "sigue al que da la orden"],
        "de": ["folge mir", "bleib bei mir", "folge dem Auftraggeber"],
    },
    "farm.start": {
        "en": ["farm {target}", "farm {target} for {kills} kills",
               "farm {target} in {zone}"],
        "fr": ["farm les {target}", "farm les {target} pendant {kills} kills",
               "farm les {target} dans {zone}"],
        "es": ["farmea {target}", "farmea {target} durante {kills} muertes",
               "farmea {target} en {zone}"],
        "de": ["farme {target}", "farme {target} für {kills} Kills",
               "farme {target} in {zone}"],
    },
    "farm.stop": {
        "en": ["stop the farm loop", "end the farming session", "stop farming"],
        "fr": ["arrête la boucle de farm", "termine la session de farm", "arrête de farmer"],
        "es": ["detén el bucle de farmeo", "termina la sesión de farmeo", "deja de farmear"],
        "de": ["beende die Farm-Schleife", "beende die Farm-Sitzung", "hör auf zu farmen"],
    },
    "fight.join": {
        "en": ["join the fight", "join the fight against {target}", "enter combat"],
        "fr": ["rejoins le combat", "rejoins le combat contre {target}", "entre en combat"],
        "es": ["únete al combate", "únete al combate contra {target}", "entra en combate"],
        "de": ["Kampf beitreten", "tritt dem Kampf gegen {target} bei", "betrete den Kampf"],
    },
    "fight.pass": {
        "en": ["pass the turn", "pass", "skip my turn"],
        "fr": ["passe le tour", "passe", "saute mon tour"],
        "es": ["pasa el turno", "pasa", "salta mi turno"],
        "de": ["passe den Zug", "pass", "überspringe meinen Zug"],
    },
    "inv.list": {
        "en": ["show my {kind}", "list my {kind}", "what is in my {kind}?"],
        "fr": ["montre mon {kind}", "liste mon {kind}", "qu'y a-t-il dans mon {kind} ?"],
        "es": ["muestra mi {kind}", "lista mi {kind}", "¿qué hay en mi {kind}?"],
        "de": ["zeig mein {kind}", "liste mein {kind}", "was ist in meinem {kind}?"],
    },
    "inv.sell": {
        "en": ["sell {target}", "sell {qty} {target} at {price} kamas",
               "sell all my resources at market price -1"],
        "fr": ["vends {target}", "vends {qty} {target} à {price} kamas",
               "vends toutes mes ressources au prix du marché -1"],
        "es": ["vende {target}", "vende {qty} {target} a {price} kamas",
               "vende todos mis recursos al precio de mercado -1"],
        "de": ["verkaufe {target}", "verkaufe {qty} {target} für {price} Kamas",
               "verkaufe alle meine Ressourcen zum Marktpreis -1"],
    },
    "hdv.scan": {
        "en": ["scan the price of {target}", "what is the price of {target}?",
               "check the market for {target}"],
        "fr": ["scan le prix de {target}", "quel est le prix de {target} ?",
               "vérifie le marché pour {target}"],
        "es": ["escanea el precio de {target}", "¿cuál es el precio de {target}?",
               "consulta el mercado para {target}"],
        "de": ["scanne den Preis von {target}", "was kostet {target}?",
               "prüfe den Markt für {target}"],
    },
    "hdv.list": {
        "en": ["list {qty} {target} for {price} kamas", "put {target} on the market",
               "sell {qty} {target} on the market at market price -1"],
        "fr": ["mets {qty} {target} en vente à {price} kamas",
               "mets {target} sur le marché",
               "vends {qty} {target} au marché au prix du marché -1"],
        "es": ["pon {qty} {target} en venta por {price} kamas",
               "pon {target} en el mercado",
               "vende {qty} {target} en el mercado al precio de mercado -1"],
        "de": ["biete {qty} {target} für {price} Kamas an",
               "stelle {target} auf den Markt",
               "verkaufe {qty} {target} auf dem Markt zum Marktpreis -1"],
    },
    "chat.say": {
        "en": ["say {text} in {channel} chat", "send {text} to {channel}",
               "tell the {channel} {text}"],
        "fr": ["dis {text} dans le chat {channel}", "envoie {text} à {channel}",
               "dis {text} au {channel}"],
        "es": ["di {text} en el chat {channel}", "envía {text} a {channel}",
               "di {text} al {channel}"],
        "de": ["sag {text} im {channel}", "sende {text} an {channel}",
               "schreib {text} in den {channel}"],
    },
    "state.snapshot": {
        "en": ["where am I?", "snapshot my state", "give me my current state"],
        "fr": ["où suis-je ?", "fais un snapshot de mon état", "donne-moi mon état actuel"],
        "es": ["¿dónde estoy?", "haz una captura de mi estado", "dame mi estado actual"],
        "de": ["wo bin ich?", "mach eine Zustandsaufnahme", "gib mir meinen aktuellen Zustand"],
    },
    "quest.advance": {
        "en": ["advance quest {target}", "continue quest {target}",
               "do the next step of quest {target}"],
        "fr": ["avance la quête {target}", "continue la quête {target}",
               "fais l'étape suivante de la quête {target}"],
        "es": ["avanza la misión {target}", "continúa la misión {target}",
               "haz el siguiente paso de la misión {target}"],
        "de": ["avanciere Quest {target}", "mach mit Quest {target} weiter",
               "erledige den nächsten Schritt von Quest {target}"],
    },
}

_CHANNEL_WORDS: Dict[str, Dict[str, str]] = {
    "en": {"say": "local", "team": "team", "guild": "guild"},
    "fr": {"say": "local", "team": "équipe", "guild": "guilde"},
    "es": {"say": "local", "team": "equipo", "guild": "gremio"},
    "de": {"say": "lokal", "team": "Team", "guild": "Gilde"},
}

_REASONS = ("low hp", "it's dangerous", "out of potions", "too many monsters")
_CHAT_TEXT = (
    "hello", "bonjour", "hola", "Hallo", "gg", "thanks", "merci", "gracias",
    "danke", "on my way", "need help?", "ok", "wait",
)


def _novel_name(rng: random.Random, kind: str) -> str:
    parts = [rng.choice(_NOVEL_SYLLABLES) for _ in range(rng.randint(2, 3))]
    name = "".join(part.capitalize() for part in parts)
    if kind == "item":
        name = f"{name} {rng.choice(('Wool', 'Ore', 'Essence', 'Shard', 'Fiber'))}"
    elif kind == "monster":
        name = f"{name} {rng.choice(('Goblin', 'Toad', 'Boar', 'Sprite'))}"
    elif kind == "area":
        name = f"{name} Island"
    if rng.random() < 0.25:
        name += f" {rng.randint(2, 99)}"
    return name


def _entity(sampler: _Sampler, kind: str, lang: str, attempts: int = 20) -> Optional[str]:
    entries = sampler._entries(kind)
    for _ in range(attempts):
        if not entries:
            return None
        record = sampler.rng.choice(entries)[3]
        names = _names(record)
        name = names.get(lang) or names.get("en") or names.get("fr")
        if name and _usable(name):
            return name
    return None


def _entity_or_novel(
    sampler: _Sampler,
    kind: str,
    lang: str,
    novel_ratio: float = 0.4,
) -> Optional[str]:
    if sampler.rng.random() < novel_ratio:
        return _novel_name(sampler.rng, kind)
    return _entity(sampler, kind, lang)


def _area_name(sampler: _Sampler, lang: str) -> Optional[str]:
    if sampler.rng.random() < 0.4:
        return _novel_name(sampler.rng, "area")
    pool = sampler._geo("areas_full.json") if sampler._areas is None else sampler._areas
    if not pool:
        return None
    for _ in range(20):
        names = sampler.rng.choice(pool)
        name = names.get(lang) or names.get("en") or names.get("fr")
        if name and _usable(name):
            return name
    return None


def _build_safe_stop(sampler: _Sampler, lang: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    reason = sampler.rng.choice(_REASONS)
    template = sampler.rng.choice(_TEMPLATES["safe.stop"][lang])
    return template.format(reason=reason), {"reason": reason}


def _build_nav_goto(sampler: _Sampler, lang: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    rng = sampler.rng
    if rng.random() < 0.5:
        x, y = rng.randint(-45, 45), rng.randint(-45, 45)
        template = rng.choice(_NAV_COORD_TEMPLATES[lang])
        return template.format(x=x, y=y), {"x": x, "y": y}
    name = rng.choice(_ZAAP_NAMES)
    if rng.random() < 0.3:
        name = _novel_name(rng, "area")
    template = rng.choice(_NAV_ZAAP_TEMPLATES[lang])
    return template.format(name=name), {"zaap": name}


def _build_nav_follow(sampler: _Sampler, lang: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    return sampler.rng.choice(_TEMPLATES["nav.follow"][lang]), {"name": ""}


def _build_quest_list(sampler: _Sampler, lang: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    area = _area_name(sampler, lang)
    if not area:
        return None
    rng = sampler.rng
    variant = rng.randrange(5)
    level = rng.choice((10, 20, 30, 40, 50, 60, 80, 100, 120, 150))
    limit = rng.choice((3, 5, 10, 20, 50))
    goal = _QUEST_TEMPLATES[lang][variant].format(area=area, level=level, limit=limit)
    args: Dict[str, Any] = {"area": area}
    if variant == 1:
        args["level_min"] = level
    elif variant == 2:
        args["dungeon_only"] = True
    elif variant == 3:
        args["limit"] = limit
    return goal, args


def _build_farm_start(sampler: _Sampler, lang: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    variant = sampler.rng.random()
    if variant < 0.65:
        target = _entity_or_novel(sampler, "monster", lang)
        args: Dict[str, Any] = {"monster": target} if target else {}
    else:
        target = _entity_or_novel(sampler, "item", lang)
        args = {"resource": target} if target else {}
    if not target:
        return None
    template = sampler.rng.choice(_TEMPLATES["farm.start"][lang])
    kills = sampler.rng.choice((20, 50, 100, 200))
    zone = ""
    if "{zone}" in template:
        zone = _area_name(sampler, lang) or ""
        if not zone:
            return None
        args["zone"] = zone
    if "{kills}" in template:
        args["kill_count"] = kills
    return template.format(target=target, kills=kills, zone=zone), args


def _build_farm_stop(sampler: _Sampler, lang: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    return sampler.rng.choice(_TEMPLATES["farm.stop"][lang]), {}


def _build_fight_join(sampler: _Sampler, lang: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    template = sampler.rng.choice(_TEMPLATES["fight.join"][lang])
    if "{target}" in template:
        target = _entity_or_novel(sampler, "monster", lang)
        if not target:
            return None
        return template.format(target=target), {"target": target}
    return template, {}


def _build_fight_pass(sampler: _Sampler, lang: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    return sampler.rng.choice(_TEMPLATES["fight.pass"][lang]), {}


def _build_inv_list(sampler: _Sampler, lang: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    kind = sampler.rng.choice(("all", "resources", "equipment", "consumables", "quest_items"))
    template = sampler.rng.choice(_TEMPLATES["inv.list"][lang])
    goal = template.format(kind=_KIND_WORDS[lang][kind])
    return goal, {"kind": kind}


def _build_inv_sell(sampler: _Sampler, lang: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    template = sampler.rng.choice(_TEMPLATES["inv.sell"][lang])
    price = sampler.rng.choice((50, 100, 250, 500, 1000, 5000))
    qty = sampler.rng.choice((1, 5, 10, 20, 50))
    if "{target}" in template:
        target = _entity_or_novel(sampler, "item", lang)
        if not target:
            return None
        args: Dict[str, Any] = {"item": target, "quantity": qty}
        if "{price}" in template:
            args.update({"price_rule": "fixed", "price": price})
        else:
            args.update({"price_rule": "median_minus_1", "min_stock": 1})
        return template.format(target=target, qty=qty, price=price), args
    return template, {"all": True, "price_rule": "median_minus_1", "min_stock": 1}


def _build_hdv_scan(sampler: _Sampler, lang: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    target = _entity_or_novel(sampler, "item", lang)
    if not target:
        return None
    template = sampler.rng.choice(_TEMPLATES["hdv.scan"][lang])
    return template.format(target=target), {"item": target}


def _build_hdv_list(sampler: _Sampler, lang: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    target = _entity_or_novel(sampler, "item", lang)
    if not target:
        return None
    template = sampler.rng.choice(_TEMPLATES["hdv.list"][lang])
    qty = sampler.rng.choice((1, 5, 10, 20, 50))
    price = sampler.rng.choice((100, 250, 500, 1000, 5000, 10000))
    args: Dict[str, Any] = {"item": target, "quantity": qty}
    if "{price}" in template:
        args.update({"price_rule": "fixed", "price": price})
    else:
        args.update({"price_rule": "median_minus_1"})
    return template.format(target=target, qty=qty, price=price), args


def _build_chat_say(sampler: _Sampler, lang: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    channel = sampler.rng.choice(("say", "team", "guild"))
    text = sampler.rng.choice(_CHAT_TEXT)
    template = sampler.rng.choice(_TEMPLATES["chat.say"][lang])
    goal = template.format(text=text, channel=_CHANNEL_WORDS[lang][channel])
    return goal, {"text": text, "channel": channel}


def _build_state_snapshot(sampler: _Sampler, lang: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    return sampler.rng.choice(_TEMPLATES["state.snapshot"][lang]), {}


def _build_quest_advance(sampler: _Sampler, lang: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    rng = sampler.rng
    template = rng.choice(_TEMPLATES["quest.advance"][lang])
    if rng.random() < 0.5:
        quest_id = rng.randint(50, 9999)
        return template.format(target=quest_id), {"quest_id": quest_id}
    entries = sampler._entries("quest")
    if not entries:
        quest_id = rng.randint(50, 9999)
        return template.format(target=quest_id), {"quest_id": quest_id}
    for _ in range(20):
        record = rng.choice(entries)[3]
        quest_id = record.get("id")
        if not isinstance(quest_id, int):
            continue
        names = _names(record)
        target = names.get(lang) or names.get("en") or str(quest_id)
        if not _usable(str(target)):
            target = str(quest_id)
        return template.format(target=target), {"quest_id": quest_id}
    quest_id = rng.randint(50, 9999)
    return template.format(target=quest_id), {"quest_id": quest_id}


_BUILDERS: Dict[str, Callable[[_Sampler, str], Optional[Tuple[str, Dict[str, Any]]]]] = {
    "safe.stop": _build_safe_stop,
    "nav.goto": _build_nav_goto,
    "nav.follow": _build_nav_follow,
    "quest.list": _build_quest_list,
    "farm.start": _build_farm_start,
    "farm.stop": _build_farm_stop,
    "fight.join": _build_fight_join,
    "fight.pass": _build_fight_pass,
    "inv.list": _build_inv_list,
    "inv.sell": _build_inv_sell,
    "hdv.scan": _build_hdv_scan,
    "hdv.list": _build_hdv_list,
    "chat.say": _build_chat_say,
    "state.snapshot": _build_state_snapshot,
    "quest.advance": _build_quest_advance,
}


def generate(
    count: int = 1500,
    seed: int = 7,
    root: Path = REPO_ROOT / "trajectories",
    instance: str = "synthetic",
    dry_run: bool = False,
) -> Dict[str, Any]:
    rng = random.Random(seed)
    recorder = TrajectoryRecorder(root=root, instance=instance)
    executor = ToolExecutor()
    sampler = _Sampler(executor, rng)

    tools = list(SYNTHETIC_TOOLS)
    plan: List[Tuple[str, str]] = []
    for lang in LANGS:
        for tool in tools:
            plan.append((tool, lang))
    weights = [_WEIGHTS[tool] for tool in tools]
    while len(plan) < count:
        tool = rng.choices(tools, weights=weights, k=1)[0]
        plan.append((tool, rng.choice(LANGS)))
    rng.shuffle(plan)

    stats: Counter = Counter(total=0, ok=0, invalid=0, no_entity=0)
    by_tool: Counter = Counter()
    by_lang: Counter = Counter()
    seen: set[str] = set()

    for tool, lang in plan:
        if stats["total"] >= count:
            break
        built = _BUILDERS[tool](sampler, lang)
        if built is None:
            stats["no_entity"] += 1
            continue
        goal, args = built
        ok, validated = validate_call(tool, args)
        if not ok:
            stats["invalid"] += 1
            continue
        stats["total"] += 1
        key = f"{goal}\x1f{tool}\x1f{validated}"
        if key in seen:
            continue
        seen.add(key)
        stats["ok"] += 1
        by_tool[tool] += 1
        by_lang[lang] += 1
        if dry_run:
            continue
        recorder.step(
            tool=tool,
            args=validated,
            ok=True,
            goal=goal,
            source="synthetic",
            result={"constructed": True,
                    "note": "label derived from template; not executed against a client"},
        )

    summary = dict(stats)
    summary["by_tool"] = dict(sorted(by_tool.items()))
    summary["by_lang"] = dict(sorted(by_lang.items()))
    summary["path"] = str(recorder.path)
    summary["dry_run"] = dry_run
    return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic full-tool demonstrations")
    parser.add_argument("--count", type=int, default=1500)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--root", default=str(REPO_ROOT / "trajectories"))
    parser.add_argument("--instance", default="synthetic")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    summary = generate(
        count=args.count, seed=args.seed, root=Path(args.root),
        instance=args.instance, dry_run=args.dry_run)

    print(f"generated : {summary['total']} (unique {summary['ok']}, "
          f"invalid {summary['invalid']}, no entity {summary['no_entity']})")
    print(f"by tool   : {summary['by_tool']}")
    print(f"by lang   : {summary['by_lang']}")
    if not args.dry_run:
        print(f"trajectory: {summary['path']}")
        print("next      : python agent/tool_dataset.py --sources scripted,live,synthetic")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
