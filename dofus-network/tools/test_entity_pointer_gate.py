import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.schema import tool_names
from tools import build_entity_pointer_dataset as builder


def manifest(actions=2000, progress=50):
    return {"action_rows": actions, "action_progress_rows": progress,
            "tools": {name: 10 for name in tool_names()}}


def step(instance="client", tool="quest.advance", ts=1.0, source="live"):
    return {"kind": "step", "source": source, "instance": instance, "ts": ts,
            "tool": tool, "args": {}, "goal": "quests", "ok": True,
            "observation": {"in_world": True, "objectives_completed": 0},
            "result": {"snapshot": {"in_world": True, "objectives_completed": 1}}}


class PointerCoverageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pointer-gate-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.output = self.root / "existing"
        self.output.mkdir()
        self.existing = self.output / "train.jsonl"
        self.existing.write_text("keep existing dataset\n", encoding="utf-8")
        output = patch("sys.stdout", new=io.StringIO())
        output.start()
        self.addCleanup(output.stop)

    def log(self, name, rows):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return path

    def test_exact_coverage_floors_pass(self):
        gate = builder.coverage_gate(manifest())
        self.assertTrue(gate["passed"])
        self.assertEqual(gate["undercovered_tools"], {})

    def test_action_rows_not_total_rows_control_gate(self):
        data = manifest(actions=1999)
        data.update(rows=100000, read_rows=98001)
        self.assertFalse(builder.coverage_gate(data)["passed"])

    def test_read_progress_cannot_satisfy_action_progress(self):
        data = manifest(progress=49)
        data["progress"] = 1000
        self.assertFalse(builder.coverage_gate(data)["passed"])

    def test_every_tool_must_reach_ten_rows(self):
        for name in tool_names():
            with self.subTest(tool=name):
                data = manifest()
                data["tools"][name] = 9
                gate = builder.coverage_gate(data)
                self.assertFalse(gate["passed"])
                self.assertEqual(gate["undercovered_tools"], {name: 9})

    def test_missing_tool_is_reported_with_zero_rows(self):
        data = manifest()
        del data["tools"]["hdv.buy"]
        self.assertEqual(builder.coverage_gate(data)["undercovered_tools"], {"hdv.buy": 0})

    def test_build_counts_reads_separately_from_action_progress(self):
        path = self.log("rows.jsonl", [step(), step(tool="state.snapshot", ts=2)])
        rows, data = builder.build([path], 120)
        self.assertEqual(len(rows), 2)
        self.assertEqual(data["read_rows"], 1)
        self.assertEqual(data["action_rows"], 1)
        self.assertEqual(data["action_progress_rows"], 1)
        self.assertFalse(data["coverage_gate"]["passed"])

    def test_duplicates_and_repeated_paths_do_not_inflate_counts(self):
        first = self.log("a.jsonl", [step()])
        second = self.log("b.jsonl", [step(), step(ts=2)])
        rows, data = builder.build([first, first, second], 120)
        self.assertEqual(len(rows), 2)
        self.assertEqual(data["duplicate_steps"], 1)
        self.assertEqual(len(data["sources"]), 2)

    def test_nonlive_or_unattributed_steps_are_excluded(self):
        missing = step()
        del missing["source"]
        path = self.log("rows.jsonl", [step(source="synthetic"), step(source="dry_run"), missing])
        rows, data = builder.build([path], 120)
        self.assertEqual(rows, [])
        self.assertEqual(data["action_rows"], 0)

    def test_instances_never_share_segments_or_carried_observations(self):
        missing = step("second", ts=2)
        del missing["observation"]
        path = self.log("rows.jsonl", [step("first"), missing, step("second", ts=3)])
        rows, data = builder.build([path], 120)
        self.assertEqual([row["instance"] for row in rows], ["first", "second"])
        self.assertEqual(len({row["segment"] for row in rows}), 2)
        self.assertEqual(data["action_rows"], 2)

    def test_missing_outcome_cannot_reuse_previous_snapshot(self):
        missing = step(ts=2)
        missing["result"] = {}
        path = self.log("rows.jsonl", [step(), missing])
        rows, _ = builder.build([path], 120)
        self.assertEqual(len(rows), 1)

    def test_failed_gate_preserves_existing_files(self):
        path = self.log("rows.jsonl", [step()])
        before = {item.name: item.read_bytes() for item in self.output.iterdir()}
        self.assertEqual(builder.main(["--log", str(path), "--out", str(self.output)]), 1)
        self.assertEqual({item.name: item.read_bytes() for item in self.output.iterdir()}, before)

    def test_check_never_creates_output_even_when_coverage_passes(self):
        path = self.log("rows.jsonl", [])
        data = manifest()
        data["coverage_gate"] = builder.coverage_gate(data)
        output = self.root / "not-created"
        with patch.object(builder, "build", return_value=([], data)):
            self.assertEqual(builder.main(["--log", str(path), "--out", str(output), "--check"]), 0)
        self.assertFalse(output.exists())

    def test_default_discovery_includes_all_instance_logs(self):
        first = self.log("trajectories/any-client-name.jsonl", [step("any-client-name")])
        second = self.log("trajectories/server-instance-43.jsonl", [step("server-instance-43")])
        self.log("trajectories/ignored.stats.json", [])
        data = manifest()
        data["coverage_gate"] = builder.coverage_gate(data)
        with patch.object(builder, "REPO_ROOT", self.root), \
                patch.object(builder, "build", return_value=([], data)) as build:
            self.assertEqual(builder.main(["--check"]), 0)
        self.assertEqual(set(build.call_args.args[0]), {first, second})


if __name__ == "__main__":
    unittest.main()
