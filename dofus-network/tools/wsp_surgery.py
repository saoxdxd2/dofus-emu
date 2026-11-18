from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import onnx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import weight_space as ws

ALLOWED = ("tool_fc.weight", "tool_fc.bias")
STALE = ws.feature_names().index("obs_stale")


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")


def append_json(path, value):
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, allow_nan=False) + "\n")


def split_episodes(source, folder):
    grouped = defaultdict(list)
    for line in Path(source).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row.get("ep"), int) or not isinstance(row.get("t"), int):
            raise ValueError("episode and step metadata required; no row-random fallback")
        grouped[row["ep"]].append((row["t"], line))
    episodes = sorted(grouped)
    if len(episodes) < 3:
        raise ValueError("at least three complete episodes required")
    counts = np.cumsum([len(grouped[e]) for e in episodes])
    first = min(range(1, len(episodes) - 1), key=lambda i: abs(counts[i - 1] / counts[-1] - .35))
    second = min(range(first + 1, len(episodes)), key=lambda i: abs(counts[i - 1] / counts[-1] - .70))
    result = {}
    for name, keys in zip(("diagnose_errors", "search_val", "frozen_temporal_test"),
                          (episodes[:first], episodes[first:second], episodes[second:])):
        lines = []
        for ep in keys:
            steps = grouped[ep]
            if len({t for t, _ in steps}) != len(steps):
                raise ValueError("duplicate episode step")
            lines.extend(line for _, line in sorted(steps))
        path = Path(folder) / (name + ".jsonl")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result[name] = {"path": str(path), "episodes": keys, "rows": len(lines),
                        "sha256": ws.sha256_file(path)}
    result["source_sha256"] = ws.sha256_file(source)
    result["ordering"] = "whole episodes ordered by ep, then t; this does not establish wall-clock chronology"
    write_json(Path(folder) / "split_lock.json", result)
    return result


def assert_sealed(folder):
    if (Path(folder) / "unsealed_once.json").exists():
        raise ValueError("holdout already unsealed; no further search or final evaluation")


def assert_unused_source(source, archive):
    digest = ws.sha256_file(source)
    for path in Path(archive).glob("*/split_lock.json"):
        if (path.parent / "unsealed_once.json").exists():
            if json.loads(path.read_text(encoding="utf-8")).get("source_sha256") == digest:
                raise ValueError("this corpus already has an unsealed holdout; provide new data")


def model_inputs(rows, base):
    x = np.asarray([r["x"] for r in rows], dtype=np.float32)
    history = base.get("history") or {}
    if history.get("steps"):
        from agent.features import HISTORY_TOKEN_OBS
        tokens = ws._history_rows(rows, obs=history.get("token") == HISTORY_TOKEN_OBS,
                                  derived=bool(history.get("derived")))
        x = np.hstack((x, np.asarray(tokens, dtype=np.float32)))
    return x


def attribute_value(attribute):
    value = onnx.helper.get_attribute_value(attribute)
    if isinstance(value, onnx.TensorProto):
        return {"dtype": value.data_type, "shape": list(value.dims),
                "values": onnx.numpy_helper.to_array(value).tolist()}
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, (float, int, str, list)):
        return value
    raise ValueError("unsupported graph attribute in raw dump: " + attribute.name)


def dump_raw(folder, model, sidecar):
    folder = Path(folder)
    manifest_path = folder / "raw_tensors.json"
    rows_path = folder / "raw_diagnosis_failures.jsonl"
    if manifest_path.exists() or rows_path.exists():
        raise ValueError("raw dump already exists; read the existing files")
    manifest = ws.export_manifest(model, sidecar)
    graph = onnx.load(str(model))
    manifest["graph"] = [{"name": n.name, "op_type": n.op_type, "inputs": list(n.input),
                          "outputs": list(n.output),
                          "attributes": {a.name: attribute_value(a) for a in n.attribute}}
                         for n in graph.graph.node]
    lock = json.loads((folder / "split_lock.json").read_text(encoding="utf-8"))
    diag = Path(lock["diagnose_errors"]["path"])
    if ws.sha256_file(diag) != lock["diagnose_errors"]["sha256"]:
        raise ValueError("diagnosis split changed")
    rows = ws.read_val_rows(diag)
    logits = ws._onnx_logits(model, rows, manifest["base"])
    predictions = np.asarray(logits).argmax(axis=1)
    inputs = model_inputs(rows, manifest["base"])
    write_json(manifest_path, manifest)
    with rows_path.open("x", encoding="utf-8") as output:
        for i, row in enumerate(rows):
            predicted = int(predictions[i])
            gains = row.get("gains") or {}
            chosen = manifest["base"]["tools"][predicted]
            suboptimal = bool(gains) and ws.gain_of_row(row, chosen) < max(gains.values()) - 1e-9
            if predicted != row["y"] or suboptimal:
                output.write(json.dumps({"row_index": i, "row": row, "model_input": inputs[i].tolist(),
                                         "raw_logits": np.asarray(logits[i]).tolist(),
                                         "predicted_index": predicted}, allow_nan=False) + "\n")
    return {"tensors": str(manifest_path), "failing_rows": str(rows_path)}


def initialize(folder, source):
    assert_unused_source(source, ws.EDITS_DIR)
    base = ws.export_manifest()
    pin = json.loads(ws.PROVENANCE_PATH.read_text(encoding="utf-8"))
    if base["base"]["sha256"] != pin["base"]["onnx_sha256"] or not ws.check_pin()["match"]:
        raise ValueError("baseline provenance mismatch")
    folder.mkdir(exist_ok=False)
    split_episodes(source, folder)
    shutil.copy2(ws.BASE_ONNX, folder / "base.onnx")
    shutil.copy2(ws.BASE_SIDECAR, folder / "base.json")
    write_json(folder / "scoreboard_state.json", {
        "base_sha256": base["base"]["sha256"], "sidecar_sha256": base["base"]["sidecar_sha256"],
        "best_path": str(folder / "base.onnx"), "best_sha256": base["base"]["sha256"],
        "touched": [], "cumulative_writes": 0, "failures": 0, "seen": [],
        "created_at": time.time(), "budget_seconds": 4 * 3600})


def episode_bootstrap(rows, base_values, candidate_values, *, seed=317, iters=4000):
    groups = defaultdict(list)
    for i, row in enumerate(rows):
        groups[row["ep"]].append(i)
    delta = np.asarray(candidate_values, dtype=float) - np.asarray(base_values, dtype=float)
    sums = np.asarray([delta[indices].sum() for indices in groups.values()])
    sizes = np.asarray([len(indices) for indices in groups.values()])
    draws = np.random.default_rng(seed).integers(0, len(groups), (iters, len(groups)))
    means = sums[draws].sum(axis=1) / sizes[draws].sum(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return {"delta": float(delta.mean()), "lo": float(lo), "hi": float(hi), "significant": bool(lo > 0)}


def protected_pass(base, candidate, rows, tools):
    y = np.asarray([r["y"] for r in rows])
    bp, cp = np.asarray(base["_predictions"]), np.asarray(candidate["_predictions"])
    bo, co = np.asarray(base["_optimal_correct"]), np.asarray(candidate["_optimal_correct"])
    fresh = np.asarray([r["x"][STALE] < .5 for r in rows])
    for mask in (fresh, ~fresh):
        if not mask.any() or co[mask].mean() < bo[mask].mean() - .0005 - 1e-12:
            return False
    f1 = []
    for c in range(len(tools)):
        tp = int(((cp == c) & (y == c)).sum())
        fp = int(((cp == c) & (y != c)).sum())
        fn = int(((cp != c) & (y == c)).sum())
        f1.append(2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.)
        mask = y == c
        if mask.sum() >= ws.COMMON_SUPPORT and (cp[mask] == c).sum() < (bp[mask] == c).sum():
            return False
    return float(np.mean(f1)) >= .15


def scoreboard_pass(verdict, rows, tools, original=None):
    if "error" in verdict or not verdict["gate"]["promote"]:
        return False
    base, candidate = verdict["_base_eval"], verdict["_candidate_eval"]
    if not protected_pass(base, candidate, rows, tools):
        return False
    if original is not None and not protected_pass(original, candidate, rows, tools):
        return False
    optimal = episode_bootstrap(rows, base["_optimal_correct"], candidate["_optimal_correct"])
    bg = [ws.gain_of_row(r, tools[p]) for r, p in zip(rows, base["_predictions"])]
    cg = [ws.gain_of_row(r, tools[p]) for r, p in zip(rows, candidate["_predictions"])]
    denominator = np.mean([ws.gain_of_row(r, r["tool"]) - ws.gain_of_row(r, r["teacher"]) for r in rows])
    gain = episode_bootstrap(rows, bg, cg)
    return ((verdict["paired_optimal"]["significant"] and optimal["significant"])
            or (denominator > 0 and gain["significant"]))


def verify_graph(base, candidate):
    models = [onnx.load(str(path)) for path in (base, candidate)]
    for model in models:
        model.graph.ClearField("initializer")
    if models[0].SerializeToString() != models[1].SerializeToString():
        raise ValueError("graph changed, not just initializer values")
    onnx.checker.check_model(str(candidate))


def verify_weights(base, reference, candidate, notes, touched, cumulative):
    maps = [{t["name"]: t for t in m["tensors"]} for m in (base, reference, candidate)]
    if maps[0].keys() != maps[1].keys() or maps[0].keys() != maps[2].keys():
        raise ValueError("tensor set changed")
    changes, norm2, delta2 = {}, 0., 0.
    for name, tensor in maps[0].items():
        arrays = []
        for mapping in maps:
            item = mapping[name]
            if item["shape"] != tensor["shape"] or item["dtype"] != tensor["dtype"]:
                raise ValueError("tensor shape or dtype changed")
            arr = np.asarray(item["values"], dtype=item["dtype"])
            if not np.isfinite(arr).all():
                raise ValueError("non-finite tensor")
            arrays.append(arr)
        b, r, c = arrays
        moved = c != r
        if ((c != b).any() or moved.any()) and name not in ALLOWED:
            raise ValueError("non-output tensor changed")
        for index in np.argwhere(moved):
            key = tuple(int(i) for i in index)
            changes[(name, key)] = (float(r[key]), float(c[key]))
        norm2 += float(np.square(b.astype(float)).sum())
        delta2 += float(np.square(c.astype(float) - b).sum())
    if not 0 < len(changes) <= 16:
        raise ValueError("actual changed floats outside 1..16")
    union = touched | set(changes)
    limit = math.floor(base["base"]["params"] * .005)
    ratio = math.sqrt(delta2 / norm2)
    if len(union) > limit or cumulative + len(changes) > limit or ratio > .05:
        raise ValueError("cumulative drift budget exceeded")
    explained = set()
    for note in notes:
        key = (note["tensor"], tuple(note["index"]))
        if key in explained or key not in changes:
            raise ValueError("duplicate or unrelated causal note")
        explained.add(key)
        feature = note["feature_index"]
        if not isinstance(feature, int) or not 0 <= feature < len(ws.feature_names()):
            raise ValueError("invalid feature index")
        if note["feature_name"] != ws.feature_names()[feature] or not note.get("reason", "").strip():
            raise ValueError("missing feature name or causal explanation")
        if not note.get("predicted_tool") or not note.get("true_tool"):
            raise ValueError("missing confusion pair")
        if (note["old_value"], note["new_value"]) != changes[key]:
            raise ValueError("causal note does not match actual float values")
    if explained != set(changes):
        raise ValueError("every changed float needs an AI-authored causal note")
    return union, cumulative + len(changes)


def flipped_rows(verdict, rows, tools):
    if "error" in verdict:
        return []
    before, after = verdict["_base_eval"]["_predictions"], verdict["_candidate_eval"]["_predictions"]
    return [{"row_index": i, "ep": row["ep"], "t": row["t"], "before": tools[b],
             "after": tools[c], "target": tools[row["y"]]}
            for i, (row, b, c) in enumerate(zip(rows, before, after)) if b != c]


def score(folder, candidate, notes_path=None, final=False):
    folder, candidate = Path(folder), Path(candidate).resolve()
    assert_sealed(folder)
    state_path = folder / "scoreboard_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    lock = json.loads((folder / "split_lock.json").read_text(encoding="utf-8"))
    base_path, sidecar = folder / "base.onnx", folder / "base.json"
    reference = base_path if final else Path(state["best_path"])
    candidate_hash = ws.sha256_file(candidate)
    if ws.sha256_file(base_path) != state["base_sha256"] or ws.sha256_file(sidecar) != state["sidecar_sha256"]:
        raise ValueError("baseline snapshot changed")
    if ws.sha256_file(Path(state["best_path"])) != state["best_sha256"]:
        raise ValueError("accepted reference changed")
    verify_graph(base_path, candidate)
    base = ws.export_manifest(base_path, sidecar)
    tools = base["base"]["tools"]
    touched = {(name, tuple(index)) for name, index in state["touched"]}
    writes = state["cumulative_writes"]
    if final:
        if candidate_hash != state["best_sha256"]:
            raise ValueError("final candidate must be the last PASS artifact")
        with (folder / "unsealed_once.json").open("x", encoding="utf-8") as handle:
            json.dump({"candidate_sha256": candidate_hash}, handle)
    else:
        if state["failures"] >= 10 or time.time() - state["created_at"] >= state["budget_seconds"]:
            raise ValueError("search budget closed")
        if candidate_hash in state["seen"]:
            raise ValueError("candidate already scored")
        if notes_path is None:
            raise ValueError("supply AI-authored causal notes")
        touched, writes = verify_weights(base, ws.export_manifest(reference, sidecar),
                                         ws.export_manifest(candidate, sidecar),
                                         json.loads(Path(notes_path).read_text(encoding="utf-8")), touched, writes)
    name = "frozen_temporal_test" if final else "search_val"
    val = Path(lock[name]["path"])
    if ws.sha256_file(val) != lock[name]["sha256"]:
        raise ValueError("split hash changed")
    result = ws.candidate_verdict(candidate, val=val, reference_onnx=reference, include_details=True)
    rows = ws.read_val_rows(val)
    original = None if final else ws.score_artifact(base, rows, base_path, return_correct=True)
    passed = scoreboard_pass(result, rows, tools, original)
    if final:
        for label, command, cwd in (
            ("regression", [sys.executable, "-m", "agent.regression"], ROOT),
            ("payload", [sys.executable, str(ROOT.parent / "tools/payload_audit.py"),
                         str(ROOT.parent / "setup.exe"), "--gate"], ROOT.parent)):
            with (folder / (label + ".log")).open("w", encoding="utf-8") as output:
                completed = subprocess.run(command, cwd=cwd, stdout=output, stderr=subprocess.STDOUT)
            passed = passed and completed.returncode == 0
    else:
        state["seen"].append(candidate_hash)
        state["failures"] = 0 if passed else state["failures"] + 1
        if passed:
            state.update(best_path=str(candidate), best_sha256=candidate_hash, cumulative_writes=writes,
                         touched=[[name, list(index)] for name, index in sorted(touched)])
        write_json(state_path, state)
    output = {"status": "PASS" if passed else "FAIL", "flipped_rows": flipped_rows(result, rows, tools)}
    append_json(folder / "scoreboard.jsonl", {"candidate_sha256": candidate_hash, "final": final, **output})
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description="Raw WSP microscope and supplied-candidate scoreboard; no edit generation.")
    commands = parser.add_subparsers(dest="command", required=True)
    dump = commands.add_parser("dump")
    dump.add_argument("--session", required=True, type=Path)
    dump.add_argument("--source", type=Path, default=ws.DEFAULT_VAL)
    verify = commands.add_parser("score")
    verify.add_argument("--session", required=True, type=Path)
    verify.add_argument("--candidate", required=True, type=Path)
    verify.add_argument("--notes", type=Path, help="AI-authored JSON list: tensor, index, old_value, new_value, feature_index, feature_name, predicted_tool, true_tool, reason")
    verify.add_argument("--final", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "dump":
            if not args.session.exists():
                initialize(args.session, args.source)
            model = args.session / "base.onnx"
            sidecar = args.session / "base.json"
            result = dump_raw(args.session, model if model.exists() else ws.BASE_ONNX,
                              sidecar if sidecar.exists() else ws.BASE_SIDECAR)
        else:
            result = score(args.session, args.candidate, args.notes, args.final)
    except (ValueError, OSError, KeyError, TypeError, onnx.checker.ValidationError):
        if args.command == "dump":
            raise
        result = {"status": "FAIL", "flipped_rows": []}
    print(json.dumps(result))
    return 0 if result.get("status", "PASS") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
