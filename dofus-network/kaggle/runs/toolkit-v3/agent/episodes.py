"""Scripted local episode collector.

Generates natural multi-language goals (EN/FR/ES/DE) from the synced DofusDB
cache, resolves each one to the *intended* tool call by construction, executes
it against the local backend, and records the real outcome through the normal
executor/recorder path.

This is how the tool-use corpus starts before any live session is available:
the goal text varies freely, while the label is exact (no rule-policy guessing),
so the resulting JSONL is honest SFT material. Local tools only — knowledge
search and quest listing — because they run without Sentinel or a client.

Usage (from ``dofus-network``)::

    python agent/episodes.py --count 400
    python agent/episodes.py --count 20 --dry-run
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.executor import ToolExecutor  # noqa: E402
from agent.recorder import TrajectoryRecorder  # noqa: E402

LANGS = ("en", "fr", "es", "de")

_KNOWLEDGE_TEMPLATES: Dict[str, List[str]] = {
    "en": [
        "look up {name}",
        "what is {name}?",
        "tell me about {name}",
        "search the codex for {name}",
    ],
    "fr": [
        "cherche {name}",
        "c'est quoi {name} ?",
        "donne-moi des infos sur {name}",
        "trouve {name} dans le codex",
    ],
    "es": [
        "busca {name}",
        "¿qué es {name}?",
        "dame información sobre {name}",
        "encuentra {name} en el códice",
    ],
    "de": [
        "suche {name}",
        "was ist {name}?",
        "gib mir Infos über {name}",
        "finde {name} im Kodex",
    ],
}

# One template per variant: plain, level, dungeon, limit, plain (different verb).
_QUEST_TEMPLATES: Dict[str, List[str]] = {
    "en": [
        "list quests in {area}",
        "quests for level {level} in {area}",
        "dungeon quests in {area}",
        "show me {limit} quests in {area}",
        "which quests can I do in {area}?",
    ],
    "fr": [
        "liste les quêtes de {area}",
        "quêtes de niveau {level} à {area}",
        "quêtes de donjon à {area}",
        "montre-moi {limit} quêtes à {area}",
        "quelles quêtes puis-je faire à {area} ?",
    ],
    "es": [
        "lista las misiones de {area}",
        "misiones de nivel {level} en {area}",
        "misiones de mazmorra en {area}",
        "muéstrame {limit} misiones en {area}",
        "¿qué misiones puedo hacer en {area}?",
    ],
    "de": [
        "liste die Quests in {area}",
        "Quests für Level {level} in {area}",
        "Dungeon-Quests in {area}",
        "zeige mir {limit} Quests in {area}",
        "welche Quests kann ich in {area} machen?",
    ],
}

_NAV_ZAAP_TEMPLATES: Dict[str, List[str]] = {
    "en": [
        "go to the {name} zaap",
        "take me to the {name} zaap",
        "zaap {name}",
        "navigate to the {name} zaap",
    ],
    "fr": [
        "va au zaap {name}",
        "prends le zaap {name}",
        "zaap {name}",
        "emmène-moi au zaap {name}",
    ],
    "es": [
        "ve al zaap {name}",
        "toma el zaap {name}",
        "zaap {name}",
        "llévame al zaap {name}",
    ],
    "de": [
        "geh zum Zaap {name}",
        "nimm den Zaap {name}",
        "zaap {name}",
        "bring mich zum Zaap {name}",
    ],
}

_NAV_COORD_TEMPLATES: Dict[str, List[str]] = {
    "en": ["go to [{x}, {y}]", "walk to [{x}, {y}]", "move to [{x}, {y}]"],
    "fr": ["va en [{x}, {y}]", "marche vers [{x}, {y}]", "déplace-toi en [{x}, {y}]"],
    "es": ["ve a [{x}, {y}]", "camina a [{x}, {y}]", "muévete a [{x}, {y}]"],
    "de": ["geh zu [{x}, {y}]", "laufe zu [{x}, {y}]", "bewege dich zu [{x}, {y}]"],
}

_KIND_WEIGHTS: Tuple[Tuple[str, int], ...] = (
    ("item", 30), ("monster", 30), ("npc", 15), ("quest", 10),
    ("dungeon", 10), ("achievement", 5),
)

_LEVELS = (10, 20, 30, 40, 50, 60, 80, 100, 120, 150, 180)
_LIMITS = (3, 5, 10, 20, 50)


def _names(record: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    name = record.get("name")
    if isinstance(name, dict):
        for lang in LANGS:
            value = name.get(lang)
            if isinstance(value, str) and value.strip():
                out[lang] = " ".join(value.split())
    for lang in LANGS:
        value = record.get(f"name_{lang}")
        if lang not in out and isinstance(value, str) and value.strip():
            out[lang] = " ".join(value.split())
    return out


def _usable(name: str) -> bool:
    if not name or len(name) < 3 or len(name) > 64:
        return False
    if any(ch in name for ch in "{}\n\r\t"):
        return False
    return True


class _Sampler:
    """Samples entities and areas from the local cache, any language."""

    def __init__(self, executor: ToolExecutor, rng: random.Random) -> None:
        self.executor = executor
        self.rng = rng
        self._areas: Optional[List[Dict[str, str]]] = None
        self._subareas: Optional[List[Dict[str, str]]] = None
        self._zaaps: Optional[List[Dict[str, str]]] = None
        self.kinds = [
            kind for kind, _weight in _KIND_WEIGHTS
            if self.executor.knowledge.entries(kind)
        ]

    def _entries(self, kind: str) -> List[Tuple[int, str, List[str], Dict[str, Any]]]:
        return self.executor.knowledge.entries(kind)

    def _pick(self, kind: str, attempts: int = 25) -> Optional[Tuple[str, str]]:
        entries = self._entries(kind)
        if not entries:
            return None
        for _ in range(attempts):
            _record_id, _haystack, _entry_names, record = self.rng.choice(entries)
            names = _names(record)
            langs = [lang for lang, value in names.items() if _usable(value)]
            if not langs:
                continue
            lang = self.rng.choice(langs)
            return lang, names[lang]
        return None

    def _geo(self, filename: str) -> List[Dict[str, str]]:
        rows: List[Dict[str, str]] = []
        for record in self.executor._load_cache(filename):
            names = _names(record)
            if names:
                rows.append(names)
        return rows

    def zaaps(self) -> List[Dict[str, str]]:
        if self._zaaps is None:
            self._zaaps = []
            for zaap in self.executor._zaaps():
                names = _names(zaap)
                if names:
                    self._zaaps.append(names)
        return self._zaaps

    def area(self) -> Optional[Tuple[str, str]]:
        if self._areas is None:
            self._areas = self._geo("areas_full.json")
            self._subareas = self._geo("subareas_full.json")
        pool = self._areas
        if self._subareas and self.rng.random() < 0.3:
            pool = self._areas + self._subareas
        if not pool:
            return None
        names = self.rng.choice(pool)
        langs = [lang for lang, value in names.items() if _usable(value)]
        if not langs:
            return None
        lang = self.rng.choice(langs)
        return lang, names[lang]


def _knowledge_goal(sampler: _Sampler, kind: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    sampled = sampler._pick(kind)
    if sampled is None:
        return None
    lang, name = sampled
    template = sampler.rng.choice(_KNOWLEDGE_TEMPLATES[lang])
    goal = template.format(name=name)
    args: Dict[str, Any] = {"query": name, "limit": sampler.rng.choice(_LIMITS)}
    if sampler.rng.random() < 0.75:
        args["kind"] = kind
    return goal, args


def _nav_goal(sampler: _Sampler) -> Optional[Tuple[str, Dict[str, Any]]]:
    rng = sampler.rng
    zaaps = sampler.zaaps()
    if zaaps and rng.random() < 0.65:
        names = rng.choice(zaaps)
        lang = rng.choice([lang for lang in LANGS if lang in names] or ["en"])
        name = names.get(lang) or names.get("en") or names.get("fr") or ""
        if _usable(name):
            template = rng.choice(_NAV_ZAAP_TEMPLATES[lang])
            return template.format(name=name), {"zaap": name}
    lang = rng.choice(LANGS)
    x, y = rng.randint(-30, 30), rng.randint(-30, 30)
    template = rng.choice(_NAV_COORD_TEMPLATES[lang])
    return template.format(x=x, y=y), {"x": x, "y": y}


def _quest_goal(sampler: _Sampler) -> Optional[Tuple[str, Dict[str, Any]]]:
    sampled = sampler.area()
    if sampled is None:
        return None
    lang, area = sampled
    variant = sampler.rng.randrange(5)
    level = sampler.rng.choice(_LEVELS)
    limit = sampler.rng.choice(_LIMITS)
    goal = _QUEST_TEMPLATES[lang][variant].format(area=area, level=level, limit=limit)
    args: Dict[str, Any] = {"area": area}
    if variant == 1:
        args["level_min"] = level
    elif variant == 2:
        args["dungeon_only"] = True
    elif variant == 3:
        args["limit"] = limit
    return goal, args


def collect(
    count: int = 400,
    seed: int = 7,
    root: Path = REPO_ROOT / "trajectories",
    instance: str = "collector",
    knowledge_ratio: float = 0.6,
    nav_ratio: float = 0.2,
    dry_run: bool = False,
    sample_print: int = 0,
) -> Dict[str, Any]:
    rng = random.Random(seed)
    recorder = TrajectoryRecorder(root=root, instance=instance)
    executor = ToolExecutor(recorder=recorder)
    sampler = _Sampler(executor, rng)

    nav_available = bool(executor._zaaps())
    effective_nav = nav_ratio if nav_available else 0.0
    if nav_ratio and not nav_available:
        print("  note: Sentinel is not running; skipping nav.goto episodes")

    if not sampler.kinds:
        return {"error": "no knowledge caches found under data/cache"}

    kind_names = [kind for kind, _weight in _KIND_WEIGHTS if kind in sampler.kinds]
    kind_weights = [weight for kind, weight in _KIND_WEIGHTS if kind in sampler.kinds]

    stats: Counter = Counter(total=0, ok=0, failed=0, empty=0, no_entity=0)
    by_tool: Counter = Counter()
    shown = 0

    while stats["total"] < count:
        roll = rng.random()
        if roll < effective_nav:
            planned = _nav_goal(sampler)
            tool = "nav.goto"
        elif rng.random() < knowledge_ratio:
            kind = rng.choices(kind_names, weights=kind_weights, k=1)[0]
            planned = _knowledge_goal(sampler, kind)
            tool = "knowledge.search"
        else:
            planned = _quest_goal(sampler)
            tool = "quest.list"
        if planned is None:
            stats["no_entity"] += 1
            if stats["no_entity"] > 200:
                return {"error": "could not sample enough usable entities", **stats}
            continue

        goal, args = planned
        stats["total"] += 1
        by_tool[tool] += 1
        if dry_run:
            if shown < max(sample_print, 5):
                print(f"  [{tool}] {goal}  -> {args}")
                shown += 1
            continue

        result = executor.execute(tool, args, goal=goal, source="scripted")
        if result.ok:
            stats["ok"] += 1
            if tool == "knowledge.search":
                if not (result.data or {}).get("results"):
                    stats["empty"] += 1
        else:
            stats["failed"] += 1
        if stats["total"] % 50 == 0:
            print(f"  ... {stats['total']} episodes ({stats['ok']} ok)")

    summary = dict(stats)
    summary["by_tool"] = dict(sorted(by_tool.items()))
    summary["nav_available"] = nav_available
    summary["dry_run"] = dry_run
    summary["path"] = str(recorder.path)
    if dry_run and sample_print <= 0:
        summary["sample_goals"] = True
    return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Collect scripted local tool-use episodes")
    parser.add_argument("--count", type=int, default=400)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--root", default=str(REPO_ROOT / "trajectories"))
    parser.add_argument("--instance", default="collector")
    parser.add_argument("--knowledge-ratio", type=float, default=0.6)
    parser.add_argument("--nav-ratio", type=float, default=0.2,
                        help="share of nav.goto episodes (needs Sentinel on 8890)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    summary = collect(
        count=args.count,
        seed=args.seed,
        root=Path(args.root),
        instance=args.instance,
        knowledge_ratio=args.knowledge_ratio,
        nav_ratio=args.nav_ratio,
        dry_run=args.dry_run,
        sample_print=5 if args.dry_run else 0,
    )
    print()
    print(f"collected : {summary.get('total', 0)} "
          f"(ok {summary.get('ok', 0)}, failed {summary.get('failed', 0)}, "
          f"empty results {summary.get('empty', 0)})")
    print(f"by tool   : {summary.get('by_tool', {})}")
    if summary.get("error"):
        print(f"error     : {summary['error']}")
        return 1
    if not args.dry_run:
        print(f"trajectory: {summary['path']}")
        print("next      : python agent/tool_dataset.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
