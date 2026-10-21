"""Surface-form gazetteer: canonical argument values learned from the corpus.

Why the knowledge base is not enough
------------------------------------
The SFT corpus is full of *invented* entities — "Oscuras intenciones",
"Don't Push", "SharUlmNi Fiber" — that exist nowhere in ``data/cache``. A
DofusDB lookup therefore resolves 1 of the 4 quest names that appear in a typical
validation batch. The mapping that matters is surface form -> canonical value,
and the only place it exists is the corpus itself.

What it builds
--------------
For every training row, for every string-valued argument, if that value occurs in
the goal text (accent/case-insensitive), record the *surface form as written*:

    "avanza la misión Oscuras intenciones"  +  quest_id=1935
        ->  "oscuras intenciones" -> 1935

and for free-text fields, the canonical spelling:

    "farm Madoll"  +  monster="Madoll"
        ->  "madoll" -> "Madoll"

**Train split only.** Building from val would leak the benchmark into the
post-processor, which is the same mistake the frame-disjoint split fixed.

At inference the post-processor uses this to
  1. resolve a name in the goal to an id the encoder failed to emit;
  2. normalise a span the encoder got slightly wrong (wrong case, stray article).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ai.data_splits import normalize, load_jsonl

DEFAULT_GAZETTEER = Path(__file__).resolve().parent.parent / "data" / "gazetteer.json"

# Fields whose canonical value is an identifier (resolve name -> id).
ID_FIELDS = ("quest_id", "map_id", "item_id", "monster_id")
# Fields whose canonical value is a display name (normalise spelling/case).
NAME_FIELDS = ("monster", "item", "zaap", "npc", "query", "zone", "target")


def _surface_forms(goal: str, value: str) -> List[str]:
    """Every way ``value`` appears in ``goal``, as actually written."""
    flat_goal = normalize(goal)
    flat_value = normalize(value)
    if len(flat_value) < 3 or flat_value not in flat_goal:
        return []
    # Walk the goal once, matching on the normalised form but keeping the original
    # characters, so "Laine de Bouftou" is recorded with its capitalisation.
    forms: List[str] = []
    index = flat_goal.find(flat_value)
    while index != -1:
        forms.append(goal[index:index + len(flat_value)])
        index = flat_goal.find(flat_value, index + 1)
    return [form for form in forms if form.strip()]


# Fields whose value cannot be aligned by matching the value inside the goal
# (the goal carries a *name*, the argument carries an *id*). For those we align
# with a tool-specific rule instead.
_ID_FROM_TAIL = ("quest_id",)


def _goal_tail(goal: str) -> str:
    """The goal with the leading verb phrase removed — usually the entity name."""
    from ai.postprocess import _LEAD_VERBS

    tail = _LEAD_VERBS.sub("", goal or "").strip(" .,!?:;")
    return tail[:80]


def build(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Build the gazetteer from flat corpus rows (train split only)."""
    id_map: Dict[str, Dict[str, Counter]] = {}
    name_map: Dict[str, Dict[str, Counter]] = {}
    tail_map: Dict[str, Counter] = {}

    for row in rows:
        goal = str(row.get("goal") or "")
        args = row.get("args") or {}
        if not goal or not isinstance(args, dict):
            continue
        for key, value in args.items():
            # (a) id fields whose *name* appears in the goal, not the value:
            #     align on the goal's tail instead. Must run before the string
            #     guard below, because these values are integers.
            if key in _ID_FROM_TAIL and isinstance(value, int):
                tail = _goal_tail(goal)
                if len(tail) >= 3:
                    tail_map.setdefault(normalize(tail), Counter())[value] += 1
                continue

            # (b) id fields whose digits appear verbatim in the goal.
            if key in ID_FIELDS and not isinstance(value, str):
                digits = str(value).strip()
                if digits and digits in normalize(goal):
                    field_map = id_map.setdefault(key, {})
                    field_map.setdefault(normalize(digits), Counter())[value] += 1
                continue

            if not isinstance(value, str) or len(value.strip()) < 3:
                continue

            # (c) string fields: align on the surface form as written.
            if key in ID_FIELDS:
                field_map = id_map.setdefault(key, {})
            elif key in NAME_FIELDS:
                field_map = name_map.setdefault(key, {})
            else:
                continue
            for surface in _surface_forms(goal, value):
                field_map.setdefault(normalize(surface), Counter())[value] += 1

    # A tail that maps to more than one id would silently resolve the wrong
    # quest, so keep only unambiguous ones.
    quest_names = {tail: dict(counter) for tail, counter in tail_map.items()
                   if len(counter) == 1}
    return {
        "ids": {field: {k: dict(v) for k, v in surfaces.items()}
                for field, surfaces in id_map.items()},
        "names": {field: {k: dict(v) for k, v in surfaces.items()}
                  for field, surfaces in name_map.items()},
        "quest_names": quest_names,
    }


class Gazetteer:
    """Lookup over the corpus-derived surface forms."""

    def __init__(self, payload: Optional[Dict[str, Any]] = None,
                 path: Path = DEFAULT_GAZETTEER) -> None:
        self.path = Path(path)
        self.payload = payload if payload is not None else self._load()
        self._ids = self.payload.get("ids") or {}
        self._names = self.payload.get("names") or {}
        self._quest_names = self.payload.get("quest_names") or {}

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"ids": {}, "names": {}}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"ids": {}, "names": {}}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.payload, ensure_ascii=False, indent=1),
                             encoding="utf-8")

    # -- lookups -------------------------------------------------------------

    def lookup_id(self, field: str, text: str) -> Optional[Any]:
        """Resolve a surface form (or bare digits) to a canonical id."""
        entries = self._ids.get(field) or {}
        if not entries:
            return None
        key = normalize(text)
        if key in entries:
            return _best(entries[key])
        digits = re.sub(r"\D", "", text)
        if digits and digits in entries:
            return _best(entries[digits])
        # Longest surface form contained in the text wins, so "avance la quête
        # Oscuras intenciones" resolves on the quest name, not on "quête".
        best: Optional[Tuple[int, Any]] = None
        for surface, values in entries.items():
            if not _bounded_match(key, surface):
                continue
            candidate = (len(surface), _best(values))
            if best is None or candidate[0] > best[0]:
                best = candidate
        return None if best is None else best[1]

    def lookup_quest_id(self, goal: str) -> Optional[int]:
        """Quest id from a quest *name* written in the goal.

        Built with a tool-specific alignment (the goal's tail maps to the row's
        ``quest_id``), because the name and the id never appear together.
        """
        if not self._quest_names:
            return None
        from ai.postprocess import _LEAD_VERBS

        tail = normalize(_LEAD_VERBS.sub("", goal or "").strip(" .,!?:;"))
        if len(tail) < 3:
            return None
        if tail in self._quest_names:
            return int(_best(self._quest_names[tail]))
        best: Optional[Tuple[int, int]] = None
        for name, values in self._quest_names.items():
            if not _bounded_match(tail, name):
                continue
            candidate = (len(name), int(_best(values)))
            if best is None or candidate[0] > best[0]:
                best = candidate
        return None if best is None else best[1]

    def lookup_name(self, field: str, text: str) -> Optional[str]:
        """Canonical spelling for a free-text field."""
        entries = self._names.get(field) or {}
        if not entries:
            return None
        key = normalize(text)
        if key in entries:
            return _best(entries[key])
        # Longest bounded surface form in the text wins, so "Parchemin de Vaelor"
        # resolves on the full name rather than on "Parchemin".
        best: Optional[Tuple[int, str]] = None
        for surface, values in entries.items():
            if not _bounded_match(key, surface):
                continue
            candidate = (len(surface), _best(values))
            if best is None or candidate[0] > best[0]:
                best = candidate
        return None if best is None else best[1]

    def stats(self) -> Dict[str, Any]:
        return {
            "id_fields": {key: len(value) for key, value in sorted(self._ids.items())},
            "name_fields": {key: len(value) for key, value in sorted(self._names.items())},
            "quest_names": len(self._quest_names),
            "path": str(self.path),
        }


def _bounded_match(key: str, surface: str) -> bool:
    """True if ``surface`` occurs in ``key`` at a **word boundary**.

    A plain ``surface in key`` test is not good enough: the item surface ``tin``
    occurs inside "glimmerroot potion", so ``lookup_name`` happily replaced a
    correct span with "Tin". Surfaces may contain apostrophes, hyphens and
    accents, so ``\\b`` is unreliable -- the boundary is checked by hand on the
    characters either side of the match.
    """
    if not surface or len(surface) < 3:
        return False
    start = key.find(surface)
    while start != -1:
        end = start + len(surface)
        before_ok = start == 0 or not key[start - 1].isalnum()
        after_ok = end >= len(key) or not key[end].isalnum()
        if before_ok and after_ok:
            return True
        start = key.find(surface, start + 1)
    return False


def _best(values: Dict[str, int]) -> Any:
    """Most frequent canonical value for a surface form."""
    return max(values.items(), key=lambda pair: pair[1])[0]


def build_from_corpus(train_path: Path, out: Path = DEFAULT_GAZETTEER) -> Dict[str, Any]:
    rows = load_jsonl(train_path)
    payload = build(rows)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    gazetteer = Gazetteer(payload, out)
    print(json.dumps(gazetteer.stats(), indent=2))
    return payload


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", default=str(
        Path(__file__).resolve().parent.parent / "data" / "core_sft_v4" / "train.jsonl"))
    parser.add_argument("--out", default=str(DEFAULT_GAZETTEER))
    args = parser.parse_args(argv)
    build_from_corpus(Path(args.train), Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
