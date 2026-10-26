from __future__ import annotations

import json, logging, os, threading, time, unicodedata, urllib.error, urllib.request, uuid
from collections import OrderedDict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from agent.bridge import directive_url as bridge_directive_url, opener_for
from agent.jsonio import dumps_bytes, loads
from agent.recorder import TrajectoryRecorder
from agent.knowledge_store import KnowledgeStore
from agent.schema import TOOLS_BY_NAME, validate_call

logger = logging.getLogger("dofus.agent.executor")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "cache"

# kind -> cache file. Every cache file is "<kind>s_full.json".
KNOWLEDGE_FILES = {k: k + "s_full.json"
                   for k in ("monster", "spell", "quest", "item", "npc",
                             "achievement", "dungeon")}

_ANY_KIND_ORDER = ("monster", "quest", "item", "npc", "spell", "dungeon", "achievement")
_NAME_KEYS_FLAT = ("name_fr", "name_en", "name_es", "name_de")


def _accent_table():
    t = {}
    for c in range(0xC0, 0x250):
        s = "".join(x for x in unicodedata.normalize("NFKD", chr(c))
                    if not unicodedata.combining(x))
        if s != chr(c):
            t[c] = s
    return t


_ACCENT_TABLE = _accent_table()


def _normalize(text):
    if not isinstance(text, str):
        return ""
    text = text.lower()
    if text.isascii():
        return text
    text = text.translate(_ACCENT_TABLE)
    if text.isascii():
        return text
    return "".join(c for c in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(c))


def _name_strings(record):
    out = []
    name = record.get("name")
    if isinstance(name, dict):
        out.extend(str(v) for v in name.values() if isinstance(v, str))
    elif isinstance(name, str):
        out.append(name)
    out.extend(v for k in _NAME_KEYS_FLAT if isinstance(v := record.get(k), str))
    return out


def _description_strings(record):
    out = []
    d = record.get("description")
    if isinstance(d, dict):
        out.extend(str(v) for v in d.values() if isinstance(v, str))
    elif isinstance(d, str):
        out.append(d)
    return out


def _localized_strings(record):
    return _name_strings(record) + _description_strings(record)


# Fields a search result exposes, in the order the compact record uses.
_INDEX_FIELDS = {
    "item": ("id", "name", "level", "type_id", "price", "is_saleable"),
    "spell": ("id", "name_fr", "name_en", "name_es", "type_id"),
    "npc": ("id", "name", "actions"),
    "achievement": ("id", "name", "description", "points", "level"),
    "dungeon": ("id", "name", "level_min", "entrance_map_id", "boss_monster_ids"),
    "monster": ("id", "name", "name_fr", "name_en", "name_es", "name_de", "is_boss"),
}


def _index_record(kind, record):
    f = _INDEX_FIELDS.get(kind)
    if f is None:
        return record
    s = {k: record.get(k) for k in f}
    if kind == "monster":
        s["is_boss"] = bool(record.get("is_boss", False))
        s["grades"] = [{"level": g.get("level"), "life_points": g.get("life_points")}
                       for g in record.get("grades") or [] if isinstance(g, dict)]
    return s


@dataclass
class ToolResult:
    tool: str
    ok: bool
    data: Any = None
    error: Optional[str] = None
    directive: Optional[str] = None
    latency_ms: float = 0.0
    source: str = "live"

    def as_dict(self):
        return {"tool": self.tool, "ok": self.ok, "data": self.data,
                "error": self.error, "directive": self.directive,
                "latency_ms": round(self.latency_ms, 2), "source": self.source}


# Order is the tie-break when a BFS step has several equal-length routes.
_MAP_DIRECTIONS = (
    ("top", "topNeighbourId"),
    ("right", "rightNeighbourId"),
    ("bottom", "bottomNeighbourId"),
    ("left", "leftNeighbourId"),
)


class _MapGraph:
    def __init__(self, path=None):
        self.path = Path(path) if path else REPO_ROOT / "data" / "maps" / "_index.json"
        self._lock = threading.RLock()
        self._edges = None
        self._reverse_edges = None
        self._trees = OrderedDict()
        self._tree_limit = 4

    def edges(self):
        with self._lock:
            if self._edges is not None:
                return self._edges
            e = {}
            try:
                p = loads(self.path.read_bytes())
            except (OSError, ValueError) as x:
                logger.warning("map index unavailable (%s): %s", self.path, x)
                p = {}
            for key, rec in (p.items() if isinstance(p, dict) else ()):
                if not isinstance(rec, dict):
                    continue
                try:
                    mid = int(rec.get("id", key))
                except (TypeError, ValueError):
                    continue
                nb = rec.get("neighbours") or {}
                ex = {}
                for d, f in _MAP_DIRECTIONS:
                    try:
                        n = int(nb.get(f))
                    except (TypeError, ValueError):
                        continue
                    if n > 0:
                        ex[d] = n
                if ex:
                    e[mid] = ex
            self._edges = e
            return e

    def reverse_edges(self):
        with self._lock:
            c = self._reverse_edges
            if c is not None:
                return c
        r = {}
        for n, ex in self.edges().items():
            for d, x in ex.items():
                r.setdefault(x, []).append((n, d))
        with self._lock:
            self._reverse_edges = r
        return r

    def _tree(self, target):
        with self._lock:
            c = self._trees.get(target)
            if c is not None:
                self._trees.move_to_end(target)
                return c
        tr = {target: ("", target, 0)}
        q = deque([target])
        rev = self.reverse_edges()
        while q:
            node = q.popleft()
            dist = tr[node][2] + 1
            for prev, d in rev.get(node, ()):
                if prev in tr:
                    continue
                tr[prev] = (d, node, dist)
                q.append(prev)
        with self._lock:
            self._trees[target] = tr
            while len(self._trees) > self._tree_limit:
                self._trees.popitem(last=False)
        return tr

    def _hop_bfs(self, here, target, blocked_first):
        e = self.edges()
        par = {here: (None, None)}
        q = deque([here])
        while q:
            node = q.popleft()
            if node == target:
                break
            for d, x in e.get(node, {}).items():
                if node == here and d in blocked_first:
                    continue
                if x in par:
                    continue
                par[x] = (node, d)
                q.append(x)
        if target not in par:
            return None
        p = []
        node = target
        while par[node][0] is not None:
            prev, d = par[node]
            p.append(d)
            node = prev
        p.reverse()
        return (p[0], e[here][p[0]], len(p)) if p else None

    def hop(self, here, target, blocked_first=None):
        if here is None or target is None or here == target:
            return None
        en = self._tree(target).get(here)
        if en is None:
            return None
        d, n, k = en
        blocked_first = blocked_first or set()
        return self._hop_bfs(here, target, blocked_first) if d in blocked_first else (d, n, k)


def _obs_map_id(observation):
    if not isinstance(observation, dict):
        return None
    m = observation.get("map")
    p = observation.get("position")
    for v in (observation.get("map_id"), p.get("map_id") if isinstance(p, dict) else None,
              m.get("map_id") if isinstance(m, dict) else None):
        try:
            if v is not None:
                return int(v)
        except (TypeError, ValueError):
            continue
    return None


def _obs_map_exits(observation):
    m = observation.get("map") if isinstance(observation, dict) else None
    if not isinstance(m, dict):
        return {}
    ex = {}
    for e in m.get("exits") or []:
        if not isinstance(e, dict) or e.get("blocked"):
            continue
        try:
            n = int(e.get("map_id"))
        except (TypeError, ValueError):
            continue
        d = e.get("direction")
        if d:
            ex[n] = str(d)
    return ex


def _obs_blocked_directions(observation):
    m = observation.get("map") if isinstance(observation, dict) else None
    if not isinstance(m, dict):
        return set()
    b = set()
    for e in m.get("exits") or []:
        if isinstance(e, dict) and e.get("blocked") and e.get("direction"):
            b.add(str(e["direction"]))
    return b


class _KnowledgeIndex:
    def __init__(self, cache_dir):
        self.cache_dir = cache_dir
        self._lock = threading.Lock()
        self._entries = {}
        self._haystacks = {}
        self._search_cache = OrderedDict()

    def entries(self, kind):
        with self._lock:
            c = self._entries.get(kind)
            if c is not None:
                return c
            path = self.cache_dir / KNOWLEDGE_FILES.get(kind, "")
            recs = []
            if path.exists():
                try:
                    p = loads(path.read_bytes())
                    if isinstance(p, list):
                        recs = p
                except (ValueError, OSError) as x:
                    logger.warning(f"Unreadable cache file {path}: {x}")
            es = []
            for r in recs:
                if not isinstance(r, dict):
                    continue
                ns = [_normalize(n) for n in _name_strings(r) if n]
                ds = tuple(d for d in _description_strings(r) if d)
                st = _index_record(kind, r)
                es.append((int(r.get("id") or 0), ds, ns, st, " ".join(ns)))
            self._entries[kind] = es
            return es

    def _haystacks_for(self, kind, entries):
        with self._lock:
            c = self._haystacks.get(kind)
            if c is not None:
                return c
            b = []
            st = []
            for i, ds, ns, rec, nh in entries:
                b.append(" ".join(ns + [_normalize(d) for d in ds]) if ds else nh)
                st.append((i, (), ns, rec, nh))
            self._haystacks[kind] = b
            self._entries[kind] = st
            return b

    def search(self, query, kind="any", limit=5):
        q = _normalize(query).strip()
        if not q:
            return []
        mr = max(1, int(limit))
        ck = (kind, q, mr)
        with self._lock:
            c = self._search_cache.get(ck)
            if c is not None:
                self._search_cache.move_to_end(ck)
                return list(c)
        ts = [t for t in q.split() if len(t) >= 2]
        ks = _ANY_KIND_ORDER if kind == "any" else (kind,)
        one = len(ts) == 1 and ts[0] == q
        nt = len(ts)
        if ts:
            bo = []
            for k in ks:
                for i, _d, ns, rec, h in self.entries(k):
                    if q not in h:
                        continue
                    if any(n.startswith(q) for n in ns):
                        bo.append(((6.0 if q in ns else 4.0) + 3.5 + 0.5 * nt, i, k, rec))
            if len(bo) >= mr:
                bo.sort(key=lambda z: (-z[0], z[1]))
                return self._cache_results(ck, [
                    {"kind": k, "score": round(s, 2), **_compact(k, rec)}
                    for s, _i, k, rec in bo[:mr]])
        rs = []
        add = rs.append
        for k in ks:
            es = self.entries(k)
            for (i, _d, ns, rec, _h), h in zip(es, self._haystacks_for(k, es)):
                if q not in h:
                    if one or not ts:
                        continue
                    n = sum(map(h.__contains__, ts))
                    if not n:
                        continue
                    add(((1.5 if n == nt else 0.0) + 0.5 * n, i, k, rec))
                    continue
                s = (6.0 if q in ns else 4.0) if any(n.startswith(q) for n in ns) else 0.0
                s += 2.0
                if ts:
                    s += 1.5 + 0.5 * nt
                add((s, i, k, rec))
        rs.sort(key=lambda z: (-z[0], z[1]))
        return self._cache_results(ck, [
            {"kind": k, "score": round(s, 2), **_compact(k, rec)}
            for s, _i, k, rec in rs[:mr]])

    def _cache_results(self, cache_key, out):
        with self._lock:
            self._search_cache[cache_key] = out
            while len(self._search_cache) > 128:
                self._search_cache.popitem(last=False)
        return list(out)


def _compact(kind, record):
    if kind == "monster":
        return {"id": record.get("id"),
                "name": {k: record.get(k, "") for k in _NAME_KEYS_FLAT if record.get(k)},
                "is_boss": record.get("is_boss", False),
                "grades": [{"level": g.get("level"), "hp": g.get("life_points")}
                           for g in record.get("grades") or [] if isinstance(g, dict)][:5]}
    if kind == "spell":
        return {"id": record.get("id"),
                "name": {k: record.get(k, "") for k in ("name_fr", "name_en", "name_es")
                         if record.get(k)},
                "type_id": record.get("type_id")}
    if kind == "quest":
        return {"id": record.get("id"), "name": record.get("name"),
                "level_min": record.get("level_min"), "level_max": record.get("level_max"),
                "is_dungeon": record.get("is_dungeon"),
                "start_position": record.get("start_position"),
                "steps": [{"id": s.get("id"), "name": s.get("name"),
                           "optimal_level": s.get("optimal_level"),
                           "objectives": [{"type_id": o.get("type_id"), "text": o.get("text"),
                                           "map_id": o.get("map_id"), "coords": o.get("coords")}
                                          for o in s.get("objectives") or []]}
                          for s in record.get("steps") or []]}
    f = _INDEX_FIELDS.get(kind)
    return {k: record.get(k) for k in f} if f else {"id": record.get("id")}


class ToolExecutor:
    def __init__(
        self,
        sentinel_url="http://127.0.0.1:8890",
        directive_url=None,
        cache_dir=DEFAULT_CACHE_DIR,
        timeout=6.0,
        knowledge_store=None,
        recorder: Optional[TrajectoryRecorder] = None,
        fight_advisor=None,
    ):
        self.sentinel_url = sentinel_url.rstrip("/")
        self.directive_url = directive_url or bridge_directive_url()
        self.timeout = float(timeout)
        self.recorder = recorder
        self.knowledge = _KnowledgeIndex(Path(cache_dir))
        self.store = KnowledgeStore(knowledge_store)
        self.fights = fight_advisor
        self._zaaps_cache = None
        self._geo_index = None
        self._maps = _MapGraph()
        self.canonical_store = None
        self._fight_value_cache = None

    def _http_json(self, method, url, body=None):
        d = None
        h = {"Accept": "application/json"}
        if body is not None:
            d = dumps_bytes(body)
            h["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=d, headers=h, method=method)
        try:
            with opener_for(url).open(req, timeout=self.timeout) as r:
                raw = r.read()
                try:
                    return True, loads(raw)
                except ValueError:
                    return True, {"raw": raw.decode("utf-8", errors="replace")[:4000]}
        except urllib.error.HTTPError as x:
            return False, f"HTTP {x.code} from {url}: {x.read()[:400].decode('utf-8', errors='replace')}"
        except urllib.error.URLError as x:
            return False, f"unreachable: {url} ({x.reason})"
        except OSError as x:
            return False, f"unreachable: {url} ({x})"

    def execute(self, tool, args=None, instance=None, goal=None, source="live",
                observation=None, observation_age_ms=None, observation_source="call"):
        t0 = time.perf_counter()
        ok, v = validate_call(tool, args)
        if not ok:
            r = ToolResult(tool=tool, ok=False, error=str(v), source=source,
                           latency_ms=(time.perf_counter() - t0) * 1000.0)
            self._record(tool, args or {}, r, goal, observation,
                         observation_age_ms, observation_source)
            return r
        try:
            if tool == "nav.goto":
                routed, route = self._route_nav(v, observation)
            else:
                routed, route = v, None
            if route and route.get("already_here"):
                r = ToolResult(tool=tool, ok=True, source=source, data=route)
            else:
                r = self._dispatch(tool, routed, source=source, instance=instance,
                                   observation=observation)
                if route and isinstance(r.data, dict):
                    r.data["route"] = route
        except Exception as x:  # noqa: BLE001
            r = ToolResult(tool=tool, ok=False, error=f"{type(x).__name__}: {x}", source=source)
        r.latency_ms = (time.perf_counter() - t0) * 1000.0
        if observation is None and isinstance(r.data, dict):
            s = r.data.get("snapshot")
            if isinstance(s, dict) and s:
                observation, observation_age_ms, observation_source = s, 0.0, "call"
        self._record(tool, v, r, goal, observation, observation_age_ms, observation_source)
        return r

    def snapshot(self, instance=None):
        try:
            r = self._dispatch("state.snapshot", {}, source="live", instance=instance)
        except Exception:  # noqa: BLE001
            return None
        if not r.ok or not isinstance(r.data, dict):
            return None
        s = r.data.get("snapshot")
        if isinstance(s, dict) and s:
            return s
        resp = r.data.get("response")
        inner = resp.get("result") if isinstance(resp, dict) else None
        if isinstance(inner, dict):
            d = inner.get("data")
            if isinstance(d, dict) and d:
                return d
        return None

    def fight_state(self, instance=None):
        try:
            r = self._dispatch("fight.state", {}, source="live", instance=instance)
        except Exception:  # noqa: BLE001
            return None
        if not r.ok or not isinstance(r.data, dict):
            return None
        resp = r.data.get("response")
        inner = resp.get("result") if isinstance(resp, dict) else None
        d = inner.get("data") if isinstance(inner, dict) else None
        return d if isinstance(d, dict) and d else None

    def _attach_observation(self, result):
        if not result.ok or not isinstance(result.data, dict):
            return
        resp = result.data.get("response")
        inner = resp.get("result") if isinstance(resp, dict) else None
        data = inner.get("data") if isinstance(inner, dict) else None
        if not isinstance(data, dict):
            return
        observation = None
        try:
            from combat.battle_solver import Board
            from combat.observation import build_observation, validate_observation

            observation = build_observation(Board.from_fight_state(data), source="live")
            contract = validate_observation(observation)
        except Exception as x:  # noqa: BLE001
            contract = {"version": 1, "ok": False, "actionable": False,
                        "value_ready": False,
                        "errors": [f"observation_failed:{type(x).__name__}: {x}"],
                        "warnings": []}
        if observation is not None:
            data["observation"] = observation
        data["contract"] = contract
        result.data["observation"] = observation
        result.data["contract"] = contract

    def _fight_value(self):
        if self._fight_value_cache is not None:
            return self._fight_value_cache
        from combat.encounter.checkpoint import load_params_config

        c = os.environ.get("DOFUS_FIGHT_VALUE")
        path = Path(c) if c else REPO_ROOT / "models" / "encounter_selfplay.pkl"
        if not path.exists():
            self._fight_value_cache = (None, None, path)
            return self._fight_value_cache
        try:
            params, cfg = load_params_config(path)
            self._fight_value_cache = (params, cfg, path)
        except Exception as x:  # noqa: BLE001
            logger.warning("fight value checkpoint unavailable (%s): %s", path, x)
            self._fight_value_cache = (None, None, path)
        return self._fight_value_cache

    def fight_advisor(self):
        if self.fights is None:
            from combat.fight_estimate import FightAdvisor

            self.fights = FightAdvisor()
        return self.fights

    def character_report(self, instance=None):
        try:
            r = self._directive("CHARACTER_REPORT", "debug.character",
                                {"spell_grades": True, "spell_effects": False},
                                "live", instance)
        except Exception:  # noqa: BLE001
            return None
        d = self._client_payload(r)
        if not d or not isinstance(d.get("character"), dict):
            return None
        return d

    def fight_estimate(self, args, source="local", observation=None, instance=None):
        advisor = self.fight_advisor()
        if advisor.character is None:
            report = self.character_report(instance)
            if report is not None:
                advisor.use_character_report(report)
        if not isinstance(observation, dict) or not observation:
            observation = self.snapshot(instance)
        estimate = advisor.estimate(observation, group_id=args.get("group_id"),
                                    monster=args.get("monster"))
        unavailable = estimate.get("status") == "unavailable"
        return ToolResult(tool="fight.estimate", ok=not unavailable, source=source,
                          data=estimate,
                          error=str(estimate.get("blocked_reason")) if unavailable else None)

    def fight_plan(self, args, source="local"):
        from combat.battle_solver import BattleSolver, Board
        from combat.observation import build_observation, validate_observation

        payload = self.fight_state()
        if not payload:
            return ToolResult(tool="fight.plan", ok=False, source=source,
                              error="no live fight board: the client is not in a fight")
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
            observation = build_observation(board, source="live")
            contract = validate_observation(observation)
            solver = BattleSolver(beam_width=int(args.get("beam_width", 16) or 16),
                                  max_actions=int(args.get("max_actions", 4) or 4))
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
                if refused is not None:
                    data = {"plans": [plan.as_dict() for plan in plans],
                            "value_ranked": False, "value_checkpoint": checkpoint,
                            "value_refused": refused}
                else:
                    from combat.plan_value import rank_plans

                    ranked = rank_plans(params, board, plans, cfg)
                    data = {"plans": [{"rank_score": round(score, 4), **plan.as_dict()}
                                      for score, plan in ranked],
                            "value_ranked": True, "value_checkpoint": checkpoint}
            elif top_k > 1:
                plans = solver.plan_candidates(board, top_k)
                data = {"plans": [plan.as_dict() for plan in plans]}
            else:
                data = solver.plan_turn(board).as_dict()
            data["observation_version"] = observation["schema_version"]
            data["observation"] = observation
            data["contract"] = contract
            data["ranker"] = {"requested": ranker, "used": bool(data.get("value_ranked")),
                              "scope": "encounter_v3_synthetic", "live_calibrated": False,
                              "checkpoint": checkpoint, "refused": refused}
        except Exception as x:  # noqa: BLE001
            return ToolResult(tool="fight.plan", ok=False, error=str(x), source=source,
                              data={"observation": observation, "contract": contract}
                              if observation is not None else None)
        return ToolResult(tool="fight.plan", ok=True, data=data, source=source)

    @staticmethod
    def _client_payload(result):
        if not result.ok or not isinstance(result.data, dict):
            return None
        resp = result.data.get("response")
        if not isinstance(resp, dict):
            return None
        inner = resp.get("result")
        if isinstance(inner, dict):
            if inner.get("ok") is False:
                return None
            d = inner.get("data")
            return d if isinstance(d, dict) else None
        d = resp.get("data")
        return d if isinstance(d, dict) else None

    def _record(self, tool, args, result, goal, observation,
                observation_age_ms=None, observation_source="call"):
        if self.recorder is None:
            return
        self.recorder.step(tool=tool, args=args, ok=result.ok, result=result.data,
                           error=result.error, latency_ms=result.latency_ms, goal=goal,
                           observation=observation, observation_age_ms=observation_age_ms,
                           observation_source=observation_source, source=result.source)

    def _route_nav(self, args, observation):
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
        if target == here:
            if args.get("cell") is None and args.get("npc_id") is None \
                    and args.get("npc") is None and args.get("x") is None:
                return args, {"already_here": True, "map_id": here}
            return args, None

        d = _obs_map_exits(observation).get(target)
        if d:
            routed = dict(args)
            routed["direction"] = d
            return routed, {"requested_map_id": target, "from_map_id": here,
                            "direction": d, "next_map_id": target,
                            "hops_remaining": 1, "source": "observation"}

        blocked = _obs_blocked_directions(observation)
        step = self._maps.hop(here, target, blocked_first=blocked)
        if step is None:
            return args, {"requested_map_id": target, "from_map_id": here,
                          "routable": False, "tutorial_blocked": sorted(blocked) or None}
        d, n, k = step
        routed = dict(args)
        routed["map_id"] = n
        routed["direction"] = d
        return routed, {"requested_map_id": target, "from_map_id": here,
                        "direction": d, "next_map_id": n, "hops_remaining": k,
                        "source": "map_index"}

    def _dispatch(self, tool, args, source, instance=None, observation=None):
        spec = TOOLS_BY_NAME[tool]
        b = spec.backend
        if b == "local":
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
        if b == "sentinel":
            if tool == "nav.goto":
                return self._nav_goto(args, source)
            return ToolResult(tool=tool, ok=False, error=f"no sentinel handler for {tool}", source=source)
        if b == "directive":
            if tool in ("farm.start", "fight.join"):
                args, error = self._resolve_targets(tool, dict(args))
                if error is not None:
                    return ToolResult(tool=tool, ok=False, error=error, source=source)
            r = self._directive(spec.directive or tool.upper(), tool, args, source, instance)
            if tool == "fight.state":
                self._attach_observation(r)
            return r
        return ToolResult(tool=tool, ok=False, error=f"unknown backend '{b}'", source=source)

    def _knowledge_remember(self, args, source):
        value = args.get("value")
        try:
            value = json.loads(value) if isinstance(value, str) else value
        except (ValueError, TypeError):
            pass
        changed, note = self.store.remember(
            str(args.get("kind", "")), str(args.get("key", "")), value,
            source="observed", confidence=float(args.get("confidence", 0.5) or 0.5))
        return ToolResult(tool="knowledge.remember", ok=True, source=source,
                          data={"changed": changed, "note": note})

    def _knowledge_forget(self, args, source):
        removed = self.store.forget(str(args.get("kind", "")), str(args.get("key", "")),
                                    str(args.get("reason", "")))
        return ToolResult(tool="knowledge.forget", ok=True, source=source,
                          data={"removed": removed})

    def _resolve_entity_id(self, text, kind):
        if self.canonical_store is not None:
            try:
                hits = self.canonical_store.find_entity(kind, text, limit=1)
                if hits:
                    return int(hits[0]["id"])
            except Exception:  # noqa: BLE001
                pass
        try:
            hits = self.knowledge.search(text, kind, 1)
        except Exception:  # noqa: BLE001
            return None
        if not hits:
            return None
        try:
            return int(hits[0].get("id"))
        except (TypeError, ValueError):
            return None

    def _resolve_targets(self, tool, args):
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

    def _knowledge_search(self, args, source):
        hits = self.knowledge.search(str(args.get("query", "")),
                                     kind=str(args.get("kind", "any")),
                                     limit=int(args.get("limit", 5)))
        if not hits:
            available = [k for k in KNOWLEDGE_FILES
                         if (self.knowledge.cache_dir / KNOWLEDGE_FILES[k]).exists()]
            return ToolResult(
                tool="knowledge.search", ok=True, source=source,
                data={"results": [], "note": f"no match; cached kinds: {', '.join(available) or 'none'}"})
        return ToolResult(tool="knowledge.search", ok=True, data={"results": hits}, source=source)

    def _load_cache(self, filename):
        path = self.knowledge.cache_dir / filename
        if not path.exists():
            return []
        try:
            p = loads(path.read_bytes())
        except (ValueError, OSError):
            return []
        return [r for r in p if isinstance(r, dict)] if isinstance(p, list) else []

    def _geography_index(self):
        if self._geo_index is not None:
            return self._geo_index
        idx = {"areas": {}, "subareas": {}, "area_subs": {}, "map_to_sub": {},
               "quest_to_sub": {}}
        for s in self._load_cache("subareas_full.json"):
            i = s.get("id")
            if not isinstance(i, int):
                continue
            n = _localized_strings(s)
            if n:
                idx["subareas"][i] = n
            for m in s.get("mapIds") or []:
                if isinstance(m, int):
                    idx["map_to_sub"][m] = i
            for m in s.get("entranceMapIds") or []:
                if isinstance(m, int):
                    idx["map_to_sub"].setdefault(m, i)
            for q in s.get("questsIds") or []:
                if isinstance(q, int):
                    idx["quest_to_sub"][q] = i
        for a in self._load_cache("areas_full.json"):
            i = a.get("id")
            if not isinstance(i, int):
                continue
            n = _localized_strings(a)
            if n:
                idx["areas"][i] = n
            idx["area_subs"][i] = [x for x in a.get("subareaIds") or [] if isinstance(x, int)]
        self._geo_index = idx
        return idx

    def _resolve_area_filter(self, area_norm):
        idx = self._geography_index()

        def matches(names):
            for name in names:
                n = _normalize(name)
                if not n:
                    continue
                if n == area_norm:
                    return True
                if len(area_norm) >= 3 and area_norm in n:
                    return True
                if len(n) >= 3 and n in area_norm:
                    return True
            return False

        ma = [(i, n) for i, n in idx["areas"].items() if matches(n)]
        ms = [(i, n) for i, n in idx["subareas"].items() if matches(n)]
        subs = {i for i, _n in ms}
        for i, _n in ma:
            subs.update(idx["area_subs"].get(i, ()))
        qs = {q for q, s in idx["quest_to_sub"].items() if s in subs}
        return {"query": area_norm, "resolved": bool(subs),
                "areas": [n[0] for _i, n in ma[:5]],
                "subareas": [n[0] for _i, n in ms[:5]],
                "subarea_count": len(subs), "_subarea_ids": subs, "_quest_ids": qs}

    def _quest_list(self, args, source):
        entries = self.knowledge.entries("quest")
        if not entries:
            return ToolResult(
                tool="quest.list", ok=False, source=source,
                error="quest cache is empty — run: python -m data.sync_full_database quests")

        area = _normalize(str(args.get("area", ""))).strip()
        lo = args.get("level_min")
        hi = args.get("level_max")
        dungeon_only = bool(args.get("dungeon_only", False))
        limit = max(1, int(args.get("limit", 10)))

        resolution = None
        subs = set()
        qids = set()
        m2s = {}
        if area:
            resolution = self._resolve_area_filter(area)
            subs = resolution.pop("_subarea_ids")
            qids = resolution.pop("_quest_ids")
            if resolution["resolved"]:
                m2s = self._geography_index()["map_to_sub"]

        matched = []
        for _i, desc, _n, rec, _h in entries:
            if dungeon_only and not rec.get("is_dungeon"):
                continue
            lv = int(rec.get("level_min") or 0)
            if isinstance(lo, int) and lv < lo:
                continue
            if isinstance(hi, int) and lv > hi:
                continue
            if area:
                if subs:
                    inside = int(rec.get("id") or 0) in qids
                    if not inside:
                        for start in rec.get("start_position") or []:
                            mid = start.get("mapId") if isinstance(start, dict) else None
                            if isinstance(mid, int) and m2s.get(mid) in subs:
                                inside = True
                                break
                    if not inside and area not in desc:
                        continue
                elif area not in desc:
                    continue
            matched.append(rec)

        matched.sort(key=lambda r: (int(r.get("level_min") or 0), int(r.get("id") or 0)))
        data = {"count": len(matched),
                "quests": [_compact("quest", r) for r in matched[:limit]]}
        if resolution is not None:
            data["area"] = resolution
            if not resolution["resolved"]:
                data["note"] = ("area not found in the local cache; "
                                "showing quests whose name contains the text")
        return ToolResult(tool="quest.list", ok=True, data=data, source=source)

    def _zaaps(self):
        if self._zaaps_cache is not None:
            return self._zaaps_cache
        ok, payload = self._http_json("GET", f"{self.sentinel_url}/sentinel/navigation/zaaps")
        z = []
        if ok and isinstance(payload, dict):
            for key in ("zaaps", "data"):
                c = payload.get(key)
                if isinstance(c, list):
                    z = [x for x in c if isinstance(x, dict)]
                    break
        self._zaaps_cache = z
        return z

    def _resolve_zaap(self, name):
        n = _normalize(name)
        for z in self._zaaps():
            strings = _localized_strings(z)
            x = z.get("x", z.get("posX"))
            y = z.get("y", z.get("posY"))
            c = z.get("coord") or z.get("coords") or z.get("position")
            if isinstance(c, dict):
                x = c.get("x", x)
                y = c.get("y", y)
            elif isinstance(c, list) and len(c) == 2:
                x, y = c[0], c[1]
            if not isinstance(x, int) or not isinstance(y, int):
                continue
            for t in strings:
                tn = _normalize(t)
                if n and (n in tn or tn in n):
                    return x, y, t
        return None

    def _nav_goto(self, args, source):
        target = None
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

        body = {"from": [int(args.get("from_x", 4)), int(args.get("from_y", -19))],
                "to": [target[0], target[1]], "allow_zaap": True,
                "lang": str(args.get("lang", "fr"))}
        ok, payload = self._http_json("POST", f"{self.sentinel_url}/sentinel/navigation/path", body)
        if not ok:
            return ToolResult(tool="nav.goto", ok=False, source=source,
                              error=f"sentinel unreachable: {payload}")
        return ToolResult(
            tool="nav.goto", ok=True, source=source,
            data={"target": target_name,
                  "path": payload.get("path") if isinstance(payload, dict) else payload})

    def _directive(self, directive, tool, args, source, instance=None):
        payload = {"id": uuid.uuid4().hex, "ts": round(time.time(), 3),
                   "directive": directive, "tool": tool, "args": args, "source": source}
        if instance and instance != "default":
            payload["target_instance"] = instance
        ok, response = self._http_json("POST", self.directive_url, payload)
        if not ok:
            return ToolResult(
                tool=tool, ok=False, source=source, directive=directive,
                error=(f"directive bridge at {self.directive_url} not reachable "
                       f"({response}); directive '{directive}' was NOT executed"),
                data={"directive_payload": payload})
        bridge_ok = True
        bridge_error = None
        if isinstance(response, dict) and "ok" in response:
            bridge_ok = bool(response.get("ok"))
            bridge_error = response.get("error")
        result = ToolResult(tool=tool, ok=bridge_ok, source=source, directive=directive,
                            data={"directive_payload": payload, "response": response})
        if isinstance(response, dict) and isinstance(response.get("snapshot"), dict):
            result.data["snapshot"] = response["snapshot"]
        if not bridge_ok:
            result.error = f"client directive failed: {bridge_error or 'unknown error'}"
        return result
