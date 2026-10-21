"""Publish a finished run so the next one continues from it instead of restarting.

The loop this closes:

    run v8 (scratch)  ->  model.pt
        |
        |  python tools/kaggle_continue.py          # download, stage, push
        v
    dataset thx01-encoder-core vN+1  (contains data/checkpoints/encoder30_prev.pt)
        |
        v
    run vN+1  ->  kernel sees the staged checkpoint and resumes from it

Usage::

    python tools/kaggle_continue.py --ref tajdinenizar/thx-01-encoder-train
    python tools/kaggle_continue.py --from C:/path/to/model.pt   # skip the download
    python tools/kaggle_continue.py --dry-run                    # show what would happen
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

REPO_ROOT = Path(__file__).resolve().parent.parent
STAGING = Path.home() / "AppData" / "Local" / "Temp" / "opencode" / "kaggle_push"
DATASET_REF = "tajdinenizar/thx01-encoder-core"
KERNEL_REF = "tajdinenizar/thx-01-encoder-train"


def run(command: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print("+", " ".join(command), flush=True)
    completed = subprocess.run(command, capture_output=True, text=True)
    if check and completed.returncode != 0:
        raise SystemExit(f"command failed: {completed.stderr.strip()[:400]}")
    if completed.stdout.strip():
        print(completed.stdout.strip()[-600:], flush=True)
    return completed


def wait_for_dataset_ready(timeout: int = 600) -> None:
    started = time.time()
    while time.time() - started < timeout:
        completed = subprocess.run(
            ["python", str(REPO_ROOT / "tools" / "kaggle_mcp.py"), "call",
             "get_dataset_status",
             '{"ownerSlug":"tajdinenizar","datasetSlug":"thx01-encoder-core"}'],
            capture_output=True, text=True, cwd=str(REPO_ROOT))
        if '"READY"' in completed.stdout:
            print("dataset READY", flush=True)
            return
        time.sleep(20)
    print("dataset still processing; the kernel push may queue", flush=True)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default=KERNEL_REF)
    parser.add_argument("--from", dest="source", default=None,
                        help="use this model.pt instead of downloading kernel output")
    parser.add_argument("--dataset-ref", default=DATASET_REF)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-kernel-push", action="store_true",
                        help="publish the checkpoint but do not start a new run")
    args = parser.parse_args(argv)

    model = Path(args.source) if args.source else None
    if model is None:
        out_dir = Path.home() / "kaggle_outputs" / args.ref.split("/")[-1]
        run(["kaggle", "kernels", "output", args.ref, "-p", str(out_dir), "-o"])
        candidates = sorted(out_dir.rglob("model.pt"), key=lambda p: p.stat().st_mtime)
        if not candidates:
            raise SystemExit(f"no model.pt under {out_dir}")
        model = candidates[-1]
    if not model.exists():
        raise SystemExit(f"checkpoint not found: {model}")

    staged = STAGING / "data" / "checkpoints"
    staged.mkdir(parents=True, exist_ok=True)
    target = staged / "encoder30_prev.pt"
    print(f"staging {model} ({model.stat().st_size / 1e6:.1f} MB) -> {target}")
    if not args.dry_run:
        shutil.copyfile(model, target)

    if args.dry_run:
        print("dry run: would push the dataset and start a new kernel run")
        return 0

    run(["kaggle", "datasets", "version", "-p", str(STAGING), "-m",
         "checkpoint for continue-training", "--dir-mode", "zip"])
    wait_for_dataset_ready()

    if args.no_kernel_push:
        print("checkpoint published; no new run started")
        return 0

    kernel_dir = Path.home() / "AppData" / "Local" / "Temp" / "opencode" / "kaggle_push_kernel"
    kernel_dir.mkdir(parents=True, exist_ok=True)
    # Always stage the current script, so a resume run never ships a stale kernel.
    shutil.copyfile(REPO_ROOT / "colab" / "kaggle_train_encoder30.py",
                    kernel_dir / "kaggle_train_encoder30.py")
    if not (kernel_dir / "kernel-metadata.json").exists():
        raise SystemExit(f"missing kernel-metadata.json in {kernel_dir}")
    run(["kaggle", "kernels", "push", "-p", str(kernel_dir)])
    print(f"next run will resume from the staged checkpoint. "
          f"Watch: python tools/kaggle_watch.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
