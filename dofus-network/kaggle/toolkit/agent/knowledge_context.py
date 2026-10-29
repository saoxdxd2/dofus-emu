"""Attach the canonical knowledge to a live observation.

The client reports objective ids and a short tracker; the policy reads French
text. This module is the one bridge between the two: it resolves the current
objective against `knowledge.CanonicalStore` and adds the result under
`observation["knowledge"]`.

It does not plan and it does not invent. If the store is missing, or the
objective is not in it, the observation says why and the loop continues with
what the client provided.
"""

from __future__ import annotations

import json
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from knowledge.store import CanonicalStore

DEFAULT_STORE = (
    Path(__file__).resolve().parents[1] / "data" / "knowledge" / "dofus_knowledge.sqlite"
)
# Rows the client itself reported, persisted by `tools/ingest_observed_quests.py` (G29).
# This file is licence-clean (it comes from the running software) and is never mixed into
# the downloaded store. Missing is normal until a live run has produced a catalogue.
OBSERVED_FILE = (
    Path(__file__).resolve().parents[1] / "data" / "knowledge" / "observed_quests.jsonl"
)
# The verified client index (`tools/knowledge_index.py --write`): the same rows with
# their provenance and conflicts resolved, built from all four client sources instead
# of the catalogue alone. It wins when present because it strictly dominates the
# JSONL ingest: same keys, plus the text that only ready/current objectives carry.
OBSERVED_INDEX_FILE = (
    Path(__file__).resolve().parents[1] / "data" / "knowledge" / "client_index.json"
)


_STORE_CACHE: Dict[str, CanonicalStore] = {}
_STORE_LOCK = threading.Lock()


def load_default_store(path: Optional[Path] = None) -> Optional[CanonicalStore]:
    target = Path(path) if path is not None else DEFAULT_STORE
    key = str(target)
    with _STORE_LOCK:
        cached = _STORE_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        store = CanonicalStore(target)
    except FileNotFoundError:
        return None
    with _STORE_LOCK:
        return _STORE_CACHE.setdefault(key, store)


def _current_objective(obs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    tracker = obs.get("current_objective")
    if isinstance(tracker, dict) and tracker.get("objective_id") is not None:
        return tracker
    ready = obs.get("ready_objectives")
    if isinstance(ready, list):
        for entry in ready:
            if isinstance(entry, dict) and entry.get("objective_id") is not None:
                return entry
    return None


def _block_from_row(row: Dict[str, Any], language: str, origin: str) -> Dict[str, Any]:
    """One observed row in the shape the observation contract publishes.

    `origin` says where the row came from: the catalogue in this snapshot, or the
    persisted ingest file. Both are the client's own data; the distinction is for
    diagnosis, not for precedence.
    """
    return {
        "status": "observed",
        "language": language,
        "objective_id": int(row.get("objective_id")),
        "quest_id": row.get("quest_id"),
        "observed_from": origin,
        "objective": {
            "text": row.get("text"),
            "type_id": row.get("type_id"),
            "type": row.get("type"),
            "map_id": row.get("map_id"),
            "params": row.get("params"),
            "step_id": row.get("step_id"),
            "cur_completion": row.get("cur_completion"),
            "max_completion": row.get("max_completion"),
            "source": "observed",
        },
        "quest_name": row.get("quest_name"),
        "step_name": row.get("step_name"),
        "step_description": row.get("step_description"),
    }


def _catalog_row(quest: Dict[str, Any], step: Dict[str, Any],
                 obj: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "quest_id": quest.get("id"),
        "quest_name": quest.get("name"),
        "step_id": obj.get("step_id", step.get("id")),
        "step_name": step.get("name"),
        "step_description": step.get("description"),
        "objective_id": obj.get("id"),
        "type_id": obj.get("type_id"),
        "type": obj.get("type"),
        "map_id": obj.get("map_id"),
        "params": obj.get("params"),
        "text": obj.get("text"),
        "cur_completion": obj.get("cur_completion"),
        "max_completion": obj.get("max_completion"),
    }


@lru_cache(maxsize=4)
def _persisted_index(path: str, mtime_ns: int, size: int
                     ) -> Dict[Tuple[int, int], Dict[str, Any]]:
    """(quest, objective) -> row, cached until the file changes.

    The stat tuple is the cache key, so an ingest is visible to the next observation
    without restarting the loop. A duplicate key keeps the last line: the ingest is the
    only writer and it already merges by richness.
    """
    rows: Dict[Tuple[int, int], Dict[str, Any]] = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                try:
                    key = (int(row.get("quest_id")), int(row.get("objective_id")))
                except (TypeError, ValueError):
                    continue
                rows[key] = row
    except OSError:
        return {}
    return rows


@lru_cache(maxsize=4)
def _json_index(path: str, mtime_ns: int, size: int
                ) -> Dict[Tuple[int, int], Dict[str, Any]]:
    """(quest, objective) -> verified row, from `client_index.json`."""
    rows: Dict[Tuple[int, int], Dict[str, Any]] = {}
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    block = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(block, dict):
        return {}
    for row in block.values():
        if not isinstance(row, dict):
            continue
        try:
            key = (int(row.get("quest_id")), int(row.get("objective_id")))
        except (TypeError, ValueError):
            continue
        rows[key] = row
    return rows


def load_observed_index(path: Optional[Path] = None
                        ) -> Dict[Tuple[int, int], Dict[str, Any]]:
    if path is None:
        target = OBSERVED_INDEX_FILE if OBSERVED_INDEX_FILE.is_file() else OBSERVED_FILE
    else:
        target = Path(path)
    try:
        stat = target.stat()
    except OSError:
        return {}
    if target.suffix == ".json":
        return _json_index(str(target), stat.st_mtime_ns, stat.st_size)
    return _persisted_index(str(target), stat.st_mtime_ns, stat.st_size)


def _observed_objective(obs: Dict[str, Any], objective_id: int,
                        tracker_quest_id: Any, language: str,
                        observed_path: Optional[Path] = None
                        ) -> Optional[Dict[str, Any]]:
    """The client's own row for this objective, when it was captured (G29).

    The catalogue is the live graph the running client holds for the character's
    active quests (`dbQuest`/`dbSteps`/`dbObjectives`), so it outranks any
    downloaded cache: it cannot describe another game or another quest. Matching
    on the tracker's quest id when there is one keeps a shared objective id from
    resolving to a different quest the character also holds. When this snapshot
    does not carry the catalogue, the persisted ingest file is consulted; an
    objective id alone is used only when exactly one quest claims it.
    """
    catalog = obs.get("quests_catalog")
    if isinstance(catalog, list):
        for quest in catalog:
            if not isinstance(quest, dict):
                continue
            if tracker_quest_id is not None:
                try:
                    if int(quest.get("id")) != int(tracker_quest_id):
                        continue
                except (TypeError, ValueError):
                    continue
            for step in quest.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                for obj in step.get("objectives") or []:
                    if not isinstance(obj, dict):
                        continue
                    try:
                        if int(obj.get("id")) != int(objective_id):
                            continue
                    except (TypeError, ValueError):
                        continue
                    return _block_from_row(_catalog_row(quest, step, obj),
                                           language, "catalog")
    index = load_observed_index(observed_path)
    if not index:
        return None
    try:
        oid = int(objective_id)
    except (TypeError, ValueError):
        return None
    hit = None
    if tracker_quest_id is not None:
        try:
            hit = index.get((int(tracker_quest_id), oid))
        except (TypeError, ValueError):
            hit = None
    else:
        candidates = [row for (_, row_oid), row in index.items() if row_oid == oid]
        if len({row.get("quest_id") for row in candidates}) == 1:
            hit = candidates[0]
    if hit is None:
        return None
    return _block_from_row(hit, language, "file")


@lru_cache(maxsize=2048)
def _knowledge_block(store: CanonicalStore, objective_id: int, language: str,
                     tracker_quest_id: Any
                     ) -> Dict[str, Any]:
    """Resolve one objective for the observation contract.

    The SQLite store is opened read-only and the objective text cannot change
    while it is open, so the five queries behind `resolve_objective` were being
    repeated on every snapshot for the same objective. The cache key includes
    the tracker's quest id only because the miss path echoed it back.
    """
    resolved = store.resolve_objective(objective_id, language)
    if resolved is None:
        return {
            "status": "objective_not_in_store",
            "objective_id": objective_id,
            "quest_id": tracker_quest_id,
        }
    store_quest_id = resolved.get("quest_id")
    if tracker_quest_id is not None and store_quest_id is not None:
        try:
            disagrees = int(store_quest_id) != int(tracker_quest_id)
        except (TypeError, ValueError):
            disagrees = False
        if disagrees:
            # The client named this objective while holding quest A, the store finds the
            # same id under quest B. Measured over the live corpus: of the 23 (quest,
            # objective) pairs the store could resolve, **all 23** disagreed about the
            # quest and 17 disagreed about the objective's own type — objective 13900 is
            # a talk-to-NPC for the client and "Vaincre x1 {monster,5243}" for the store.
            # So a shared id is not the same objective, and publishing the store's text
            # would rank NPC replies, label dialog outcomes and quote a quest name for
            # content the character is not doing. The ids stay in the row for diagnosis;
            # the content does not go any further.
            return {
                "status": "objective_id_collision",
                "objective_id": objective_id,
                "quest_id": tracker_quest_id,
                "store_quest_id": store_quest_id,
                "store_type_id": resolved.get("type_id"),
            }
    quest_id = resolved.get("quest_id") or tracker_quest_id
    quest = store.quest(int(quest_id)) if quest_id is not None else None
    step = None
    if quest:
        for candidate in quest.get("steps") or []:
            if candidate.get("step_index") == resolved.get("step_index"):
                step = candidate
                break
    return {
        "status": "resolved",
        "language": language,
        "objective_id": objective_id,
        "quest_id": quest_id,
        "objective": {
            "text": resolved.get("text"),
            "raw_text": resolved.get("raw_text"),
            "type_id": resolved.get("type_id"),
            "map_id": resolved.get("map_id"),
            "params": resolved.get("params"),
            "entities": resolved.get("entities") or [],
            "unresolved": resolved.get("unresolved") or [],
            "source": resolved.get("source"),
        },
        "quest_name": (quest or {}).get("names", {}).get(language)
        or (quest or {}).get("names", {}).get("fr"),
        "step_name": (step or {}).get("names", {}).get(language)
        or (step or {}).get("names", {}).get("fr"),
        "step_description": (step or {}).get("descriptions", {}).get(language)
        or (step or {}).get("descriptions", {}).get("fr"),
    }


def enrich_observation(
    obs: Optional[Dict[str, Any]], store: Optional[CanonicalStore],
    language: str = "fr",
    observed_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Return a shallow copy of `obs` with `knowledge` added.

    The copy matters: the same snapshot is passed to the executor and written to
    the trajectory, and a second enrichment must not stack fields.
    """
    if not isinstance(obs, dict):
        return obs
    if store is None:
        return obs

    out = dict(obs)
    out.pop("knowledge", None)
    tracker = _current_objective(obs)
    if not tracker:
        out["knowledge"] = {"status": "no_active_objective"}
        return out

    try:
        objective_id = int(tracker["objective_id"])
    except (TypeError, ValueError):
        out["knowledge"] = {"status": "invalid_objective_id",
                            "objective_id": tracker.get("objective_id")}
        return out

    tracker_quest_id = tracker.get("quest_id")
    if tracker_quest_id is not None:
        try:
            tracker_quest_id = int(tracker_quest_id)
        except (TypeError, ValueError):
            tracker_quest_id = None
    observed = _observed_objective(obs, objective_id, tracker_quest_id, language,
                                   observed_path)
    if observed is not None:
        out["knowledge"] = observed
        return out
    block = _knowledge_block(store, objective_id, language, tracker_quest_id)
    # Shallow copy: the cached block must not be mutated by a caller.
    out["knowledge"] = dict(block)
    return out
