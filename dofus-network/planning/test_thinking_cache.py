"""Tests for the structure cache and the planner path that reads it.

The cache existed for a while as 184 lines nothing constructed: ``Planner.plan``
had the read path, ``ScheduleManager`` had no way to flush it, and the only
construction site passed neither. These tests pin both halves -- the cache's own
semantics, and the fact that the live stack actually reaches it.

Measured on this host (level 60, an item the player cannot afford, so the plan
carries a kamas-earning subtree):

    cold plan   136.6 ms
    cached hit    2.1 ms   (then 0.4 ms on the next)
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from planning.economy import CostModel, Economy, PlayerState
from planning.kb import KnowledgeBase
from planning.planner import Goal, Planner
from planning.schedule import build_schedule_stack
from planning.thinking_cache import CacheEntry, ThinkingCache, kb_version

# One KB load for the whole module: np.load of 250 KB, and every test here needs
# the same cost model underneath it.
_KB = None


def kb() -> KnowledgeBase:
    global _KB
    if _KB is None:
        _KB = KnowledgeBase()
    return _KB


def temp_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="thinking-cache-"))


def stack(cache):
    """A planner over the real KB, with the cache the caller asked for."""
    knowledge = kb()
    cost = CostModel(knowledge)
    economy = Economy(cost, state_file=temp_dir() / "state.json")
    return Planner(knowledge, cost, economy, cache=cache)


class CacheSemanticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cache = ThinkingCache(path=temp_dir() / "cache.json")

    def test_a_miss_then_a_hit_is_counted_and_reports_its_own_saving(self):
        self.assertIsNone(self.cache.get("k"))
        self.cache.put("k", {"root": {}}, 120.0)
        entry = self.cache.get("k")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.hits, 1)
        self.assertEqual(self.cache.hits, 1)
        self.assertEqual(self.cache.misses, 1)
        self.assertEqual(self.cache.saved_ms, 120.0)
        self.assertAlmostEqual(self.cache.stats()["hit_rate"], 0.5)

    def test_a_rebuilt_knowledge_base_invalidates_every_entry(self):
        self.cache.put("k", {"root": {}}, 10.0)
        self.cache.version = "a-different-kb"
        self.assertIsNone(self.cache.get("k"))
        self.assertEqual(self.cache.invalidations, 1)
        self.assertEqual(len(self.cache.entries), 0)

    def test_an_expired_entry_is_dropped_rather_than_served(self):
        self.cache.put("k", {"root": {}}, 10.0)
        self.cache.ttl_seconds = 60.0
        self.cache.entries["k"].created = time.time() - 3600.0
        self.assertIsNone(self.cache.get("k"))
        self.assertEqual(self.cache.invalidations, 1)

    def test_a_zero_ttl_means_never_expire_rather_than_expire_immediately(self):
        self.cache.ttl_seconds = 0.0
        self.cache.put("k", {"root": {}}, 10.0)
        self.cache.entries["k"].created = time.time() - 10_000.0
        self.assertIsNotNone(self.cache.get("k"))

    def test_the_least_recently_used_entry_is_the_one_evicted(self):
        self.cache.max_entries = 2
        self.cache.put("old", {"root": {}}, 1.0)
        self.cache.put("used", {"root": {}}, 1.0)
        self.cache.entries["used"].last_hit = time.time() + 10.0
        self.cache.put("new", {"root": {}}, 1.0)
        self.assertEqual(len(self.cache.entries), 2)
        self.assertIn("used", self.cache.entries)
        self.assertIn("new", self.cache.entries)
        self.assertNotIn("old", self.cache.entries)

    def test_a_saved_cache_reloads_with_its_entries_and_its_hit_count(self):
        self.cache.put("k", {"root": {"kind": "buy"}}, 42.0)
        self.cache.hits = 7
        self.cache.save()
        again = ThinkingCache(path=self.cache.path)
        self.assertEqual(list(again.entries), ["k"])
        self.assertEqual(again.entries["k"].structure["root"]["kind"], "buy")
        self.assertEqual(again.hits, 7)

    def test_a_corrupt_cache_file_is_ignored_rather_than_fatal(self):
        path = temp_dir() / "cache.json"
        path.write_text("{not json", encoding="utf-8")
        cache = ThinkingCache(path=path)
        self.assertEqual(cache.entries, {})

    def test_a_truncated_entry_is_skipped_and_the_rest_still_load(self):
        path = temp_dir() / "cache.json"
        path.write_text(json.dumps({"entries": {
            "bad": {"version": "v"},                        # no key, no structure
            "good": {"key": "good", "structure": {"root": {}}},
        }}), encoding="utf-8")
        cache = ThinkingCache(path=path)
        self.assertEqual(list(cache.entries), ["good"])

    def test_the_key_separates_quantity_and_band_but_not_language(self):
        goal = Goal(1001, 3)
        self.assertNotEqual(self.cache.key(goal, "60-79"),
                            self.cache.key(Goal(1001, 4), "60-79"))
        self.assertNotEqual(self.cache.key(goal, "60-79"),
                            self.cache.key(goal, "80-99"))
        self.assertEqual(self.cache.key(goal, "60-79"),
                         self.cache.key(Goal(1001, 3, lang="en"), "60-79"))

    def test_the_version_is_a_short_stable_identity_not_the_path(self):
        version = kb_version()
        self.assertEqual(len(version), 12)
        self.assertNotIn(os.sep, version)


class PlannerCacheTests(unittest.TestCase):
    """The planner must save the search and never the judgement."""

    def plan_twice(self, player):
        cache = ThinkingCache(path=temp_dir() / "cache.json")
        planner = stack(cache)
        first = planner.plan(Goal(1001, 3), player)
        second = planner.plan(Goal(1001, 3), player)
        return cache, first, second

    def test_the_second_identical_plan_is_a_hit_and_the_first_is_not(self):
        cache, first, second = self.plan_twice(PlayerState(level=60, kamas=0))
        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        self.assertEqual(cache.hits, 1)

    def test_a_hit_returns_the_same_conclusion_not_a_cheaper_one(self):
        _, first, second = self.plan_twice(PlayerState(level=60, kamas=0))
        self.assertEqual(first.root.kind, second.root.kind)
        self.assertEqual(len(first.root.children), len(second.root.children))
        self.assertAlmostEqual(first.minutes, second.minutes)
        self.assertEqual([alt.label for alt in first.alternatives],
                         [alt.label for alt in second.alternatives])

    def test_the_hit_is_materially_faster_than_the_search_it_replaces(self):
        cache, _, _ = self.plan_twice(PlayerState(level=60, kamas=0))
        # saved_ms accumulates the stored reasoning time of every hit; the cold
        # search on this host is ~136 ms, so one hit must bank real milliseconds.
        self.assertGreater(cache.saved_ms, 10.0)

    def test_a_different_quantity_is_a_different_entry_not_a_stale_hit(self):
        cache = ThinkingCache(path=temp_dir() / "cache.json")
        planner = stack(cache)
        player = PlayerState(level=60, kamas=0)
        planner.plan(Goal(1001, 3), player)
        other = planner.plan(Goal(1001, 9), player)
        self.assertFalse(other.cache_hit)
        self.assertEqual(len(cache.entries), 2)

    def test_a_re_priced_conclusion_that_lost_is_replaced_by_a_real_search(self):
        """The cache stores structure, never estimates. When beliefs move enough
        that the stored winner is no longer the winner, the entry must be dropped."""
        cache = ThinkingCache(path=temp_dir() / "cache.json")
        planner = stack(cache)
        player = PlayerState(level=60, kamas=0)
        planner.plan(Goal(1001, 3), player)
        key = list(cache.entries)[0]
        # Poison the stored root with an estimate no re-pricing can reproduce and
        # with a sibling alternative that now looks far better: the rebuild has to
        # refuse it rather than serve a plan the cost model no longer supports.
        cache.entries[key].structure["alternatives"] = [{
            "kind": "buy", "label": "poisoned", "minutes": 0.0001,
            "children": [], "meta": {"item_id": 1001, "qty": 1},
        }]
        poisoned = planner.plan(Goal(1001, 3), player)
        self.assertFalse(poisoned.cache_hit)
        self.assertEqual(cache.invalidations, 1)

    def test_an_uncached_planner_still_plans_and_stores_nothing(self):
        planner = stack(None)
        plan = planner.plan(Goal(1001, 3), PlayerState(level=60, kamas=0))
        self.assertFalse(plan.cache_hit)
        self.assertIsNone(planner.cache)


class StackWiringTests(unittest.TestCase):
    """The construction site is the bug that made the cache dead."""

    def test_the_live_stack_attaches_a_cache_by_default(self):
        manager = build_schedule_stack(cache=temp_dir() / "cache.json")
        self.assertIsNotNone(manager.cache)
        self.assertIs(manager.planner.cache, manager.cache)

    def test_the_default_cache_lives_in_the_knowledge_base_directory(self):
        # Constructed, not saved: this test must not touch the checked-in data dir.
        previous = os.environ.pop("DOFUS_THINKING_CACHE", None)
        try:
            manager = build_schedule_stack()
        finally:
            if previous is not None:
                os.environ["DOFUS_THINKING_CACHE"] = previous
        self.assertIsNotNone(manager.planner.cache)
        self.assertEqual(manager.planner.cache.path.name, "thinking_cache.json")
        self.assertEqual(manager.planner.cache.path.parent.name, "kb")

    def test_asking_for_no_cache_really_means_no_cache(self):
        manager = build_schedule_stack(cache=None)
        self.assertIsNone(manager.cache)
        self.assertIsNone(manager.planner.cache)

    def test_the_environment_can_switch_the_cache_off(self):
        previous = os.environ.get("DOFUS_THINKING_CACHE")
        os.environ["DOFUS_THINKING_CACHE"] = "0"
        try:
            manager = build_schedule_stack()
            self.assertIsNone(manager.planner.cache)
        finally:
            if previous is None:
                os.environ.pop("DOFUS_THINKING_CACHE", None)
            else:
                os.environ["DOFUS_THINKING_CACHE"] = previous

    def test_a_path_lands_the_cache_file_where_it_was_asked_for(self):
        path = temp_dir() / "nested" / "cache.json"
        # `submit` persists the schedule, so the state file has to be redirected too --
        # a test that writes into `data/kb` is a test that changes the repo.
        manager = build_schedule_stack(cache=path, state_file=temp_dir() / "state.json")
        manager.submit(Goal(1001, 3), PlayerState(level=60, kamas=0))
        manager._save_cache()
        self.assertTrue(path.exists())
        self.assertIn("entries", json.loads(path.read_text(encoding="utf-8")))

    def test_flushing_a_manager_with_no_cache_is_a_no_op(self):
        manager = build_schedule_stack(cache=None)
        manager._save_cache()   # must not raise


class EntryRoundTripTests(unittest.TestCase):
    def test_an_entry_survives_its_own_dict_form(self):
        entry = CacheEntry(key="k", version="v", created=1.0,
                           structure={"root": {"kind": "farm"}},
                           reasoning_ms=3.0, hits=2, last_hit=4.0)
        again = CacheEntry.from_dict(entry.as_dict())
        self.assertEqual(again, entry)

    def test_a_missing_optional_field_defaults_instead_of_raising(self):
        again = CacheEntry.from_dict({"key": "k"})
        self.assertEqual(again.version, "")
        self.assertEqual(again.structure, {})
        self.assertEqual(again.hits, 0)


if __name__ == "__main__":
    unittest.main()
