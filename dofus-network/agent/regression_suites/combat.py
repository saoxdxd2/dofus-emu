"""Combat-related regression tests, moved from regression.py.

These tests exercise the combat solver, grid model, duel simulation,
damage calibration, and related combat subsystems.
"""

from __future__ import annotations

import importlib.util
import json as _json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent.regression_suites.common import REPO_ROOT, OPTIONAL_IMPORTS, check, section


def test_side_swap() -> None:
    import unittest
    from combat import test_side_swap as module

    section("Side-swapped cross-play (seat bias vs decision skill)")
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("side-swap tests execute and pass",
          result.testsRun >= 6 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_move_gate() -> None:
    import unittest
    from ai import test_move_gate as module

    section("Live move head (promotion gate on the temporal test fold)")
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("move-head gate tests execute and pass",
          result.testsRun >= 5 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_damage_calibration_join() -> None:
    section("Damage calibration (the solver's estimate vs the server's own loss)")
    tool = REPO_ROOT.parent / "tools" / "damage_calibration.py"
    check("the calibration tool exists", tool.is_file(), str(tool))
    check("the live fight-outcome capture tool exists",
          (REPO_ROOT.parent / "tools" / "capture_fight_outcome.py").is_file(),
          "tools/capture_fight_outcome.py")
    if not tool.is_file():
        return
    spec = importlib.util.spec_from_file_location("damage_calibration", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    decisions_path = Path(tempfile.mkdtemp(prefix="damage-dec-")) / "decisions.jsonl"
    decisions_path.write_text("\n".join(_json.dumps(row) for row in [
        {"fight_id": 124, "my_id": 4434090, "decision": {
            "slots": [{"spell_id": 10, "damage": 20.0, "empty": False},
                      {"spell_id": 0, "damage": 12.0, "empty": False}],
            "targets": [{"fighter_id": 4434090}, {"fighter_id": -1}]}},
        {"fight_id": 124, "my_id": 4434090, "decision": {
            "slots": [{"spell_id": 10, "damage": 22.0, "empty": False}],
            "targets": [{"fighter_id": 4434090}, {"fighter_id": -1}]}},
    ]), encoding="utf-8")
    outcomes = [{"kind": "fight_outcome", "fight_id": 124, "won": True, "end": {
        "fight_id": 124, "won": True, "battle_log": [
            {"type": "GameActionFightSpellCastMessage", "at_action": 300,
             "source": 4434090, "spell": 99, "target": 4434090},
            {"type": "GameActionFightSpellCastMessage", "at_action": 300,
             "source": 4434090, "spell": 10, "target": -1},
            {"type": "GameActionFightLifePointsLostMessage", "at_action": 96,
             "source": 4434090, "target": -1, "loss": 18},
            {"type": "GameActionFightLifePointsLostMessage", "at_action": 96,
             "source": 4434090, "target": -1, "loss": 8},
            {"type": "GameActionFightSpellCastMessage", "at_action": 300,
             "source": 4434090, "spell": 0, "target": -1},
            {"type": "GameActionFightLifePointsLostMessage", "at_action": 97,
             "source": -22358, "target": -1, "loss": 11},
            {"type": "GameActionFightSpellCastMessage", "at_action": 300,
             "source": 4434090, "spell": 10, "target": -1},
            {"type": "GameActionFightSpellCastMessage", "at_action": 10,
             "source": -1, "spell": 97, "target": 4434090},
            {"type": "GameActionFightLifePointsLostMessage", "at_action": 10,
             "source": -1, "target": 4434090, "loss": 5},
        ]}}]
    result = module.report(module.load_decisions(decisions_path), outcomes)
    summary = result["summary"]
    check("casts are paired with effects that carry different action ids",
          summary["matched_casts"] == 2, summary)
    check("an unpaired cast is counted, not dropped",
          summary["unmatched_casts"] == 1, summary)
    check("a self-cast is not counted as a damage test",
          summary["non_damage_casts"] == 1, summary)
    check("both effects of one cast add to its actual loss",
          any(pair["spell"] == 10 and pair["actual"] == 26.0 for pair in result["pairs"]),
          result["pairs"])
    check("a summon's hit counts as this side's damage",
          any(pair["spell"] == 0 and pair["actual"] == 11.0 for pair in result["pairs"]),
          result["pairs"])
    check("the enemy's cast and the damage on me are excluded",
          all(pair["target"] == -1 for pair in result["pairs"])
          and "97" not in summary["per_spell"], result["pairs"])
    check("a spell's prediction is the median of its decisions",
          result["pairs"][0]["predicted"] == 21.0, result["pairs"][:1])
    check("fewer than five pairs is reported as not enough, not as accuracy",
          summary["status"] == "not_enough_casts", summary)
    check("the per-spell error is the pair's own absolute error",
          summary["per_spell"]["10"]["mae"] == 5.0
          and summary["per_spell"]["0"]["mae"] == 1.0, summary["per_spell"])

    if Path(module.DEFAULT_DECISIONS).is_file():
        live = module.report(module.load_decisions(module.DEFAULT_DECISIONS), [])
        check("with no recorded battle log the live report says so honestly",
              live["summary"]["status"] == "not_enough_casts", live["summary"])


def test_damage_calibration_live_evidence() -> None:
    """The joiner must keep working on real captured rows, not only synthetic ones.

    Fixtures: fights 133 and 138 (2026-10-28), copied verbatim from
    `data/live_evidence/fight_outcomes.jsonl` and the decision log. Their shapes
    are the traps the synthetic test cannot invent: one cast action id (300) for
    every spell, effects numbered 96/97, a summon source (-22358), a self-buff, and
    one fight that ran past the old ring cap. The numbers below are what the live
    run reported; a change here means the joiner stopped reading live rows.
    """
    section("Damage calibration on captured live fights (fixtures)")
    tool = REPO_ROOT.parent / "tools" / "damage_calibration.py"
    decisions_path = REPO_ROOT / "data" / "live_evidence" / "decisions_fixture.jsonl"
    outcomes_path = REPO_ROOT / "data" / "live_evidence" / "fight_outcomes_fixture.jsonl"
    check("the live calibration fixtures exist",
          decisions_path.is_file() and outcomes_path.is_file(),
          "%s / %s" % (decisions_path, outcomes_path))
    if not (tool.is_file() and decisions_path.is_file() and outcomes_path.is_file()):
        return
    spec = importlib.util.spec_from_file_location("damage_calibration_live", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    outcomes = list(module._iter_rows(outcomes_path))
    result = module.report(module.load_decisions(decisions_path), outcomes)
    summary = result["summary"]
    check("both captured fights are read", summary["fights"] == 2, summary)
    check("every damage cast in the fixtures pairs",
          summary["matched_casts"] == 9 and summary["unmatched_casts"] == 0,
          summary)
    check("the per-spell counts match the live run",
          {k: v["casts"] for k, v in summary["per_spell"].items()}
          == {"7041": 2, "7045": 6, "7051": 1}, summary["per_spell"])
    check("the live error and bias are unchanged",
          abs(summary["overall_mae"] - 9.44) < 0.01
          and abs(summary["overall_bias"] + 7.48) < 0.01, summary)
    check("self-targeted casts are counted as non-damage, not as misses",
          summary["non_damage_casts"] == 4, summary)
    check("the join keeps the live cast order and amounts",
          sorted(pair["actual"] for pair in result["pairs"] if pair["spell"] == 7045)
          == [15.0, 18.0, 21.0, 32.0, 32.0, 40.0], result["pairs"])


def test_combat_suites() -> None:
    section("Combat suites (delegated, so '892 checks' includes them)")
    for module in ("combat.test_battle_solver", "combat.test_grid_model",
                   "combat.test_grid_dataset",
                   "combat.test_grid_train",
                   "combat.test_live_policy", "combat.test_normal_build_duel",
                   "combat.test_live_data", "combat.test_side_swap",
                   "combat.test_live_learn",
                   "combat.test_online_learn", "combat.test_env_math",
                   "combat.test_threat",
                   "agent.test_hunting",
                   "agent.test_problem_nn",
                   "combat.test_map_field",
                   "combat.test_duel_value",
                   "combat.test_monster_mech",
                   "combat.test_outcome_stats",
                   "agent.test_bridge",
                   "planning.test_thinking_cache",
                   "planning.test_live_tap",
                   "agent.test_calibration",
                   "agent.test_inert_attribution",
                   "ai.test_glicko2"):
        proc = subprocess.run([sys.executable, "-m", module], cwd=str(REPO_ROOT),
                              capture_output=True, text=True, timeout=300)
        text = (proc.stderr or "") + (proc.stdout or "")
        absent = next((m for m in OPTIONAL_IMPORTS
                       if "No module named '%s'" % m in text), None) \
            if proc.returncode != 0 else None
        if absent:
            print("   SKIP: %s needs %r, which this interpreter does not ships"
                  % (module, absent))
            check("%s is skipped for a missing optional wheel (%s)" % (module, absent),
                  True, absent)
            continue
        ran = re.search(r"^Ran (\d+) tests?", text, re.MULTILINE)
        executed = int(ran.group(1)) if ran else 0
        check("%s passes" % module, proc.returncode == 0 and executed > 0,
              text.strip()[-300:] if executed else
              "no 'Ran N tests' line: the module collected nothing\n" + text.strip()[-300:])


__all__ = [
    "test_side_swap",
    "test_move_gate",
    "test_damage_calibration_join",
    "test_damage_calibration_live_evidence",
    "test_combat_suites",
]
