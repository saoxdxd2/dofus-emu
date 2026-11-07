"""Capture the player's actual sentences, because there is no real-language eval set to score on.

Every number in this project's input layer -- rules 42.9, encoder 94.1, lexical policy 97.9, Qwen Z 98.5 tool
accuracy on `val` -- is measured on sentences *we generated*. The proof is cheap to state: the live
trajectory logs contain **7 distinct goal strings**, all machine schedule goals (`source: "live"`), and
`data/command_replay.jsonl` has ten player-ish ones. `ai/prepare_encoder_sft.py:13` even documents that
`val_indist.jsonl` is a sample of train frames. So "real language understanding" currently has no
real-language holdout, and the 40 KB table versus 853M decision cannot be settled honestly.

The interim holdout is `data/utterances/novel_frames.jsonl`: 95 rows authored by hand and labelled by
construction, in the register the generator never emitted. It is **not** player data and `benchmark` does
not pretend otherwise -- it answers "does this survive unseen frames", which `val` cannot. Verdict and
paired statistics: `docs/ARCH_REASSESSMENT.md` §K.

This module is the instrument that fixes the supply side. It records what a player typed, what each layer
made of it, and whether anything resolved -- and it marks, rather than silently drops, text that already
exists in the SFT corpus, because the volume of repetition is itself a finding.

Wiring: `networkhealth/sentinel.py` is the live chat entry (`/sentinel/eval_chat`, `/sentinel/parse_command`).
Capture is append-only, wrapped so a failure can never break a command.

Usage (from ``dofus-network``; script mode, because the embedded runtime has no ``-m`` entry)::

    python -X utf8 ai/utterance_capture.py stats
    python -X utf8 ai/utterance_capture.py audit --out data/utterances/label_me.jsonl
    python -X utf8 ai/utterance_capture.py ingest --file data/utterances/novel_frames.jsonl
    python -X utf8 ai/utterance_capture.py benchmark   # rules vs lexical policy vs 30M encoder
"""
from __future__ import annotations

import argparse
import collections
import datetime as _dt
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CAPTURE_DIR = REPO_ROOT / "data" / "utterances"
CAPTURE = CAPTURE_DIR / "capture.jsonl"
LABELLED = CAPTURE_DIR / "labelled.jsonl"
SFT_FILES = ("train.jsonl", "val.jsonl", "val_indist.jsonl", "gold.jsonl")
SFT_DIR = REPO_ROOT / "data" / "core_sft_v4"

# Normalise the way the scorer does, so "Vends  5 laine" and "vends 5 laine" are one row.
_WS = re.compile(r"\s+")


def norm(text: str) -> str:
    return _WS.sub(" ", (text or "").strip().lower())


def utterance_id(text: str) -> str:
    return hashlib.sha1(norm(text).encode("utf-8")).hexdigest()[:16]


def _corpus_goals() -> Dict[str, str]:
    out = {}
    for name in SFT_FILES:
        p = SFT_DIR / name
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row.get("goal"), str):
                out.setdefault(norm(row["goal"]), name)
    return out


def record(
    text: str,
    *,
    sender: str = "",
    instance: str = "",
    lang: Optional[str] = None,
    engine: Optional[Dict[str, Any]] = None,
    model: Optional[Dict[str, Any]] = None,
    resolved_tool: Optional[str] = None,
    channel: str = "chat",
) -> Optional[Dict[str, Any]]:
    """Append one observed utterance. Never raises for a reason the caller should not see.

    Returns the row written, or None when the text was blank -- the caller must not treat a skipped
    capture as a failure of the command path.
    """
    try:
        clean = (text or "").strip()
        if not clean:
            return None
        row: Dict[str, Any] = {
            "id": utterance_id(clean),
            "iso": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "text": clean[:400],
            "sender": (sender or "")[:60],
            "instance": (instance or "")[:40],
            "channel": channel,
            "lang": lang,
            "engine": None if engine is None else {
                "intent": engine.get("intent"), "confidence": engine.get("confidence"),
                "detected_lang": engine.get("detected_lang")},
            "model": None if model is None else {
                "intent": model.get("intent"), "confidence": model.get("confidence"),
                "source": model.get("source")},
            "resolved_tool": resolved_tool,
            "unresolved": resolved_tool is None,
        }
        CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
        with CAPTURE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row
    except Exception:                              # noqa: BLE001 - a capture must never break a command
        return None


def read_capture() -> List[Dict[str, Any]]:
    if not CAPTURE.exists():
        return []
    rows = []
    for line in CAPTURE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def stats(rows: Optional[Iterable[Dict[str, Any]]] = None) -> Dict[str, Any]:
    rows = list(rows if rows is not None else read_capture())
    corpus = _corpus_goals()
    seen, uniq = set(), {}
    for r in rows:
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        uniq.setdefault(norm(r["text"]), r)
    in_corpus = sum(1 for t in uniq if t in corpus)
    langs = collections.Counter(str(r.get("lang")) for r in uniq.values())
    return {
        "records": len(rows),
        "distinct_utterances": len(uniq),
        "already_in_sft_corpus": in_corpus,
        "novel_vs_corpus": len(uniq) - in_corpus,
        "unresolved": sum(1 for r in uniq.values() if r.get("unresolved")),
        "by_lang": dict(langs),
        "per_channel": dict(collections.Counter(str(r.get("channel")) for r in uniq.values())),
    }


def audit(out: Optional[Path] = None, limit: int = 0) -> Dict[str, Any]:
    """Emit the novel utterances in the shape a human labels, so the set can be scored by tool_eval.

    The row format matches `data/core_sft_v4/*.jsonl` on purpose: one labelled set, two scorers, no fork.
    """
    s = stats()
    corpus = _corpus_goals()
    rows = []
    seen = set()
    for r in read_capture():
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        if norm(r["text"]) in corpus:
            continue
        rows.append({"id": r["id"], "goal": r["text"], "lang": r.get("lang"),
                     "tool": None, "args": {}, "needs_label": True,
                     "observed_unresolved": bool(r.get("unresolved"))})
    rows.sort(key=lambda x: (not x["observed_unresolved"], str(x["lang"])))
    if limit:
        rows = rows[:limit]
    payload = {"stats": s, "to_label": rows}
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return payload


def divergence(min_n: int = 50) -> Dict[str, Any]:
    """What the shadow session has to answer before any rule may be replaced: agreement, abstention, and
    how often the model is right where the engine is wrong. Rows come from the capture, so this stays
    honest-by-construction: it can only report sentences players actually sent.
    """
    rows, seen = [], set()
    for r in read_capture():
        if r["id"] in seen or not r.get("model"):
            continue
        seen.add(r["id"])
        rows.append(r)
    out: Dict[str, Any] = {"paired_rows": len(rows), "min_n_for_a_verdict": min_n,
                           "verdict_allowed": len(rows) >= min_n}
    if not rows:
        print("no paired rows yet: the capture has seen no sentence with a model view alongside the "
              "engine's. Nothing may be claimed about a shadow session that has not run.", flush=True)
        return out
    agree = sum(1 for r in rows if (r.get("engine") or {}).get("intent")
                == (r.get("model") or {}).get("intent"))
    abstain = sum(1 for r in rows if float((r.get("model") or {}).get("confidence") or 0.0) < 0.55)
    resolved = sum(1 for r in rows if r.get("resolved_tool"))
    out.update({"agreement_pct": round(100.0 * agree / len(rows), 1),
                "abstention_pct": round(100.0 * abstain / len(rows), 1),
                "engine_resolved_pct": round(100.0 * resolved / len(rows), 1)})
    print("%d paired sentences: agreement %.1f%%, model abstains on %.1f%%, engine resolved %.1f%%"
          % (len(rows), out["agreement_pct"], out["abstention_pct"], out["engine_resolved_pct"]), flush=True)
    if len(rows) < min_n:
        print("  n=%d is below the %d needed for a verdict -- report the counts, do not draw a "
              "conclusion" % (len(rows), min_n), flush=True)
    return out


def ingest(path: Path, channel: str = "ingest") -> Dict[str, Any]:
    """Load a hand-collected set of real messages, with or without labels.

    The logs hold none -- 250k lines sampled across every trajectory file give 0 player strings -- so the
    set has to arrive from outside: people typing to the bot, or phrases written by someone who plays.
    Labelled rows (`tool`/`args` present) go straight into the benchmark; unlabelled rows join the queue
    that `audit` emits.

    Every labelled row is checked before it can be scored. A hand-written truth set is only a ruler if the
    labels are expressible in the schema: an unregistered tool, an arg the tool has no parameter for, a
    value outside that parameter's enum or of the wrong type is a void row, not a miss -- it drags every
    arm down identically and the difference between the arms survives, so the ranking looks fine while the
    absolute numbers are junk. Rows whose tool has a required parameter the sentence never states are kept
    and counted separately: they are real messages, and they cap achievable accuracy for any system that
    refuses to invent.
    """
    from agent.schema import TOOLS_BY_NAME

    corpus = _corpus_goals()
    out: Dict[str, Any] = {"read": 0, "labelled": 0, "skipped": 0,
                           "illegal": 0, "under_specified": 0, "already_in_sft_corpus": 0}
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            out["skipped"] += 1
            continue
        text = row.get("text") or row.get("goal")
        if not isinstance(text, str) or not text.strip():
            out["skipped"] += 1
            continue
        out["read"] += 1
        tool = row.get("tool")
        labelled = isinstance(tool, str) and tool
        args = row.get("args") or {}
        void = False
        if labelled:
            spec = TOOLS_BY_NAME.get(tool)
            if spec is None or not isinstance(args, dict):
                void = True
            else:
                for key, value in args.items():
                    if key not in spec.params:
                        void = True
                        break
                    param = spec.params[key]
                    typ = param.get("type")
                    enum = param.get("enum")
                    if enum is not None and value not in enum:
                        void = True
                        break
                    if typ == "bool" and not isinstance(value, bool):
                        void = True
                        break
                    if typ in ("int", "integer", "number") and (
                            isinstance(value, bool) or not isinstance(value, (int, float))):
                        void = True
                        break
                    if typ == "str" and not isinstance(value, str):
                        void = True
                        break
        if labelled and not void:
            out["labelled"] += 1
            if norm(text) in corpus:
                out["already_in_sft_corpus"] += 1
            if any(p.get("required") and k not in args for k, p in spec.params.items()):
                out["under_specified"] += 1
        elif labelled:
            out["illegal"] += 1
            labelled = False
        rows.append({"id": row.get("id") or utterance_id(text), "goal": text.strip(),
                     "lang": row.get("lang"), "tool": tool if labelled else None, "args": args,
                     "needs_label": not labelled, "source": channel,
                     "in_sft_corpus": norm(text) in corpus})
    LABELLED.parent.mkdir(parents=True, exist_ok=True)
    with LABELLED.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return out


def mcnemar_exact(wins: int, losses: int) -> float:
    """Two-sided exact McNemar p on the discordant pairs.

    The marginal accuracies of two arms on the same 95 rows are not independent samples -- the paired floor
    is what decides a ranking, and it is far tighter than the ~20 pt single-arm interval suggests.
    """
    from math import comb

    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    tail = sum(comb(n, i) for i in range(k + 1)) * (0.5 ** n)
    return min(1.0, 2.0 * tail)


def benchmark(model: Optional[Path] = None, encoder: Optional[Path] = None) -> Dict[str, Any]:
    """Score the connected rules, the learned policy and the 30M encoder on the real-phrase set.

    One row set, one scorer, three arms -- the comparison the NPL decision actually needs, because each
    arm is a different answer to "what understands the language": a hand-written alias table, a 15 MB
    lexical classifier over character n-grams, and a 25 MB trained encoder.
    """
    from agent.commands import intent_tool, parse_command
    from agent.tool_eval import evaluate

    if not LABELLED.exists():
        print("no labelled real phrases yet: %s is absent. Nothing may be claimed about unseen language."
              % LABELLED, flush=True)
        return {}
    rows = []
    seen = set()
    for line in LABELLED.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("needs_label") or not r.get("tool") or r["id"] in seen:
            continue
        seen.add(r["id"])
        rows.append(r)
    if not rows:
        print("%d rows on file, none labelled -- the set exists but cannot be scored yet"
              % len(seen), flush=True)
        return {}
    print("%d labelled real phrases" % len(rows), flush=True)
    out: Dict[str, Any] = {"n": len(rows)}
    arms: Dict[str, List[bool]] = {}

    def report(name: str, preds: Dict[str, Any], rep: Dict[str, Any]) -> Dict[str, Any]:
        from agent.tool_eval import score_one

        t = rep["totals"]
        tool_pct = round(100.0 * t["tool_ok"] / t["cases"], 1)
        call_pct = round(100.0 * t["call_ok"] / t["cases"], 1)
        arms[name] = [score_one(r, preds.get(str(r["id"])))[1] for r in rows]
        out[f"{name}_tool"] = tool_pct
        out[f"{name}_call"] = call_pct
        out[f"{name}_by_lang"] = {
            lang: round(100.0 * v["tool_ok"] / v["cases"], 1)
            for lang, v in sorted(rep["by_lang"].items())}
        # A call miss and a wrong-tool miss are different diseases: one says the extractor is thin, the
        # other says the classifier is. Averaging them into one number hides which to fix.
        classes = {"abstain": 0, "wrong_tool": 0, "missing_arg": 0, "wrong_arg": 0}
        for r in rows:
            pred = preds.get(str(r["id"]))
            if score_one(r, pred)[1]:
                continue
            if pred is None:
                classes["abstain"] += 1
            elif pred.get("tool") != r["tool"]:
                classes["wrong_tool"] += 1
            else:
                got = pred.get("args") or {}
                want = r.get("args") or {}
                if any(k not in got for k in want):
                    classes["missing_arg"] += 1
                else:
                    classes["wrong_arg"] += 1
        out[f"{name}_misses"] = classes
        print("%-18s tool %5.1f%%  call %5.1f%%  per-lang tool %s"
              % (name, tool_pct, call_pct, out[f"{name}_by_lang"]), flush=True)
        print("%-18s   misses: %s" % ("", classes), flush=True)
        return rep

    # The incumbent is scored as the live path routes: `rule_parse` delegates to RulePolicy.choose, and
    # dropping its args here would compare a tool-only rules arm against args-carrying learned arms.
    from agent.tool_eval import rule_predictions

    rule_preds = rule_predictions(rows)
    report("connected rules", rule_preds, evaluate(rule_preds, rows))

    if model and (Path(model) / "policy.joblib").exists():
        from ai.lexical_policy import LexicalCommandPolicy
        pol = LexicalCommandPolicy.load(Path(model))
        preds = {}
        for r in rows:
            p = parse_command(r["goal"], model=pol)
            tool = intent_tool(p.get("intent"), (p.get("slots") or {}).get("action"))
            preds[str(r["id"])] = None if tool is None else {
                "tool": tool, "args": {k: v for k, v in (p.get("slots") or {}).items() if k != "action"}}
        report("learned policy", preds, evaluate(preds, rows))
    elif model:
        print("learned policy     SKIP -- no artifact at %s" % model, flush=True)

    if encoder and Path(encoder).with_name("policy.json").exists():
        try:
            from ai.onnx_policy import OnnxEncoder
            enc = OnnxEncoder(Path(encoder))
        except ImportError as exc:          # the encoder arm needs onnxruntime + tokenizers
            print("30M encoder        SKIP -- %s" % exc, flush=True)
            enc = None
        if enc is not None:
            enc_preds = enc.predict_rows(rows)
            report("30M encoder", enc_preds, evaluate(enc_preds, rows))

    names = list(arms)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            wins = sum(1 for k in range(len(rows)) if arms[a][k] and not arms[b][k])
            loses = sum(1 for k in range(len(rows)) if arms[b][k] and not arms[a][k])
            out[f"paired_{a}_vs_{b}"] = {"wins": wins, "losses": loses,
                                         "p": mcnemar_exact(wins, loses)}
            print("%s vs %s: %d rows only the first solves, %d only the second (McNemar p=%.4g)"
                  % (a, b, wins, loses, out[f"paired_{a}_vs_{b}"]["p"]), flush=True)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="capture and audit real player utterances")
    ap.add_argument("cmd", choices=["stats", "audit", "selftest", "ingest", "benchmark", "divergence"])
    ap.add_argument("--file", default="", help="rows to ingest (text/goal, optional tool/args/lang)")
    ap.add_argument("--model", default=str(REPO_ROOT / "models" / "lexical_policy"))
    ap.add_argument("--encoder", default=str(REPO_ROOT / "models" / "encoder30_v15" / "encoder_int8.onnx"))
    ap.add_argument("--out", default=str(CAPTURE_DIR / "label_me.jsonl"))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    if args.cmd == "selftest":
        return selftest()
    if args.cmd == "ingest":
        if not args.file:
            raise SystemExit("ingest needs --file")
        print(json.dumps(ingest(Path(args.file)), indent=1))
        return 0
    if args.cmd == "divergence":
        divergence()
        return 0
    if args.cmd == "benchmark":
        out = benchmark(Path(args.model), Path(args.encoder))
        if out.get("n"):
            print("  n=%d -> single-arm 95%% floor ~%.1f pt; per-language cells are ~2x that. "
                  "Rank the arms only where they differ by more than it."
                  % (out["n"], 100 * 1.96 / out["n"] ** 0.5), flush=True)
        return 0
    if args.cmd == "stats":
        print(json.dumps(stats(), indent=1))
        return 0
    payload = audit(Path(args.out), args.limit)
    print("stats:", json.dumps(payload["stats"]))
    print("wrote %d rows needing labels -> %s" % (len(payload["to_label"]), args.out))
    if not payload["to_label"]:
        print("NOTHING TO LABEL YET -- the capture path is not wired, or no player has typed. "
              "This is not a pass; it is an empty instrument.", flush=True)
    return 0


def selftest() -> int:
    """The instrument must be able to fail: duplicate folding, corpus overlap, and the empty case."""
    import tempfile
    global CAPTURE, CAPTURE_DIR
    keep = (CAPTURE, CAPTURE_DIR)
    tmp = Path(tempfile.mkdtemp(prefix="utt_"))
    CAPTURE_DIR, CAPTURE = tmp, tmp / "capture.jsonl"
    bad = 0
    try:
        if record("   ") is not None:
            print("  FAIL blank text was recorded"); bad += 1
        corpus = _corpus_goals()
        probe = next(iter(corpus)) if corpus else "vends 5 laine de bouftou"
        a = record(probe.upper() + "   ", sender="X")           # same utterance, different case/spacing
        b = record(probe, sender="Y")
        if not (a and b and a["id"] == b["id"]):
            print("  FAIL case/spacing variants did not fold to one id"); bad += 1
        s = stats()
        if s["distinct_utterances"] != 1:
            print("  FAIL distinct count %d after folding" % s["distinct_utterances"]); bad += 1
        if not s["already_in_sft_corpus"]:
            print("  FAIL a corpus sentence was not recognised as already known"); bad += 1
        novel = record("zzzqqq never-seen phrasing 12345")
        if novel is None or stats()["novel_vs_corpus"] != 1:
            print("  FAIL a novel utterance did not count as novel"); bad += 1
        payload = audit(CAPTURE_DIR / "label_me.jsonl")
        if not payload["to_label"]:
            print("  FAIL audit produced nothing to label although a novel row exists"); bad += 1
        else:
            row = payload["to_label"][0]
            if row.get("needs_label") is not True or "goal" not in row:
                print("  FAIL label row is not in the scorable shape"); bad += 1
        print("  %d records, %d distinct, %d novel, %d unresolved"
              % (s["records"], s["distinct_utterances"], stats()["novel_vs_corpus"],
                 stats()["unresolved"]))
    finally:
        CAPTURE, CAPTURE_DIR = keep
    print("utterance_capture selftest %s" % ("PASS" if bad == 0 else "FAILED (%d)" % bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
