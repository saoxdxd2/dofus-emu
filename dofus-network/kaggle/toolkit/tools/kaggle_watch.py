"""Watch a Kaggle kernel and pull the results when it finishes.

    python tools/kaggle_watch.py --once                  # just report status
    python tools/kaggle_watch.py                         # poll until done, then fetch
    python tools/kaggle_watch.py --ref tajdinenizar/thx-01-encoder-train --interval 120

On completion it downloads the kernel output with the Kaggle CLI, prints
``summary.json`` (per split, per language) and lists the artifacts. Exit code 0
only when the run finished without error, so it can gate a follow-up step.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kaggle_mcp import KaggleMCP  # noqa: E402

DONE = {"COMPLETE", "COMPLETED"}
FAILED = {"ERROR", "FAILED", "CANCELLED", "CANCELED"}


def status(client: KaggleMCP, owner: str, slug: str) -> str:
    """Version status via the CLI.

    The MCP session endpoint reports the *interactive* session and returns
    UNKNOWN for a queued batch run; ``kaggle kernels status`` reports the version.
    """
    ref = f"{owner}/{slug}"
    completed = subprocess.run(["kaggle", "kernels", "status", ref],
                               capture_output=True, text=True, timeout=120)
    text = (completed.stdout + completed.stderr).strip()
    for token in ("COMPLETE", "ERROR", "QUEUED", "RUNNING", "CANCELED", "CANCELLED"):
        if f'"{token}"' in text or token in text.upper():
            return token
    return text[:60] or "UNKNOWN"


def fetch(ref: str, out_dir: Path) -> bool:
    out_dir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        ["kaggle", "kernels", "output", ref, "-p", str(out_dir), "-o"],
        capture_output=True, text=True)
    if completed.returncode != 0:
        print(f"download failed: {completed.stderr.strip()[:400]}", file=sys.stderr)
        return False
    print(f"downloaded -> {out_dir}")
    return True


def report(out_dir: Path) -> int:
    summary_path = out_dir / "summary.json"
    if not summary_path.exists():
        print("no summary.json in the output", file=sys.stderr)
        for path in sorted(out_dir.rglob("*")):
            if path.is_file():
                print(f"  {path.relative_to(out_dir)}  {path.stat().st_size / 1e6:.2f} MB")
        return 1

    summary: Dict[str, Any] = json.loads(summary_path.read_text(encoding="utf-8"))
    print(f"\n{'split':12s} {'cases':>6s} {'tool':>8s} {'call':>8s}")
    for split, data in summary.items():
        print(f"{split:12s} {data['cases']:6d} {data['tool']:8.1%} {data['call']:8.1%}")
        langs = data.get("by_lang") or {}
        if langs:
            line = "  ".join(f"{lang}:{stats['tool']:.0%}/{stats['call']:.0%}"
                             f"({stats['cases']})" for lang, stats in sorted(langs.items()))
            print(f"             per language (tool/call): {line}")

    print("\nbar to clear: val > 71% tool / > 40% call, gold > 56% call")
    for path in sorted(out_dir.rglob("*")):
        if path.is_file() and path.suffix in (".pt", ".onnx", ".json", ".zip"):
            print(f"  {path.relative_to(out_dir)}  {path.stat().st_size / 1e6:.2f} MB")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="tajdinenizar/thx-01-encoder-train")
    parser.add_argument("--out", default=str(Path.home() / "kaggle_outputs"))
    parser.add_argument("--interval", type=int, default=120, help="seconds between polls")
    parser.add_argument("--timeout", type=int, default=4 * 3600, help="give up after N seconds")
    parser.add_argument("--once", action="store_true", help="print status and exit")
    parser.add_argument("--no-download", action="store_true")
    args = parser.parse_args(argv)

    owner, _, slug = args.ref.partition("/")
    client = KaggleMCP()
    started = time.time()

    while True:
        current = status(client, owner, slug)
        elapsed = int(time.time() - started)
        print(f"[{elapsed:5d}s] {args.ref}: {current}", flush=True)
        if args.once or current in DONE or current in FAILED:
            break
        if elapsed > args.timeout:
            print("timeout waiting for the kernel", file=sys.stderr)
            return 2
        time.sleep(max(15, args.interval))

    if args.once:
        return 0
    if current in FAILED:
        print(f"kernel ended in {current}", file=sys.stderr)
        return 1
    if args.no_download:
        return 0
    out_dir = Path(args.out) / slug
    if not fetch(args.ref, out_dir):
        return 1
    return report(out_dir)


if __name__ == "__main__":
    raise SystemExit(main())
