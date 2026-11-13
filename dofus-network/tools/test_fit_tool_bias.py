"""Checks for the fitted tool bias: its units, its sidecar block, its application.

    python tools/test_fit_tool_bias.py

The bug this pins down was silent: the bias was fitted on the exported graph's
logits (already multiplied by 1/T) and then never applied, because nothing passed
`tool_penalty`. Applying it *scaled* would have been worse than not applying it --
at T = 0.25 that is a 4x overshoot.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
for candidate in (str(REPO_ROOT), str(HERE)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from agent.planner_nn import NNPlanner, apply_tool_bias  # noqa: E402
from fit_tool_bias import bias_block  # noqa: E402

FAILED = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global FAILED
    if not ok:
        FAILED += 1
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, ("  " + detail) if detail else ""))


TOOLS = ["quest.list", "nav.goto", "fight.join"]
BIAS = {"quest.list": 1.3, "nav.goto": -0.8}


def test_application_matches_the_reference() -> None:
    rng = np.random.default_rng(0)
    logits = rng.normal(0.0, 2.0, size=(256, len(TOOLS)))
    vector = np.array([BIAS.get(tool, 0.0) for tool in TOOLS])
    adjusted = np.array([apply_tool_bias(row.tolist(), TOOLS, BIAS) for row in logits])
    # `tool_penalty` is a cost: the planner subtracts it from the logits.
    check("the adjusted scores are logits - the bias",
          np.allclose(adjusted, logits - vector[None, :]))
    check("the choice matches argmax(logits - bias)",
          np.array_equal(adjusted.argmax(1), (logits - vector[None, :]).argmax(1)))
    check("an empty bias is the identity",
          np.allclose(np.array(apply_tool_bias(logits[0].tolist(), TOOLS, {})), logits[0]))
    check("a tool outside the head is ignored",
          np.allclose(apply_tool_bias(logits[0].tolist(), TOOLS, {"chat.say": 9.0}), logits[0]))


def test_scaling_by_the_temperature_overshoots() -> None:
    """The 4x mistake, concretely: a row the fit leaves alone, 1/T flips."""
    temperature = 0.25
    bias = {"nav.goto": -0.8}          # a cost, so nav.goto gains 0.8 when subtracted
    logits = [2.0, 0.5, 0.0]           # quest.list leads by 1.5
    plain = apply_tool_bias(logits, TOOLS, bias)
    big = apply_tool_bias(logits, TOOLS, {k: v / temperature for k, v in bias.items()})
    check("the fitted bias leaves a 1.5 gap alone",
          int(np.argmax(plain)) == 0, str(plain))
    check("bias/T flips it, which the fit never chose",
          int(np.argmax(big)) == 1, str(big))
    check("the overshoot is exactly the graph scale",
          abs(big[1] - (logits[1] + 4.0 * 0.8)) < 1e-12, "%.3f" % big[1])


def test_block_names_its_units() -> None:
    delta = np.array([0.0, 0.0, 1.0, -1.0, 0.0])
    block = bias_block(TOOLS, [1.30004, -0.0, 0.5], 0.25, 10198.67, delta, "models/x.onnx")
    check("units are post_scale", block["units"] == "post_scale", str(block["units"]))
    check("the temperature travels with the numbers", block["temperature"] == 0.25)
    check("the graph scale is 1/T", abs(float(block["graph_scale"]) - 4.0) < 1e-9,
          str(block["graph_scale"]))
    check("zero bias entries are dropped", set(block["bias"]) == {"quest.list", "fight.join"},
          str(block["bias"]))
    check("the val verdict is recorded", block["improved"] == 1 and block["worsened"] == 1
          and abs(float(block["val_gain_delta"])) < 1e-12)


def _planner(tools=TOOLS) -> NNPlanner:
    planner = NNPlanner(level_hint=60)
    planner.tools = list(tools)
    return planner


def test_planner_loads_the_block() -> None:
    planner = _planner()
    planner._load_sidecar_bias({"tools": TOOLS,
                                "tool_bias": bias_block(TOOLS, [1.3, -0.8, 0.0],
                                                        0.25, 1.0, [0.0], "m")})
    check("the planner carries the fitted bias",
          planner.tool_penalty == {"quest.list": 1.3, "nav.goto": -0.8},
          str(planner.tool_penalty))
    check("the note names the units", "post-scale" in planner.tool_bias_note,
          planner.tool_bias_note)
    # quest.list carries a 1.3 cost and a 0.2 lead over fight.join, which has
    # none: 6.0 - 1.3 = 4.7 still wins at 4.7 against 4.4. A four-times cost
    # (5.2) would have lost it, so this row separates the two applications.
    scores = apply_tool_bias([6.0, 0.0, 4.4], TOOLS, planner.tool_penalty)
    check("a loaded bias changes the decision",
          int(np.argmax(scores)) == 0, str(scores))
    check("and it is applied once, not four times",
          abs(scores[0] - 4.7) < 1e-9 and abs(scores[2] - 4.4) < 1e-9, str(scores))


def test_the_loader_refuses_a_wrong_unit() -> None:
    planner = _planner()
    planner._load_sidecar_bias({"tool_bias": {"units": "pre_scale",
                                              "bias": {"quest.list": 1.3}}})
    check("a pre_scale block is not applied", planner.tool_penalty == {},
          planner.tool_bias_note)
    check("and it says why", "pre_scale" in planner.tool_bias_note,
          planner.tool_bias_note)

    planner = _planner()
    planner._load_sidecar_bias({"tool_bias": {"units": "post_scale",
                                              "bias": {"fight.join": 0.4, "chat.say": 1.0}}})
    check("unknown tools are dropped", planner.tool_penalty == {"fight.join": 0.4},
          str(planner.tool_penalty))


def test_explicit_argument_wins() -> None:
    planner = NNPlanner(level_hint=60, tool_penalty={"nav.goto": 0.2})
    planner.tools = list(TOOLS)
    planner._load_sidecar_bias({"tool_bias": {"units": "post_scale",
                                              "bias": {"quest.list": 1.3}}})
    check("a caller's dict is not overwritten", planner.tool_penalty == {"nav.goto": 0.2},
          str(planner.tool_penalty))
    check("and the note says so", "caller" in planner.tool_bias_note,
          planner.tool_bias_note)


def test_sign_test_separates_signal_from_noise() -> None:
    import fit_tool_bias

    check("a coin flip is not evidence", fit_tool_bias.sign_test_p(53, 50) > 0.4,
          "%.3f" % fit_tool_bias.sign_test_p(53, 50))
    check("a one-sided landslide is", fit_tool_bias.sign_test_p(90, 10) < 0.001,
          "%.4f" % fit_tool_bias.sign_test_p(90, 10))
    check("an untouched split proves nothing", fit_tool_bias.sign_test_p(0, 0) == 1.0)


def test_unstable_entries_are_dropped() -> None:
    import fit_tool_bias

    original = fit_tool_bias.fit
    try:
        # The two halves disagree on tools 0 and 1 and agree on tool 2.
        fit_tool_bias.fit = lambda *_a, **_k: (np.array([1.0, 1.0, 0.5]), 0.0)
        out, dropped = fit_tool_bias.stable_rewards(
            np.zeros((4, 3)), np.zeros((4, 3)), np.array([1.0, -1.0, 0.5]))
    finally:
        fit_tool_bias.fit = original
    check("the entry the halves disagree about is dropped", dropped == [1], str(dropped))
    check("the agreed entries survive", np.allclose(out, [1.0, 0.0, 0.5]), str(out))
    check("sub-threshold entries are dropped too",
          fit_tool_bias.stable_rewards(np.zeros((2, 1)), np.zeros((2, 1)),
                                       np.array([0.05]))[0][0] == 0.0)


def test_the_decision_path_uses_the_bias() -> None:
    """`_choose_tool` must actually apply it -- the original bug was a path nobody ran."""
    import agent.features as features

    corpus = REPO_ROOT / "data" / "tool_sft_v3" / "train.jsonl"
    if not corpus.is_file():
        check("decision path (corpus absent)", False, str(corpus))
        return
    row = json.loads(corpus.read_text(encoding="utf-8").splitlines()[0])
    sidecar = json.loads((REPO_ROOT / "models" / "tool_policy.json").read_text(encoding="utf-8"))
    from agent.weight_space import _onnx_logits

    logits = np.asarray(_onnx_logits(REPO_ROOT / "models" / "tool_policy.onnx",
                                     [row], sidecar))[0]

    class StubSession:
        def run(self, _names, _feeds):
            return [logits[None, :].astype("float32")]

    planner = NNPlanner(level_hint=60)
    planner.tools = list(sidecar["tools"])
    planner.expected_features = len(row["x"])
    planner._model_loaded = True
    planner.session = StubSession()
    planner.head = "mlp"
    planner.history_steps = 0
    planner.stack_mode = "off"

    original_features = features.objective_features
    features.objective_features = lambda *_a, **_k: list(row["x"])
    try:
        planner.tool_penalty = {}
        without = planner._choose_tool({}, 0, {}, {})
        order = np.argsort(logits)[::-1]
        top, rival = int(order[0]), int(order[1])
        # A bonus big enough to take the lead, expressed as a cost on the winner.
        gap = float(logits[top] - logits[rival])
        planner.tool_penalty = {planner.tools[top]: gap + 0.5}
        with_bias = planner._choose_tool({}, 0, {}, {})
    finally:
        features.objective_features = original_features
    check("without a bias the head's argmax wins",
          without == planner.tools[top], "%s vs %s" % (without, planner.tools[top]))
    check("with a bias the decision moves", with_bias != without,
          "%s -> %s" % (without, with_bias))
    check("and it moves to the runner-up", with_bias == planner.tools[rival],
          "%s vs %s" % (with_bias, planner.tools[rival]))


def test_the_shipped_sidecar_is_consistent() -> None:
    path = REPO_ROOT / "models" / "tool_policy.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    block = payload.get("tool_bias")
    check("the shipped sidecar is still readable", payload["temperature"] == 0.25,
          str(payload.get("temperature")))
    if block:
        check("a shipped bias names post_scale units", block.get("units") == "post_scale",
              str(block.get("units")))
        check("every shipped bias tool is a head output",
              set(block.get("bias") or {}) <= set(payload["tools"]))
        planner = _planner(payload["tools"])
        planner._load_sidecar_bias(payload)
        check("the planner accepts the shipped block", bool(planner.tool_penalty),
              planner.tool_bias_note)
    else:
        check("no bias shipped (the fit did not generalise to val)", True, "absent")


if __name__ == "__main__":
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            function()
    print("%d failed" % FAILED)
    raise SystemExit(1 if FAILED else 0)
