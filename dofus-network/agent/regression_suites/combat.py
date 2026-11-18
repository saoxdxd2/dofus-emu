"""Extracted regression suite: combat"""
from __future__ import annotations
import importlib.util
import json as _json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from agent.regression_suites.common import REPO_ROOT, OPTIONAL_IMPORTS, check, section, logger

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


def test_kill_pricing() -> None:
    """The learned max_hp hop: payload -> table -> solver kill bonus.

    The payload omits max_hp on most enemy rows, so a standing kill is priced at
    0.35 and loses to a bigger hit. Three links have to hold for the fix to be
    real: the observer stores what the page publishes, the board teaches the
    table, and the solver reads it back when pricing a kill.
    """
    import unittest
    from combat import test_battle_solver as solver_module
    from combat import test_live_learn as learn_module

    section("Kill pricing (a learned max_hp restores a standing kill)")
    suite = unittest.TestSuite([
        unittest.defaultTestLoader.loadTestsFromName(
            "KillPricingTests", solver_module),
        unittest.defaultTestLoader.loadTestsFromName(
            "ThreatObserverMultiSlotTests."
            "test_a_published_max_hp_is_kept_without_a_battle_log",
            learn_module),
    ])
    result = unittest.TextTestRunner(verbosity=0).run(suite)
    check("the kill pricing tests execute and pass",
          result.testsRun >= 4 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_damage_calibration_join() -> None:
    section("Damage calibration (the solver's estimate vs the server's own loss)")
    import importlib.util
    import json as _json
    import tempfile

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
            # Live semantics (fight 133, 2026-10-28): the cast's action id is 300
            # for every spell, and the damage arrives as an effect with its own id.
            {"type": "GameActionFightSpellCastMessage", "at_action": 300,
             "source": 4434090, "spell": 99, "target": 4434090},
            {"type": "GameActionFightSpellCastMessage", "at_action": 300,
             "source": 4434090, "spell": 10, "target": -1},
            {"type": "GameActionFightLifePointsLostMessage", "at_action": 96,
             "source": 4434090, "target": -1, "loss": 18},
            # A second effect of the same cast (multi-hit) adds to it.
            {"type": "GameActionFightLifePointsLostMessage", "at_action": 96,
             "source": 4434090, "target": -1, "loss": 8},
            {"type": "GameActionFightSpellCastMessage", "at_action": 300,
             "source": 4434090, "spell": 0, "target": -1},
            # A summon's damage is still this side's damage.
            {"type": "GameActionFightLifePointsLostMessage", "at_action": 97,
             "source": -22358, "target": -1, "loss": 11},
            # A cast the log does not follow with a loss: counted, not hidden.
            {"type": "GameActionFightSpellCastMessage", "at_action": 300,
             "source": 4434090, "spell": 10, "target": -1},
            # The enemy's own cast and the damage it did to me are not mine.
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
    import importlib.util

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
    for module in ("combat.test_battle_solver", "combat.test_two_ply_world_model",
                   "combat.test_grid_model",
                   # The encoder itself, which was missing from this list until
                   # 2026-11-11 -- and a dead `0.0` fighter-feature slot survived
                   # there unnoticed for as long as nothing ran its suite. The
                   # model can only understand what the tensor carries.
                   "combat.test_grid_dataset",
                   # The trainer's input contract and loss. A corpus built for a
                   # narrower encoder used to fail deep inside a jitted matmul,
                   # and the loss silently changed from a hard label to a
                   # distribution over the solver's tied-best set -- which makes
                   # every older metric incomparable if it is ever misapplied.
                   "combat.test_grid_train",
                   "combat.test_live_policy", "combat.test_normal_build_duel",
                   "combat.test_live_data", "combat.test_side_swap",
                   "combat.test_live_learn",
                   "combat.test_online_learn", "combat.test_env_math",
                   # The positional half of the turn: what a cell costs to stand
                   # on, learned from the fights themselves.
                   "combat.test_threat",
                   # The engagement ladder the supervisor repairs with. It is the
                   # difference between a repair that works and one that logs ok=True.
                   "agent.test_hunting",
                   # Which failure is this, and what fixed it last time. The net is
                   # allowed to have no opinion, and that path is most of the suite.
                   "agent.test_problem_nn",
                   # The map as geometry rather than as twelve scalars: distance,
                   # directed line of sight and reachability, each checked against
                   # `CellMap`, which is the decoder the live client has been
                   # exercised against.
                   "combat.test_map_field",
                   # The outcome-trained value and the one-step lookahead built on
                   # it. Two failures it exists to catch already happened once: a
                   # feature-width constant drifting from the encoder (a broadcast
                   # error three frames inside a jitted matmul), and a value head
                   # with no spread -- "inert" -- which made every value-vs-solver
                   # duel measure the solver against itself.
                   "combat.test_duel_value",
                   # What a monster can actually cast. The live board sends an empty
                   # spell list for every enemy, so without this the policy is asked
                   # to avoid a reach nobody told it.
                   "combat.test_monster_mech",
                   # Wins, losses and draws kept apart, and a probability scored by
                   # a proper rule. `network_win_rate: 0.4062` over 1 win and 157
                   # draws is the score, not a win rate.
                   "combat.test_outcome_stats",
                   # A loopback probe must not be handed to HTTP_PROXY. This gate's own
                   # `/health` check failed on 2026-11-11 with `WinError 10054` while the
                   # brain was answering, because the probe went through the machine's
                   # proxy -- and the same fault made `brain_is_up` declare a live brain
                   # dead and respawn it. A false failure here accuses the product of the
                   # harness's own fault, so the rule is a test, not a comment.
                   "agent.test_bridge",
                   # Boards nobody wrote down: a seeded generator over the payload the
                   # client actually sends, checking AP/MP conservation, HP bounds,
                   # cast limits, determinism and "never raises" across both an
                   # actionable and a malformed corpus. It carries its own coverage
                   # guard, because the first version of it only ever saw passes.
                   "combat.test_battle_solver_fuzz",
                   # The structure cache the planner reasons through. It existed as
                   # 184 lines nothing constructed -- the read path was in
                   # `Planner.plan`, the flush path was nowhere, and the one
                   # construction site passed neither. This is the only delegated
                   # non-combat suite in this loop and it is here because the loop is
                   # the only place the gate delegates to a module runner.
                   "planning.test_thinking_cache",
                   # G126's production caller. The tap must reuse the loop's store,
                   # keep every state file out of the staged `data/kb` tree, never
                   # raise into the loop, and never submit a goal on its own (which
                   # would put a fabricated objective in front of the policy).
                   "planning.test_live_tap",
                   # The recalibration of `move_prob`. Three hardcoded cuts in
                   # `planner_nn` (0.20, 0.20, 0.25) are only meaningful if 0.20
                   # means one in five, and on the newest era it did not: the base
                   # rate of "this action moved the world" went from 0.22 to 0.645
                   # because `nav.goto` started working. The suite pins the three
                   # properties that make the fit safe -- never worse than identity
                   # in sample, never a negative slope (which would make the planner
                   # prefer the candidate it is sure will fail), and bit-identical
                   # output when no sidecar is present.
                   "agent.test_calibration",
                   # The inert-action attribution. The reviews quote "86% inert" as
                   # a permanent property of the agent; the corpus says it was one
                   # chronological quarter (88.6%) that ended when nav.goto started
                   # working (39.5% in the newest quarter). This suite pins the two
                   # things that keep that claim honest: exclusive layers sum to the
                   # inert count, and `obs_source=carried` -- on 96% of inert rows
                   # but 99% of effective ones -- is never reported as a cause.
                   "agent.test_inert_attribution",
                   # Glicko-2 as an archetype-stratified release gate. Pinned against
                   # Glickman's published worked example (1464.06 / 151.52 / 0.05999)
                   # and against the property that matters: a 3-case family can win
                   # 3-0 and still fail, because the gate uses r - 2*RD, not r.
                   "ai.test_glicko2"):
        command = [sys.executable, "-m", module]
        if module == "combat.test_two_ply_world_model":
            shipped_python = REPO_ROOT.parent / "runtimes" / "python" / "python.exe"
            check("the mechanics timing suite has the shipped interpreter",
                  shipped_python.is_file(), str(shipped_python))
            if not shipped_python.is_file():
                continue
            command = [str(shipped_python), "-B",
                       str(REPO_ROOT / "combat" / "test_two_ply_world_model.py")]
        proc = subprocess.run(command, cwd=str(REPO_ROOT),
                              capture_output=True, text=True, timeout=300)
        text = (proc.stderr or "") + (proc.stdout or "")
        absent = next((m for m in OPTIONAL_IMPORTS
                       if "No module named '%s'" % m in text), None) \
            if proc.returncode != 0 else None
        if absent:
            # Same policy as `tools/gate.py`: a wheel this box lacks is not a regression.
            logger.debug("   SKIP: %s needs %r, which this interpreter does not ship"
                  % (module, absent))
            check("%s is skipped for a missing optional wheel (%s)" % (module, absent),
                  True, absent)
            continue
        # Exit code 0 is not the same as "the suite ran". A module whose tests all
        # failed to *collect* still exits 0, and for a while every line here would
        # have accepted a suite of zero tests. `Ran 0 tests` is the signature of a
        # rename that broke discovery, which is exactly what a delegated suite
        # cannot report on itself.
        ran = re.search(r"^Ran (\d+) tests?", text, re.MULTILINE)
        executed = int(ran.group(1)) if ran else 0
        check("%s passes" % module, proc.returncode == 0 and executed > 0,
              text.strip()[-300:] if executed else
              "no 'Ran N tests' line: the module collected nothing\n" + text.strip()[-300:])


__all__ = ['test_side_swap', 'test_move_gate', 'test_kill_pricing',
           'test_damage_calibration_join', 'test_damage_calibration_live_evidence',
           'test_combat_suites']