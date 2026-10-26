"""Mutation test: prove the new cause/recovery checks can actually fail.

Each mutation is applied to the real source, the affected gate sections run in a
throwaway process, and the file is restored in a `finally`. A section that crashes
counts as caught: the gate exits non-zero, which is red by definition.

Warning: this writes deliberately broken code into the working tree for a second or
two at a time. Do not run it while something else is committing or packaging the
tree (an editor auto-save, a build, or a scheduled commit) -- the restore is fast but
not atomic, and a capture inside the window would record a mutated file.

`MUT_ONLY="brain,dry run"` selects mutations by a substring of their label, so a new
batch can be proven without re-running every older one.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CTRL = ROOT / "agent" / "controller.py"
AUTO = ROOT / "agent" / "autopilot.py"
BRIDGE = ROOT / "agent" / "bridge.py"
PLAN = ROOT / "agent" / "planner.py"
REPORT = ROOT / "tools" / "recovery_report.py"
SREPORT = ROOT / "tools" / "session_report.py"
BRAIN = ROOT / "combat" / "brain_service.py"
READINESS = ROOT / "tools" / "brain_readiness.py"
REPLAY = ROOT / "tools" / "policy_over_record_boards.py"
# The live page and its own suite, one directory up: fight.state is where the board is
# produced, so a gate that only mutates Python cannot prove it reads that producer.
PAGE = ROOT.parent / "scripts" / "mobile-disguise.js"
PAGE_TEST = ROOT.parent / "tools" / "test_fight_state_fields.js"
SCAN = ROOT / "tools" / "group_scan.py"
VERIF = ROOT / "agent" / "verifier.py"
FEATURES = ROOT / "agent" / "features.py"
NNPLAN = ROOT / "agent" / "planner_nn.py"
KCTX = ROOT / "agent" / "knowledge_context.py"
SHAPES = ROOT / "tools" / "quest_advance_shapes.py"

SNIPPET = ("import agent.regression as r\n"
           "r.test_failure_causes()\n"
           "r.test_absent_client_recovery()\n"
           "r.test_repair_credit()\n"
           "r.test_reads_are_not_actions()\n"
           "r.test_dialog_labels()\n"
           "r.test_dialog_repeat_guard()\n"
           "r.test_brain_autostart()\n"
           "r.test_brain_readiness()\n"
           "r.test_js_page_suites()\n"
           "r.test_board_replay()\n"
           "r.test_group_scan_reader()\n"
           "r.test_npc_start_rotation_bound()\n"
           "r.test_group_refusal_routing()\n"
           "r.test_ready_objective_scan()\n"
           "r.test_objective_buckets()\n"
           "r.test_objective_parameter_table()\n"
           "r.test_knowledge_identity()\n"
           "r.test_client_confirmed_starts()\n"
           "r.test_quest_advance_shapes()\n"
           "r.test_session_report_census()\n"
           "r.test_report_instrument()\n"
           "r.test_settle_waits_for_promised_effect()\n"
           "print('FAILURES=%d' % len(r._FAILURES))\n"
           "[print('  -', f[:74]) for f in r._FAILURES]\n")

MUTATIONS = [
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
     'window = max(window, 6.0)',
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
     "        self._step_total += 1",
     "        self._step_total += 1"),
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
     'if candidate.tool not in idle and candidate.tool != "-":',
     "if True:"),
    # ---- dialog outcome labels
    ("the dialog row is not written as a dialog row", AUTO,
     '            "kind": "dialog",', '            "kind": "dialog_disabled",'),
    ("a stalled reply is reported as progress", AUTO,
     '            outcome = "same page"', '            outcome = "advanced"'),
    ("every step becomes a dialog row", AUTO,
     "        if chose is None and not args.get(\"close\"):\n            return\n",
     ""),
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
     '            ended_by = "asked" if asked == "close" else "after_reply"',
     '            ended_by = "asked"'),
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
    ("the report cannot say who ended the dialog", REPORT,
     '            if r.get("ended_by"):', "            if False:"),
    ("the report hides the re-ask NOTE", REPORT,
     "        worst = [(n, k) for k, n in dlg_repeat.most_common(3) if n > 1]",
     "        worst = []"),
    ("a refused send is invisible in the report", REPORT,
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
     '    if open_port(port):\n        return "port_held"',
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
    ("the fill rewrites the recorded board instead of a copy", REPLAY,
     "    out = json.loads(json.dumps(board))", "    out = board"),
    ("the self max_ap is filled from the movement stat", REPLAY,
     'fighter["max_ap"] = stats.get("action_points")',
     'fighter["max_ap"] = stats.get("movement_points")'),
    ("an enemy's max hp is never inferred, so nothing becomes actionable", REPLAY,
     '        if fighter.get("max_hp") is None:\n            fighter["max_hp"] = fighter.get("hp")',
     '        if False:\n            fighter["max_hp"] = fighter.get("hp")'),
    ("a policy that raises disappears from the report", REPLAY,
     '            errors["%s: %s" % (type(exc).__name__, str(exc)[:70])] += 1\n            continue',
     "            continue"),
    ("the replay asks turns that are not the character's", REPLAY,
     '        if only_my_turn and not board.get("is_my_turn"):',
     "        if False:"),
    ("percentiles are read off the unsorted latencies", REPLAY,
     "    latencies.sort()\n", ""),
    ("an empty corpus is reported as a result", REPLAY,
     '    if not rows:\n        print("VERDICT: nothing recorded',
     '    if False:\n        print("VERDICT: nothing recorded'),
    ("the fill hypothesis is never printed", REPLAY,
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
     '                                                   detail=getattr(result, "data", None))',
     "                                                   before, after)"),
    ("refusal: the reader stops counting the loop's group refusals", SCAN,
     "    refusals = _refusals(paths)", "    refusals = []"),
    ("refusal: the agent log is left out of the default reading", SCAN,
     "    if not args.paths and AGENT_LOG is not None and AGENT_LOG.exists():\n"
     "        paths.append(AGENT_LOG)\n", ""),
    ("refusal: any failed call is counted as a group refusal", SCAN,
     '                if (row.get("kind") == "decision" and row.get("tool") in GROUP_REFUSAL_TOOLS\n'
     '                        and row.get("ok") is False):',
     '                if (row.get("kind") == "decision"\n'
     '                        and row.get("ok") is False):'),
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
     '        print("  objectives +%d, quests completed +%d, maps %d, manual interventions %d, "',
     '        print("  objectives +%d, maps %d, manual interventions %d, "'),
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
]


def run_sections():
    try:
        proc = subprocess.run([sys.executable, "-c", SNIPPET], cwd=str(ROOT),
                              capture_output=True, text=True, timeout=240)
    except subprocess.TimeoutExpired:
        # A hang is a red gate too: an unattended run would park there.
        return ["presence wait hung (no exit)"], 1, ""
    out = proc.stdout + proc.stderr
    labels = [ln.strip(" -") for ln in out.splitlines() if ln.strip().startswith("-")]
    return labels, proc.returncode, out


bad = []
# `MUT_ONLY=substring` runs one batch of mutations. The driver writes broken code into
# the working tree, so a targeted run is also the safe run while anything else is
# looking at the tree; the full sweep is for a quiet one.
import os
only = [token.strip() for token in str(os.environ.get("MUT_ONLY") or "").split(",")
        if token.strip()]
selected = [item for item in MUTATIONS
            if not only or any(token in item[0] for token in only)]
print("%d of %d mutations selected" % (len(selected), len(MUTATIONS)))
for name, path, old, new in selected:
    original = path.read_text(encoding="utf-8")
    if old not in original:
        print("SKIP  %-52s anchor not found in %s" % (name, path.name))
        bad.append(name)
        continue
    try:
        path.write_text(original.replace(old, new, 1), encoding="utf-8")
        labels, code, out = run_sections()
    finally:
        path.write_text(original, encoding="utf-8")
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
