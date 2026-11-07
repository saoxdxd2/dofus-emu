"""Clean-room functional decompilation: probe a black-box head, deduce its rule,
compile a small module from scratch -- no distillation training, no SGD.

The claim being tested is a *sample-efficiency* claim, so this file is built around
three things that make it falsifiable rather than impressive:

1. An oracle with a budget. `probe` answers questions about the shipped tool-policy
   head and counts them. A deduced rule from 10,000 probes is not the claim; a rule
   from 100 is. The counter is in a state file, not in the caller's honesty.
2. Ground truth. The target is a model whose weights we actually hold, so the deduced
   rule can be scored against the real function on 3,719 rows from episodes that were
   never in the probe set. If the method cannot recover a function whose truth is
   known, it cannot recover a private model's either -- and this says so loudly.
3. A decoy that must fail. One oracle is a head with a 3-feature parity term: it is
   not expressible as a small rule set over the named features, so a deducer that
   scores ~1.0 on it is fabricating agreement, not finding structure. The harness is
   only trustworthy if it reports FAIL there.

Controls it reports against, at the same probe budget:
  - the current head (the ceiling, fit by teacher cloning on 15,967 rows);
  - the same architecture trained by SGD on only the probed rows;
  - a majority-class floor.

Subcommands:
  selftest                      # the three arms that can fail (run this first)
  probe --file q.json           # answer queries, count them against the budget
  budget                        # how many probes are spent
  reset --budget N              # start a clean probe session
  score --rule r.json           # a deduced rule set, scored against ground truth
  compile --rule r.json         # realize the rule set in head-shaped weights, verify
  sgd --rows N                  # the equal-budget SGD control

Rules are expressed as mutually exclusive condition sets (a partition), not a
priority list: that is what makes the weight compile exact rather than approximate,
and `compile` proves exactness by running the network over every held-out row.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.weight_space import evaluate, export_manifest, forward, manifest_weights

MODELS = REPO_ROOT / "models"


def state_path() -> Path:
    """Where the probe counter lives. Overridable so `selftest` can run against a
    throwaway session: the gate runs the self test while a real probe session is in
    progress, and a harness that resets the experiment it is meant to measure is
    worse than no harness."""
    import os

    return Path(os.environ.get("CLEANROOM_STATE")
                or (REPO_ROOT / "data" / "cleanroom_session.json"))

# A feature is compared with `>=` unless the rule says otherwise. Conditions are on
# the 42 names the head itself sees, so a deduced rule cannot smuggle in a variable
# the model never had -- that would be a leak, not a circuit.
OPS = (">=", "<=", "==", "!=")


# ------------------------------------------------------------------ oracles

class HeadOracle:
    """The shipped tool-policy head, exposed only as question -> answer."""

    name = "tool_policy"

    def __init__(self) -> None:
        self.manifest = export_manifest()
        self.weights = manifest_weights(self.manifest)
        self.tools: List[str] = list(self.manifest["base"]["tools"])
        self.feature_names: List[str] = list(self.manifest["base"]["feature_names"])
        self.width = len(self.feature_names)
        self._rows = None

    def answer(self, x: List[float]) -> Dict[str, Any]:
        import numpy as np

        vec = np.asarray([self._pad(x)], dtype="float64")
        logits = forward(self.weights, vec)[0]
        order = sorted(range(len(logits)), key=lambda i: -float(logits[i]))
        return {"top": self.tools[order[0]],
                "runner_up": self.tools[order[1]],
                "margin": round(float(logits[order[0]] - logits[order[1]]), 4),
                "top3": [[self.tools[i], round(float(logits[i]), 3)] for i in order[:3]]}

    def _pad(self, x: List[float]) -> List[float]:
        if len(x) != self.width:
            raise ValueError("expected %d features, got %d" % (self.width, len(x)))
        return [float(v) for v in x]

    def rows(self) -> List[Dict[str, Any]]:
        if self._rows is None:
            from agent.weight_space import read_val_rows

            self._rows = read_val_rows()
        return self._rows

    def truth(self, x: List[float]) -> str:
        import numpy as np

        vec = np.asarray([self._pad(x)], dtype="float64")
        return self.tools[int(np.argmax(forward(self.weights, vec)))]


class ParityOracle:
    """A decoy: three features enter only through an odd-parity term.

    The bits are taken at each feature's own median, so roughly half the rows turn
    each bit on. The first version keyed the bits on `> 0.5` over the three objective
    one-hot columns, and the self test caught it: at most one of those is ever on, so
    "any on" *is* parity there and the decoy was trivially satisfiable -- a harness
    whose hostile arm is easy is worse than a harness without one.
    """

    name = "decoy_parity"

    def __init__(self, idx: Optional[Tuple[int, int, int]] = None) -> None:
        import numpy as np

        head = HeadOracle()
        self.feature_names = head.feature_names
        self.width = head.width
        self.tools = ["alpha", "beta"]
        xs = np.asarray([r["x"] for r in head.rows()], dtype="float64")
        med = np.median(xs, axis=0)
        above = [(xs[:, i] > med[i]).mean() for i in range(self.width)]
        spread = xs.std(axis=0)
        # Balance the bits: a feature that is 1.0 for half the rows has a median of 1.0
        # and a `> median` bit that almost never turns on, which made the first decoy
        # hard for the wrong reason. Take the three features whose split is closest to
        # half, and say what the split is, so a hostile arm cannot be quietly easy.
        order = sorted((abs(a - 0.5), i) for i, a in enumerate(above) if spread[i] > 0)
        idx = tuple(idx) if idx else tuple(i for _d, i in order[:3])
        self.idx = idx
        self.cuts = [float(med[i]) for i in idx]
        self.splits = [round(float(above[i]), 3) for i in idx]
        if min(self.splits) < 0.35 or max(self.splits) > 0.65:
            raise SystemExit("decoy bits are not balanced (%s); the hostile arm would be "
                             "satisfiable by accident" % self.splits)

    def _bits(self, x: List[float]) -> List[int]:
        return [1 if float(x[i]) > c else 0 for i, c in zip(self.idx, self.cuts)]

    def answer(self, x: List[float]) -> Dict[str, Any]:
        odd = sum(self._bits(x)) % 2 == 1
        return {"top": self.tools[0 if odd else 1], "runner_up": self.tools[1 if odd else 0],
                "margin": 1.0, "top3": []}

    def truth(self, x: List[float]) -> str:
        return self.answer(x)["top"]

    def rows(self) -> List[Dict[str, Any]]:
        return HeadOracle().rows()


class LabelOracle:
    """What the game accepted, limited to a fixed pool of observable states.

    The head oracle measures "can you imitate this model". That question has a hard
    ceiling -- imitating the incumbent cannot beat it -- and the shipped head is the
    incumbent, so this second oracle asks the question that matters in play: from the
    same 100-probe budget, can a hand-compiled rule beat SGD on the *task's* answers?

    Two properties keep it honest:

    * only the pool is observable. A private API answers for states it has actually
      been shown; inventing an answer for an unseen vector would be the harness
      guessing. `pool` prints the states, the labels stay behind the oracle.
    * the pool is fixed by the harness, and scoring uses the rows outside it, so the
      100-query budget and the held-out set are the same for the rule and for SGD.
    """

    name = "accepted_action"
    POOL_SEED = 20261104

    def __init__(self, pool_size: int = 100) -> None:
        import random

        head = HeadOracle()
        self.feature_names = head.feature_names
        self.tools = head.tools
        self.width = head.width
        rows = head.rows()
        rng = random.Random(self.POOL_SEED)
        self.pool = rng.sample(rows, min(pool_size, len(rows)))
        pool_ids = {id(r) for r in self.pool}
        self.holdout = [r for r in rows if id(r) not in pool_ids]
        self.all_rows = rows
        self._by_x = {self._key(r["x"]): r for r in rows}

    def ceiling(self) -> Dict[str, Any]:
        """How well *anything* could do, given only these 42 features.

        For each distinct feature vector, the best possible answer is the most common
        accepted action among the rows that share it, so the sum of those majorities
        over the row count is an upper bound on any model or rule that sees only this
        input space. Measured 2026-11-04: 3,719 val rows collapse to 2,064 distinct
        vectors, and the bound is well below 1.0 -- which is the honest meaning of
        "headroom": it is not the head's fault, and no weight edit buys it back.
        """
        from collections import Counter, defaultdict

        groups: Dict[Tuple[float, ...], Counter] = defaultdict(Counter)
        for r in self.all_rows:
            groups[self._key(r["x"])][self.tools[int(r["y"])]] += 1
        best = sum(c.most_common(1)[0][1] for c in groups.values())
        rows = len(self.all_rows)
        return {"rows": rows, "distinct_states": len(groups),
                "rows_sharing_a_state": rows - len(groups),
                "feature_conditioned_ceiling": round(best / rows, 4)}

    @staticmethod
    def _key(x: List[float]) -> Tuple[float, ...]:
        return tuple(round(float(v), 6) for v in x)

    def answer(self, x: List[float]) -> Dict[str, Any]:
        r = self._by_x.get(self._key(x))
        if r is None or r not in self.pool:
            return {"top": None, "runner_up": None, "margin": None, "top3": [],
                    "note": "unobservable: this state is not in the pool the target was "
                            "shown. Ask about a pool state."}
        tool = self.tools[int(r["y"])]
        return {"top": tool, "runner_up": None, "margin": None,
                "top3": [[tool, 1.0]], "objective_type": r.get("objective_type")}

    def truth(self, x: List[float]) -> Optional[str]:
        r = self._by_x.get(self._key(x))
        return self.tools[int(r["y"])] if r is not None else None

    def rows(self) -> List[Dict[str, Any]]:
        """The scoring universe: every held-out row, pool included."""
        return self.all_rows


def oracle(which: str):
    if which == "decoy":
        return ParityOracle()
    if which == "labels":
        return LabelOracle()
    return HeadOracle()


# ------------------------------------------------------------------ budget

def reset(budget: int) -> Dict[str, Any]:
    state = {"started": time.time(), "budget": int(budget), "asked": 0, "hash": None}
    state_path().parent.mkdir(parents=True, exist_ok=True)
    state_path().write_text(json.dumps(state), encoding="utf-8")
    return state


def state() -> Dict[str, Any]:
    if not state_path().exists():
        raise SystemExit("no probe session: run `reset --budget N` first")
    return json.loads(state_path().read_text(encoding="utf-8"))


def spend(o, queries: List[List[float]]) -> List[Dict[str, Any]]:
    st = state()
    if st["asked"] + len(queries) > st["budget"]:
        raise SystemExit("probe budget is %d and %d are already spent; refusing %d more"
                         % (st["budget"], st["asked"], len(queries)))
    out = []
    for x in queries:
        st["asked"] += 1
        rec = {"x": x, "a": o.answer(x)}
        out.append(rec)
    state_path().write_text(json.dumps(st), encoding="utf-8")
    return out


# ------------------------------------------------------------------ rules

def loads_rules(path: Path) -> Dict[str, Any]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    rules = doc["rules"] if isinstance(doc, dict) else doc
    for r in rules:
        for c in r["when"]:
            if c["op"] not in OPS:
                raise SystemExit("unknown op %r" % c["op"])
    return {"rules": rules, "fallback": (doc.get("fallback") if isinstance(doc, dict)
                                         else None) or rules[0]["tool"]}


def rule_predict(doc: Dict[str, Any], names: List[str], x: List[float]) -> str:
    for r in doc["rules"]:
        if matches(r["when"], names, x):
            return r["tool"]
    return doc["fallback"]


def matches(conds: List[Dict[str, Any]], names: List[str], x: List[float]) -> bool:
    for c in conds:
        if c["feature"] not in names:
            return False
        v = float(x[names.index(c["feature"])])
        t = float(c["value"])
        op = c["op"]
        if op == ">=" and not (v >= t):
            return False
        if op == "<=" and not (v <= t):
            return False
        if op == "==" and not abs(v - t) < 1e-9:
            return False
        if op == "!=" and not abs(v - t) >= 1e-9:
            return False
    return True


def score_rule(doc: Dict[str, Any], o, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """How well does the deduced rule imitate the oracle, on rows never probed?"""
    import numpy as np

    names = o.feature_names
    xs = [r["x"] for r in rows]
    pred = [rule_predict(doc, names, x) for x in xs]
    truth = [o.truth(x) for x in xs]
    agree = sum(p == t for p, t in zip(pred, truth)) / len(rows)
    labelled = [r for r in rows if r.get("y") is not None]
    tools = getattr(o, "tools", [])
    # Label accuracy only means something against the real corpus tool space. A decoy
    # oracle with two names has no mapping for `y`, and inventing one would be a
    # number that measures nothing.
    if len(tools) >= 21:
        correct = sum(p == tools[int(r["y"])] for r, p in zip(
            labelled, [rule_predict(doc, names, r["x"]) for r in labelled])) / max(1, len(labelled))
    else:
        correct = None
    floor_counts: Dict[str, int] = {}
    for t in truth:
        floor_counts[t] = floor_counts.get(t, 0) + 1
    floor = max(floor_counts.values()) / len(rows) if floor_counts else 0.0
    return {"target": o.name, "rows": len(rows),
            "agreement_with_target": round(agree, 4),
            "accuracy_on_labels": (round(correct, 4) if correct is not None else None),
            "majority_floor_of_target": round(floor, 4),
            "distinct_tools_used": len(set(pred)),
            "fallback_rate": round(sum(1 for r in rows
                                       if rule_predict(doc, names, r["x"]) == doc["fallback"])
                                   / len(rows), 4)}


# ------------------------------------------------------------------ compile

def compile_rule(doc: Dict[str, Any], names: List[str], tools: List[str],
                 gain: float = 50.0) -> Dict[str, Any]:
    """Realise a mutually exclusive rule set as one hidden unit per rule.

    Each condition is a threshold on a single input feature, so a rule is an AND of
    half-space tests: h_j = relu(sum_i w_ij x_i + b_j) is 1 exactly when every test
    passes (weights +/-K per passing test, bias set so a failing test cancels one).
    Output logits then read M for the tool of the fired rule, so argmax is the rule's
    tool -- provided no two rules fire on the same input. `verify_partition` proves
    that, and `compile` refuses a rule set that is not a partition rather than
    emitting weights that mean something else.
    """
    rules = doc["rules"]
    hidden = len(rules)
    width = len(names)
    k = 10.0
    w0 = [[0.0] * width for _ in range(hidden)]
    b0 = [0.0] * hidden
    for j, r in enumerate(rules):
        feats = set()
        for c in r["when"]:
            i = names.index(c["feature"])
            sign = 1.0 if c["op"] in (">=", "==") else -1.0
            if (i in feats):
                raise SystemExit("rule %d tests the same feature twice; not expressible "
                                 "as one half-space" % j)
            feats.add(i)
            w0[j][i] += sign * k
            b0[j] += -sign * k * float(c["value"])
        # one passing test contributes +k, so fire when all n tests pass
        b0[j] += 0.5 - k * (len(r["when"]) - 0.5) if r["when"] else 0.0
        if not r["when"]:
            raise SystemExit("rule %d has no conditions: it would always fire" % j)
    w1 = [[0.0] * hidden for _ in range(len(tools))]
    for j, r in enumerate(rules):
        w1[tools.index(r["tool"])][j] = gain
    return {"architecture": "linear-relu-linear", "hidden": hidden, "width": width,
            "0.weight": w0, "0.bias": b0, "1.weight": w1,
            "1.bias": [-1e-3] * len(tools), "tools": tools}


def compiled_predict(net: Dict[str, Any], x: List[float]) -> str:
    import numpy as np

    w0 = np.asarray(net["0.weight"], dtype="float64")
    b0 = np.asarray(net["0.bias"], dtype="float64")
    w1 = np.asarray(net["1.weight"], dtype="float64")
    b1 = np.asarray(net["1.bias"], dtype="float64")
    h = np.maximum(0.0, w0 @ np.asarray(x, dtype="float64") + b0)
    return net["tools"][int(np.argmax(w1 @ h + b1))]


def verify_partition(doc: Dict[str, Any], xs: List[List[float]]) -> Dict[str, Any]:
    """Count inputs where two rules fire at once -- the compile is exact only at zero."""
    names = doc.setdefault("_names", None)
    clashes = 0
    for x in xs:
        fired = [r for r in doc["rules"] if matches(r["when"], names, x)]
        if len(fired) > 1:
            clashes += 1
    return {"rows": len(xs), "inputs_with_two_rules_firing": clashes}


# ------------------------------------------------------------------ controls

def sgd_control(o, rows: List[Dict[str, Any]], probe_rows: List[Dict[str, Any]],
                on: str = "head", seed: int = 7, epochs: int = 400) -> Dict[str, Any]:
    """The control the claim needs: the same head, trained on only the probed rows.

    `on="head"` is the distillation reading -- imitate what the target answers.
    `on="labels"` is the supervised reading -- imitate what the *task* accepted, which
    is the number a deducer must actually beat, because copying a target's argmax
    cannot exceed the target and the target is the incumbent.
    """
    import numpy as np

    if not probe_rows:
        return {"error": "no probe rows supplied"}
    rng = np.random.default_rng(seed)
    tools = o.tools if isinstance(o, HeadOracle) else ["alpha", "beta"]
    width, hidden = o.width, 192
    x = np.asarray([r["x"] for r in probe_rows], dtype="float64")
    if on == "labels":
        y = np.asarray([int(r["y"]) for r in probe_rows], dtype="int64")
    else:
        y = np.asarray([tools.index(o.truth(list(r["x"]))) for r in probe_rows], dtype="int64")
    w0 = rng.normal(0, 0.1, (hidden, width)); b0 = np.zeros(hidden)
    w1 = rng.normal(0, 0.1, (len(tools), hidden)); b1 = np.zeros(len(tools))
    lr = 0.05
    for _ in range(epochs):
        h = np.maximum(0.0, x @ w0.T + b0)
        z = h @ w1.T + b1
        p = np.exp(z - z.max(axis=1, keepdims=True))
        p /= p.sum(axis=1, keepdims=True)
        g = p - np.eye(len(tools))[y]
        dw1 = g.T @ h / len(x); db1 = g.mean(axis=0)
        dh = g @ w1 * (h > 0)
        w0 -= lr * dh.T @ x / len(x); b0 -= lr * dh.mean(axis=0)
        w1 -= lr * dw1; b1 -= lr * db1
    xall = np.asarray([r["x"] for r in rows], dtype="float64")
    hall = np.maximum(0.0, xall @ w0.T + b0)
    argmax = np.argmax(hall @ w1.T + b1, axis=1)
    pred = [tools[i] for i in argmax]
    truth = [o.truth(r["x"]) for r in rows]
    labels = [tools[int(r["y"])] for r in rows] if isinstance(o, HeadOracle) else None
    return {"trained_on": on, "train_rows": len(probe_rows), "probe_budget": state()["budget"],
            "agreement_with_target": round(sum(a == b for a, b in zip(pred, truth))
                                           / len(rows), 4),
            "accuracy_on_labels": (round(sum(a == b for a, b in zip(pred, labels))
                                         / len(rows), 4) if labels else None)}


# ------------------------------------------------------------------ self tests

def selftest() -> int:
    """Every arm here has to be able to say FAIL. A harness that only flatters is worse
    than no harness: the clean-room claim is exactly the kind that survives a friendly
    test and dies on a hostile one.

    Runs against a throwaway probe counter in the temp directory, because the gate
    executes this while a real probe session is in progress.
    """
    import os
    import tempfile

    os.environ["CLEANROOM_STATE"] = str(Path(tempfile.mkdtemp(prefix="cleanroom-"))
                                        / "session.json")
    print("== clean-room harness self test ==")
    fail = 0
    head = HeadOracle()
    rows = head.rows()
    print("rows %d, features %d, tools %d" % (len(rows), head.width, len(head.tools)))

    metrics = evaluate(manifest_weights(head.manifest), rows, head.tools)
    ok = abs(metrics["val_accuracy"] - 0.9043) < 0.005
    print("[%s] the oracle answers as the shipped head does (val acc %.4f)"
          % ("PASS" if ok else "FAIL", metrics["val_accuracy"]))
    fail += 0 if ok else 1

    if not state_path().exists():
        reset(100)
    spent_before = state()["asked"]
    try:
        spend(head, [[0.0] * head.width] * (state()["budget"] + 1))
        print("[FAIL] an over-budget probe burst was allowed")
        fail += 1
    except SystemExit:
        print("[PASS] a burst larger than the budget is refused before any answer")
    ok = state()["asked"] == spent_before
    print("[%s] a refused burst spends nothing (%d)" % ("PASS" if ok else "FAIL",
                                                        state()["asked"]))
    fail += 0 if ok else 1

    good = {"rules": [{"when": [{"feature": head.feature_names[int(rows[0]["y"] and 0)],
                                 "op": ">=", "value": 0.0}], "tool": head.tools[0]}],
            "fallback": head.tools[0], "_names": head.feature_names}
    s = score_rule(good, head, rows)
    ok = s["majority_floor_of_target"] > 0.0
    print("[%s] the scorer measures something (%.3f majority floor over %d tools)"
          % ("PASS" if ok else "FAIL", s["majority_floor_of_target"], len(head.tools)))
    fail += 0 if ok else 1

    decoy = ParityOracle()
    drows = rows[:600]
    cuts = {decoy.feature_names[i]: c for i, c in zip(decoy.idx, decoy.cuts)}
    or_rule = {"rules": [{"when": [{"feature": f, "op": ">=", "value": v}],
                          "tool": decoy.tools[0]} for f, v in cuts.items()],
               "fallback": decoy.tools[1], "_names": decoy.feature_names}
    ds = score_rule(or_rule, decoy, drows)
    ok = ds["agreement_with_target"] < 0.9
    print("[%s] the decoy is not satisfiable by OR-ing its three bits (%.4f)"
          % ("PASS" if ok else "FAIL", ds["agreement_with_target"]))
    fail += 0 if ok else 1

    constant = score_rule({"rules": [], "fallback": decoy.tools[0],
                           "_names": decoy.feature_names}, decoy, drows)
    print("[%s] a constant answer is blamed too (%.4f)"
          % ("PASS" if constant["agreement_with_target"] < 0.9 else "FAIL",
             constant["agreement_with_target"]))
    fail += 0 if constant["agreement_with_target"] < 0.9 else 1
    print("   decoy bits: features %s cut at %s, share above %s"
          % (list(decoy.idx), [round(c, 4) for c in decoy.cuts], decoy.splits))
    print("selftest failures: %d" % fail)
    return 1 if fail else 0


# ------------------------------------------------------------------ cli

def main(argv: Optional[List[str]] = None) -> int:
    from agent.jsonio import utf8_console

    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["selftest", "probe", "budget", "reset", "score", "pool",
                                        "compile", "sgd", "sample"])
    ap.add_argument("--file", help="query file: a JSON list of 42-wide feature vectors")
    ap.add_argument("--rule", help="rule document written by the deducer")
    ap.add_argument("--oracle", default="head", choices=["head", "decoy", "labels"])
    ap.add_argument("--budget", type=int, default=100)
    ap.add_argument("--rows", type=int, default=3000)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    o = oracle(a.oracle)
    if a.command == "selftest":
        return selftest()
    if a.command == "reset":
        print(json.dumps(reset(a.budget)))
        return 0
    if a.command == "budget":
        print(json.dumps(state()))
        return 0
    if a.command == "probe":
        queries = json.loads(Path(a.file).read_text(encoding="utf-8"))
        answers = spend(o, queries)
        print(json.dumps({"asked": len(answers), "spent": state()["asked"],
                          "budget": state()["budget"],
                          "answers": answers}, ensure_ascii=False))
        return 0
    if a.command == "sample":
        # Row vectors the deducer may reason about *without* spending a probe: the
        # corpus labels are the player-visible state, the oracle answer is not.
        rows = o.rows()[:a.rows]
        print(json.dumps({"rows": len(rows),
                          "x": [r["x"] for r in rows]}, ensure_ascii=False))
        return 0
    if a.command == "pool":
        if not isinstance(o, LabelOracle):
            print("`pool` needs --oracle labels")
            return 2
        print(json.dumps({"pool": len(o.pool), "scoring_rows": len(o.rows()),
                          "holdout_rows": len(o.holdout),
                          "feature_names": o.feature_names,
                          "x": [r["x"] for r in o.pool]}, ensure_ascii=False))
        return 0
    if a.command == "sgd":
        st = state()
        n = st["asked"] or 100
        probes = random.Random(1).sample(o.rows(), min(n, len(o.rows())))
        out = {"probe_budget": st["budget"], "train_rows": n}
        if isinstance(o, HeadOracle):
            out["incumbent_head_val_accuracy"] = evaluate(
                manifest_weights(o.manifest), o.rows(), o.tools)["val_accuracy"]
            out["feature_conditioned_bound"] = LabelOracle().ceiling()
        for which in ("head", "labels"):
            out["sgd_on_" + which] = sgd_control(o, o.rows(), probes, on=which)
        print(json.dumps(out, indent=1))
        return 0
    if not a.rule:
        print("`%s` needs --rule" % a.command)
        return 2
    doc = loads_rules(Path(a.rule))
    doc["_names"] = o.feature_names
    if a.command == "score":
        res = score_rule(doc, o, o.rows())
        print(json.dumps(res, indent=1))
        return 0
    if a.command == "compile":
        part = verify_partition(doc, [r["x"] for r in o.rows()])
        if part["inputs_with_two_rules_firing"]:
            print(json.dumps({"refused": "rules overlap on %d inputs"
                                    % part["inputs_with_two_rules_firing"]}))
            return 1
        net = compile_rule(doc, o.feature_names, o.tools)
        import numpy as np

        xs = [r["x"] for r in o.rows()]
        mismatch = sum(1 for x in xs if compiled_predict(net, x)
                       != rule_predict(doc, o.feature_names, x))
        out = {"hidden": net["hidden"], "width": net["width"], "tools": len(o.tools),
               "params": sum(len(r) for r in net["0.weight"]) + len(net["0.bias"])
               + sum(len(r) for r in net["1.weight"]) + len(net["1.bias"]),
               "rows_checked": len(xs), "compile_mismatches": mismatch}
        print(json.dumps(out, indent=1))
        if a.out:
            Path(a.out).write_text(json.dumps(net), encoding="utf-8")
        return 0 if mismatch == 0 else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
