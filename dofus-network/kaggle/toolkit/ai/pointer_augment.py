"""Query augmentation for the entity pointer, driven by the KB and gazetteer.

The pointer dataset has 4,017 rows, 325 of them ``moved`` and 4 ``progress``.
A lexical model memorises those rows in one epoch. Augmentation is the cheapest
way to manufacture the *variety* the corpus lacks without inventing labels:

* **Paraphrase.** The same request said with another lead verb
  ("trouve la Laine de Bouftou" -> "cherche la Laine de Bouftou"). The entity, and
  therefore the pointer label, is unchanged.
* **Localisation.** ``planning.kb`` carries the same entity's name in fr/en/es/
  de/pt. Replacing the surface form with another language's is a real
  paraphrase a bilingual player produces, and the label is still the same entity.
* **Surface swap.** ``ai.gazetteer`` learned several spellings for one canonical
  value from the corpus ("Laine de Bouftou", "laine de bouftou"). Swapping one
  learned surface for another teaches the pointer that case is not identity.

Nothing here touches the chosen entity, the tool, or the outcome flags; a variant
that changed the answer would be label noise, not augmentation.

Determinism: every variant is seeded from (row index, copy), so two runs over the
same corpus produce the same variants and a validation split is reproducible.
"""

from __future__ import annotations

import random
import re
import zlib
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ai.data_splits import normalize

#: Lead verbs the corpus uses to open a goal, in the two languages it is written in.
LEAD_VERBS = (
    "va voir", "va parler a", "va parler à", "parle a", "parle à", "rends-toi a",
    "rends-toi à", "trouve", "cherche", "achète", "achete", "vends", "tue",
    "combats", "explore", "termine", "avance", "continue", "fais",
    "go to", "talk to", "find", "look for", "buy", "sell", "kill", "explore",
    "finish", "advance", "continue", "do",
)

TEMPLATES = {
    "fr": ("trouve {x}", "cherche {x}", "va voir {x}", "occupe-toi de {x}",
           "il me faut {x}", "rends-toi à {x}"),
    "en": ("find {x}", "look for {x}", "go to {x}", "deal with {x}",
           "i need {x}", "head to {x}"),
}

#: Entity kind -> the KB table that can localise it, and the id field in `chosen`.
KB_KINDS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "item": ("item", ("gid", "item_id")),
    "mob": ("monster", ("main_mob_id", "monster_id", "group_id")),
    "objective": ("quest", ("objective_id", "quest_id")),
}


class Augmenter:
    """Builds goal paraphrases for one corpus row.

    ``kb`` and ``gazetteer`` are optional: without them the paraphrases still
    run, and only the language/surface swaps are skipped. Neither is imported at
    module load, so the trainer still starts on a machine with no KB.
    """

    def __init__(self, kb: Any = None, gazetteer: Any = None,
                 languages: Sequence[str] = ("en", "fr")) -> None:
        self.kb = kb
        self.gazetteer = gazetteer
        self.languages = tuple(languages)

    # -- surface helpers ----------------------------------------------------

    @staticmethod
    def _tail(goal: str) -> str:
        """The goal without its lead verb phrase."""
        text = (goal or "").strip()
        lowered = text.lower()
        for verb in sorted(LEAD_VERBS, key=len, reverse=True):
            if lowered.startswith(verb + " "):
                return text[len(verb):].strip()
        return text

    def _paraphrase(self, goal: str, rng: random.Random, language: str) -> Optional[str]:
        tail = self._tail(goal)
        if len(tail) < 3:
            return None
        template = rng.choice(TEMPLATES.get(language, TEMPLATES["fr"]))
        candidate = template.format(x=tail)
        return candidate if normalize(candidate) != normalize(goal) else None

    def _localised(self, kind: str, ident: Any, language: str) -> Optional[str]:
        """The entity's name in another language, from the KB."""
        if self.kb is None or kind not in KB_KINDS:
            return None
        table, fields = KB_KINDS[kind]
        value = None
        for field in fields:
            if ident.get(field) is not None:
                value = ident.get(field)
                break
        if value is None:
            return None
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        try:
            if table == "item":
                record = self.kb.item(number, language)
            elif table == "monster":
                record = self.kb.monster(number, language)
            else:
                record = self.kb.quest(number, language)
        except Exception:  # noqa: BLE001 - a bad id is not an augmentation failure
            return None
        name = getattr(record, "name", None)
        return name if isinstance(name, str) and len(name) >= 2 else None

    def _surface_swap(self, goal: str, rng: random.Random) -> Optional[str]:
        """Replace one learned surface form with another spelling of the same value."""
        if self.gazetteer is None:
            return None
        payload = getattr(self.gazetteer, "payload", {}) or {}
        names = payload.get("names") or {}
        flat = normalize(goal)
        options: List[Tuple[str, str]] = []
        for field, entries in names.items():
            for surface in entries:
                if len(surface) < 4 or surface not in flat:
                    continue
                canonical = entries[surface]
                for other, other_values in entries.items():
                    if other == surface or len(other) < 4:
                        continue
                    if other_values == canonical and other not in flat:
                        options.append((surface, other))
        if not options:
            return None
        surface, other = rng.choice(options)
        # Recover the original spelling: the index is normalised, so replace on
        # the lowered goal and keep the length (both surfaces are normalised).
        index = normalize(goal).find(surface)
        if index < 0:
            return None
        if other.isspace() or not other.isprintable():
            return None
        rebuilt = goal[:index] + other + goal[index + len(surface):]
        # A word-boundary sanity check: the replacement must not fuse into a
        # neighbouring word ("laine" inside "lainemagique").
        before = rebuilt[:index]
        after = rebuilt[index + len(other):]
        if before and before[-1].isalnum():
            return None
        if after and after[0].isalnum():
            return None
        return rebuilt

    # -- variants -----------------------------------------------------------

    def variants(self, row: Dict[str, Any], count: int,
                 seed: int = 0) -> List[str]:
        """``count`` distinct goal paraphrases for one row (may be shorter)."""
        base = str(row.get("goal") or "").strip()
        if count <= 0 or not base:
            return []
        out: List[str] = []
        seen = {normalize(base)}
        chosen = row.get("chosen") or {}
        kind = str(chosen.get("kind") or "")
        for copy in range(count):
            key = ("%s|%d|%d" % (base, seed, copy)).encode("utf-8")
            rng = random.Random((zlib.crc32(key) << 8) ^ copy)
            goal: Optional[str] = None
            choice = rng.random()
            if choice < 0.5:
                language = "fr" if any(ch in base.lower() for ch in "éèàùç’") else rng.choice(self.languages)
                goal = self._paraphrase(base, rng, language)
            if goal is None:
                goal = self._surface_swap(base, rng)
            if goal is None:
                name = self._localised(kind, chosen, rng.choice(self.languages))
                if name and normalize(name) != normalize(base):
                    goal = name if len(self._tail(base)) < 4 else "%s %s" % (
                        self._tail(base).split()[0] if self._tail(base) else "", name)
            if goal is None:
                goal = self._paraphrase(base, rng, rng.choice(self.languages))
            if goal is None:
                continue
            key = normalize(goal)
            if key in seen:
                continue
            seen.add(key)
            out.append(goal)
        return out


def load_defaults(with_kb: bool = True) -> Tuple[Any, Any]:
    """Best-effort ``(KnowledgeBase, Gazetteer)``; either may be ``None``."""
    kb = gazetteer = None
    if not with_kb:
        return kb, gazetteer
    try:
        from planning.kb import DEFAULT_KB, KnowledgeBase

        if DEFAULT_KB.is_file():
            kb = KnowledgeBase()
    except Exception:  # noqa: BLE001 - augmentation is optional
        kb = None
    try:
        from ai.gazetteer import DEFAULT_GAZETTEER, Gazetteer

        if DEFAULT_GAZETTEER.is_file():
            gazetteer = Gazetteer()
    except Exception:  # noqa: BLE001
        gazetteer = None
    return kb, gazetteer
