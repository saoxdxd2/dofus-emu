"""The planning stack finally has a production caller, and this is what it must not break.

Measured 2026-11-13 (G126): `serve.py --schedule` is the only site that built the
stack and nothing launches that flag. The live loop is `run_autopilot.py`. Its new
tap records outcomes; it must not fork the posterior store, write into the tree that
`setup.exe` stages, raise into the loop, or change a single tool decision.

    python -m unittest planning.test_live_tap
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent.executor import ToolResult
from planning.bandit import ARMS
from planning.experience import ExperienceStore
from planning.live_tap import ARM_BY_TOOL, STATE_NAMES, attach
from planning.schedule import STATE_FILE as REPO_SCHEDULE_STATE


def call(tool: str, **args):
    return SimpleNamespace(tool=tool, args=args)


def result(tool: str, data=None, ok: bool = True, latency_ms: float = 1200.0):
    return ToolResult(tool=tool, ok=ok, data=data,
                      error=None if ok else "backend refused",
                      latency_ms=latency_ms, source="live")


class TapFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "appdata" / "DofusFarm"
        self.store = ExperienceStore(log_path=self.root / "experience.jsonl",
                                     snapshot_path=self.root / "posteriors.json")
        self.tap = attach(self.store, root=self.root, env={})
        self.assertIsNotNone(self.tap, "attach must build a real tap")

    def tearDown(self):
        self.tmp.cleanup()


class VocabularyTests(unittest.TestCase):
    def test_every_mapped_arm_is_a_real_arm(self):
        self.assertTrue(set(ARM_BY_TOOL.values()) <= set(ARMS),
                        "a mapped tool would silently never learn")

    def test_every_mapped_tool_is_in_the_28_tool_vocabulary(self):
        caps = json.loads((Path(__file__).resolve().parents[1] / "data" /
                           "capabilities.json").read_text(encoding="utf-8"))
        ids = {c["id"] for c in caps["capabilities"]}
        self.assertEqual(len(ids), 28)
        self.assertEqual(set(ARM_BY_TOOL) - ids, set(),
                         "the tap names a tool the harness cannot execute")

    def test_read_only_and_navigation_tools_are_not_claimed_as_evidence(self):
        for tool in ("state.snapshot", "nav.goto", "quest.list", "knowledge.search",
                     "farm.stop", "fight.pass", "ui.tap"):
            self.assertNotIn(tool, ARM_BY_TOOL)


class AttachTests(TapFixture):
    def test_the_kill_switch_returns_none(self):
        self.assertIsNone(attach(self.store, root=self.root,
                                 env={"DOFUS_SCHEDULE_TAP": "0"}))

    def test_a_missing_store_returns_none(self):
        self.assertIsNone(attach(None, root=self.root, env={}))

    def test_a_broken_root_returns_none_instead_of_raising(self):
        # A path that is a regular file cannot become a directory.
        bad = Path(self.tmp.name) / "a-file"
        bad.write_text("x", encoding="utf-8")
        self.assertIsNone(attach(self.store, root=bad, env={}))

    def test_one_store_is_shared_by_every_consumer(self):
        # Forking this store is the exact bug tools/test_online_learning_wiring.py warns about.
        self.assertIs(self.tap.manager.economy.experience, self.store)
        self.assertIs(self.tap.manager.economy.cost.experience, self.store)
        self.assertIs(self.tap.manager.ranker.store, self.store)

    def test_every_state_file_lives_under_the_app_data_root(self):
        for name in STATE_NAMES:
            path = self.tap.paths[name]
            self.assertEqual(path.parent, self.root)
            self.assertNotIn("dofus-network", str(path))
        self.assertNotEqual(REPO_SCHEDULE_STATE.parent, self.root)


class LearningTests(TapFixture):
    def test_a_verified_farm_result_moves_posteriors(self):
        rep = self.tap.observe(
            call("fight.join", monster_id=68),
            result("fight.join", {"monster_id": 68, "item_id": 1001,
                                  "kills": 20, "drops": 1}),
            elapsed_ms=180000.0, observation={"level": 60})
        self.assertEqual(rep["signal"], "verified")
        self.assertEqual(len(rep["updates"]), 2)
        keys = {u["param"] for u in rep["updates"]}
        self.assertIn("drop:1001", keys)
        self.assertIn("kpm:68:51-90", keys,
                      "the observation's level must reach the posterior bucket")
        self.assertFalse(any(k.startswith("kpm:68:1-20") for k in keys),
                         "without the observation's level every run lands in level 1")

    def test_ids_discovered_in_the_result_are_not_dropped(self):
        # `_ingest` used to pass the caller's bare `entity` to `_learn`, so ids that
        # arrived inside result["data"] never reached a posterior.
        rep = self.tap.observe(call("farm.start"), result(
            "farm.start", {"monster_id": 31, "item_id": 77,
                           "kills": 10, "drops": 2, "minutes": 2.0}),
            elapsed_ms=120000.0)
        self.assertEqual(rep["signal"], "verified")
        self.assertTrue(rep["updates"], "discovered entity ids must reach _learn")

    def test_a_bare_ok_learns_nothing_but_is_still_traced(self):
        rep = self.tap.observe(call("fight.join"), result("fight.join", None),
                               elapsed_ms=1000.0)
        self.assertEqual(rep["signal"], "unverified")
        self.assertEqual(rep["updates"], [])
        self.assertTrue(self.tap.paths["traces.jsonl"].exists())

    def test_a_failed_call_is_not_credited(self):
        rep = self.tap.observe(call("farm.start"),
                               result("farm.start", {"kills": 99}, ok=False),
                               elapsed_ms=1000.0)
        self.assertEqual(rep["signal"], "failed")
        self.assertEqual(rep["updates"], [])

    def test_state_files_are_written_and_the_store_survives_a_reload(self):
        self.tap.observe(call("fight.join"), result(
            "fight.join", {"monster_id": 9, "item_id": 5, "kills": 4, "drops": 1}),
            elapsed_ms=60000.0)
        for name in ("traces.jsonl", "decision_graph.json", "economy_state.json",
                     "thinking_cache.json"):
            self.assertTrue(self.tap.paths[name].exists(), name)
        again = ExperienceStore(log_path=self.root / "experience.jsonl",
                                snapshot_path=self.root / "posteriors.json")
        self.assertEqual(len(again), len(self.store),
                         "a new process must see the posteriors this one learned")

    def test_observe_never_raises_into_the_loop(self):
        class Exploding:
            def as_dict(self):
                raise RuntimeError("the backend returned something exotic")

        self.assertIsNone(self.tap.observe(call("farm.start"), Exploding()))
        self.assertEqual(self.tap.failed, 1)
        # and the very next step still works
        self.assertIsNotNone(self.tap.observe(
            call("state.snapshot"), result("state.snapshot", {})))

    def test_counters_report_what_happened(self):
        self.tap.observe(call("fight.join"), result(
            "fight.join", {"item_id": 1, "kills": 3, "drops": 1}), elapsed_ms=60000.0)
        self.tap.observe(call("state.snapshot"), result("state.snapshot", {}),
                         elapsed_ms=10.0)
        s = self.tap.stats()
        self.assertEqual(s["observed"], 2)
        self.assertGreaterEqual(s["learned"], 1)
        self.assertEqual(s["failed"], 0)


class DecisionBoundaryTests(TapFixture):
    def test_no_objective_exists_until_someone_explicitly_submits_a_goal(self):
        # This is the safety property: the tap records, it does not choose.
        self.assertIsNone(self.tap.objective())
        self.assertFalse(self.tap.stats()["objective"])

    def test_recording_actions_never_creates_a_plan(self):
        for i in range(5):
            self.tap.observe(call("quest.advance", quest_id=i),
                             result("quest.advance", {"quest_id": i}))
        self.assertIsNone(self.tap.objective())
        self.assertIsNone(self.tap.manager.plan)

    def test_an_explicit_goal_does_produce_a_plan_and_an_objective(self):
        self.tap.submit_goal(1001, 3, level=60)
        obj = self.tap.objective()
        self.assertIsNotNone(obj)
        self.assertEqual(obj["tool"], "farm.start")

    def test_submit_then_record_then_restore_keeps_the_schedule_state(self):
        # Two measured bugs (2026-11-13): the economy's save used to overwrite the
        # schedule's restore file, and `PlayerState.as_dict` used to emit a key the
        # constructor rejects. Both would make this round trip fail.
        self.tap.submit_goal(1001, 3, level=60)
        self.tap.observe(call("fight.join"), result(
            "fight.join", {"item_id": 1001, "kills": 5, "drops": 1}))
        state = self.tap.paths["schedule_state.json"]
        keys = set(json.loads(state.read_text(encoding="utf-8")))
        self.assertIn("goal", keys)
        self.assertNotIn("events_seen", keys, "the economy overwrote the schedule")
        fresh = attach(self.store, root=self.root, env={})
        self.assertIsNotNone(fresh.manager.restore())
        self.assertIsNotNone(fresh.objective())


if __name__ == "__main__":
    unittest.main()
