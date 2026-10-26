"""Mutation test: prove the new cause/recovery checks can actually fail.

Each mutation is applied to the real source, the affected gate sections run in a
throwaway process, and the file is restored in a `finally`. A section that crashes
counts as caught: the gate exits non-zero, which is red by definition.

Warning: this writes deliberately broken code into the working tree for a second or
two at a time. Do not run it while something else is committing or packaging the
tree (an editor auto-save, a build, or a scheduled commit) -- the restore is fast but
not atomic, and a capture inside the window would record a mutated file.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CTRL = ROOT / "agent" / "controller.py"
AUTO = ROOT / "agent" / "autopilot.py"
BRIDGE = ROOT / "agent" / "bridge.py"
PLAN = ROOT / "agent" / "planner.py"
REPORT = ROOT / "tools" / "recovery_report.py"
BRAIN = ROOT / "combat" / "brain_service.py"

SNIPPET = ("import agent.regression as r\n"
           "r.test_failure_causes()\n"
           "r.test_absent_client_recovery()\n"
           "r.test_repair_credit()\n"
           "r.test_reads_are_not_actions()\n"
           "r.test_dialog_labels()\n"
           "r.test_dialog_repeat_guard()\n"
           "r.test_brain_autostart()\n"
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
for name, path, old, new in MUTATIONS:
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
