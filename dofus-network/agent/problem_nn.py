"""ProblemNet: which failure is this, and what fixes it.

The supervisor repairs by habit. `Component.repair` is one callable per component, so
whatever goes wrong with farming gets the same answer: `farm.start`, `farm.start`,
`farm.start`. The logs measure what that costs -- the repair was re-issued for a hundred
seconds with `ok=True` and nothing fought, because an ack is not an effect and the
supervisor had no way to tell one failure from another.

This module is the missing judgement, in two heads over one shared trunk:

* **remedy** -- softmax over the repairs the agent actually has. Trained on the attempts
  that *worked*, so it answers "in a situation that looks like this, what has fixed it
  before". Nothing here is a rule about game mechanics; it is a table of experience.
* **escalate** -- P(this attempt fails). Trained on every attempt, success or failure,
  so it is the half that recognises a situation it has not solved before. A high value
  is the signal to try the next remedy instead of repeating the last one -- which is the
  exact behaviour the user asked to stop.

Why a network and not a dictionary of `detail -> remedy`: the detail strings are the
supervisor's own prose ("forced_join: nothing within budget and nowhere to go"), and the
same underlying problem arrives in a dozen wordings as the code grows. The features are
the *shape* of the failure -- which component, how many attempts, how long since
progress, which markers appear in the detail, what was tried last and whether it worked
-- and that shape is what generalises.

Torch-free on purpose, like `agent/foresight.py`: the weights are a JSON file of small
arrays and scoring one situation is a 30 -> 24 -> (7, 1) matmul in numpy, so the
installed client needs no training stack and no service. Training is here too, in numpy,
because a few hundred rows do not need a GPU -- and the rows only exist after the
supervisor has run, which is not a thing a Kaggle notebook can wait for.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import logging
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = Path(
    os.environ.get("DOFUS_PROBLEM_MODEL") or (REPO_ROOT / "models" / "problem_net.json"))
DEFAULT_ROWS = Path(
    os.environ.get("DOFUS_PROBLEM_ROWS") or (REPO_ROOT / "data" / "problem_rows.jsonl"))

# The components the supervisor watches. Order is the feature layout and must not
# change without bumping FEATURE_VERSION -- the width is baked into the weights.
COMPONENTS = ("tunnel", "client", "brain", "farming")

# The repairs the agent actually has. Each one is something a component can be asked
# to do, not a wish: `restart` restarts a process, `engage` joins a fight, `travel`
# asks the client to change map. A remedy that no component offers can never be
# chosen, which is what keeps the net's output actionable.
REMEDIES = ("restart", "rearm", "engage", "travel", "walk", "wait", "replan")

# What each remedy says about the problem when it is the one that works. Reporting
# only: the net predicts remedies, and this is the sentence attached to one.
REMEDY_PROBLEM = {
    "restart": "component_down",
    "rearm": "not_engaged",
    "engage": "not_engaged",
    "travel": "no_route",
    "walk": "no_route",
    "wait": "transient",
    "replan": "model_mismatch",
}

# Markers looked for in the supervisor's own detail line. Lower-cased substrings, in a
# fixed order, because the feature vector is positional.
DETAIL_MARKERS = (
    "ok=true",          # an ack that may not have been an effect
    "ok=false",
    "refused",
    "not found",
    "stayed on",        # travel acked, map did not change
    "no monster",
    "nowhere",          # nowhere left to walk
    "forced",           # the last rung of the engagement ladder
    "raised",
    "timeout",
    "401",
    "empty",
    "not ready",
)

OUTCOMES = ("none", "worked", "failed")

FEATURE_VERSION = 1
FEATURE_WIDTH = (len(COMPONENTS) + 1 + 1 + 1 + len(DETAIL_MARKERS)
                 + len(REMEDIES) + len(OUTCOMES))
HIDDEN = 24
# Below this many labelled attempts the net is guessing, and a guess that reorders the
# remedies is worse than the fixed repair the component already declares.
MIN_ROWS = 40


def _one_hot(index: int, width: int) -> List[float]:
    row = [0.0] * width
    if 0 <= index < width:
        row[index] = 1.0
    return row


def features_of(situation: Dict[str, Any]) -> List[float]:
    """The situation as a fixed-width vector. Pure, total, and cheap.

    Every field has a defined meaning when it is missing, because the caller is a
    supervisor in the middle of a failure: a feature builder that can raise is a
    feature builder that turns "farming is stuck" into "the supervisor is stuck".
    """
    name = str(situation.get("component") or "").strip().lower()
    component = COMPONENTS.index(name) if name in COMPONENTS else -1
    features: List[float] = list(_one_hot(component, len(COMPONENTS)))
    try:
        failures = float(situation.get("failures") or 0.0)
    except (TypeError, ValueError):
        failures = 0.0
    features.append(min(1.0, math.log1p(max(0.0, failures)) / 3.0))
    try:
        idle = float(situation.get("seconds_since_progress") or 0.0)
    except (TypeError, ValueError):
        idle = 0.0
    features.append(min(1.0, max(0.0, idle) / 120.0))
    features.append(1.0 if situation.get("dry_run") else 0.0)
    detail = str(situation.get("detail") or "").lower()
    features.extend(1.0 if marker in detail else 0.0 for marker in DETAIL_MARKERS)
    last = str(situation.get("last_remedy") or "").strip().lower()
    features.extend(_one_hot(REMEDIES.index(last) if last in REMEDIES else -1,
                             len(REMEDIES)))
    outcome = str(situation.get("last_outcome") or "none").strip().lower()
    features.extend(_one_hot(OUTCOMES.index(outcome) if outcome in OUTCOMES else 0,
                             len(OUTCOMES)))
    if len(features) != FEATURE_WIDTH:  # pragma: no cover - a layout bug, not a case
        raise AssertionError("feature width %d != %d" % (len(features), FEATURE_WIDTH))
    return features


# ---------------------------------------------------------------------------
# the network
# ---------------------------------------------------------------------------


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - float(np.max(logits))
    weights = np.exp(shifted)
    total = float(np.sum(weights))
    return weights / total if total > 0 else np.full_like(weights, 1.0 / len(weights))


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp = math.exp(value)
    return exp / (1.0 + exp)


@dataclass
class ProblemNet:
    """Two heads over one trunk. Weights are plain arrays, so a JSON file is enough."""

    params: Dict[str, np.ndarray] = field(default_factory=dict)
    rows: int = 0
    trained_at: Optional[float] = None

    @classmethod
    def fresh(cls, seed: int = 0) -> "ProblemNet":
        rng = np.random.default_rng(seed)
        scale = 1.0 / math.sqrt(FEATURE_WIDTH)
        return cls(params={
            "w1": rng.normal(0.0, scale, (FEATURE_WIDTH, HIDDEN)),
            "b1": np.zeros(HIDDEN),
            "wr": rng.normal(0.0, scale, (HIDDEN, len(REMEDIES))),
            "br": np.zeros(len(REMEDIES)),
            "we": rng.normal(0.0, scale, (HIDDEN, 1)),
            "be": np.zeros(1),
        })

    # -- inference --------------------------------------------------------
    def _forward(self, x: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        hidden = np.tanh(x @ self.params["w1"] + self.params["b1"])
        return hidden, hidden @ self.params["wr"] + self.params["br"], (
            hidden @ self.params["we"] + self.params["be"])

    def predict(self, situation: Dict[str, Any]) -> Dict[str, Any]:
        """Remedy probabilities and the chance this attempt fails."""
        x = np.asarray(features_of(situation), dtype=np.float64)
        _hidden, remedy_logits, escalate = self._forward(x)
        probabilities = _softmax(np.asarray(remedy_logits, dtype=np.float64))
        ranked = sorted(zip(REMEDIES, probabilities.tolist()),
                        key=lambda item: (-item[1], REMEDIES.index(item[0])))
        failure = _sigmoid(float(np.asarray(escalate).reshape(-1)[0]))
        return {
            "remedies": [{"remedy": name, "p": round(p, 4)} for name, p in ranked],
            "best": ranked[0][0],
            "p_fail": round(failure, 4),
            "problem": REMEDY_PROBLEM.get(ranked[0][0], "unknown"),
            "trained_rows": self.rows,
        }

    def ready(self) -> bool:
        return self.rows >= MIN_ROWS and bool(self.params)

    def suggest(self, situation: Dict[str, Any],
                offered: Sequence[str]) -> Optional[str]:
        """The best remedy this component can actually perform, or None.

        None means "no opinion", and the caller keeps its own repair. Returning a
        remedy the component cannot perform would turn a learned hint into a
        guaranteed no-op, which is the failure this whole file is about.
        """
        if not self.ready() or not offered:
            return None
        allowed = [name for name in offered if name in REMEDIES]
        if not allowed:
            return None
        ranked = self.predict(situation)["remedies"]
        for row in ranked:
            if row["remedy"] in allowed:
                return str(row["remedy"])
        return None

    # -- persistence ------------------------------------------------------
    def as_dict(self) -> Dict[str, Any]:
        return {
            "version": FEATURE_VERSION,
            "feature_width": FEATURE_WIDTH,
            "hidden": HIDDEN,
            "remedies": list(REMEDIES),
            "components": list(COMPONENTS),
            "markers": list(DETAIL_MARKERS),
            "rows": int(self.rows),
            "trained_at": self.trained_at,
            "params": {key: np.asarray(value).tolist()
                       for key, value in self.params.items()},
        }

    @classmethod
    def from_dict(cls, data: Any) -> Optional["ProblemNet"]:
        """The net in a file, or None when the file does not match this build.

        A mismatch is not an error to raise on: the supervisor has to keep running
        with a stale model on disk, and the honest degradation is the fixed repair.
        """
        if not isinstance(data, dict):
            return None
        if data.get("version") != FEATURE_VERSION:
            return None
        if data.get("feature_width") != FEATURE_WIDTH or data.get("hidden") != HIDDEN:
            return None
        if list(data.get("remedies") or ()) != list(REMEDIES):
            return None
        raw = data.get("params")
        if not isinstance(raw, dict):
            return None
        try:
            params = {key: np.asarray(raw[key], dtype=np.float64) for key in
                      ("w1", "b1", "wr", "br", "we", "be")}
        except (KeyError, TypeError, ValueError):
            return None
        if params["w1"].shape != (FEATURE_WIDTH, HIDDEN):
            return None
        return cls(params=params, rows=int(data.get("rows") or 0),
                   trained_at=data.get("trained_at"))

    @classmethod
    def load(cls, path: Optional[Path] = None) -> Optional["ProblemNet"]:
        try:
            return cls.from_dict(json.loads(Path(path or DEFAULT_MODEL)
                                            .read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None

    def save(self, path: Optional[Path] = None) -> None:
        target = Path(path or DEFAULT_MODEL)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.as_dict(), indent=1), encoding="utf-8")


# ---------------------------------------------------------------------------
# training, in numpy
# ---------------------------------------------------------------------------


def train(rows: Sequence[Dict[str, Any]], epochs: int = 400, lr: float = 0.02,
          seed: int = 0, net: Optional[ProblemNet] = None) -> ProblemNet:
    """Fit both heads on the supervisor's own attempts.

    Two losses over one trunk:

    * remedy: cross-entropy against the remedy that *worked*, on successful rows
      only. A failed attempt is not evidence that the remedy was wrong -- it is
      evidence that it did not work *there*, and treating it as a wrong label is
      how a learner talks itself out of the only repair it has.
    * escalate: binary cross-entropy on every row, so the trunk also sees the
      situations nothing has fixed yet. Without this the trunk would only ever
      describe the easy half.

    Full batch, Adam. A few hundred rows and 1,144 parameters do not need more,
    and a deterministic fit is a fit that can be re-run from the same file.
    """
    usable = [row for row in rows if isinstance(row, dict) and row.get("remedy")]
    net = net or ProblemNet.fresh(seed)
    if not usable:
        return net
    x = np.asarray([features_of(row) for row in usable], dtype=np.float64)
    escalate = np.asarray([0.0 if str(row.get("outcome")) == "worked" else 1.0
                           for row in usable], dtype=np.float64)
    worked = [row for row in usable if str(row.get("outcome")) == "worked"]
    if worked:
        remedy_index = np.asarray([REMEDIES.index(str(row["remedy"]))
                                   for row in worked], dtype=np.int64)
        remedy_rows = np.asarray([features_of(row) for row in worked],
                                 dtype=np.float64)
    else:
        remedy_index = np.zeros(0, dtype=np.int64)
        remedy_rows = np.zeros((0, FEATURE_WIDTH), dtype=np.float64)

    params = {key: np.array(value, dtype=np.float64, copy=True)
              for key, value in net.params.items()}
    moment = {key: np.zeros_like(value) for key, value in params.items()}
    velocity = {key: np.zeros_like(value) for key, value in params.items()}
    beta1, beta2, epsilon = 0.9, 0.999, 1e-8
    for step in range(1, max(1, int(epochs)) + 1):
        hidden, remedy_logits, escalate_logit = _forward(params, x)
        grads = {key: np.zeros_like(value) for key, value in params.items()}
        # escalate head: sigmoid + BCE, on every row
        probability = 1.0 / (1.0 + np.exp(-escalate_logit.reshape(-1)))
        delta_e = ((probability - escalate) / len(x)).reshape(-1, 1)
        grads["we"] = hidden.T @ delta_e
        grads["be"] = np.asarray([float(np.sum(delta_e))])
        # The two heads see different batches, so the trunk is back-propagated
        # once per head and the gradients are summed. Accumulating the deltas
        # into one array would only work if both heads had every row, and the
        # remedy head deliberately does not.
        delta_pre = (delta_e @ params["we"].T) * (1.0 - hidden * hidden)
        grads["w1"] = x.T @ delta_pre
        grads["b1"] = np.sum(delta_pre, axis=0)
        # remedy head: softmax + cross-entropy on the rows that worked
        if len(remedy_rows):
            hidden_r, logits_r, _ = _forward(params, remedy_rows)
            probabilities = np.exp(logits_r - logits_r.max(axis=1, keepdims=True))
            probabilities /= probabilities.sum(axis=1, keepdims=True)
            probabilities[np.arange(len(remedy_index)), remedy_index] -= 1.0
            delta_r = probabilities / len(remedy_index)
            grads["wr"] = hidden_r.T @ delta_r
            grads["br"] = np.sum(delta_r, axis=0)
            delta_pre_r = (delta_r @ params["wr"].T) * (1.0 - hidden_r * hidden_r)
            grads["w1"] += remedy_rows.T @ delta_pre_r
            grads["b1"] += np.sum(delta_pre_r, axis=0)
        for key in params:
            moment[key] = beta1 * moment[key] + (1 - beta1) * grads[key]
            velocity[key] = beta2 * velocity[key] + (1 - beta2) * grads[key] ** 2
            m_hat = moment[key] / (1 - beta1 ** step)
            v_hat = velocity[key] / (1 - beta2 ** step)
            params[key] -= lr * m_hat / (np.sqrt(v_hat) + epsilon)
    return ProblemNet(params=params, rows=len(usable), trained_at=time.time())


def _forward(params: Dict[str, np.ndarray], x: np.ndarray
             ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    hidden = np.tanh(x @ params["w1"] + params["b1"])
    return hidden, hidden @ params["wr"] + params["br"], hidden @ params["we"] + params["be"]


# ---------------------------------------------------------------------------
# the row log
# ---------------------------------------------------------------------------


@dataclass
class ProblemMemory:
    """Attempts, appended as they happen and labelled when the outcome is known.

    A decision and its verdict are two moments, so the situation is held in memory
    until the next check says whether the component recovered, and only then is one
    complete row appended. The file is therefore append-only *and* always carries
    the features its label belongs to -- writing a pending row first and a bare
    label later would produce rows that cannot be featurised.
    """

    path: Path = DEFAULT_ROWS
    written: int = 0
    _pending: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def record(self, situation: Dict[str, Any], remedy: str) -> None:
        """Remember what was tried. Appended by `label` once the outcome is known."""
        name = str(situation.get("component") or "")
        self._pending[name] = {
            "at": round(time.time(), 3),
            "component": name,
            "detail": str(situation.get("detail") or "")[:400],
            "failures": int(situation.get("failures") or 0),
            "seconds_since_progress": round(
                float(situation.get("seconds_since_progress") or 0.0), 1),
            "last_remedy": situation.get("last_remedy"),
            "last_outcome": situation.get("last_outcome"),
            "remedy": remedy,
        }

    def label(self, component: str, outcome: str) -> Optional[Dict[str, Any]]:
        """Append the verdict for the pending attempt, if there is one."""
        row = self._pending.pop(str(component), None)
        if row is None or outcome not in ("worked", "failed"):
            return None
        row["outcome"] = outcome
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            self.written += 1
        except OSError:
            return None
        return row

    def rows(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict) and row.get("outcome") in ("worked",
                                                                        "failed"):
                        out.append(row)
        except OSError:
            pass
        return out

    def fit(self, model_path: Optional[Path] = None,
            epochs: int = 400) -> Optional[ProblemNet]:
        rows = self.rows()
        if len(rows) < MIN_ROWS:
            return None
        net = train(rows, epochs=epochs)
        net.save(model_path or DEFAULT_MODEL)
        return net


# ---------------------------------------------------------------------------
# the advisor the supervisor talks to
# ---------------------------------------------------------------------------


class ProblemAdvisor:
    """Loads the net once and answers "what should I try" for a component.

    Deliberately total: every method returns a usable answer when the model is
    missing, stale or raises. The supervisor runs on a machine that may never have
    trained a model, and "no opinion" has to be a normal answer rather than an
    exception in the middle of a repair.
    """

    def __init__(self, model_path: Optional[Path] = None,
                 rows_path: Optional[Path] = None) -> None:
        self.model_path = Path(model_path or DEFAULT_MODEL)
        self.memory = ProblemMemory(Path(rows_path or DEFAULT_ROWS))
        self.net = ProblemNet.load(self.model_path)
        self.asked = 0
        self.advised = 0

    def choose(self, situation: Dict[str, Any],
               offered: Sequence[str]) -> Optional[str]:
        self.asked += 1
        try:
            pick = self.net.suggest(situation, offered) if self.net else None
        except Exception:  # noqa: BLE001 - a hint must never stop a repair
            return None
        if pick is not None:
            self.advised += 1
        return pick

    def learn(self, situation: Dict[str, Any], remedy: str) -> None:
        try:
            self.memory.record(situation, remedy)
        except Exception:  # noqa: BLE001 - learning must never stop a repair
            pass

    def label(self, component: str, outcome: str) -> Optional[Dict[str, Any]]:
        try:
            return self.memory.label(component, outcome)
        except Exception:  # noqa: BLE001
            return None

    def report(self) -> Dict[str, Any]:
        return {
            "ready": bool(self.net and self.net.ready()),
            "rows": int(self.net.rows) if self.net else 0,
            "asked": self.asked,
            "advised": self.advised,
            "model": str(self.model_path),
        }


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Train ProblemNet from the supervisor's log")
    parser.add_argument("--rows", default=str(DEFAULT_ROWS))
    parser.add_argument("--model", default=str(DEFAULT_MODEL))
    parser.add_argument("--epochs", type=int, default=400)
    parser.add_argument("--report", action="store_true", help="print what is learned")
    args = parser.parse_args(argv)

    memory = ProblemMemory(Path(args.rows))
    rows = memory.rows()
    logger.debug("rows: %d (need %d to train)" % (len(rows), MIN_ROWS))
    if args.report:
        counts: Dict[str, int] = {}
        for row in rows:
            key = "%s/%s" % (row.get("remedy"), row.get("outcome"))
            counts[key] = counts.get(key, 0) + 1
        for key in sorted(counts):
            logger.debug("  %-20s %d" % (key, counts[key]))
        return 0
    if len(rows) < MIN_ROWS:
        return 1
    net = train(rows, epochs=args.epochs)
    net.save(Path(args.model))
    logger.debug("trained on %d rows -> %s" % (net.rows, args.model))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
