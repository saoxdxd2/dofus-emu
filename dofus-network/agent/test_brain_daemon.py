import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from agent.brain_daemon import BrainDaemon, BrainInstance
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
        self.daemon = BrainDaemon(log_dir=self.root, recorder_dir=self.root)
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
        for item in self.daemon._instances.values():
            if item.thread:
                item.thread.join(3)
                self.assertFalse(item.thread.is_alive())
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
        self.daemon._instances[name].thread.join(3)
        self.assertFalse(self.daemon._instances[name].alive())

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
        self.assertEqual(json.loads((self.root / "brain.lock").read_text())["instances"], [])

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
        self.assertEqual(json.loads((self.root / "brain.lock").read_text())["instances"], ["one"])
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
        self.assertEqual(json.loads((self.root / "brain.lock").read_text())["instances"], ["one"])
        self.finish("one")
        self.assertEqual(self.daemon._instances["one"].state, "error")
        self.assertEqual(json.loads((self.root / "brain.lock").read_text())["instances"], [])
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
            req = urllib.request.Request(base + path, data=data,
                                         headers={"Content-Type": "application/json"})
            with opener.open(req, timeout=2) as response:
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
