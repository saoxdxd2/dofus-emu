import json
import os
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from agent.brain_daemon import BrainDaemon, BrainInstance
from agent.bridge import local_headers
from agent.serve import AgentHTTPServer


class Pilot:
    def __init__(self, name, root):
        self.goals = ["auto"]
        self.log_path = root / ("autopilot.%s.jsonl" % name)
        self.stop_file = root / ("AUTOPILOT_STOP.%s" % name)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.cleaning = threading.Event()
        self.cleanup_release = threading.Event()

    def run(self):
        self.entered.set()
        if not self.release.wait(15):
            raise RuntimeError("test did not release pilot")
        return {"steps": 0}

    def stop_client(self):
        self.cleaning.set()
        if not self.cleanup_release.wait(15):
            raise RuntimeError("test did not release cleanup")


class BrainLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="brain-lifecycle-")
        self.root = Path(self.temp.name)
        # The service requires the run secret on every loopback call, so the suite has to
        # present one like any other client. Pinned in the environment rather than read
        # from %LOCALAPPDATA% so the test is hermetic: it neither depends on nor creates
        # the machine's real secret file.
        self._saved_secret = os.environ.get("DOFUS_RUN_TOKEN")
        os.environ["DOFUS_RUN_TOKEN"] = "test-run-secret"
        self.daemon = BrainDaemon(log_dir=self.root, recorder_dir=self.root)
        # Stopping an instance kicks off a real dataset rebuild (tools/refresh_live_dataset.py
        # over 100 MB+, then the knowledge index). Measured 2026-11-07: a fleet stop during
        # this suite spawned those subprocesses, they saturated the disk, and the 2 s client
        # budget in the HTTP test began timing out one run in three -- a flake caused by the
        # test, not by the daemon. The rebuild has its own suite; here it is only noise.
        self.daemon._ingest_live = lambda: False
        self.pilots = {}
        self.requests = []
        self.daemon._make_pilot = self.make_pilot

    def make_pilot(self, name, config):
        pilot = Pilot(name, self.root)
        self.pilots[name] = pilot
        return pilot

    def tearDown(self):
        for pilot in self.pilots.values():
            pilot.release.set()
            pilot.cleanup_release.set()
        for thread in self.requests:
            thread.join(6)
        # Snapshot under the daemon's own lock: a start that outlived its client is still
        # inserting instances from the server thread, and iterating the live dict raised
        # "dictionary changed size during iteration" *after* the real failure, burying it.
        with self.daemon._lock:
            items = list(self.daemon._instances.values())
        for item in items:
            if item.thread:
                item.thread.join(3)
                self.assertFalse(item.thread.is_alive())
        if self._saved_secret is None:
            os.environ.pop("DOFUS_RUN_TOKEN", None)
        else:
            os.environ["DOFUS_RUN_TOKEN"] = self._saved_secret

    # -- the self-reload guard ------------------------------------------------

    def test_a_daemon_built_by_a_test_does_not_re_exec_the_test(self):
        """Re-exec replaces the process that *constructed* the daemon, not the daemon.

        Measured 2026-11-10: `agent.regression` builds a daemon to check the
        40-instance cap. A later suite rewrote a source file and restored it,
        which bumped its mtime past `PROCESS_START`; the watcher then re-exec'd
        the harness 15 s later. The run ended at 55 lines and exited 0 -- a
        truncated gate reporting success.
        """
        import sys
        entry = getattr(sys.modules.get("__main__"), "__file__", None) or ""
        self.assertNotIn(Path(entry).name, self.daemon.SERVICE_ENTRYPOINTS,
                         "this suite runs under %s; the guard's own premise is wrong" % entry)
        self.assertFalse(self.daemon._owns_this_process())

    def test_the_service_entrypoint_owns_its_process(self):
        """The production host is the one process the watcher may replace.

        `run_brain.py` is the entrypoint the host starts (it imports `agent.serve`
        and calls `main`), so a guard that only knew `serve.py` would have blocked
        the self-reload in the one process it exists for.
        """
        import sys
        saved = sys.modules.get("__main__")
        try:
            for name in ("run_brain.py", "serve.py", "brain_daemon.py"):
                stub = type(sys)("__main__")
                stub.__file__ = str(self.root / name)
                sys.modules["__main__"] = stub
                self.assertTrue(self.daemon._owns_this_process(), name)
            stub = type(sys)("__main__")
            stub.__file__ = str(self.root / "regression.py")
            sys.modules["__main__"] = stub
            self.assertFalse(self.daemon._owns_this_process())
        finally:
            if saved is not None:
                sys.modules["__main__"] = saved

    def test_a_process_the_daemon_does_not_own_never_re_execs(self):
        """The watcher must return, not fall through to `os.execv`."""
        import sys
        from unittest import mock
        saved = sys.modules.get("__main__")
        stub = type(sys)("__main__")
        stub.__file__ = str(self.root / "regression.py")
        sys.modules["__main__"] = stub
        calls, slept = [], []

        def fake_sleep(_seconds):
            slept.append(1)
            if len(slept) > 3:
                # The guard returns after one window; four means it kept looping
                # with a mocked execv, i.e. the guard did not fire at all.
                raise RuntimeError("the watcher looped instead of returning")

        try:
            with mock.patch("agent.brain_daemon.stale_modules",
                            return_value=[{"module": "agent.planner"}]), \
                    mock.patch("agent.brain_daemon.time.sleep", side_effect=fake_sleep), \
                    mock.patch("agent.brain_daemon.os.execv",
                               side_effect=lambda *a: calls.append(a)):
                try:
                    self.daemon._reload_watch()
                except RuntimeError as exc:
                    self.fail(str(exc))
            self.assertEqual(calls, [], "the watcher re-exec'd a process it does not own")
            self.assertEqual(len(slept), 1, "it should decide on the first stale window")
        finally:
            if saved is not None:
                sys.modules["__main__"] = saved
        self.temp.cleanup()

    def start(self, *names):
        result = self.daemon.start({"instances": list(names)})
        self.assertTrue(result["ok"])
        for name in names:
            self.assertTrue(self.pilots[name].entered.wait(3))
        return result

    def finish(self, name):
        self.pilots[name].release.set()
        self.pilots[name].cleanup_release.set()
        self.daemon._instances[name].thread.join(10)
        self.assertFalse(self.daemon._instances[name].alive())

    def test_the_real_pilot_factory_constructs(self):
        """Every other test in this suite stubs ``_make_pilot``, so nothing in the repo ever built a
        real pilot -- which is how ``explore_objectives=`` shipped on a constructor whose parameter is
        named ``explore``. The shipped HTTP start path raised TypeError for every instance while the
        gate stayed green, because the harness only ever exercised the stub.
        """
        from agent.autopilot import Autopilot
        from agent.brain_daemon import _DaemonAutopilot

        daemon = BrainDaemon(log_dir=self.root, recorder_dir=self.root)
        pilot = daemon._make_pilot("dofus-real", {
            "goals": ["do all the quests"], "steps": 0, "dry_run": True,
            "explore_objectives": 0.25, "policy": "rule"})
        self.assertIsInstance(pilot, Autopilot)
        self.assertIsInstance(pilot, _DaemonAutopilot)
        # the daemon's config key has to land on the parameter the loop actually reads
        self.assertAlmostEqual(float(pilot.controller.explore), 0.25)
        second = daemon._make_pilot("dofus-real-2", {"steps": 0, "dry_run": True})
        self.assertIsInstance(second, Autopilot)

    def test_the_step_budget_the_host_asks_for_is_the_budget_a_goal_gets(self):
        """`/brain/start {"steps": 40}` used to run 8.

        The host names a total and never a per-goal slice (`aipanel.cs` writes
        `"steps":N` only), so the factory's `or 8` quartered every request: measured
        live 2026-11-12 (session 75e47da4) a 40-step start produced 12 step rows -- 4
        memory refusals and 8 directives -- and ended "goals exhausted" with three
        objectives still ready. From outside that is indistinguishable from the agent
        stopping on its own.
        """
        daemon = BrainDaemon(log_dir=self.root, recorder_dir=self.root)
        asked = daemon._make_pilot("budget-a", {"steps": 40, "dry_run": True})
        self.assertEqual(asked.max_steps, 40)
        self.assertEqual(asked.steps_per_goal, 40,
                         "one goal must be allowed the whole budget the host named")
        explicit = daemon._make_pilot("budget-b", {"steps": 40, "steps_per_goal": 5,
                                                   "dry_run": True})
        self.assertEqual(explicit.steps_per_goal, 5, "an explicit slice still wins")
        legacy = daemon._make_pilot("budget-c", {"dry_run": True})
        self.assertEqual(legacy.steps_per_goal, 8,
                         "a request that names nothing keeps the old default")
        unlimited = daemon._make_pilot("budget-d", {"steps": 0, "dry_run": True})
        self.assertEqual(unlimited.max_steps, 0)
        self.assertEqual(unlimited.steps_per_goal, 8)

    def test_stop_returns_before_workers_finish(self):
        self.start("one", "two")
        returned = threading.Event()
        result = {}

        def stop():
            result.update(self.daemon.stop(stop_all=True))
            returned.set()

        request = threading.Thread(target=stop, daemon=True)
        self.requests.append(request)
        request.start()
        self.assertTrue(returned.wait(0.75), "stop blocked on worker cleanup")
        self.assertEqual({row["state"] for row in result["stopped"]}, {"stopping"})
        self.assertEqual(set(self.daemon.health()["active"]), {"one", "two"})
        for pilot in self.pilots.values():
            self.assertTrue(pilot.stop_file.exists())
        for name in self.pilots:
            self.finish(name)
        self.assertEqual({item.state for item in self.daemon._instances.values()}, {"stopped"})
        self.assertEqual(json.loads((self.root / "brain.lock").read_text(encoding="utf-8"))["instances"], [])

    def test_stop_one_does_not_stop_sibling(self):
        self.start("one", "two")
        self.daemon.stop(instance="one")
        self.assertTrue(self.pilots["one"].stop_file.exists())
        self.assertFalse(self.pilots["two"].stop_file.exists())
        self.finish("one")
        self.assertEqual(self.daemon._instances["one"].state, "stopped")
        self.assertEqual(self.daemon._instances["two"].state, "running")
        self.assertTrue(self.daemon._instances["two"].alive())

    def test_duplicate_start_reuses_worker(self):
        self.start("one")
        original = self.daemon._instances["one"]
        result = self.daemon.start({"instances": ["one", "one"]})
        self.assertTrue(result["ok"])
        self.assertIs(self.daemon._instances["one"], original)
        self.assertEqual(len(self.pilots), 1)

    def test_terminal_stop_is_idempotent(self):
        self.start("one")
        self.finish("one")
        before = self.daemon._instances["one"].status()
        self.daemon.stop(instance="one")
        self.daemon.stop(stop_all=True)
        after = self.daemon._instances["one"].status()
        self.assertEqual(before, after)
        self.assertEqual(after["state"], "finished")
        self.assertFalse(self.pilots["one"].stop_file.exists())

    def test_cleanup_keeps_instance_active(self):
        self.start("one")
        self.pilots["one"].release.set()
        self.assertTrue(self.pilots["one"].cleaning.wait(3))
        with self.daemon._lock:
            self.daemon._write_daemon_lock()
        status = self.daemon._instances["one"].status()
        self.assertEqual(status["state"], "running")
        self.assertIsNone(status["finished_at"])
        self.assertEqual(json.loads((self.root / "brain.lock").read_text(encoding="utf-8"))["instances"], ["one"])
        self.daemon.stop(instance="one")
        self.finish("one")
        self.assertEqual(self.daemon._instances["one"].state, "stopped")

    def test_failed_worker_cleanup_keeps_lock(self):
        from unittest.mock import patch

        def fail():
            raise RuntimeError("fixture failure")

        def factory(name, config):
            pilot = self.make_pilot(name, config)
            pilot.run = fail
            return pilot

        self.daemon._make_pilot = factory
        with patch("agent.brain_daemon.traceback.print_exc"):
            self.daemon.start({"instances": ["one"]})
            self.assertTrue(self.pilots["one"].cleaning.wait(3))
        with self.daemon._lock:
            self.daemon._write_daemon_lock()
        self.assertEqual(json.loads((self.root / "brain.lock").read_text(encoding="utf-8"))["instances"], ["one"])
        self.finish("one")
        self.assertEqual(self.daemon._instances["one"].state, "error")
        self.assertEqual(json.loads((self.root / "brain.lock").read_text(encoding="utf-8"))["instances"], [])
        self.daemon.stop(instance="one")
        self.assertEqual(self.daemon._instances["one"].state, "error")

    def test_stop_before_worker_entry_skips_run(self):
        pilot = self.make_pilot("one", {})
        pilot.cleanup_release.set()
        item = BrainInstance("one", pilot.goals, pilot.log_path, pilot.stop_file, pilot)
        item.state = "stopping"
        self.daemon._instances["one"] = item
        self.daemon._run_instance(item)
        self.assertFalse(pilot.entered.is_set())
        self.assertTrue(pilot.cleaning.is_set())
        self.assertEqual(item.state, "stopped")
        self.assertIsNone(item.summary)

    def test_a_finished_instance_releases_its_pilot(self):
        """The daemon is built to run for days, so a stopped instance must let go.

        Nothing ever removed an entry from `_instances`, so every finished instance kept
        its whole Autopilot -- controller, planner, recorder, learning store -- for the
        life of the process. Measured 2026-11-07: 40 completed runs retained 41.9 MB,
        growing linearly. The status row still has to answer "how many steps did it
        take", so the counters are copied out before the pilot is dropped.
        """
        import gc
        import weakref

        self.start("one")
        pilot = self.daemon._instances["one"].pilot
        pilot._step_total = 7
        ref = weakref.ref(pilot)

        self.finish("one")

        item = self.daemon._instances["one"]
        self.assertIsNone(item.pilot, "a finished instance still holds its Autopilot")
        self.assertEqual(item.status()["step_total"], 7,
                         "the counters were dropped with the pilot")
        del pilot
        # This suite's own factory keeps every pilot in `self.pilots`; that reference
        # has to go before collection can say anything about the daemon.
        self.pilots.pop("one", None)
        gc.collect()
        self.assertIsNone(ref(), "the Autopilot outlived its instance")

    def test_the_finished_table_stays_bounded(self):
        """A fresh instance name per session must not accumulate a row per session."""
        from unittest.mock import patch

        with patch("agent.brain_daemon.MAX_FINISHED_RECORDS", 4):
            for i in range(9):
                name = "churn-%03d" % i
                self.start(name)
                self.finish(name)

        self.assertLessEqual(len(self.daemon._instances), 5,
                             "the completed-instance table grew without bound")
        self.assertNotIn("churn-000", self.daemon._instances, "the oldest row was kept")
        self.assertIn("churn-008", self.daemon._instances, "the newest row was evicted")

    def test_pruning_never_removes_a_running_instance(self):
        """Eviction is for records whose loop ended; a live instance is never a candidate."""
        from unittest.mock import patch

        self.start("keeper")
        with patch("agent.brain_daemon.MAX_FINISHED_RECORDS", 2):
            for i in range(6):
                name = "temp-%03d" % i
                self.start(name)
                self.finish(name)

        self.assertIn("keeper", self.daemon._instances, "a running instance was evicted")
        self.assertTrue(self.daemon._instances["keeper"].alive())

    def test_http_dynamic_fleets_and_cooperative_stop(self):
        class Runtime:
            executor = None

        server = AgentHTTPServer(("127.0.0.1", 0), Runtime(), None, brain=self.daemon)
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base = "http://127.0.0.1:%d" % server.server_port

        def request(path, body=None):
            data = json.dumps(body).encode() if body is not None else None
            # Every real caller presents the run secret; a test client that does not is
            # testing the 401 path, not the fleet lifecycle.
            headers = {"Content-Type": "application/json"}
            headers.update(local_headers())
            req = urllib.request.Request(base + path, data=data, headers=headers)
            with opener.open(req, timeout=10) as response:
                return json.load(response)

        try:
            for count in (1, 7, 43):
                with self.subTest(instances=count):
                    names = ["fleet-%d-client-%d" % (count, i) for i in range(count)]
                    response = request("/brain/start", {"instances": names})
                    self.assertTrue(response["ok"])
                    self.assertEqual(len(response["instances"]), count)
                    for name in names:
                        self.assertTrue(self.pilots[name].entered.wait(3))
                    self.assertEqual(set(request("/brain/health")["active"]), set(names))
                    status = request("/brain/status")
                    self.assertEqual({row["instance"] for row in status["instances"] if row["alive"]},
                                     set(names))
                    response = request("/brain/stop", {"all": True})
                    self.assertTrue(response["ok"])
                    self.assertTrue(all(self.pilots[name].stop_file.exists() for name in names))
                    for name in names:
                        self.finish(name)
                    self.assertEqual(request("/brain/health")["active"], [])
        finally:
            server.shutdown()
            server.server_close()
            serving.join(3)


if __name__ == "__main__":
    unittest.main()
