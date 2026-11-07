"""An artifact may only be replaced when the previous bytes are recoverable."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.artifacts import (archive_previous, previous_path,  # noqa: E402
                             publish, restore_previous)


class ArtifactTests(unittest.TestCase):

    def test_archive_then_restore_returns_the_old_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "foresight.json"
            artifact.write_text('{"version": 1}', encoding="utf-8")
            kept = archive_previous(artifact)
            self.assertEqual(kept, previous_path(artifact))
            self.assertEqual(kept.name, "foresight.previous.json")
            artifact.write_text('{"version": 2}', encoding="utf-8")
            result = restore_previous(artifact)
            self.assertTrue(result["restored"])
            self.assertEqual(artifact.read_text(encoding="utf-8"),
                             '{"version": 1}')

    def test_nothing_to_archive_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "tool_stack.json"
            self.assertIsNone(archive_previous(artifact))
            self.assertFalse(previous_path(artifact).exists())

    def test_restore_without_a_previous_refuses(self):
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "tool_stack.json"
            artifact.write_text('{"current": true}', encoding="utf-8")
            result = restore_previous(artifact)
            self.assertFalse(result["restored"])
            self.assertEqual(result["reason"], "no_previous")
            self.assertEqual(artifact.read_text(encoding="utf-8"),
                             '{"current": true}')

    def test_publish_keeps_the_artifact_it_replaces(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "foresight.json"
            source = Path(temporary) / "seed0" / "foresight.json"
            source.parent.mkdir()
            target.write_text('{"version": "old"}', encoding="utf-8")
            source.write_text('{"version": "new"}', encoding="utf-8")
            publish(source, target)
            self.assertEqual(target.read_text(encoding="utf-8"),
                             '{"version": "new"}')
            self.assertEqual(previous_path(target).read_text(encoding="utf-8"),
                             '{"version": "old"}')
            restore_previous(target)
            self.assertEqual(target.read_text(encoding="utf-8"),
                             '{"version": "old"}')

    def test_the_archive_keeps_the_last_promoted_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "foresight.json"
            for version in (1, 2):
                artifact.write_text('{"version": %d}' % version, encoding="utf-8")
                archive_previous(artifact)
            artifact.write_text('{"version": 3}', encoding="utf-8")
            restore_previous(artifact)
            self.assertEqual(artifact.read_text(encoding="utf-8"),
                             '{"version": 2}')


if __name__ == "__main__":
    unittest.main(verbosity=1)
