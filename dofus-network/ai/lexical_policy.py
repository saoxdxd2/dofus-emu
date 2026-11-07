"""A learned command policy that speaks the seam `agent/commands.py` already defines.

`parse_command(text, model=...)` asks a model first and falls back to the rules when it is unsure; the model
only needs `predict(text) -> {"intent","slots","confidence"}` (commands.py:1-13). That seam has had no
production supplier -- only `agent/regression.py` ever passed a model -- so the live path has been the
`DofusNLPEngine` alias tables plus regexes the whole time.

Measured on the repo's own splits and scorer on 2026-11-02, which is what justified writing this instead of
only training a big model:

    arm                        val tool / call    gold tool / call
    connected rules             42.9 / 29.3        63.4 / 50.7
    30M encoder (unwired)       94.1 / 69.7        94.4 / 77.5
    Qwen3.5-0.8B pooled Z       98.5 /  9.1        91.5 / 54.9

That middle column is the reason this module is small: on tool choice a character-n-gram linear model is
within ~2 pt of an 853M transformer, and on arguments the transformer's pooled vector is *worse* than the
table. A 1 GB file, a supervisor and 0.1-2.5 s per message do not buy that.

Off by default. `commands.py`'s own docstring sets the rule -- a learned model replaces a rule "only after it
beats the rule on held-out prompts and survives a shadow session" -- so nothing here changes live behaviour
until a caller passes `model=` or `DOFUS_LEARNED_COMMAND_MODEL` points at a trained artifact.

Usage (from ``dofus-network``)::

    python -m ai.lexical_policy train --out models/lexical_policy
    python -m ai.lexical_policy eval  --model models/lexical_policy
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.commands import TOOL_INTENT, _ARG_SLOTS          # noqa: E402  the seam's own mapping
from agent.schema import TOOLS                              # noqa: E402

# slot name -> tool-argument name. _ARG_SLOTS is the forward map; retrieval of a rule slot has to land
# back on the key the schema validates.
_SLOT_ARG = {v: k for k, v in _ARG_SLOTS.items()}

SFT_DIR = REPO_ROOT / "data" / "core_sft_v4"
DEFAULT_OUT = REPO_ROOT / "models" / "lexical_policy"
ENV_MODEL = "DOFUS_LEARNED_COMMAND_MODEL"
MAX_DOMAIN = 30          # a field with at most this many distinct values gets a classifier, else retrieval
MIN_ROWS = 25


def read_jsonl(p: Path) -> List[Dict[str, Any]]:
    with p.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _fold(text: str) -> str:
    """Lowercase, accents dropped, punctuation to spaces -- the same reason `_match_arg` in tool_eval
    ignores accents and case. Kept local so the policy and the scorer disagree in one direction only."""
    import unicodedata
    return re.sub(r"[^a-z0-9 ]+", " ", unicodedata.normalize("NFKD", (text or "").lower())
                  .encode("ascii", "ignore").decode("ascii"))


def recount(tool: str, slots: Dict[str, Any]) -> Dict[str, Any]:
    """Move a leading count out of a span into the number field it was blocking.

    `RulePolicy().choose("vends 5 bouftou wool")` returns ``item='5 bouftou wool'`` and no quantity --
    that is the connected path's own behaviour, which the hybrid then inherits. The digit is stated in the
    sentence, so relocating it is a repair, not a guess, and the schema wants both fields.
    """
    from agent.schema import TOOLS_BY_NAME

    spec = TOOLS_BY_NAME.get(tool)
    if spec is None:
        return slots
    out = dict(slots)
    for key, value in list(out.items()):
        params = spec.params
        if key not in params or str(params[key].get("type")) != "str" or not isinstance(value, str):
            continue
        m = re.match(r"^\s*(\d+)\s+(\S.*)$", value)
        if not m:
            continue
        digits, rest = m.group(1), m.group(2).strip()
        numeric = [f for f, sp in params.items()
                   if str(sp.get("type")) in ("int", "float") and f not in out]
        if not numeric or not rest:
            continue
        # a bare leading integer is a count, not a price: prefer `quantity`, then any numeric that is
        # not a price/level, which the sentence would have named if it meant one.
        target = "quantity" if "quantity" in numeric else next(
            (f for f in numeric if f not in ("price", "level_min", "duration_min")), None)
        if target is None or (target == "price" and "kamas" not in value.lower()):
            continue
        out[key] = rest
        out[target] = int(digits) * (1000 if target == "price" and re.search(r"\d\s*k", value) else 1)
    return out


_RE_KILL_SUFFIX = re.compile(r"\b(\d{1,6})\s*[x×]\b", re.IGNORECASE)
_RE_PRICE_K = re.compile(r"\b(\d{1,4})\s*k\b", re.IGNORECASE)
_RE_COORD_PAIR = re.compile(r"(?<![\w.])(-?\d{1,4})\s*[,;]\s*(-?\d{1,4})(?![\d])")
_RE_COORD_KV = re.compile(r"\b([xy])\s*=\s*(-?\d{1,4})", re.IGNORECASE)


def read_stated(tool: str, args: Dict[str, Any], text: str) -> Dict[str, Any]:
    """Fill numeric fields whose digits the sentence states but no span contains.

    Measured on the unseen-frame holdout (docs/ARCH_REASSESSMENT.md §K): every argument miss was a value the
    player *did* say -- `farm bouftou 100x`, `geh zu 4,-19`, `met 5 laine ah 2k` -- and the corpus generator
    never emits those shapes, so neither the trained field heads nor `recount` (which only relocates a digit
    already sitting inside a span) can see them. Fill-only, like the rest of the seam: a field the sentence
    does not state stays absent, because proposing a number the player never said is the failure mode this
    artifact is explicitly built to avoid.
    """
    from agent.schema import TOOLS_BY_NAME

    spec = TOOLS_BY_NAME.get(tool)
    if spec is None:
        return args
    out = dict(args)
    params = spec.params

    def stated(field: str, value: Any) -> None:
        if field in params and field not in out and value is not None:
            out[field] = int(value)

    m = _RE_KILL_SUFFIX.search(text or "")
    if m:
        stated("kill_count", m.group(1))
    m = _RE_PRICE_K.search(text or "")
    if m:
        stated("price", int(m.group(1)) * 1000)
    if all(f in params for f in ("x", "y")) and not any(f in out for f in ("x", "y")):
        pair = _RE_COORD_PAIR.search(text or "")
        if pair:
            out["x"], out["y"] = int(pair.group(1)), int(pair.group(2))
        else:
            for axis, digits in _RE_COORD_KV.findall(text or ""):
                stated(axis.lower(), digits)
    return out


def extract_value(text: str, values: List[Any]) -> Any:
    """Longest known value that appears in the goal as a whole-token run, else None.

    Arguments in this corpus are nearly always verbatim in the player's sentence ("vends 5 laine de
    bouftou"), so matching them back is both cheaper and more accurate than nearest-neighbour over a
    TF-IDF matrix -- which is also how this artifact grew to 115 MB of stored row matrices.
    """
    folded = " " + _fold(text) + " "
    best = None
    for value in values:
        if not isinstance(value, str):
            continue
        needle = " " + _fold(value) + " "
        if needle in folded and (best is None or len(value) > len(best)):
            best = value
    return best


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


class LexicalCommandPolicy:
    """predict(text) -> {"intent","slots","confidence"} -- the seam contract, and nothing more.

    `tool` and `args` are returned alongside so this module can be scored by `agent.tool_eval` without a
    second inference pass; `_valid_prediction` in commands.py ignores the extra keys.
    """

    def __init__(self, vectorizer: Any, tool_clf: Any, field_models: Dict[Tuple[str, str], Any],
                 field_values: Dict[Tuple[str, str], List[Any]], stats: Dict[str, Any]) -> None:
        self.vectorizer = vectorizer
        self.tool_clf = tool_clf
        self.field_models = field_models
        self.field_values = field_values
        self.stats = stats

    # ---- inference --------------------------------------------------------

    def predict(self, text: str, slots_from_rules: bool = True) -> Dict[str, Any]:
        """slots_from_rules: take the intent from the model and the slot *values* from the regexes.

        Measured, not assumed: on `val` the model's own argument heads score 16.7 call against the rules'
        29.3, while its intent choice scores 97.7 against their 42.9. Each side is better at a different
        half of the call, and the seam is where that gets reconciled.
        """
        x = self.vectorizer.transform([_norm(text)])
        tool = str(self.tool_clf.predict(x)[0])
        confidence = float(np.max(self.tool_clf.predict_proba(x)[0]))
        args: Dict[str, Any] = {}
        for (t, field), model in self.field_models.items():
            if t != tool:
                continue
            value = self._predict_field(tool, field, x, model, _norm(text))
            if value is not None:
                args[field] = value
        intent, extra = TOOL_INTENT.get(tool, (None, {}))
        if intent is None:
            return {"intent": "unknown", "slots": {}, "confidence": 0.0, "tool": tool, "args": args}
        merged = dict(extra)
        merged.update(args)                      # the model's own reading wins over the intent's default
        if slots_from_rules:
            from agent.commands import INTENT_SLOTS, rule_parse
            legal = INTENT_SLOTS.get(intent, set())
            for key, value in (rule_parse(text).get("slots") or {}).items():
                if key in legal and key not in merged:
                    merged[key] = value          # fill-only: never overwrite what the model proposed
        # The seam's own legality mask, applied HERE rather than left to _valid_prediction: an argument the
        # mapped intent cannot carry is not silently dropped at the last moment, it is never claimed. Scoring
        # around the mask would quote a call accuracy the live path cannot deliver.
        from agent.commands import INTENT_SLOTS
        legal = INTENT_SLOTS.get(intent, set())
        merged = {k: v for k, v in merged.items() if k == "action" or k in legal
                  or _ARG_SLOTS.get(k, k) in legal}
        # tool_eval scores argument KEYS, so everything leaves this method back in tool-arg spelling
        args_out = {}
        for k, v in merged.items():
            if k == "action":
                continue
            args_out[_SLOT_ARG.get(k, k)] = v
        args_out = recount(tool, args_out)
        from agent.schema import TOOLS_BY_NAME
        spec = TOOLS_BY_NAME.get(tool)
        if spec is not None:                       # the schema decides what a call may carry
            args_out = {k: v for k, v in args_out.items() if k in spec.params}
        # `slots` is what parse_command consumes and `args` is what tool_eval scores; if a repair lands in
        # only one of them, the seam and the measurement quietly disagree -- which is exactly how a 72.1%
        # harness number shipped as 35.4%.
        from agent.commands import INTENT_SLOTS
        allowed = INTENT_SLOTS.get(intent, set())
        for key, value in read_stated(tool, args_out, text).items():
            # a digit the sentence states enters through the same legality gate as every other field, and
            # only where nothing was already claimed -- so the two views below cannot disagree about it
            if key not in args_out and _SLOT_ARG.get(key, key) in allowed:
                args_out[key] = value
        rebuilt = {"action": merged["action"]} if "action" in merged else {}
        rebuilt.update({_SLOT_ARG.get(k, k): v for k, v in args_out.items()
                        if _SLOT_ARG.get(k, k) in allowed})
        return {"intent": intent, "slots": rebuilt,
                "confidence": confidence, "tool": tool, "args": args_out}

    def _predict_field(self, tool: str, field: str, x: Any, model: Any, text: str = "") -> Any:
        if model == "retrieve":
            return extract_value(text, self.field_values.get((tool, field)) or [])
        if model == "none":
            return None
        if model == "none":
            return None
        try:
            v = model.predict(x)[0]
        except Exception:                        # noqa: BLE001 - a bad field guess is dropped, not fatal
            return None
        # scikit-learn hands back numpy scalars; command_row() and the trajectory logs json.dumps() the
        # slots, and np.str_ is not serialisable. Coerce at the boundary, not in the caller.
        return v.item() if hasattr(v, "item") else (v if isinstance(v, (str, int, float, bool)) or v is None
                                                    else str(v))

    # ---- persistence ------------------------------------------------------

    def save(self, out: Path) -> int:
        import joblib
        out.mkdir(parents=True, exist_ok=True)
        joblib.dump({"vectorizer": self.vectorizer, "tool_clf": self.tool_clf,
                     "field_models": self.field_models, "field_values": self.field_values,
                     "stats": self.stats}, out / "policy.joblib")
        (out / "policy.json").write_text(json.dumps(self.stats, indent=2), encoding="utf-8")
        return (out / "policy.joblib").stat().st_size

    @classmethod
    def load(cls, path: Path) -> "LexicalCommandPolicy":
        import joblib
        d = joblib.load(Path(path) / "policy.joblib")
        return cls(d["vectorizer"], d["tool_clf"], d["field_models"], d["field_values"], d["stats"])


def train(rows: List[Dict[str, Any]], max_features: int = 50000) -> LexicalCommandPolicy:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression

    X = [_norm(r["goal"]) for r in rows]
    y = [str(r["tool"]) for r in rows]
    vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=2,
                                 max_features=max_features, sublinear_tf=True)
    A = vectorizer.fit_transform(X)
    tool_clf = LogisticRegression(max_iter=3000, C=4.0, class_weight="balanced").fit(A, y)

    # A field's FORM is decided by the schema, not by what the corpus happened to contain. This matters:
    # the first version classified every field, and a classifier over goal text has no way to read "300"
    # out of "sell 300 kamas of wood" -- it returns the most frequent quantity instead. That is an
    # invented argument, which is the one failure mode this repo forbids ("fill-only, never-add,
    # refuse-not-guess", docs/ARCH_REASSESSMENT.md item 2). So numeric fields are never proposed here;
    # the rules and ai/postprocess.py, which do parse numbers, fill them.
    fields_by_tool = {t.name: list(t.params.keys()) for t in TOOLS}
    param_spec = {t.name: dict(t.params) for t in TOOLS}
    by_tool: Dict[str, List[int]] = collections.defaultdict(list)
    for i, t in enumerate(y):
        by_tool[t].append(i)

    field_models: Dict[Tuple[str, str], Any] = {}
    field_values: Dict[Tuple[str, str], List[Any]] = {}
    n_cls = n_ret = n_none = 0
    for tool, idxs in by_tool.items():
        for field in fields_by_tool.get(tool, []):
            have = [i for i in idxs if field in (rows[i].get("args") or {})
                    and isinstance(rows[i]["args"][field], (str, int, float, bool))]
            if len(have) < MIN_ROWS:
                continue
            labels = [rows[i]["args"][field] for i in have]
            uniq = sorted(set(map(str, labels)))
            spec = param_spec.get(tool, {}).get(field) or {}
            ftype = str(spec.get("type") or "")
            key = (tool, field)
            if ftype in ("int", "float", "number") and not spec.get("enum"):
                field_models[key] = "none"           # a number must be read, not guessed
                field_values[key] = []
                n_none += 1
            elif len(uniq) <= MAX_DOMAIN and len(set(uniq)) >= 2:
                field_models[key] = LogisticRegression(max_iter=1500, C=2.0,
                                                       class_weight="balanced").fit(A[have],
                                                                                    [str(v) for v in labels])
                field_values[key] = list(uniq)
                n_cls += 1
            else:
                # free-text and id-like fields: recover the value from the sentence itself. Numbers never
                # appear as a string value, so they fall to "none" and the rule path keeps filling them.
                strs = [v for v in labels if isinstance(v, str)]
                if strs:
                    field_models[key] = "retrieve"
                    field_values[key] = sorted(set(strs), key=len, reverse=True)
                    n_ret += 1
                else:
                    field_models[key] = "none"
                    field_values[key] = []
                    n_none += 1
    stats = {"rows": len(rows), "tools": len(by_tool), "field_classifiers": n_cls,
             "field_retrieval": n_ret,
             "train_tool_self": float(np.mean([str(v) == t
                                               for v, t in zip(tool_clf.predict(A), y)]))}
    stats["field_none"] = n_none
    for m in list(field_models.values()) + [tool_clf]:
        if hasattr(m, "coef_"):
            m.coef_ = np.asarray(m.coef_, np.float32)   # 15 MB per head in float64; this is a farm box
    return LexicalCommandPolicy(vectorizer, tool_clf, field_models, field_values, stats)


_RZ: Optional[Dict[str, Any]] = None


def _resolvers() -> Dict[str, Any]:
    """The repo's own grounded resolvers, loaded the way encoder_serve loads them: absent is fine, and
    saying so is the point -- an argument number measured without them is not the deployable number."""
    global _RZ
    if _RZ is not None:
        return _RZ
    out = {"gazetteer": None, "kb": None, "why": "not requested"}
    try:
        from ai.gazetteer import DEFAULT_GAZETTEER, Gazetteer
        if DEFAULT_GAZETTEER.exists():
            out["gazetteer"] = Gazetteer()
            out["why"] = "gazetteer loaded"
        else:
            out["why"] = "no gazetteer artifact at %s" % DEFAULT_GAZETTEER
    except Exception as exc:                       # noqa: BLE001 - resolver absence is a measurement fact
        out["why"] = "gazetteer unusable: %s" % type(exc).__name__
    try:
        from planning.kb import DEFAULT_KB, DEFAULT_NAMES, KnowledgeBase
        if DEFAULT_KB.exists() and DEFAULT_NAMES.exists():
            out["kb"] = KnowledgeBase(DEFAULT_KB, DEFAULT_NAMES)
    except Exception:                              # noqa: BLE001 - same
        pass
    _RZ = out
    return out


def score_calls(policy: LexicalCommandPolicy, rows: List[Dict[str, Any]],
                slots_from_rules: bool = True, resolvers: Optional[Dict[str, Any]] = None,
                gold_tool: bool = False) -> Dict[str, Any]:
    """Score as tool calls with the repo's own scorer, so the table is comparable with release_gate."""
    from agent.tool_eval import evaluate
    from agent.commands import intent_tool
    preds = {}
    for r in rows:
        p = policy.predict(r["goal"], slots_from_rules=slots_from_rules)
        if p["intent"] == "unknown" and not gold_tool:
            preds[str(r["id"])] = None
            continue
        tool = r["tool"] if gold_tool else (intent_tool(p["intent"], (p.get("slots") or {}).get("action"))
                                            or p["tool"])
        args = dict(p["args"])
        if resolvers and (resolvers.get("gazetteer") or resolvers.get("kb")):
            from ai.postprocess import clean_call
            from ai.lang_detect import reply_language
            args = clean_call(tool, args, r["goal"], gazetteer=resolvers.get("gazetteer"),
                              kb=resolvers.get("kb"), lang=reply_language(r["goal"], default="fr"))
        preds[str(r["id"])] = {"tool": tool, "args": args}
    return evaluate(preds, rows)


def shadow(model_path: Path) -> Dict[str, Any]:
    """Rules vs the learned policy, on the goals the running system actually received.

    This is the closest thing available to the shadow session commands.py asks for before a rule is
    replaced: it does not score accuracy (there are no labels on live text), it reports *divergence* --
    how often the policy would have chosen a different intent than the engine did, on sentences that came
    from the trajectory logs and the command replay rather than from a generator.
    """
    import glob
    from agent.commands import parse_command, rule_parse

    policy = LexicalCommandPolicy.load(model_path)
    goals: List[str] = []
    for path in sorted(glob.glob(str(REPO_ROOT / "trajectories" / "*.jsonl"))):
        try:
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    if '"goal"' not in line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    g = row.get("goal")
                    if isinstance(g, str) and g.strip() and row.get("kind") in (None, "step", "wait"):
                        goals.append(g.strip())
        except OSError:
            continue
    replay = REPO_ROOT / "data" / "command_replay.jsonl"
    if replay.exists():
        for row in read_jsonl(replay):
            if isinstance(row.get("goal"), str):
                goals.append(row["goal"].strip())
    uniq = sorted(set(goals))
    rows, diverge = [], 0
    for g in uniq:
        r = rule_parse(g)
        m = policy.predict(g)
        same = r["intent"] == m["intent"]
        if not same:
            diverge += 1
        rows.append({"goal": g[:70], "rule_intent": r["intent"], "model_intent": m["intent"],
                     "confidence": round(m["confidence"], 3), "agree": same})
    # The number that matters is not "does it disagree" but "does the gate let the disagreement through":
    # parse_command only takes the model at or above `threshold`, so a low-confidence divergence is an
    # abstention, and abstention is the behaviour the fallback exists to provide.
    taken = [row for row in rows if not row["agree"] and row["confidence"] >= 0.55]
    out = {"live_goals_seen": len(uniq), "divergent": diverge, "overrides_taken_at_0.55": len(taken),
           "divergence_pct": round(100.0 * diverge / max(len(uniq), 1), 1), "rows": rows}
    print("%d distinct goals from live/replay logs; the policy disagrees on %d (%.1f%%), and the 0.55 gate "
          "would let %d of those through"
          % (len(uniq), diverge, out["divergence_pct"], len(taken)), flush=True)
    for row in rows:
        print("   %-70s rules=%-10s model=%-10s conf %.2f %s"
              % (row["goal"], row["rule_intent"], row["model_intent"], row["confidence"],
                 "" if row["agree"] else "  <-- DIVERGES"), flush=True)
    if len(uniq) < 20:
        print("CAVEAT: only %d distinct goals exist in the logs and they are machine-written schedule goals. "
              "This is not a shadow session over player language -- see ai/utterance_capture.py." % len(uniq),
              flush=True)
    return out


def ground_call(tool: str, slots: Dict[str, Any], goal: str) -> Dict[str, Any]:
    """Ground a command's slots with the repo's own resolvers, in slot spelling in and out.

    `clean_call` speaks tool-argument names; the command schema speaks slot names. Without this the seam
    ships 35.4 call while the harness prints 72.1, and that difference is the whole point: an argument
    measured on a path nobody runs is not a result.
    """
    rz = _resolvers()
    if not (rz.get("gazetteer") or rz.get("kb")):
        return slots
    from ai.lang_detect import reply_language
    from ai.postprocess import clean_call
    # `action` is the seam's own routing slot, not a tool argument: clean_call drops anything it does not
    # declare, and dropping action turns inv.sell into inv.list. Ground the arguments, carry the verb.
    args = {_SLOT_ARG.get(k, k): v for k, v in (slots or {}).items() if k != "action"}
    try:
        cleaned = clean_call(tool, args, goal, gazetteer=rz.get("gazetteer"), kb=rz.get("kb"),
                             lang=reply_language(goal, default="fr"))
    except Exception:                              # noqa: BLE001 - a resolver failure keeps the raw call
        return slots
    out = {_ARG_SLOTS.get(k, k): v for k, v in cleaned.items()}
    if "action" in (slots or {}):
        out["action"] = slots["action"]
    return out


def score_seam(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Score what `parse_command` actually returns, through the seam's own mapping.

    score_calls reaches into the policy; this does not. If the two disagree, the table in the docs is
    describing a code path nobody runs.
    """
    from agent.commands import intent_tool, parse_command
    from agent.tool_eval import evaluate
    preds = {}
    for r in rows:
        c = parse_command(r["goal"])
        tool = intent_tool(c.get("intent"), (c.get("slots") or {}).get("action"))
        preds[str(r["id"])] = None if tool is None else {
            "tool": tool, "args": {k: v for k, v in (c.get("slots") or {}).items() if k != "action"}}
    return evaluate(preds, rows)


def load_if_configured() -> Optional[LexicalCommandPolicy]:
    """The seam's supplier: a policy only exists for the live path if the environment names an artifact."""
    path = os.environ.get(ENV_MODEL)
    if not path:
        return None
    p = Path(path)
    if not (p / "policy.joblib").exists():
        print("[lexical_policy] %s=%s has no policy.joblib; staying on the rules" % (ENV_MODEL, p))
        return None
    return LexicalCommandPolicy.load(p)


def selfcheck(policy: LexicalCommandPolicy) -> int:
    """Prove the seam contract holds, and prove the check can fail.

    The non-vacuity half matters: a contract test that passes on garbage is how void columns happened
    earlier this session, so a deliberately malformed prediction must be rejected by commands.py.
    """
    from agent.commands import INTENT_SLOTS, INTENTS, parse_command, rule_parse

    bad = 0
    rows = read_jsonl(SFT_DIR / "gold.jsonl")
    for r in rows:
        p = policy.predict(r["goal"])
        if p["intent"] not in INTENTS:
            print("  FAIL intent %r not in INTENTS" % p["intent"])
            bad += 1
        illegal = set(p["slots"]) - INTENT_SLOTS.get(p["intent"], set())
        if illegal:
            print("  FAIL %r carries slots %s illegal for intent %r" % (r["goal"][:30], sorted(illegal),
                                                                        p["intent"]))
            bad += 1
        if not (0.0 <= p["confidence"] <= 1.0):
            print("  FAIL confidence %r out of range" % p["confidence"])
            bad += 1
    print("  %d predictions checked, %d contract violations" % (len(rows), bad))

    # the parse_command path must be identical to the rules when no model is configured
    os.environ.pop(ENV_MODEL, None)
    for probe in ("stop everything now", "vends 5 bouftou wool", "fais les quetes"):
        a, b = parse_command(probe), rule_parse(probe)
        if a["intent"] != b["intent"] or a["slots"] != b["slots"] or a.get("source") != b.get("source"):
            print("  FAIL with no model configured, parse_command diverged from the rules on %r: %s vs %s"
                  % (probe, a, b))
            bad += 1
    # and a malformed model must be refused, not trusted: this is the check that can fail
    class Broken:
        @staticmethod
        def predict(_text: str) -> Dict[str, Any]:
            return {"intent": "make_coffee", "slots": {"nonsense": 1}, "confidence": 0.99}

    if parse_command("stop everything now", model=Broken)["source"] == "model":
        print("  FAIL a fabricated intent was accepted by the seam")
        bad += 1
    print("selfcheck %s" % ("PASS" if bad == 0 else "FAILED (%d)" % bad))
    return 0 if bad == 0 else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="learned command policy on the parse_command seam")
    ap.add_argument("cmd", choices=["train", "eval", "selfcheck", "shadow"])
    ap.add_argument("--model", default=str(DEFAULT_OUT))
    ap.add_argument("--max-train", type=int, default=0)
    ap.add_argument("--resolvers", action="store_true",
                    help="also score with ai/postprocess.clean_call + the gazetteer/kb attached, and print "
                         "the gold-tool argument ceiling for this policy")
    args = ap.parse_args(argv)

    if args.cmd == "train":
        rows = read_jsonl(SFT_DIR / "train.jsonl")
        if args.max_train:
            rows = rows[:args.max_train]
        policy = train(rows)
        size = policy.save(Path(args.model))
        print("trained on %d rows | fields: %d classifiers, %d span-recover, %d left to the rules | "
              "artifact %.2f MB -> %s"
              % (policy.stats["rows"], policy.stats["field_classifiers"], policy.stats["field_retrieval"],
                 policy.stats.get("field_none", 0), size / 1e6, args.model), flush=True)
        print("train tool self-accuracy %.1f%% (a fit check, not a result)"
              % (100 * policy.stats["train_tool_self"]), flush=True)
        return 0

    if args.cmd == "shadow":
        shadow(Path(args.model))
        return 0
    policy = LexicalCommandPolicy.load(Path(args.model))
    if args.cmd == "selfcheck":
        return selfcheck(policy)
    import os
    os.environ[ENV_MODEL] = str(args.model)      # the seam loads the policy through this, exactly as a
    #                                       running brain would; without it `seam` measures the rules
    for name in ("val", "gold", "val_indist"):
        p = SFT_DIR / ("%s.jsonl" % name)
        if not p.exists():
            continue
        rows = read_jsonl(p)
        rz = _resolvers() if getattr(args, "resolvers", False) else None
        if rz and name == "val":
            print("resolvers: %s | kb %s" % (rz["why"], "loaded" if rz["kb"] else "absent"), flush=True)
            rep = score_calls(policy, rows, slots_from_rules=True, resolvers=rz)
            t = rep["totals"]
            print("resolv  %-11s tool %5.1f%%  call %5.1f%%  (n=%d)"
                  % (name, 100 * t["tool_ok"] / t["cases"], 100 * t["call_ok"] / t["cases"], t["cases"]),
                  flush=True)
            rep = score_calls(policy, rows, slots_from_rules=True, resolvers=rz, gold_tool=True)
            t = rep["totals"]
            print("ceil    %-11s tool %5.1f%%  call %5.1f%%  (n=%d)  [gold tool given: the argument ceiling "
                  "for this policy]" % (name, 100 * t["tool_ok"] / t["cases"],
                                        100 * t["call_ok"] / t["cases"], t["cases"]), flush=True)
        rep = score_seam(rows)
        t = rep["totals"]
        print("seam    %-11s tool %5.1f%%  call %5.1f%%  (n=%d)  [parse_command, as shipped]"
              % (name, 100 * t["tool_ok"] / t["cases"], 100 * t["call_ok"] / t["cases"], t["cases"]),
              flush=True)
        for label, sfr in (("hybrid ", True), ("model  ", False)):
            rep = score_calls(policy, rows, slots_from_rules=sfr)
            t = rep["totals"]
            print("%s %-11s tool %5.1f%%  call %5.1f%%  (n=%d)"
                  % (label, name, 100 * t["tool_ok"] / t["cases"], 100 * t["call_ok"] / t["cases"],
                     t["cases"]), flush=True)
            if name == "val" and sfr and label.startswith("hybrid"):
                print("   per-language tool: " + "  ".join(
                    "%s %.1f%%" % (l, 100 * s["tool_ok"] / s["cases"])
                    for l, s in sorted(rep["by_lang"].items())), flush=True)
    print("reference: connected rules val 42.9/29.3 gold 63.4/50.7 | 30M encoder val 94.1/69.7 | "
          "pooled Qwen Z val 98.5/9.1", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
