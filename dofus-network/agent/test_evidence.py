import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.autopilot import Autopilot
from agent.controller import Controller, Decision
from agent.evidence import EvidenceMemory
from agent.persistent_memory import PersistentMemory
from agent.recorder import TrajectoryRecorder
from agent.runtime import AgentRuntime, ToolCall
from agent.executor import ToolResult
from agent import autopilot as ap
from planning.experience import Event, ExperienceStore
from tools.build_live_transitions import _attribute_lagged, segments, label_steps

CTX = (42, 6, 1, 7)


def event(action="A", sequence=1, stage="walk_confirmed", error="", epoch="page"):
    return {"action_id": action, "source_epoch": epoch, "source_sequence": sequence,
            "stage": stage, "error": error, "channel": "__agentLastNav"}


def snapshot(sequence, events=(), epoch="page", cell=1):
    return {"source_snapshot": {"epoch": epoch, "sequence": sequence},
            "action_evidence": copy.deepcopy(list(events)),
            "position": {"map_id": 7, "cell": cell}, "in_world": True,
            "in_dialog": False, "in_fight": False}


class EvidenceTests(unittest.TestCase):
    def memory(self, **capacities):
        memory = EvidenceMemory(**capacities)
        memory.ingest(snapshot(1))
        memory.register("A", "nav.goto", {"cell": 2}, CTX)
        return memory

    def test_missing_support_is_unknown(self):
        memory = self.memory()
        memory.observe_outcome("A", "failed")
        self.assertIsNone(memory.belief("A")["verdict"])
        self.assertEqual(memory.failure_count(CTX, "nav.goto", {"cell": 2}), 0)

    def test_duplicate_is_idempotent(self):
        memory = self.memory()
        state = snapshot(2, [event()])
        first = memory.ingest(state)
        self.assertEqual(first.revisions[-1]["verdict"], "advanced")
        revision = memory.belief("A")["revision"]
        self.assertEqual(memory.ingest(state).status, "duplicate")
        self.assertEqual(memory.ingest(snapshot(3, [event()])).revisions, [])
        self.assertEqual(memory.belief("A")["revision"], revision)

    def test_source_order_not_arrival_order(self):
        memory = self.memory()
        memory.ingest(snapshot(4, [event(sequence=3)]))
        result = memory.ingest(snapshot(2, [event(sequence=2, stage="walk_failed", error="no path")]))
        self.assertEqual(result.status, "late")
        self.assertFalse(result.adopt)
        self.assertEqual(memory.belief("A")["verdict"], "advanced")
        self.assertEqual(memory.belief("A")["alternatives"], ["advanced", "failed"])

    def test_late_failure_revises_only_matching_action(self):
        memory = self.memory()
        memory.register("B", "nav.goto", {"cell": 3}, CTX)
        memory.observe_outcome("A", "advanced")
        memory.ingest(snapshot(5))
        revision = memory.ingest(snapshot(3, [event(stage="walk_failed", error="A failed")]))
        self.assertEqual(revision.revisions[-1]["action_id"], "A")
        self.assertEqual(memory.belief("A")["verdict"], "failed")
        self.assertIsNone(memory.belief("B")["verdict"])

    def test_conflict_retracts_without_adding_support(self):
        memory = self.memory()
        memory.ingest(snapshot(2, [event()]))
        memory.ingest(snapshot(3, [event(stage="walk_failed", error="contradiction")]))
        self.assertEqual(memory.belief("A")["status"], "conflicted")
        self.assertIsNone(memory.belief("A")["verdict"])
        self.assertEqual(len(memory.events), 1)

    def test_snapshot_conflict_does_not_poison_unchanged_callback(self):
        memory = self.memory()
        memory.ingest(snapshot(2, [event()], cell=2))
        conflict = memory.ingest(snapshot(2, [event()], cell=3))
        self.assertEqual(conflict.status, "conflict")
        self.assertEqual(memory.belief("A")["verdict"], "advanced")

    def test_epoch_reset_does_not_accept_old_page(self):
        memory = self.memory()
        memory.ingest(snapshot(1, epoch="new"))
        self.assertEqual(memory.ingest(snapshot(3)).status, "retired_epoch")
        self.assertEqual(memory.epoch, "new")
        self.assertIsNone(memory.belief("A")["verdict"])

    def test_missing_time_is_not_receipt_time(self):
        memory = self.memory()
        malformed = snapshot(2, [event()])
        del malformed["action_evidence"][0]["source_sequence"]
        memory.ingest(malformed)
        self.assertIsNone(memory.belief("A")["verdict"])
        self.assertEqual(memory.ingest({"cell": 2}).status, "unversioned")

    def test_callback_watermark_is_enforced(self):
        memory = self.memory()
        state = snapshot(2, [event(sequence=10)])
        state["source_snapshot"]["evidence_sequence"] = 3
        memory.ingest(state)
        self.assertIsNone(memory.belief("A")["verdict"])

    def test_window_is_bounded_and_old_frames_are_ignored(self):
        memory = self.memory(frames=3, events=3, actions=3)
        for i in range(2, 30):
            memory.register(str(i), "nav.goto", {"cell": i}, CTX)
            memory.ingest(snapshot(i, [event(str(i), i)]))
        self.assertLessEqual(len(memory.frames), 3)
        self.assertLessEqual(len(memory.events), 3)
        self.assertLessEqual(len(memory.actions), 3)
        self.assertEqual(memory.ingest(snapshot(2, [event("29", 2)])).status, "outside_window")

    def test_snapshot_ring_retains_a_callback_overwritten_before_poll(self):
        memory = self.memory()
        memory.register("B", "nav.goto", {"cell": 3}, CTX)
        memory.ingest(snapshot(2, [event("A", 1, "walk_failed", "A failed"), event("B", 2)]))
        self.assertEqual(memory.belief("A")["verdict"], "failed")
        self.assertEqual(memory.belief("B")["verdict"], "advanced")

    def test_identity_is_immutable(self):
        memory = self.memory()
        with self.assertRaises(ValueError):
            memory.register("A", "nav.goto", {"cell": 99}, CTX)
        belief = memory.belief("A")
        belief["args"]["cell"] = 99
        self.assertEqual(memory.belief("A")["args"]["cell"], 2)

    def test_checkpoint_carries_revision_history(self):
        memory = self.memory()
        memory.ingest(snapshot(2, [event()]))
        restored = EvidenceMemory()
        restored.load(memory.dumps())
        self.assertEqual(restored.ingest(snapshot(2, [event()])).status, "duplicate")
        restored.ingest(snapshot(3, [event(stage="walk_failed", error="conflict")]))
        self.assertIsNone(restored.belief("A")["verdict"])

    def test_persistent_counts_are_replaceable_not_additive(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = self.memory()
            store = PersistentMemory(Path(directory) / "memory.json")
            failure = memory.ingest(snapshot(2, [event(stage="walk_failed", error="no path")])).revisions[-1]
            store.replace_outcome(failure)
            store.replace_outcome(failure)
            self.assertEqual(store.failure_count(CTX, "nav.goto", {"cell": 2}), 1)
            success = memory.ingest(snapshot(3, [event(sequence=2)])).revisions[-1]
            store.replace_outcome(success)
            self.assertEqual(store.failure_count(CTX, "nav.goto", {"cell": 2}), 0)
            self.assertEqual(store.best_solution(CTX)["wins"], 1)
            store.save()
            restored = PersistentMemory(store.path)
            restored.replace_outcome(success)
            self.assertEqual(restored.best_solution(CTX)["wins"], 1)
            conflict = memory.ingest(snapshot(4, [event(sequence=2, stage="walk_failed", error="conflict")])).revisions[-1]
            restored.replace_outcome(conflict)
            self.assertIsNone(restored.best_solution(CTX))
            self.assertEqual(restored.failure_count(CTX, "nav.goto", {"cell": 2}), 0)

    def test_controller_credit_is_reversible(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(session="test")
            controller.state_dir = Path(directory)
            decision = Decision("recover", "stale_state", "refresh")
            controller.observe_outcome(decision, "failed", outcome_id="A")
            controller.observe_outcome(decision, "failed", outcome_id="A")
            key = next(iter(controller._strategies))
            self.assertEqual(controller._strategies[key].attempts, 1)
            controller.revise_outcome("A", "advanced")
            self.assertEqual(controller._strategies[key].successes, 1)
            controller.save(force=True)
            restored = Controller(session="test")
            restored.state_dir = Path(directory)
            restored.load()
            restored.revise_outcome("A", None)
            self.assertEqual(restored._strategies[key].attempts, 0)
            self.assertEqual(restored._strategies[key].successes, 0)

    def test_experience_snapshot_and_replay_agree_after_retraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ExperienceStore(root / "log.jsonl", root / "state.json")
            store.record(Event(kind="beta", key="repair:test", value=1))
            store.record_outcome("repair:test", "A", 0, 1)
            store.record_outcome("repair:test", "A", 0, 1)
            self.assertEqual(store.get("repair:test").n, 2)
            store.record_outcome("repair:test", "A", 1, 2)
            store.record_outcome("repair:test", "A", None, 3)
            self.assertEqual(store.get("repair:test").n, 1)
            store.save()
            before = store.get("repair:test").to_dict()
            restored = ExperienceStore(store.log_path, store.snapshot_path)
            self.assertEqual(restored.get("repair:test").to_dict(), before)
            restored.replay()
            self.assertEqual(restored.get("repair:test").to_dict(), before)

    def test_runtime_keeps_current_state_but_reconstructs_late_credit(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = TrajectoryRecorder(Path(directory), "unit")
            runtime = AgentRuntime(recorder=recorder)
            runtime.note_observation(snapshot(1))
            runtime.evidence.register("A", "nav.goto", {"cell": 2}, CTX)
            runtime.note_observation(snapshot(5, cell=5))
            received_at = runtime.last_observation_ts
            self.assertFalse(runtime.note_observation(snapshot(3, [event(stage="walk_failed", error="late")], cell=3)))
            self.assertEqual(runtime.current_observation()[0]["position"]["cell"], 5)
            self.assertEqual(runtime.last_observation_ts, received_at)
            self.assertEqual(runtime.drain_evidence_revisions()[-1]["verdict"], "failed")
            runtime.save_evidence()
            restored = AgentRuntime(recorder=recorder)
            self.assertEqual(restored.evidence.belief("A")["verdict"], "failed")

    def test_real_autopilot_receipt_updates_durable_and_controller_credit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = AgentRuntime(recorder=TrajectoryRecorder(root, "unit"))
            pilot = Autopilot(runtime, ["go"], log_path=root / "pilot.jsonl",
                              stop_file=root / "STOP", memory_path=root / "memory.json", preflight=False)
            pilot.controller.state_dir = root
            runtime.note_observation(snapshot(1))
            runtime.evidence.register("A", "nav.goto", {"cell": 2}, CTX)
            decision = Decision("recover", "stale_state", "refresh")
            pilot.controller.observe_outcome(decision, None, outcome_id="A")
            pilot._note_experience = lambda *a, **k: None
            runtime.note_observation(snapshot(2, [event(stage="walk_failed", error="no path")]))
            pilot._sync_evidence_credit()
            key = next(iter(pilot.controller._strategies))
            self.assertEqual(pilot.controller._strategies[key].attempts, 1)
            self.assertEqual(pilot._memory.failure_count(CTX, "nav.goto", {"cell": 2}), 1)
            runtime.note_observation(snapshot(4, [event(sequence=2)]))
            pilot._sync_evidence_credit()
            self.assertEqual(pilot.controller._strategies[key].attempts, 1)
            self.assertEqual(pilot.controller._strategies[key].successes, 1)
            self.assertEqual(pilot._memory.failure_count(CTX, "nav.goto", {"cell": 2}), 0)

    def test_actual_run_registers_and_credits_source_identified_action_once(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recorder = TrajectoryRecorder(root, "live-shaped")
            first = snapshot(1)
            sent = snapshot(2, [event(stage="walking")])
            arrived = snapshot(3, [event(sequence=2)], cell=2)
            calls = []
            def execute(tool, args, **kwargs):
                calls.append(tool)
                result = {"directive_payload": {"id": "A"}, "snapshot": sent}
                recorder.step(tool, args, True, result=result, observation=first, source="live")
                return ToolResult(tool=tool, ok=True, data=result)
            executor = SimpleNamespace(execute=execute, snapshot=lambda **kwargs: arrived)
            policy = SimpleNamespace(choose=lambda goal, obs, tools: ToolCall("nav.goto", {"cell": 2}, goal),
                                     note_result=lambda *a, **k: None)
            runtime = AgentRuntime(executor=executor, recorder=recorder, policy=policy)
            runtime.note_observation(first)
            pilot = Autopilot(runtime, ["go"], log_path=root / "pilot.jsonl", stop_file=root / "STOP",
                              memory_path=root / "memory.json", preflight=False, max_steps=1, think_s=0)
            pilot.controller.state_dir = root
            pilot.experience = ExperienceStore(root / "experience.jsonl", root / "posterior.json")
            pilot._pending = Decision("recover", "stale_state", "refresh")
            class Clock:
                t = 1000.0
                def time(self): return self.t
                def monotonic(self): return self.t
                def perf_counter(self): return self.t
                def sleep(self, seconds): self.t += seconds
            with patch.object(ap, "time", Clock()):
                summary = pilot._run()
            self.assertEqual(calls, ["nav.goto"])
            self.assertEqual(summary["steps"], 1)
            self.assertEqual(runtime.evidence.belief("A")["verdict"], "advanced")
            self.assertEqual(pilot.experience.get("repair:stale_state:recover").n, 1)
            rows = [json.loads(line) for line in recorder.path.read_text(encoding="utf-8").splitlines()]
            result = label_steps(segments(rows, "live-shaped", 120)[0])[0]
            self.assertEqual(result["credit_verdict"], "advanced")
            self.assertTrue(result["claim_met"])
            self.assertTrue(result["moved"])

    def test_malformed_checkpoint_does_not_replace_current_memory(self):
        memory = self.memory()
        for value in ([], {"actions": [{}]}, {"actions": [], "frames": {"1": None}}):
            with self.assertRaises(ValueError):
                memory.load(json.dumps(value))
            self.assertIsNotNone(memory.belief("A"))

    def test_unknown_outcomes_are_excluded_by_forecast_readers(self):
        from ai.train_foresight import load_live
        from ai.train_move_head import load_transitions
        from agent.features import FEATURE_COUNT
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "train.jsonl"
            rows = [dict(x=[0]*FEATURE_COUNT, has_pair=True, was_read=False, tool="nav.goto",
                         source_identified=True, credit_eligible=known, moved=known,
                         progress=False, segment="unit") for known in (False, True)]
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            self.assertEqual(len(load_live([path])["rows"]), 1)
            self.assertEqual(len(load_transitions(path.parent, "train")["x"]), 1)

    def test_unattributed_read_is_not_given_to_last_action(self):
        rows = [dict(action_id=action, tool="nav.goto", moved=False, progress=False,
                     advance=False, was_read=False, moved_keys=[]) for action in ("A", "B")]
        rows.append(dict(tool="state.snapshot", moved=True, progress=False, advance=False,
                         was_read=True, moved_keys=["cell"]))
        _attribute_lagged(rows)
        self.assertFalse(rows[0]["moved"])
        self.assertFalse(rows[1]["moved"])
        self.assertTrue(rows[2]["moved_observed"])
        self.assertFalse(rows[2]["moved"])

    def test_builder_joins_latest_revision_not_log_neighbor(self):
        step = {"kind": "step", "tool": "nav.goto", "args": {"cell": 2}, "ok": True,
                "instance": "unit", "goal": "go", "observation": snapshot(1), "ts": 1,
                "result": {"directive_payload": {"id": "A"}, "snapshot": snapshot(2)}}
        records = [step, {"kind": "action_evidence", "instance": "unit", "action_id": "A",
                          "revision": 2, "status": "confirmed", "verdict": "failed"},
                   {"kind": "action_evidence", "instance": "unit", "action_id": "A",
                    "revision": 1, "status": "unconfirmed", "verdict": None}]
        result = label_steps(segments(records, "test", 120)[0])[0]
        self.assertEqual(result["credit_verdict"], "failed")
        self.assertEqual(result["evidence_revision"], 2)
        self.assertFalse(result["claim_met"])

    def test_builder_preserves_unknown_instead_of_counting_a_miss(self):
        before = snapshot(1)
        result = label_steps({"source": "test", "instance": "unit", "goal": "go", "steps": [
            {"tool": "nav.goto", "args": {"cell": 2}, "ok": True, "observation": before,
             "result": {"directive_payload": {"id": "A"}, "snapshot": snapshot(2)}}]})[0]
        self.assertIsNone(result["claim_met"])
        self.assertFalse(result["credit_eligible"])


if __name__ == "__main__":
    unittest.main()
