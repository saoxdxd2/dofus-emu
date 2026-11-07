"""Does refusing a fight before it starts pay? Measured as a curve, with floors that can explain it.

Where the remaining win rate lives. Inside the fight, the action head is at its ceiling (+0.0119
per-decision oracle in `wsp_rank_curve`, best weight edit +/-0.003) and a 1-ply rollout search buys
+0.0254 (`wsp_search_value`). Outside the fight is different: on this corpus a large share of boards
are monster-opening and the solo player dies before its first decision, so nothing the head does
once it is inside can reach them. A refusal rule is the only mechanism that touches them, and the
assessment head built by `wsp_program_value` (held-out terminal-win AUC 0.8986 vs the trained
head's 0.7816) is the first signal good enough to consider wiring one.

This tool answers the one question that decides whether to wire it: *if we decline the fights the
signal ranks lowest, what happens to the win rate, and how many wins do we throw away to get
there?* Everything is computed on the shipped path -- `initial_state`, `observe`, `model.forward`,
and `make_eval_rollout` with the scripted opponent -- so the terminal result per board is the same
rollout the published 0.2266 comes from, not a reimplementation of it.

Arms, all scored at the opening state before any decision is taken:
  value_trained   the shipped `value` head as it is today.
  value_solved    `value2` rewritten by `wsp_program_value` against cached terminal outcomes.
  hp_material     ally mean hp_ratio minus enemy mean hp_ratio from the observation tokens.
  first_to_act    +1 if the player has initiative, 0 otherwise. THE floor that matters: the ambush
                  class is initiative-driven, so if initiative explains the whole curve the new
                  head is buying nothing and the rule should be a one-line check.
  strike_economy  rounds-to-die minus rounds-to-kill from the tables: `floor(ap/cost) * damage`,
                  best slot per actor, through the target's resistance to the element actually cast.
                  Computed by the shipped `combat/encounter/strike.py`, which is what publishes.
  stepped_estimate the same race measured by stepping the real environment once per side, i.e.
                  `combat.preflight.Preflight.estimate`'s quantity. It scores the *policy's* turn,
                  so it depends on which spell the head happens to pick; the tables measure what the
                  fight contains, and on this corpus the difference is 0.8071 against 0.7479.
  random          uniform score, the arm that must produce the base rate at every threshold.

`refuse_share(q)` accepts the top q of boards by score and reports the win rate among accepted,
the share of all losses avoided, and the share of all wins sacrificed. A useful signal must dominate
random at every q and must be compared to `first_to_act`, not to nothing.

    python tools/wsp_refuse_rule.py --data <encounters.npz> --value-checkpoint <value_wsp.pkl>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import pickle
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
BRAIN = REPO / "dofus-network"
if str(BRAIN) not in sys.path:
    sys.path.insert(0, str(BRAIN))

import numpy as np  # noqa: E402


def auc(score, label):
    """Tie-corrected Mann-Whitney over the pooled sample."""
    score = np.asarray(score, np.float64)
    label = np.asarray(label, np.int64)
    wins = label == 1
    n_pos, n_neg = int(wins.sum()), int((~wins).sum())
    pool = np.sort(score)
    lo = np.searchsorted(pool, score, side="left")
    hi = np.searchsorted(pool, score, side="right")
    ranks = (lo + hi + 1) / 2.0
    return float((ranks[wins].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def curve(score, won, qs):
    """Accept the top `q` fraction by score; report what the refusal buys."""
    order = np.argsort(-np.asarray(score, np.float64), kind="stable")
    total_wins, total_losses = int(won.sum()), int((~won.astype(bool)).sum())
    out = []
    for q in qs:
        keep = int(round(q * len(won)))
        if keep < 1:
            continue
        accepted = order[:keep]
        w = int(won[accepted].sum())
        out.append({"accept_share": q, "accepted_boards": keep,
                    "win_rate_among_accepted": round(w / keep, 4),
                    "losses_avoided_share": round((total_losses - (keep - w)) / max(1, total_losses), 4),
                    "wins_sacrificed_share": round((total_wins - w) / max(1, total_wins), 4),
                    "wins_kept": w, "losses_taken": keep - w})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True)
    ap.add_argument("--checkpoint", default=str(BRAIN / "models" / "encounter_selfplay.pkl"))
    ap.add_argument("--value-checkpoint", required=True,
                    help="the artifact from tools/wsp_program_value.py")
    ap.add_argument("--samples", type=int, default=None)
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--reps", type=int, default=200)
    ap.add_argument("--ridge", type=float, default=1.0,
                    help="penalty for the held-out opening readout; 1.0 on standardised features")
    ap.add_argument("--out", default=None)
    ap.add_argument("--model-out", default=None,
                    help="write the solved opening gate as a coefficient artifact and assert it reproduces the held-out AUC")
    ap.add_argument("--ablations", action="store_true",
                    help="refit the opening gate without each feature in turn and report the "
                         "held-out AUC lost, which is the honest substitute for reading "
                         "coefficients as importance")
    args = ap.parse_args()

    import jax
    import jax.numpy as jnp

    from combat.encounter import backend, model
    if backend.BACKEND != "jax":
        raise SystemExit("this needs the jax backend (%s); it replays the shipped eval rollout"
                         % backend.JAX_IMPORT_ERROR)
    from combat.encounter.checkpoint import config_from_payload
    from combat.encounter.env import initial_state, observe
    from combat.encounter.train import SPEC_KEYS, TABLE_KEYS, load_encounters, make_eval_rollout

    ckpt = pathlib.Path(args.checkpoint).resolve()
    vpath = pathlib.Path(args.value_checkpoint).resolve()
    data_path = pathlib.Path(args.data)
    digest = {str(q): hashlib.sha256(q.read_bytes()).hexdigest() for q in (ckpt, vpath, data_path)}
    payload = pickle.loads(ckpt.read_bytes())
    vpayload = pickle.loads(vpath.read_bytes())
    if payload.get("config") != vpayload.get("config"):
        raise SystemExit("the value artifact's config differs from the policy checkpoint")
    cfg = config_from_payload(payload)
    params = jax.tree_util.tree_map(jnp.asarray, payload["params"])
    vparams = jax.tree_util.tree_map(jnp.asarray, vpayload["params"])
    heads = int(payload.get("heads", 4))

    tables, spec = load_encounters(data_path, args.samples)
    count = int(spec["hp"].shape[0])
    state0 = initial_state(spec, tables, cfg)

    # The opening read: value of the board before anybody decides, taken from the player's side so
    # both initiative cases are comparable. This is the only query a pre-fight planner can make --
    # it has no state to step, only the encounter it is about to accept.
    zero = jnp.zeros_like(state0["acting_team"])
    obs, glob, attention = observe(state0, tables, cfg, acting_team=zero)
    scores = {
        "value_trained": np.asarray(model.forward(params, obs, glob, attention,
                                                  heads=heads)["value"], np.float64),
        "value_solved": np.asarray(model.forward(vparams, obs, glob, attention,
                                                 heads=heads)["value"], np.float64),
    }
    same = np.asarray(obs)[:, :, 0] > 0.5
    alive = np.asarray(obs)[:, :, 1] > 0.5
    hp = np.asarray(obs)[:, :, 2]
    scores["hp_material"] = (np.where(same & alive, hp, 0.0).sum(1) / np.maximum(same.sum(1), 1)
                             - np.where(~same & alive, hp, 0.0).sum(1) / np.maximum((~same).sum(1), 1))
    first = np.asarray(spec["first_team"]).reshape(-1)
    scores["first_to_act"] = (first == 0).astype(np.float64)
    scores["random"] = np.random.default_rng(args.seed).random(count)
    # Each clock on its own, because the ablation table below says three features carry the whole
    # forecast and one of them -- the distance the sampler drew uniformly at build time -- is not in
    # the untuned arithmetic at all. Its marginal AUC is the check on whether the fitted gate found
    # a property of fights or a property of how this corpus was generated.
    from combat.encounter.strike import round_strikes as shipped_strikes

    shipped_ref = shipped_strikes({k: v for k, v in spec.items()}, tables)
    # entered as the raw distance, because it measured AUC 0.3534 that way: further from the
    # monsters predicts a WIN (0.6466 in the natural direction), which is the env's reachability
    # rule talking -- a ranged character at distance 8 casts while the group spends MP approaching.
    # The sampler drew this variable uniformly at build time, so it is an independent cause of
    # outcome variation here rather than a consequence of play, and the live planner can choose it.
    scores["distance_alone"] = np.asarray(spec["dist"], np.float64)[:, 0]
    scores["rounds_to_die_alone"] = np.clip(np.asarray(shipped_ref["rounds_to_die"], np.float64),
                                            -1e3, 1e3)
    scores["incoming_damage_alone"] = np.asarray(shipped_ref["incoming_damage_per_round"],
                                                 np.float64)

    # The pre-registered hand rule. Written from the tables before running, with the expectation
    # stated in advance: this should beat `hp_material`, because the health read ignores the only
    # quantities that decide an abstract fight -- how much damage each side can actually put down
    # per round, what it costs, and what the target resists. If it does not beat the health read,
    # the honest conclusion is that one round of arithmetic is no better than a ratio of HP bars
    # and the planner should not be built on it.
    #
    # strike(b) = the best affordable spell's damage per round for one actor, after that actor's
    # AP budget and the target's resistance for that element; turns_to_kill uses the ally's strike
    # against the enemies' pooled HP, turns_to_die uses the enemies' pooled strike against the
    # ally's HP. Score = turns_to_die - turns_to_kill: negative means the arithmetic says you die
    # first, which is exactly what a pre-fight refusal should key on.
    ap = np.asarray(spec["ap_max"], np.float64)
    hp = np.asarray(spec["hp"], np.float64)
    alive = np.asarray(spec["alive"], np.float64)
    s_ap = np.maximum(np.asarray(tables["spell_ap"], np.float64), 1.0)
    s_dmg = np.asarray(tables["spell_damage"], np.float64)
    s_el = np.asarray(tables["spell_element"], np.int64)
    res = np.asarray(spec["res"], np.float64)                      # [B,N,E]
    # per (batch, actor, slot): how many casts this actor can pay for in a round
    casts = np.floor(ap[:, :, None] / s_ap)
    per_cast = s_dmg
    strike_slot = np.where(alive[:, :, None] > 0, casts * per_cast, 0.0)
    best = strike_slot.max(axis=2)                                  # [B,N] raw damage/round
    slot_of_best = strike_slot.argmax(axis=2)                        # for the element
    rows = np.arange(count)[:, None]
    cols = np.arange(best.shape[1])[None, :]
    element = s_el[rows, cols, slot_of_best]                          # [B,N]
    strike = best

    def pooled_damage(b, target):
        """`b`'s strike against actor `target`, with that target's resistance to the element."""
        mult = np.clip(1.0 - res[np.arange(count), target, element[np.arange(count), b]] / 100.0,
                       0.0, 2.0)
        return strike[np.arange(count), b] * mult

    n = best.shape[1]
    enemy_hp = (hp * alive)[:, 1:].sum(axis=1)
    ally_strike = sum(pooled_damage(0, i) for i in range(1, n))
    enemy_strike = sum(pooled_damage(i, 0) for i in range(1, n))
    ttk = np.where(ally_strike > 0, enemy_hp / np.maximum(ally_strike, 1e-9), 1e9)
    ttd = np.where(enemy_strike > 0, hp[:, 0] / np.maximum(enemy_strike, 1e-9), 1e9)
    scores["strike_economy"] = np.clip(ttd - ttk, -1e3, 1e3)
    out_extra = {"median_rounds_to_die": round(float(np.median(ttd)), 3),
                 "median_rounds_to_kill": round(float(np.median(ttk)), 3),
                 "stepped_estimate_note": (
                     "`stepped_estimate` is combat.preflight.Preflight.estimate's quantity "
                     "(turns_to_die - turns_to_kill) recomputed here on corpus tensors through the "
                     "env's own `step`; the shipped module now owns the closed-form one, so the "
                     "row named `shipped_rule` is the published number and this one is the "
                     "cross-check that the two implementations still agree.")}

    # The shipped implementation, scored rather than re-derived: an instrument that measures its
    # own local copy of a rule cannot tell you the shipped copy drifted.
    from combat.encounter.strike import pre_fight

    shipped = shipped_ref
    scores["shipped_rule"] = np.clip(np.asarray(shipped["score"], np.float64), -1e3, 1e3)
    agreement = float(np.abs(np.asarray(shipped["score"], np.float64)
                             - np.clip(ttd - ttk, -1e3, 1e3)).max())
    if agreement > 1e-6:
        raise SystemExit("the shipped combat.encounter.strike and this tool's arithmetic disagree "
                         "by %.4g on at least one board; the published row would be one of the two "
                         "and there would be no way to tell which" % agreement)

    # The same quantity the shipped planner already reports, but measured by stepping the real
    # environment once per side from the untouched opening state. This is the arm that decides
    # whether the planner needs a new signal at all or only a threshold on the one it has.
    from combat.encounter.env import action_masks, step
    from combat.encounter.heuristic import heuristic_actions

    def greedy_player(state):
        o, g, a = observe(state, tables, cfg)
        lg = model.forward(params, o, g, a, heads=heads)
        return model.greedy_actions(lg, action_masks(state, tables, cfg), cfg)

    s0 = initial_state(spec, tables, cfg)
    hp_open = np.asarray(s0["hp"])[0].astype(np.float64)
    enemies = [i for i in range(1, hp_open.shape[0]) if hp_open[i] > 0]
    after_m = step(s0, heuristic_actions(s0, tables, cfg), tables, cfg)
    hp_m = np.asarray(after_m["hp"]).astype(np.float64)
    monster_dmg = np.maximum(0.0, hp_open[None, 0] - hp_m[:, 0])
    after_p = step(s0, greedy_player(s0), tables, cfg)
    hp_p = np.asarray(after_p["hp"]).astype(np.float64)
    player_dmg = np.maximum(0.0, hp_open[None, enemies] - hp_p[:, enemies]).sum(axis=1)
    s_ttd = np.where(monster_dmg > 0, hp_open[0] / np.maximum(monster_dmg, 1e-9), 1e9)
    s_ttk = np.where(player_dmg > 0, float(hp_open[1:].sum()) / np.maximum(player_dmg, 1e-9), 1e9)
    scores["stepped_estimate"] = np.clip(s_ttd - s_ttk, -1e3, 1e3)
    out_extra["stepped_median_rounds_to_die"] = round(float(np.median(s_ttd)), 3)
    out_extra["stepped_median_rounds_to_kill"] = round(float(np.median(s_ttk)), 3)

    # Terminal results, from the shipped rollout, greedy selection -- the same instrument whose
    # published number is 0.2266 on this corpus.
    rollout = jax.jit(make_eval_rollout(cfg, 2 * cfg.max_rounds, opponent="heuristic",
                                        player="greedy"))
    final = rollout(params, state0, tables, jax.random.PRNGKey(args.seed))
    winner = np.asarray(final["winner"])
    done = np.asarray(final["done"])
    if not done.all() or not np.isin(winner, (0, 1, 2)).all():
        raise SystemExit("the eval rollout did not reach verified terminal outcomes")
    won = (winner == 0).astype(np.int64)
    base = float(won.mean())

    verdict, _ = pre_fight({k: v for k, v in spec.items()}, tables)
    verdict = np.asarray(verdict)
    accepted = np.isin(verdict, ("accept", "unknown"))
    out_extra_verdict = {
        "verdict_counts": {v: int((verdict == v).sum()) for v in
                           ("accept", "refuse", "unknown")},
        "win_rate_when_accept_or_unknown": round(float(won[accepted].mean()), 4),
        "win_rate_when_refuse": round(float(won[~accepted].mean()), 4) if (~accepted).any() else None,
        "share_refused": round(float((~accepted).mean()), 4),
        "wins_lost_by_the_untuned_rule": int(won[~accepted].sum()),
        "losses_avoided_by_the_untuned_rule": int((~won.astype(bool))[~accepted].sum())}
    # Wins per unit of fight time, because a refusal that raises the win rate by refusing the short
    # hopeless fights buys nothing: the accepted boards are only worth playing if the *rate* of
    # winning per round went up as well. Rounds are the simulator's own clock, so this is a proxy
    # for time-on-task, not a measured kamas/hour -- the corpus has no kamas value column (§N.13).
    rd = np.asarray(final["round"]).astype(np.float64)
    out_extra_verdict["mean_rounds_all"] = round(float(rd.mean()), 3)
    out_extra_verdict["mean_rounds_accepted"] = round(float(rd[accepted].mean()), 3)
    out_extra_verdict["wins_per_1000_rounds_accept_everything"] = round(1000.0 * base / float(rd.mean()), 2)
    out_extra_verdict["wins_per_1000_rounds_under_the_rule"] = round(
        1000.0 * float(won[accepted].mean()) / float(rd[accepted].mean()), 2)

    # Does the ambush layer earn its place on top of the race? `preflight.readiness()` refuses a
    # board whose opening monster turn kills the character before it acts, which is a fact the
    # race does not contain -- but on this corpus 73.97% of boards open on the monsters, so a
    # second refusal rule could easily collapse acceptance without refusing anything the race had
    # not already refused. The test is incremental: how many boards does it newly refuse, what does
    # it win there, and does wins-per-1000-rounds go up. `perfect_foresight` is the ceiling line --
    # refuse exactly the boards that end in a loss -- which no rule can beat, so it bounds the
    # whole gate idea before any of it is wired.
    ambush_kills = (first == 1) & (monster_dmg >= hp_open[0])
    gates = {"race_only": accepted,
             "ambush_only": ~ambush_kills,
             "race_and_ambush": accepted & ~ambush_kills,
             "perfect_foresight": won.astype(bool)}
    out_extra_verdict["ambush_kill_share"] = round(float(ambush_kills.mean()), 4)
    out_extra_verdict["gates"] = {}
    for name, keep in gates.items():
        if not keep.any():
            out_extra_verdict["gates"][name] = {"accepted_boards": 0}
            continue
        out_extra_verdict["gates"][name] = {
            "accepted_boards": int(keep.sum()),
            "accept_share": round(float(keep.mean()), 4),
            "win_rate_among_accepted": round(float(won[keep].mean()), 4),
            "mean_rounds_accepted": round(float(rd[keep].mean()), 3),
            "wins_per_1000_rounds": round(1000.0 * float(won[keep].mean())
                                          / float(rd[keep].mean()), 2),
            "wins_kept_of_464": int(won[keep].sum()),
            "losses_avoided": int((~won.astype(bool))[~keep].sum())}
    # The incremental claim, stated as a count rather than an adjective.
    out_extra_verdict["newly_refused_by_the_ambush_layer"] = int((accepted & ambush_kills).sum())
    out_extra_verdict["wins_given_up_by_the_ambush_layer"] = int(won[accepted & ambush_kills].sum())
    ally_strike = sum(pooled_damage(0, i) for i in range(1, n))
    enemy_strike = sum(pooled_damage(i, 0) for i in range(1, n))
    ttk = np.where(ally_strike > 0, enemy_hp / np.maximum(ally_strike, 1e-9), 1e9)
    ttd = np.where(enemy_strike > 0, hp[:, 0] / np.maximum(enemy_strike, 1e-9), 1e9)

    qs = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1]
    out = {"data": str(data_path), "boards": count, "base_win_rate": round(base, 4),
           **out_extra, **out_extra_verdict,
           "shipped_vs_tool_score_max_abs_delta": agreement,
           "monster_opening_share": round(float((first == 1).mean()), 4),
           "win_rate_when_player_opens": round(float(won[first == 0].mean()), 4)
           if (first == 0).any() else None,
           "win_rate_when_monster_opens": round(float(won[first == 1].mean()), 4)
           if (first == 1).any() else None,
           "mean_rounds": round(float(np.asarray(final["round"]).mean()), 2),
           "score_metric": "terminal_player_win_v1",
           "value_artifact": str(vpath),
           "arms": {}}
    rng = np.random.default_rng(args.seed)
    for name, score in scores.items():
        boots = []
        for _ in range(args.reps):
            idx = rng.integers(0, count, count)
            if len(np.unique(won[idx])) < 2:
                continue
            boots.append(auc(score[idx], won[idx]))
        boots.sort()
        out["arms"][name] = {
            "auc": round(auc(score, won), 4),
            "auc_ci95": [round(boots[int(0.025 * len(boots))], 4),
                         round(boots[int(0.975 * len(boots))], 4)],
            "curve": curve(score, won, qs)}
    # Non-vacuity of the whole table: the random arm must sit at the base rate everywhere and at AUC
    # 0.5. If it does not, the curve function or the corpus ordering is broken and every other row
    # is suspect. The tolerance is the arm's own standard error, not a constant: at n=64 with 15
    # positives the SE of an AUC is 0.086, and the fixed ±0.06 I first wrote refused a perfectly
    # ordinary 0.368 -- which blocked the small-sample smoke test this tool exists to support, the
    # same way an over-tight gate hides a real defect by firing on nothing.
    n_pos, n_neg = int(won.sum()), int((~won.astype(bool)).sum())
    auc_se = float(np.sqrt((count + 1.0) / (12.0 * max(1, n_pos) * max(1, n_neg))))
    rand = out["arms"]["random"]
    rate_se = float(np.sqrt(max(base * (1.0 - base), 1e-9)))
    bad = [(c["accept_share"], c["win_rate_among_accepted"]) for c in rand["curve"][:6]
           if abs(c["win_rate_among_accepted"] - base)
           > 3.0 * rate_se / np.sqrt(max(1, int(c["accept_share"] * count)))]
    if abs(rand["auc"] - 0.5) > 3.0 * auc_se or bad:
        raise SystemExit("the random arm does not read like noise (AUC %.3f, its own SE is %.3f, "
                         "worst curve cell off by %.3f over %d cells); the instrument is wrong, not "
                         "the signal" % (rand["auc"], auc_se,
                                         max([abs(b - base) for _, b in bad] or [0.0]), len(bad)))
    out["random_arm_control"] = {"auc": rand["auc"], "standard_error": round(auc_se, 4),
                                 "tolerance": round(3.0 * auc_se, 4),
                                 "cells_outside_tolerance": len(bad)}
    # Out-of-sample: can anything predict better than the untuned arithmetic? The honest move is the
    # WSP one -- solve, do not train. A ridge readout on the opening board's own features is fitted
    # on the first half of the corpus rows and scored on the second, and both rules are reported on
    # the *same* held-out half, because a threshold tuned on the whole corpus is the mistake this
    # section has already caught the planner making once (`hp_over_opening_damage < 3.0`).
    #
    # The feature vector is the set of things a player can look at before the fight: the two clocks
    # from `strike`, the health and AP totals, group size, whether anything in the group is a boss,
    # the player's level against the enemies' mean level, who opens, the distance, and the pooled
    # resistance the player faces. Nothing is derived from the outcome.
    feats = np.concatenate([
        np.clip(np.asarray(shipped["score"], np.float64), -1e3, 1e3)[:, None],
        np.log1p(np.clip(np.asarray(shipped["rounds_to_kill"], np.float64), 0, 1e3))[:, None],
        np.log1p(np.clip(np.asarray(shipped["rounds_to_die"], np.float64), 0, 1e3))[:, None],
        np.log1p(np.asarray(shipped["player_damage_per_round"], np.float64))[:, None],
        np.log1p(np.asarray(shipped["incoming_damage_per_round"], np.float64))[:, None],
        (hp * alive)[:, 1:].sum(axis=1)[:, None],
        hp[:, 0][:, None],
        (alive[:, 1:].sum(axis=1) - 1).astype(np.float64)[:, None],
        np.asarray(spec["boss"], np.float64).sum(axis=1)[:, None],
        np.asarray(spec["level"], np.float64)[:, 0:1],
        np.asarray(spec["level"], np.float64)[:, 1:].mean(axis=1)[:, None],
        (first == 0).astype(np.float64)[:, None],
        np.asarray(spec["dist"], np.float64)[:, 0:1],
        np.asarray(spec["res"], np.float64)[:, 1:, :].mean(axis=(1, 2))[:, None],
    ], axis=1)
    names = ["strike_score", "log_rounds_to_kill", "log_rounds_to_die", "log_my_damage",
             "log_incoming_damage", "enemy_hp_total", "player_hp", "group_size", "boss_count",
             "player_level", "enemy_mean_level", "player_opens", "distance", "enemy_mean_res"]
    half = count // 2
    F = (feats - feats[:half].mean(0)) / np.maximum(feats[:half].std(0), 1e-9)
    yc = won[:half] - won[:half].mean()
    W = np.linalg.solve(F[:half].T @ F[:half] + args.ridge * np.eye(F.shape[1]), F[:half].T @ yc)
    fitted = F @ W + won[:half].mean()
    fit_arm = {"auc_held_out": round(auc(fitted[half:], won[half:]), 4),
               "auc_in_sample": round(auc(fitted[:half], won[:half]), 4),
               "features": names, "ridge": args.ridge, "split": "first half trains, second scores"}
    # Both rules on the held-out half only, so the comparison is not tilted by the fit's own data.
    def throughput(keep, lo):
        k = keep[lo:]
        if not k.any() or float(rd[lo:][k].mean()) == 0.0:
            return None
        return {"accept_share": round(float(k.mean()), 4),
                "win_rate": round(float(won[lo:][k].mean()), 4),
                "wins_per_1000_rounds": round(1000.0 * float(won[lo:][k].mean())
                                              / float(rd[lo:][k].mean()), 2)}

    cut = float(np.median(fitted[:half]))
    # Matched-operating-point comparison: the arithmetic rule accepts `s` of the held-out boards, so
    # the fitted readout is scored at the same `s`. Comparing a rule at 38% acceptance with a rule
    # at 90% would rank the caution, not the forecast.
    arith_ok = np.asarray(shipped["score"], np.float64) >= 0.0
    s_arith = float(arith_ok[half:].mean())
    keep_n = max(1, int(round(s_arith * (count - half))))
    thr = np.sort(fitted[half:])[::-1][keep_n - 1]
    fit_arm["held_out"] = {
        "arithmetic_accept_share_on_this_half": round(s_arith, 4),
        "baseline_accept_everything": {"accept_share": 1.0,
                                       "win_rate": round(float(won[half:].mean()), 4),
                                       "wins_per_1000_rounds": round(
                                           1000.0 * float(won[half:].mean())
                                           / float(rd[half:].mean()), 2)},
        "untuned_arithmetic": throughput(arith_ok, half),
        "fitted_readout": throughput(fitted >= cut, half),
        "fitted_at_the_same_accept_share": throughput(fitted >= thr, half),
        "perfect_foresight": throughput(won.astype(bool), half)}
    out["opening_readout_fit"] = fit_arm

    # Coefficients are not an importance ranking, and one of them makes that concrete: `strike_score`
    # is *minus* `log_rounds_to_kill` plus `log_rounds_to_die`, so its fitted weight is a residual
    # between two features that already contain it, and reading it as "the arithmetic hurts the
    # forecast" would be a misattribution. The defensible question is which features carry the
    # discrimination, so each one is dropped, the model is refit on the same split, and the
    # held-out AUC is re-measured.
    if args.ablations:
        ab = []
        for j, name in enumerate(names):
            keep = [k for k in range(len(names)) if k != j]
            Fa = F[:, keep]
            Wa = np.linalg.solve(Fa[:half].T @ Fa[:half] + args.ridge * np.eye(len(keep)),
                                 Fa[:half].T @ yc)
            fa = ((Fa @ Wa + won[:half].mean())[half:] - fitted[half:])
            ab.append({"dropped": name, "auc_held_out": round(auc((Fa @ Wa + won[:half].mean())[half:],
                                                                 won[half:]), 4),
                       "auc_delta": round(auc((Fa @ Wa + won[:half].mean())[half:], won[half:])
                                          - fit_arm["auc_held_out"], 4),
                       "mean_abs_score_shift": round(float(np.abs(fa).mean()), 4)})
        ab.sort(key=lambda r: r["auc_delta"])
        out["ablations"] = {"full_model_auc": fit_arm["auc_held_out"], "drop_one": ab}

    # The fitted rule is only worth anything if it can leave this process. It is written as
    # coefficients plus the exact standardisation it was solved against, and re-loaded and
    # re-scored here: the assertion is that the artifact reproduces the AUC printed above, so a
    # shipped gate keyed on it cannot be quietly different from the thing that was measured. The
    # coefficients are not pasted into the planner as constants -- that would freeze today's fit --
    # and a recurrence is caught by the round trip, not by a comment.
    if args.model_out:
        model = {"format": "opening_gate_v1",
                 "features": names,
                 "mean": [float(v) for v in feats[:half].mean(0)],
                 "std": [float(v) for v in np.maximum(feats[:half].std(0), 1e-9)],
                 "weight": [float(v) for v in W],
                 "intercept": float(won[:half].mean()),
                 "ridge": args.ridge, "accept_threshold": float(cut),
                 "split_train_rows": int(half), "split_eval_rows": int(count - half),
                 "auc_held_out": fit_arm["auc_held_out"],
                 "auc_reproduced_from_artifact": None,
                 "data": str(data_path), "data_sha256": digest[str(data_path)],
                 "checkpoint": str(ckpt), "checkpoint_sha256": digest[str(ckpt)]}
        reloaded = json.loads(json.dumps(model))
        w = np.asarray(reloaded["weight"], np.float64)
        mu = np.asarray(reloaded["mean"], np.float64)
        sd = np.asarray(reloaded["std"], np.float64)
        replay = ((feats - mu) / sd) @ w + reloaded["intercept"]
        model["auc_reproduced_from_artifact"] = round(auc(replay[half:], won[half:]), 4)
        if model["auc_reproduced_from_artifact"] != fit_arm["auc_held_out"]:
            raise SystemExit("the written model does not reproduce the fitted held-out AUC (%.4f "
                             "against %.4f): the artifact and the measurement are two different "
                             "rules" % (model["auc_reproduced_from_artifact"],
                                        fit_arm["auc_held_out"]))
        pathlib.Path(args.model_out).write_text(json.dumps(model, indent=1, sort_keys=True),
                                                encoding="utf-8")
        out["opening_gate_model"] = args.model_out

    text = json.dumps(out, indent=1, sort_keys=True)
    if args.out:
        pathlib.Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
