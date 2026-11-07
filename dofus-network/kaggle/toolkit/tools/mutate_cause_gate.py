"""Mutation test: prove the new cause/recovery checks can actually fail.

Each mutation is applied to the real source, the affected gate sections run in a
throwaway process, and the file is restored in a `finally`. A section that crashes
counts as caught: the gate exits non-zero, which is red by definition.

Warning: this writes deliberately broken code into the working tree for a second or
two at a time. Do not run it while something else is committing or packaging the
tree (an editor auto-save, a build, or a scheduled commit) -- the restore is fast but
not atomic, and a capture inside the window would record a mutated file.

`MUT_ONLY="brain,dry run"` selects mutations by a substring of their label, so a new
batch can be proven without re-running every older one. `MUT_FILES=session_close.py,jsonio.py`
selects by the file a mutation edits -- labels are prose, the target is the thing that moved,
so re-verifying a change to one module does not depend on guessing at names.
`MUT_SHARD=i/n` splits one sweep across isolated copies by global mutation index, so the
verdicts merge cleanly.

Run a long sweep unbuffered -- `python -u tools/mutate_cause_gate.py > log` -- or its progress
is invisible until the process ends. Measured 2026-11-01: a background batch was stopped part
way through to hand the working tree back, and the log it left behind had **0 lines** for
fifteen minutes of mutation passes, so the sweep proved nothing it could show. The tree itself
was safe (`--recover` restored the in-flight file from the journal), which is the point of the
journal; the lost verdicts are the point of `-u`.

And set a selector deliberately. With neither `MUT_ONLY` nor `MUT_FILES`, the selection is the
**whole list** -- the header prints "542 of 542 mutations selected" and starts overwriting files
for hours. A caller that meant seven and passed none therefore starts a nine-hour sweep in the
foreground; when that one was cut off by its own timeout it had already verified 14 mutations
(more evidence, by accident) and left one mutation mid-file for `--recover` to undo. If you
cannot watch it, use `MUT_PLAN=1` first: it prints the count and exits without touching anything.
"""
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CTRL = ROOT / "agent" / "controller.py"
AUTO = ROOT / "agent" / "autopilot.py"
OBSV = ROOT / "agent" / "observe.py"
BRIDGE = ROOT / "agent" / "bridge.py"
PLAN = ROOT / "agent" / "planner.py"
EXEC = ROOT / "agent" / "executor.py"
# Not REPORT: that name is rebound to session_report.py below, the same
# shadowing that once silently repointed eight BOARDS anchors (G71).
RECOVERY = ROOT / "tools" / "recovery_report.py"
SREPORT = ROOT / "tools" / "session_report.py"
BRAIN = ROOT / "combat" / "brain_service.py"
# The board model and its payload form: `to_fight_state()` is the seam the live policy is
# measured through offline (§L.19), so a silently dropped key is a wrong measurement.
BSOLVER = ROOT / "combat" / "battle_solver.py"
LPOL = ROOT / "combat" / "live_policy.py"
# §L.34 moved the live path onto `contract_from_board` / `board_block` / `live_view`. The decision
# reads only the contract's two booleans, so a wrong contract is a silently different refusal rate on
# live boards -- and nothing in the sweep could see this file until it became a target.
OBS = ROOT / "combat" / "observation.py"
# The arrays the trained head reads. A wrong cell here is a decision change, not a crash, and the
# only other witness was a duel run far away from the gate (§L.34, §L.38).
PVAL = ROOT / "combat" / "plan_value.py"
# The production entry point: the decision row every offline read-out is built from, its rotation
# threshold, and its promise that a bad disk never reaches the fight.
CSRV = ROOT / "combat" / "combat_ai_server.py"
# The forward math and the loader that decides how wide its inputs are -- #85's last two files.
MODEL = ROOT / "combat" / "encounter" / "model.py"
CKPT = ROOT / "combat" / "encounter" / "checkpoint.py"
# The measuring tool the fight numbers now depend on. A mutation here is invisible to the gate check
# that calls it -- that is exactly why `test_doc_facts_self_witness` points DOCS at a throwaway file.
DOCFACTS = ROOT / "tools" / "doc_facts.py"
# The duel harness one level up: its paired-delta statistics are what every §L win rate is read
# through, and a sign test that prints 1.0 for any data is a dead instrument, not a cautious one.
WSP = ROOT.parent / "tools" / "wsp_fight.py"
# The array backend and the environment math that runs on it. Both branches of `at()` are reached
# through the same call sites, so a call-site mutation is visible to whichever interpreter sweeps.
EBACK = ROOT / "combat" / "encounter" / "backend.py"
EENV = ROOT / "combat" / "encounter" / "env.py"
READINESS = ROOT / "tools" / "brain_readiness.py"
# `BOARDS`, not `REPLAY`: a second REPLAY below points at command_replay.py, and the
# shadowed name silently repointed these eight anchors at the wrong file, so they were
# reported as SKIP instead of never running.
BOARDS = ROOT / "tools" / "policy_over_record_boards.py"
# The live page and its own suite, one directory up: fight.state is where the board is
# produced, so a gate that only mutates Python cannot prove it reads that producer.
PAGE = ROOT.parent / "scripts" / "mobile-disguise.js"
PAGE_TEST = ROOT.parent / "tools" / "test_fight_state_fields.js"
# Read the page's own stamp so a bump cannot orphan the mutation that anchors on it. A pinned
# literal goes to zero matches on the next generation, and the driver reports that as SKIP —
# which reads like a pass in a wall of CAUGHT lines.
import re as _re
_PAGE_STAMP = (_re.search(r"var AGENT_BUILD = '([^']+)';",
                          PAGE.read_text(encoding="utf-8")) or (None, ""))[1]
SCAN = ROOT / "tools" / "group_scan.py"
PLAN_STACK = ROOT / "agent" / "plan_stack.py"
VERIF = ROOT / "agent" / "verifier.py"
FEATURES = ROOT / "agent" / "features.py"
NNPLAN = ROOT / "agent" / "planner_nn.py"
KCTX = ROOT / "agent" / "knowledge_context.py"
SHAPES = ROOT / "tools" / "quest_advance_shapes.py"
SCHEMA = ROOT / "agent" / "schema.py"
CAPS = ROOT / "data" / "capabilities.json"
INGEST = ROOT / "tools" / "ingest_observed_quests.py"
CSPANEL = ROOT.parent / "desktop" / "aipanel.cs"
CSBUNDLE = ROOT.parent / "desktop" / "clientbundle.cs"
# The brain-attach verdict lives here so the gate can compile and run it; the
# daemon's own staleness verdict lives here.
CSBOOT = ROOT.parent / "desktop" / "BootState.cs"
BRAIND = ROOT / "agent" / "brain_daemon.py"
# The instrument that decides whether a vendor bundle can carry the hooks, and the
# record it writes into the shim. Nothing in the gate opened this file before
# 2026-11-04, which is how `bundleBytes: text.length` reached a shipped table.
CMPROBE = ROOT.parent / "tools" / "compat_probe.js"
# The reference patcher the shipped updater mirrors, and the guard that makes a
# second adoption a no-op instead of a second wrapper around the same callback.
APRULES = ROOT.parent / "tools" / "apply-patch-rules.py"
# The consult gate: the fitted "the head is unsure, the rules decide" threshold, and
# the one place in the encoder arm where it is honoured.
CONSULTG = ROOT / "agent" / "consult_gate.py"
# The fight calibrator's promotion gate. It decided whether a learned model replaces the
# analytic estimate, and until 2026-11-04 nothing in the sweep opened this file at all
# (SS L.43: the largest unanchored half of the runtime).
OLEARN = ROOT / "combat" / "online_learn.py"
# The duel simulator every recorded win rate and value-model row comes from. It was
# executed by the gate for months without being mutable (SS L.43).
DUEL = ROOT / "combat" / "duel.py"
# The grounding seam that ships the 93.3 command number. #67 found a defect in it that a
# static import walk missed (sklearn is imported inside a method), which is the reason it
# needs a mutable anchor rather than more reading.
LEX = ROOT / "ai" / "lexical_policy.py"
CSPA = CSPANEL
CSB2 = ROOT.parent / "desktop" / "agentbridge.cs"
CMAINF = ROOT.parent / "desktop" / "mainform.cs"
CSPROG = ROOT.parent / "desktop" / "program.cs"
TDS = ROOT / "agent" / "tool_dataset.py"
ENGINE = ROOT.parent / "tools" / "patch-engine.js"
KTOOL = ROOT / "tools" / "sync_kaggle_toolkit.py"
READY = ROOT / "tools" / "live_ready.py"
SIMRUN = ROOT / "sim" / "run.py"
CMD = ROOT / "agent" / "commands.py"
CDATA = ROOT / "tools" / "build_command_dataset.py"
SHADOW = ROOT / "agent" / "command_shadow.py"
SHADOW_REPORT = ROOT / "tools" / "command_shadow_report.py"
REPLAY = ROOT / "tools" / "command_replay.py"
LIVE = ROOT / "tools" / "live_evidence.py"
READY2 = ROOT / "tools" / "live_ready.py"
HOSTCTL = ROOT / "tools" / "host_control.py"
AUTOLIVE = ROOT / "tools" / "auto_live.py"
ONNX = ROOT / "agent" / "command_onnx.py"
WORLD = ROOT / "sim" / "world.py"
TRANS = ROOT / "tools" / "build_live_transitions.py"
FORE = ROOT / "agent" / "foresight.py"
PROBE = ROOT / "tools" / "live_outcome_probe.py"
JANITOR = ROOT / "tools" / "data_janitor.py"
REPORT = ROOT / "tools" / "session_report.py"
# The instrument that writes the frozen planner corpus. The gate replays the checked-in
# file, so a collector that reads nothing stayed invisible until 2026-11-01.
FREEZE = ROOT / "tools" / "freeze_planner_replay.py"
CLOSE = ROOT / "tools" / "session_close.py"
MOVE = ROOT / "ai" / "train_move_head.py"
SIDE = ROOT / "combat" / "side_swap.py"
PROGRESS = ROOT / "tools" / "progress_report.py"
ARTIFACTS = ROOT / "agent" / "artifacts.py"
JSONIO = ROOT / "agent" / "jsonio.py"
LOCKTEST = ROOT / "agent" / "test_brain_daemon.py"
# Outside the mirror: the damage read-out lives in the repo's root `tools/`, the folder
# the JS page suites sit in, not in `dofus-network/tools/`.
DAMAGE = ROOT.parent / "tools" / "damage_calibration.py"
# The gate's own formatter: a mutation here must be caught by the gate it breaks.
REG = ROOT / "agent" / "regression.py"

SNIPPET = ("import agent.regression as r\n"
           # The snippet tells the gate it is inside a sweep: a check that re-derives a
           # corpus from 65 MB of live logs would otherwise pay that cost 539 times, and
           # this host has 7.8 GB. The cheap fixture checks still run and still bite.
           "import os\n"
           "os.environ['GATE_IN_MUTATION_SNIPPET'] = '1'\n"
           "for _t in (\n"
           "    r.test_failure_causes,\n"
           "    r.test_absent_client_recovery,\n"
           "    r.test_identity_binding,\n"
           "    r.test_live_witnesses,\n"
           "    r.test_verifier_head_gate,\n"
           "    r.test_observation_text,\n"
           # test_kaggle_toolkit_sync (the real tree) is deliberately NOT run inside a
           # mutation sweep: a mutation edits a mirrored source file, so the drift check
           # fails for every mutation and every batch reads "CAUGHT" no matter what the
           # checks think. The fixture suite below exercises the same rules on temp
           # trees, so the three kaggle mutations are killable here; the real-tree test
           # stays in the gate (`run()`).
           "    r.test_sync_kaggle_toolkit_rules,\n"
           "    r.test_quest_chain_replay,\n"
           # The frozen planner replay: without it, a mutation inside the tool that writes
           # that corpus had no check that could see it (measured 2026-11-01, MISSED).
           "    r.test_planner_replay,\n"
           "    r.test_live_ready,\n"
           "    r.test_sim_planner_runs,\n"
           "    r.test_command_parser,\n"
           "    r.test_command_dataset,\n"
           "    r.test_command_shadow,\n"
           "    r.test_command_shadow_report,\n"
           "    r.test_command_replay,\n"
           "    r.test_live_evidence,\n"
           "    r.test_command_onnx_decode,\n"
           "    r.test_egress_preflight,\n"
           "    r.test_host_launch_guard,\n"
           "    r.test_auto_live_guards,\n"
           "    r.test_auto_live_summarise,\n"
           # The bundle updater and the multi-instance selection are source
           # assertions over desktop/*.cs: nothing else in here watches them, and
           # their only other witness is a launch against the real CDN.
           "    r.test_bundle_resilience,\n"
           "    r.test_bundle_updater,\n"
           "    r.test_multi_instance_targeting,\n"
           "    r.test_per_instance_ai,\n"
           "    r.test_keep_alive_instrument,\n"
           "    r.test_tutorial_hold,\n"
           "    r.test_live_transitions,\n"
           "    r.test_claim_calibration,\n"
           "    r.test_worker_headroom_layer,\n"
           "    r.test_foresight_net,\n"
           "    r.test_counterfactual_factory_alive,\n"
           "    r.test_repair_credit,\n"
           "    r.test_reads_are_not_actions,\n"
           "    r.test_dialog_labels,\n"
           "    r.test_dialog_repeat_guard,\n"
           "    r.test_quest_accept_planner,\n"
           "    r.test_observed_quest_catalog,\n"
           "    r.test_observed_quest_ingest,\n"
           "    r.test_brain_autostart,\n"
           "    r.test_brain_readiness,\n"
           # The daemon's currency verdict and the host's brain-attach decision. Written
           # 2026-11-04 and MISSED at first for exactly the reason the two comments above
           # name: four mutations against `BootState.cs` and `agent/brain_daemon.py`
           # reported "0 failing checks" because the sweep ran 97 tests, none of which
           # opens either file.
           "    r.test_boot_state,\n"
           "    r.test_brain_reports_stale_code,\n"
           "    r.test_measured_sizes_say_their_unit,\n"
           "    r.test_patch_rules_are_idempotent,\n"
           "    r.test_consult_gate_is_fitted_and_live,\n"
           "    r.test_js_page_suites,\n"
           "    r.test_board_replay,\n"
           "    r.test_group_scan_reader,\n"
           "    r.test_npc_start_rotation_bound,\n"
           "    r.test_group_refusal_routing,\n"
           "    r.test_combined_objectives,\n"
           "    r.test_objective_travel_is_bounded,\n"
           "    r.test_adjacent_crossing_is_bounded_tighter,\n"
           "    r.test_promise_shadow_names_a_bounced_crossing,\n"
           "    r.test_online_learn_gate_needs_transfer_it_did_not_copy,\n"
           "    r.test_duel_simulator_keeps_its_own_contract,\n"
           # The encounter env is dense array math that nothing else in here steps: two
           # arms inside combat/encounter/env.py read MISSED across a full 595-arm pass
           # because no check the snippet runs could reach env.py at all.
           "    r.test_env_scatter_and_mask_are_witnessed,\n"
           "    r.test_the_search_borders_are_bounded,\n"
           # The brain has to say which code wrote a row: the page stamp never
           # moved, so a landed fix was invisible in the log (#101).
           "    r.test_the_brain_stamps_its_own_code_identity,\n"
           "    r.test_the_plan_reason_reaches_the_action_row,\n"
           "    r.test_a_silent_house_map_is_left_by_the_crossings_it_knows,\n"
           # The settle that lets a crossing finish, and the row that says why a run
           # ended. Both are only visible in the live log, so a mutation of either has
           # to be caught here rather than discovered on the next run.
           "    r.test_a_crossing_in_flight_is_waited_out,\n"
           "    r.test_the_session_end_row_names_the_reason,\n"
           # The return grant: without it a spent visit bars the objective's own map and
           # the quest is retired (measured live, 90 retired in eleven steps).
           "    r.test_a_return_to_the_objective_map_is_granted_once,\n"
           # The failure budget that keeps one unplannable pass from executing the quest.
           "    r.test_the_active_quest_survives_one_unplannable_pass,\n"
           # ...and the fork copy it had to be added to. This suite was not in the
           # snippet, so the arm that deletes the copy reported MISSED with 0 failing
           # checks: nothing that reads `fork()` ran.
           "    r.test_fork_carries_the_planner_state,\n"
           "    r.test_lexical_seam_fills_only_what_the_sentence_states,\n"
           "    r.test_search_prefers_fresh_maps,\n"
           "    r.test_no_call_is_explained,\n"
           "    r.test_session_report,\n"
           "    r.test_data_janitor,\n"
           "    r.test_session_close,\n"
           "    r.test_session_close_hook,\n"
           "    r.test_the_close_fits_the_timeout_that_kills_it,\n"
           "    r.test_side_swap,\n"
           "    r.test_move_gate,\n"
           "    r.test_progress_report,\n"
           "    r.test_artifact_rollback,\n"
           "    r.test_ready_objective_scan,\n"
           "    r.test_objective_buckets,\n"
           "    r.test_objective_parameter_table,\n"
           "    r.test_knowledge_identity,\n"
           # The knowledge index's compact stores. Measured 2026-11-03: the snippet listed 88
           # named tests and none of them could see `agent/executor.py::_KnowledgeIndex`, so four
           # mutations written against the projection store reported MISSED with 0 failing checks
           # -- not because the gate is weak there, but because nothing that reads it ran.
           "    r.test_knowledge_index_storage,\n"
           "    r.test_board_payload_round_trip,\n"
           # §L.34's parity claim is the only thing standing between a silent contract change and
           # a shipped one: `combat/observation.py` had no mutation target and no snippet entry
           # before this, so four mutations written against `contract_from_board` would have
           # reported MISSED with 0 failing checks -- the same failure mode the knowledge index
           # hit, reached here by a different file.
           "    r.test_slim_live_observation,\n"
           # §L.34 showed the enemy spellbooks feed the actor's own arrays, so this is the file where
           # a wrong cell changes what the head does. It had no anchor and no fixture until now.
           "    r.test_encounter_input_tables,\n"
           # The production entry point's own writer: row shape, rotation and the never-raise path.
           # #13 was pinned through agent/jsonio.py, which left combat_ai_server.py unmutated.
           "    r.test_combat_decision_log,\n"
           # The forward and its loader: a wrong layer or a wrong config width changes every
           # decision while the request still returns a well-formed action.
           "    r.test_encounter_model_and_checkpoint,\n"
           # The same reasoning about the measuring tool: `doc_facts` now guards every fight number,
           # and a checker mutated to report no problems is invisible to the gate check that runs it.
           "    r.test_doc_facts_self_witness,\n"
           # §L.21's measurement path: the synthesis that lets the duel engine execute a client
           # action, and the statistics that decide whether a paired delta is a result at all.
           "    r.test_live_action_realization,\n"
           "    r.test_duel_harness_statistics,\n"
           "    r.test_client_confirmed_starts,\n"
           "    r.test_quest_advance_shapes,\n"
           "    r.test_session_report_census,\n"
           "    r.test_gate_shadow_reaches_the_trajectory,\n"
           "    r.test_veto_alternative_priced,\n"
           "    r.test_stall_retirement_is_bounded,\n"
           "    r.test_session_report_play_quality,\n"
           "    r.test_npc_map_fact_is_routed,\n"
           "    r.test_map_graph_live_wins,\n"
           "    r.test_read_streak_is_bounded,\n"
           "    r.test_report_instrument,\n"
           "    r.test_settle_waits_for_promised_effect,\n"
           "    r.test_plan_stack,\n"
           "    r.test_console_output_is_utf8,\n"
           "    r.test_text_io_declares_encoding,\n"
           "    r.test_damage_calibration_sees_the_recorded_fights,\n"
           "    r.test_check_helper_survives_a_tuple_detail,\n"
           "    r.test_delegated_suites_report_themselves,\n"
           "    r.test_one_log_record_per_line,\n"
           "    r.test_tools_scripts_find_the_repo_root,\n"
           # The harness's own wiring: a mutation that drops a test from the snippet must
           # be caught by the rule that requires the snippet to see its targets.
           "    r.test_the_snippet_can_see_its_own_mutation_targets,\n"
           "    r.test_the_mutation_pass_stays_affordable,\n"
           "    r.test_every_mutation_anchor_still_points_at_code,\n"
           "    r.test_live_evidence_lands_in_the_repository,\n"
           "):\n"
           "    r._run_test(_t)\n"
           "print('FAILURES=%d' % len(r._FAILURES))\n"
           "[print('  -', f[:74]) for f in r._FAILURES]\n")


MUTATIONS = [
    # ---- a learned destination is routed, not handed to the client as a map id
    ("npc-map: the learned map is sent without routing", PLAN,
     '                calls.append(self._travel_call(obj, int(target), obs,\n                                               key=("npc", int(npc))))',
     '                calls.append({"tool": "nav.goto", "args": {"map_id": int(target)},\n                              "why": "travelled (mutated)"})'),
                ("settle-dialog: a hop waits for the journey's destination", AUTO,
     '            if bool(after_map) and after_map != before_map:\n                return True\n',
     '            if False and bool(after_map) and after_map != before_map:\n                return True\n'),
    # ---- a reply promises a new dialog page, so the settle can end (2026-10-29)
    ("settle-dialog: a reply settles only on an objective change", AUTO,
     '            elif args.get("reply") is not None or args.get("reply_text") is not None:\n',
     '            elif False:\n'),
    ("settle-dialog: a dialog tick counts as a new page", AUTO,
     '        return (bool(dialog.get("open")), dialog.get("npc_id"),\n',
     '        return (dialog.get("ids_ready"), bool(dialog.get("open")), dialog.get("npc_id"),\n'),
    # ---- the census must not name deaths it has not measured (G79)
    ("census: a page-shaped step row is keyed by the flat key only", SREPORT,
     '    position = _position(row)\n',
     '    position = {}\n'),
    ("census: a row with no verdict is counted as dead", SREPORT,
     '        elif verdict or row.get("ok") is False or answer:\n            dead[key] += 1\n',
     '        else:\n            dead[key] += 1\n'),
    ("census: a page refusal is not counted as dead", SREPORT,
     '        elif verdict or row.get("ok") is False or answer:\n            dead[key] += 1\n',
     '        elif verdict or row.get("ok") is False:\n            dead[key] += 1\n'),
# ---- a run of reads is looking, not acting (live 2026-10-29)
    ("read-streak: reads are never bounded", AUTO,
     '        if self._read_streak < READ_STREAK_LIMIT:\n            return "keep"',
     '        if True:\n            return "keep"'),
# ---- the client corrects the exported map index (live 2026-10-29)
    ("graph-live: the router plans on the export without the client's exits", EXEC,
     '        self._maps.observe(here, _obs_exit_map(observation))\n',
     "        pass  # mutated: the export is planned on without the client's exits\n"),
    ("graph-live: a corrected edge keeps its stale BFS tree", EXEC,
     '            self._trees.clear()\n',
     '            pass  # mutated: the stale BFS tree survives the correction\n'),
    # ---- the way back out of a house: the landing cell is a door guess
    ("door-arrival: the landing cell is not recorded as the way back", PAGE,
     "                    if (cell != null) _agentDoorNote(mapId, cell, _agentDoorWatch.map, 'arrival');",
     "                    if (false) _agentDoorNote(mapId, cell, _agentDoorWatch.map, 'arrival');"),
    ("door-arrival: an arrival guess replaces a walked door", PAGE,
     "            if (prior && prior.via !== 'arrival' && how === 'arrival') return;",
     "            if (false && prior && prior.via !== 'arrival' && how === 'arrival') return;"),
    ("door-arrival: a guess that fails is kept and a walked door is dropped", PAGE,
     "                                        && list[d].via === 'arrival') {",
     "                                        && list[d].via === 'walk') {"),
    # ---- a border cell the character already stands on never transitions (live 2026-10-29)
    ("edge-step-off: the border cell is asked for directly, never stepped off", PAGE,
     "var offEdge = pin ? null : _agentStepOffCell(currentCell, edgeAvoid);",
     "var offEdge = null;"),
    ("edge-step-off: the step off may land on another border cell", PAGE,
     "if (avoid && avoid.indexOf(cand) >= 0) continue;",
     "if (false && avoid && avoid.indexOf(cand) >= 0) continue;"),
    ("edge-step-off: a cell-pinned tutorial is stepped off anyway", PAGE,
     "var pin = tutorialActive && allowedCells;",
     "var pin = false;"),
    ("edge-step-off: the walk back onto the border is never scheduled", PAGE,
     "                                            iso.gotoNeighbourMap(changeDir, exitCell, 0, 0);\n"
     "                                            window.__agentLastNav = { at: Date.now(),",
     "                                            window.__agentLastNav = { at: Date.now(),"),
    # ---- a named reply whose dialog page moved on (live 2026-10-30)
    ("reply-fallback: a moved-on page with one reply is refused again", PAGE,
     "                if (count === 1) {", "                if (false) {"),
    ("reply-fallback: the fallback answers a page with several choices", PAGE,
     "                if (count === 1) {", "                if (count >= 1) {"),
    ("reply-fallback: the substitution sends no index", PAGE,
     "                    return { want: 0, error: null, data: null,",
     "                    return { want: null, error: null, data: null,"),
    ("reply-fallback: the substituted text is not reported", PAGE,
     "                                         live_text: texts[0] != null",
     "                                         live_text: null"),
    # ---- a close that was sent is not a failure (live 2026-10-29)
    ("close-sent: a sent close is called a failure again", PAGE,
     "                        if (sent) {", "                        if (false) {"),
    ("close-sent: a throwing path counts as sent", PAGE,
     "if (/:ok$/.test(attempts[closeIdx])) sent = true;",
     "if (attempts[closeIdx]) sent = true;"),
    # ---- the explore walked straight back to the map it came from (live 2026-10-29)
    ("explore-back: the explore walks back to the map it just left", PLAN_STACK,
     "if back is not None and dest == back:",
     "if False and back is not None and dest == back:"),
    ("explore-back: a dead end walks nowhere instead of crossing back", PLAN_STACK,
     "    return fallback\n", "    return None\n"),
    ("explore-self: a self-edge is offered as an explore direction", PLAN_STACK,
     '        if here is not None and dest == here:\n            continue',
     '        if False and here is not None and dest == here:\n            continue'),
    ("explore-self: a blocked border is offered as an explore direction", PLAN_STACK,
     '        if row.get("blocked"):\n            continue',
     '        if False and row.get("blocked"):\n            continue'),
    # ---- the level gate walked back to an empty kill map all session (G86)
    ("gate-dry: an empty kill map is walked back to forever", PLAN,
     "            if dry < RETURN_LIMIT:", "            if True:"),
    ("gate-dry: the empty looks are never counted", PLAN,
     "                self._gate_dry[dry_spot] = self._gate_dry.get(dry_spot, 0) + 1",
     "                self._gate_dry[dry_spot] = self._gate_dry.get(dry_spot, 0) + 0"),
    ("gate-dry: a group seen on the map does not clear the memory", PLAN,
     "            if usable:\n                self._gate_dry.pop(dry_spot, None)\n"
     "                self._gate_dry_warned.discard(dry_spot)",
     "            if usable:\n                pass  # mutated: the dry memory survives a group"),
    # ---- the objective travel funnel re-entered the same map unbounded (G87)
    ("objective-travel: the return is never recognised", PLAN,
     "                if returning:", "                if False:"),
    ("objective-travel: the spent budget does not stop the walk", PLAN,
     "                    if spent >= RETURN_LIMIT:\n                        # A read, not nothing",
     "                    if False:\n                        # A read, not nothing"),
    ("objective-travel: one return past the budget is still walked", PLAN,
     "                    if spent >= RETURN_LIMIT:", "                    if spent > RETURN_LIMIT:"),
    ("objective-travel: a return never spends the budget", PLAN,
     "                    self._returns[travel_key] = spent + 1",
     "                    self._returns[travel_key] = spent + 0"),
    ("objective-travel: a different trip keeps the stale budget", PLAN,
     "                    self._returns[travel_key] = 0",
     "                    pass  # mutated: the return budget survives another trip"),
    # ---- a border that refused twice bounced the whole session (2026-11-04)
    # No arm for `limit = ADJACENT_ROUTE_LIMIT if remaining == 1 else ...`: the first
    # version of the fix scaled the *distance* budget, and the live session showed that
    # shape was unobservable once each border carries its own count -- a mutation of it
    # read MISSED with eight other arms green. The distance branch was deleted rather
    # than kept and papered over with a check that could not tell it apart.
    ("adjacent-border: the refused crossing is asked again by direction", PLAN,
     "                                                    spent=sorted(spent) or None)",
     "                                                    spent=None)"),
    ("adjacent-border: a spent border still counts as an exit to walk", PLAN,
     "        exits = cls._observed_exits(obs)\n        if spent:",
     "        exits = cls._observed_exits(obs)\n        if False:"),
    ("adjacent-border: the dead end never names the border that refused", PLAN,
     "                refused = [e[\"direction\"] for e in self._observed_exits(obs)\n"
     "                           if e[\"map_id\"] in spent]",
     "                refused = []  # mutated: nothing is reported as refused"),
    ("adjacent-border: an ask for a border is never counted", PLAN,
     "        self._crossings[entry] += 1",
     "        self._crossings[entry] += 0"),
    ("adjacent-border: a border that refused twice is never spent", PLAN,
     "                     and tried >= CROSSING_ASK_LIMIT}",
     "                     and tried >= 10 ** 9}"),
    ("adjacent-border: the crossing the step asks for is never attributed", PLAN,
     "        if isinstance(dest, int) and dest != here:",
     "        if False:"),
    ("adjacent-border: a fork restarts a border the parent already spent", PLAN,
     "        twin._crossings = dict(self._crossings)",
     "        pass  # mutated: the fork forgets which borders were spent"),
    ("adjacent-border: arriving on the map never reopens the border it used", PLAN,
     "        seen.add(here_i)",
     "        pass  # mutated: the travel budget survives an arrival"),
    # ---- the shadow that names a bounced crossing (2026-11-04, #96)
    ("promise: a crossing that never landed is reported as met", VERIF,
     "        return \"met\" if there == want else \"unmet\"",
     "        return \"met\""),
    ("promise: a direction ask that stayed on the map is reported as met", VERIF,
     "        return \"met\" if there != here else \"unmet\"",
     "        return \"met\""),
    ("promise: no post-observation reports success instead of unknown", VERIF,
     "    if not isinstance(there, int):\n        return \"unknown\"",
     "    if not isinstance(there, int):\n        return \"met\""),
    ("promise: every call is judged as a crossing, whatever the tool", VERIF,
     "    if tool != \"nav.goto\":\n        return \"na\"",
     "    if False:\n        return \"na\""),
    ("promise: the row is never stamped from the call and the observations", AUTO,
     "                    promise=promise_state(call.tool, call.args, before, after),",
     "                    promise=None,"),
    ("promise: the summary stops counting what the rows say", AUTO,
     "        if step.promise:",
     "        if False:"),
    # ---- the fight calibrator's promotion gate (SS L.43: this file had no anchor)
    ("calibrate: a verdict is issued on fewer fights than the floor", OLEARN,
     "    if count < min_fights:", "    if False:"),
    ("calibrate: a copy of the analytic ranking clears the AUC margin", OLEARN,
     "MARGIN_AUC = 0.02", "MARGIN_AUC = 0.0"),
    ("calibrate: any permutation result is accepted", OLEARN,
     "MAX_PERM_P = 0.10", "MAX_PERM_P = 1.5"),
    ("calibrate: the permutation comparison is dropped", OLEARN,
     "    elif perm_p > MAX_PERM_P:", "    elif False:"),
    # ---- the duel simulator (SS L.43: executed for months, never mutated)
    ("duel: overtime is scored on raw HP instead of the healthier fraction", DUEL,
     "        state.winner = a.id if ha > hb else (b.id if hb > ha else None)",
     "        state.winner = a.id if a.hp > b.hp else (b.id if b.hp > a.hp else None)"),
    ("duel: the round cap is one turn short", DUEL,
     "    if state.round > state.max_rounds:", "    if state.round >= state.max_rounds:"),
    ("duel: the side that is not acting keeps its AP", DUEL,
     "    state.opponent.ap = 0.0", "    state.opponent.ap = state.opponent.max_ap"),
    ("duel: a heal effect is chosen even when the target is not an ally", DUEL,
     "    if ally and heal > 0:", "    if heal > 0:"),
    ("duel: a fractional AP cost becomes free", DUEL,
     "        ap_cost=max(1, int(round(ap))),", "        ap_cost=int(round(ap)),"),
    ("duel: an engaged pair no longer needs line of sight", DUEL,
     "                and cells.has_los(a, b)):", "                and True):"),
    ("duel: the board gives the enemy side to me", DUEL,
     '        fighters=[_fighter(actor, "player"), _fighter(opponent, "enemy")],',
     '        fighters=[_fighter(actor, "enemy"), _fighter(opponent, "player")],'),
    ("duel: the lookahead leaves the AP it borrowed spent", DUEL,
     "    actor.ap, actor.mp, opponent.ap, opponent.mp = saved",
     "    pass  # mutated: the borrowed resources are not put back"),
    # ---- the grounding seam (93.3 shipped through these five lines)
    ("seam: only the first digit of a count is moved", LEX,
     '        m = re.match(r"^\\s*(\\d+)\\s+(\\S.*)$", value)',
     '        m = re.match(r"^\\s*(\\d)\\s*(\\S.*)$", value)'),
    ("seam: a stated value overwrites the field the span already filled", LEX,
     "        if field in params and field not in out and value is not None:",
     "        if field in params and value is not None:"),
    ("seam: a k-scale price is taken as kamas", LEX,
     '        stated("price", int(m.group(1)) * 1000)',
     '        stated("price", int(m.group(1)))'),
    ("seam: coordinates are written even over a half-grounded move", LEX,
     '    if all(f in params for f in ("x", "y")) and not any(f in out for f in ("x", "y")):',
     '    if all(f in params for f in ("x", "y")):'),
    ("seam: the first value that matches wins, not the longest", LEX,
     "        if needle in folded and (best is None or len(value) > len(best)):",
     "        if needle in folded and best is None:"),
    ("seam: a value is grounded on a fragment of the word", LEX,
     '        needle = " " + _fold(value) + " "',
     "        needle = _fold(value)"),
    ("seam: folding deletes punctuation instead of separating it", LEX,
     '    return re.sub(r"[^a-z0-9 ]+", " ", unicodedata.normalize("NFKD", (text or "").lower())',
     '    return re.sub(r"[^a-z0-9 ]+", "", unicodedata.normalize("NFKD", (text or "").lower())'),
    # ---- the search walked the same ring three times (G88)
    ("fresh-search: a walked destination is taken again", PLAN,
     "        if chosen is not None and int(chosen[\"map_id\"]) not in seen:",
     "        if chosen is not None:  # mutated: a walked destination is taken as if fresh"),
    ("fresh-search: the walked map is never recorded", PLAN,
     "        seen.add(here_i)",
     "        pass  # mutated: nothing is ever recorded as walked"),
    ("fresh-search: a scripted edge hint is overridden", PLAN,
     "        if hint:\n"
     "            # A hint is scripted (\"walk the right edge\"), not a search: it is the",
     "        if False:  # mutated: the scripted edge is re-chosen by the search\n            # A hint is scripted (\"walk the right edge\"), not a search: it is the"),
    ("fresh-search: an unkeyed discovery search stays stateless", PLAN,
     "            key = (obj.get(\"quest_id\"), oid)",
     "            return call  # mutated: no search memory without a travel key"),
    ("fresh-search: an id-less objective shares one search key", PLAN,
     "            if oid is None:\n"
     "                return self._explore_call(obj, obs, avoid=avoid, spent=spent)",
     "            if oid is None:\n                oid = 0  # mutated: every id-less objective shares one key"),
    # ---- the dead end names itself (G90)
    ("no-call: the dead end is never named", PLAN,
     '            "quest mode: nothing plannable -- %d ready objective(s), %d active "',
     '            "quest mode: nothing to do -- %d ready objective(s), %d active "'),
    ("no-call: the row drops the reason the planner wrote", AUTO,
     '                    why = str(getattr(self.runtime.policy, "last_no_call", "")\n'
     '                              or "<policy gave no reason>")[:220]',
     '                    why = "<policy gave no reason>"  # mutated: the sentence is dropped'),
    # ---- the search that walks a ring forever (#100, measured 2026-11-05)
    ("search: a spent border is asked again", PLAN,
     "        spend |= {d for (which, from_map, d), tried in self._crossings.items()\n"
     "                  if which == key and from_map == here_i\n"
     "                  and tried >= CROSSING_ASK_LIMIT}",
     "        pass  # mutated: the search never spends a border it already asked"),
    ("search: the spent map is never recorded", PLAN,
     "            if spend and self._open_exits(obs):\n"
     "                self._exhausted_at.add((key[0], key[1], here_i))",
     "            if spend and self._open_exits(obs):\n"
     "                pass  # mutated: nothing records that this map is spent"),
    ("search: the ask that lands a fresh map is never counted", PLAN,
     "        if chosen is not None and int(chosen[\"map_id\"]) not in seen:\n"
     "            self._note_crossing(key, here_i, int(chosen[\"map_id\"]))",
     "        if chosen is not None and int(chosen[\"map_id\"]) not in seen:\n"
     "            pass  # mutated: the crossing the search asks is not attributed"),
    ("search: the fallback forgets which borders are spent", PLAN,
     "        exits = self._open_exits(obs, spend)",
     "        exits = self._open_exits(obs)  # mutated: spent borders are back in play"),
    ("search: the rotated ask is never counted", PLAN,
     "        count = self._note_crossing(key, here_i, int(row[\"map_id\"]))",
     "        count = 0  # mutated: rotating without spending, so the lap never ends"),
    ("search: the scripted edge is asked forever", PLAN,
     "                        if self._crossings.get(\n"
     "                                (key, int(here_now), dest), 0) >= CROSSING_ASK_LIMIT:",
     "                        if False:  # mutated: the named edge is always good for another ask"),
    ("brain stamp: the digest is a label rather than the loaded code", AUTO,
     "        _BRAIN_BUILD = \"brain-\" + h.hexdigest()[:12]",
     "        _BRAIN_BUILD = \"brain-000000000000\"  # mutated: a constant, so the stamp lies"),
    ("brain stamp: the row leaves the seam without a code identity", AUTO,
     "        blob = _stamp_brain_build(blob)",
     "        pass  # mutated: nothing stamps, and every row is anonymous again"),
    ("brain stamp: the source is named instead of read", AUTO,
     "                out[name] = inspect.getsource(loaded)",
     "                out[name] = name  # mutated: module names, not loaded code"),
    ("plan reason: the row is written without it", AUTO,
     "                    why=str(_meta.get(\"why\") or \"\")[:240],",
     "                    pass  # mutated: the plan sentence never reaches the record"),
    ("plan reason: the row reads the directive instead", AUTO,
     "                    why=str(_meta.get(\"why\") or \"\")[:240],",
     "                    why=str(call.args.get(\"why\") or \"\")[:240],  # mutated: args are the directive, not the reason"),
    ("house map: the accessor reads the flat key again", OBSV,
     "    return flatten(obs).get(\"map_id\")",
     "    return obs.get(\"map_id\")  # mutated: the slim projection reads as nowhere"),
    ("house map: the indexed crossing is never asked", PLAN,
     "                ask = self._indexed_crossing_ask(obj, obs, here, key, target)",
     "                ask = None  # mutated: a spent border ends in a read again"),
    ("house map: the border budget is not applied to it", PLAN,
     "        spent = {dest for (which, from_map, dest), tried in self._crossings.items()\n"
     "                 if key is not None and which == key and from_map == here_i",
     "        spent = set()  # mutated: nothing is ever spent, so one wall is walked\n        if False and key is not None and which == key and from_map == here_i"),
    ("house map: the ask is not counted", PLAN,
     "            self._note_crossing(key, here_i, near)",
     "            pass  # mutated: asked forever, counted never"),
    ("house map: the client's own exit list is overridden", PLAN,
     "            if isinstance(live_exits, list) and live_exits:\n"
     "                return None",
     "            if False:\n                return None"),
    # A crossing is a walk the client performs. The live log of 2026-11-04/05 shows one
    # border asked five times at 6.5 s spacing (the landing came 30 s after the first ask)
    # and another asked ten times while the character paced cell 299 -> 537 -> 299. The
    # settle is what keeps the loop from re-asking over its own walk, so every part of it
    # has to bite: the slide, the cap, the tool guard, and the loop actually re-taking
    # the deadline after each movement.
    ("in flight: a walk still moving is cut off at the base window", AUTO,
     "    return min(started + cap, last_change + window)",
     "    return started + window  # mutated: the journey is interrupted as before"),
    ("in flight: the slide has no cap", AUTO,
     "    return min(started + cap, last_change + window)",
     "    return last_change + window  # mutated: wait as long as anything moves, ever"),
    ("in flight: every tool is treated as a crossing", AUTO,
     "    if tool != \"nav.goto\":\n"
     "        return started + window",
     "    if False:\n        return started + window  # mutated: reads pace forever too"),
    ("in flight: the cap is frozen to today's value instead of read", AUTO,
     "    cap = CROSSING_IN_FLIGHT_S if cap_s is None else cap_s",
     "    cap = 30.0 if cap_s is None else cap_s  # mutated: the constant is decoration"),
    ("in flight: the deadline is taken once and never moved", AUTO,
     "                    if extended > deadline:\n"
     "                        self._crossings_waited_out += 1\n"
     "                    deadline = extended",
     "                    if False:\n"
     "                        self._crossings_waited_out += 1\n"
     "                    deadline = deadline  # mutated: movement observed does not extend"),
    ("session end: no row ever names the reason", AUTO,
     "        if not getattr(self, \"_end_row_written\", False):\n"
     "            self._end_row_written = True",
     "        if False:\n            self._end_row_written = True  # mutated: stderr only"),
    ("session end: the row is written without the reason it exists for", AUTO,
     "                \"reason\": data[\"reason\"],\n"
     "                \"steps\": data[\"steps\"],",
     "                \"reason\": None,\n                \"steps\": data[\"steps\"],  # mutated: empty shell"),
    ("session end: an exit path goes straight back to the bare report", AUTO,
     "        return self._finish(ended_because)",
     "        return self.summary(ended_because)  # mutated: this end is not on the log"),
    # A visited-and-spent map is the record that stops the 2026-10-29 A->B->A bounce, and
    # it is keyed on the map the character stood on -- which for a one-map objective is
    # the map the objective happens on. Live 2026-11-05: that barred the return, the
    # active quest was retired and the session parked. The grant is the difference
    # between "bounded bounce" and "unreachable objective", so both halves must bite.
    ("return grant: no return is ever granted, the map stays barred", PLAN,
     "        if triple in self._return_granted:",
     "        if False:  # mutated: the granted lap never happens either"),
    ("return grant: the visit record is ignored entirely", PLAN,
     "        if triple not in getattr(self, \"_exhausted_at\", ()):\n"
     "            return False",
     "        if True:\n            return False  # mutated: marks answer nothing"),
    ("return grant: the mark survives its own grant", PLAN,
     "        self._exhausted_at.discard(triple)",
     "        pass  # mutated: the next ask reads the same mark and is barred"),
    ("return grant: the lap is granted without recording it", PLAN,
     "        self._return_granted.add(triple)\n"
     "        self._exhausted_at.discard(triple)",
     "        self._exhausted_at.discard(triple)  # mutated: a bounce is unbounded again"),
    ("return grant: the travel funnel ignores the visit record", PLAN,
     "            if travel_key is not None and self._visit_is_exhausted(travel_key, need):",
     "            if travel_key is not None and False:"),
    ("return grant: the ready scan ignores the visit record", PLAN,
     "                if self._visit_is_exhausted((quest_id, objective_id), need):",
     "                if False:"),
    ("return grant: the end-of-scenario reset keeps a spent grant", PLAN,
     "            self._exhausted_at.clear()\n            self._return_granted.clear()",
     "            self._exhausted_at.clear()  # mutated: the grant survives the scenario"),
    ("return grant: the new-step reset keeps a spent grant", PLAN,
     "                self._exhausted_at.clear()\n                self._return_granted.clear()",
     "                self._exhausted_at.clear()  # mutated: a spent grant outlives its gate"),
    ("return grant: a fork forgets which laps were spent", PLAN,
     "        twin._return_granted = set(getattr(self, \"_return_granted\", ()))",
     "        pass  # mutated: the fork grants a lap the parent already used"),
    # The failure budget on retiring the active quest (measured live: quest 4262 executed
    # on ONE unplannable pass, which is what ended that session with "nothing plannable").
    ("plan budget: the limit is ignored and any unplannable pass retires", PLAN,
     "        if tried >= limit:",
     "        if True:  # mutated: one bad step executes the quest"),
    ("plan budget: the situation is left out of the key", PLAN,
     "        tried = 1 if last != situation else tried + 1",
     "        tried = tried + 1  # mutated: every pass counts toward one verdict"),
    ("plan budget: a pass that plans does not refund the debt", PLAN,
     "                self._refund_plan_none(quest_id)",
     "                pass  # mutated: successes are forgotten, only failures accrue"),
    ("plan budget: the ready-scan site skips the budget entirely", PLAN,
     "            if self._plan_none_reaches_limit(quest_id, (objective_id, need)):",
     "            if True:  # mutated: this site is back to retiring on sight"),
    ("plan budget: a fork loses the failure count", PLAN,
     "        twin._plan_none = dict(getattr(self, \"_plan_none\", {}))",
     "        pass  # mutated: the fork starts with a clean slate the parent never had"),
    # The two rules the user's next run will be judged by have to be *counted*, or a
    # quiet log is indistinguishable from a working one.
    ("run counters: a waited-out walk is not counted", AUTO,
     "                        self._crossings_waited_out += 1",
     "                        pass  # mutated: the rule ran and the log cannot tell"),
    ("run counters: the end row reports no waits", AUTO,
     "                \"crossings_waited_out\": data[\"crossings_waited_out\"],",
     "                \"crossings_waited_out\": 0,  # mutated: a fixed zero, not a measure"),
    ("run counters: the granted returns are never read from the policy", AUTO,
     "            \"returns_granted\": len(getattr(policy, \"_return_granted\", ()) or ()),",
     "            \"returns_granted\": 0,  # mutated: the other rule is unreportable too"),
# ---- the play-quality census: the report that names where the steps went
    ("session-play: an advancing pair is counted as dead", SREPORT,
     '        if verdict == "advanced":',
     '        if verdict != "advanced":'),
    ("session-play: reads are counted as acting", SREPORT,
     '    reads = sum(1 for r in steps if r.get("tool") == "state.snapshot")',
     '    reads = sum(1 for r in steps if r.get("tool") != "state.snapshot")'),
    ("session-play: no objective divides instead of reporting unknown", SREPORT,
     "                                if objectives and objectives > 0 else None),",
     "                                if True else None),"),
    # ---- the page's own answer is the verdict (G84)
    ("session-play: the client's refusal is read as a success", SREPORT,
     '    if answer.get("ok") is True and not answer.get("error"):\n        return None\n',
     '    if True:\n        return None\n'),
    ("session-play: the ok/failed totals ignore the page's answer", SREPORT,
     '                      or r.get("verdict") in ("failed", "blocked")\n'
     '                      or _page_refusal(r)),',
     '                      or r.get("verdict") in ("failed", "blocked")\n'
     '                      ),'),
    ("session-play: a refusal is not named in the error census", SREPORT,
     '            error = str(refusal or row.get("error") or row.get("note")',
     '            error = str(row.get("error") or row.get("note")'),
    # ---- the stall budget: a quest retired forever is a loop wearing patience's name
    ("stall: the same quest is retired without limit", AUTO,
     "            self._retire_repeats += 1",
     "            self._retire_repeats += 0"),
    ("stall: an unretirable window never escalates", AUTO,
     "            if self._stall_unretired >= RETIRE_REPEAT_LIMIT:",
     "            if False:"),
    ("stall: the repeat limit is unreachable", AUTO,
     "RETIRE_REPEAT_LIMIT = 3",
     "RETIRE_REPEAT_LIMIT = 99999"),
    ("stall: the tutorial exemption never expires", AUTO,
     "                    elif now - self._tutorial_since > TUTORIAL_PATIENCE_S:",
     "                    elif False:"),
    # ---- cause taxonomy
    ("claim-refusal marker removed", CTRL,
     'or "perform the objective instead" in err',
     'or "zzz-mutated" in err'),
    ("observation fall-through removed", CTRL,
     '        if a:\n            return "stale_state"',
     '        if False:\n            return "stale_state"'),
    ("structural cause removed", CTRL,
     'if error_class(error) == "structural":\n            return "unavailable"',
     'if False:\n            return "unavailable"'),
    ("refusal prefix marker removed", CTRL,
     '    "refused: ",\n',
     ''),
    ("rule refusal routed to replan instead of the measured continue", CTRL,
     'if cause == "invalid_assumption" and error_class(error) != "refused":',
     'if cause == "invalid_assumption":'),
    ("game-level outcome dropped at the step site", AUTO,
     "ok=step_ok, error=step_error", "ok=None, error=None"),
    ("no-call decision emit removed", AUTO,
     "self._emit_decision(decision, ToolCall(",
     "self._emit_decision_removed(decision, ToolCall("),
    # ---- absent-client recovery
    ("recovery trigger lost", AUTO,
     'trigger="client_absent"', 'trigger="fell_out"'),
    ("recovery row no longer a recovery row", AUTO,
     '"kind": "recovery",', '"kind": "recovery_disabled",'),
    ("successful rescues not counted", AUTO,
     "self._recoveries_ok += 1", "self._recoveries_ok += 0"),
    ("the wait is skipped and the run just stops", AUTO,
     "if not step_ok and client_absent(step_error):",
     "if False and client_absent(step_error):"),
    ("the window check is dropped from the recovery", AUTO,
     'present = self._wait_for_window() if trigger == "client_absent" else True',
     "present = True"),
    ("an empty window count is read as a window", AUTO,
     "            if count:\n",
     "            if True:\n"),
    ("the presence read reports zero instead of unknown", BRIDGE,
     "    return count if isinstance(count, int) and count >= 0 else None",
     "    return 0"),
    ("a failed presence probe reports zero windows", BRIDGE,
     '    except Exception:   # noqa: BLE001 - any failure means "unknown", never "zero"\n'
     "        return None",
     '    except Exception:   # noqa: BLE001 - any failure means "unknown", never "zero"\n'
     "        return 0"),
    # ---- repair credit
    ("a read is allowed to certify a repair again", AUTO,
     'if str(getattr(call, "tool", "") or "") in READ_ONLY_TOOLS:',
     "if False:"),
    ("the pending decision never ages out", AUTO,
     "            self._pending_age += 1\n",
     "            self._pending_age += 0\n"),
    ("the credit is never delivered", AUTO,
     "        self.controller.observe_outcome(self._pending, verdict)",
     "        pass"),
    # ---- settle window
    ("every tool is treated as having no promise", AUTO,
     "promised = tool in SETTLE_TOOLS",
     "promised = False"),
    ("the quiet shortcut can end a promised walk again", AUTO,
     "                elif (not promised\n"
     "                      and changed_keys(before, best)",
     "                elif (changed_keys(before, best)"),
    ("the walk window is cut below the measured effect latency", AUTO,
     'window = max(window, PROMISED_SETTLE_WINDOW_S)',
     'window = 0.6'),
    # ---- idle waits are not steps
    ("reads are dispatched as steps again", AUTO,
     '                if (call is not None and call.tool == "state.snapshot"\n'
     "                        and self._consecutive_waits < MAX_CONSECUTIVE_WAITS):",
     '                if (False and call is not None\n'
     '                        and self._consecutive_waits < MAX_CONSECUTIVE_WAITS):'),
    ("the wait budget is removed, so a spin is invisible", AUTO,
     "MAX_CONSECUTIVE_WAITS = 40", "MAX_CONSECUTIVE_WAITS = 0"),
    ("a wait row is written as a step row", AUTO,
     '            "kind": "wait",', '            "kind": "wait_disabled",'),
    ("a step leaves the wait streak running", AUTO,
     '        self._consecutive_waits = 0\n        self._forced_reason = ""\n'
     '        if step.tool not in READ_ONLY_TOOLS and step.tool not in LISTING_TOOLS:',
     '        if step.tool not in READ_ONLY_TOOLS and step.tool not in LISTING_TOOLS:'),
    ("the wait paces faster than the loop it replaces", AUTO,
     "WAIT_POLL_S = 0.35", "WAIT_POLL_S = 0.0"),
    ("the wait ignores --interval", AUTO,
     "time.sleep(max(WAIT_POLL_S, self.interval))", "time.sleep(WAIT_POLL_S)"),
    ("a dry run sleeps on a wait with no world", AUTO,
     "if not self.dry_run:", "if True:"),
    ("a wait is charged to the goal's directive budget", AUTO,
     '                    if not self._hold_wait(goal, before):\n'
     '                        ended_because = "stopped while waiting"\n'
     "                        break\n"
     "                    taken -= 1  # waiting is not one of the goal's directives",
     '                    if not self._hold_wait(goal, before):\n'
     '                        ended_because = "stopped while waiting"\n'
     "                        break"),
    ("a silent fall-out is no longer a fall-out", AUTO,
     '    if before.get("in_world") is not False or reason:',
     '    if before.get("in_world") is not False and reason:'),
    ("the wait does not look for a kick", AUTO,
     "        left = fell_out_of_world(before, fresh)\n        if left is not None:",
     "        left = fell_out_of_world(before, fresh)\n        if False:"),
    ("the rescue from a wait is reported as a plain kick", AUTO,
     'trigger="wait_fell_out"', 'trigger="fell_out"'),
    ("a rescued world keeps the old wait budget", AUTO,
     "            if recovered:\n                # The count measured a wait inside a "
     "session that has ended; the new\n                # world gets its own budget.\n"
     "                self._consecutive_waits = 0",
     "            if recovered:\n                pass"),
    # ---- wait escalation and the goal boundary
    ("the escalation never happens", AUTO,
     "ESCALATE_TRIES = 3", "ESCALATE_TRIES = 0"),
    ("the escalation is not recorded in the wait row", AUTO,
     '"escalated": thought.tool if thought is not None else None',
     '"escalated": None'),
    ("a repair queued by one goal is dispatched under the next goal's label", AUTO,
     '            self._forced_call = None\n            self._forced_reason = ""\n'
     "            self._consecutive_waits = 0\n",
     ""),
    # Takes the read out of the escalation filter. The wait then accepts the layer's
    # own `state.snapshot` fallback, resets its budget forever and never dispatches --
    # the run hangs, which the driver counts as caught (at the cost of one timeout).
    ("a read is accepted as an escalation", AUTO,
     '            if candidate.tool in idle or candidate.tool == "-":',
     "            if True:"),
    # ---- dialog outcome labels
    ("the dialog row is not written as a dialog row", AUTO,
     '            "kind": "dialog",', '            "kind": "dialog_disabled",'),
    ("a stalled reply is reported as progress", AUTO,
     '            outcome = "same page"', '            outcome = "advanced"'),
    ("every step becomes a dialog row", AUTO,
     '        if chose is None and not args.get("close") and not accepted:\n            return',
     '        if False:\n            return'),
    ("a reply sent with no dialog open is attributed to one", AUTO,
     "        if not isinstance(db, dict) or not db.get(\"open\"):\n            return\n",
     ""),
    ("the pairing is never recorded", AUTO,
     "                self._record_dialog(call, before, after, result)\n", ""),
    # ---- G21: the dispatch half of the dialog label
    # Anchored on three lines: `data = getattr(result, "data", None)` alone appears five
    # times in this file, and the first draft of this mutation silently hit a helper two
    # thousand lines above the dialog row instead.
    ("the client's dispatch result is ignored", AUTO,
     '        data = getattr(result, "data", None)\n'
     '        data = data if isinstance(data, dict) else {}\n'
     "        self._dialogs += 1",
     "        data = {}\n        self._dialogs += 1"),
    ("the outcome does not say which read produced it", AUTO,
     '            "after_source": getattr(self, "_obs_source", "unknown"),\n', ""),
    ("every closed dialog is credited to us", AUTO,
     '                        else ("asked" if asked == "close" else "after_reply"))',
     '                        else "asked")'),
    ("a refused send reports itself as sent", AUTO,
     '            "dispatch_ok": bool(getattr(result, "ok", True)),',
     '            "dispatch_ok": True,'),
    ("the objective type is dropped from the row", AUTO,
     '            "objective_type": kn.get("type_id", tracker.get("type_id")),',
     '            "objective_type": None,'),
    ("the after-page is faked from the before-page", AUTO,
     '            "page_after": str(da.get("message") or "")[:120] if da.get("open") else None,',
     '            "page_after": str(db.get("message") or "")[:120] if da.get("open") else None,'),
    # ---- G22: the spent-page guard
    ("a repeat is never refused (the 87-ask loop returns)", PLAN,
     "DIALOG_ASK_LIMIT = 2", "DIALOG_ASK_LIMIT = 99"),
    ("the spent-page guard declines but still falls through to asking", PLAN,
     '                                             "reason": "reply already left this page unchanged"}\n'
     "                return None",
     '                                             "reason": "reply already left this page unchanged"}\n'
     "                pass"),
    ("the spent-page memo forgets which objective it was spent on", PLAN,
     "\n                   decision.get(\"objective_id\"))",
     "\n                   None)"),
    ("the spent-page memo forgets the page", PLAN,
     '            ask = (dialog.get("npc_id"), dialog.get("message"), reply["index"],',
     '            ask = (dialog.get("npc_id"), reply["index"], reply["index"],'),
    ("a fork restarts a spent loop", PLAN,
     "        twin._dialog_exhausted = set(self._dialog_exhausted)",
     "        twin._dialog_exhausted = set()"),
    # ---- G21/G22 as read by the report
    ("the report cannot say who ended the dialog", RECOVERY,
     '            if r.get("ended_by"):', "            if False:"),
    ("the report hides the re-ask NOTE", RECOVERY,
     "        worst = [(n, k) for k, n in dlg_repeat.most_common(3) if n > 1]",
     "        worst = []"),
    ("a refused send is invisible in the report", RECOVERY,
     '            if not r.get("dispatch_ok", True):', "            if False:"),
    # ---- G24: the combat brain autostart
    # Half of these exist because the module's whole job is to distinguish "the
    # trained policy decided that turn" from "nobody was listening". A state that
    # collapses into another one is the failure mode that would flatter a run.
    ("a process that never says ok is treated as the brain", BRAIN,
     '    if not isinstance(body, dict) or body.get("status") != "ok":\n        return None',
     '    if not isinstance(body, dict):\n        return None'),
    ("the health probe dials a hardcoded port instead of the one it was given", BRAIN,
     '    target = "http://127.0.0.1:%d/health" % int(port)',
     '    target = "http://127.0.0.1:%d/health" % BRAIN_PORT'),
    ("one interpreter spelled two ways is probed twice", BRAIN,
     "        if key in seen:\n            continue",
     "        if False:\n            continue"),
    ("a dry run starts a service anyway", BRAIN,
     '    if dry_run:\n        return "dry_run"',
     '    if False:\n        return "dry_run"'),
    ("a port held by a stranger goes unreported", BRAIN,
     '    if open_port(port) and not model:\n        return "port_held"',
     '    if False:\n        return "port_held"'),
    ("no capable interpreter becomes a retry loop", BRAIN,
     '    exe = pick()\n    if not exe:',
     '    exe = pick()\n    if False:'),
    ("a spawn that failed is reported as started", BRAIN,
     '    if not record.get("spawned"):\n        return "spawn_failed"',
     '    if False:\n        return "spawn_failed"'),
    ("readiness is assumed instead of waited for", BRAIN,
     '        if model:\n            record["model"] = model\n            return "started", record',
     '        if model:\n            record["model"] = model\n            pass'),
    ("the synthetic gate loop would start services on the machine", AUTO,
     '        if self.brain_mode == "off" or not self.preflight:\n            return',
     '        if self.brain_mode == "off":\n            return'),
    ("--brain off is ignored and the probe runs anyway", AUTO,
     '        if self.brain_mode == "off" or not self.preflight:',
     '        if self.brain_mode == "zzz" or not self.preflight:'),
    ("a brain that answered gets restarted at every fight", AUTO,
     '        if self._brain.get("state") in self.BRAIN_FINAL_STATES:\n            return',
     '        if False:\n            return'),
    ("the brain is probed on every step instead of on a cadence", AUTO,
     '        if now - self._brain_try_at < self.brain_retry_s:\n            return',
     '        if False:\n            return'),
    ("the probe is kept in memory but never written to the log", AUTO,
     "        self._brain = row\n        self._append_log_line(_fast_dumps(row))",
     "        self._brain = row"),
    ("the probe is printed but the run keeps no record of it", AUTO,
     "        self._brain = row\n        self._append_log_line(_fast_dumps(row))",
     "        self._append_log_line(_fast_dumps(row))"),
    ("the interpreter command is dropped, so a wrong python is unauditable", AUTO,
     '        for key in ("model", "pid", "cmd", "reason", "waited_s"):',
     '        for key in ("model", "pid", "reason", "waited_s"):'),
    ("the row cannot say when the probe happened", AUTO,
     '                               "when": reason, "mode": self.brain_mode,',
     '                               "when": "startup", "mode": self.brain_mode,'),
    ("the summary loses which policy was available for this run", AUTO,
     '            "brain": {key: self._brain.get(key)',
     '            "brain_unused": {key: self._brain.get(key)'),
    ("the loop never ensures the brain before the first step", AUTO,
     "            self._ensure_brain()\n", ""),
    ("a fight that opens with the brain down never asks again", AUTO,
     '            self._ensure_brain("fight_start")\n', ""),
    # ---- G25: the readiness reader
    ("an absent field and a null field become the same defect", READINESS,
     '            elif field not in me:\n                census[f"{field}: key absent"] += 1',
     '            elif field not in me:\n                census[f"{field}: null"] += 1'),
    ("the contract computed at capture time is thrown away", READINESS,
     '    if isinstance(recorded, dict) and "value_ready" in recorded:\n'
     '        return recorded, "recorded"',
     '    if False:\n        return recorded, "recorded"'),
    ("an empty corpus is reported as a clean bill of health", READINESS,
     '    if not rows:\n        print("VERDICT: no recorded boards',
     '    if False:\n        print("VERDICT: no recorded boards'),
    ("a partly-fixed corpus still reads as a total failure", READINESS,
     "    elif unusable:", "    elif False:"),
    ("the reader stops saying where each contract came from", READINESS,
     "        contract, source = _contract(board)\n        sourced[source] += 1",
     "        contract, source = _contract(board)"),
    ("the field census drops initiative, which is the one key the page never emits", READINESS,
     'REQUIRED = ("max_hp", "ap", "max_ap", "mp", "max_mp", "initiative")',
     'REQUIRED = ("max_hp", "ap", "max_ap", "mp", "max_mp")'),
    ("the fight count is taken from a field the board does not have", READINESS,
     '        fights.add(board.get("fight_id"))',
     '        fights.add(board.get("map_id"))'),
    # ---- G25: the recorded-board replay harness
    # Its whole job is a labelled hypothesis, so the mutations attack the label and the
    # copy: an experiment that rewrites the log, or forgets to say what it assumed, is
    # the shape that later gets read as a measurement.
    ("the fill rewrites the recorded board instead of a copy", BOARDS,
     "    out = json.loads(json.dumps(board))", "    out = board"),
    ("the self max_ap is filled from the movement stat", BOARDS,
     'fighter["max_ap"] = stats.get("action_points")',
     'fighter["max_ap"] = stats.get("movement_points")'),
    ("an enemy's max hp is never inferred, so nothing becomes actionable", BOARDS,
     '        if fighter.get("max_hp") is None:\n            fighter["max_hp"] = fighter.get("hp")',
     '        if False:\n            fighter["max_hp"] = fighter.get("hp")'),
    ("a policy that raises disappears from the report", BOARDS,
     '            errors["%s: %s" % (type(exc).__name__, str(exc)[:70])] += 1\n            continue',
     "            continue"),
    ("the replay asks turns that are not the character's", BOARDS,
     '        if only_my_turn and not board.get("is_my_turn"):',
     "        if False:"),
    ("percentiles are read off the unsorted latencies", BOARDS,
     "    latencies.sort()\n", ""),
    ("an empty corpus is reported as a result", BOARDS,
     '    if not rows:\n        print("VERDICT: nothing recorded',
     '    if False:\n        print("VERDICT: nothing recorded'),
    ("the fill hypothesis is never printed", BOARDS,
     '    print("fill hypothesis:", ASSUMPTION)\n', ""),
    # ---- G25: the producer itself, in the live page.
    # These are the shapes that made 156 of 156 recorded boards unusable, written back in
    # one at a time. If the gate stays green with any of them installed, the suite that
    # claims to pin the field names does not actually read them.
    ("the fighter max hp is read off the characteristics name again", PAGE,
     "                            max_hp: num(stats.effectiveMaxLifePoints != null\n"
     "                                ? stats.effectiveMaxLifePoints : stats.maxLifePoints),",
     "                            max_hp: num(stats.maxLifePoints),"),
    ("initiative leaves the payload, so the board is unordered again", PAGE,
     "                            initiative: num(stats.initiative),\n", ""),
    ("a spent turn becomes unknown AP, because 0 is falsy", PAGE,
     "                            ap: num(stats.actionPoints != null\n"
     "                                ? stats.actionPoints : stats.actionPointsCurrent),",
     "                            ap: num(stats.actionPoints || stats.actionPointsCurrent),"),
    ("an invented field name is back as a fallback", PAGE,
     "                            max_ap: num(stats.maxActionPoints),",
     "                            max_ap: num(stats.maxActionPoints || stats.actionPointsMax),"),
    ("the stat-key capture stops re-sampling, so a second fight reports the first one", PAGE,
     "                            if (!cache || cache.fight !== state.fight_id) {",
     "                            if (!cache) {"),
    ("the fighter data bag stops being sampled, so the monster id stays a mystery", PAGE,
     "                        state.data_keys = sampleFightKeys('__agentFightDataKeys', data);",
     "                        state.data_keys = state.stat_keys;"),
    # ---- G7: the monster-group census. An empty `groups` list is what the planner is told
    # when it asks to join a fight, and 4,782 recorded observations all said the same empty
    # thing without ever saying which of the three causes it was.
    ("the census counts only the groups, so a broken filter still looks like an empty map",
     PAGE, "                scan.seen += 1;\n", ""),
    ("the census is never published, so the page keeps the answer to itself", PAGE,
     "            window.__agentGroupScan = scan;\n", ""),
    ("the snapshot drops the census, leaving only the empty list behind", PAGE,
     "                group_count: allGroups.length,\n"
     "                groups_scan: window.__agentGroupScan || null",
     "                group_count: allGroups.length"),
    # ---- G7: one membership read and one group search. Three handlers used to answer "who is
    # in this group" for themselves, and the two that decide whether a fight is reachable matched
    # against the reference-database name. These write each old shape back, one at a time.
    ("membership: the search enumerates the reference database again", PAGE,
     "                var members = _groupMembers(a);",
     "                var members = (a.data && a.data.staticInfos\n"
     "                    && a.data.staticInfos.monsters) || [];"),
    ("membership: farm.start walks the actors itself again", PAGE,
     "                    var hunt = _findMonsterGroup(wanted);",
     "                    var hunt = {group: null, groups: 0, monsters: [], path: 'actors'};"),
    ("membership: fight.join keeps a private group walk", PAGE,
     "                    var found = _findMonsterGroup(wantMonster);",
     "                    var found = {group: null, key: null, groups: 0,\n"
     "                        monsters: [], path: 'actors'};"),
    ("membership: an unnamed objective no longer takes the first group", PAGE,
     "                if (!out.group && wanted == null) { out.group = a; out.key = k; }\n", ""),
    ("membership: a string monster id stops matching the numeric one", PAGE,
     "                    if (!out.group && wanted != null && idn === Number(wanted)) {",
     "                    if (!out.group && wanted != null && mid === wanted) {"),
    ("membership: the group's own generic id stops counting as a member", PAGE,
     "            if (!members.length && d.creatureGenericId != null) {\n"
     "                members = [{creatureGenericId: d.creatureGenericId, level: d.level}];\n"
     "            }\n", ""),
    ("membership: the NPC reader falls back to a name that is not in the bundle", PAGE,
     "var _listNpcActors = function () {\n"
     "            var am = window.actorManager\n"
     "                || (window.isoEngine && window.isoEngine.actorManager) || null;\n"
     "            var actors = (am && am.actors) || null;",
     "var _listNpcActors = function () {\n"
     "            var am = window.actorManager\n"
     "                || (window.isoEngine && window.isoEngine.actorManager) || null;\n"
     "            var actors = (am && (am.actors || am.actorList)) || null;"),
    ("membership: the group search falls back to a name that is not in the bundle", PAGE,
     "var _findMonsterGroup = function (wanted) {\n"
     "            var am = window.actorManager\n"
     "                || (window.isoEngine && window.isoEngine.actorManager) || null;\n"
     "            var actors = (am && am.actors) || null;",
     "var _findMonsterGroup = function (wanted) {\n"
     "            var am = window.actorManager\n"
     "                || (window.isoEngine && window.isoEngine.actorManager) || null;\n"
     "            var actors = (am && (am.actors || am.actorList)) || null;"),
    ("membership: the group listing falls back to a name that is not in the bundle", PAGE,
     "var _listMonsterGroups = function () {\n"
     "            var am = window.actorManager\n"
     "                || (window.isoEngine && window.isoEngine.actorManager) || null;\n"
     "            var actors = (am && am.actors) || null;",
     "var _listMonsterGroups = function () {\n"
     "            var am = window.actorManager\n"
     "                || (window.isoEngine && window.isoEngine.actorManager) || null;\n"
     "            var actors = (am && (am.actors || am.actorList)) || null;"),
    ("membership: a manager with no collection stops being its own cause", SCAN,
     "    no_collection = [m for m in scanned if m[\"groups_scan\"].get(\"manager\")\n"
     "                     and m[\"groups_scan\"].get(\"path\") in (None, \"none\")]",
     "    no_collection = []"),
    # ---- G7: the reader that turns the census into one of three answers.
    ("a corpus with no census is read as an empty world", SCAN,
     "    if not scanned:\n        if unread:\n",
     "    if False:\n        if unread:\n"),
    ("the actor type names never reach the verdict, so the fix is still a guess", SCAN,
     "    top = \", \".join(\"%s x%d\" % (k, v) for k, v in names.most_common(8))",
     "    top = \"none\""),
    ("one matched group does not certify the filter", SCAN,
     "    if matched:\n        return (\"VERDICT: the scan found",
     "    if False:\n        return (\"VERDICT: the scan found"),
    ("an unanswerable corpus exits successfully, so a caller reads it as a result", SCAN,
     "    return 0 if answered else 1", "    return 0"),
    # ---- G2: the equip read. The stall is a page the loop cannot get past because it has no
    # equip verb; this read is the verb's prerequisite, so its failures are the interesting ones.
    ("the preset id is dropped, leaving the verb with nothing to send", PAGE,
     "                                preset_id: pid != null ? Number(pid)\n"
     "                                    : (preset.presetId != null ? Number(preset.presetId) : null),",
     "                                preset_id: null,"),
    ("the per-mode inventory is never followed, so the read sees an empty manager", PAGE,
     "                    if (bag.equippedItems == null && bag.presets == null\n"
     "                            && typeof bag.getCorrectInventory === 'function') {",
     "                    if (false) {"),
    ("the client's preset guard is reported as free whatever it says", PAGE,
     "                        snap.inventory.preset_blocked = !!(bag && typeof bag.blockPreset === 'function'\n"
     "                            && bag.blockPreset());",
     "                        snap.inventory.preset_blocked = false;"),
    ("a worn item's gid is invented from its slot number", PAGE,
     "                                gid: worn && worn.objectGID != null ? Number(worn.objectGID) : null",
     "                                gid: slotNum"),
    # ---- G7: reading a group refusal. The page's counts separate "the mob is elsewhere" from
    # "groups are here and their members could not be read"; only the first one may travel.
    ("refusal: unread members are routed like an absent mob", PLAN,
     '                self._group_read_broken = outcome == "unread_members"',
     "                self._group_read_broken = False"),
    ("refusal: an empty map is no longer told apart from an unread one", PLAN,
     '    if seen <= 0:\n        return "empty_map"', '    if seen <= 0:\n        return "unread_members"'),
    ("refusal: a success leaves the broken read latched", PLAN,
     "                self._farm_target_missing = False\n"
     "                self._group_read_broken = False\n",
     "                self._farm_target_missing = False\n"),
    ("refusal: the payload never reaches the policy", AUTO,
     '                            reporter(*reported, getattr(result, "data", None))',
     "                            reporter(*reported)"),
    ("refusal: the run summary stops carrying the refusal census", AUTO,
     '            "group_refusals": dict(getattr(policy, "_group_refusals", {}) or {}),\n',
     ""),
    ("refusal: the bridge envelope is never unwrapped", PLAN,
     "    data = refusal_payload(detail)",
     "    data = detail if isinstance(detail, dict) else {}"),
    ("refusal: a broken member read keeps the wrong-target label", CTRL,
     '            if group_refusal_outcome(detail, error) == "unread_members":\n'
     '                return "stale_state"',
     "            if False:\n"
     '                return "stale_state"'),
    ("refusal: the page's join wording falls back to unknown", CTRL,
     '                                  "no group on this map",\n', ""),
    ("refusal: the decision site stops forwarding the payload", AUTO,
     '                                                   before, after,\n'
     '                                                   detail=getattr(result, "data", None),',
     '                                                   before, after,'),
    ("refusal: the reader stops counting the loop's group refusals", SCAN,
     "    refusals = _refusals(paths)", "    refusals = []"),
    ("refusal: the agent log is left out of the default reading", SCAN,
     "    if not args.paths and AGENT_LOG is not None and AGENT_LOG.exists():\n"
     "        paths.append(AGENT_LOG)\n", ""),
    ("refusal: any failed call is counted as a group refusal", SCAN,
     '            if (row.get("kind") == "decision" and row.get("tool") in GROUP_REFUSAL_TOOLS\n'
     '                    and row.get("ok") is False):',
     '            if (row.get("kind") == "decision"\n'
     '                    and row.get("ok") is False):'),
    # ---- G17 family: the scripted-fight NPC fallback. 259 of 287 live steps after a claim
    # refusal were the same rotation of the same four NPCs; the bound and its memory are the fix.
    ("rotation: the memory of who was asked is dropped", PLAN,
     "        asked = self._npc_asked.setdefault(key, set())", "        asked = set()"),
    ("rotation: the NPC just asked is not recorded", PLAN,
     '        asked.add(int(npc["npc_id"]))\n', ""),
    ("rotation: the fork loses the asked memory", PLAN,
     "        twin._npc_asked = {key: set(value)\n"
     "                           for key, value in self._npc_asked.items()}\n", ""),
    ("rotation: the scene flap wipes the asked memory again", PLAN,
     "            self._start_waits.clear()\n",
     "            self._start_waits.clear()\n            self._npc_asked.clear()\n"),
    # ---- the page's own fight budget. `_localTick` is the policy that has played every live
    # turn so far, so these are defects that cost real turns.
    ("the budget prefers the character sheet over the fighter's own bag", PAGE,
     "                if (fighterBag && fighterBag.actionPoints != null) {\n"
     "                    ap = Number(fighterBag.actionPoints); apFrom = 'fighter';\n"
     "                } else if (stats.actionPointsCurrent != null) {",
     "                if (stats.actionPointsCurrent != null) {"),
    ("a turn with zero AP left reads as missing and gets the assumed six", PAGE,
     "                if (fighterBag && fighterBag.actionPoints != null) {",
     "                if (fighterBag && fighterBag.actionPoints) {"),
    ("the candidate loop offers spells the turn cannot pay for", PAGE,
     "                    if (ctx.ap_source !== 'assumed' && cost > ctx.ap) continue;\n", ""),
    ("an assumption is labelled as a reading, so nothing distinguishes them", PAGE,
     "                if (ap == null || isNaN(ap)) { ap = 6; apFrom = 'assumed'; }",
     "                if (ap == null || isNaN(ap)) { ap = 6; apFrom = 'fighter'; }"),
    ("the suite stops reading the contract, so the two sides can drift apart", PAGE_TEST,
     "const requiredMatch = readiness.match(/^REQUIRED = \\(([^)]*)\\)/m);",
     "const requiredMatch = readiness.match(/^NEVER_APPEARS = \\(([^)]*)\\)/m);"),
    # ---- G7/G17 family: the ready-objective scan. The live log had `objectives_completed` 0 in
    # all 4,286 rows while 4,267 of them carried a second ready objective the loop never read.
    ("ready: the claim loop is back to one ready entry", PLAN,
     "READY_SCAN_LIMIT = 8", "READY_SCAN_LIMIT = 1"),
    ("ready: a detour plan is never deferred", PLAN,
     "                if not names_target(first):\n", "                if False:\n"),
    ("ready: a read counts as naming the target", PLAN,
     "    if tool in READ_ONLY_TOOLS:\n        return False",
     "    if tool in READ_ONLY_TOOLS:\n        return True"),
    ("ready: walking out an exit counts as a destination", PLAN,
     '    if tool == "nav.goto":\n        return args.get("map_id") is not None',
     '    if tool == "nav.goto":\n        return True'),
    ("ready: a validation request is no longer recognised as a target", PLAN,
     '    if tool == "quest.advance":\n        return (args.get("npc_id") is not None\n'
     '                or args.get("objective_id") is not None)',
     '    if tool == "quest.advance":\n        return args.get("npc_id") is not None'),
    ("ready: a retired head ends the scan instead of moving on", PLAN,
     "            if quest_id in self._skipped:\n                continue\n"
     "            obj = self._lookup_objective(quest_id, objective_id, item)",
     "            if quest_id in self._skipped:\n                break\n"
     "            obj = self._lookup_objective(quest_id, objective_id, item)"),
    ("ready: the deferred wait is thrown away", PLAN,
     "        if detour is not None:\n", "        if False:\n"),
    # ---- the report's own census of the same defect: `objectives +0` has to say whether the
    # world offered nothing or the loop never looked past the first ready objective.
    ("census: a ready step is not counted", SREPORT,
     "            ready_steps += 1", "            ready_steps = 0"),
    ("census: the entries waiting behind the head are not counted", SREPORT,
     "            if len(ready) > 1:\n                ready_behind += 1",
     "            if len(ready) > 99:\n                ready_behind += 1"),
    ("census: the head of the list is not identified", SREPORT,
     '            ready_heads[str(head.get("text") or head.get("objective_id") or "?")]'
     " += 1\n", ""),
    ("census: an empty ready list still counts as a ready step", SREPORT,
     "        if isinstance(ready, list) and ready:",
     "        if isinstance(ready, list):"),
    ("census: a malformed ready entry crashes the report", SREPORT,
     "            head = ready[0] if isinstance(ready[0], dict) else {}",
     "            head = ready[0]"),
    ("census: starvation is asserted even when the progress counter was never read", SREPORT,
     '                and report["objectives_measured"]):', "                ):"),
    ("census: the starvation line is removed", SREPORT,
     '        if (report["objectives_completed"] == 0 and report["ready_steps"]',
     '        if False and (report["objectives_completed"] == 0 and report["ready_steps"]'),
    # ---- G28: the progress read. `finishedCount` is a real bundle name that is only ever
    # written on a quest being moved into the finished list, so a bundle-membership guard cannot
    # catch reading it on an active quest -- these mutations are what keep that honest.
    ("progress: the step position is off by one", PAGE,
     "                            if (at > 0) out.index = at;",
     "                            if (at >= 0) out.index = at + 1;"),
    ("progress: the quest's own step table is never consulted", PAGE,
     "                        var sids = q && q.dbQuest && q.dbQuest.stepIds;",
     "                        var sids = null;"),
    ("progress: a string step id is compared without coercion", PAGE,
     "                        if (sid != null && !isNaN(Number(sid))) out.step_id = Number(sid);",
     "                        if (sid != null && !isNaN(Number(sid))) out.step_id = sid;"),
    ("progress: a quest object without a step stops the read", PAGE,
     "                    var out = {index: 0, step_id: 0};\n                    try {",
     "                    var out = {index: 0, step_id: 0};\n                    if (true) {"),
    ("progress: the per-quest value goes back to the field the client never sets", PAGE,
     "                            qs.active[qid] = prog.index;",
     "                            qs.active[qid] = Number(q.finishedCount || 0);"),
    ("progress: the session counter stops adding the real position", PAGE,
     "                            qs.completed += prog.index;",
     "                            qs.completed += 0;"),
    ("progress: the raw step id is not emitted, so a bad derivation is invisible", PAGE,
     "                snap.quest_steps = qs.steps;\n", ""),
    ("progress: the finished count is emitted as a constant", PAGE,
     "                            qs.finished = fq.length;", "                            qs.finished = 0;"),
    ("progress: the finished-quest line disappears", PAGE,
     "                snap.quests_finished = qs.finished;\n", ""),
    ("progress: the planner only ever believes a tracker on step zero", PLAN,
     '                and int(tracker.get("step", -1)) == int(step_idx):',
     '                and int(tracker.get("step", -1)) == 0:'),
    ("progress: a finished quest is not seen as the world moving", VERIF,
     '              "quests_active", "quests_finished", "quest_steps")',
     '              "quests_active")'),
    ("progress: the report stops counting finished quests", SREPORT,
     '        print("  objectives %+d, quests completed %+d, maps %d, manual interventions %d, "\n'
     '              "recoveries %s"\n'
     '              % (report["objectives_completed"], report["quests_completed"],\n',
     '        print("  objectives %+d, maps %d, manual interventions %d, "\n'
     '              "recoveries %s"\n'
     '              % (report["objectives_completed"],\n'),
    # ---- G27: the client's declared objective-parameter table decides targeting
    ("param: the free-form class declares a monster", FEATURES,
     '    "QuestObjectiveFreeForm": (("getText", "GetText"),),',
     '    "QuestObjectiveFreeForm": (("table", "Monsters"),),'),
    ("param: the discover-map class declares a monster", FEATURES,
     '    "QuestObjectiveDiscoverMap": (("getText", "GetText"),),',
     '    "QuestObjectiveDiscoverMap": (("table", "Monsters"),),'),
    ("param: the fight class declares no monster", FEATURES,
     '    "QuestObjectiveFightMonster": (("table", "Monsters"),),',
     '    "QuestObjectiveFightMonster": (("getText", "GetText"),),'),
    ("param: the monster set is hand-written, not derived", FEATURES,
     'MONSTER_PARAMETER_TYPES = frozenset(\n'
     '    name for name, params in OBJECTIVE_PARAMETERS.items()\n'
     '    if any(pid == "Monsters" for _kind, pid in params))',
     'MONSTER_PARAMETER_TYPES = frozenset({\n'
     '    "QuestObjectiveFightMonster", "QuestObjectiveFreeForm"})'),
    ("param: an objective with no class name reads as text-only", FEATURES,
     '        return name in MONSTER_PARAMETER_TYPES\n    return None',
     '        return name in MONSTER_PARAMETER_TYPES\n    return False'),
    ("param: the class name is never consulted", FEATURES,
     '    if isinstance(name, str) and name in OBJECTIVE_PARAMETERS:',
     '    if False:'),
    ("param: a class the client does not declare is added to the copy", FEATURES,
     '    "QuestObjective": (("getText", "GetText"),),',
     '    "QuestObjectiveBase": (("getText", "GetText"),),'),
    ("param: the npc class gains a parameter the client does not declare", FEATURES,
     '    "QuestObjectiveGoToNpc": (("table", "Npcs"),),',
     '    "QuestObjectiveGoToNpc": (("table", "Npcs"), ("table", "Monsters")),',),
    ("param: the kill branch still hunts a text-only objective", PLAN,
     '        if t in KILL_TYPES:\n            if declares_monster_target(obj) is False:',
     '        if t in KILL_TYPES:\n            if False:'),
    ("param: fight.join keeps the invented monster", PLAN,
     '        if declares_monster_target(obj) is False:\n'
     '            return {}\n'
     '        monster = p.get("parameter0")\n'
     '        try:\n'
     '            return {"monster": int(monster)} if monster is not None else {}\n'
     '        except (TypeError, ValueError):\n'
     '            return {}\n'
     '    return {}',
     '        if False:\n'
     '            return {}\n'
     '        monster = p.get("parameter0")\n'
     '        try:\n'
     '            return {"monster": int(monster)} if monster is not None else {}\n'
     '        except (TypeError, ValueError):\n'
     '            return {}\n'
     '    return {}'),
    ("param: farm.start keeps the invented monster", PLAN,
     '            except (TypeError, ValueError):\n'
     '                return {}\n'
     '        if declares_monster_target(obj) is False:\n'
     '            return {}\n'
     '        monster = p.get("parameter0")',
     '            except (TypeError, ValueError):\n'
     '                return {}\n'
     '        if False:\n'
     '            return {}\n'
     '        monster = p.get("parameter0")'),
    ("param: the gate also blocks the deliver route", PLAN,
     '    if tool == "farm.start":\n        if t in DELIVER_TYPES:',
     '    if tool == "farm.start":\n'
     '        if declares_monster_target(obj) is False:\n'
     '            return {}\n'
     '        if t in DELIVER_TYPES:'),
    ("param: the learned head's empty hunt is sent to the client", NNPLAN,
     '        if not args and tool in ("quest.advance", "fight.join", "farm.start"):',
     '        if not args and tool == "quest.advance":'),
    # The two lines that decide whether the table above is ever read on live data: the
    # KB holds no live quest id, so the observation fallback is the only route a real
    # quest takes, and a class name lost there or in the merge makes every check above
    # pass while the loop still hunts an invented monster.
    ("param: the class name is dropped from the observation fallback", PLAN,
     '        if isinstance(entry.get("type"), str) and entry.get("type"):\n'
     '            obj["type"] = entry["type"]',
     '        if False:\n            obj["type"] = entry["type"]'),
    ("param: the knowledge-base merge drops the class name", PLAN,
     '                        for key in ("type_id", "map_id", "type"):',
     '                        for key in ("type_id", "map_id"):'),
    # ---- G30: the knowledge bridge publishes only what the client's identity confirms
    ("identity: a collision is published as a resolution", KCTX,
     '                "status": "objective_id_collision",',
     '                "status": "resolved",'),
    ("identity: the guard never fires", KCTX,
     '        if disagrees:',
     '        if False:'),
    ("identity: the store's quest id replaces the tracker's", KCTX,
     '                "store_quest_id": store_quest_id,',
     '                "quest_id": store_quest_id,'),
    ("identity: the store's class is dropped from the diagnostic row", KCTX,
     '                "store_type_id": resolved.get("type_id"),',
     '                "store_type_id": None,'),
    ("identity: the foreign objective text leaks through", KCTX,
     '                "store_type_id": resolved.get("type_id"),',
     '                "store_type_id": resolved.get("type_id"),\n'
     '                "objective": {"text": resolved.get("text")},'),
    ("identity: every store hit is treated as a collision", KCTX,
     '            disagrees = int(store_quest_id) != int(tracker_quest_id)',
     '            disagrees = int(store_quest_id) >= 0'),
    ("identity: the tracker's quest id never reaches the block", KCTX,
     '    tracker_quest_id = tracker.get("quest_id")',
     '    tracker_quest_id = None'),
    ("identity: the cached block is keyed for one fixed quest", KCTX,
     '    block = _knowledge_block(store, objective_id, language, tracker_quest_id)',
     '    block = _knowledge_block(store, objective_id, language, 4260)'),
    ("identity: the dialog row loses the tracker's own class", AUTO,
     '"objective_type": kn.get("type_id", tracker.get("type_id")),',
     '"objective_type": kn.get("type_id"),'),
    # ---- G31: a quest start is only sendable for a quest the running client reported
    ("start: the client-confirmed guard is removed", PLAN,
     '                if qid not in self._client_quests:',
     '                if False:'),
    ("start: the guard also refuses client-confirmed quests", PLAN,
     '                if qid not in self._client_quests:',
     '                if True:'),
    ("start: the memory is never grown from the observation", PLAN,
     '        self._client_quests |= client_quest_ids(obs)',
     '        pass'),
    ("start: the memory is filled from the cached table instead of the client", PLAN,
     '        self._client_quests |= client_quest_ids(obs)',
     '        self._client_quests |= set(self.quests)'),
    ("start: a tracker or ready entry no longer confirms its quest", PLAN,
     '    sources = [obs.get("current_objective")]',
     '    sources = []'),
    ("start: a string quest id is kept as a string, so it never matches", PLAN,
     '            out.add(int(raw))',
     '            out.add(raw)'),
    ("start: the refusal does not retire the quest, so it is re-derived every step", PLAN,
     '                    self._skipped.add(qid)\n                    continue\n'
     '                if self._start_failures.get(qid, 0) >= START_ATTEMPT_LIMIT:',
     '                    continue\n'
     '                if self._start_failures.get(qid, 0) >= START_ATTEMPT_LIMIT:'),
    ("start: a fork loses the confirmed quests and re-asks them", PLAN,
     '        twin._client_quests = set(self._client_quests)',
     '        twin._client_quests = set()'),
    # ---- G32: the dialog LINK read (a quest offer is an anchor in the NPC's text)
    ("links: the anchor pattern is dropped", PAGE,
     "var anchor = /<a\\shref=['\"](.*?)['\"]\\s*>(.*?)<\\/a>/gi;",
     "var anchor = /<a-nomatch-zzz>/gi;"),
    ("links: a quest offer is not recognised", PAGE,
     "if (kind === 'startquest') {",
     "if (kind === 'zzz-startquest') {"),
    ("links: the quest id is published as a string", PAGE,
     "out.push({kind: kind, quest_id: Number(parts[1])});",
     "out.push({kind: kind, quest_id: parts[1]});"),
    ("links: coordinates stop being truncated the client's way", PAGE,
     "out.push({kind: kind, world_x: ~~parts[1], world_y: ~~parts[2]});",
     "out.push({kind: kind, world_x: Number(parts[1]), world_y: Number(parts[2])});"),
    ("links: reply text is never scanned for links", PAGE,
     "for (var i = 0; i < texts.length && i < 12; i++) collect(texts[i], 'reply', i);",
     "for (var i = 0; i < 0; i++) collect(texts[i], 'reply', i);"),
    ("links: the raw question text is not kept for the parse", PAGE,
     "raw_msg: (ui && ui.currentMsg != null) ? ui.currentMsg : null,",
     "raw_msg: null,"),
    ("links: the snapshot stops publishing the offer", PAGE,
     "start_quest: _agentStartQuestOffered(links),",
     "start_quest: null,"),
    ("links: the handler owner is guessed instead of detected", PAGE,
     "if (info.ui && typeof info.ui._linkHandler === 'function') return 'gui.npcDialogUi';",
     "if (info.ui) return 'gui.npcDialogUi';"),
    ("links: the read starts sending on its own", PAGE,
     "        var _agentStartQuestOffered = function (links) {",
     "        window.dofus.sendMessage('QuestStartRequestMessage', {questId: 1});\n"
     "        var _agentStartQuestOffered = function (links) {"),

    # ---- call-shape classifier (G31's number was wrong because one argument decided it)
    ("shape: a talk is counted as an accept", SHAPES,
     '    if args.get("npc_id") is not None:\n        return "talk"',
     '    if args.get("npc_id") is not None:\n        return "start"'),
    ("shape: a claim stops being the most specific shape", SHAPES,
     '    if args.get("objective_id") is not None:\n        return "claim"',
     '    if False:\n        return "claim"'),
    ("shape: a reply named by text is counted as a start", SHAPES,
     '    if args.get("reply") is not None or args.get("reply_text") is not None:',
     '    if args.get("reply") is not None:'),
    ("shape: a close is counted as a start", SHAPES,
     '    if args.get("close"):\n        return "close"',
     '    if False:\n        return "close"'),
    ("shape: a call with no quest id is still counted", SHAPES,
     '    if args.get("quest_id") is None:\n        return "other"',
     '    if False:\n        return "other"'),
    ("shape: reported-ness stops consulting the client's own rule", SHAPES,
     "    return client_quest_ids(obs)",
     "    return set()"),
    ("shape: the honour window is ignored", SHAPES,
     "            for later in items[index + 1:index + 1 + window]:",
     "            for later in items[index + 1:]:"),
    ("shape: the flat in-dialog flag is not read", SHAPES,
     '    if obs.get("in_dialog") is True:\n        return True',
     '    if False:\n        return True'),
    ("shape: the nested dialog block is not read", SHAPES,
     '    return isinstance(dialog, dict) and bool(dialog.get("open"))',
     "    return False"),
    ("shape: the page's own stage is never read out", SHAPES,
     '    for key in ("via", "stage", "reason"):',
     "    for key in ():"),
    ("shape: an unanswered row is reported as accepted", SHAPES,
     '    return ok, "silent"',
     '    return ok, "accepted"'),
    ("shape: a transport error is dropped from the row", SHAPES,
     '    if row.get("error"):\n        return ok, "error: "',
     '    if False:\n        return ok, "error: "'),
    # ---- G33: accepting an offered quest before the reply path
    ("accept: the branch is skipped and a reply is sent instead", PAGE,
     "if (args && args.accept === true) {",
     "if (false && args && args.accept === true) {"),
    ("accept: the offer guard accepts an id nobody offered", PAGE,
     "if (offeredId == null || offeredId !== acceptId) {",
     "if (offeredId == null && acceptId === -1) {"),
    ("accept: the wrong packet is sent", PAGE,
     "var acceptSend = _cmSend('QuestStartRequestMessage',",
     "var acceptSend = _cmSend('NpcDialogReplyMessage',"),
    ("accept: the repeat guard is disarmed", PAGE,
     "&& Date.now() - Number(acceptPending.at || 0) < 5000) {",
     "&& Date.now() - Number(acceptPending.at || 0) < -1) {"),
    ("accept: the dialog is not closed after the send", PAGE,
     "if (acceptUi && typeof acceptUi.leaveDialog === 'function') {",
     "if (false && acceptUi && typeof acceptUi.leaveDialog === 'function') {"),
    ("accept: a bare start is answered as reply 0 again", PAGE,
     "&& !_agentQuestKnown(bareStartId)) {",
     "&& false) {"),
    ("accept: the planner never uses an offered quest", PLAN,
     '            if offered == current or offered == wanted or takes_new:',
     '            if False:'),
    ("accept: the tutorial sends a bare start inside a dialog", PLAN,
     'reply_args: Dict[str, Any] = {"quest_id": qid, "reply": 0}',
     'reply_args: Dict[str, Any] = {"quest_id": qid}'),
    ("accept: the tool contract forgets the argument", SCHEMA,
     '"accept": _p("bool", "Accept the quest this dialog is offering. The page "',
     '"_unused": _p("bool", "Accept the quest this dialog is offering. The page "'),
    ("accept: the runtime registry forgets the argument", CAPS,
     '"accept": {\n     "type": "bool",\n     "required": false,',
     '"_unused": {\n     "type": "bool",\n     "required": false,'),
    # ---- G29: the live quest catalogue and the observed row that outranks the cache
    ("catalog: the step graph stops listing objectives", PAGE,
     "var oids = Array.isArray(ds.objectiveIds) ? ds.objectiveIds : [];",
     "var oids = [];"),
    ("catalog: step names are dropped", PAGE,
     "if (ds.nameId != null) step.name = ds.nameId;",
     "if (false) step.name = ds.nameId;"),
    ("catalog: truncation is not reported", PAGE,
     '                        if (sids.length > shownSteps) {\n                            out.truncated = true;',
     '                        if (false) {\n                            out.truncated = true;'),
    ("catalog: async-only rows are dropped", PAGE,
     '                        if (rest.length) {\n                            out.steps.push({ id: null, unlisted: true,',
     '                        if (false) {\n                            out.steps.push({ id: null, unlisted: true,'),
    ("catalog: the snapshot never builds the catalogue", PAGE,
     "if (qs.catalog.length < 12) {",
     "if (false) {"),
    ("catalog: the catalogue is published empty", PAGE,
     "snap.quests_catalog = qs.catalog;",
     "snap.quests_catalog = [];"),
    ("catalog: the foreign store wins over the client row", KCTX,
     '    observed = _observed_objective(obs, objective_id, tracker_quest_id, language,\n                                   observed_path)',
     '    observed = _observed_objective({}, objective_id, tracker_quest_id, language,\n                                   observed_path)'),
    ("catalog: a row for another quest is accepted", KCTX,
     '                if int(quest.get("id")) != int(tracker_quest_id):',
     "                if False:"),
    ("catalog: the planner stops reading the catalogue", PLAN,
     '        for entry in catalog_objectives(obs, quest_id):\n'
     '            if str(entry.get("id")) == str(oid):',
     '        for entry in []:\n'
     '            if str(entry.get("id")) == str(oid):'),
    ("catalog: the planner reads every quest's rows", PLAN,
     '            if int(quest.get("id")) != int(quest_id):\n'
     '                continue',
     '            if False:\n'
     '                continue'),
    # ---- G29: persisting observed rows and resolving from the artefact
    ("ingest: a thinner later row overwrites the richer one", INGEST,
     "    if new_rich < old_rich:\n        return old",
     "    if False:\n        return old"),
    ("ingest: the dedup key stops naming the objective", INGEST,
     '                    key = (flat["quest_id"], flat["objective_id"])',
     '                    key = (flat["quest_id"], flat["objective_id"], len(rows))'),
    # The ingest scan no longer parses lines itself (G97/G98 moved that into the shared
    # reader), so the "a torn line must not abort the scan" mutation follows it there.
    ("ingest: a torn log line aborts the whole scan", JSONIO,
     "                except ValueError:\n                    break",
     "                except ValueError:\n                    raise"),
    ("ingest: a different-quest hit counts as agreement", INGEST,
     '        same_quest = _coerce_int(resolved.get("quest_id")) == qid',
     "        same_quest = True"),
    ("ingest: an empty capture is reported as data", INGEST,
     '    if not stats["rows"]:',
     "    if False:"),
    ("ingest: the runtime ignores the persisted file", KCTX,
     "    index = load_observed_index(observed_path)",
     "    index = {}"),
    ("ingest: an id two quests claim resolves anyway", KCTX,
     '        if len({row.get("quest_id") for row in candidates}) == 1:',
     "        if True:"),
    ("ingest: a rewrite is invisible until restart", KCTX,
     "    return _persisted_index(str(target), stat.st_mtime_ns, stat.st_size)",
     "    return _persisted_index(str(target), 0, 0)"),
    ("ingest: the persisted block claims to be the catalogue", KCTX,
     '    return _block_from_row(hit, language, "file")',
     '    return _block_from_row(hit, language, "catalog")'),
    ("ingest: the CLI forgets the repo root on sys.path", INGEST,
     "if str(ROOT) not in sys.path:\n"
     "    sys.path.insert(0, str(ROOT))",
     "pass"),
    # ---- G32/G33: a quest/auto run takes the next quest an NPC offers
    ("offer: a new quest is never accepted", PLAN,
     "            if offered == current or offered == wanted or takes_new:",
     "            if offered == current or offered == wanted:"),
    ("offer: an order or farm prompt acquires a quest", PLAN,
     '            takes_new = (_goal_mode(goal) in ("quest", "auto")',
     "            takes_new = (True"),
    ("offer: a named focus is ignored", PLAN,
     "                         and (focus is None or focus == offered))",
     "                         and True)"),
    ("offer: a named prompt takes an unrelated offer", PLAN,
     "and (not named or offered in named)",
     "and True"),
    ("offer: an already-active quest is accepted again", PLAN,
     "            if offered in active_ids:\n                takes_new = False",
     "            if False:\n                takes_new = False"),
    ("offer: the accepted quest is not remembered as client-confirmed", PLAN,
     "                self._client_quests.add(offered)",
     "                pass"),
    ("offer: the accepted quest is not recorded as pending", PLAN,
     "                self.current_quest = offered\n"
     "                self.current_objective = None\n"
     "                self._pending_start = offered\n"
     "                # The offer is client-reported",
     "                self.current_quest = offered\n"
     "                self.current_objective = None\n"
     "                # The offer is client-reported"),
    # ---- G9: bind the run to one character; a slot is not an account
    ("identity: the run never binds a character", AUTO,
     "            self._identity = seen",
     "            self._identity = None"),
    ("identity: a different id is accepted as the same character", AUTO,
     '            same = bound["id"] == seen["id"]',
     "            same = True"),
    ("identity: an unknown snapshot counts as drift", AUTO,
     "        if cid is None and not name:\n"
     '            return getattr(self, "_identity_drift", None) is None',
     "        if cid is None and not name:\n"
     "            return False"),
    ("identity: a character-select snapshot binds the run", AUTO,
     "        if in_world is not True:",
     "        if False:"),
    ("identity: drift is logged but does not stop the run", AUTO,
     "        drift = getattr(self, \"_identity_drift\", None)\n"
     "        if drift is not None:",
     "        drift = getattr(self, \"_identity_drift\", None)\n"
     "        if False:"),
    ("identity: the drift is not recorded", AUTO,
     '        self._identity_drift = {"bound": dict(bound), "seen": dict(seen)}',
     "        self._identity_drift = None"),
    ("identity: the main loop stops checking", AUTO,
     "                if not self._check_identity(before):",
     "                if False:"),
    ("identity: the world wait stops checking", AUTO,
     "            if not self._check_identity(obs):",
     "            if False:"),
    # ---- the one-command live readout for G9/G29/G32/G33
    ("witness: the knowledge census drops observed rows", SREPORT,
     '        if status == "observed" and isinstance(kn, dict):',
     "        if False:"),
    ("witness: the catalogue is never counted", SREPORT,
     '            catalog["rows"] += 1',
     '            catalog["rows"] += 0'),
    ("witness: an explicit accept is counted as a start", SREPORT,
     '        if args.get("accept") is True:',
     "        if False:"),
    ("witness: a start inside a dialog is not counted", SREPORT,
     '                if obs.get("in_dialog") is True or (\n'
     '                        isinstance(dialog, dict) and dialog.get("open")):\n'
     '                    accept["start_in_dialog"] += 1',
     "                if False:\n"
     '                    accept["start_in_dialog"] += 1'),
    ("witness: identity rows are not counted", SREPORT,
     "        identity[event] += 1",
     "        identity[event] += 0"),
    ("witness: no catalogue is reported as data", SREPORT,
     '    if catalog["rows"] == 0:',
     "    if False:"),
    ("witness: the printed report drops the verdicts", SREPORT,
     '        for line in (report.get("witnesses") or {}).get("verdicts") or []:\n'
     '            print("  " + line)',
     "        for line in []:\n"
     '            print("  " + line)'),
    # ---- G29 catalogue: one budget for the whole graph
    ("catalog: the snapshot loop passes the shared budget into the builder", PAGE,
     "                var cat = _agentQuestCatalog(q, qid, prog,\n"
     "                                                                 catalogBudget);",
     "                var cat = _agentQuestCatalog(q, qid, prog);"),
    ("catalog: the shared step budget is ignored", PAGE,
     "                        var stepCap = Math.min(MAX_STEPS,\n"
     "                            Math.max(0, Math.floor(budget.steps)));",
     "                        var stepCap = MAX_STEPS;"),
    ("catalog: the shared objective budget is ignored", PAGE,
     "                            var objCap = Math.min(MAX_OBJECTIVES,\n"
     "                                Math.max(0, Math.floor(budget.objectives)));",
     "                            var objCap = MAX_OBJECTIVES;"),
    ("catalog: a step budget truncation is not reported", PAGE,
     "                        if (sids.length > shownSteps) {\n"
     "                            out.truncated = true;\n"
     "                            budget.truncated = true;\n"
     "                        }",
     "                        if (sids.length > shownSteps) {\n"
     "                            out.truncated = true;\n"
     "                            budget.truncated = false;\n"
     "                        }"),
    # ---- the page build stamp a live run reads first
    ("build: the stamp stays on the old generation", PAGE,
     "    var AGENT_BUILD = '%s';" % _PAGE_STAMP,
     "    var AGENT_BUILD = '2026-10-25d';"),
    ("build: the marker is set from a stale literal", PAGE,
     "        window.__agentBuild = AGENT_BUILD;",
     "        window.__agentBuild = '2026-10-25d';"),
    ("build: a debug fallback bypasses the constant", PAGE,
     "                    build: window.__agentBuild || AGENT_BUILD,",
     "                    build: '2026-10-25d',"),
    ("build: the report stops counting the page build", SREPORT,
     '        builds[str(build) if build else "missing"] += 1',
     '        builds[str(build) if build else "missing"] += 0'),
    # ---- G6: the GUI may not auto-load a circular verifier head
    ("head: the GUI loads any file that exists", CSPANEL,
     "if (File.Exists(contractPath))",
     "if (true)"),
    ("head: the label provenance is not checked", CSPANEL,
     "if (validated && enough && above)",
     "if (true)"),
    ("head: the sample size is ignored", CSPANEL,
     "int.Parse(n.Groups[1].Value) >= 60",
     "true"),
    ("head: the rules are not the default", CSPANEL,
     "string verifier = null;",
     "string verifier = head;"),
    # ---- G29: the quest graph stays out of the training/prompt text
    ("obs: the raw quest graph is dumped into the text", TDS,
     '    rendered.add("quests_catalog")',
     '    pass'),
    ("obs: the quest graph census is dropped", TDS,
     '        lines.append(f"quest graph: {quests} active quest(s), {objectives} objective(s)")',
     "        pass"),
    ("obs: the graph census counts no quests", TDS,
     "        quests = len(graph)",
     "        quests = 0"),
    # ---- G12: the installed map-transition watchdog, executed offline
    ("watchdog: the stalled map is not unlocked", ENGINE,
     'window.foreground.unlock("loadMap");window.foreground.unlock("quickReconnection");',
     'window.foreground.unlock("zzzLoad");window.foreground.unlock("zzzQuick");'),
    ("watchdog: the loading flag stays set", ENGINE,
     "_selfTr._isMapLoading=!1;",
     "_selfTr._isMapLoading=!0;"),
    ("watchdog: the delay is no longer 3.5 s", ENGINE,
     "},3500);this._loadingProgress",
     "},99999);this._loadingProgress"),
    ("watchdog: a healthy map is reset too", ENGINE,
     "if(_selfTr._isMapLoading||_selfTr._mapSceneTransitionGraphic||(window.foreground&&window.foreground.locked)){",
     "if(true){"),
    ("watchdog: a completed transition does not cancel it", ENGINE,
     "if(this._transitionWatchdog){clearTimeout(this._transitionWatchdog);this._transitionWatchdog=null;}",
     "if(false){clearTimeout(this._transitionWatchdog);this._transitionWatchdog=null;}"),
    # ---- G7: the fallback that plays live fight turns, executed offline
    ("fight: a spell is offered without a range check", PAGE,
     "if (this._spellReaches(ctx, candidates[s].spell, enemies[e].cell)) {",
     "if (true) {"),
    ("fight: the lowest-HP target is not chosen", PAGE,
     "var enemies = ctx.enemies.slice().sort(function(a, b) { return a.hp - b.hp; });",
     "var enemies = ctx.enemies.slice().sort(function(a, b) { return b.hp - a.hp; });"),
    ("fight: an unaffordable spell is still offered", PAGE,
     "if (ctx.ap_source !== 'assumed' && cost > ctx.ap) continue;",
     "if (false) continue;"),
    ("fight: no move is planned when nothing reaches", PAGE,
     "if (ctx.mp > 0 && enemies.length) return this._planFightMove(ctx, enemies);",
     "if (false) return this._planFightMove(ctx, enemies);"),
    ("fight: a non-damage spell is cast as an attack", PAGE,
     "if (!this._damageSpell(spell)) continue;",
     "if (false) continue;"),
    ("fight: a move is not required to get closer", PAGE,
     "if (dist >= bestDist) continue;",
     "if (false) continue;"),
    # ---- G10: the Kaggle toolkit copy/pin matrix
    ("kaggle: a toolkit-only module is planned as a copy", KTOOL,
     '            status = "sync" if source.is_file() and rel.suffix == ".py" else "pinned"',
     '            status = "sync" if source.is_file() and rel.suffix == ".py" else "sync"'),
    ("kaggle: the trimmed map index is no longer pinned", KTOOL,
     'PINNED = (\n    "dataset-metadata.json",\n    "data/maps/_index.json",\n',
     'PINNED = (\n    "dataset-metadata.json",\n'),
    ("kaggle: drift is never reported", KTOOL,
     '    return [item for item in entries if item["drift"] or item["missing"]]',
     "    return []"),
    ("kaggle: the manifest is not written", KTOOL,
     "    manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + \"\\n\",\n"
     "                        encoding=\"utf-8\")",
     "    pass"),
    ("kaggle: the manifest records the pre-copy drift", KTOOL,
     "    fresh = plan(root, toolkit)",
     "    fresh = entries"),
    # ---- G29+G33: the chain itself
    ("chain: a live quest is planned without its observation", PLAN,
     'obj = self._observed_current(quest_id, obs)\n                if obj is not None:',
     "obj = None\n                if obj is not None:"),
    ("chain: a live objective is not planned at all", PLAN,
     'calls = self.plan_objective({"id": quest_id}, 0, obj, obs)\n        if not calls:\n            return None',
     "calls = []\n        if not calls:\n            return None"),
    # ---- the live preflight
    ("ready: a stale client page passes", READY,
     '        out.append(_entry("FAIL", "page build",\n'
     '                          "last client recorded %s but the repo is %s; restart the app "\n'
     '                          "fully (a reload keeps the old page)" % (last, current)))',
     '        out.append(_entry("PASS", "page build", "ignored"))'),
    ("ready: a leftover stop file passes", READY,
     "    if stop.exists():\n"
     '        out.append(_entry("FAIL", "stop file",',
     "    if False:\n"
     '        out.append(_entry("FAIL", "stop file",'),
    ("ready: an old installer is not flagged", READY,
     "    elif page_time and setup_time < page_time:",
     "    elif False:"),
    ("ready: the exit code ignores failures", READY,
     '    return 1 if any(item["level"] == "FAIL" for item in results) else 0',
     "    return 0"),
    ("sim: the client never offers the curriculum", SIMRUN,
     "        policy._client_quests |= set(policy._curriculum(args.level))",
     "        pass"),
    # ---- the user-command parser and its dataset
    ("goal: asking the bot to play is not Full Auto", PLAN,
     'if _RE_GOAL_AUTO_PLAY.search(text):',
     'if False:  # _RE_GOAL_AUTO_PLAY.search(text):'),
    ("command: the model is never consulted", CMD,
     "    if model is not None:",
     "    if False:"),
    ("command: the model's confidence is ignored", CMD,
     'if prediction is not None and prediction["confidence"] >= threshold:',
     "if prediction is not None:"),
    ("witness: the command shadow is dropped", SREPORT,
     'shadow["rows"] += 1',
     'shadow["rows"] = 0'),
    ("command report: goal frequencies are lost", SHADOW_REPORT,
     'goals[goal] += 1',
     'goals[goal] = 1'),
    ("live evidence: the autopilot summary is dropped", LIVE,
     'if isinstance(value, dict):',
     'if False:'),
    ("live evidence: a relative log stays relative", LIVE,
     'return Path(path).expanduser().resolve()',
     'return Path(path)'),
    ("live evidence: overrides are dropped from the summary", LIVE,
     '"would_override": sum(1 for row in shadow if row.get("would_override")),',
     '"would_override": 0,'),
    ("command onnx: the price default is dropped", ONNX,
     'if "price" in slots:',
     'if False:  # "price" in slots:'),
    ("command replay: a page advancement is dropped", REPLAY,
     'advanced = int(verdicts.get("advanced", 0))',
     'advanced = 0'),
    ("command replay: the objective delta is reversed", REPLAY,
     'else objectives_after - objectives_before),',
     'else objectives_before - objectives_after),'),
    ("command adapter: the canary can change", SHADOW,
     'return all(after.get(text) == value for text, value in before.items())',
     'return True'),
    ("command adapter: an equal base score is rejected", SHADOW,
     'return (int(after.get("intent", 0)) >= int(before.get("intent", 0))',
     'return (int(after.get("intent", 0)) > int(before.get("intent", 0))'),
    ("command shadow: a confident disagreement is not flagged", SHADOW,
     'row["would_override"] = confidence >= threshold and not agree',
     'row["would_override"] = False'),
    ("command shadow: a mode change is not flagged", SHADOW,
     'row["would_change_mode"] = (usable is not None and confidence >= threshold\n'
     '                                and not row["mode_agree"])',
     'row["would_change_mode"] = False'),
    ("command: the action-to-tool bridge is lost", CMD,
     'return chosen or INTENT_TOOL.get(str(intent or ""))',
     'return INTENT_TOOL.get(str(intent or ""))'),
    ("command: intent-illegal model slots survive", CMD,
     'allowed = INTENT_SLOTS.get(intent)',
     'allowed = None'),
    ("command: tool defaults leak into user intent", CMD,
     'if key == "price_rule" and value == "median_minus_1":\n            continue',
     "if False:\n            continue"),
    ("command data: a held-out entity leaks into train", CDATA,
     'augmented = _augment(command["text"], command["slots"], TRAIN_POOLS, rng)',
     'augmented = _augment(command["text"], command["slots"], EVAL_POOLS, rng)'),
    ("command data: an eval phrasing stays in train", CDATA,
     'if _norm(command["text"]) in eval_texts:\n            skipped += 1\n            continue',
     'if False:\n            skipped += 1\n            continue'),
    ("command data: a near-copy stays in train", CDATA,
     'if contains_eval(command["text"]):\n            near_copies += 1\n            continue',
     'if False:\n            near_copies += 1\n            continue'),
    ("command data: every phrasing lands in train", CDATA,
     'if len(rows) >= 3:\n            train_rows, val_row, test_row = rows[:-2], rows[-2], rows[-1]',
     'if len(rows) >= 3:\n            train_rows, val_row, test_row = rows, None, None'),
    # ---- egress preflight and the host launcher guard
    ("egress: a dead upstream is only a warning", READY2,
     '        return _entry("FAIL", "egress",\n                      "upstream %s:%d is not listening',
     '        return _entry("WARN", "egress",\n                      "upstream %s:%d is not listening'),
    ("egress: pool entries are never probed", READY2,
     '        live = [e for e in live if e and _port_open(e[0], e[1], 0.6)]',
     '        live = []'),
    ("host: the launcher ignores the preflight", HOSTCTL,
     '    if not gate.get("ok"):',
     '    if False:'),
    ("host: an unsafe target rectangle is accepted", HOSTCTL,
     '    if window is None:\n        return True',
     '    if True:\n        return True'),
    # ---- the unattended live runner
    ("live: the runner launches into a dead egress anyway", AUTOLIVE,
     '    return (not blocking or force), blocking',
     '    return True, blocking'),
    ("live: the runner trusts a slot it never saw in a world", AUTOLIVE,
     '        if last.get("in_world"):',
     '        if True:'),
    ("live: the stop file is ignored while waiting", AUTOLIVE,
     '        if stop.exists():\n            return {"ok": False, "reason": "stop file", "last": last}',
     '        if False:\n            return {"ok": False, "reason": "stop file", "last": last}'),
    ("live: a torn log line crashes the round accounting", AUTOLIVE,
     '            if not isinstance(row, dict) or not _is_step_row(row):\n                continue    # a torn write can put anything on the last line',
     '            if False:\n                continue'),
    # ---- the login screen, which used to need a person
    ("live: the runner presses the wrong login path", AUTOLIVE,
     '    data = call(slot, "session.login", {"target": "guest"}, timeout=20.0)',
     '    data = call(slot, "session.login", {}, timeout=20.0)'),
    ("live: a connected page at the login screen is only polled", AUTOLIVE,
     '        if login is not None and last.get("connected") and time.time() - tried_login >= every_s:',
     '        if False:'),
    ("host: the UIA read takes any window of the host process", HOSTCTL,
     "$win = $cands | Where-Object { $_.Current.Name -notmatch 'WebView2|Instance #' -and $_.Current.Name -match 'Farm|Manager' } | Select-Object -First 1",
     "$win = $cands[0]"),
    ("host: the running count is read without raising the panel", HOSTCTL,
     '    if items is None:\n        focus_host()\n        items = elements()',
     '    if items is None:\n        items = elements()'),
    ("login: inspect mode presses the button anyway", PAGE,
     '                if (inspectOnly) return _agentResult(true, out);',
     '                if (false) return _agentResult(true, out);'),
    ("login: the guest step is picked by button order, not by what is stored", PAGE,
     "                    var step = saved ? '_continueGuest' : '_createGuest';",
     "                    var step = '_continueGuest';"),
    ("login: a press does not look at the screen it caused", PAGE,
     '                    out.after = { in_world: !!(after && after.in_world),',
     '                    out.after = { in_world: null,'),
    ("login: the tool writes credentials itself", PAGE,
     "                    var g = raw && JSON.parse(raw)['all#guestAccount'];",
     "                    var g = raw && JSON.parse(raw)['all#guestAccount'];\n                    if (g && g.login) { try { window.dofus.setCredentials(g.login, g.password); } catch (eX) {} }"),
    ("login: a press is reported even when nothing was pressed", PAGE,
     '                if (!pressed) return _agentResult(false, out,',
     '                if (false) return _agentResult(false, out,'),
    # ---- foresight, live transitions, and the counterfactual factory
    ("factory: an objective type gates a quest offer again (the focus/id confusion)", PLAN,
     '            if focus is not None and focus in OBJECTIVE_TYPES:',
     '            if False:'),
    ("transition: an hp tick counts as the world moving", TRANS,
     '        world = sorted(set(changed) & set(WORLD_KEYS))',
     '        world = sorted(changed)'),
    ("transition: progress widened to any change", TRANS,
     '        progress = sorted(set(changed) & set(PROGRESS_KEYS))',
     '        progress = sorted(changed)'),
    ("transition: a read is counted as an action", TRANS,
     '            "was_read": tool in READ_ONLY_TOOLS,',
     '            "was_read": False,'),
    ("transition: silence does not end a segment", TRANS,
     '        broken = (current is not None and gap_s > 0 and ts and last_ts',
     '        broken = (False and current is not None and gap_s > 0 and ts and last_ts'),
    ("transition: the split is by row instead of by segment", TRANS,
     '        folds[plan[row["segment"]]].append(',
     '        folds[split_of(row["id"])].append('),
    ("transition: the live state vector is dropped", TRANS,
     '                if len(candidate) == FEATURE_COUNT:',
     '                if False:'),
    ("foresight: a read may outrank an action", FORE,
     '        out.sort(key=lambda r: (r["read"], -r["gain"], -r["move_prob"]))',
     '        out.sort(key=lambda r: (-r["gain"], -r["move_prob"]))'),
    ("foresight: a broken artifact loads anyway", FORE,
     '        if any(name not in arrays for name in required):',
     '        if False:'),
    ("foresight: a stale feature width scores silently", FORE,
     '        if vec.shape[-1] != self.width:\n            raise ValueError(',
     '        if False:\n            raise ValueError('),
    # ---- the bundle updater: the only witness is a launch against the real CDN,
    # so the source text is what can be pinned here (measured 2026-10-27: a reused
    # WebClient loses its User-Agent after request one and the origin 403s, which
    # kept the client on build 1.74.9 while the server ran 1.74.10).
    ("update: the second request from a client goes without a User-Agent", CSBUNDLE,
     '                        if (!string.IsNullOrEmpty(Agent)) http.UserAgent = Agent;',
     '                        // if (!string.IsNullOrEmpty(Agent)) http.UserAgent = Agent;'),
    ("update: the UA is handed to the shared header bag again", CSBUNDLE,
     '                    wc.Agent = "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36";',
     '                    wc.Headers["User-Agent"] = "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36";'),
    ("update: a staged bundle is judged on the page it never downloads", CSBUNDLE,
     '                        if (!Validate(staging, false, out stagedWhy))',
     '                        if (!Validate(staging, out stagedWhy))'),
    ("update: an unpatched bundle is adopted anyway", CSBUNDLE,
     '                        if (patching\n'
     '                            && (skippedTotal > 0 || (coreTotal > 0 && coreHit < coreTotal)))',
     '                        if (false\n'
     '                            && (skippedTotal > 0 || (coreTotal > 0 && coreHit < coreTotal)))'),
    ("update: instances race to overwrite the same script.js", CSBUNDLE,
     '            lock (_gate) { return UpdateLocked(proxyPort, log); }',
     '            return UpdateLocked(proxyPort, log);'),
    ("update: the page keeps pointing at the bundle it replaced", CSBUNDLE,
     '                            RewriteCacheBusters(dir, log);\n\n'
     '                            // A patched bundle is a hypothesis.',
     '                            // restamping moved away: the page keeps the old ?v=\n\n'
     '                            // A patched bundle is a hypothesis.'),
    # ---- brain daemon currency (a live daemon running code older than its own files)
    ("brain: a daemon with newer files on disk is attached to anyway", CSBOOT,
     '            return StaleModulesOf(health).Length == 0 ? BrainAttach : BrainRestart;',
     '            return BrainAttach;'),
    ("brain: the restart is aimed at no pid in particular", CSBOOT,
     '            return m.Success ? int.Parse(m.Groups["pid"].Value) : 0;',
     '            return 0;'),
    ("brain: the health reply stops carrying the verdict", BRAIND,
     '            "code_stale": [row["module"] for row in stale],',
     '            "code_stale_never": [],'),
    ("brain: every module counts as imported after it changed", BRAIND,
     '        if mtime > started + 1.0:',
     '        if mtime < started + 1.0:'),
    ("probe: the recorded bundleBytes is the character count again", CMPROBE,
     '        bundleBytes: Buffer.byteLength(text, "utf8"),',
     '        bundleBytes: text.length,'),
    # ---- the consult gate (fitted 2026-11-04: 0.9043 -> 0.9099 at nested folds)
    ("gate: the head is never allowed to step aside", CONSULTG,
     '    return f > 0 and gap < f',
     '    return False'),
    ("gate: the encoder arm stops consulting on a low margin", NNPLAN,
     '            if consult_gate.should_defer(gap=self.last_margin):',
     '            if False:'),
    # ---- patch-rule idempotence (a second adoption must not wrap twice)
    ("patch: the guard reverts to the blunt version that dropped a rule", APRULES,
     '            if r["old"] in r["new"] and r["new"] in js:',
     '            if r["new"] in js:'),
    ("patch: a boundary rule is applied again to its own output", APRULES,
     '            if r["start"] in r["replacement"] and r["replacement"] in js:',
     '            if False and r["replacement"] in js:'),
    ("patch: the shipped guard never skips a re-application", CSBOOT,
     '            return selfEmbedding && effectPresent;',
     '            return false;'),
    ("patch: no rule is recognised as self-embedding", CSBOOT,
     '            return replacement.IndexOf(anchor, StringComparison.Ordinal) >= 0;',
     '            return false;'),
    # ---- multi-instance selection (panel choice, loop children, bridge fan-out)
    ("multi: only one instance can ever be selected", CSPA,
     "        public List<int> TargetSlots = new List<int>();",
     "        public int TargetSlotsFixed = 0;"),
    ("multi: the choice is not persisted", CSPA,
     '                        case "target_slots":',
     '                        case "target_slots_disabled":'),
    ("multi: an empty selection addresses nothing instead of all", CSPA,
     '            return ids.Count == 0 ? "all" : string.Join(",", ids.ToArray());',
     '            return ids.Count == 0 ? null : string.Join(",", ids.ToArray());'),
    ("multi: every loop writes into one shared log", CSPA,
     '                        : Path.Combine(stateDir, "autopilot." + instance + ".jsonl");',
     '                        : logFile;'),
    ("multi: one loop leaving declares the machine idle", CSPA,
     "            if (!anyLeft)\n            {",
     "            if (true)\n            {"),
    ("multi: all means the configured list, not the open windows", CSB2,
     "                        foreach (string active in _backend.GetActiveInstanceIds())",
     "                        foreach (string active in new string[0])"),
    ("multi: a directive can name only one client", CSB2,
     "                foreach (string part in target.Split(','))",
     "                foreach (string part in new string[0])"),
    ("multi: a fan-out mislabels an answer from another window", CSB2,
     "                string fallback = allowFallback ? FirstActiveInstance() : null;",
     "                string fallback = FirstActiveInstance();"),
    # ---- the round manifest's own accounting, falsified by a real live round
    ("summarise: steps are matched by a kind no row has ever had", AUTOLIVE,
     '    return row.get("index") is not None and bool(row.get("tool"))',
     '    return row.get("kind") == "step"'),
    ("summarise: the executed rows are filtered on the old kind again", AUTOLIVE,
     '            if not isinstance(row, dict) or not _is_step_row(row):',
     '            if not isinstance(row, dict) or row.get("kind") != "step":'),
    ("summarise: a map change is read from a vector the rows do not carry", AUTOLIVE,
     '    if "map_id" in value:',
     '    if False:'),
    # ---- per-instance AI: one client's badge must not describe or drive the farm
    ("badge: the pill stops every loop again", PAGE,
     "action: this.aiRunning ? 'stop_one' : 'start_one'",
     "action: this.aiRunning ? 'stop_all' : 'start_auto'"),
    ("badge: every window is told the farm-wide answer", CMAINF,
     "bool running = _aiPanel != null && _aiPanel.IsRunningFor(instanceId);",
     "bool running = _aiPanel != null && _aiPanel.RunningNow;"),
    ("badge: one instance's status reads the shared log", CSPA,
     "string line = ReadLastStepLine(StepLogFor(instanceId));",
     "string line = ReadLastStepLine();"),
    ("badge: a child is not attributed to its instance", CSPA,
     "                        _instanceByProc[p] = instance;",
     "                        _instanceByProc[p] = null;"),
    ("badge: the badge diff remembers one flag for all windows", CMAINF,
     "                            _aiPushedByInstance[id] = mine;",
     "                            _aiPushedByInstance[\"all\"] = mine;"),
    ("badge: the host ignores a per-instance request", CMAINF,
     "                        else if (string.Equals(action, \"start_one\", StringComparison.OrdinalIgnoreCase))",
     "                        else if (string.Equals(action, \"start_one_disabled\", StringComparison.OrdinalIgnoreCase))"),
    ("stop: one instance writes the farm-wide stop file", CSPA,
     "File.WriteAllText(StopFilePath(instanceId), why + \" at \" + DateTime.Now.ToString(\"HH:mm:ss\")); }",
     "File.WriteAllText(StopFilePath(null), why + \" at \" + DateTime.Now.ToString(\"HH:mm:ss\")); }"),
    ("stop: every child watches the same stop file again", CSPA,
     "+ \" --stop-file \\\"\" + childStop + \"\\\"\"",
     "+ \" --stop-file \\\"\" + stopFile + \"\\\"\""),
    ("start: a badge start reuses the panel's shared selection", CSPA,
     "string policyOverride = null, string onlyInstance = null)",
     "string policyOverride = null)"),
    # ---- the status reader: the pill said "waiting for the first step" about loops
    # that had taken 44 steps (measured live 2026-10-27)
    ("status: the step tail window is 8 KB again", CSPA,
     "long start = Math.Max(0, len - 65536);",
     "long start = Math.Max(0, len - 8192);"),
    ("status: one short Read ends the tail", CSPA,
     "                    while (got < buf.Length)",
     "                    while (false)"),
    ("status: a fragment of a row is served as the last step", CSPA,
     '                        if (line.IndexOf("\\"index\\"") < 0) continue;',
     '                        // if (line.IndexOf("\\"index\\"") < 0) continue;'),
    ("status: the selftest lost its witness for a mis-formatted row", CSPROG,
     "bool wrongShape = parsed && !UI.AiPanel.FormatStatus(true, line).Contains(\"step\");",
     "bool wrongShape = false;"),
    ("status: an instance ask silently reads the farm-wide log", CSPROG,
     "bool fellBack = askedForInstance && !log.EndsWith(instance + \".jsonl\");",
     "bool fellBack = false;"),
    ("status: the instance argument is read from one fixed index again", CSPROG,
     'if (!args[i].StartsWith("--")) { instance = args[i]; break; }',
     '{ }'),
    # ---- the target picker and Start, both measured live on 2026-10-27: the dialog
    # held "All instances" and "#1" checked together, and Start refused a second
    # instance because a first one was already running.
    ("picker: a chosen row no longer clears 'All instances'", CSPA,
     "                if (box.GetItemChecked(0)) box.SetItemChecked(0, false);",
     "                if (false) box.SetItemChecked(0, false);"),
    ("picker: the 'All instances' tick swallows the chosen rows", CSPA,
     "                if (list.GetItemChecked(i)) next.Add(i);",
     "                if (list.GetItemChecked(i) && !list.GetItemChecked(0)) next.Add(i);"),
    ("picker: the farm-wide refusal runs before the selection", CSPA,
     "            if (picked.Count > 0)",
     "            if (picked.Count > 99)"),
    ("picker: Start goes dark again while any loop runs", CSPA,
     "_btnStart.Enabled = !running || SelectedSlots().Count > 0;",
     "_btnStart.Enabled = !running;"),
    ("picker: a new selection leaves the button state stale", CSPA,
     "            SetRunningUi(RunningNow);",
     "            // (no button refresh)"),
    ("picker: a selected instance is reported instead of started", CSPA,
     "                    StartInstance(id, force);",
     "                    Append(\"[ai] would start \" + id);"),
    # ---- the inactivity kick during a live run (measured 2026-10-27): the client's
    # own timer warns at 5 min idle and calls disconnect("INACTIVITY") at 10, and the
    # keep-alive gate could be shut by a page reload while the loop was still driving.
    ("keep-alive: a directive no longer marks the client's activity", PAGE,
     "                try { window.__agentMarkActivity('directive'); } catch (eAct) {}",
     "                // try { window.__agentMarkActivity('directive'); } catch (eAct) {}"),
    ("keep-alive: the marks are no longer published in the observation", PAGE,
     "                        marks: ka.marks || 0, failed: ka.failed || 0,",
     "                        marks: 0, failed: 0,"),
    ("keep-alive: a missing recordActivity is counted as a success", PAGE,
     "            rec.present = false;\n            rec.failed += 1;",
     "            rec.present = true;\n            rec.marks += 1;"),
    ("keep-alive: the tracker is looked for only on connectionManager again", PAGE,
     "            var found = window.__agentActivityTracker();",
     "            var found = null;"),
    # ---- the loop side of the same instrument: the page counts, the run reports.
    ("afk: the step row stops folding in the witness", AUTO,
     "                self._note_keep_alive(after)",
     "                pass  # (no witness folding)"),
    ("afk: a client with nothing to call is reported as covered", AUTO,
     '        if record.get("present"):',
     '        if record.get("marks"):'),
    ("afk: the counters keep the last reading, not the worst", AUTO,
     '        counters["marks"] = max(counters["marks"], int(record.get("marks") or 0))',
     '        counters["marks"] = int(record.get("marks") or 0)'),
    # ---- the bounded tutorial hold: measured live, 119 and 120 identical no-ops in
    # two sessions, because the wait cleared its own counters and re-dispatched.
    ("hold: an expired window is not counted as a cycle", AUTO,
     "        self._hold_cycles += 1",
     "        pass  # (uncounted)"),
    ("hold: an expired window releases the call on every pass", AUTO,
     "        if (now - self._hold_started_at) < TUTORIAL_HOLD_S:",
     "        if True:"),
    ("hold: a new step does not get its own window", AUTO,
     "        if key != self._hold_key:",
     "        if False:"),
    ("hold: the wait is spent dispatching again", AUTO,
     "                        self._hold_wait(goal, before)",
     "                        time.sleep(1.0)"),
    ("talk: a dead talk is counted but nothing is done", AUTO,
     "                        if self._dead_talks[key] >= DEAD_TALK_TRIES:",
     "                        if False:"),
    ("talk: the page's receipt is ignored", AUTO,
     'in ("talked", "walk_then_talk"))):',
     'in ("talked",))):'),
    ("nav: a declined crossing is not recognised", AUTO,
     "        if exit_to.get(\"direction\") == direction and cell in (",
     "        if False and exit_to.get(\"direction\") == direction and cell in ("),
    ("nav: the crossing is re-asked after the bound", AUTO,
     "                        if self._dead_navs[key] >= DEAD_TALK_TRIES:",
     "                        if False:"),
    ("talk: the unwrapper reads the wrapper, not the payload", EXEC,
     "    inner = data.get(\"response\")",
     "    inner = data"),
    # ---- the knowledge index's projection store (2026-11-03, §L.12): results come from a
    # per-kind JSON blob decoded only for winners, so a broken store must not be able to
    # answer -- or silently fall back to per-row dicts, which is the 20.7 MB this replaced.
    ("proj-store: a lookup finds no store and the row's fields vanish", EXEC,
     "        store = self._val.get(kind)",
     "        store = None"),
    ("proj-store: a row is decoded from an empty slice", EXEC,
     "        return json.loads(blob[offsets[slot]:offsets[slot + 1]])",
     "        return json.loads(blob[offsets[slot]:offsets[slot]])"),
    ("proj-store: the offsets array is published empty", EXEC,
     "                self._val[kind] = (val_offsets, bytes(val_blob))",
     "                self._val[kind] = (array(\"I\", [0]), b\"\")"),
    ("proj-store: rows keep their materialized dict again", EXEC,
     "                        slot: Any = len(entries)",
     "                        slot = stored"),
    # ...and the separator itself. Joining the names with a space is the old representation,
    # which lets a needle containing a space match across two language variants of one name and
    # hands that row the whole-query tier it has no name for.
    ("proj-store: names are joined with a space, so variants splice", EXEC,
     '                    blob.extend("\\x00".join(names_norm).encode("utf-8"))',
     '                    blob.extend(" ".join(names_norm).encode("utf-8"))'),
    # The blob scan maps every occurrence back to the row it starts in; these two are the rules
    # that make it answer the same question the per-row bounded search answered.
    ("proj-scan: an occurrence that ends in the next row still counts", EXEC,
     "            if 0 <= row < count and hit + span <= ends[row + end_shift]:",
     "            if 0 <= row < count:"),
    ("payload: the board's spell range is dropped on the way out", BSOLVER,
     '                "min_range": spell.min_range, "range": spell.max_range,',
     '                "range": spell.max_range,'),
    ("payload: the emitted cast loses the target id", LPOL,
     '                    "targetId": int(first.target_id) if first.target_id is not None else None,',
     '                    "targetId": None,'),
    # §L.34: the live path now takes its contract from `contract_from_board` instead of validating
    # the observation. Every anchor below is text unique to the slim function -- several of its lines
    # are otherwise identical to `validate_observation`'s, and an ambiguous anchor would mutate the
    # full path, move both arms together, and report MISSED while looking like a weak check.
    ("slim contract: a dead enemy still counts as live advice", OBS,
     '    live_enemies = [enemy for enemy in enemies if enemy.alive]',
     '    live_enemies = list(enemies)'),
    ("slim contract: the enemy-stats warning stops being published", OBS,
     '                warnings.append(f"enemy_spell_effects_missing:{ident}:{spell.id}")\n'
     '    if live_enemies:\n'
     '        warnings.append("enemy_stats_not_exposed")',
     '                warnings.append(f"enemy_spell_effects_missing:{ident}:{spell.id}")\n'
     '    if False:\n'
     '        warnings.append("enemy_stats_not_exposed")'),
    ("slim contract: actionable inverts its self check", OBS,
     '        "actionable": bool(ok and in_fight and is_my_turn and me is not None\n'
     '                           and me.alive and live_enemies),',
     '        "actionable": bool(ok and in_fight and is_my_turn and me is None\n'
     '                           and me.alive and live_enemies),'),
    ("slim board block: occupied cells inverts its filter", OBS,
     '        "occupied_cells": sorted({\n'
     '            fighter.cell for fighter in board.fighters\n'
     '            if fighter.alive and fighter.cell is not None}),',
     '        "occupied_cells": sorted({\n'
     '            fighter.cell for fighter in board.fighters\n'
     '            if fighter.alive and fighter.cell is None}),'),
    # The actor's own input arrays (§L.34). Each of these is a silent decision change: the request
    # still succeeds, the action still parses, and only the win rate far away would have moved.
    ("encounter: the enemy stops carrying its level as initiative", PVAL,
     '        initiative[index, offset] = float(fighter.level)',
     '        initiative[index, offset] = 0.0'),
    ("encounter: the boss power branch collapses", PVAL,
     '        power[index, offset] = (10.0 + (1.5 if fighter.is_boss else 1.0)\n'
     '                                * float(fighter.level))',
     '        power[index, offset] = (10.0 + 1.0\n'
     '                                * float(fighter.level))'),
    ("encounter: the enemy spell rows never reach the tables", PVAL,
     '        fill_rows(offset, rows)',
     '        pass'),
    ("encounter: the player's max HP is dropped from the spec", PVAL,
     '        max_hp[index, 0] = float(max(1, player.max_hp))',
     '        max_hp[index, 0] = 1.0'),
    ("log: the challenges a fight started with stop being recorded", CSRV,
     '            "challenges": payload.get("challenges") or [],',
     '            "challenges": [],'),
    # Only assertions proven killable are pinned here. A first draft also carried "drop the tanh"
    # and "misalign the actor slice"; neither is reachable from any check in the fixture, and a
    # MISSED row is coverage debt, not coverage -- those two belong to #18's sweep, not to this list.
    ("model: an illegal move stops being impossible", MODEL,
     '    move_logits = XP.where(masks["move"], logits["move"], NEG)',
     '    move_logits = logits["move"]'),
    ("model: a pass starts carrying a target choice", MODEL,
     '          + XP.where(actions["spell"] < cfg.spells,\n'
     '                      XP.take_along_axis(target_lp,\n'
     '                                          actions["target"][..., None], -1)[..., 0],\n'
     '                      0.0))',
     '          + XP.where(actions["spell"] >= 0,\n'
     '                      XP.take_along_axis(target_lp,\n'
     '                                          actions["target"][..., None], -1)[..., 0],\n'
     '                      0.0))'),
    ("checkpoint: the saved config is ignored and defaults are always used", CKPT,
     '    saved = payload.get("config") or {}',
     '    saved = {}'),
    ("checkpoint: the loader's own max-rounds default moves to the dataclass's", CKPT,
     '        max_rounds=int(saved.get("max_rounds", 24)),',
     '        max_rounds=int(saved.get("max_rounds", 16)),'),
    ("checkpoint: a config field stops being coerced out of JSON", CKPT,
     '        spells=int(saved.get("spells", 4)),',
     '        spells=saved.get("spells", 4),'),
    # §L.41 refused these two because nothing could reach them. The golden fingerprint can, so they
    # are coverage now rather than MISSED rows -- and a monotone function like `tanh` is exactly the
    # case an argmax-only pin would have missed: the ranking survives, the magnitude does not.
    ("model: the value head stops being squashed into [-1, 1]", MODEL,
     '    value = XP.tanh(_dense_apply(hidden, params["value2"]))[:, 0]',
     '    value = _dense_apply(hidden, params["value2"])[:, 0]'),
    ("model: the actor slice is misaligned by one token", MODEL,
     '    return tokens[:, 1:], tokens[:, 0]',
     '    return tokens[:, :-1], tokens[:, 0]'),
    ("log: records get glued onto one line", CSRV,
     '        line = dumps_bytes(record) + b"\\n"',
     '        line = dumps_bytes(record)'),
    ("log: the rotation threshold never fires", CSRV,
     '            if _LOG_BYTES + len(line) >= LOG_MAX_BYTES:',
     '            if False:'),
    ("log: the server stops stamping its own latency", CSRV,
     '    decision["server_latency_ms"] = round(\n'
     '        (time.perf_counter() - started) * 1000.0, 1)',
     '    decision["server_latency_ms"] = None'),
    # The checker itself. Neither of these is visible to the gate's own `doc_facts` check -- the
    # mutated tool would report the green answer -- so only the self-witness fixture can see them.
    ("checker: a stale tagged number stops being a problem", DOCFACTS,
     '                except ValueError:\n                    got = None\n'
     '                if got != want:',
     '                except ValueError:\n                    got = None\n'
     '                if False:'),
    ("checker: an unrendered reference stops being reported", DOCFACTS,
     '                elif measured.get(name) is not None:\n'
     '                    problems.append("%s:%d {{%s}} was never rendered - run --render"',
     '                elif False:\n'
     '                    problems.append("%s:%d {{%s}} was never rendered - run --render"'),
    # `plan_for_action()` is what lets the duel engine play the shipped rule at all (§L.21). Its
    # arithmetic is a copy of the beam expansion's, so the two have to stay in agreement; each of
    # these is one clause of that copy, and the last two are the legality gates.
    ("action: the synthesized cast leaves the AP where it was", BSOLVER,
     "        remaining_ap = board.my_ap - spell.ap_cost + template.ap_gain",
     "        remaining_ap = board.my_ap + template.ap_gain"),
    ("action: the synthesized cast does not wound the target", BSOLVER,
     "            final_hp[target_id] = max(0, target_hp - template.damage_int)",
     "            final_hp[target_id] = target_hp"),
    ("action: the walk spends no movement points", BSOLVER,
     "                remaining_mp=max(0.0, float(board.my_mp) - float(cost)),",
     "                remaining_mp=float(board.my_mp),"),
    ("action: a walk that goes nowhere is still a walk", BSOLVER,
     "            if dest is None or dest == me.cell:",
     "            if dest is None:"),
    ("action: a cast on an already dead target is accepted", BSOLVER,
     "        if spell_id is None or target_hp is None or target_hp <= 0:",
     "        if spell_id is None or target_hp is None:"),
    # The dead instrument: summing the binomial tail upward reads conservative and is constant.
    ("sign: the paired tail is summed from the smaller count up", WSP,
     "        tail = sum(comb(n, i) for i in range(0, min(ups, downs) + 1)) / (2.0 ** n)",
     "        tail = sum(comb(n, i) for i in range(min(ups, downs), n + 1)) / (2.0 ** n)"),
    # §L.22's claim rests on this counter being able to move: the unit arm that reaches the branch
    # reads asked=1, so a counter that stops counting is caught rather than believed.
    ("critic: the consultation stops being counted", LPOL,
     '        self._vc_asked = getattr(self, "_vc_asked", 0) + 1',
     '        self._vc_asked = getattr(self, "_vc_asked", 0)'),
    ("critic: a pick is reported that the ranker never made", LPOL,
     '        self._vc_fired = getattr(self, "_vc_fired", 0) + int(picked is not None)',
     '        self._vc_fired = getattr(self, "_vc_fired", 0) + 1'),
    # §L.24's env math: the health spell and the mask loop are both on the path every interpreter
    # takes, so these are the arms that say the parity measurement can fail.
    ("env: a cast overwrites the target's hp instead of accumulating", EENV,
     "            hp = at(hp)[rows, tgt].add(mend - hit)",
     "            hp = at(hp)[rows, tgt].set(mend - hit)"),
    ("env: every spell slot is declared castable", EENV,
     "        spell_mask = at(spell_mask)[:, :, s].set(can_cast)",
     "        spell_mask = at(spell_mask)[:, :, s].set(True)"),
    ("backend: `at()` hands back the array instead of an update proxy", EBACK,
     "        return array.at",
     "        return array"),
    ("proj-scan: the exact tier forgets where the name ends", EXEC,
     "        return position + span == end or (position + span < end and\n"
     "                                          blob[position + span] == 0)",
     "        return True"),
    ("talk: the counter is reset instead of accumulating", AUTO,
     "self._dead_talks[key] = self._dead_talks.get(key, 0) + 1",
     "self._dead_talks[key] = 1"),
    ("hold: the held call is dispatched anyway", AUTO,
     "                if call is not None and self._holding(call, before):",
     "                if False:"),
    ("hold: a moved world does not release it", AUTO,
     "        if self._hold_world != _world_sig(obs):",
     "        if False:"),
    ("hold: the signature forgets where the character stands", AUTO,
     '    return "%s|%s|%s|%s" % (flat.get("map_id"), flat.get("cell"),',
     '    return "%s|%s|%s|%s" % (flat.get("map_id"), 0,'),
    ("afk: the summary stops reporting the witness", AUTO,
     '"keep_alive": dict(getattr(self, "_keep_alive", {})),',
     '"keep_alive": {},'),
    ("keep-alive: a reloaded page never asks the host for its state", PAGE,
     "                window.chrome.webview.postMessage({ type: 'AI_REQUEST', action: 'state' });",
     "                // (no startup state ask)"),
    # The no-live-enemy guard (#66): its whole content is that allies are not enemies. Count them
    # as targets and the page asks again on a board the brain can only refuse.
    ("guard: allies are counted as live enemies", PAGE,
     "                    if (f && f.team === 'enemy' && f.alive !== false && Number(f.hp) > 0) {",
     "                    if (f && f.alive !== false && Number(f.hp) > 0) {"),
    # ---- the per-form calibration (2026-10-30): every number it prints has a check.
    ("calib: the bucket counts the exact streak, not the normalized one", PROBE,
     '    value = row.get("repeat_streak_norm")\n    if value is None:\n        value = row.get("repeat_streak")',
     '    value = row.get("repeat_streak")\n    if value is None:\n        value = row.get("repeat_streak_norm")'),
    ("calib: the bucket is not clamped at three", PROBE,
     '    return "3+" if value >= 3 else str(max(0, value))',
     '    return str(max(0, value))'),
    ("calib: a form is not shrunk toward the global rate", PROBE,
     '        rate = (met + prior * global_rate) / (len(cell) + prior)',
     '        rate = (met + 0.0) / max(1, len(cell))'),
    ("calib: an unseen bucket is not given its form's rate", PROBE,
     '                "p": (hit + prior * rate) / (len(sub) + prior),',
     '                "p": (hit + 0.0) / max(1, len(sub)),'),
    ("calib: the equal-budget comparison vetoes by row order", PROBE,
     '            order = sorted(range(len(p)), key=lambda i: p[i])',
     '            order = list(range(len(p)))'),
    ("calib: the counter is the exact streak again", PROBE,
     '    return [float(r.get("repeat_streak_norm") or 0) >= 2 for r in rows]',
     '    return [float(r.get("repeat_streak") or 0) >= 2 for r in rows]'),
    ("calib: a tie with the counter is promoted", PROBE,
     '    return (bool(ci) and ci[0] > 0\n'
     '            and points >= PROMOTION_DELTA_POINTS\n',
     '    return (bool(ci) and ci[0] >= 0\n'
     '            and points >= PROMOTION_DELTA_POINTS\n'),
    # The point floor was declared in the plan and read by nothing until G71; this is the
    # mutant that proves it is enforced now.
    ("calib: the point floor is dropped and any positive interval promotes", PROBE,
     '            and points >= PROMOTION_DELTA_POINTS\n',
     '            and points >= -1.0\n'),
    # ---- the second layer: it may only remove the worst of the counter's kept calls.
    ("worker: the extension removes the best kept calls, not the worst", PROBE,
     '        order_kept = sorted(kept, key=lambda i: p[i])',
     '        order_kept = sorted(kept, key=lambda i: -p[i])'),
    ("worker: the extension also re-removes calls the counter already vetoed", PROBE,
     '        kept = [i for i in range(len(te)) if not counter[i]]',
     '        kept = list(range(len(te)))'),
    # ---- the page's interactive tap: the client's own path walks to the element
    # before using it. Recorded live 2026-10-29, sending the message from a distant
    # cell produced nothing at all (element 513208, cell 330, taps from 504/549).
    ("use: the client's walk-then-use path is bypassed", PAGE,
     '                        ie.useInteractive(target.elemId, useSkill);',
     '                        void 0;'),
    ("use: the character's cell is no longer reported", PAGE,
     '                    cell: target.cell, my_cell: myCell, via: via,',
     '                    cell: target.cell, my_cell: null, via: via,'),
    # ---- the priced alternative (G70). One honest mutant: raising the threshold changes
    # who is a candidate, so a check must notice. A "count reads as acting calls" mutant
    # was tried first and MISSED for a legitimate reason — the corpus already gives a read
    # no `claim`, so that filter is redundant and the mutant is equivalent.
    ("alt: the veto threshold is raised past the measured one", PROBE,
     'def alternative_probe(data: Path, target: str = "claim_met",\n'
     '                      veto_at: int = 2) -> Dict[str, Any]:',
     'def alternative_probe(data: Path, target: str = "claim_met",\n'
     '                      veto_at: int = 3) -> Dict[str, Any]:'),
    ("alt: the lookahead stops at a read instead of the next acting call", PROBE,
     '            nxt = next((later for later in group[index + 1:]\n'
     '                        if not later.get("was_read") and later.get("claim")), None)',
     '            nxt = group[index + 1] if index + 1 < len(group) else None'),
    # ---- the corpus janitor and the session close (2026-10-31)
    ("report: an objective first seen done is credited anyway", REPORT,
     '            if state.get(oid) is False and done:\n                flips += 1',
     '            if done:\n                flips += 1'),
    ("close: the ledger drops the objectives that were done", CLOSE,
     '                  "objectives_done", "quests_completed", "maps", "ready_steps",',
     '                  "quests_completed", "maps", "ready_steps",'),
    ("progress: newly done objectives are not progress", PROGRESS,
     '        "objective_progress": (totals["objectives_completed"] > 0\n                               or totals["objectives_done"] > 0),',
     '        "objective_progress": (totals["objectives_completed"] > 0),'),
    ("janitor: a tutorial row is trainable data", JANITOR,
     '            if context.get("tutorial") is True:\n                why.append("tutorial")',
     '            if False:\n                why.append("tutorial")'),
    ("janitor: a wrong-width state vector is kept", JANITOR,
     '                if len(vector) != FEATURE_COUNT:\n                    why.append("feature_width")',
     '                if False:\n                    why.append("feature_width")'),
    ("close: a close with no new rows rebuilds anyway", CLOSE,
     '    if not fresh and not config.force:\n        record["action"] = "no_new_rows"',
     '    if False:\n        record["action"] = "no_new_rows"'),
    ("close: a failing step takes the whole close down", CLOSE,
     '    except Exception as exc:  # noqa: BLE001 - a close must never fail a session',
     '    except ZeroDivisionError as exc:  # noqa: BLE001'),
    ("close: a shrunken log is reported as a negative session", CLOSE,
     '    if any(isinstance(out.get(key), (int, float)) and out[key] < 0\n           for key in MONOTONIC_FIELDS):\n        return {"rotated": True}',
     '    if False:\n        return {"rotated": True}'),
    ("close-hook: a finished run never hands itself off", AUTO,
     '            self.fight_log.flush("session_end")\n            self._close_session()',
     '            self.fight_log.flush("session_end")'),
    ("move-gate: a chance AUC still promotes", MOVE,
     '    if auc is None or float(auc) < min_auc:\n        reasons.append("auc_moved %s < %.2f" % (auc, min_auc))',
     '    if False:\n        reasons.append("auc_moved %s < %.2f" % (auc, min_auc))'),
    ("move-gate: miscalibration still promotes", MOVE,
     '    if ece is None or float(ece) > max_ece:\n        reasons.append("ece_moved %s > %.2f" % (ece, max_ece))',
     '    if False:\n        reasons.append("ece_moved %s > %.2f" % (ece, max_ece))'),
    ("move-gate: too few test rows still promotes", MOVE,
     '    if rows < min_rows:\n        reasons.append("test_rows %d < %d" % (rows, min_rows))',
     '    if False:\n        reasons.append("test_rows %d < %d" % (rows, min_rows))'),
    # ---- the scoreboard and the self-describing close row (2026-10-31)
    ("close: a row stops saying which files were baselines", CLOSE,
     '        report_step["baseline_files"] = sum(\n            1 for delta in deltas.values() if delta.get("baseline"))',
     '        report_step["baseline_files"] = 0'),
    ("close: the ledger forgets why fight evidence was dropped", CLOSE,
     '            "filters": result.get("filters") or {}}',
     '            "filters": {}}'),
    ("close: the move record hides the seed spread", CLOSE,
     '            "seed_aucs": [(run.get("test") or {}).get("auc_moved") for run in runs],\n            "seed_eces": [(run.get("test") or {}).get("ece_moved") for run in runs],',
     '            "seed_aucs": [],\n            "seed_eces": [],'),
    ("close: the row claims the promoted head drove the session", CLOSE,
     '    record["artifacts"] = {"before": census_before,\n                           "after": artifact_census(config.move_out)}',
     '    record["artifacts"] = {"before": census_before,\n                           "after": census_before}'),
    ("close: the live capture adds up only the first slot", CLOSE,
     "        values = [row.get(key) for row in rows]",
     "        values = [row.get(key) for row in rows[:1]]"),
    ("close: the live totals are read but never land in the repository", CLOSE,
     "    _append_ledger(config.evidence_out, dict(at=at, **out))",
     "    pass  # mutated: nothing is copied"),
    ("close: an unchanged live log is re-summarised on every close", CLOSE,
     "    if not changed and not config.evidence_force:\n"
     '        return {"files": len(paths), "changed": [],',
     "    if False and not config.evidence_force:\n"
     '        return {"files": len(paths), "changed": [],'),
    ("progress: a legacy close is not named", PROGRESS,
     '        if is_legacy:\n            legacy += 1',
     '        if False:\n            legacy += 1'),
    ("progress: the session rate is the cumulative corpus again", PROGRESS,
     '                    row["session_rate"] = round(d_moved / d_actions, 4)',
     '                    row["session_rate"] = round(corpus["moved"] / corpus["actions"], 4)'),
    ("progress: a shrinking rebuild is not marked shifted", PROGRESS,
     '                elif d_actions < 0 or d_moved < 0:\n                    row["shifted"] = True',
     '                elif False:\n                    row["shifted"] = True'),
    ("progress: the trend never compares two sessions", PROGRESS,
     '    if len(session_rates) >= 2:',
     '    if len(session_rates) >= 99:'),
    ("progress: the cumulative corpus is summed across closes", PROGRESS,
     '            corpus = {"actions": row["corpus_actions"],\n                      "moved": row["corpus_moved"], "at": record.get("at")}',
     '            corpus = {"actions": (corpus.get("actions") or 0) + row["corpus_actions"],\n                      "moved": (corpus.get("moved") or 0) + row["corpus_moved"],\n                      "at": record.get("at")}'),
    ("progress: a relabeled rebuild counts as a session", PROGRESS,
     '        row["relabeled"] = bool(prev_labeler and labeler and prev_labeler != labeler)',
     '        row["relabeled"] = False'),
    ("progress: a relabeled delta enters the session rates", PROGRESS,
     '                    if not is_legacy and not row["relabeled"]:',
     '                    if not is_legacy:'),
    ("transitions: the manifest stops naming the labeler", TRANS,
     '        "labeler_sha": labeler_sha(),',
     '        "labeler_sha": "",'),
    ("close: the close row stops carrying the labeler identity", CLOSE,
     '            "labeler_sha": manifest.get("labeler_sha")}',
     '            "labeler_sha": None}'),
    ("close: the row stops carrying the goal's verdict", CLOSE,
     '        record["verdict"] = progress_report.verdict(report)',
     '        record["verdict"] = None'),
    ("close: the scorecard scores the new row without the ledger", CLOSE,
     '            progress_report.read_ledger(Path(ledger)) + [record])',
     '            [record])'),
    ("close-lock: a held lock is treated as stale", CLOSE,
     '            if 0 <= age < stale_s:\n                return holder if isinstance(holder, dict) else {}',
     '            if False:\n                return holder if isinstance(holder, dict) else {}'),
    # ---- the advance label sees a client done-flip (2026-10-31)
    ("transitions: an objective first seen done is credited anyway", TRANS,
     '        if state.get(oid) is False and done:\n            flipped = True',
     '        if done:\n            flipped = True'),
    ("transitions: a done flip never reaches the advance label", TRANS,
     '            advance = sorted(set(advance) | {"objectives_done"})',
     '            advance = advance'),
    # ---- side-swapped cross-play (2026-10-31)
    ("side-swap: a seat that wins everything is reported as a policy edge", SIDE,
     '    if max(seat1, 1.0 - seat1) >= SEAT_DOMINANCE:\n        verdict = "seat_dominated"',
     '    if False:\n        verdict = "seat_dominated"'),
    ("side-swap: an edge from one seat is called an edge from both", SIDE,
     '        if wins_a > wins_b and a_seat1 > 0.5 and a_seat2 > 0.5:',
     '        if wins_a > wins_b and a_seat1 > 0.5:'),
    ("side-swap: the second half does not actually swap the seats", SIDE,
     '            a_seat = 1 if half == 0 else 2',
     '            a_seat = 1'),
    # ---- rollback (2026-10-31)
    ("artifacts: a promotion overwrites without keeping the old bytes", ARTIFACTS,
     '    archive_previous(target)\n    _atomic_copy(Path(source), Path(target))',
     '    _atomic_copy(Path(source), Path(target))'),
    ("artifacts: a missing previous restores anyway", ARTIFACTS,
     '    if not previous.is_file():\n        return {"restored": False, "reason": "no_previous", "artifact": str(path)}',
     '    if False:\n        return {"restored": False, "reason": "no_previous", "artifact": str(path)}'),
    # ---- the console encoding of printed data (2026-10-31)
    ("console: the report stops forcing UTF-8 on its stdout", REPORT,
     '    jsonio.utf8_console()  # captured text must not meet a cp1252 console',
     '    pass  # mutated: the console keeps the shell encoding'),
    ("console: the shared guard does not reconfigure the stream", JSONIO,
     '                stream.reconfigure(encoding="utf-8", errors="replace")',
     '                pass  # mutated: the stream keeps the shell encoding'),
    ("io: a text read drops its declared encoding", LOCKTEST,
     '        self.assertEqual({item.state for item in self.daemon._instances.values()}, {"stopped"})\n'
     '        self.assertEqual(json.loads((self.root / "brain.lock").read_text(encoding="utf-8"))["instances"], [])',
     '        self.assertEqual({item.state for item in self.daemon._instances.values()}, {"stopped"})\n'
     '        self.assertEqual(json.loads((self.root / "brain.lock").read_text())["instances"], [])'),
    # ---- the damage read-out's evidence path (2026-10-31)
    ("damage: the read-out defaults back to the probe-only ledger", DAMAGE,
     '                        default=[str(DEFAULT_OUTCOMES), str(TRAJECTORY_OUTCOMES)])',
     '                        default=[str(DEFAULT_OUTCOMES)])'),
    ("damage: the merge keeps whichever copy the first ledger names", DAMAGE,
     '            if held is None or (_has_battle_log(row) and not _has_battle_log(held)):',
     '            if held is None:'),
    ("gate: the failure formatter goes back to nesting the detail", REG,
     '    text = "%s: %s" % (label, detail) if detail else label',
     '    text = "%s%s" % (label, (": %s" % detail) if detail else "")  # mutated'),
    ("delegation: a suite that ran signs nothing", REG,
     '    _delegation("JS page suites", True, ROOT / "tools")',
     '    pass  # mutated: the suite runs but does not sign for it'),
    ("log: a record is appended without its line ending", AUTO,
     '            blob = blob + b"\\n"',
     '            pass  # mutated: the caller is on its own'),
    ("reader: the shared salvage stops decoding glued records", JSONIO,
     '                    row, index = decoder.raw_decode(text, index)',
     '                    row, index = json.loads(text), len(text)'),
    ("reader: a log reader goes back to line-at-a-time parsing", REPORT,
     '    rows: List[Dict[str, Any]] = list(jsonio.iter_rows(path))',
     '    rows: List[Dict[str, Any]] = [json.loads(ln) for ln in path.read_text('
     'encoding="utf-8", errors="replace").splitlines() if ln.strip().startswith("{")]'),
    ("cli: the report script stops adding the repo root", REPORT,
     "if str(REPO_ROOT) not in sys.path:\n"
     "    sys.path.insert(0, str(REPO_ROOT))",
     "pass"),
    ("cli: the script scan watches the files that are not CLIs", REG,
     '               if not p.name.startswith("test_")]',
     '               if p.name.startswith("test_")]'),
    ("cli: the executed reader list loses a member", REG,
     '               ("replay_explore_returns.py", []), '
     '("freeze_planner_replay.py", ["--check"]),',
     '               ("freeze_planner_replay.py", ["--check"]),'),
    ("cli: the scan-only half stops signing", REG,
     '    _delegation("log CLIs executed in script mode", True, REPO_ROOT / "tools",',
     '    _delegation("log CLIs run somewhere else", True, REPO_ROOT / "tools",'),
    ("close: the loop's close timeout drops below the measured pipeline", AUTO,
     "SESSION_CLOSE_TIMEOUT_S = 300",
     "SESSION_CLOSE_TIMEOUT_S = 60"),
    ("close: the live capture stops recording what its read costs", CLOSE,
     '    out["capture_seconds"] = round(time.perf_counter() - started, 2)',
     "    pass  # mutated: the row says it happened but not how long it took"),
    ("no-call: a stranded goal is neither retired nor bounded", AUTO,
     "                    if self._retired < RETIRE_LIMIT:",
     "                    if False:"),
    ("sweep: the loop test pays the settle window again", REG,
     "                   settle_s=0.0)",
     "                   )"),
    ("janitor: the clean corpus forgets who labelled it", JANITOR,
     '    report["labeler_sha"] = read_meta["manifest"].get("labeler_sha")',
     "    pass  # mutated: the trainer reads rows whose labeller it cannot name"),
    ("harness: the replay test loses sight of the collector", REG,
     "    from tools import freeze_planner_replay as freezer",
     "    freezer = None  # mutated: the corpus replay no longer reads its own instrument"),
    ("replay: the collector strands its own append under the skip", FREEZE,
     "            if not isinstance(obs, dict):\n"
     "                continue\n"
     "            by_session.setdefault(str(record.get(\"session\")), []).append(record)",
     "            if not isinstance(obs, dict):\n"
     "                continue\n"
     "                by_session.setdefault(str(record.get(\"session\")), []).append(record)"),
]


JOURNAL = ROOT / ".mutate-journal.json"


def _recover_journal() -> bool:
    """Repair a file a killed run left deliberately broken.

    The restore below lives in a `finally`, which covers an exception and Ctrl+C but not a
    hard kill: a killed sweep left `ok=None, error=None` at a live decision site in
    `agent/autopilot.py` on 2026-10-30, and only the Kaggle-mirror drift check caught it.
    So the pre-mutation bytes are journaled *before* each write, and the next start
    repairs the previous crash before it mutates anything else.
    """
    if not JOURNAL.exists():
        return False
    try:
        entry = json.loads(JOURNAL.read_text(encoding="utf-8"))
        target = Path(entry["path"])
        if entry.get("original_b64"):
            # The exact pre-mutation bytes: a text round-trip rewrites CRLF to the
            # platform newline and left `agent/controller.py` drifted after a killed
            # sweep on 2026-10-31 (found by the Kaggle-mirror drift check).
            target.write_bytes(base64.b64decode(entry["original_b64"]))
        else:
            target.write_text(entry["original"], encoding="utf-8")
        print("RECOVERED %s from the interrupted run" % target.name)
    except Exception as exc:                                  # noqa: BLE001
        print("WARNING a journal exists but could not be applied: %s" % exc)
    finally:
        JOURNAL.unlink(missing_ok=True)
    return True


def run_sections():
    # The child runs deliberately broken code, so its output is a traceback nobody planned for.
    # Reading it as strict UTF-8 (`text=True`) killed the sweep on 2026-11-03: a mutated
    # route check made the child write 0xe9, Python's reader thread raised UnicodeDecodeError,
    # `proc.stdout` came back None, and `None + str` aborted the run at mutation 3 of 18 -- an
    # instrument that dies exactly when the code under test misbehaves is worst when it matters.
    # Capture bytes, decode tolerantly, and ask the child for UTF-8 with a replace fallback.
    env = dict(os.environ, PYTHONIOENCODING="utf-8:replace", PYTHONUTF8="1")
    try:
        proc = subprocess.run([sys.executable, "-c", SNIPPET], cwd=str(ROOT),
                              capture_output=True, timeout=240, env=env)
    except subprocess.TimeoutExpired:
        # A hang is a red gate too: an unattended run would park there.
        return ["presence wait hung (no exit)"], 1, ""
    out = ((proc.stdout or b"") + (proc.stderr or b"")).decode("utf-8", "replace")
    # Only the snippet's own failure marker counts. A bare `startswith("-")` also
    # matched unittest's `------` separators, so every mutation "failed" a check
    # and the sweep could not tell caught from missed (found 2026-10-31).
    labels = [ln[4:].strip() for ln in out.splitlines() if ln.startswith("  - ")]
    return labels, proc.returncode, out


if __name__ != "__main__":
    # This file writes deliberately broken code into the working tree the moment it is
    # imported, so an accidental `import mutate_cause_gate` (or a spec loader in a
    # verification script) must stop here rather than run a sweep nobody asked for.
    raise SystemExit("mutate_cause_gate.py mutates the working tree; run it as a program, "
                     "do not import it")

if "--recover" in sys.argv:
    # Repair-only mode: touch nothing else, run no gate section. This is what a caller
    # uses after an interrupted sweep, and what the regression gate exercises.
    _recover_journal()
    raise SystemExit(0)

_recover_journal()

bad = []
# `MUT_ONLY=substring` runs one batch of mutations. The driver writes broken code into
# the working tree, so a targeted run is also the safe run while anything else is
# looking at the tree; the full sweep is for a quiet one.
only = [token.strip() for token in str(os.environ.get("MUT_ONLY") or "").split(",")
        if token.strip()]
selected = [(index, item) for index, item in enumerate(MUTATIONS)
            if not only or any(token in item[0] for token in only)]
# `MUT_FILES=session_close.py,jsonio.py` selects by the file a mutation edits, which is
# how a change to one module is re-verified without guessing at label substrings: labels
# are prose, targets are the thing that moved.
files = [token.strip() for token in str(os.environ.get("MUT_FILES") or "").split(",")
         if token.strip()]
if files:
    selected = [(index, item) for index, item in selected
                if Path(str(item[1])).name in files]
# `MUT_SHARD=i/n` splits a sweep across isolated copies; each mutation keeps its
# global index, so the shard verdicts can be merged without rerunning anything.
shard = str(os.environ.get("MUT_SHARD") or "").strip()
if shard:
    index, _, total = shard.partition("/")
    index, total = int(index), int(total)
    selected = [(at, item) for at, item in selected if at % total == index]
selected = [item for _, item in selected]
# `MUT_RESUME=logA,logB` skips the mutations already CAUGHT in those sweep logs.
# MISSED and SKIP never count as done: both need a rerun on the current tree.
resume_logs = [Path(item) for item in str(os.environ.get("MUT_RESUME") or "").split(",")
               if item.strip()]
done = set()
for log in resume_logs:
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        continue
    for line in lines:
        if line.startswith("CAUGHT "):
            # `CAUGHT  <label padded to 52> <n> failing check(s)`. A label at or over
            # 52 chars is followed by a single space, so split on the suffix, then
            # drop the count -- splitting on double spaces lost every long label.
            rest = line[len("CAUGHT "):].split(" failing check(s)")[0]
            done.add(rest.rsplit(" ", 1)[0].strip())
if done:
    selected = [item for item in selected if item[0] not in done]
print("%d of %d mutations selected (%d already caught in %d resume log(s))"
      % (len(selected), len(MUTATIONS), len(done), len(resume_logs)))
if os.environ.get("MUT_PLAN"):
    raise SystemExit(0)
# A sweep only reads CAUGHT when the mutated code turns a check red. If the snippet
# cannot run at all -- the wrong interpreter (an embedded one whose path file ignores
# the working directory cannot `import agent`), or a tree an interrupted sweep left
# broken -- then `code != 0` is true for every mutation and the batch manufactures a
# wall of CAUGHT lines out of its own inability to measure. Refuse to start, and pay
# one pass to prove the clean tree is green first.
base_labels, base_code, base_out = run_sections()
if base_labels or base_code != 0:
    print("REFUSED: the clean tree already fails the snippet (%d failing check(s), "
          "exit %d) -- nothing was mutated" % (len(base_labels), base_code))
    for label in base_labels[:6]:
        print("      %s" % label)
    print(base_out[-1500:])
    raise SystemExit(1)
print("clean tree runs the snippet: 0 failing checks, exit 0")
for name, path, old, new in selected:
    original = path.read_text(encoding="utf-8")
    if old not in original:
        print("SKIP  %-52s anchor not found in %s" % (name, path.name))
        bad.append(name)
        continue
    raw = path.read_bytes()
    JOURNAL.write_text(json.dumps({
        "path": str(path),
        "original_b64": base64.b64encode(raw).decode("ascii")}), encoding="utf-8")
    try:
        path.write_text(original.replace(old, new, 1), encoding="utf-8")
        labels, code, out = run_sections()
    finally:
        path.write_bytes(raw)
        JOURNAL.unlink(missing_ok=True)
    caught = bool(labels) or code != 0
    print("%s %-52s %d failing check(s)%s"
          % ("CAUGHT " if caught else "MISSED ", name, len(labels),
             " (section crashed)" if code != 0 and not labels else ""))
    for label in labels[:2]:
        print("      %s" % label)
    if not caught:
        bad.append(name)

labels, code, out = run_sections()
print("\nrestored: %d failing checks, exit %d (must be 0/0)" % (len(labels), code))
if labels or code != 0:
    print(out[-1500:])
print("mutations that no check noticed:", bad or "none")
raise SystemExit(1 if bad or labels or code else 0)
