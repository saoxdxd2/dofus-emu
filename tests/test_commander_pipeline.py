import unittest
import json
import subprocess
import time
import urllib.request
import os
import sys

class TestCommanderPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cls.sentinel_script = os.path.join(base_dir, "dofus-network", "networkhealth", "sentinel.py")
        
        # Start sentinel daemon
        cls.proc = subprocess.Popen(
            [sys.executable, cls.sentinel_script],
            cwd=base_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # Wait up to 5 seconds for daemon to respond
        for _ in range(25):
            try:
                with urllib.request.urlopen("http://127.0.0.1:8890/health", timeout=1) as resp:
                    if resp.status == 200:
                        break
            except Exception:
                time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        if cls.proc and cls.proc.poll() is None:
            cls.proc.terminate()
            try:
                cls.proc.wait(timeout=3)
            except Exception:
                cls.proc.kill()

    def _send_cmd(self, command_text: str, target: str = "slot-1") -> dict:
        req = urllib.request.Request(
            "http://127.0.0.1:8890/sentinel/parse_command",
            data=json.dumps({"command": command_text, "target_slot": target}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_follow_command(self):
        res = self._send_cmd("can you follow the leader please?", "slot-2")
        self.assertEqual(res.get("directive"), "FOLLOW")
        self.assertGreaterEqual(res.get("confidence", 0), 0.90)

    def test_combat_command(self):
        res = self._send_cmd("join combat right now", "all")
        self.assertEqual(res.get("directive"), "AUTO_JOIN_COMBAT")

    def test_pass_turn_command(self):
        res = self._send_cmd("passe mon tour stp", "slot-3")
        self.assertEqual(res.get("directive"), "PASS_TURN")

    def test_farm_mobs_command(self):
        res = self._send_cmd("can you farm this mobs?", "slot-1")
        self.assertEqual(res.get("directive"), "AUTO_FARM")

    def test_stop_command(self):
        res = self._send_cmd("stop all operations", "all")
        self.assertEqual(res.get("directive"), "STOP")

if __name__ == "__main__":
    unittest.main()
