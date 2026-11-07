"""The mirror's own rules, on a fixture tree.

`test_kaggle_toolkit_sync` asks about the real tree, which a mutation sweep cannot
use: the mutated file is *supposed* to differ from its mirror, so that test would
fail for every mutation. These tests exercise `plan`/`drift`/`write` on a temporary
root and toolkit instead, so the mirror rules stay mutation-testable.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import sync_kaggle_toolkit as toolkit  # noqa: E402


class ToolkitTests(unittest.TestCase):

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.root = root / "src"
        self.mirror = root / "tk"
        self.manifest = root / "TOOLKIT_MANIFEST.json"
        (self.root / "agent").mkdir(parents=True)
        (self.mirror / "agent").mkdir(parents=True)
        self.source = self.root / "agent" / "a.py"
        self.target = self.mirror / "agent" / "a.py"
        self.source.write_text("x = 1\n", encoding="utf-8")
        self.target.write_text("x = 0\n", encoding="utf-8")
        # A module the harness removed but the toolkit still ships, and the trimmed
        # map index the toolkit pins instead of copying.
        (self.mirror / "agent" / "legacy.py").write_text("old = 1\n", encoding="utf-8")
        (self.mirror / "data" / "maps").mkdir(parents=True)
        (self.mirror / "data" / "maps" / "_index.json").write_text("{}\n",
                                                                   encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_a_toolkit_only_module_is_pinned_not_deleted(self):
        rows = {item["path"]: item for item in toolkit.plan(self.root, self.mirror)}
        self.assertEqual(rows["agent/legacy.py"]["status"], "pinned")
        self.assertIsNone(rows["agent/legacy.py"]["source"])
        self.assertFalse(rows["agent/legacy.py"]["missing"])

    def test_the_trimmed_map_index_is_pinned(self):
        rows = {item["path"]: item for item in toolkit.plan(self.root, self.mirror)}
        self.assertEqual(rows["data/maps/_index.json"]["status"], "pinned")

    def test_a_different_source_is_named_as_drift(self):
        entries = toolkit.plan(self.root, self.mirror)
        self.assertEqual([item["path"] for item in toolkit.drift(entries)],
                         ["agent/a.py"])
        self.assertTrue(entries[0]["drift"])

    def test_write_copies_and_the_manifest_describes_the_tree_after_the_copy(self):
        entries = toolkit.plan(self.root, self.mirror)
        copied = toolkit.write(entries, self.root, self.mirror, self.manifest)
        self.assertEqual(copied, 1)
        self.assertEqual(self.target.read_text(encoding="utf-8"), "x = 1\n")
        payload = json.loads(self.manifest.read_text(encoding="utf-8"))
        row = payload["files"][0]
        self.assertEqual(row["path"], "agent/a.py")
        self.assertFalse(row["drift"])
        self.assertEqual(row["source_sha256"], row["toolkit_sha256"])
        self.assertEqual(toolkit.drift(toolkit.plan(self.root, self.mirror)), [])

    def test_an_unchanged_mirror_copies_nothing(self):
        self.target.write_text("x = 1\n", encoding="utf-8")
        entries = toolkit.plan(self.root, self.mirror)
        self.assertEqual(toolkit.drift(entries), [])
        self.assertEqual(toolkit.write(entries, self.root, self.mirror, self.manifest), 0)


if __name__ == "__main__":
    unittest.main(verbosity=1)
