"""The menu-to-window table is learned, not asserted.

`_UI_WINDOWS = {"Bag": "equipment"}` was a fact about the game written into Python because a
live session needed it that evening. It should not be there: `ui.tap` already reports
`open_before` and `open_windows`, so one successful press says which window an icon opens. The
constant is now a *seed* (the one pair that was measured), the loop learns the rest, and the
knowledge base carries them across restarts.

These checks drive the real `Autopilot` object and the real `KnowledgeStore`; no client is
needed, because the producer reads an observation and a tool call.

    python tools/test_menu_window_learning.py
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "dofus-network"))

from agent.autopilot import Autopilot  # noqa: E402
from agent.executor import ToolExecutor  # noqa: E402
from agent.planner import QuestPlanner  # noqa: E402
from agent.runtime import AgentRuntime, ToolCall  # noqa: E402


def windows(*names):
    return {"open_windows": list(names)}


class MenuWindowLearning(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.kb = self.root / "knowledge.jsonl"
        self.executor = ToolExecutor(knowledge_store=self.kb)
        runtime = AgentRuntime(executor=self.executor, policy=QuestPlanner(),
                               directive_url="http://127.0.0.1:1/directive",
                               instance="audit")
        self.pilot = Autopilot(runtime=runtime, goals=["do all the quests"],
                               log_path=self.root / "a.jsonl",
                               stop_file=self.root / "stop", max_steps=1)

    def tearDown(self):
        self.tmp.cleanup()

    def stored(self):
        return {str(f.key): f.value for f in self.executor.store.find("menu_window")}

    def test_a_successful_press_teaches_the_pair(self):
        """The observable case: nothing open, press Spell, the spell window appears."""
        self.pilot._learn_menu_window(windows(), windows("spell"),
                                      ToolCall("ui.tap", {"menu": "Spell"}))
        self.assertEqual(self.pilot.runtime.policy.menu_window_for("Spell"), "spell")
        self.assertEqual(self.stored().get("Spell"), "spell",
                         "the pair was learned but not persisted")

    def test_a_disabled_press_teaches_nothing(self):
        """A press on an icon whose window is already open changes nothing, so it proves nothing."""
        self.pilot._learn_menu_window(windows("equipment"), windows("equipment"),
                                      ToolCall("ui.tap", {"menu": "Bag"}))
        self.assertIsNone(self.pilot.runtime.policy.menu_window_for("Map"),
                          "a no-op press invented a pair")
        self.assertEqual(self.stored(), {})

    def test_two_new_windows_teach_nothing(self):
        """If two appeared, this press did not name the pair -- guessing would be worse than not."""
        self.pilot._learn_menu_window(windows(), windows("market", "shop"),
                                      ToolCall("ui.tap", {"menu": "BidHouse"}))
        self.assertIsNone(self.pilot.runtime.policy.menu_window_for("BidHouse"))
        self.assertEqual(self.stored(), {})

    def test_another_tool_teaches_nothing(self):
        self.pilot._learn_menu_window(windows(), windows("quest"),
                                      ToolCall("quest.advance", {"quest_id": 3841}))
        self.assertEqual(self.stored(), {})

    def test_the_seed_survives_as_a_starting_point(self):
        """The one pair that was measured stays, so a first run is not blind."""
        self.assertEqual(self.pilot.runtime.policy.menu_window_for("Bag"), "equipment")

    def test_a_new_start_adopts_what_the_last_one_learned(self):
        """The reader is the point: a fact that is stored and never read is the old mistake."""
        self.pilot._learn_menu_window(windows(), windows("spell"),
                                      ToolCall("ui.tap", {"menu": "Spell"}))
        self.pilot._learn_menu_window(windows(), windows("quest"),
                                      ToolCall("ui.tap", {"menu": "Book"}))
        self.assertEqual(len(self.stored()), 2, self.stored())

        fresh = QuestPlanner()
        self.assertIsNone(fresh.menu_window_for("Book"), "the seed already knew this pair")
        adopted = fresh.load_menu_windows(
            {str(f.key): f.value for f in ToolExecutor(
                knowledge_store=self.kb).store.find("menu_window")})
        self.assertEqual(adopted, 2)
        self.assertEqual(fresh.menu_window_for("Book"), "quest")
        self.assertEqual(fresh.menu_window_for("Spell"), "spell")

    def test_learning_is_idempotent(self):
        """A second identical press must not re-persist or report itself as news."""
        call = ToolCall("ui.tap", {"menu": "Spell"})
        self.pilot._learn_menu_window(windows(), windows("spell"), call)
        self.pilot._learn_menu_window(windows(), windows("spell"), call)
        self.assertEqual(len(self.stored()), 1, self.stored())

    def test_a_rename_overwrites_rather_than_keeping_both(self):
        """The client has one answer at a time; two rows for one key would be a contradiction."""
        self.pilot._learn_menu_window(windows(), windows("spell"),
                                      ToolCall("ui.tap", {"menu": "Spell"}))
        self.pilot._learn_menu_window(windows(), windows("spell_v2"),
                                      ToolCall("ui.tap", {"menu": "Spell"}))
        self.assertEqual(self.pilot.runtime.policy.menu_window_for("Spell"), "spell_v2")
        # The fact log is append-only (compacted later), so the file may hold both rows; what
        # must be true is that the answer is single-valued -- `find` is keyed by (kind, key).
        self.assertEqual(self.stored().get("Spell"), "spell_v2", self.stored())


class SurfaceLabelLearning(unittest.TestCase):
    """The French→key table is gone; the client's own labels replace it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.kb = self.root / "knowledge.jsonl"
        self.executor = ToolExecutor(knowledge_store=self.kb)
        runtime = AgentRuntime(executor=self.executor, policy=QuestPlanner(),
                               directive_url="http://127.0.0.1:1/directive",
                               instance="audit")
        self.pilot = Autopilot(runtime=runtime, goals=["do all the quests"],
                               log_path=self.root / "a.jsonl",
                               stop_file=self.root / "stop", max_steps=1)

    def tearDown(self):
        self.tmp.cleanup()

    def observation(self, text, icons=None):
        return {
            "level": 10, "map_id": 100, "cell": 0, "connected": True,
            "in_world": True, "in_fight": False, "in_dialog": False,
            "tutorial": {"active": True, "quest_id": 777, "step_id": 4970},
            "quests_active": {}, "quests_done": [], "current_objective": None,
            "ready_objectives": [{"quest_id": "777", "objective_id": 9001,
                                  "type_id": 0, "step_id": 4970, "text": text}],
            "step_objectives": [], "exits": [], "npcs": [], "groups": [],
            "inventory": {}, "kamas": 0,
            "menu_icons": icons or [],
        }

    def stored(self):
        return {str(f.key): f.value for f in self.executor.store.find("surface_label")}

    def test_the_clients_label_is_learned_and_persisted(self):
        obs = self.observation("Ouvrez votre inventaire depuis le menu",
                               [{"key": "Bag", "label": "Inventaire"}])
        self.assertEqual(self.pilot._learn_surface_labels(obs), 1)
        self.assertEqual(self.stored().get("Inventaire"), "Bag")
        policy = self.pilot.runtime.policy
        self.assertEqual(policy.surface_labels().get("inventaire"), "Bag")

    def test_a_french_objective_resolves_through_the_learned_label(self):
        obs = self.observation("Ouvrez votre inventaire depuis le menu",
                               [{"key": "Bag", "label": "Inventaire"}])
        self.pilot._learn_surface_labels(obs)
        call = self.pilot.runtime.policy._tutorial_call(obs, "do all the quests")
        self.assertIsNotNone(call, "the learned label did not reach the planner")
        self.assertEqual(call.tool, "ui.tap")
        self.assertEqual(call.args.get("menu"), "Bag")

    def test_an_icon_without_a_key_or_label_teaches_nothing(self):
        obs = self.observation("Ouvrez votre inventaire", [
            {"key": None, "label": "Inventaire"},
            {"key": "Bag", "label": ""},
            {"key": "Map", "label": "ab"},
        ])
        self.assertEqual(self.pilot._learn_surface_labels(obs), 0)
        self.assertEqual(self.stored(), {})

    def test_a_new_start_adopts_learned_labels(self):
        obs = self.observation("Ouvrez votre inventaire",
                               [{"key": "Bag", "label": "Inventaire"}])
        self.pilot._learn_surface_labels(obs)
        fresh = QuestPlanner()
        self.assertEqual(fresh.surface_labels(), {})
        adopted = fresh.load_surface_labels(
            {str(f.key): f.value for f in ToolExecutor(
                knowledge_store=self.kb).store.find("surface_label")})
        self.assertEqual(adopted, 1)
        self.assertEqual(fresh.surface_labels().get("inventaire"), "Bag")

    def test_learning_is_idempotent(self):
        obs = self.observation("Ouvrez votre inventaire",
                               [{"key": "Bag", "label": "Inventaire"}])
        self.assertEqual(self.pilot._learn_surface_labels(obs), 1)
        self.assertEqual(self.pilot._learn_surface_labels(obs), 0)
        self.assertEqual(len(self.stored()), 1)

    def test_the_english_seed_still_covers_a_first_run(self):
        fresh = QuestPlanner()
        call = fresh._tutorial_call(
            self.observation("Open the World Map."), "do all the quests")
        self.assertIsNotNone(call)
        self.assertEqual(call.args.get("menu"), "Map")

    def test_a_learned_label_wins_over_the_seed_it_duplicates(self):
        """The seed is a first-run fallback; what the client teaches outranks it.

        This used to assert that `("inventaire", {"menu": "Bag"})` was absent from
        planner.py, which stopped being true as soon as the seed kept its French rows --
        and a seed is the point: a first run has no `menu_icons` to learn from. Pinning
        absence also pinned the seed's own content, so the check could only pass while
        the seed was empty. Precedence is the property, so it is tested by behaviour.
        """
        fresh = QuestPlanner()
        self.assertEqual(fresh._ui_surface("ouvrez votre inventaire"), {"menu": "Bag"})
        self.assertTrue(fresh.learn_surface_label("inventaire", "Pouch"))
        self.assertEqual(fresh._ui_surface("ouvrez votre inventaire"), {"menu": "Pouch"},
                         "the seed answered over the label the client published")


if __name__ == "__main__":
    unittest.main(verbosity=2)
