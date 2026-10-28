"""Deterministic collector tests: no real HTTP, processes, sleeps or corpus writes."""

import contextlib
import io
import json
import unittest
from unittest.mock import Mock, call, patch

try:
    from . import collect_live_data as collector
except ImportError:  # Also support: python tools/test_collect_live_data.py
    import collect_live_data as collector


def row(name="chosen", state="running", alive=True, started_at=100.0, **extra):
    return dict(instance=name, state=state, alive=alive, started_at=started_at, **extra)


def status(*rows):
    return {"ok": True, "instances": list(rows),
            "active": [item["instance"] for item in rows if item.get("alive")]}


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.delays = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.delays.append(seconds)
        self.now += seconds


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.output = io.StringIO()
        self.stack.enter_context(contextlib.redirect_stdout(self.output))
        self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        self.clock = FakeClock()
        self.stack.enter_context(patch.object(collector.time, "monotonic", self.clock.monotonic))
        self.stack.enter_context(patch.object(collector.time, "sleep", self.clock.sleep))
        self.real_preflight = collector._preflight
        self.preflight = self.stack.enter_context(patch.object(collector, "_preflight", return_value=0))
        self.ensure = self.stack.enter_context(patch.object(collector, "_ensure_brain", return_value=True))
        self.get = self.stack.enter_context(patch.object(collector, "_get", side_effect=KeyboardInterrupt))
        self.post = self.stack.enter_context(patch.object(collector, "_post", return_value=status(row())))
        self.run = self.stack.enter_context(patch.object(collector, "_run", return_value=0))
        # Fail closed if a future code path bypasses the fakes.
        for owner, name in ((collector, "_local_opener"), (collector.subprocess, "Popen"),
                            (collector.subprocess, "run")):
            self.stack.enter_context(patch.object(owner, name, side_effect=AssertionError(name)))

    def tearDown(self):
        # Applies to every success, failure, timeout and interrupt test below.
        for request in self.post.call_args_list:
            self.assertEqual(request.args[0], "/brain/start")
            self.assertNotIn("all", request.args[1])

    def feed(self, *snapshots):
        # Exhaustion interrupts rather than hanging even if the implementation regresses.
        self.get.side_effect = list(snapshots) + [KeyboardInterrupt]

    def repeat(self, snapshot):
        self.get.side_effect = None
        self.get.return_value = snapshot

    def assert_read_only(self):
        self.preflight.assert_not_called()
        self.ensure.assert_not_called()
        self.post.assert_not_called()
        self.run.assert_not_called()
        for request in self.get.call_args_list:
            self.assertEqual(request, call("/brain/status"))

    def plan(self):
        return json.JSONDecoder().raw_decode(self.output.getvalue())[0]

    def snapshots(self):
        return [json.loads(line)["instances"] for line in self.output.getvalue().splitlines()
                if line.startswith('{"instances":')]

    def test_defaults_are_read_only_and_all_budgets_zero(self):
        self.feed(status())
        self.assertEqual(collector.main([]), 130)
        self.assert_read_only()
        self.assertEqual(self.plan(), {
            "mode": "monitor", "monitor_timeout_seconds": 0.0,
            "daemon_budgets": {"steps": 0, "minutes": 0.0},
            "rebuild": False, "request": None,
        })
        self.assertEqual(self.get.call_count, 2)
        self.assertEqual(self.clock.delays, [2.0])

    def test_dynamic_fleet_over_forty_discovers_additions_removals_and_empty_fleet(self):
        original = [row("arbitrary/fleet-%d" % i) for i in range(64)]
        changed = original[12:] + [row("new name %d" % i) for i in range(25)]
        self.feed(status(*original), status(*changed), status())
        self.assertEqual(collector.main([]), 130)
        snapshots = self.snapshots()
        self.assertEqual([[item["instance"] for item in snapshot] for snapshot in snapshots],
                         [[item["instance"] for item in original],
                          [item["instance"] for item in changed], []])
        self.assertEqual(self.get.call_count, 4)
        self.assert_read_only()

    def test_fleet_manual_stop_is_observed_without_restart_or_exit(self):
        stopped = row(state="stopped", alive=False)
        self.feed(status(row()), status(row(state="stopping")), status(stopped),
                  status(stopped), status())
        self.assertEqual(collector.main([]), 130)
        self.assertEqual(self.get.call_count, 6)
        self.assertEqual(self.snapshots()[2][0]["state"], "stopped")
        self.assert_read_only()

    def test_fleet_unavailable_or_malformed_status_keeps_monitoring(self):
        self.feed(None, {}, {"ok": False}, {"ok": True, "instances": None},
                  status({"instance": "incomplete"}), status())
        self.assertEqual(collector.main([]), 130)
        self.assertEqual(self.get.call_count, 7)
        self.assertEqual(self.snapshots(), [[]])
        self.assert_read_only()

    def test_monitor_timeout_is_incomplete_even_when_fleet_empty(self):
        self.repeat(status())
        self.assertEqual(collector.main(["--timeout", "3"]), 124)
        self.assertEqual(self.clock.delays, [2.0, 1.0])
        self.assert_read_only()

    def test_monitor_ignores_daemon_budgets_and_rebuild(self):
        self.feed(status(row()))
        self.assertEqual(collector.main(["--instances", " , ", "--steps", "5",
                                         "--minutes", "1", "--rebuild"]), 130)
        self.assertEqual(self.plan()["daemon_budgets"], {"steps": 0, "minutes": 0.0})
        self.assertFalse(self.plan()["rebuild"])
        self.assert_read_only()

    def test_monitor_dry_run_has_no_io(self):
        self.assertEqual(collector.main(["--dry-run"]), 0)
        self.assert_read_only()
        self.get.assert_not_called()
        self.assertEqual(self.clock.delays, [])

    def test_cohort_dry_run_reports_resolved_budgets_and_request_without_io(self):
        self.assertEqual(collector.main(["--instances", " a, b,a ", "--steps", "9",
                                         "--minutes", "2.5", "--timeout", "7",
                                         "--command-shadow", "off", "--rebuild", "--dry-run"]), 0)
        plan = self.plan()
        self.assertEqual(plan["mode"], "cohort")
        self.assertEqual(plan["daemon_budgets"], {"steps": 9, "minutes": 2.5})
        self.assertEqual(plan["monitor_timeout_seconds"], 7)
        self.assertTrue(plan["rebuild"])
        self.assertEqual(plan["request"]["instances"], ["a", "b"])
        self.assertEqual(plan["request"]["command_shadow"], "off")
        self.assert_read_only()
        self.get.assert_not_called()

    def test_invalid_budgets_are_rejected_before_io(self):
        for flag, value in (("--steps", "-1"), ("--minutes", "-1"), ("--timeout", "-1"),
                            ("--minutes", "nan"), ("--minutes", "inf"),
                            ("--timeout", "nan"), ("--timeout", "inf")):
            with self.subTest(flag=flag, value=value), self.assertRaises(SystemExit) as exc:
                collector.main([flag, value])
            self.assertEqual(exc.exception.code, 2)
        self.assert_read_only()
        self.get.assert_not_called()

    def test_explicit_instances_default_to_unlimited_daemon_budgets(self):
        self.feed(status(row(state="finished", alive=False)))
        self.assertEqual(collector.main(["--instances", "chosen"]), 0)
        request = self.post.call_args.args[1]
        self.assertEqual(request["instances"], ["chosen"])
        self.assertEqual(request["steps"], 0)
        self.assertEqual(request["minutes"], 0.0)
        self.assertEqual(self.plan()["monitor_timeout_seconds"], 0.0)
        self.run.assert_not_called()
        self.post.assert_called_once()

    def test_preflight_and_ensure_precede_start_and_wait(self):
        sequence = Mock()
        for name in ("preflight", "ensure", "post", "get"):
            sequence.attach_mock(getattr(self, name), name)
        self.feed(status(row(state="finished", alive=False)))
        self.assertEqual(collector.main(["--instances", "chosen"]), 0)
        self.assertEqual([item[0] for item in sequence.mock_calls],
                         ["preflight", "ensure", "post", "get"])

    def test_cohort_completion_does_not_wait_for_unrelated_active_instance(self):
        self.feed(status(row("unrelated"), row(state="finished", alive=False)))
        self.assertEqual(collector.main(["--instances", "chosen", "--steps", "1"]), 0)
        self.get.assert_called_once_with("/brain/status")
        self.assertEqual([item["instance"] for item in self.snapshots()[0]], ["chosen"])

    def test_unrelated_session_details_do_not_block_selected_completion(self):
        self.feed(status({"instance": "unrelated", "state": "error"},
                         row(state="finished", alive=False)))
        self.assertEqual(collector.main(["--instances", "chosen"]), 0)
        self.get.assert_called_once()

    def test_reused_session_uses_returned_started_at_and_starts_only_once(self):
        self.post.return_value = status(row(started_at=12.0))
        self.feed(status(row(started_at=12.0), row("other")),
                  status(row(state="finished", alive=False, started_at=12.0), row("other")))
        self.assertEqual(collector.main(["--instances", "chosen", "--steps", "8"]), 0)
        self.post.assert_called_once()
        self.assertEqual(self.get.call_count, 2)

    def test_all_selected_sessions_must_finish(self):
        self.post.return_value = status(row("a"), row("b"))
        self.feed(status(row("a", state="finished", alive=False), row("b"), row("other")),
                  status(row("a", state="finished", alive=False),
                         row("b", state="finished", alive=False), row("other")))
        self.assertEqual(collector.main(["--instances", "a,b,a"]), 0)
        self.assertEqual(self.post.call_args.args[1]["instances"], ["a", "b"])
        self.assertEqual(self.get.call_count, 2)

    def test_selected_manual_stop_is_incomplete_and_never_restarted_or_rebuilt(self):
        self.feed(status(row(state="stopping")), status(row(state="stopped", alive=False)))
        self.assertEqual(collector.main(["--instances", "chosen", "--steps", "5", "--rebuild"]), 2)
        self.post.assert_called_once()
        self.assertEqual(self.get.call_count, 2)
        self.run.assert_not_called()

    def test_reused_stopping_session_is_not_restarted(self):
        self.post.return_value = status(row(state="stopping"))
        self.feed(status(row(state="stopped", alive=False)))
        self.assertEqual(collector.main(["--instances", "chosen"]), 2)
        self.post.assert_called_once()

    def test_replacement_session_cannot_satisfy_original_cohort(self):
        self.feed(status(row(state="finished", alive=False, started_at=101.0)))
        self.assertEqual(collector.main(["--instances", "chosen", "--steps", "1", "--rebuild"]), 2)
        self.post.assert_called_once()
        self.run.assert_not_called()

    def test_missing_selected_instance_is_not_success(self):
        self.repeat(status(row("other", state="finished", alive=False)))
        self.assertEqual(collector.main(["--instances", "chosen", "--timeout", "3",
                                         "--steps", "1", "--rebuild"]), 124)
        self.assertEqual(self.get.call_count, 2)
        self.run.assert_not_called()

    def test_malformed_or_missing_status_never_completes_a_cohort(self):
        invalid = [None, [], {}, {"ok": False, "instances": []}, {"ok": True},
                   {"ok": True, "instances": None}, {"ok": True, "instances": {}},
                   {"ok": True, "instances": [None]}, status(),
                   status(row(), row()), status({"instance": "chosen"}),
                   status(row(alive="false")), status(row(started_at=None)),
                   status(row(started_at=True)), status(row(started_at=float("nan"))),
                   status(row(state="unknown")), status(row(name=""))]
        for snapshot in invalid:
            with self.subTest(snapshot=snapshot):
                self.repeat(snapshot)
                self.assertEqual(collector.main(["--instances", "chosen", "--timeout", "1",
                                                 "--steps", "1", "--rebuild"]), 124)
        self.run.assert_not_called()

    def test_status_can_recover_after_missing_snapshot(self):
        self.feed(None, status(), status(row(state="finished", alive=False)))
        self.assertEqual(collector.main(["--instances", "chosen", "--timeout", "10"]), 0)
        self.assertEqual(self.get.call_count, 3)

    def test_false_alive_without_finished_state_is_not_completion(self):
        for state in ("starting", "running", "stopping"):
            with self.subTest(state=state):
                self.repeat(status(row(state=state, alive=False)))
                self.assertEqual(collector.main(["--instances", "chosen", "--timeout", "1"]), 124)

    def test_finished_but_still_alive_is_not_completion(self):
        self.repeat(status(row(state="finished", alive=True)))
        self.assertEqual(collector.main(["--instances", "chosen", "--timeout", "1"]), 124)

    def test_start_error_row_overrides_top_level_ok_true(self):
        self.post.return_value = status(row("healthy"), row(state="error", alive=False, error="failed"))
        self.assertEqual(collector.main(["--instances", "healthy,chosen", "--steps", "1", "--rebuild"]), 2)
        self.get.assert_not_called()
        self.run.assert_not_called()
        self.post.assert_called_once()

    def test_start_error_state_without_error_text_is_failure(self):
        self.post.return_value = status(row(state="error", alive=False))
        self.assertEqual(collector.main(["--instances", "chosen"]), 2)
        self.get.assert_not_called()

    def test_start_error_field_even_on_running_row_is_failure(self):
        self.post.return_value = status(row(error="setup failed"))
        self.assertEqual(collector.main(["--instances", "chosen"]), 2)
        self.get.assert_not_called()

    def test_invalid_start_response_never_switches_to_fleet_monitor(self):
        invalid = [None, [], {}, {"ok": False}, status(), status(row("wrong")),
                   status(row(), row("extra")), status(row(), row()),
                   status({"instance": "chosen"}), status(row(started_at=None)),
                   status(row(alive=0)), status(row(state="unknown"))]
        for response in invalid:
            with self.subTest(response=response):
                self.post.return_value = response
                self.assertEqual(collector.main(["--instances", "chosen", "--rebuild", "--steps", "1"]), 2)
        self.get.assert_not_called()
        self.run.assert_not_called()

    def test_runtime_error_or_exit_is_incomplete(self):
        for terminal in (row(state="error", alive=False), row(state="exited", alive=False),
                         row(state="finished", alive=False, error="failed")):
            with self.subTest(terminal=terminal):
                self.feed(status(terminal))
                self.assertEqual(collector.main(["--instances", "chosen", "--steps", "1", "--rebuild"]), 2)
        self.run.assert_not_called()

    def test_timeout_does_not_stop_or_rebuild_selected_or_unrelated_loops(self):
        self.repeat(status(row(), row("unrelated")))
        self.assertEqual(collector.main(["--instances", "chosen", "--minutes", "20",
                                         "--timeout", "3", "--rebuild"]), 124)
        self.assertEqual(self.clock.now, 3.0)
        self.post.assert_called_once()
        self.run.assert_not_called()

    def test_explicit_monitor_timeout_is_not_clamped_to_daemon_minutes(self):
        self.repeat(status(row()))
        self.assertEqual(collector.main(["--instances", "chosen", "--minutes", "0.1",
                                         "--timeout", "100"]), 124)
        self.assertEqual(self.clock.now, 100.0)

    def test_daemon_budget_does_not_create_implicit_monitor_timeout(self):
        def slow_snapshot(_path):
            self.clock.now += 5000.0
            if self.get.call_count == 1:
                return status(row())
            return status(row(state="finished", alive=False))

        self.get.side_effect = slow_snapshot
        self.assertEqual(collector.main(["--instances", "chosen", "--steps", "1", "--minutes", "0.1"]), 0)
        self.assertGreater(self.clock.now, 10000.0)
        self.post.assert_called_once()

    def test_status_arriving_after_deadline_is_not_success(self):
        def late_snapshot(_path):
            self.clock.now = 4.0
            return status(row(state="finished", alive=False))

        self.get.side_effect = late_snapshot
        self.assertEqual(collector.main(["--instances", "chosen", "--timeout", "3"]), 124)

    def test_ctrl_c_leaves_started_loops_untouched_and_skips_rebuild(self):
        self.assertEqual(collector.main(["--instances", "chosen", "--steps", "1", "--rebuild"]), 130)
        self.post.assert_called_once()
        self.run.assert_not_called()

    def test_ctrl_c_during_poll_delay_leaves_loops_untouched(self):
        self.repeat(status(row()))
        with patch.object(collector.time, "sleep", side_effect=KeyboardInterrupt):
            self.assertEqual(collector.main(["--instances", "chosen", "--steps", "1", "--rebuild"]), 130)
        self.post.assert_called_once()
        self.run.assert_not_called()

    def test_ctrl_c_during_start_does_not_attempt_cleanup_stop(self):
        self.post.side_effect = KeyboardInterrupt
        self.assertEqual(collector.main(["--instances", "chosen"]), 130)
        self.post.assert_called_once()
        self.get.assert_not_called()

    def test_successful_finite_cohort_does_not_rebuild_by_default(self):
        self.feed(status(row(state="finished", alive=False)))
        self.assertEqual(collector.main(["--instances", "chosen", "--steps", "2"]), 0)
        self.run.assert_not_called()

    def test_rebuild_and_probe_are_opt_in_after_successful_finite_completion(self):
        self.feed(status(row()), status(row(state="finished", alive=False), row("unrelated")))
        observed = []
        self.run.side_effect = lambda command: observed.append(self.get.call_count) or 0
        self.assertEqual(collector.main(["--instances", "chosen", "--steps", "2", "--rebuild"]), 0)
        self.assertEqual(self.run.call_args_list, [
            call([collector.sys.executable, "tools/build_entity_pointer_dataset.py"]),
            call([collector.sys.executable, "-m", "combat.grid_probe"]),
        ])
        self.assertEqual(observed, [2, 2])

    def test_positive_minutes_also_allow_explicit_rebuild(self):
        self.feed(status(row(state="finished", alive=False)))
        self.assertEqual(collector.main(["--instances", "chosen", "--minutes", "1", "--rebuild"]), 0)
        self.assertEqual(self.run.call_count, 2)

    def test_unbounded_cohort_cannot_rebuild_even_with_monitor_timeout(self):
        self.feed(status(row(state="finished", alive=False)))
        self.assertEqual(collector.main(["--instances", "chosen", "--timeout", "5", "--rebuild"]), 0)
        self.run.assert_not_called()
        self.assertFalse(self.plan()["rebuild"])

    def test_builder_failure_propagates_and_skips_probe(self):
        self.feed(status(row(state="finished", alive=False)))
        self.run.return_value = 17
        self.assertEqual(collector.main(["--instances", "chosen", "--steps", "1", "--rebuild"]), 17)
        self.run.assert_called_once_with([collector.sys.executable, "tools/build_entity_pointer_dataset.py"])

    def test_probe_failure_propagates(self):
        self.feed(status(row(state="finished", alive=False)))
        self.run.side_effect = [0, 23]
        self.assertEqual(collector.main(["--instances", "chosen", "--steps", "1", "--rebuild"]), 23)
        self.assertEqual(self.run.call_count, 2)

    def test_preflight_tool_failure_propagates_and_prevents_start(self):
        self.preflight.side_effect = self.real_preflight
        self.run.return_value = 19
        self.assertEqual(collector.main(["--instances", "chosen"]), 19)
        self.run.assert_called_once_with([collector.sys.executable, "tools/live_ready.py"])
        self.ensure.assert_not_called()
        self.post.assert_not_called()
        self.get.assert_not_called()

    def test_ensure_failure_prevents_start(self):
        self.ensure.return_value = False
        self.assertEqual(collector.main(["--instances", "chosen"]), 2)
        self.post.assert_not_called()
        self.run.assert_not_called()

    def test_builder_launch_error_is_nonzero_without_probe_or_stop(self):
        self.feed(status(row(state="finished", alive=False)))
        self.run.side_effect = OSError("cannot launch builder")
        self.assertEqual(collector.main(["--instances", "chosen", "--steps", "1", "--rebuild"]), 2)
        self.run.assert_called_once()


class HttpAndToolTests(unittest.TestCase):
    def response(self, payload):
        response = Mock()
        response.read.return_value = payload
        context = Mock()
        context.__enter__ = Mock(return_value=response)
        context.__exit__ = Mock(return_value=False)
        return context

    def test_get_decodes_fake_http_status(self):
        payload = status(row())
        with patch.object(collector, "_local_opener") as opener:
            opener.return_value.open.return_value = self.response(json.dumps(payload).encode())
            self.assertEqual(collector._get("/brain/status"), payload)
            opener.return_value.open.assert_called_once_with(collector.BRAIN + "/brain/status", timeout=1.5)

    def test_get_returns_none_for_bad_json_nonobject_or_http_failure(self):
        with patch.object(collector, "_local_opener") as opener:
            for payload in (b"not json", b"null", b"[]"):
                with self.subTest(payload=payload):
                    opener.return_value.open.return_value = self.response(payload)
                    self.assertIsNone(collector._get("/brain/status"))
            opener.return_value.open.side_effect = OSError("offline")
            self.assertIsNone(collector._get("/brain/status"))

    def test_post_encodes_only_explicit_start_request(self):
        body = {"instances": ["chosen"], "steps": 0, "minutes": 0.0}
        with patch.object(collector, "_local_opener") as opener:
            opener.return_value.open.return_value = self.response(b'{"ok": true}')
            self.assertEqual(collector._post("/brain/start", body), {"ok": True})
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, collector.BRAIN + "/brain/start")
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(json.loads(request.data), body)

    def test_post_returns_none_for_malformed_response_and_http_failure(self):
        with patch.object(collector, "_local_opener") as opener:
            for payload in (b"invalid", b"null", b"[]"):
                with self.subTest(payload=payload):
                    opener.return_value.open.return_value = self.response(payload)
                    self.assertIsNone(collector._post("/brain/start", {}))
            opener.return_value.open.side_effect = OSError("offline")
            self.assertIsNone(collector._post("/brain/start", {}))

    def test_run_returns_real_subprocess_exit_value_from_fake_process(self):
        with patch.object(collector.subprocess, "run", return_value=Mock(returncode=27)) as run:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(collector._run(["python", "fake_tool.py"]), 27)
            run.assert_called_once_with(["python", "fake_tool.py"], cwd=str(collector.REPO_ROOT), check=False)


if __name__ == "__main__":
    unittest.main()
