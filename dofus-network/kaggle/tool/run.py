"""THX-02: generate the counterfactual tool corpus, then train the small head.

One kernel, two stages, because the slow part of this project is not the training —
it is manufacturing labels. The training takes seconds on a CPU; the corpus takes
minutes of forking a symbolic world once per candidate tool per state. A GPU that
only trains is a GPU sitting idle while the real bottleneck runs on four vCPUs.

Stage 1 — generate. Runs `sim/tool_choice.py` with several workers against the
knowledge base shipped in the toolkit dataset. Every label is a fact about the world:
the tool whose fork completed the most objectives per step. The world charges travel
(`--travel-cost`) and reports the post-action snapshot to the planner, matching the
live loop; a free-travel corpus taught the head that moving costs nothing, and a
pre-action report retired quests the server had actually started.

Stage 2 — train the deployable head. v2 swept five capacities and found a +0.0047
best-to-worst spread; the 1.1M-parameter winner was not worth 4.3 MB in the client.
This run trains the 48.6K-parameter h192 head directly on the 42-feature corpus and
aborts if the corpus width disagrees with the live featuriser.

Self-contained by necessity: a Kaggle script kernel cannot import the repository, so
the repository is a dataset instead.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

WORK = Path("/kaggle/working")
# The corpus goes to scratch, NOT to the output directory.
#
# Writing 134 MB of jsonl into /kaggle/working made the output 134 MB, which had two
# costs that had nothing to do with the experiment: `kaggle kernels output` broke twice
# on the 100 MB+ file with IncompleteRead, and a dependent kernel that only wanted the
# 4 MB model had to mount the whole thing and sat in RUNNING for ten minutes. The
# artifact is the model; the corpus is reproducible in 26 minutes from the toolkit.
DATA = Path("/tmp/corpus")
MINUTES = 26
WORKERS = 4
EPISODES = 900
STEPS = 80
# Keep in sync with dofus-network/agent/features.py FEATURE_COUNT.
EXPECTED_FEATURES = 42


def find_toolkit() -> Path:
    """Locate the toolkit dataset, wherever Kaggle mounted it, however it arrived.

    Private datasets mount at /kaggle/input/datasets/<owner>/<slug>/, not at
    /kaggle/input/<slug>/ as the docs say. Two earlier runs died on that. And the CLI
    uploads each directory as an archive, so the mount may hold `sim.zip` rather than
    a `sim/` tree — the code should not care which, because the failure mode is a
    twenty-six-minute run that dies at import.
    """
    root = Path("/kaggle/input")
    for candidate in sorted(root.rglob("tool_choice.py")) if root.exists() else []:
        if candidate.parent.name == "sim":
            return candidate.parent.parent

    target = WORK / "toolkit"
    if not (target / "sim" / "tool_choice.py").exists() and root.exists():
        import zipfile
        for archive in sorted(root.rglob("*.zip")):
            try:
                with zipfile.ZipFile(archive) as zf:
                    zf.extractall(target)
                print("unpacked", archive.name)
            except zipfile.BadZipFile:
                print("skipped non-archive", archive.name)
    for candidate in sorted(target.rglob("tool_choice.py")):
        if candidate.parent.name == "sim":
            return candidate.parent.parent
    tree = [str(p) for p in sorted(root.rglob("*"))[:60]] if root.exists() else []
    raise SystemExit("toolkit not found under /kaggle/input; saw: %s" % tree)


def write_diagnostics(toolkit: Path) -> None:
    """A file in the output survives a crash; the log often does not."""
    root = Path("/kaggle/input")
    payload = {
        "input_exists": root.exists(),
        "toolkit": str(toolkit),
        "entries": [str(p) for p in sorted(root.rglob("*"))[:80]] if root.exists() else [],
        "quests_present": (toolkit / "data/cache/quests_full.json").exists(),
        "capabilities_present": (toolkit / "data/capabilities.json").exists(),
    }
    (WORK / "diagnostics.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print("diagnostics:", json.dumps({k: v for k, v in payload.items() if k != "entries"}))


def generate(toolkit: Path) -> tuple[dict, Path]:
    print("\n== stage 1: generate the corpus ==", flush=True)
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        probe = DATA / ".writable"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        out_dir = DATA
    except OSError:
        # Some images restrict /tmp. Falling back to the output directory is correct
        # but makes the run slow to collect, so say so rather than doing it silently.
        out_dir = WORK / "corpus"
        print("WARNING: /tmp is not writable; the corpus will inflate the output "
              "directory to ~%d MB" % 150, flush=True)
    cmd = [sys.executable, str(toolkit / "sim" / "tool_choice.py"),
           "--episodes", str(EPISODES), "--steps", str(STEPS),
           "--noise", "--travel-cost", "--ordering", "nearest",
           "--parallel", str(WORKERS),
           "--minutes", str(MINUTES), "--seed", "2026",
           "--out", str(out_dir)]
    env = dict(os.environ, PYTHONPATH=str(toolkit))
    started = time.time()
    proc = subprocess.run(cmd, cwd=str(toolkit), env=env,
                          capture_output=True, text=True)
    print(proc.stdout[-4000:])
    if proc.returncode != 0:
        print("STDERR:", proc.stderr[-4000:])
    print("generate took %.0fs" % (time.time() - started), flush=True)
    stats_path = out_dir / "stats.json"
    stats = json.loads(stats_path.read_text(encoding="utf-8")) if stats_path.exists() else {}
    # A stale toolkit dataset silently produced a 41-wide corpus while the live
    # featuriser was 42; the exported ONNX would then fall back at load time. Read
    # the actual row, not a version string.
    train_path = out_dir / "train.jsonl"
    width = 0
    if train_path.exists():
        with train_path.open("r", encoding="utf-8") as fh:
            first = fh.readline()
        width = len(json.loads(first)["x"]) if first else 0
    if width != EXPECTED_FEATURES:
        raise SystemExit("corpus is %d-wide; the live featuriser is %d"
                         % (width, EXPECTED_FEATURES))
    # The statistics are small and are the part worth keeping.
    (WORK / "corpus_stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    return stats, out_dir


def train(toolkit: Path, data_dir: Path) -> int:
    print("\n== stage 2: train the 42-feature h192 head ==", flush=True)
    trainer = toolkit / "ai" / "train_tool_policy.py"
    if not trainer.exists():
        raise SystemExit("trainer missing from the toolkit: %s" % trainer)
    # v2 swept five capacities and found a +0.0047 best-to-worst spread; the
    # 1.1M-parameter winner was not worth 4.3 MB in the client. Train the small
    # deployable head directly. The sweep report from v2 remains the capacity
    # evidence; this run exists to produce a 42-feature artifact.
    cmd = [sys.executable, str(trainer), "--data", str(data_dir),
           "--out", str(WORK / "tool_policy.onnx"),
           "--epochs", "120", "--batch", "4096", "--lr", "3e-3",
           "--hidden", "192", "--depth", "2", "--device", "cuda"]
    env = dict(os.environ, PYTHONPATH=str(toolkit))
    proc = subprocess.run(cmd, cwd=str(toolkit), env=env, capture_output=True, text=True)
    print(proc.stdout[-12000:])
    if proc.returncode != 0:
        print("STDERR:", proc.stderr[-4000:])
    return proc.returncode


def main() -> int:
    print("THX-02 build 2026-10-24d: 42 features, travel-cost corpus, h192 head",
          flush=True)
    toolkit = find_toolkit()
    print("toolkit:", toolkit)
    write_diagnostics(toolkit)
    if (toolkit / "ai/train_tool_policy.py").exists() is False:
        # The trainer ships with the toolkit; if it is missing, stage 2 cannot run and
        # the run should say so now rather than after twenty-six minutes of forking.
        print("WARNING: trainer not in toolkit")
    stats, data_dir = generate(toolkit)
    print("\ncorpus:", json.dumps(stats, indent=1))
    code = train(toolkit, data_dir)
    if code == 0 and data_dir != DATA:
        # The fallback corpus lives in the output directory; keeping 130 MB of
        # reproducible jsonl makes Kaggle take minutes to finalize the run and blocks
        # the next queued version. The model is the artifact.
        shutil.rmtree(data_dir, ignore_errors=True)
    for name in ("tool_policy.onnx", "tool_policy.json", "tool_policy_report.json"):
        p = WORK / name
        print("%-26s %s" % (name, "ok %d bytes" % p.stat().st_size if p.exists() else "MISSING"))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
