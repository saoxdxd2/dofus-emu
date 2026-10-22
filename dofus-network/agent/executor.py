"""Tool executor — runs a validated tool call against its backend.

Three backends (see ``agent.schema``):

* ``local``      — the DofusDB cache in ``data/cache`` (knowledge.search,
                    quest.list). Always available, no game required.
* ``sentinel``   — the Sentinel service on ``127.0.0.1:8890`` (nav.goto).
* ``directive``  — a commander directive POSTed to the client bridge. The
                    bridge (C# + injected JS) performs the action in the live
                    client and reports the outcome.

Every execution, success or failure, is recorded as a trajectory ``step`` so
the log doubles as the SFT/RL dataset.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.recorder import TrajectoryRecorder
from agent.knowledge_store import KnowledgeStore
from agent.schema import TOOLS_BY_NAME, validate_call

logger = logging.getLogger("dofus.agent.executor")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "cache"

# kind -> cache file
KNOWLEDGE_FILES: Dict[str, str] = {
    "monster": "monsters_full.json",
    "spell": "spells_full.json",
    "quest": "quests_full.json",
    "item": "items_full.json",
    "npc": "npcs_full.json",
    "achievement": "achievements_full.json",
    "dungeon": "dungeons_full.json",
}

_ANY_KIND_ORDER = ("monster", "quest", "item", "npc", "spell", "dungeon", "achievement")

_NAME_KEYS_FLAT = ("name_fr", "name_en", "name_es", "name_de")


def _normalize(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def _localized_strings(record: Dict[str, Any]) -> List[str]:
    """All human-readable strings of a record (localised names + description)."""
    out: List[str] = []
    name = record.get("name")
    if isinstance(name, dict):
        out.extend(str(v) for v in name.values() if isinstance(v, str))
    elif isinstance(name, str):
        out.append(name)
    for key in _NAME_KEYS_FLAT:
        value = record.get(key)
        if isinstance(value, str):
            out.append(value)
    description = record.get("description")
    if isinstance(description, dict):
        out.extend(str(v) for v in description.values() if isinstance(v, str))
    elif isinstance(description, str):
        out.append(description)
    return out


@dataclass
class ToolResult:
    tool: str
    ok: bool
    data: Any = None
    error: Optional[str] = None
    directive: Optional[str] = None
    latency_ms: float = 0.0
    source: str = "live"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool,
            "ok": self.ok,
            "data": self.data,
            "error": self.error,
            "directive": self.directive,
            "latency_ms": round(self.latency_ms, 2),
            "source": self.source,
        }


class _KnowledgeIndex:
    """Lazy, thread-safe index over the DofusDB cache files."""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self._lock = threading.Lock()
        self._entries: Dict[str, List[Tuple[int, str, List[str], Dict[str, Any]]]] = {}

    def entries(self, kind: str) -> List[Tuple[int, str, List[str], Dict[str, Any]]]:
        with self._lock:
            cached = self._entries.get(kind)
            if cached is not None:
                return cached
            path = self.cache_dir / KNOWLEDGE_FILES.get(kind, "")
            records: List[Dict[str, Any]] = []
            if path.exists():
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(payload, list):
                        records = payload
                except (json.JSONDecodeError, OSError) as exc:
                    logger.warning(f"Unreadable cache file {path}: {exc}")
            entries: List[Tuple[int, str, List[str], Dict[str, Any]]] = []
            for record in records:
                if not isinstance(record, dict):
                    continue
                names = _localized_strings(record)
                names_norm = [_normalize(n) for n in names if n]
                haystack = " ".join(names_norm)
                entries.append((int(record.get("id") or 0), haystack, names_norm, record))
            self._entries[kind] = entries
            return entries

    def search(
        self,
        query: str,
        kind: str = "any",
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        query_norm = _normalize(query).strip()
        if not query_norm:
            return []
        tokens = [t for t in query_norm.split() if len(t) >= 2]
        kinds = _ANY_KIND_ORDER if kind == "any" else (kind,)

        results: List[Tuple[float, int, str, Dict[str, Any]]] = []
        for k in kinds:
            for record_id, haystack, names_norm, record in self.entries(k):
                score = 0.0
                if names_norm:
                    if query_norm in names_norm:
                        score += 6.0
                    elif any(name.startswith(query_norm) for name in names_norm):
                        score += 4.0
                if query_norm and query_norm in haystack:
                    score += 2.0
                token_hits = sum(1 for t in tokens if t in haystack)
                if tokens and token_hits == len(tokens):
                    score += 1.5
                score += 0.5 * token_hits
                if score > 0:
                    results.append((score, record_id, k, record))

        results.sort(key=lambda item: (-item[0], item[1]))
        return [
            {"kind": k, "score": round(score, 2), **_compact(k, record)}
            for score, _record_id, k, record in results[: max(1, int(limit))]
        ]


def _compact(kind: str, record: Dict[str, Any]) -> Dict[str, Any]:
    if kind == "monster":
        grades = record.get("grades") or []
        return {
            "id": record.get("id"),
            "name": {key: record.get(key, "") for key in _NAME_KEYS_FLAT if record.get(key)},
            "is_boss": record.get("is_boss", False),
            "grades": [
                {"level": g.get("level"), "hp": g.get("life_points")}
                for g in grades if isinstance(g, dict)
            ][:5],
        }
    if kind == "spell":
        return {
            "id": record.get("id"),
            "name": {key: record.get(key, "") for key in ("name_fr", "name_en", "name_es")
                     if record.get(key)},
            "type_id": record.get("type_id"),
        }
    if kind == "quest":
        steps = []
        for step in record.get("steps") or []:
            objectives = [
                {
                    "type_id": obj.get("type_id"),
                    "text": obj.get("text"),
                    "map_id": obj.get("map_id"),
                    "coords": obj.get("coords"),
                }
                for obj in step.get("objectives") or []
            ]
            steps.append({
                "id": step.get("id"),
                "name": step.get("name"),
                "optimal_level": step.get("optimal_level"),
                "objectives": objectives,
            })
        return {
            "id": record.get("id"),
            "name": record.get("name"),
            "level_min": record.get("level_min"),
            "level_max": record.get("level_max"),
            "is_dungeon": record.get("is_dungeon"),
            "start_position": record.get("start_position"),
            "steps": steps,
        }
    if kind == "item":
        return {
            "id": record.get("id"),
            "name": record.get("name"),
            "level": record.get("level"),
            "type_id": record.get("type_id"),
            "price": record.get("price"),
            "is_saleable": record.get("is_saleable"),
        }
    if kind == "npc":
        return {
            "id": record.get("id"),
            "name": record.get("name"),
            "actions": record.get("actions"),
        }
    if kind == "achievement":
        return {
            "id": record.get("id"),
            "name": record.get("name"),
            "description": record.get("description"),
            "points": record.get("points"),
            "level": record.get("level"),
        }
    if kind == "dungeon":
        return {
            "id": record.get("id"),
            "name": record.get("name"),
            "level_min": record.get("level_min"),
            "entrance_map_id": record.get("entrance_map_id"),
            "boss_monster_ids": record.get("boss_monster_ids"),
        }
    return {"id": record.get("id")}


class ToolExecutor:
    """Dispatch validated tool calls to local / sentinel / directive backends."""

    def __init__(
        self,
        sentinel_url: str = "http://127.0.0.1:8890",
        directive_url: str = "http://127.0.0.1:8893/directive",
        cache_dir: Path = DEFAULT_CACHE_DIR,
        timeout: float = 6.0,
        knowledge_store: Optional[Path] = None,
        recorder: Optional[TrajectoryRecorder] = None,
    ) -> None:
        self.sentinel_url = sentinel_url.rstrip("/")
        self.directive_url = directive_url
        self.timeout = float(timeout)
        self.recorder = recorder
        self.knowledge = _KnowledgeIndex(Path(cache_dir))
        # Discovered facts live here, not in the model and not in the cache. The
        # path is injectable so a test never writes to the user's real store.
        self.store = KnowledgeStore(knowledge_store)
        self._zaaps_cache: Optional[List[Dict[str, Any]]] = None
        self._geo_index: Optional[Dict[str, Any]] = None

    # -- HTTP helpers --------------------------------------------------------

    def _http_json(
        self,
        method: str,
        url: str,
        body: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                try:
                    return True, json.loads(raw)
                except json.JSONDecodeError:
                    return True, {"raw": raw[:4000]}
        except urllib.error.HTTPError as exc:
            return False, f"HTTP {exc.code} from {url}: {exc.read()[:400].decode('utf-8', errors='replace')}"
        except urllib.error.URLError as exc:
            return False, f"unreachable: {url} ({exc.reason})"
        except OSError as exc:
            return False, f"unreachable: {url} ({exc})"

    # -- public API ----------------------------------------------------------

    def execute(
        self,
        tool: str,
        args: Optional[Dict[str, Any]] = None,
        instance: Optional[str] = None,
        goal: Optional[str] = None,
        source: str = "live",
        observation: Optional[Dict[str, Any]] = None,
        observation_age_ms: Optional[float] = None,
        observation_source: str = "call",
    ) -> ToolResult:
        started = time.perf_counter()
        ok, validated = validate_call(tool, args)
        if not ok:
            result = ToolResult(
                tool=tool, ok=False, error=str(validated), source=source,
                latency_ms=(time.perf_counter() - started) * 1000.0)
            self._record(tool, args or {}, result, goal, observation,
                         observation_age_ms, observation_source)
            return result

        try:
            result = self._dispatch(tool, validated, source=source, instance=instance)
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller and the log
            result = ToolResult(
                tool=tool, ok=False, error=f"{type(exc).__name__}: {exc}", source=source)
        result.latency_ms = (time.perf_counter() - started) * 1000.0
        # A live directive carries the game state it ran against: the C# bridge
        # attaches a snapshot to every /directive response. Use it when the
        # caller did not supply a fresher one -- this is what turns
        # `observation: null` into a usable training row, at age 0, without a
        # second polling clock.
        if observation is None and isinstance(result.data, dict):
            snap = result.data.get("snapshot")
            if isinstance(snap, dict) and snap:
                observation, observation_age_ms, observation_source = snap, 0.0, "call"
        self._record(tool, validated, result, goal, observation,
                     observation_age_ms, observation_source)
        return result

    # -- internals -----------------------------------------------------------

    def _record(
        self,
        tool: str,
        args: Dict[str, Any],
        result: ToolResult,
        goal: Optional[str],
        observation: Optional[Dict[str, Any]],
        observation_age_ms: Optional[float] = None,
        observation_source: str = "call",
    ) -> None:
        if self.recorder is None:
            return
        self.recorder.step(
            tool=tool,
            args=args,
            ok=result.ok,
            result=result.as_dict().get("data"),
            error=result.error,
            latency_ms=result.latency_ms,
            goal=goal,
            observation=observation,
            observation_age_ms=observation_age_ms,
            observation_source=observation_source,
            source=result.source,
        )

    def _dispatch(self, tool: str, args: Dict[str, Any], source: str,
                  instance: Optional[str] = None) -> ToolResult:
        spec = TOOLS_BY_NAME[tool]
        if spec.backend == "local":
            if tool == "knowledge.search":
                return self._knowledge_search(args, source)
            if tool == "quest.list":
                return self._quest_list(args, source)
            if tool == "knowledge.remember":
                return self._knowledge_remember(args, source)
            if tool == "knowledge.forget":
                return self._knowledge_forget(args, source)
            return ToolResult(tool=tool, ok=False, error=f"no local handler for {tool}", source=source)
        if spec.backend == "sentinel":
            if tool == "nav.goto":
                return self._nav_goto(args, source)
            return ToolResult(tool=tool, ok=False, error=f"no sentinel handler for {tool}", source=source)
        if spec.backend == "directive":
            return self._directive(spec.directive or tool.upper(), tool, args, source, instance)
        return ToolResult(tool=tool, ok=False, error=f"unknown backend '{spec.backend}'", source=source)

    # -- local handlers ------------------------------------------------------

    def _knowledge_remember(self, args: Dict[str, Any], source: str) -> ToolResult:
        """Store a discovered fact. The model learns *when* to do this; the store
        keeps the fact, so the model does not have to remember it."""
        value = args.get("value")
        try:
            value = json.loads(value) if isinstance(value, str) else value
        except (ValueError, TypeError):
            pass                      # plain text is a perfectly good fact
        changed, note = self.store.remember(
            str(args.get("kind", "")), str(args.get("key", "")), value,
            source="observed", confidence=float(args.get("confidence", 0.5) or 0.5))
        return ToolResult(tool="knowledge.remember", ok=True, source=source,
                          data={"changed": changed, "note": note})

    def _knowledge_forget(self, args: Dict[str, Any], source: str) -> ToolResult:
        removed = self.store.forget(str(args.get("kind", "")), str(args.get("key", "")),
                                    str(args.get("reason", "")))
        return ToolResult(tool="knowledge.forget", ok=True, source=source,
                          data={"removed": removed})

    def _knowledge_search(self, args: Dict[str, Any], source: str) -> ToolResult:
        hits = self.knowledge.search(
            str(args.get("query", "")),
            kind=str(args.get("kind", "any")),
            limit=int(args.get("limit", 5)),
        )
        if not hits:
            available = [
                kind for kind, _ in KNOWLEDGE_FILES.items()
                if (self.knowledge.cache_dir / KNOWLEDGE_FILES[kind]).exists()
            ]
            return ToolResult(
                tool="knowledge.search", ok=True, source=source,
                data={"results": [], "note": f"no match; cached kinds: {', '.join(available) or 'none'}"},
            )
        return ToolResult(tool="knowledge.search", ok=True, data={"results": hits}, source=source)

    def _load_cache(self, filename: str) -> List[Dict[str, Any]]:
        path = self.knowledge.cache_dir / filename
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(payload, list):
            return []
        return [record for record in payload if isinstance(record, dict)]

    def _geography_index(self) -> Dict[str, Any]:
        """Lazy index over the subareas/areas caches.

        ``map_to_sub``/``quest_to_sub`` come straight from the subarea records
        (``mapIds``, ``entranceMapIds``, ``questsIds``), so the area filter works
        without any map geometry; the ``/maps`` collection is gone from the
        public mirror, the subarea collection covers the same relation.
        """
        if self._geo_index is not None:
            return self._geo_index
        index: Dict[str, Any] = {
            "areas": {}, "subareas": {}, "area_subs": {},
            "map_to_sub": {}, "quest_to_sub": {},
        }
        for subarea in self._load_cache("subareas_full.json"):
            sub_id = subarea.get("id")
            if not isinstance(sub_id, int):
                continue
            names = _localized_strings(subarea)
            if names:
                index["subareas"][sub_id] = names
            for map_id in subarea.get("mapIds") or []:
                if isinstance(map_id, int):
                    index["map_to_sub"][map_id] = sub_id
            for map_id in subarea.get("entranceMapIds") or []:
                if isinstance(map_id, int):
                    index["map_to_sub"].setdefault(map_id, sub_id)
            for quest_id in subarea.get("questsIds") or []:
                if isinstance(quest_id, int):
                    index["quest_to_sub"][quest_id] = sub_id
        for area in self._load_cache("areas_full.json"):
            area_id = area.get("id")
            if not isinstance(area_id, int):
                continue
            names = _localized_strings(area)
            if names:
                index["areas"][area_id] = names
            index["area_subs"][area_id] = [
                sub_id for sub_id in area.get("subareaIds") or []
                if isinstance(sub_id, int)]
        self._geo_index = index
        return index

    def _resolve_area_filter(self, area_norm: str) -> Dict[str, Any]:
        """Resolve a free-text area query against area/subarea names, any language."""
        index = self._geography_index()

        def matches(names: List[str]) -> bool:
            for name in names:
                name_norm = _normalize(name)
                if not name_norm:
                    continue
                if name_norm == area_norm:
                    return True
                if len(area_norm) >= 3 and area_norm in name_norm:
                    return True
                if len(name_norm) >= 3 and name_norm in area_norm:
                    return True
            return False

        matched_areas = [(aid, names) for aid, names in index["areas"].items() if matches(names)]
        matched_subareas = [(sid, names) for sid, names in index["subareas"].items() if matches(names)]
        subarea_ids = {sid for sid, _names in matched_subareas}
        for area_id, _names in matched_areas:
            subarea_ids.update(index["area_subs"].get(area_id, ()))
        quest_ids = {qid for qid, sid in index["quest_to_sub"].items() if sid in subarea_ids}
        return {
            "query": area_norm,
            "resolved": bool(subarea_ids),
            "areas": [names[0] for _id, names in matched_areas[:5]],
            "subareas": [names[0] for _id, names in matched_subareas[:5]],
            "subarea_count": len(subarea_ids),
            "_subarea_ids": subarea_ids,
            "_quest_ids": quest_ids,
        }

    def _quest_list(self, args: Dict[str, Any], source: str) -> ToolResult:
        entries = self.knowledge.entries("quest")
        if not entries:
            return ToolResult(
                tool="quest.list", ok=False, source=source,
                error="quest cache is empty — run: python -m data.sync_full_database quests")

        area_norm = _normalize(str(args.get("area", ""))).strip()
        level_min = args.get("level_min")
        level_max = args.get("level_max")
        dungeon_only = bool(args.get("dungeon_only", False))
        limit = max(1, int(args.get("limit", 10)))

        resolution: Optional[Dict[str, Any]] = None
        subarea_ids: set = set()
        quest_ids: set = set()
        map_to_sub: Dict[int, int] = {}
        if area_norm:
            resolution = self._resolve_area_filter(area_norm)
            subarea_ids = resolution.pop("_subarea_ids")
            quest_ids = resolution.pop("_quest_ids")
            if resolution["resolved"]:
                map_to_sub = self._geography_index()["map_to_sub"]

        matched: List[Dict[str, Any]] = []
        for _record_id, haystack, _names, record in entries:
            if dungeon_only and not record.get("is_dungeon"):
                continue
            quest_level = int(record.get("level_min") or 0)
            if isinstance(level_min, int) and quest_level < level_min:
                continue
            if isinstance(level_max, int) and quest_level > level_max:
                continue
            if area_norm:
                if subarea_ids:
                    in_area = int(record.get("id") or 0) in quest_ids
                    if not in_area:
                        for start in record.get("start_position") or []:
                            map_id = start.get("mapId") if isinstance(start, dict) else None
                            if isinstance(map_id, int) and map_to_sub.get(map_id) in subarea_ids:
                                in_area = True
                                break
                    if not in_area and area_norm not in haystack:
                        continue
                elif area_norm not in haystack:
                    continue
            matched.append(record)

        matched.sort(key=lambda r: (int(r.get("level_min") or 0), int(r.get("id") or 0)))
        data: Dict[str, Any] = {
            "count": len(matched),
            "quests": [_compact("quest", r) for r in matched[:limit]],
        }
        if resolution is not None:
            data["area"] = resolution
            if not resolution["resolved"]:
                data["note"] = ("area not found in the local cache; "
                                "showing quests whose name contains the text")
        return ToolResult(tool="quest.list", ok=True, data=data, source=source)

    # -- sentinel handlers ---------------------------------------------------

    def _zaaps(self) -> List[Dict[str, Any]]:
        if self._zaaps_cache is not None:
            return self._zaaps_cache
        ok, payload = self._http_json("GET", f"{self.sentinel_url}/sentinel/navigation/zaaps")
        zaaps: List[Dict[str, Any]] = []
        if ok and isinstance(payload, dict):
            for key in ("zaaps", "data"):
                candidate = payload.get(key)
                if isinstance(candidate, list):
                    zaaps = [z for z in candidate if isinstance(z, dict)]
                    break
        self._zaaps_cache = zaaps
        return zaaps

    def _resolve_zaap(self, name: str) -> Optional[Tuple[int, int, str]]:
        name_norm = _normalize(name)
        for zaap in self._zaaps():
            strings = _localized_strings(zaap)
            # coords live under various shapes; probe the known ones
            x = zaap.get("x", zaap.get("posX"))
            y = zaap.get("y", zaap.get("posY"))
            coord = zaap.get("coord") or zaap.get("coords") or zaap.get("position")
            if isinstance(coord, dict):
                x = coord.get("x", x)
                y = coord.get("y", y)
            elif isinstance(coord, list) and len(coord) == 2:
                x, y = coord[0], coord[1]
            if not isinstance(x, int) or not isinstance(y, int):
                continue
            for text in strings:
                text_norm = _normalize(text)
                if name_norm and (name_norm in text_norm or text_norm in name_norm):
                    return x, y, text
        return None

    def _nav_goto(self, args: Dict[str, Any], source: str) -> ToolResult:
        target: Optional[Tuple[int, int]] = None
        target_name = ""
        if "x" in args and "y" in args:
            target = (int(args["x"]), int(args["y"]))
            target_name = f"[{target[0]}, {target[1]}]"
        elif args.get("zaap"):
            resolved = self._resolve_zaap(str(args["zaap"]))
            if resolved is None:
                return ToolResult(
                    tool="nav.goto", ok=False, source=source,
                    error=("could not resolve zaap name against the Sentinel zaap table "
                           "(is the Sentinel running on 8890 and is the name right?)"))
            x, y, matched = resolved
            target = (x, y)
            target_name = f"zaap {matched} [{x}, {y}]"
        else:
            return ToolResult(
                tool="nav.goto", ok=False, source=source,
                error="npc/map_id routing needs the world export (P0.5); use x+y or zaap")

        body = {
            "from": [int(args.get("from_x", 4)), int(args.get("from_y", -19))],
            "to": [target[0], target[1]],
            "allow_zaap": True,
            "lang": str(args.get("lang", "fr")),
        }
        ok, payload = self._http_json("POST", f"{self.sentinel_url}/sentinel/navigation/path", body)
        if not ok:
            return ToolResult(tool="nav.goto", ok=False, source=source,
                              error=f"sentinel unreachable: {payload}")
        return ToolResult(
            tool="nav.goto", ok=True, source=source,
            data={"target": target_name, "path": payload.get("path") if isinstance(payload, dict) else payload},
        )

    # -- directive handler ---------------------------------------------------

    def _directive(self, directive: str, tool: str, args: Dict[str, Any], source: str,
                   instance: Optional[str] = None) -> ToolResult:
        payload = {
            "id": uuid.uuid4().hex,
            "ts": round(time.time(), 3),
            "directive": directive,
            "tool": tool,
            "args": args,
            "source": source,
        }
        if instance and instance != "default":
            payload["target_instance"] = instance
        ok, response = self._http_json("POST", self.directive_url, payload)
        if not ok:
            return ToolResult(
                tool=tool, ok=False, source=source, directive=directive,
                error=(f"directive bridge at {self.directive_url} not reachable "
                       f"({response}); directive '{directive}' was NOT executed"),
                data={"directive_payload": payload},
            )
        bridge_ok = True
        bridge_error = None
        if isinstance(response, dict) and "ok" in response:
            bridge_ok = bool(response.get("ok"))
            bridge_error = response.get("error")
        result = ToolResult(
            tool=tool, ok=bridge_ok, source=source, directive=directive,
            data={"directive_payload": payload, "response": response},
        )
        # The bridge attaches the game state it ran against. Keep it on the
        # result so `execute()` can record it as the observation, and so a
        # caller that wants the raw state does not have to re-poll the client.
        if isinstance(response, dict) and isinstance(response.get("snapshot"), dict):
            result.data["snapshot"] = response["snapshot"]
        if not bridge_ok:
            result.error = f"client directive failed: {bridge_error or 'unknown error'}"
        return result
