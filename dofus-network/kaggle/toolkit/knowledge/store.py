"""Read-only access to the canonical knowledge store.

This is the **static** reference layer: quests, objectives, achievements,
monsters, NPCs and items. It is rebuilt from the caches, never learned from, and
opened read-only.

It is deliberately not `agent.knowledge_store`: that one holds **mutable
discoveries** (a drop seen once, a price, a prerequisite inferred by the loop)
and ages facts out. Same word, different object, different lifetime. Giving them
one class name is how the two-store bug in `PIPELINE.md` §1.5 happened; keep the
names distinct.

The important operation is `resolve_objective`: the server and the client speak
in objective ids, while the policy reads French text. Turning
`Vaincre x1 {monster,182}` into `Vaincre x1 Wabbit Gm` is generic -- kind plus id,
no per-quest branch.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

LANGUAGES = ("fr", "en", "es", "de", "pt")

_NAME_COLUMN = {lang: f"name_{lang}" for lang in LANGUAGES}
_PLACEHOLDER = re.compile(r"\{([a-zA-Z_]+)\s*,\s*(\d+)\}")
_KIND_ALIASES = {
    "monster": "monster",
    "npc": "npc",
    "item": "item",
    "map": "map",
    "subarea": "subarea",
    "area": "area",
    "quest": "quest",
    "achievement": "achievement",
}

# Accentless input is common on a normal keyboard ("ble" for "Blé"). Generate
# the bounded exact spellings before falling back to the full normalized scan.
_ACCENTS = {
    "a": "àáâäãå", "c": "ç", "e": "éèêë", "i": "íìîï",
    "n": "ñ", "o": "óòôöõ", "u": "úùûü", "y": "ýÿ",
}


def _accent_variants(text: str, limit: int = 16) -> list[str]:
    base = text.strip().lower()
    if not any(_ACCENTS.get(ch) for ch in base):
        return []
    combos = [""]
    for ch in base:
        opts = [ch] + list(_ACCENTS.get(ch, ""))
        combos = [prefix + opt for prefix in combos for opt in opts]
        if len(combos) > limit:
            return []
    variants = set(combos)
    variants.update(value[:1].upper() + value[1:] for value in list(variants))
    variants.update(value.title() for value in list(variants) if " " in value)
    return sorted(variants)[:limit]


def _loads(value: str | None) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(text).lower())
    return "".join(ch for ch in normalized
                   if not unicodedata.combining(ch) and (ch.isalnum() or ch.isspace())
                   ).strip()


def _row_names(row: sqlite3.Row) -> dict[str, str]:
    return {
        lang: row[_NAME_COLUMN[lang]]
        for lang in LANGUAGES
        if row[_NAME_COLUMN[lang]]
    }


class _ThreadLocalConnection:
    """One read-only SQLite connection per thread.

    The daemon shares a cached store across instance workers, and a single
    `check_same_thread=False` connection is still unsafe when two threads step a
    cursor at once (observed as `IndexError: tuple index out of range` in the
    gate). A connection per thread removes the race and keeps the cache shared.
    """

    def __init__(self, uri: str):
        self._uri = uri
        self._local = threading.local()

    def _conn(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = sqlite3.connect(self._uri, uri=True, check_same_thread=False)
            con.row_factory = sqlite3.Row
            self._local.con = con
        return con

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self._conn().execute(sql, params)

    def close(self) -> None:
        con = getattr(self._local, "con", None)
        if con is not None:
            con.close()
            self._local.con = None


class CanonicalStore:
    """A read-only handle to `dofus_knowledge.sqlite`."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(
                f"knowledge store not found: {self.path}; run python -m knowledge.build"
            )
        uri = self.path.resolve().as_uri() + "?mode=ro"
        self._con = _ThreadLocalConnection(uri)

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> "CanonicalStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def meta(self) -> dict[str, str]:
        return {row["key"]: row["value"] for row in self._con.execute("SELECT key, value FROM meta")}

    def entity(self, kind: str, entity_id: int) -> dict[str, Any] | None:
        row = self._con.execute(
            "SELECT * FROM entities WHERE kind = ? AND entity_id = ?",
            (kind, int(entity_id)),
        ).fetchone()
        if row is None:
            return None
        return {
            "kind": row["kind"],
            "id": row["entity_id"],
            "names": _row_names(row),
            "source": row["source"],
            "source_version": row["source_version"],
            "verified_live": bool(row["verified_live"]),
            "data": _loads(row["data_json"]),
        }

    def name(self, kind: str, entity_id: int, lang: str = "fr") -> str | None:
        kind = _KIND_ALIASES.get(kind.lower(), kind.lower())
        row = self._con.execute(
            "SELECT name_fr, name_en, name_es, name_de, name_pt "
            "FROM entities WHERE kind = ? AND entity_id = ?",
            (kind, int(entity_id)),
        ).fetchone()
        if row is None:
            return None
        candidate = _NAME_COLUMN.get(lang, "name_fr")
        for column in (candidate, "name_fr", "name_en"):
            value = row[column]
            if value:
                return value
        return None

    @lru_cache(maxsize=512)
    def find_entity(
        self, kind: str, text: str, lang: str = "fr", limit: int = 5
    ) -> list[dict[str, Any]]:
        """Find an entity by name in any language without loading every row.

        The fight guard receives a monster name from the trained actor and the
        client needs an id. This is the generic name -> id lookup; it is not a
        quest-specific table.
        """
        kind = _KIND_ALIASES.get(kind.lower(), kind.lower())
        needle = _fold(text)
        if not needle:
            return []
        raw = str(text).strip()
        if raw:
            def exact_result(rows: list[Any]) -> list[dict[str, Any]]:
                matched = []
                for row in rows:
                    name = ""
                    for column in _NAME_COLUMN.values():
                        value = row[column]
                        if value and _fold(value) == needle:
                            name = value
                            break
                    if name:
                        matched.append((int(row["entity_id"]), name))
                matched.sort(key=lambda item: item[0])
                return [{"kind": kind, "id": entity_id, "name": name}
                        for entity_id, name in matched[: max(1, int(limit))]]

            # Exact names are the common target case ("Bouftou", "Blé") and
            # SQL can answer them without converting/folding every row. Prefix,
            # accent-folded and contains matches still use the full scan below,
            # so ranking semantics are unchanged.
            exact = self._con.execute(
                "SELECT entity_id, name_fr, name_en, name_es, name_de, name_pt "
                "FROM entities WHERE kind = ? AND ("
                "name_fr = ? COLLATE NOCASE OR name_en = ? COLLATE NOCASE OR "
                "name_es = ? COLLATE NOCASE OR name_de = ? COLLATE NOCASE OR "
                "name_pt = ? COLLATE NOCASE)",
                (kind, raw, raw, raw, raw, raw),
            ).fetchall()
            if exact:
                result = exact_result(exact)
                if result:
                    return result
            variants = _accent_variants(raw, limit=16)
            if variants:
                placeholders = ",".join("?" * len(variants))
                sql = (
                    "SELECT entity_id, name_fr, name_en, name_es, name_de, name_pt "
                    "FROM entities WHERE kind = ? AND (" + " OR ".join(
                        "name_%s IN (%s)" % (lang, placeholders)
                        for lang in LANGUAGES) + ")"
                )
                params = (kind,) + tuple(variants) * len(LANGUAGES)
                accented = self._con.execute(sql, params).fetchall()
                if accented:
                    result = exact_result(accented)
                    if result:
                        return result
        rows = self._con.execute(
            "SELECT entity_id, name_fr, name_en, name_es, name_de, name_pt "
            "FROM entities WHERE kind = ?", (kind,)
        ).fetchall()
        scored: list[tuple[int, int, str]] = []
        for row in rows:
            best = 0
            best_name = ""
            for column in _NAME_COLUMN.values():
                value = row[column]
                if not value:
                    continue
                candidate = _fold(value)
                if candidate == needle:
                    rank = 3
                elif candidate.startswith(needle):
                    rank = 2
                elif needle in candidate:
                    rank = 1
                else:
                    continue
                if rank > best:
                    best, best_name = rank, value
            if best:
                scored.append((best, int(row["entity_id"]), best_name))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [
            {"kind": kind, "id": entity_id, "name": name}
            for _rank, entity_id, name in scored[: max(1, int(limit))]
        ]

    def quest(self, quest_id: int) -> dict[str, Any] | None:
        row = self._con.execute(
            "SELECT * FROM quests WHERE quest_id = ?", (int(quest_id),)
        ).fetchone()
        if row is None:
            return None
        out: dict[str, Any] = {
            "quest_id": row["quest_id"],
            "names": _loads(row["name_json"]) or {},
            "level_min": row["level_min"],
            "level_max": row["level_max"],
            "category_id": row["category_id"],
            "is_dungeon": bool(row["is_dungeon"]),
            "is_event": bool(row["is_event"]),
            "is_party": bool(row["is_party"]),
            "start": _loads(row["start_json"]),
            "source": row["source"],
            "steps": [],
        }
        steps = self._con.execute(
            "SELECT * FROM quest_steps WHERE quest_id = ? ORDER BY step_index",
            (int(quest_id),),
        ).fetchall()
        for step in steps:
            objectives = self._con.execute(
                "SELECT objective_id FROM quest_objectives "
                "WHERE quest_id = ? AND step_index = ? ORDER BY objective_index",
                (int(quest_id), step["step_index"]),
            ).fetchall()
            out["steps"].append(
                {
                    "step_index": step["step_index"],
                    "step_id": step["step_id"],
                    "names": _loads(step["name_json"]) or {},
                    "descriptions": _loads(step["description_json"]) or {},
                    "optimal_level": step["optimal_level"],
                    "objective_ids": [o["objective_id"] for o in objectives],
                }
            )
        return out

    def objective(self, objective_id: int) -> dict[str, Any] | None:
        row = self._con.execute(
            "SELECT * FROM quest_objectives WHERE objective_id = ?",
            (int(objective_id),),
        ).fetchone()
        if row is None:
            return None
        return {
            "objective_id": row["objective_id"],
            "quest_id": row["quest_id"],
            "step_index": row["step_index"],
            "objective_index": row["objective_index"],
            "type_id": row["type_id"],
            "texts": _loads(row["text_json"]) or {},
            "map_id": row["map_id"],
            "coords": _loads(row["coords_json"]),
            "params": _loads(row["params_json"]),
            "source": row["source"],
        }

    def resolve_text(self, text: str | None, lang: str = "fr") -> dict[str, Any]:
        """Replace typed placeholders with their localized names.

        Unknown entities are left as the original placeholder and reported in
        `unresolved`. They are never replaced by an empty string, which would
        hide that the knowledge is missing.
        """
        if not text:
            return {"text": text, "entities": [], "unresolved": []}

        entities: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []
        seen: set[tuple[str, int]] = set()

        def replace(match: re.Match[str]) -> str:
            raw_kind = match.group(1).lower()
            kind = _KIND_ALIASES.get(raw_kind, raw_kind)
            entity_id = int(match.group(2))
            found = self.name(kind, entity_id, lang)
            if not found:
                unresolved.append(
                    {"kind": raw_kind, "id": entity_id, "placeholder": match.group(0)}
                )
                return match.group(0)
            if (kind, entity_id) not in seen:
                seen.add((kind, entity_id))
                entities.append({"kind": kind, "id": entity_id, "name": found})
            return found

        return {
            "text": _PLACEHOLDER.sub(replace, text),
            "entities": entities,
            "unresolved": unresolved,
        }

    def resolve_objective(self, objective_id: int, lang: str = "fr") -> dict[str, Any] | None:
        obj = self.objective(objective_id)
        if obj is None:
            return None
        raw = obj["texts"].get(lang) or obj["texts"].get("fr")
        resolved = self.resolve_text(raw, lang)
        return {
            **obj,
            "text": resolved["text"],
            "raw_text": raw,
            "entities": resolved["entities"],
            "unresolved": resolved["unresolved"],
            "language": lang,
        }

    def achievement(self, achievement_id: int) -> dict[str, Any] | None:
        row = self._con.execute(
            "SELECT * FROM achievements WHERE achievement_id = ?",
            (int(achievement_id),),
        ).fetchone()
        if row is None:
            return None
        objectives = self._con.execute(
            "SELECT objective_id, details_json, source FROM achievement_objectives "
            "WHERE achievement_id = ? ORDER BY objective_id",
            (int(achievement_id),),
        ).fetchall()
        return {
            "achievement_id": row["achievement_id"],
            "names": _loads(row["name_json"]) or {},
            "descriptions": _loads(row["description_json"]) or {},
            "points": row["points"],
            "level": row["level"],
            "category_id": row["category_id"],
            "objectives": [
                {
                    "objective_id": o["objective_id"],
                    "details": _loads(o["details_json"]),
                    "source": o["source"],
                }
                for o in objectives
            ],
            "source": row["source"],
        }

    def monster(self, monster_id: int) -> dict[str, Any] | None:
        row = self._con.execute(
            "SELECT * FROM monsters WHERE monster_id = ?", (int(monster_id),)
        ).fetchone()
        if row is None:
            return None
        return {
            "monster_id": row["monster_id"],
            "names": _loads(row["name_json"]) or {},
            "is_boss": bool(row["is_boss"]),
            "grades": _loads(row["grades_json"]) or [],
            "source": row["source"],
        }

    def guide_refs(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._con.execute(
            "SELECT id, source, url, title, lang, license, fetched_at "
            "FROM guide_refs ORDER BY id LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [dict(row) for row in rows]


def build_store(cache_dir: str | Path | None = None,
                out_path: str | Path | None = None) -> dict[str, Any]:
    from .build import build_store as _build

    return _build(cache_dir, out_path)
