"""Micro-benchmark for the navigation/NLP hot paths.

Run with:
    python scripts/bench_navigation.py
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dofus-network"))

from navigation.world_graph import DofusWorldGraph, MapCoord, CellPathfinder  # noqa: E402
from navigation.resource_graph import ResourceKnowledgeGraph  # noqa: E402
from navigation.quest_graph import QuestKnowledgeGraph  # noqa: E402
from navigation.nlp_intent_engine import DofusNLPEngine  # noqa: E402


def bench(label, fn, n=2000):
    fn()  # warm
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    dt = time.perf_counter() - t0
    print(f"{label:42s} {dt/n*1e6:9.1f} us/call  ({n} calls, {dt:.3f}s)")


def main():
    wg = DofusWorldGraph()
    rg = ResourceKnowledgeGraph()
    qg = QuestKnowledgeGraph()
    nlp = DofusNLPEngine()

    bench("find_nearest_zaap (repeat)", lambda: wg.find_nearest_zaap(MapCoord(4, -19)))
    bench("find_path short (allow_zaap)", lambda: wg.find_path((4, -19), (12, -22)))
    bench("find_path long (allow_zaap)", lambda: wg.find_path((-32, -56), (-26, 36)))
    bench("find_path no zaap", lambda: wg.find_path((0, 0), (30, 30), allow_zaap=False))
    bench("find_zaap_by_name('astrub')", lambda: wg.find_zaap_by_name("astrub"))
    bench("cell_distance", lambda: CellPathfinder.cell_distance(280, 315), n=50000)
    bench("find_resource_by_name('fer')", lambda: rg.find_resource_by_name("fer"))
    bench("find_resource_by_name('wheat')", lambda: rg.find_resource_by_name("wheat"))
    bench("search_spots('fer', near)", lambda: rg.search_spots("fer", near_pos=(4, -19)))
    bench("generate_farm_loop('fer')", lambda: rg.generate_farm_loop("fer"), n=200)
    bench("search_guides('cawotte')", lambda: qg.search_guides("cawotte"))
    bench("search_guides('otomai')", lambda: qg.search_guides("otomai"))
    bench("nlp detect_language", lambda: nlp.detect_language("va en [5, 5]"))
    bench("nlp process_query coord", lambda: nlp.process_query("va en [12, -22]"))
    bench("nlp process_query resource", lambda: nlp.process_query("ou trouve-t-on du fer ?"))


if __name__ == "__main__":
    main()