"""Price and parity of the knowledge index, on whichever interpreter runs it.

`agent/executor.py::_KnowledgeIndex` builds one in-memory index per knowledge kind from
`data/cache/*.json`. This tool answers the two questions #58 needs kept honest:

    python tools/knowledge_index_probe.py snapshot <out.json>  # search answers + residency
    python tools/knowledge_index_probe.py compare <a> <b>      # did a refactor change an answer?
    python tools/knowledge_index_probe.py stages               # peak vs commit vs settled per kind
    python tools/knowledge_index_probe.py latency [repeats]    # per-query cost, cache emptied each time
    python tools/knowledge_index_probe.py breakdown            # where the index's memory goes, held vs charged

Why both: the 2026-11-03 measurement showed the item kind costing 121.7 MB committed for 16.2 MB of
searchable text, so the cost is object overhead and the JSON parse transient, not the corpus. A
compact store is only acceptable if `knowledge.search` keeps answering identically -- which is what
`compare` is for, and the snapshot must be taken before the refactor, on the shipped interpreter
(`runtimes/python/python.exe`, 3.11.9) as well as the development one.

Residency comes from `GetProcessMemoryInfo` through psapi, because psutil is not in the embedded
runtime; `argtypes` must be declared or the call silently returns 0. `EmptyWorkingSet` is used to
separate live objects from pages the heap has not handed back.
"""
from __future__ import annotations

import ctypes
import gc
import json
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CACHE_DIR = REPO_ROOT / "data" / "cache"

# The latency arm's own query set: high-candidate words, because the cost the packed name region
# added is per *candidate* row, not per query. Ordinary probes mostly match a handful of rows and
# would never show a decode regression.
LATENCY_QUERIES = [
    ("epee", "item", 5),
    ("potion", "item", 5),
    ("anneau", "item", 5),
    ("bague", "item", 5),
    ("manteau", "any", 5),
    ("bouftou", "any", 5),
]

# (query, kind, limit). Chosen to cover every kind, `any`, an unmapped kind, the LRU repeat path and
# both name-match tiers (exact, prefix, description-only, token-only).
PROBES = [
    ("bouftou", "item", 5),
    ("laine", "any", 3),
    ("Bouftou", "monster", 3),
    ("temporix", "quest", 5),
    ("ram", "spell", 3),
    ("Astrub", "npc", 3),
    ("crocodusse", "monster", 5),
    ("pelle", "any", 5),
    ("dofus pourpre", "item", 4),
    ("mimo", "any", 2),
    ("zeth", "dungeon", 3),
    ("haut", "achievement", 3),
    ("glouton", "any", 4),
]

KINDS = ("monster", "quest", "item", "npc", "spell", "dungeon", "achievement")


class _PMC(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("cbUsage", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


def _psapi():
    if sys.platform != "win32":
        return None
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PMC), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    psapi.EmptyWorkingSet.argtypes = [ctypes.c_void_p]
    psapi.EmptyWorkingSet.restype = wintypes.BOOL
    return psapi


def residency(trim=False):
    """(resident, peak, commit) MB from the OS, or zeros where the API is absent."""
    psapi = _psapi()
    if psapi is None:
        return (0.0, 0.0, 0.0)
    pmc = _PMC()
    pmc.cb = ctypes.sizeof(_PMC)
    handle = ctypes.c_void_p(-1)
    if trim:
        psapi.EmptyWorkingSet(handle)
    if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
        return (0.0, 0.0, 0.0)
    return tuple(v / 1048576.0 for v in
                 (pmc.WorkingSetSize, pmc.PeakWorkingSetSize, pmc.PagefileUsage))


def snapshot(out_path: Path) -> int:
    import agent.executor as ex

    index = ex._KnowledgeIndex(CACHE_DIR)
    results = {}
    ram = {}
    for kind in KINDS:
        t0 = time.perf_counter()
        entries = index.entries(kind)
        build = time.perf_counter() - t0
        gc.collect()
        ws, peak, commit = residency()
        ram[kind] = {"entries": len(entries), "build_s": round(build, 3),
                     "resident_mb": round(ws, 1), "peak_mb": round(peak, 1),
                     "commit_mb": round(commit, 1)}
    for query, kind, limit in PROBES:
        results["%s|%s|%d" % (query, kind, limit)] = index.search(query, kind, limit)
    gc.collect()
    ws, peak, commit = residency()
    ram["after_probes"] = {"resident_mb": round(ws, 1), "peak_mb": round(peak, 1),
                           "commit_mb": round(commit, 1)}
    results["repeat"] = index.search("bouftou", "item", 5)
    results["unmapped_kind"] = index.search("bouftou", "nonsense", 3)
    results["blank_query"] = index.search("   ", "any", 3)
    out_path.write_text(json.dumps({"python": sys.version.split()[0],
                                    "results": results, "ram": ram},
                                   indent=1, sort_keys=True, default=str),
                        encoding="utf-8")
    print(json.dumps(ram, indent=1, sort_keys=True))
    return 0


def compare(left: Path, right: Path) -> int:
    a = json.loads(left.read_text(encoding="utf-8"))
    b = json.loads(right.read_text(encoding="utf-8"))
    keys = sorted(set(a["results"]) | set(b["results"]))
    diffs = [k for k in keys if a["results"].get(k) != b["results"].get(k)]
    print("compared %d query arms on python %s vs %s; answers identical: %s"
          % (len(keys), a.get("python"), b.get("python"), not diffs))
    for key in diffs[:8]:
        print("  DIFF %s\n    before %s\n    after  %s"
              % (key, a["results"].get(key), b["results"].get(key)))
    before = a.get("ram", {}).get("after_probes", {}).get("commit_mb")
    after = b.get("ram", {}).get("after_probes", {}).get("commit_mb")
    print("commit after all kinds: before=%s MB after=%s MB" % (before, after))
    return 0 if not diffs else 1


def stages() -> int:
    """Per-kind resident / peak / commit across the build, with a forced working-set trim."""
    import agent.executor as ex

    base = residency()
    print("python %s  baseline resident=%.1f commit=%.1f" % (sys.version.split()[0], base[0], base[2]))
    for kind in ("item", "achievement", "npc"):
        index = ex._KnowledgeIndex(CACHE_DIR)
        entries = index.entries(kind)
        rows = len(entries)
        gc.collect()
        after_entries = residency()
        offsets, blob = index._hay_for(kind)
        gc.collect()
        after_hay = residency()
        text_bytes = len(blob)
        del index, entries, offsets, blob
        gc.collect()
        released = residency(trim=True)
        print("%-12s rows=%6d text=%9d bytes | entries res=%.1f commit=%.1f | "
              "haystack res=%.1f peak=%.1f commit=%.1f | released commit=%.1f"
              % (kind, rows, text_bytes, after_entries[0], after_entries[2],
                 after_hay[0], after_hay[1], after_hay[2], released[2]))
    return 0


def latency(repeats: int = 5) -> int:
    """Per-query cost of `knowledge.search`, with the answer cache emptied between calls.

    The index stores each row's names inside the haystack blob and decodes them only for the rows
    whose names region contains the query (§L.14). That is a win for memory and a new cost per
    *candidate* row, so it needs a witness that reads time rather than bytes: the residency
    numbers can look better while a query gets slower, and nothing else in the tree would notice.

    The cache is cleared before every timed call, so this measures the scan, not the LRU.
    `LATENCY_QUERIES` are deliberately high-candidate French item words -- the shape that decodes
    the most rows -- alongside the ordinary probes.
    """
    import agent.executor as ex

    index = ex._KnowledgeIndex(CACHE_DIR)
    for kind in KINDS:
        index.entries(kind)
    rows = {"python": sys.version.split()[0], "queries": {}}
    for query, kind, limit in list(PROBES) + LATENCY_QUERIES:
        timings = []
        for _ in range(max(1, repeats)):
            index._search_cache.clear()
            t0 = time.perf_counter()
            hits = index.search(query, kind, limit)
            timings.append((time.perf_counter() - t0) * 1000.0)
        timings.sort()
        key = "%s|%s|%d" % (query, kind, limit)
        rows["queries"][key] = {
            "mean_ms": round(sum(timings) / len(timings), 3),
            "p95_ms": round(timings[min(len(timings) - 1, int(0.95 * len(timings)))], 3),
            "hits": len(hits),
        }
        print("%-34s mean %7.3f ms  p95 %7.3f ms  hits %d"
              % (key, rows["queries"][key]["mean_ms"], rows["queries"][key]["p95_ms"],
                 len(hits)))
    worst = max(rows["queries"].values(), key=lambda v: v["mean_ms"])
    print("worst mean: %.3f ms over %d arms (python %s)"
          % (worst["mean_ms"], len(rows["queries"]), rows["python"]))
    if out_path := os.environ.get("LATENCY_OUT"):
        Path(out_path).write_text(json.dumps(rows, indent=1, sort_keys=True), encoding="utf-8")
    return 0


def breakdown() -> int:
    """Where the index's memory goes, with what is *held* kept apart from what is *charged*.

    Written after a plausible saving was measured and rejected: keeping the grow-in-place
    `bytearray` instead of copying it to `bytes` (dropping a 25 MB duplicate) moved commit
    77.6 -> 80.3 MB and left residency unchanged, because an over-grown buffer keeps its spare
    capacity. That is only visible if the components are added up against both counters, so they
    are printed here: residency is the honest size of the index, commit is what Windows keeps
    charged for memory the build freed, and the gap between them is not a thing to optimize away.

    `python tools/knowledge_index_probe.py breakdown`
    """
    import gc
    import sys as _sys

    import agent.executor as ex

    gc.collect()
    resident0, _peak0, commit0 = residency()
    index = ex._KnowledgeIndex(CACHE_DIR)
    for kind in KINDS:
        index.entries(kind)
    index.search("bouftou", "any", 5)
    gc.collect()
    # Not trimmed: `EmptyWorkingSet` unmaps the interpreter's pages as well, so a resident reading
    # taken straight after it says ~0.7 MB for a process holding 36 MB, and a "live bytes" number
    # built from it is nonsense (the first version of this arm reported -21.9 MB). The daemon's
    # eviction cadence measures residency its own way; here the pair that matters is what is
    # mapped now against what is charged, because the difference is freed build memory.
    resident1, peak1, commit1 = residency()
    parts = {
        "haystack blobs": sum(len(index._hay_for(k)[1]) for k in KINDS),
        "row-offset arrays": sum(index._hay_for(k)[0].itemsize * len(index._hay_for(k)[0])
                                 for k in KINDS),
        "name-end arrays": sum(a.itemsize * len(a) for a in index._nend.values()),
        "projection blobs": sum(len(v[1]) for v in index._val.values()),
        "row tuple shells": sum(_sys.getsizeof(row) for k in KINDS
                               for row in index.entries(k)),
        "row int objects": sum(_sys.getsizeof(row[0]) + _sys.getsizeof(row[3])
                               for k in KINDS for row in index.entries(k)
                               if isinstance(row[0], int) and isinstance(row[3], int)),
    }
    rows = sum(len(index.entries(k)) for k in KINDS)
    accounted = sum(parts.values()) / 1048576.0
    print("python %s, %d rows over %d kinds" % (_sys.version.split()[0], rows, len(KINDS)))
    for name, value in parts.items():
        print("  %-20s %7.2f MB" % (name, value / 1048576.0))
    print("  %-20s %7.2f MB" % ("accounted", accounted))
    print("resident: %.1f -> %.1f MB (the process now maps %.1f MB more)"
          % (resident0, resident1, resident1 - resident0))
    print("commit: %.1f -> %.1f MB (charged %.1f, of which %.1f is memory the build freed and "
          "Windows still holds charged)"
          % (commit0, commit1, commit1 - commit0,
             max(0.0, (commit1 - commit0) - accounted)))
    print("peak during the build: %.1f MB" % peak1)
    print("live set %.1f MB vs accounted %.1f MB: the %.1f MB is memory the build passed through "
          "and neither CPython's arenas nor Windows returned -- NOT a per-row cost. The index's "
          "own bytes are the %.1f MB accounted above; `snapshot`'s per-kind readings, taken after "
          "the working set is emptied and touched again, are the live figure."
          % (resident1 - resident0, accounted,
             max(0.0, (resident1 - resident0) - accounted), accounted))
    return 0


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    mode = argv[0]
    if mode == "snapshot":
        return snapshot(Path(argv[1]))
    if mode == "compare":
        return compare(Path(argv[1]), Path(argv[2]))
    if mode == "stages":
        return stages()
    if mode == "breakdown":
        return breakdown()
    if mode == "latency":
        return latency(int(argv[1]) if len(argv) > 1 else 5)
    print("unknown mode %r" % mode)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
