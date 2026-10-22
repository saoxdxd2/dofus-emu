"""One command that says where every Kaggle job stands and what it produced.

The failure mode this prevents: three kernels queued, two dead from a path bug, and
no single place that says so. A run that nobody tracks is a run that teaches nothing.

    python ai/kaggle_report.py            # status of every known job
    python ai/kaggle_report.py --fetch    # download outputs of finished jobs
    python ai/kaggle_report.py --md       # rewrite docs/KAGGLE_RUNS.md

Status and metrics are kept separate on purpose: `COMPLETE` only means the process
exited. The metrics file is what says whether the run was worth anything.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
KAGGLE = Path.home() / ".workbuddy-ai" / "binaries" / "python" / "envs" / "default" / "Scripts" / "kaggle.exe"
KAGGLE_FALLBACK = "kaggle"
RUNS = REPO_ROOT / "kaggle" / "runs"
DOC = REPO_ROOT / "docs" / "KAGGLE_RUNS.md"

# Every job this project has ever pushed, with what it was meant to answer.
JOBS: List[Dict[str, Any]] = [
    {
        "slug": "thx-02-trm-hard-world",
        "what": "TRM on the noisy world (stochastic actions, stale/partial observations)",
        "metric": "trm_repair_metrics.json",
        "question": "does recursion help once the first pass is no longer enough?",
    },
    {
        "slug": "thx-02-trm-repair-v3",
        "what": "TRM recursion (K=12) on 99k behavioural rows",
        "metric": "trm_repair_metrics.json",
        "question": "does thinking longer help, and does it start lying confidently?",
    },
    {
        "slug": "thx-02-plan-chunk-3-steps-ahead",
        "what": "GRU over 3 states -> next 3 tools",
        "metric": "plan_chunk_metrics.json",
        "question": "can a chunk run without a mid-plan replan?",
    },
    {
        "slug": "thx-02-trm-repair-v2-99k-rows-k-12",
        "what": "TRM v2 — died on the dataset mount path",
        "metric": None,
        "question": "(superseded by v3)",
    },
    {
        "slug": "thx-02-trm-plan-repair",
        "what": "TRM v1 on 9.6k teacher rows",
        "metric": None,
        "question": "(superseded by v3)",
    },
]


def cli() -> str:
    return str(KAGGLE) if KAGGLE.exists() else KAGGLE_FALLBACK


def run(cmd: List[str], timeout: int = 120) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (out.stdout or out.stderr or "").strip()
    except Exception as exc:  # noqa: BLE001
        return "error: %s" % exc


def status_of(slug: str) -> str:
    raw = run([cli(), "kernels", "status", "tajdinenizar/" + slug])
    if "has status" in raw:
        return raw.split("status", 1)[1].strip().strip('"').split(".")[-1]
    if "Cannot access" in raw or "404" in raw:
        return "MISSING"
    return raw[:60] or "UNKNOWN"


def fetch(slug: str) -> Optional[Path]:
    target = RUNS / slug
    target.mkdir(parents=True, exist_ok=True)
    run([cli(), "kernels", "output", "tajdinenizar/" + slug, "-p", str(target)], timeout=300)
    return target if any(target.iterdir()) else None


def metrics_of(folder: Optional[Path], name: Optional[str]) -> Optional[Dict[str, Any]]:
    if folder is None or not name:
        return None
    path = folder / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def summarise(job: Dict[str, Any], folder: Optional[Path]) -> Dict[str, Any]:
    m = metrics_of(folder, job.get("metric"))
    row: Dict[str, Any] = {"slug": job["slug"], "what": job["what"],
                           "question": job["question"], "status": status_of(job["slug"])}
    if m:
        if "best_val_accuracy" in m:
            row["headline"] = "acc %.4f @ step %s" % (m["best_val_accuracy"], m.get("best_step"))
        elif "exact_match" in m:
            row["headline"] = "exact %.4f  per-step %s" % (
                m["exact_match"], m.get("per_horizon_accuracy"))
        else:
            row["headline"] = "metrics present"
        row["metrics"] = m
    return row


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--md", action="store_true")
    args = ap.parse_args(argv)

    rows = []
    for job in JOBS:
        folder = None
        if args.fetch:
            folder = fetch(job["slug"])
        elif (RUNS / job["slug"]).exists():
            folder = RUNS / job["slug"]
        rows.append(summarise(job, folder))

    width = max(len(r["slug"]) for r in rows)
    print("%-*s  %-10s  %s" % (width, "kernel", "status", "result"))
    for r in rows:
        print("%-*s  %-10s  %s" % (width, r["slug"], r["status"], r.get("headline", "-")))

    if args.md:
        lines = ["# Kaggle runs", "",
                 "Generated by `ai/kaggle_report.py`. A `COMPLETE` status only means the",
                 "process exited; the headline is what the run actually produced.", "",
                 "| kernel | what it tests | status | result |", "|---|---|---|---|"]
        for r in rows:
            lines.append("| `%s` | %s | %s | %s |" % (r["slug"], r["what"], r["status"],
                                                     r.get("headline", "—")))
        lines += ["", "## Questions still open", ""]
        for r in rows:
            if r["status"] not in ("COMPLETE",):
                lines.append("- **%s** — %s (%s)" % (r["slug"], r["question"], r["status"]))
        DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\nwrote %s" % DOC)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
