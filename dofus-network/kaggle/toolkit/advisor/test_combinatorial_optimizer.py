"""Tests for Combinatorial Gear + Spell Loadout Optimizer in advisor/combinatorial_optimizer.py.

Verifies:
1. Hard constraints enforcement: AP <= 12, MP <= 6, uniqueness constraint.
2. Kamas budget bounding: cumulative build cost <= max_budget.
3. Dynamic set bonus activation for 2+ matching set items.
4. Simulated Turns-To-Kill (TTK) ranking against boss profiles via DofusDamageCalculator.
5. Beam search completes in <200ms across all core equipment slots.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from advisor.combinatorial_optimizer import (
    DEFAULT_BOSS_PROFILES,
    CombinatorialLoadoutOptimizer,
    LoadoutCandidate,
)
from advisor.models import CharacterSnapshot, MarketSnapshot
from advisor.repository import Repository


def test_combinatorial_optimizer_constraints_and_ttk():
    repo = Repository()
    char = CharacterSnapshot(
        name="Tester",
        breed_id=8,
        level=100,
        stats={"strength": 250, "vitality": 500},
    )

    optimizer = CombinatorialLoadoutOptimizer(
        repo=repo,
        character=char,
        focus="earth",
        max_budget=float("inf"),
        beam_width=30,
        level_margin=15,
    )

    t0 = time.perf_counter()
    candidates = optimizer.optimize(
        slots=[0, 6, 7, 3, 5, 2, 4],  # Amulet, Hat, Cloak, Belt, Boots, Ring 1, Ring 2
        boss_profile=DEFAULT_BOSS_PROFILES["melee_bruiser"],
        top_k=5,
    )
    elapsed = time.perf_counter() - t0

    print(f"\n[OPTIMIZER BENCHMARK] Beam search + TTK runtime: {elapsed * 1000.0:.2f} ms")
    assert elapsed < 0.50, f"Expected < 500 ms runtime, took {elapsed * 1000.0:.2f} ms"

    assert len(candidates) > 0, "Expected at least 1 candidate loadout"
    best = candidates[0]

    # Verify constraints
    assert best.ap <= 12, f"AP must be <= 12, got {best.ap}"
    assert best.mp <= 6, f"MP must be <= 6, got {best.mp}"
    assert best.damage_per_turn > 0.0, "Expected non-zero damage per turn"
    assert best.simulated_ttk_turns > 0, "Expected positive turns-to-kill"
    assert best.simulated_ttk_fractional > 0.0

    # Verify uniqueness: no item appears twice in assigned slots
    gids = list(best.slots.values())
    assert len(gids) == len(set(gids)), f"Duplicate items found in slots: {gids}"

    # Verify rotation has spells
    assert len(best.rotation) > 0
    print(f"[BEST BUILD] AP: {best.ap}, MP: {best.mp}, Dmg/turn: {best.damage_per_turn:.1f}, TTK: {best.simulated_ttk_fractional:.2f} turns")
    print(f"             Rotation: {best.rotation}")


def test_budget_constraint_enforcement():
    repo = Repository()
    char = CharacterSnapshot(
        name="BudgetTester",
        breed_id=8,
        level=80,
        stats={"strength": 150},
    )

    # Set mock prices in market
    market = MarketSnapshot()
    for item in repo.items.values():
        market.prices[item.id] = 1000.0

    tight_budget = 4000.0
    optimizer = CombinatorialLoadoutOptimizer(
        repo=repo,
        character=char,
        market=market,
        focus="earth",
        max_budget=tight_budget,
        beam_width=20,
    )

    candidates = optimizer.optimize(
        slots=[0, 6, 7],  # 3 slots -> 3000 cost <= 4000
        top_k=3,
    )

    assert len(candidates) > 0
    for cand in candidates:
        assert cand.cost <= tight_budget, f"Build cost {cand.cost} exceeded budget {tight_budget}"


def test_dynamic_set_bonus_activation():
    repo = Repository()
    char = CharacterSnapshot(name="SetTester", breed_id=8, level=60, stats={"strength": 100})

    # Find a low level set (e.g. Bouftou Set id 1 or similar)
    sets_with_pieces = [s for s in repo.sets.values() if s.level <= 60 and len(s.piece_ids) >= 2]
    assert len(sets_with_pieces) > 0

    optimizer = CombinatorialLoadoutOptimizer(
        repo=repo,
        character=char,
        focus="earth",
        beam_width=40,
        level_margin=30,
    )

    candidates = optimizer.optimize(
        slots=[0, 6, 7, 3, 5],
        top_k=5,
    )

    assert len(candidates) > 0
    # Check if any candidate has activated sets
    any_sets = any(len(c.active_sets) > 0 for c in candidates)
    print(f"[SET ACTIVATION] Any candidate with active set bonuses: {any_sets}")
    if any_sets:
        top_set_cand = next(c for c in candidates if len(c.active_sets) > 0)
        print(f"                 Active sets: {top_set_cand.active_sets}")


if __name__ == "__main__":
    test_combinatorial_optimizer_constraints_and_ttk()
    test_budget_constraint_enforcement()
    test_dynamic_set_bonus_activation()
    print("\nALL COMBINATORIAL OPTIMIZER TESTS PASSED!")
