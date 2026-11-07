"""Run the paired teacher -> distillation -> student controls on pinned partitions.

The point of this driver is attribution. Every distillation arm shares one beam-labelled corpus,
one split, one budget and one student init, and differs only in the supervision it adds:

  beam        direct solver-label distillation (the control that already exists)
  teacher     neural teacher soft KL on the same corpus
  teacherwsp  the neural teacher first WSP-edited toward the solver, then soft KL

Each distilled checkpoint then gets the same spell-head WSP, and every arm - plus the shipped
student and the raw teacher - is duelled on the dev table with the harness's own paired compare,
which refuses comparisons made on different data hashes or search settings.

Resumable: an existing artifact or result is reported and skipped, and a directory that already
holds another job's work is refused by check_resume, which reads the identities the artifacts
carry (cache checkpoint SHA, corpus source table, duel data SHA and search settings).
Nothing is promoted; the report is what the ship decision reads.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
NETWORK = ROOT / "dofus-network"
DISTILL = NETWORK / "tools" / "distill_beam.py"
PROGRAM = ROOT / "tools" / "wsp_program.py"
FIGHT = ROOT / "tools" / "wsp_fight.py"


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def command(argv, dry, log):
    print("$ " + " ".join(str(x) for x in argv), flush=True)
    if dry:
        return None
    done = subprocess.run([str(x) for x in argv], cwd=str(ROOT), capture_output=True, text=True)
    log.write("\n$ %s\n" % " ".join(str(x) for x in argv))
    log.write(done.stdout or "")
    if done.returncode:
        log.write("\nSTDERR\n%s\n" % (done.stderr or ""))
    if done.returncode != 0:
        raise SystemExit("command failed (%d); see %s" % (done.returncode, log.name))
    return done.stdout


def stage(name, argv, dry, log):
    row = {"stage": name, "argv": [str(x) for x in argv]}
    output = command(argv, dry, log)
    if output:
        row["stdout_tail"] = output.strip().splitlines()[-1][:400]
    return row


def parse_json(stdout):
    try:
        return json.loads(stdout)
    except (TypeError, ValueError):
        return None


def edit_arm(name, cache, checkpoint, args, out, log, steps):
    argv = [args.python, PROGRAM, "edit", "--cache", cache, "--head", "spell",
            "--teacher-class", args.teacher_class, "--etas", args.etas, "--out", checkpoint]
    print("$ " + " ".join(str(x) for x in argv), flush=True)
    if args.dry_run:
        return {"written": False, "dry_run": True}
    done = subprocess.run([str(x) for x in argv], cwd=str(ROOT), capture_output=True, text=True)
    log.write("\n$ %s\n%s\n" % (" ".join(str(x) for x in argv), done.stdout))
    if done.stderr:
        log.write("STDERR\n%s\n" % done.stderr)
    report = parse_json(done.stdout)
    if done.returncode == 0 and report and report.get("wrote"):
        steps.append({"stage": name, "result": report})
        return {"written": True, "eta": report.get("chosen", {}).get("eta"),
                "train_agreement": report.get("chosen", {}).get("train_agreement"),
                "heldout_agreement": report.get("chosen", {}).get("heldout_agreement")}
    if report and report.get("verdict"):
        steps.append({"stage": name, "result": report})
        return {"written": False, "verdict": report["verdict"],
                "baseline_train_agreement": report.get("baseline_train_agreement"),
                "baseline_heldout_agreement": report.get("baseline_heldout_agreement"),
                "candidates": report.get("candidates")}
    raise SystemExit("%s failed (%d); see %s" % (name, done.returncode, log.name))


def check_resume(out, args, train, edit, dev):
    """Let only the owning job continue into an existing directory.

    The artifacts carry their own identities -- a cache records the checkpoint and the table
    it was built from, a duel result records the table, seed and search settings -- so a
    resume is verified against those bytes rather than against a marker file that could be
    stale. A directory holding another job's work is refused, never quietly mixed.
    """
    problems = []
    teacher_sha = sha256(args.teacher)
    cache = out / "teacher_cache.npz"
    if cache.exists():
        import numpy as np
        with np.load(cache, allow_pickle=False) as stored:
            recorded = str(stored["checkpoint_sha256"].item())
            if recorded != teacher_sha:
                problems.append("teacher_cache belongs to checkpoint %s, not %s"
                                % (recorded[:12], teacher_sha[:12]))
            if str(stored["data_sha256"].item()) != sha256(edit):
                problems.append("teacher_cache used a different edit table")
    corpus = out / "shared.corpus.json"
    if corpus.exists():
        manifest = json.loads(corpus.read_text(encoding="utf-8"))["manifest"]
        if manifest["sources"]["data_sha256"] != sha256(train):
            problems.append("the shared corpus was harvested from a different train table")
        if (manifest["seed"], manifest["requested_games"]) != (args.seed, args.games):
            problems.append("the shared corpus used seed %s / %s games, not %s / %s"
                            % (manifest["seed"], manifest["requested_games"],
                               args.seed, args.games))
    settings = set()
    for result in sorted(out.glob("duel_*.json")):
        config = json.loads(result.read_text(encoding="utf-8"))["config"]
        if config.get("data_sha256") != sha256(dev):
            problems.append("%s was duelled on a different dev table" % result.name)
        settings.add((config.get("matchups"), config.get("matchup_seed"),
                      config.get("beam_width"), config.get("max_actions"),
                      config.get("plan_k")))
    if len(settings) > 1:
        problems.append("the recorded duels disagree on search settings: %s" % (settings,))
    if problems:
        raise SystemExit("refusing to resume %s: %s" % (out, "; ".join(problems)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--partitions", type=pathlib.Path, required=True,
                        help="directory holding train.npz edit.npz dev.npz test.npz")
    parser.add_argument("--student", type=pathlib.Path, default=NETWORK / "models" / "encounter_selfplay.pkl")
    parser.add_argument("--teacher", type=pathlib.Path, required=True, help="Kaggle teacher checkpoint")
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--games", type=int, default=120)
    parser.add_argument("--iters", type=int, default=600)
    parser.add_argument("--batch", type=int, default=64)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--temperature", type=float, default=2.0)
    parser.add_argument("--seed", type=int, default=20261105)
    parser.add_argument("--boards", type=int, default=768)
    parser.add_argument("--etas", default="0.002,0.005,0.01,0.02,0.04,0.05")
    parser.add_argument("--teacher-class", default="all")
    parser.add_argument("--matchups", type=int, default=200)
    parser.add_argument("--matchup-seed", type=int, default=71)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if os.environ.get("DOFUS_FIGHT_BACKEND", "").strip().lower() == "numpy":
        raise SystemExit("this pipeline trains, but the environment forces the inference-only "
                         "backend (DOFUS_FIGHT_BACKEND=numpy); unset it and re-run")

    partitions = args.partitions.resolve()
    train = partitions / "train.npz"
    edit = partitions / "edit.npz"
    dev = partitions / "dev.npz"
    for name in ("train", "edit", "dev", "test"):
        if not (partitions / ("%s.npz" % name)).is_file():
            raise SystemExit("missing %s partition in %s" % (name, partitions))
    out = args.out.resolve()
    if out.is_dir() and any(out.iterdir()):
        check_resume(out, args, train, edit, dev)
    out.mkdir(parents=True, exist_ok=True)
    log = (out / "runner.log").open("a", encoding="utf-8")
    steps, artifacts = [], {}

    def remember(key, path):
        artifacts[key] = {"path": str(path), "sha256": sha256(path) if path.exists() else None}

    # 1. one shared beam-labelled corpus, harvested once from the train partition only.
    corpus = out / "shared.corpus.json"
    if not corpus.exists():
        steps.append(stage("harvest", [args.python, DISTILL, "--games", args.games,
                                       "--seed", args.seed, "--data", train,
                                       "--checkpoint", args.student, "--corpus", corpus,
                                       "--out", out / "unused.pkl", "--harvest-only"],
                           args.dry_run, log))
    remember("corpus", corpus)

    def distill(key, teacher_path=None):
        checkpoint = out / ("%s.pkl" % key)
        if checkpoint.exists():
            print("skip  distill %s" % key, flush=True)
        else:
            argv = [args.python, DISTILL, "--games", args.games, "--seed", args.seed,
                    "--data", train, "--checkpoint", args.student, "--corpus", corpus,
                    "--out", checkpoint, "--iters", args.iters, "--batch", args.batch,
                    "--lr", args.lr]
            if teacher_path is not None:
                argv += ["--teacher-checkpoint", teacher_path, "--temperature", args.temperature]
            steps.append(stage("distill:" + key, argv, args.dry_run, log))
        remember(key, checkpoint)
        return checkpoint

    # 2. WSP the neural teacher itself, exactly as the readout recipe does for a student.
    teacher_cache = out / "teacher_cache.npz"
    if not teacher_cache.exists():
        steps.append(stage("cache:teacher", [args.python, PROGRAM, "cache", "--boards", args.boards,
                                              "--data", edit, "--checkpoint", args.teacher,
                                              "--out", teacher_cache], args.dry_run, log))
    programmed_teacher = out / "teacher_programmed.pkl"
    teacher_wsp = ({"written": True} if programmed_teacher.exists()
                   else edit_arm("edit:teacher", teacher_cache, programmed_teacher,
                                 args, out, log, steps))
    remember("teacher_programmed", programmed_teacher)

    # 3. the three supervision arms on the shared corpus.
    arms = {"distill_beam": distill("distill_beam"),
            "distill_rawteacher": distill("distill_rawteacher", args.teacher)}
    if teacher_wsp.get("written") or args.dry_run:
        arms["distill_progteacher"] = distill("distill_progteacher", programmed_teacher)

    # 4. student-side WSP on each distilled checkpoint, then duels for everything.
    sources = {"shipped": args.student, "raw_teacher": args.teacher}
    if teacher_wsp.get("written") or args.dry_run:
        sources["teacher_programmed"] = programmed_teacher
    refusals = {}
    for key, path in arms.items():
        sources[key] = path
        student_wsp = out / ("%s_programmed.pkl" % key)
        edit_result = {"written": True} if student_wsp.exists() else None
        if edit_result is None:
            cache = out / ("%s_cache.npz" % key)
            if not cache.exists():
                steps.append(stage("cache:%s" % key, [args.python, PROGRAM, "cache",
                                                      "--boards", args.boards, "--data", edit,
                                                      "--checkpoint", path, "--out", cache],
                                   args.dry_run, log))
            edit_result = edit_arm("edit:" + key, cache, student_wsp, args, out, log, steps)
        if not edit_result.get("written") and not args.dry_run:
            refusals[key + "_programmed"] = edit_result
            continue
        sources[key + "_programmed"] = student_wsp
    sources_path = out / "sources.json"
    sources_path.write_text(json.dumps({k: str(v) for k, v in sources.items()}, indent=1),
                           encoding="utf-8")
    remember("sources", sources_path)

    results = {}
    for key, checkpoint in sources.items():
        result = out / ("duel_%s.json" % key)
        if not result.exists():
            steps.append(stage("duel:" + key, [args.python, FIGHT, "base", "--matchups", args.matchups,
                                               "--policy", "production", "--data", dev,
                                               "--matchup-seed", args.matchup_seed,
                                               "--checkpoint", checkpoint, "--out", result],
                               args.dry_run, log))
        results[key] = result

    comparisons = {}
    reference = results["shipped"]
    for key, result in results.items():
        if key == "shipped":
            continue
        argv = [args.python, FIGHT, "compare", reference, result]
        print("$ " + " ".join(str(x) for x in argv), flush=True)
        if args.dry_run:
            continue
        done = subprocess.run([str(x) for x in argv], cwd=str(ROOT), capture_output=True, text=True)
        log.write("\n$ %s\n%s\n" % (" ".join(str(x) for x in argv), done.stdout))
        if done.returncode:
            raise SystemExit("compare failed for %s: %s" % (key, done.stderr[-300:]))
        comparisons[key] = json.loads(done.stdout)
    report = {"partitions": {name: sha256(partitions / ("%s.npz" % name))
                             for name in ("train", "edit", "dev", "test")},
              "student": sha256(args.student), "teacher": sha256(args.teacher),
              "config": {k: getattr(args, k) for k in ("games", "iters", "batch", "lr", "seed",
                                                        "boards", "etas", "teacher_class",
                                                        "temperature", "matchups", "matchup_seed")},
              "teacher_programmed": teacher_wsp, "student_program_refusals": refusals,
              "artifacts": artifacts,
              "comparisons_vs_shipped": comparisons}
    (out / "report.json").write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    log.close()
    print(json.dumps({"planned_steps": len(steps), "arms": sorted(sources),
                      "report": str(out / "report.json")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
