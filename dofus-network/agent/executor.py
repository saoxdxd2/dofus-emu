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
import os
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from array import array
from bisect import bisect_right
from collections import OrderedDict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.bridge import directive_url as bridge_directive_url, opener_for
from agent.jsonio import dumps_bytes, iter_array, loads
from agent.observe import redact_packet_secrets
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


def _accent_table() -> Dict[int, str]:
    """Precomposed Latin characters -> their unaccented form.

    ``_normalize`` is called hundreds of thousands of times while indexing the
    item cache. ``NFKD`` plus a Python generator over every character was the
    entire cold-start cost; a C-level ``str.translate`` table handles the Latin
    range with identical results.
    """
    table: Dict[int, str] = {}
    for code in range(0x00C0, 0x0250):
        char = chr(code)
        decomposed = unicodedata.normalize("NFKD", char)
        stripped = "".join(ch for ch in decomposed
                           if not unicodedata.combining(ch))
        if stripped != char:
            table[code] = stripped
    return table


_ACCENT_TABLE = _accent_table()


def _normalize(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    lowered = text.lower()
    if lowered.isascii():
        return lowered
    translated = lowered.translate(_ACCENT_TABLE)
    if translated.isascii():
        return translated
    # Non-Latin scripts still go through the exact NFKD path.
    nfkd = unicodedata.normalize("NFKD", translated)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def _name_strings(record: Dict[str, Any]) -> List[str]:
    """Localised names only. Descriptions are matched but are not names."""
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
    return out


def _description_strings(record: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    description = record.get("description")
    if isinstance(description, dict):
        out.extend(str(v) for v in description.values() if isinstance(v, str))
    elif isinstance(description, str):
        out.append(description)
    return out


def _localized_strings(record: Dict[str, Any]) -> List[str]:
    """All human-readable strings of a record (localised names + description)."""
    return _name_strings(record) + _description_strings(record)


_INDEX_FIELDS = {
    "item": ("id", "name", "level", "type_id", "price", "is_saleable"),
    "spell": ("id", "name_fr", "name_en", "name_es", "type_id"),
    "npc": ("id", "name", "actions"),
    "achievement": ("id", "name", "description", "points", "level"),
    "dungeon": ("id", "name", "level_min", "entrance_map_id",
                "boss_monster_ids"),
    "monster": ("id", "name", "name_fr", "name_en", "name_es", "name_de",
                "is_boss"),
}


def _index_record(kind: str, record: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only fields a search result can expose; quests stay whole.

    ``entries`` is a process-long cache. Retaining every raw item/monster row
    after its normalized names are built was measurable resident memory for no
    consumer.
    """
    fields = _INDEX_FIELDS.get(kind)
    if fields is None:
        return record
    stored = {key: record.get(key) for key in fields}
    if kind == "monster":
        stored["is_boss"] = bool(record.get("is_boss", False))
        grades = record.get("grades") or []
        stored["grades"] = [
            {"level": grade.get("level"), "life_points": grade.get("life_points")}
            for grade in grades if isinstance(grade, dict)
        ]
    return stored


def payload_of(result: Any) -> Dict[str, Any]:
    """The client's own payload, out of the two wrappers the bridge puts around it.

    A live directive comes back as `{"response": {"result": {"data": {...}}}}`. A
    caller that reaches for one field with `result.data.get("via")` gets None and no
    error -- which is exactly how a talk receipt went unread for a whole session.
    """
    data = getattr(result, "data", None)
    if not isinstance(data, dict):
        return {}
    inner = data.get("response")
    inner = inner.get("result") if isinstance(inner, dict) else None
    inner = inner.get("data") if isinstance(inner, dict) else None
    return inner if isinstance(inner, dict) else {}


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


# Order is the tie-break when a BFS step has several equal-length routes.
_MAP_DIRECTIONS: Tuple[Tuple[str, str], ...] = (
    ("top", "topNeighbourId"),
    ("right", "rightNeighbourId"),
    ("bottom", "bottomNeighbourId"),
    ("left", "leftNeighbourId"),
)


class _MapGraph:
    """Routing graph over the exported maps (`data/maps/_index.json`).

    The client can only do the last step: walk to a border and cross it. It knows
    the exits of the map it is standing on and nothing else, so any route longer
    than one map has to be planned on this side, where the export is available.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else REPO_ROOT / "data" / "maps" / "_index.json"
        self._lock = threading.RLock()
        self._edges: Optional[Dict[int, Dict[str, int]]] = None
        self._reverse_edges: Optional[Dict[int, List[Tuple[int, str]]]] = None
        # A journey asks for the same target map on every step while the
        # character crosses one map at a time. The BFS tree rooted at the target
        # answers every hop in O(1); four recent targets are enough.
        self._trees: "OrderedDict[int, Dict[int, Tuple[str, int, int]]]" = OrderedDict()
        self._tree_limit = 4

    def edges(self) -> Dict[int, Dict[str, int]]:
        with self._lock:
            if self._edges is not None:
                return self._edges
            edges: Dict[int, Dict[str, int]] = {}
            try:
                payload = loads(self.path.read_bytes())
            except (OSError, ValueError) as exc:
                logger.warning("map index unavailable (%s): %s", self.path, exc)
                payload = {}
            items = payload.items() if isinstance(payload, dict) else ()
            for key, record in items:
                if not isinstance(record, dict):
                    continue
                try:
                    map_id = int(record.get("id", key))
                except (TypeError, ValueError):
                    continue
                neighbours = record.get("neighbours") or {}
                exits: Dict[str, int] = {}
                for direction, field_name in _MAP_DIRECTIONS:
                    try:
                        neighbour_id = int(neighbours.get(field_name))
                    except (TypeError, ValueError):
                        continue
                    if neighbour_id > 0:
                        exits[direction] = neighbour_id
                if exits:
                    edges[map_id] = exits
            self._edges = edges
            return edges

    def observe(self, map_id: Optional[int], exits: Dict[str, Any]) -> bool:
        """Let the client correct the export for the map it is standing on.

        The index is a static file; the client is the authority. Live 2026-10-29,
        map 145490434: the export says `right -> 145490946`, the client's own exit
        cells say `right -> 145490945`, and the page refused the hop six times
        ("the right exit leads to 145490945, not 145490946"). Directions the live
        block does not mention are kept, so an observation can only correct the
        edges it actually reports -- never delete the rest. Returns whether an
        edge changed, so a caller can tell a correction from a repeat.
        """
        try:
            map_id = int(map_id) if map_id is not None else 0
        except (TypeError, ValueError):
            return False
        if map_id <= 0 or not isinstance(exits, dict):
            return False
        live: Dict[str, int] = {}
        for direction, neighbour in exits.items():
            if direction not in ("top", "right", "bottom", "left"):
                continue
            try:
                neighbour = int(neighbour)
            except (TypeError, ValueError):
                continue
            # A self-edge is what the client publishes for a border it does not
            # have (map 145490434 reports `bottom -> itself`); it is not a route.
            if neighbour > 0 and neighbour != map_id:
                live[str(direction)] = neighbour
        if not live:
            return False
        with self._lock:
            edges = self.edges()
            merged = dict(edges.get(map_id) or {})
            changed = False
            for direction, neighbour in live.items():
                if merged.get(direction) != neighbour:
                    merged[direction] = neighbour
                    changed = True
            if not changed:
                return False
            edges[map_id] = merged
            self._reverse_edges = None
            self._trees.clear()
            return True

    def reverse_edges(self) -> Dict[int, List[Tuple[int, str]]]:
        """Neighbour -> (previous map, direction) for reverse BFS trees."""
        with self._lock:
            cached = self._reverse_edges
            if cached is not None:
                return cached
        reverse: Dict[int, List[Tuple[int, str]]] = {}
        for node, exits in self.edges().items():
            for direction, neighbour in exits.items():
                reverse.setdefault(neighbour, []).append((node, direction))
        with self._lock:
            self._reverse_edges = reverse
        return reverse

    def _tree(self, target: int) -> Dict[int, Tuple[str, int, int]]:
        """BFS tree rooted at ``target``: node -> (direction, next map, hops).

        Built on the reversed graph so the direction stored on each node is the
        one the character should take from that node. Cached per target: a
        multi-map journey reuses the same destination for many steps.
        """
        with self._lock:
            cached = self._trees.get(target)
            if cached is not None:
                self._trees.move_to_end(target)
                return cached
        tree: Dict[int, Tuple[str, int, int]] = {target: ("", target, 0)}
        queue: deque = deque([target])
        while queue:
            node = queue.popleft()
            distance = tree[node][2] + 1
            for previous, direction in self.reverse_edges().get(node, ()):
                if previous in tree:
                    continue
                tree[previous] = (direction, node, distance)
                queue.append(previous)
        with self._lock:
            self._trees[target] = tree
            while len(self._trees) > self._tree_limit:
                self._trees.popitem(last=False)
        return tree

    def _hop_bfs(self, here: int, target: int,
                 blocked_first: set) -> Optional[Tuple[str, int, int]]:
        """Forward BFS for the rare gated-first-step case.

        The cached tree gives the shortest path without constraints, but that
        path may start through a direction the tutorial blocks. Its distance is
        also unsafe to reuse for an alternative neighbour: the tree path from
        that neighbour can run back through `here`, which the forward search
        must not do. This bounded BFS keeps the old, correct behaviour.
        """
        edges = self.edges()
        parent: Dict[int, Tuple[Optional[int], Optional[str]]] = {here: (None, None)}
        queue: deque = deque([here])
        while queue:
            node = queue.popleft()
            if node == target:
                break
            for direction, neighbour in edges.get(node, {}).items():
                if node == here and direction in blocked_first:
                    continue
                if neighbour in parent:
                    continue
                parent[neighbour] = (node, direction)
                queue.append(neighbour)
        if target not in parent:
            return None
        path: List[str] = []
        node = target
        while parent[node][0] is not None:
            previous, direction = parent[node]
            path.append(direction)
            node = previous
        path.reverse()
        if not path:
            return None
        return path[0], edges[here][path[0]], len(path)

    def hop(self, here: Optional[int], target: Optional[int],
            blocked_first: Optional[set] = None) -> Optional[Tuple[str, int, int]]:
        """First step from `here` towards `target`: (direction, next map, hops left).

        `blocked_first` names directions the tutorial forbids on the current map.
        They are skipped for the first step only: the gate belongs to the scenario
        step, not to the other maps on the route.
        """
        if here is None or target is None or here == target:
            return None
        tree = self._tree(target)
        entry = tree.get(here)
        if entry is None:
            return None
        direction, next_map, hops = entry
        blocked_first = blocked_first or set()
        if direction in blocked_first:
            return self._hop_bfs(here, target, blocked_first)
        return direction, next_map, hops


def _obs_map_id(observation: Optional[Dict[str, Any]]) -> Optional[int]:
    """The current map id from any snapshot shape the bridge has used."""
    if not isinstance(observation, dict):
        return None
    map_block = observation.get("map")
    candidates = [
        observation.get("map_id"),
        observation.get("position", {}).get("map_id")
        if isinstance(observation.get("position"), dict) else None,
        map_block.get("map_id") if isinstance(map_block, dict) else None,
    ]
    for value in candidates:
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _obs_exit_map(observation: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """direction -> neighbour map id, from the client's own exit list.

    Blocked exits and self-edges are left out: the first cannot be crossed and
    the second is not a crossing at all.
    """
    if not isinstance(observation, dict):
        return {}
    map_block = observation.get("map")
    if not isinstance(map_block, dict):
        return {}
    here = map_block.get("map_id")
    try:
        here = int(here) if here is not None else None
    except (TypeError, ValueError):
        here = None
    exits: Dict[str, int] = {}
    for entry in map_block.get("exits") or []:
        if not isinstance(entry, dict) or entry.get("blocked"):
            continue
        direction = entry.get("direction")
        if not direction:
            continue
        try:
            neighbour = int(entry.get("map_id"))
        except (TypeError, ValueError):
            continue
        if neighbour <= 0 or (here is not None and neighbour == here):
            continue
        exits[str(direction)] = neighbour
    return exits


def _obs_map_exits(observation: Optional[Dict[str, Any]]) -> Dict[int, str]:
    """neighbour map id -> direction, straight from the client's own exit list.

    Exits the tutorial marks `blocked` are left out: the client refuses those
    crossings, so routing through one walks the character to a border it cannot
    cross.
    """
    if not isinstance(observation, dict):
        return {}
    map_block = observation.get("map")
    if not isinstance(map_block, dict):
        return {}
    exits: Dict[int, str] = {}
    for entry in map_block.get("exits") or []:
        if not isinstance(entry, dict) or entry.get("blocked"):
            continue
        try:
            neighbour = int(entry.get("map_id"))
        except (TypeError, ValueError):
            continue
        direction = entry.get("direction")
        if direction:
            exits[neighbour] = str(direction)
    return exits


def _obs_blocked_directions(observation: Optional[Dict[str, Any]]) -> set:
    """Directions the tutorial currently disables on the observed map."""
    if not isinstance(observation, dict):
        return set()
    map_block = observation.get("map")
    if not isinstance(map_block, dict):
        return set()
    blocked = set()
    for entry in map_block.get("exits") or []:
        if isinstance(entry, dict) and entry.get("blocked") and entry.get("direction"):
            blocked.add(str(entry["direction"]))
    return blocked


class _KnowledgeIndex:
    """Lazy, thread-safe index over the DofusDB cache files."""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self._lock = threading.Lock()
        self._entries: Dict[
            str, List[Tuple[int, Tuple[str, ...], List[str], Any, str]]] = {}
        # One UTF-8 blob per kind plus a row offset array, instead of a list of 21,554 joined
        # strings and their description tuples. A UTF-8 byte search is exactly a str search here:
        # a needle's first byte is either ASCII or a lead byte, and continuation bytes are never
        # either, so a hit cannot land mid-character.
        self._hay: Dict[str, Tuple[array, bytes]] = {}
        # One int32 per row: the byte offset where that row's names end inside the haystack blob.
        self._nend: Dict[str, array] = {}
        # The same shape for the fields a result exposes. `_compact` reads a projected record for
        # at most `limit` winners per query, but every row used to carry its own dict: 20.7 MB of
        # live objects for 21,554 items, priced in §L.3. Here the projection is one JSON blob per
        # kind with int32 row offsets, and only the winners are decoded.
        self._val: Dict[str, Tuple[array, bytes]] = {}
        self._search_cache: "OrderedDict[Tuple[str, str, int], List[Dict[str, Any]]]" = OrderedDict()

    def entries(
        self, kind: str
    ) -> List[Tuple[int, Tuple[str, ...], List[str], Any, str]]:
        with self._lock:
            cached = self._entries.get(kind)
            if cached is not None:
                return cached
            name = KNOWLEDGE_FILES.get(kind)
            if not name:
                # An unmapped kind used to resolve to the cache *directory*, and `path.exists()`
                # is true for a directory -- so the first query for a kind we do not index logged
                # "Unreadable cache file C:\...\data\cache: Permission denied" as if a data file
                # were corrupt. Nothing is indexed under an unknown kind; say so by returning empty.
                self._entries[kind] = []
                return self._entries[kind]
            path = self.cache_dir / name
            entries: List[Tuple[int, Tuple[str, ...], List[str], Any, str]] = []
            offsets = array("I", [0])
            nend = array("I")
            blob = bytearray()
            projected = _INDEX_FIELDS.get(kind) is not None
            val_offsets = array("I", [0])
            val_blob = bytearray()
            try:
                # Streamed, not `loads()`: the item cache is one 24 MB array, and the whole-tree
                # parse was the largest single block of the index's committed memory. Each record's
                # searchable text goes straight into the kind's byte blob, so a description string
                # dies with its record instead of surviving as one of 21,554 tuples.
                for record in iter_array(path):
                    if not isinstance(record, dict):
                        continue
                    names_norm = [_normalize(n) for n in _name_strings(record) if n]
                    # Descriptions stay searchable in the haystack but do not get
                    # the exact/prefix *name* bonus; keeping a second normalized
                    # copy of them in names_norm duplicated 86% of the item index
                    # text for a bonus a description should not receive.
                    #
                    # The row's own text layout is
                    # `name\x00name\x00…name \x01 description description …`
                    # with one int32 per row marking the end of its names. The list of
                    # normalized names and the joined name string used to be held per row
                    # -- measured 2026-11-03, 14.28 MB of lists plus 4.92 MB of joined
                    # strings across the seven kinds, for a scan that only ever asks
                    # "is this needle in this row's names" plus, for the few rows that say
                    # yes, "does one of them start with / equal it". `\x00` between names is
                    # what makes that recoverable: joining with a space, as the old string
                    # did, lets a needle containing a space match across two names and hand
                    # the name bonus to a row that has no such name.
                    blob.extend("\x00".join(names_norm).encode("utf-8"))
                    nend.append(len(blob))
                    blob.append(1)
                    descs = [_normalize(d) for d in _description_strings(record) if d]
                    if descs:
                        blob.extend(" ".join(descs).encode("utf-8"))
                    offsets.append(len(blob))
                    stored = _index_record(kind, record)
                    if projected:
                        val_blob.extend(json.dumps(stored, ensure_ascii=False,
                                                    separators=(",", ":")).encode("utf-8"))
                        val_offsets.append(len(val_blob))
                        slot: Any = len(entries)
                    else:
                        # Quests are read field-by-field while filtering (is_dungeon, level_min,
                        # start_position), so their whole record stays materialized.
                        slot = stored
                    entries.append((int(record.get("id") or 0), (), (), slot, ()))
            except (ValueError, OSError) as exc:
                logger.warning(f"Unreadable cache file {path}: {exc}")
            # `bytes(...)` copies each buffer down to its exact length. Keeping the bytearray
            # instead -- the obvious next saving, since it drops a duplicate of 17.3 + 7.7 MB --
            # was measured and is worse: commit after all seven kinds went 77.6 -> 80.3 MB with
            # residency unchanged, because an over-grown bytearray keeps ~2 MB of spare capacity
            # per blob that a fixed copy does not.
            self._hay[kind] = (offsets, bytes(blob))
            self._nend[kind] = nend
            if projected:
                self._val[kind] = (val_offsets, bytes(val_blob))
            self._entries[kind] = entries
            return entries

    def _rows_with(self, offsets: array, blob: bytes, needle: bytes,
                   ends: array, end_shift: int = 0) -> Dict[int, List[int]]:
        """Rows that fully contain an occurrence of `needle`, with each row's byte offsets.

        The old shape of this scan asked every row, one at a time, `blob.find(needle, start, end)`:
        measured 2026-11-03 on the item kind, 21,554 of those bounded searches cost **48 ms**, and
        the *same* question asked once to the whole 15.4 MB blob costs **0.04 ms** -- the cost was
        the Python-level loop, never the searching. So the blob is scanned once and each occurrence
        is mapped to the row it starts in, keeping the containment rule the bounded search had:
        an occurrence belongs to a row only if it both starts and ends inside that row's region,
        which is what stops the tail of one record matching against the head of the next
        (`lenda.epee d` is a real crossing of two item rows and must not match either).

        The offsets come back with the rows because the two name tests are byte questions, not
        string questions: a name *starts* with the query when an occurrence sits at the region start
        or right after a `\\x00`, and *equals* it when it also ends at the region end or right
        before one. Decoding the names of every candidate row instead cost more than the scan it
        replaced -- 18 ms against 7.8 ms on a common word like `bague`, thousands of candidates.

        `ends` is the array holding each row's end offset: the names-end array (`end_shift=0`) or
        the row offsets themselves, where row r ends at `offsets[r + 1]` (`end_shift=1`). The
        shift is a parameter rather than a copied list because a copied list of bounds is another
        0.6 MB of ints per kind.
        """
        found: Dict[int, List[int]] = {}
        if not needle:
            return found
        span = len(needle)
        count = len(offsets) - end_shift
        position = 0
        while True:
            hit = blob.find(needle, position)
            if hit < 0:
                return found
            position = hit + 1                   # overlapping starts, so a fit after a straddle counts
            row = bisect_right(offsets, hit) - 1
            if 0 <= row < count and hit + span <= ends[row + end_shift]:
                found.setdefault(row, []).append(hit)

    @staticmethod
    def _at_name_start(blob: bytes, start: int, position: int) -> bool:
        return position == start or blob[position - 1] == 0

    @staticmethod
    def _ends_region(blob: bytes, position: int, span: int, end: int) -> bool:
        return position + span == end or (position + span < end and
                                          blob[position + span] == 0)

    def _names(self, kind: str, index: int) -> List[str]:
        """The row's normalized names, recovered from its slice of the blob.

        Called only for rows that already matched the query inside their names, so the split is
        paid for candidates, never for the whole kind.
        """
        offsets, blob = self._hay_for(kind)
        nend = self._nend.get(kind)
        if nend is None or index >= len(nend):
            return []
        start = offsets[index]
        end = nend[index]
        if end <= start:
            return []
        return blob[start:end].decode("utf-8", "replace").split("\x00")

    def _stored(self, kind: str, slot: Any) -> Dict[str, Any]:
        """The projected record for one row, decoded only when a query is about to return it."""
        store = self._val.get(kind)
        if store is None:
            return slot if isinstance(slot, dict) else {}
        offsets, blob = store
        return json.loads(blob[offsets[slot]:offsets[slot + 1]])

    def _hay_for(self, kind: str) -> Tuple[array, bytes]:
        """(row offsets, UTF-8 text) for one kind: the searchable corpus, copied once.

        `offsets` has one more entry than there are rows, so row *i* spans
        `blob[offsets[i]:offsets[i + 1]]` -- and a test against it is
        `blob.find(needle, start, end)`, one C call that allocates nothing. A UTF-8 byte search is
        exactly equivalent to the string search this used to do: a needle's first byte is either
        ASCII or a lead byte, and UTF-8 continuation bytes are neither, so a hit cannot land in the
        middle of a character.
        """
        return self._hay.get(kind) or (array("I", [0]), b"")

    def search(
        self,
        query: str,
        kind: str = "any",
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        query_norm = _normalize(query).strip()
        if not query_norm:
            return []
        max_results = max(1, int(limit))
        cache_key = (kind, query_norm, max_results)
        with self._lock:
            cached = self._search_cache.get(cache_key)
            if cached is not None:
                self._search_cache.move_to_end(cache_key)
                return list(cached)
        tokens = [t for t in query_norm.split() if len(t) >= 2]
        kinds = _ANY_KIND_ORDER if kind == "any" else (kind,)
        # A single token equal to the whole query cannot score when the query
        # is absent from the haystack: every other term is a substring of the
        # query. The haystack check is one C call; the old names-list scan ran
        # first on every entry, including the overwhelming majority that do
        # not match at all.
        single_token = len(tokens) == 1 and tokens[0] == query_norm
        token_count = len(tokens)

        # A name-prefix match always outranks anything that only matches a
        # description, an internal name substring, or individual tokens. When
        # at least `limit` names start with the query, the top results are
        # fully determined by the small name haystacks; the 16 MB description
        # haystack does not need to be scanned.
        if tokens:
            bonus: List[Tuple[float, int, str, Dict[str, Any]]] = []
            bonus_needle = query_norm.encode("utf-8")
            span = len(bonus_needle)
            # This pass reads only each row's *names*, which is a few hundred kilobytes in total,
            # so it stays a per-row bounded search: scanning the whole 15 MB blob instead (the
            # trick the row loop below uses) enumerated every occurrence inside the descriptions
            # as well and cost 7.4 -> 15.6 ms on a common word like `epee`. The answer is decided
            # on byte boundaries rather than by decoding the names.
            for k in kinds:
                rows_k = self.entries(k)
                offsets, hay = self._hay_for(k)
                nend = self._nend.get(k)
                if nend is None:
                    continue
                for index in range(len(rows_k)):
                    start = offsets[index]
                    stop = nend[index]
                    if stop <= start:
                        continue
                    position = hay.find(bonus_needle, start, stop)
                    if position < 0:
                        continue
                    prefix = exact = False
                    while position >= 0 and position < stop:
                        if self._at_name_start(hay, start, position):
                            prefix = True
                            if self._ends_region(hay, position, span, stop):
                                exact = True
                                break
                        position = hay.find(bonus_needle, position + 1, stop)
                    if not prefix:
                        continue
                    score = 6.0 if exact else 4.0
                    score += 2.0 + 1.5 + 0.5 * token_count
                    bonus.append((score, rows_k[index][0], k, rows_k[index][3]))
            if len(bonus) >= max_results:
                bonus.sort(key=lambda item: (-item[0], item[1]))
                out = [
                    {"kind": k, "score": round(score, 2),
                     **_compact(k, self._stored(k, record))}
                    for score, _record_id, k, record in bonus[:max_results]
                ]
                return self._cache_results(cache_key, out)

        results: List[Tuple[float, int, str, Dict[str, Any]]] = []
        append = results.append
        needle = query_norm.encode("utf-8")
        span = len(needle)
        encoded_tokens = [token.encode("utf-8") for token in tokens]
        for k in kinds:
            entries = self.entries(k)
            offsets, blob = self._hay_for(k)
            nend = self._nend.get(k) or array("I", [0])
            # `offsets` holds one more bound than there are rows, so row r ends at offsets[r + 1];
            # `_rows_with` takes that shift instead of a copied array, because a copied bound list
            # would be another 0.6 MB of ints per kind -- the thing this index keeps removing.
            full_rows = self._rows_with(offsets, blob, needle, offsets, end_shift=1)
            token_rows: List[Dict[int, List[int]]] = []
            if not single_token and tokens:
                token_rows = [self._rows_with(offsets, blob, token, offsets, end_shift=1)
                              for token in encoded_tokens]
            candidates = set(full_rows)
            for rows_hit in token_rows:
                candidates |= set(rows_hit)
            for index in candidates:
                record_id, _descriptions, _names, record, _j = entries[index]
                if index not in full_rows:
                    token_hits = sum(1 for rows_hit in token_rows if index in rows_hit)
                    if not token_hits:
                        continue
                    score = 1.5 if token_hits == token_count else 0.0
                    score += 0.5 * token_hits
                    append((score, record_id, k, record))
                    continue
                # A prefix hit settles the name score, and exact equality is the same question
                # answered on bytes: the occurrence must start on a name boundary and, for the
                # exact tier, end on one. No row's names are decoded just to rank it.
                start = offsets[index]
                stop = nend[index] if index < len(nend) else start
                positions = [p for p in full_rows[index] if p < stop]
                if any(self._at_name_start(blob, start, p) for p in positions):
                    exact = any(self._at_name_start(blob, start, p)
                                and self._ends_region(blob, p, span, stop) for p in positions)
                    score = 6.0 if exact else 4.0
                else:
                    score = 0.0
                score += 2.0
                if tokens:
                    # The full query is present, so every token is too.
                    score += 1.5 + 0.5 * token_count
                append((score, record_id, k, record))

        results.sort(key=lambda item: (-item[0], item[1]))
        out = [
            {"kind": k, "score": round(score, 2),
             **_compact(k, self._stored(k, record))}
            for score, _record_id, k, record in results[:max_results]
        ]
        return self._cache_results(cache_key, out)

    def _cache_results(self, cache_key: Tuple[str, str, int],
                       out: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        with self._lock:
            self._search_cache[cache_key] = out
            while len(self._search_cache) > 128:
                self._search_cache.popitem(last=False)
        return list(out)


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
        directive_url: Optional[str] = None,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        timeout: float = 6.0,
        knowledge_store: Optional[Path] = None,
        recorder: Optional[TrajectoryRecorder] = None,
        fight_advisor=None,
    ) -> None:
        self.sentinel_url = sentinel_url.rstrip("/")
        self.directive_url = directive_url or bridge_directive_url()
        self.timeout = float(timeout)
        # A wedged page answers the TCP connect and then takes the full C# wait
        # (10 s) for every directive, so a loop that keeps asking pays the stall
        # again and again. Two consecutive stalls open this circuit for 15 s and
        # every call in that window fails immediately; one probe afterwards
        # closes it again. Normal calls never touch it.
        self._bridge_fails = 0
        self._bridge_open_until = 0.0
        self._bridge_last_error = None
        self.recorder = recorder
        self.knowledge = _KnowledgeIndex(Path(cache_dir))
        # Discovered facts live here, not in the model and not in the cache. The
        # path is injectable so a test never writes to the user's real store.
        self.store = KnowledgeStore(knowledge_store)
        # The fight estimator is shared with the autopilot so the guard and the
        # tool answer from the same character sheet and the same cached estimates.
        self.fights = fight_advisor
        self._zaaps_cache: Optional[List[Dict[str, Any]]] = None
        self._geo_index: Optional[Dict[str, Any]] = None
        self._maps = _MapGraph()
        # The autopilot already has the canonical SQLite store open. Prefer it
        # for name -> id lookups so a named farm target does not build the 51 MiB
        # JSON entity index; the index remains the fallback.
        self.canonical_store: Any = None
        self._fight_value_cache: Optional[Tuple[Any, Any, Path]] = None

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
            data = dumps_bytes(body)
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            # Loopback must not go through HTTP_PROXY. With a VPN or a local proxy
            # client running, HTTP_PROXY is set machine-wide, and urlopen() sent every
            # directive to the bridge *through that proxy* — an extra hop, a second
            # failure point, and a 502 whenever the proxy could not reach a port that
            # only listens on this machine.
            with opener_for(url).open(request, timeout=self.timeout) as response:
                raw = response.read()
                try:
                    return True, loads(raw)
                except ValueError:
                    return True, {"raw": raw.decode("utf-8", errors="replace")[:4000]}
        except urllib.error.HTTPError as exc:
            return False, f"HTTP {exc.code} from {url}: {exc.read()[:400].decode('utf-8', errors='replace')}"
        except urllib.error.URLError as exc:
            return False, f"unreachable: {url} ({exc.reason})"
        except OSError as exc:
            return False, f"unreachable: {url} ({exc})"

    def _note_bridge_answer(self, ok: bool, error: str) -> None:
        """Count client-page stalls; two in a row open the fast-fail circuit.

        Only failures that mean *no page answered* count. A routing mistake (a
        stale instance name while other windows are alive) must not stop the
        shared executor from serving the instances that are fine.
        """
        text = (error or "").lower()
        transport = not ok and any(
            marker in text for marker in
            ("timed out", "not ready", "not reachable", "no client instance is running"))
        if transport:
            self._bridge_fails += 1
            self._bridge_last_error = (error or "timeout")[:120]
            if self._bridge_fails >= 2:
                self._bridge_open_until = time.monotonic() + 15.0
                self._bridge_fails = 0
            return
        self._bridge_fails = 0
        self._bridge_open_until = 0.0
        self._bridge_last_error = None

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
        meta: Optional[Dict[str, Any]] = None,
    ) -> ToolResult:
        started = time.perf_counter()
        ok, validated = validate_call(tool, args)
        if not ok:
            result = ToolResult(
                tool=tool, ok=False, error=str(validated), source=source,
                latency_ms=(time.perf_counter() - started) * 1000.0)
            self._record(tool, args or {}, result, goal, observation,
                         observation_age_ms, observation_source, meta)
            return result

        try:
            routed, route = self._route_nav(validated, observation) if (
                tool == "nav.goto" and isinstance(validated, dict)) else (validated, None)
            if route and route.get("already_here"):
                result = ToolResult(tool=tool, ok=True, source=source, data=route)
            else:
                result = self._dispatch(tool, routed, source=source, instance=instance,
                                        observation=observation)
                if route and isinstance(result.data, dict):
                    result.data["route"] = route
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
                     observation_age_ms, observation_source, meta)
        return result

    def snapshot(self, instance: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Read the current game state without recording it as a step.

        Observation polling is not a decision. Recording it would put a
        ``state.snapshot`` row in the corpus for every re-read the loop makes
        while waiting for a walk to land, and a policy trained on those learns
        to spend turns looking instead of acting.
        """
        try:
            result = self._dispatch("state.snapshot", {}, source="live", instance=instance)
        except Exception:  # noqa: BLE001 - a probe must never kill the loop
            return None
        if not result.ok or not isinstance(result.data, dict):
            return None
        snap = result.data.get("snapshot")
        if isinstance(snap, dict) and snap:
            return redact_packet_secrets(snap)
        response = result.data.get("response")
        inner = response.get("result") if isinstance(response, dict) else None
        if isinstance(inner, dict):
            data = inner.get("data")
            if isinstance(data, dict) and data:
                return redact_packet_secrets(data)
        return None

    def fight_state(self, instance: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Read the live combat board without recording it as a step.

        Same contract as ``snapshot``: a read is not a decision. This exists so combat
        code has one place that knows the bridge wraps the client payload as
        ``response["result"]["data"]`` instead of every caller rediscovering it.
        """
        try:
            result = self._dispatch("fight.state", {}, source="live", instance=instance)
        except Exception:  # noqa: BLE001 - a probe must never kill the loop
            return None
        if not result.ok or not isinstance(result.data, dict):
            return None
        response = result.data.get("response")
        inner = response.get("result") if isinstance(response, dict) else None
        data = inner.get("data") if isinstance(inner, dict) else None
        return data if isinstance(data, dict) and data else None

    def _attach_observation(self, result: ToolResult) -> None:
        """Attach the normalized observation and contract to a fight.state read.

        This is the live half of the training/live contract: the model gets the
        facts the client actually sent (level, HP, spells, enemy spells) plus a
        contract naming what is missing, so it cannot fill a low-level character
        with a remembered level-200 build. A read failure is reported, not
        raised: the raw fight state is still useful evidence.

        The observation is the slim one -- the four board keys plus the contract.
        The raw payload this is attached to already carries the fighters, the
        spells and ``my_stats``, and the model only ever sees 1,200 characters of
        a tool result, so serializing all of it again here wrote 30 KB per row
        into ``fight_boards.jsonl`` that no tool reads back (ARCH_REASSESSMENT.md
        §L.34). ``DOFUS_FIGHT_OBSERVATION=full`` restores the full view.
        """
        if not result.ok or not isinstance(result.data, dict):
            return
        response = result.data.get("response")
        inner = response.get("result") if isinstance(response, dict) else None
        data = inner.get("data") if isinstance(inner, dict) else None
        if not isinstance(data, dict):
            return
        observation = None
        try:
            from combat.battle_solver import Board
            from combat.observation import live_view

            board = Board.from_fight_state(data)
            observation, contract = live_view(board)
        except Exception as exc:  # noqa: BLE001 - a read probe must not kill the loop
            contract = {
                "version": 1, "ok": False, "actionable": False,
                "value_ready": False,
                "errors": [f"observation_failed:{type(exc).__name__}: {exc}"],
                "warnings": [],
            }
        if observation is not None:
            data["observation"] = observation
        data["contract"] = contract
        result.data["observation"] = observation
        result.data["contract"] = contract

    def _fight_value(self):
        """Load the learned encounter value head once, or report why not.

        The path is overridable so an experiment can point the live agent at a
        candidate checkpoint without touching the installed default.
        """
        if self._fight_value_cache is not None:
            return self._fight_value_cache
        from combat.encounter.checkpoint import load_params_config

        configured = os.environ.get("DOFUS_FIGHT_VALUE")
        path = Path(configured) if configured else REPO_ROOT / "models" / "encounter_selfplay.pkl"
        if not path.exists():
            self._fight_value_cache = (None, None, path)
            return self._fight_value_cache
        try:
            params, cfg = load_params_config(path)
            self._fight_value_cache = (params, cfg, path)
        except Exception as exc:  # noqa: BLE001 - a bad checkpoint must not kill the fight loop
            logger.warning("fight value checkpoint unavailable (%s): %s", path, exc)
            self._fight_value_cache = (None, None, path)
        return self._fight_value_cache

    def fight_advisor(self):
        """The shared estimator, created on first use.

        Loading the class spellbook and the canonical DB costs seconds, so it is
        deferred until something actually asks about a monster.
        """
        if self.fights is None:
            from combat.fight_estimate import FightAdvisor

            self.fights = FightAdvisor()
        return self.fights

    def character_report(self, instance: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Read the live character sheet (stats, spells, gear) without acting.

        Same contract as ``snapshot``: a read is not a decision, so this never
        records a step. The payload is exactly what the advisor's rules consume.
        """
        try:
            result = self._directive(
                "CHARACTER_REPORT", "debug.character",
                {"spell_grades": True, "spell_effects": False},
                "live", instance)
        except Exception:  # noqa: BLE001 - a probe must never kill the loop
            return None
        data = self._client_payload(result)
        if not data or not isinstance(data.get("character"), dict):
            return None
        return data

    def fight_estimate(self, args: Dict[str, Any], source: str = "local",
                       observation: Optional[Dict[str, Any]] = None,
                       instance: Optional[str] = None) -> ToolResult:
        """Estimate one monster group. Read-only: it never joins or casts.

        A blocked estimate is still a successful read: ``ok`` says the tool
        answered, ``allowed`` and ``blocked_reason`` say whether the fight may
        start. Only a call that could not be answered at all is ``ok=False``.
        """
        advisor = self.fight_advisor()
        if advisor.character is None:
            report = self.character_report(instance)
            if report is not None:
                advisor.use_character_report(report)
        if not isinstance(observation, dict) or not observation:
            observation = self.snapshot(instance)
        estimate = advisor.estimate(
            observation,
            group_id=args.get("group_id"),
            monster=args.get("monster"),
        )
        unavailable = estimate.get("status") == "unavailable"
        return ToolResult(
            tool="fight.estimate",
            ok=not unavailable,
            source=source,
            data=estimate,
            error=str(estimate.get("blocked_reason")) if unavailable else None,
        )

    def fight_plan(self, args: Dict[str, Any], source: str = "local") -> ToolResult:
        """Plan one combat turn from the live board. Read-only: it never acts.

        The solver is imported here rather than at module import so the agent can
        start without the combat/data stack loaded. Every answer carries the
        normalized live observation and its contract. The learned ranker is
        opt-in and refuses to run when the observation is missing the stats or
        enemy spell facts it would otherwise have to guess.
        """
        from combat.battle_solver import BattleSolver, Board
        from combat.observation import live_view

        payload = self.fight_state()
        if not payload:
            return ToolResult(
                tool="fight.plan", ok=False, source=source,
                error="no live fight board: the client is not in a fight",
            )
        ranker = str(args.get("ranker") or "solver")
        rank_with_value = bool(args.get("rank_with_value", False))
        if rank_with_value and ranker == "solver":
            ranker = "value"
        if ranker == "value":
            rank_with_value = True

        observation = None
        contract = None
        try:
            board = Board.from_fight_state(payload)
            observation, contract = live_view(board)
            solver = BattleSolver(
                beam_width=int(args.get("beam_width", 16) or 16),
                max_actions=int(args.get("max_actions", 4) or 4),
            )
            top_k = int(args.get("top_k", 1) or 1)
            checkpoint = None
            refused = None
            if rank_with_value:
                top_k = max(top_k, 5)
                plans = solver.plan_candidates(board, top_k)
                checkpoint = None
                params = cfg = None
                if not contract["value_ready"]:
                    refused = "observation_not_value_ready"
                else:
                    params, cfg, path = self._fight_value()
                    checkpoint = str(path)
                    if params is None:
                        refused = "checkpoint_missing"
                    else:
                        try:
                            # The ranker needs an array library -- jax if it is installed, numpy
                            # otherwise (combat/encounter/backend.py). `runtimes/python` ships
                            # bare: site-packages never crosses the installer, so on a copy that
                            # has not run `pip install -r requirements.txt` yet even numpy is
                            # missing. This probe turns that into the refusal the caller already
                            # reports instead of an ImportError out of the decision loop.
                            from combat.plan_value import rank_plans  # noqa: F401
                        except ImportError as exc:
                            refused = "value_runtime_missing: %s" % (exc,)
                if refused is not None:
                    data = {
                        "plans": [plan.as_dict() for plan in plans],
                        "value_ranked": False,
                        "value_checkpoint": checkpoint,
                        "value_refused": refused,
                    }
                else:
                    from combat.plan_value import rank_plans

                    ranked = rank_plans(params, board, plans, cfg)
                    data = {
                        "plans": [
                            {"rank_score": round(score, 4), **plan.as_dict()}
                            for score, plan in ranked
                        ],
                        "value_ranked": True,
                        "value_checkpoint": checkpoint,
                    }
            elif ranker == "grid":
                top_k = max(top_k, 5)
                plans = solver.plan_candidates(board, top_k)
                grid_meta = {}
                grid_refused = None
                if board.me is None or board.me.ap is None or board.me.ap <= 0:
                    grid_refused = "observation_not_grid_ready"
                else:
                    from combat.grid_infer import rank_plans as rank_grid

                    ranked = rank_grid(board, plans)
                    if ranked is None:
                        grid_refused = "grid_checkpoint_missing"
                    else:
                        ranked_plans, grid_meta = ranked
                        checkpoint = grid_meta.get("checkpoint")
                        if grid_meta.get("config", {}).get("hidden") not in (None, 768):
                            grid_refused = "grid_checkpoint_shape"
                        else:
                            data = {
                                "plans": [
                                    {"rank_score": round(score, 4), **plan.as_dict()}
                                    for score, plan in ranked_plans
                                ],
                                "grid_ranked": True,
                                "grid": grid_meta,
                            }
                if grid_refused is not None:
                    refused = grid_refused
                    data = {
                        "plans": [plan.as_dict() for plan in plans],
                        "grid_ranked": False,
                        "grid_refused": grid_refused,
                    }
            elif top_k > 1:
                plans = solver.plan_candidates(board, top_k)
                data = {"plans": [plan.as_dict() for plan in plans]}
            else:
                data = solver.plan_turn(board).as_dict()
            data["observation_version"] = observation["schema_version"]
            data["observation"] = observation
            data["contract"] = contract
            data["ranker"] = {
                "requested": ranker,
                "used": bool(data.get("value_ranked") or data.get("grid_ranked")),
                "scope": ("grid_solver_distillation" if ranker == "grid"
                          else "encounter_v3_synthetic"),
                "live_calibrated": False,
                "checkpoint": (data.get("grid", {}).get("checkpoint")
                               if ranker == "grid" else checkpoint),
                "refused": data.get("grid_refused") if ranker == "grid" else refused,
            }
        except Exception as exc:  # noqa: BLE001 - a bad board must not kill the loop
            return ToolResult(
                tool="fight.plan", ok=False, error=str(exc), source=source,
                data={"observation": observation, "contract": contract}
                if observation is not None else None,
            )
        return ToolResult(tool="fight.plan", ok=True, data=data, source=source)

    # -- internals -----------------------------------------------------------

    @staticmethod
    def _client_payload(result: ToolResult) -> Optional[Dict[str, Any]]:
        """The game payload inside a directive envelope.

        The bridge wraps answers as ``response.result.data`` and reports the
        *game's* rejection in ``response.result.ok``, which is a different flag
        from the transport one on ``ToolResult.ok``. Read both or a refused
        directive looks like an empty success.
        """
        if not result.ok or not isinstance(result.data, dict):
            return None
        response = result.data.get("response")
        if not isinstance(response, dict):
            return None
        inner = response.get("result")
        if isinstance(inner, dict):
            if inner.get("ok") is False:
                return None
            data = inner.get("data")
            return data if isinstance(data, dict) else None
        data = response.get("data")
        return data if isinstance(data, dict) else None

    def _record(
        self,
        tool: str,
        args: Dict[str, Any],
        result: ToolResult,
        goal: Optional[str],
        observation: Optional[Dict[str, Any]],
        observation_age_ms: Optional[float] = None,
        observation_source: str = "call",
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        if self.recorder is None:
            return
        # The recorder is the persistence boundary for both the trajectory corpus
        # and the autopilot rows built from it; a credential that reached the
        # snapshot (an older page build sends the login ticket) stops here.
        redact_packet_secrets(observation)
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
            explored=bool(isinstance(meta, dict) and meta.get("explored")),
            meta=meta,
        )

    def _route_nav(
        self,
        args: Dict[str, Any],
        observation: Optional[Dict[str, Any]],
    ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        """Turn a destination map into the single step the client can take.

        The client walks to a border and crosses it; it cannot walk a route. So
        `nav.goto {"map_id": far_away}` is split here: the destination stays the
        caller's intent, while the copy handed to the client carries the next hop
        and its direction. When the map is adjacent (as the snapshot itself says),
        only the direction is added.
        """
        target_raw = args.get("map_id")
        if target_raw is None or args.get("direction"):
            return args, None
        try:
            target = int(target_raw)
        except (TypeError, ValueError):
            return args, None
        here = _obs_map_id(observation)
        if here is None:
            return args, None
        # Correct the export with what the client reports before planning a hop:
        # a stale edge is what produced six "the right exit leads to ... , not ..."
        # refusals on map 145490434 (2026-10-29).
        self._maps.observe(here, _obs_exit_map(observation))
        if target == here:
            # A bare map target on the map we already stand on is a no-op. A cell,
            # NPC or coordinate alongside it still means "do that here", so the
            # client keeps the call.
            if args.get("cell") is None and args.get("npc_id") is None \
                    and args.get("npc") is None and args.get("x") is None:
                return args, {"already_here": True, "map_id": here}
            return args, None

        direction = _obs_map_exits(observation).get(target)
        if direction:
            routed = dict(args)
            routed["direction"] = direction
            return routed, {"requested_map_id": target, "from_map_id": here,
                            "direction": direction, "next_map_id": target,
                            "hops_remaining": 1, "source": "observation"}

        step = self._maps.hop(here, target, blocked_first=_obs_blocked_directions(observation))
        if step is None:
            # Not adjacent and not in the exported graph: send the call anyway so
            # the client's own error names the exits it does have, which is better
            # evidence in the log than a silent substitution. A tutorial gate can
            # also make every route's first step unusable; the client then refuses
            # this call naming the restriction, which is the evidence we need.
            return args, {"requested_map_id": target, "from_map_id": here,
                          "routable": False,
                          "tutorial_blocked": sorted(
                              _obs_blocked_directions(observation)) or None}
        direction, next_map, hops = step
        routed = dict(args)
        routed["map_id"] = next_map
        routed["direction"] = direction
        return routed, {"requested_map_id": target, "from_map_id": here,
                        "direction": direction, "next_map_id": next_map,
                        "hops_remaining": hops, "source": "map_index"}

    def _dispatch(self, tool: str, args: Dict[str, Any], source: str,
                  instance: Optional[str] = None,
                  observation: Optional[Dict[str, Any]] = None) -> ToolResult:
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
            if tool == "fight.plan":
                return self.fight_plan(args, source)
            if tool == "fight.estimate":
                return self.fight_estimate(args, source, observation, instance)
            return ToolResult(tool=tool, ok=False, error=f"no local handler for {tool}", source=source)
        if spec.backend == "sentinel":
            if tool == "nav.goto":
                return self._nav_goto(args, source)
            return ToolResult(tool=tool, ok=False, error=f"no sentinel handler for {tool}", source=source)
        if spec.backend == "directive":
            if tool in ("farm.start", "fight.join"):
                resolved, error = self._resolve_targets(tool, dict(args))
                if error is not None:
                    return ToolResult(tool=tool, ok=False, error=error, source=source)
                args = resolved
            result = self._directive(spec.directive or tool.upper(), tool, args, source, instance)
            if tool == "fight.state":
                self._attach_observation(result)
            return result
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

    def _resolve_entity_id(self, text: str, kind: str) -> Optional[int]:
        """First knowledge id for a name; the client needs numeric ids."""
        if self.canonical_store is not None:
            try:
                hits = self.canonical_store.find_entity(kind, text, limit=1)
                if hits:
                    return int(hits[0]["id"])
            except Exception:  # noqa: BLE001 - fall back to the lazy index
                pass
        try:
            hits = self.knowledge.search(text, kind, 1)
        except Exception:  # noqa: BLE001 - an index problem is a resolution miss
            return None
        if not hits:
            return None
        try:
            return int(hits[0].get("id"))
        except (TypeError, ValueError):
            return None

    def _resolve_targets(self, tool: str, args: Dict[str, Any]
                         ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Turn id_or_name arguments into ids before they reach the page.

        The client parses farm.start.monster with parseInt; a name used to become
        NaN and silently enable the generic farm loop instead of the named mob.
        An unresolvable target is an error, never a licence to farm anything.
        """
        if tool == "farm.start":
            for key, kind in (("monster", "monster"), ("resource", "item")):
                raw = args.get(key)
                if raw is None or isinstance(raw, (int, float)):
                    continue
                text = str(raw).strip()
                if not text:
                    continue
                if text.lstrip("-").isdigit():
                    args[key] = int(text)
                    continue
                rid = self._resolve_entity_id(text, kind)
                if rid is None and kind == "monster":
                    # "farm blé" names a resource; retry as an item and switch.
                    rid = self._resolve_entity_id(text, "item")
                    if rid is not None:
                        args.pop("monster", None)
                        args["resource"] = rid
                        continue
                if rid is None:
                    return None, f"unknown {kind}: {text}"
                args[key] = rid
        elif tool == "fight.join":
            raw = args.get("monster")
            if isinstance(raw, str) and raw.strip():
                text = raw.strip()
                if text.lstrip("-").isdigit():
                    args["monster"] = int(text)
                else:
                    rid = self._resolve_entity_id(text, "monster")
                    if rid is None:
                        return None, f"unknown monster: {text}"
                    args["monster"] = rid
        return args, None

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
            payload = loads(path.read_bytes())
        except (ValueError, OSError):
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
        q_offsets, q_blob = self.knowledge._hay_for("quest")
        q_nend = self.knowledge._nend.get("quest")
        area_needle = area_norm.encode("utf-8")

        def names_hold_area(index: int) -> bool:
            """`area_norm in this row's names`, straight out the blob -- the old joined
            per-row string no longer exists to be searched."""
            if q_nend is None or index >= len(q_nend):
                return False
            return q_blob.find(area_needle, q_offsets[index], q_nend[index]) >= 0

        for index, (_record_id, _descriptions, _names, record, _j) in enumerate(entries):
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
                    if not in_area and not names_hold_area(index):
                        continue
                elif not names_hold_area(index):
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

    def recovery_call(self, tool: str, args: Optional[Dict[str, Any]] = None,
                      instance: Optional[str] = None) -> ToolResult:
        """One directive the tool vocabulary does not contain.

        Live 2026-10-29: a session that ended back at the login screen left the
        loop waiting for the world forever, because `session.login` is not one of
        the 27 policy tools. A recovery the policy must never *choose* still has
        to reach the page, and the page's own login guard stays the authority on
        whether a press is allowed.
        """
        return self._directive(tool, tool, dict(args or {}), "live", instance)

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
        now = time.monotonic()
        if now < self._bridge_open_until:
            return ToolResult(
                tool=tool, ok=False, source=source, directive=directive,
                error=("directive bridge circuit open for %.0fs: the client page "
                       "did not answer the last calls (%s); directive '%s' was NOT "
                       "executed" % (self._bridge_open_until - now,
                                     self._bridge_last_error or "timeout",
                                     directive)),
                data={"directive_payload": payload, "skipped": True,
                      "circuit_open": True},
            )
        ok, response = self._http_json("POST", self.directive_url, payload)
        port_recovered = False
        if not ok:
            # The host walks CANDIDATE_PORTS (8893, 8894, ... 8899) and republishes
            # `bridge_port.txt` when it moves, which it does whenever a leaked socket holds the
            # previous port. This executor read the port once at construction, so a move
            # mid-session turned every later directive into "not reachable": measured 2026-11-06,
            # three failed steps against 8896 while the host was answering on 8894. Re-resolve
            # once -- not in a loop, and only when the published port actually changed.
            fresh = bridge_directive_url()
            if fresh != self.directive_url:
                moved_from, self.directive_url = self.directive_url, fresh
                ok, response = self._http_json("POST", fresh, payload)
                port_recovered = ok
                if not ok:
                    self.directive_url = moved_from
        if not ok:
            self._note_bridge_answer(False, str(response))
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
        self._note_bridge_answer(bridge_ok, str(bridge_error or ""))
        result = ToolResult(
            tool=tool, ok=bridge_ok, source=source, directive=directive,
            data={"directive_payload": payload, "response": response,
                  "directive_url": self.directive_url,
                  "port_recovered": port_recovered},
        )
        # The bridge attaches the game state it ran against. Keep it on the
        # result so `execute()` can record it as the observation, and so a
        # caller that wants the raw state does not have to re-poll the client.
        if isinstance(response, dict) and isinstance(response.get("snapshot"), dict):
            result.data["snapshot"] = response["snapshot"]
        if not bridge_ok:
            result.error = f"client directive failed: {bridge_error or 'unknown error'}"
        return result
