"""The one arithmetic a player does before accepting a fight, and the refusal it justifies.

Why this exists as shipped code rather than a measurement. `tools/wsp_refuse_rule.py` scored every
pre-fight signal it could build against terminal outcomes on the 2,048-board boss evaluation corpus
(`terminal_player_win_v1`, base win rate 0.2266) and the ordering was not what the architecture
assumed:

    strike_economy  (this module)          AUC 0.8071  [0.7881, 0.8320]
    stepped_estimate (preflight.estimate)  AUC 0.7479  [0.7181, 0.7703]
    hp_material (health-bar difference)    AUC 0.7211  [0.6945, 0.7443]
    value_solved  (WSP-written value head) AUC 0.6822  [0.6487, 0.7071]
    first_to_act  (initiative)             AUC 0.5993  [0.5751, 0.6200]
    value_trained (the shipped head)       AUC 0.5439  [0.5128, 0.5743]
    random                                 AUC 0.4780  [0.4475, 0.5099]

Two things follow. The network's `value` head is nearly blind before the first cast -- 0.5439 is
indistinguishable from noise, and the WSP-solved head that reads terminal outcomes far better
mid-fight (0.8986, ARCH_REASSESSMENT section W.4) only reaches 0.6822 here, because the opening
board has no damage on it to read. And a plain closed-form ratio of what each side can put down in
one round beats all of them, including the version measured by actually stepping the environment:
the stepped one depends on which spell the *current* policy would pick, while the tables measure
what the fight contains at its best, which is the right question for a decision the policy cannot
retroactively improve.

The rule is deliberately untuned: **decline when the arithmetic says you die before they do.** It
uses no constant fitted on this corpus, so it cannot be an artifact of the sample it was read on.

    python -c "from combat.encounter.strike import pre_fight; print(pre_fight(spec, tables))"
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

import numpy as np

__all__ = ["round_strikes", "pre_fight"]


def _arr(x) -> np.ndarray:
    return np.asarray(x, np.float64)


def round_strikes(spec: Dict[str, Any], tables: Dict[str, Any]) -> Dict[str, np.ndarray]:
    """What each side can put down in one round, and the two race clocks that follow.

    For one actor: the best *affordable* spell, measured as damage in the round it can be paid for
    -- `floor(ap_max / spell_ap)` casts of `spell_damage` -- after that target's resistance to the
    element actually being used. Summed across a side, that gives `rounds_to_kill` for whoever
    strikes first and `rounds_to_die` for whoever receives it. Range is not consulted: a board where
    the damage exists but cannot be reached is a policy question, and this module answers the
    matchmaking one.
    """
    hp = _arr(spec["hp"])
    alive = _arr(spec["alive"])
    ap = np.maximum(_arr(spec["ap_max"]), 1.0)
    res = _arr(spec["res"])
    s_ap = np.maximum(_arr(tables["spell_ap"]), 1.0)
    s_dmg = _arr(tables["spell_damage"])
    s_el = np.asarray(tables["spell_element"]).astype(np.int64)

    casts = np.floor(ap[:, :, None] / s_ap)
    strike_slot = np.where(alive[:, :, None] > 0.0, casts * s_dmg, 0.0)
    damage = strike_slot.max(axis=2)
    element = s_el[np.arange(damage.shape[0])[:, None],
                   np.arange(damage.shape[1])[None, :], strike_slot.argmax(axis=2)]

    rows = np.arange(damage.shape[0])
    # actor 0 is the player and 1.. the enemies; this module is one-ally by construction, which is
    # what the encounter corpus and the live solo path both are.
    to_enemies = np.array([damage[:, 0] * np.clip(1.0 - res[rows, t, element[:, 0]] / 100.0,
                                                  0.0, 2.0)
                           for t in range(1, damage.shape[1])]).sum(axis=0)
    from_enemies = np.array([damage[:, t] * np.clip(1.0 - res[rows, 0, element[:, t]] / 100.0,
                                                    0.0, 2.0)
                             for t in range(1, damage.shape[1])]).sum(axis=0)
    enemy_hp = (hp * alive)[:, 1:].sum(axis=1)
    ttk = np.where(to_enemies > 0, enemy_hp / np.maximum(to_enemies, 1e-9), 1e9)
    ttd = np.where(from_enemies > 0, hp[:, 0] / np.maximum(from_enemies, 1e-9), 1e9)
    return {"player_damage_per_round": to_enemies, "incoming_damage_per_round": from_enemies,
            "rounds_to_kill": ttk, "rounds_to_die": ttd, "score": ttd - ttk}


def pre_fight(spec: Dict[str, Any], tables: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """`accept` when the race is winnable on the numbers, `refuse` when it is not, `unknown` when
    the board does not say (no affordable damage in either direction)."""
    s = round_strikes(spec, tables)
    score = s["score"]
    silent = (s["player_damage_per_round"] <= 0) | (s["incoming_damage_per_round"] <= 0)
    verdict = np.where(silent, "unknown", np.where(score >= 0.0, "accept", "refuse"))
    return [str(v) for v in verdict], {k: np.round(v, 3) for k, v in s.items()}
