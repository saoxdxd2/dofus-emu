"""Unified live data acquisition for the Dofus AI pipeline.

This is the module the Colab notebook calls so "get the live data" is one step
instead of three remembered scripts. It orchestrates:

1. **DofusDB encyclopedia sync** — real monsters and spells with trilingual names,
   grade scaling and elemental resistances (`data/sync_full_database.py`).
2. **Bot log ingestion** — real captured execution logs / packet traces parsed
   into structured turn and chat events (`data/log_dataset_builder.py`).
3. **Online corpus compilation** — grounded samples built from the synced
   encyclopedia plus the parsed logs (`OnlineCorpusBuilder`).
4. **Synthetic corpus generation** — the deterministic combat + intent corpus.

Every source is **optional and independently skippable**, and every source is
**non-fatal**: a network hiccup or a missing log directory degrades to the
synthetic corpus rather than crashing a Colab run that already spent GPU quota.
The returned report records exactly which sources contributed, so a training run
can be traced back to its data.

Usage::

    python -m data.acquire --all --logs-dir ./captures --cache-dir data/cache
    python -m data.acquire --skip-network --logs-dir ./captures
    python -m data.acquire --monsters 3000 --spells 2000
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logger = logging.getLogger("dofus.acquire")

DATA_DIR = Path(__file__).resolve().parent
CORPUS_FILE = DATA_DIR / "dofus_deep_training_corpus.json"
COMBAT_FILE = DATA_DIR / "dofus_combat_policy_dataset.json"
DECISION_FILE = DATA_DIR / "dofus_decision_dataset.json"
ACQUISITION_REPORT = DATA_DIR / "acquisition_report.json"


@dataclass
class SourceResult:
    name: str
    ok: bool
    records: int = 0
    seconds: float = 0.0
    detail: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "source": self.name,
            "ok": self.ok,
            "records": self.records,
            "seconds": round(self.seconds, 2),
            "detail": self.detail,
            "error": self.error,
        }


def _count_json(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0
    return len(payload) if isinstance(payload, list) else 0


def sync_encyclopedia(max_monsters: int, max_spells: int,
                      full_database: bool = False) -> SourceResult:
    """Pull real monsters/spells (optionally the whole DB) from the DofusDB mirror."""
    started = time.perf_counter()
    result = SourceResult(name="dofusdb_encyclopedia", ok=True)
    try:
        from data.sync_full_database import sync_core_spells, sync_monsters

        monsters = sync_monsters(max_monsters=max_monsters)
        spells = sync_core_spells(max_spells=max_spells)
        result.records = len(monsters) + len(spells)
        result.detail = {
            "monsters": len(monsters),
            "spells": len(spells),
            "monsters_with_grades": sum(1 for m in monsters if m.get("grades")),
            "monsters_trilingual": sum(
                1 for m in monsters if m.get("name_fr") and m.get("name_en") and m.get("name_es")),
        }
        if full_database:
            from data.sync_full_database import (
                sync_achievements, sync_areas, sync_dungeons, sync_items,
                sync_npcs, sync_quests, sync_subareas,
            )
            full_detail: Dict[str, Any] = {}
            for name, sync_fn in {
                "items": sync_items,
                "quests": sync_quests,
                "npcs": sync_npcs,
                "achievements": sync_achievements,
                "dungeons": sync_dungeons,
                "subareas": sync_subareas,
                "areas": sync_areas,
            }.items():
                try:
                    full_detail[name] = len(sync_fn())
                except Exception as exc:  # noqa: BLE001 - one collection must not kill the rest
                    full_detail[name] = f"failed: {type(exc).__name__}: {exc}"
            result.detail["full_database"] = full_detail
        if result.records == 0:
            result.ok = False
            result.error = "DofusDB returned no records (mirror down or rate limited)"
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        result.ok = False
        result.error = f"{type(exc).__name__}: {exc}"
        logger.warning(f"Encyclopedia sync failed (continuing): {result.error}")
    result.seconds = time.perf_counter() - started
    return result


def ingest_logs(logs_dir: Optional[Path]) -> SourceResult:
    """Parse captured bot logs into structured events."""
    started = time.perf_counter()
    result = SourceResult(name="bot_logs", ok=True)
    if logs_dir is None:
        result.detail = {"skipped": True, "reason": "no --logs-dir provided"}
        result.seconds = time.perf_counter() - started
        return result
    try:
        from data.log_dataset_builder import BotLogParser

        path = Path(logs_dir)
        if not path.exists():
            result.detail = {"skipped": True, "reason": f"{path} does not exist"}
            result.seconds = time.perf_counter() - started
            return result

        events = BotLogParser.ingest_log_directory(path)
        by_type: Dict[str, int] = {}
        for event in events:
            key = event.get("type", "UNKNOWN")
            by_type[key] = by_type.get(key, 0) + 1
        result.records = len(events)
        result.detail = {"directory": str(path), "by_type": by_type}

        # Persist the parsed events so training can consume real transitions.
        out = DATA_DIR / "captured_events.jsonl"
        with out.open("w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        result.detail["written"] = str(out)
    except Exception as exc:  # noqa: BLE001
        result.ok = False
        result.error = f"{type(exc).__name__}: {exc}"
        logger.warning(f"Log ingestion failed (continuing): {result.error}")
    result.seconds = time.perf_counter() - started
    return result


def build_online_corpus(count: int, seed: int) -> SourceResult:
    """Compile the encyclopedia-grounded corpus (real stats + parsed logs).

    ``OnlineCorpusBuilder`` writes directly to ``CORPUS_FILE``. Run naively after
    the synthetic backbone, it *destroys* it: the online templates are almost
    entirely a subset of the synthetic corpus, so the file shrinks from ~10k
    samples to a few hundred. We therefore let the builder write, capture its
    output, and restore the backbone immediately. The captured samples are then
    merged additively (de-duplicated) by the caller.
    """
    started = time.perf_counter()
    result = SourceResult(name="online_corpus", ok=True)
    try:
        from data.log_dataset_builder import OnlineCorpusBuilder

        backbone = CORPUS_FILE.read_text(encoding="utf-8") if CORPUS_FILE.exists() else None

        samples = OnlineCorpusBuilder.generate_encyclopedia_corpus(count, seed=seed)
        result.records = len(samples)

        # Capture what the builder produced, then put the backbone back.
        if CORPUS_FILE.exists():
            ONLINE_STAGING_FILE.write_text(
                CORPUS_FILE.read_text(encoding="utf-8"), encoding="utf-8")
        if backbone is not None:
            CORPUS_FILE.write_text(backbone, encoding="utf-8")

        result.detail = {
            "staged": str(ONLINE_STAGING_FILE),
            "backbone_preserved": _count_json(CORPUS_FILE),
        }
    except Exception as exc:  # noqa: BLE001
        result.ok = False
        result.error = f"{type(exc).__name__}: {exc}"
        logger.warning(f"Online corpus build failed (continuing): {result.error}")
    result.seconds = time.perf_counter() - started
    return result


ONLINE_STAGING_FILE = DATA_DIR / "_online_corpus_staging.json"


def build_synthetic_corpus(seed: int, combat_count: int) -> SourceResult:
    """Deterministic combat + trilingual intent corpus. This is the fallback."""
    started = time.perf_counter()
    result = SourceResult(name="synthetic_corpus", ok=True)
    try:
        from data.generate_combat_policy_dataset import generate_scenarios
        from data.generate_expanded_corpus import generate_natural_dataset

        scenarios = generate_scenarios(combat_count, seed=seed)
        corpus = generate_natural_dataset(seed=seed)
        CORPUS_FILE.write_text(
            json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")
        result.records = len(corpus)
        result.detail = {"combat_scenarios": len(scenarios), "written": str(CORPUS_FILE)}
    except Exception as exc:  # noqa: BLE001
        result.ok = False
        result.error = f"{type(exc).__name__}: {exc}"
        logger.error(f"Synthetic corpus build failed: {result.error}")
    result.seconds = time.perf_counter() - started
    return result


def _merge_into_corpus(extra_paths: List[Path]) -> int:
    """Merge additional sample files into the main corpus, de-duplicating by text."""
    if not CORPUS_FILE.exists():
        return 0
    try:
        corpus = json.loads(CORPUS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0

    seen = {s.get("text", "") for s in corpus}
    added = 0
    for path in extra_paths:
        if not path.exists():
            continue
        try:
            extra = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(extra, list):
            continue
        for sample in extra:
            text = sample.get("text", "")
            if not text or text in seen:
                continue
            seen.add(text)
            corpus.append(sample)
            added += 1

    if added:
        CORPUS_FILE.write_text(
            json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")
    return added


def acquire(
    seed: int = 42,
    combat_count: int = 20000,
    online_count: int = 12000,
    max_monsters: int = 2000,
    max_spells: int = 2000,
    full_database: bool = False,
    logs_dir: Optional[Path] = None,
    skip_network: bool = False,
    skip_logs: bool = False,
    prefer_online: bool = True,
    write_report: bool = True,
) -> Dict[str, Any]:
    """Run the full acquisition chain and return a provenance report."""
    sources: List[SourceResult] = []

    if not skip_network:
        sources.append(sync_encyclopedia(max_monsters, max_spells, full_database=full_database))
    else:
        sources.append(SourceResult(
            name="dofusdb_encyclopedia", ok=True, detail={"skipped": True}))

    if not skip_logs:
        sources.append(ingest_logs(logs_dir))
    else:
        sources.append(SourceResult(
            name="bot_logs", ok=True, detail={"skipped": True}))

    # Order matters: build the deterministic backbone first, then layer the
    # online corpus on top. Reversing this silently discards the backbone.
    synthetic = build_synthetic_corpus(seed, combat_count)
    sources.append(synthetic)

    if prefer_online and synthetic.ok:
        online = build_online_corpus(online_count, seed)
        sources.append(online)
        if online.ok:
            merged_online = _merge_into_corpus([ONLINE_STAGING_FILE])
            online.detail["merged_into_corpus"] = merged_online
            if ONLINE_STAGING_FILE.exists():
                ONLINE_STAGING_FILE.unlink()

    # Real labelled moderation/directive samples from the repo, de-duplicated.
    merged = _merge_into_corpus([DECISION_FILE])
    total = _count_json(CORPUS_FILE)

    report = {
        "seed": seed,
        "sources": [s.as_dict() for s in sources],
        "corpus_samples": total,
        "merged_extra_samples": merged,
        "corpus_file": str(CORPUS_FILE),
        "combat_file": str(COMBAT_FILE),
        "combat_scenarios": _count_json(COMBAT_FILE),
        "live_sources_ok": [s.name for s in sources if s.ok and not s.detail.get("skipped")],
        "degraded": [s.name for s in sources if not s.ok],
    }

    if write_report:
        ACQUISITION_REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\n" + "=" * 65)
    print("   DATA ACQUISITION SUMMARY")
    print("=" * 65)
    for source in sources:
        if source.detail.get("skipped"):
            print(f"  ⏭  {source.name:<24} skipped")
        else:
            icon = "✅" if source.ok else "⚠️"
            print(f"  {icon} {source.name:<24} {source.records:>8,} records "
                  f"({source.seconds:.1f}s)")
    print("-" * 65)
    print(f"  corpus samples : {total:,}")
    print(f"  combat samples : {report['combat_scenarios']:,}")
    print(f"  report         : {ACQUISITION_REPORT}")
    print("=" * 65 + "\n")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="data.acquire",
        description="Acquire live + synthetic data for the Dofus AI pipeline.",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--combat-count", type=int, default=20000)
    parser.add_argument("--online-count", type=int, default=12000)
    parser.add_argument("--monsters", type=int, default=2000)
    parser.add_argument("--spells", type=int, default=2000)
    parser.add_argument("--full-database", action="store_true",
                        help="Also sync items/quests/npcs/achievements/dungeons/subareas/areas")
    parser.add_argument("--logs-dir", type=Path, default=None,
                        help="Directory of captured bot logs (*.log, *.txt, *.jsonl)")
    parser.add_argument("--skip-network", action="store_true",
                        help="Do not hit DofusDB (offline / rate-limited runs)")
    parser.add_argument("--skip-logs", action="store_true")
    parser.add_argument("--all", action="store_true",
                        help="Enable every source, including the network sync")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = build_parser().parse_args(argv)
    report = acquire(
        seed=args.seed,
        combat_count=args.combat_count,
        online_count=args.online_count,
        max_monsters=args.monsters,
        max_spells=args.spells,
        full_database=args.full_database,
        logs_dir=args.logs_dir,
        skip_network=args.skip_network and not args.all,
        skip_logs=args.skip_logs,
    )
    # Only a total failure to produce any corpus is a hard error.
    return 0 if report["corpus_samples"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
