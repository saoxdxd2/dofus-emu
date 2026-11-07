"""What is a model-based search worth, when the weights are already at their per-decision ceiling?

Why this exists. Three measurements close off the weight end of the question:

* `wsp_noise_audit` on the 13,002-decision boss outcome cache finds zero inert columns, zero
  out-of-contention columns and 0 of 8 coordinate blocks with no effect -- there is no meaningless
  weight to delete on the spell head.
* the closed-form linear readout fitted on the cached trunk vector peaks at 0.2259 per-decision
  terminal-win against the incumbent head's 0.2654 -- nothing we can solve in closed form on that
  representation beats the head that is already there.
* the per-decision oracle over the same cache is 0.2773, so the whole one-decision budget is
  +0.0119, and reaching it needs the *outcome*, not a better read.

That leaves the other half of the architecture the plan needs: use the simulator itself to look
ahead. This tool answers with terminal wins, on the same boards, with the same weights:

  greedy   play `argmax` of the head over legal actions. No simulator access at decision time.
  search   at each player decision, for every legal spell, step it, then roll the fight to a
           terminal result under the fixed greedy continuation (the same finisher `wsp_program
           cache-pvm` uses for its labels) and keep the candidate whose continuation wins. Ties
           break to the lowest candidate index, so the arm is deterministic.
  random_legal the control, and the first version of it was wrong in a way worth recording: it
           permuted the candidate axis of the value table and took the argmax of the permuted row.
           A permutation is a bijection over the same columns, so `perm[argmax(scores[perm])]` is
           `argmax(scores)` -- the control was the treatment, and the two arms agreeing at 0.2188
           and 0.2227 looked like "the values buy nothing" when it meant nothing at all. The control
           is now a uniform draw over the *legal* candidates: it keeps the mask, throws away the
           values, and the report prints how often it disagrees with `search` so the pairing is
           observable rather than assumed.

           The residual 0.4 pt between the broken control and `search` was tie-breaking: on boards
           where several legal candidates share the best value, argmax takes the lowest index and
           the permutation took a different one. That is what `valueless_boards` and
           `ties_at_best` size.

Both arms see the identical opening boards and the identical evaluation RNG stream, so the
comparison is per board: the report counts loss->win and win->loss flips, not two marginal rates.

The second number is the one the population simulation has to be priced against: `search` costs W
full fight rollouts per decision. `ms_per_decision` is measured, not estimated.

    python tools/wsp_search_value.py --data <encounters.npz> --samples 256 --seed 5
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import pickle
import sys
import time

import numpy as np

REPO = pathlib.Path(__file__).resolve().parent.parent
BRAIN = REPO / "dofus-network"
if str(BRAIN) not in sys.path:
    sys.path.insert(0, str(BRAIN))


def _boots(values, reps, rng):
    out = []
    for _ in range(reps):
        idx = rng.integers(0, len(values), len(values))
        out.append(float(np.mean(np.asarray(values)[idx])))
    out.sort()
    return [round(out[int(0.025 * len(out))], 4), round(out[int(0.975 * len(out))], 4)]


def choose_random_legal(legal, rng):
    """Uniform over the legal columns, by their running count: draw k in [0, n_legal) and take
    the (k+1)-th legal column.

    This is a function rather than four lines inside the loop because the first control written
    here permuted the candidate axis of the value table and took the argmax of the permuted row --
    which is the same column, every time, because a permutation is a bijection over the same set.
    A control that cannot disagree with the treatment cannot fail, so it cannot be evidence. The
    arms in `test_wsp_search_select.py` pin both halves of that lesson.
    """
    counts = legal.sum(axis=1)
    k = rng.integers(np.maximum(counts, 1))
    return np.argmax(np.cumsum(legal, axis=1) > k[:, None], axis=1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--checkpoint", default=str(BRAIN / "models" / "encounter_selfplay.pkl"))
    ap.add_argument("--data", required=True)
    ap.add_argument("--value-checkpoint", default=None,
                    help="a second artifact whose `value` head backs the value1ply_edited arm; the "
                         "action heads are still read from --checkpoint")
    ap.add_argument("--samples", type=int, default=256)
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--decisions", type=int, default=0,
                    help="stop after this many player decisions per fight (0 = to terminal)")
    ap.add_argument("--reps", type=int, default=200, help="bootstrap replicates")
    ap.add_argument("--arms", nargs="+", default=["greedy", "search", "random_legal"],
                    choices=("greedy", "search", "random_legal", "value1ply",
                             "value1ply_edited", "value1ply_after_enemy",
                             "value1ply_edited_after_enemy"),
                    help="`greedy` is always played as well, because every other arm is reported "
                         "against it; name only the extra arms you want")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    import jax
    import jax.numpy as jnp

    from combat.encounter import backend, model
    if backend.BACKEND != "jax":
        raise SystemExit("the search arm needs the jax backend (%s); run it on the training "
                         "interpreter" % backend.JAX_IMPORT_ERROR)
    from combat.encounter.checkpoint import config_from_payload
    from combat.encounter.env import action_masks, initial_state, observe, step
    from combat.encounter.heuristic import heuristic_actions
    from combat.encounter.train import SPEC_KEYS, TABLE_KEYS, make_eval_rollout

    checkpoint, data_path = pathlib.Path(args.checkpoint).resolve(), pathlib.Path(args.data).resolve()
    digest = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (checkpoint, data_path)}
    payload = pickle.loads(checkpoint.read_bytes())
    cfg = config_from_payload(payload)
    if cfg.allies != 1:
        raise SystemExit("the search arm indexes actor 0 as the player; it needs a one-ally config")
    params = jax.tree_util.tree_map(jnp.asarray, payload["params"])
    # The value arm reads its head from a separate artifact, so the edit under test is isolated to
    # `value2`: the action heads, the trunk and the finisher all stay the source's.
    if args.value_checkpoint:
        vpath = pathlib.Path(args.value_checkpoint).resolve()
        vpay = pickle.loads(vpath.read_bytes())
        value_payload = {"vpath": str(vpath),
                         "sha256": hashlib.sha256(vpath.read_bytes()).hexdigest()}
        if vpay.get("config") != payload.get("config"):
            raise SystemExit("the value artifact's config differs from the policy checkpoint")
        value_params = jax.tree_util.tree_map(jnp.asarray, vpay["params"])
    else:
        value_payload = {"vpath": str(checkpoint), "sha256": digest[str(checkpoint)]}
        value_params = params

    with np.load(data_path, allow_pickle=False) as data:
        n_avail = len(data["hp"])
        count = min(args.samples, n_avail)
        if count < 2:
            raise SystemExit("need at least two corpus rows")
        chosen = np.random.default_rng(args.seed).choice(n_avail, count, replace=False)
        tables = {k: jnp.asarray(data[k][chosen]) for k in TABLE_KEYS}
        spec = {k: jnp.asarray(data[k][chosen]) for k in SPEC_KEYS if k in data}
    if any(t.shape != (count, cfg.n_actors, cfg.spells) for t in tables.values()):
        raise SystemExit("corpus actor/spell dimensions do not match the checkpoint")
    boss = np.any(np.asarray(spec["boss"]), axis=1) if "boss" in spec else np.zeros(count, bool)

    length = 2 * cfg.max_rounds
    finish = jax.jit(make_eval_rollout(cfg, length, opponent="heuristic", player="greedy"))
    advance = jax.jit(lambda s, a, t: step(s, a, t, cfg))

    @jax.jit
    def enemy_turn(s, t):
        moved = step(s, heuristic_actions(s, t, cfg), t, cfg)
        take = (s["acting_team"] == 1) & ~s["done"]
        return jax.tree_util.tree_map(
            lambda old, new: jnp.where(take.reshape((count,) + (1,) * (old.ndim - 1)), new, old),
            s, moved)

    def patched(acts, cand, target):
        out = dict(acts)
        out["spell"] = acts["spell"].at[:, 0].set(cand)
        out["target"] = acts["target"].at[:, 0].set(target)
        return out

    def value_source(arm):
        """Which artifact's `value` head a value-backed arm reads: the source's or the edit's.

        Both arms reading `value_params` is the bug this function exists to make impossible: the
        first run reported `value1ply` and `value1ply_edited` at exactly the same 0.0957 with
        exactly the same flips, which is not a finding about the edit, it is a wiring error that
        made one arm twice.
        """
        if "edited" in arm:
            if not args.value_checkpoint:
                raise SystemExit("%s needs --value-checkpoint" % arm)
            return value_params
        return params

    def value_of(s, acts, cand, target):
        """Terminal winner after casting candidate `cand` at `target` and finishing greedily.

        `step` resolves the whole turn -- the cast, any further casts the greedy finisher adds and
        the handover -- so one candidate is one *turn*, not one attack, which is the same unit the
        cached labels were built in.
        """
        return finish(params, step(s, patched(acts, cand, target), tables, cfg), tables,
                      jax.random.PRNGKey(args.seed))

    def one_ply_value(s, acts, cand, target, vparams, after_enemy=False):
        """The value head's estimate of the successor state, read from the player's side.

        `observe` expresses everything relative to the actor, and after a cast the actor is usually
        the monster side, so the acting team is pinned to 0 here. Without that the arm would be
        backing up the opponent's value and calling it foresight.

        `after_enemy` plays the monsters' reply before reading. That is the hypothesis the first
        value arms refuted a different way: the head was trained on states where the side whose
        value it reports is the side to act, so a bare successor state -- where it is the monsters'
        turn -- is off-distribution by construction, and the −0.10 it produced is the price of
        asking it a question it never saw. If the reply-then-read arm recovers, the defect is the
        query, not the head.
        """
        after = step(s, patched(acts, cand, target), tables, cfg)
        if after_enemy:
            after = enemy_turn(after, tables)
        zero = jnp.zeros_like(after["acting_team"])
        obs2, glob2, attn2 = observe(after, tables, cfg, acting_team=zero)
        logits2 = model.forward(vparams, obs2, glob2, attn2,
                                heads=int(payload.get("heads", 4)))
        return np.asarray(logits2["value"], np.float64)

    def play(arm):
        """One arm over the same boards. Returns per-board terminal winner and timings."""
        state = initial_state(spec, tables, cfg)
        width = cfg.spells + 1                       # the pass slot is the last column
        rng = np.random.default_rng(args.seed + 7)
        t_play = time.perf_counter()
        decisions = 0
        valueless = 0
        tied = 0
        disagree = 0.0
        picks = []
        for _ in range(2 * length):
            # Move the scripted side whenever a board is waiting on it. On this corpus most
            # boards open on the monsters, so the very first iteration would otherwise see no
            # active player board at all and stop with every fight unfinished.
            state = enemy_turn(state, tables)
            active = np.asarray(~state["done"] & (state["acting_team"] == 0))
            if not active.any() or (args.decisions and decisions >= args.decisions):
                break
            obs, glob, attention = observe(state, tables, cfg)
            masks = action_masks(state, tables, cfg)
            logits = model.forward(params, obs, glob, attention)
            acts = model.greedy_actions(logits, masks, cfg)
            if arm != "greedy":
                scores = np.full((count, width), -1.0, np.float64)
                targets = np.zeros((count, width), np.int32)
                legal = np.zeros((count, width), bool)
                for cand in range(width):
                    tmask = masks["target"][:, 0, cand]
                    tlogits = logits["target"][:, 0, cand]
                    target = jnp.argmax(jnp.where(tmask, tlogits, -1e9), -1)
                    if arm in ("search", "random_legal"):
                        outcome = np.asarray(value_of(state, acts, cand, target)["winner"])
                    else:
                        outcome = one_ply_value(state, acts, cand, target, value_source(arm),
                                                after_enemy=arm.endswith("_after_enemy"))
                    ok = np.asarray(masks["spell"][:, 0, cand]) & active
                    legal[:, cand] = ok
                    if arm in ("search", "random_legal"):
                        scores[ok, cand] = (outcome[ok] == 0).astype(np.float64)
                    else:
                        scores[ok, cand] = outcome[ok]
                    targets[:, cand] = np.asarray(target)
                # How much of the rollout's advantage the value function reproduces is read from
                # the win rates: `value1ply` and `search` are the same decision rule with two
                # different value sources, so their gap is the price of rolling to a terminal
                # state rather than trusting the head. The correlation between the head's value
                # and the terminal outcome is measured separately, by `wsp_lose_predictor`.
                best_value = scores.max(axis=1)
                legal_min = np.where(legal, scores, np.inf).min(axis=1)
                n_tied = (legal & (scores == best_value[:, None])).sum(axis=1)
                # A board whose legal candidates all end the same way is a board the values cannot
                # help on; `tied` is the boards where the best value is shared, which is where the
                # tie-break rule -- not the search -- picks the action.
                valueless += int(((legal_min == best_value) & active).sum())
                tied += int(((n_tied > 1) & active).sum())
                best_search = scores.argmax(axis=1)
                best_random = choose_random_legal(legal, rng)
                # The witness that the control is not the treatment in disguise: computed on the
                # same board at the same decision, so the rate is real rather than assumed. It has
                # to be near 1 - 1/n_legal; a previous version of this arm permuted the value
                # columns and so agreed with `search` on every decision by construction.
                disagree += float((best_search[active] != best_random[active]).mean())
                best = best_random if arm == "random_legal" else best_search
                acts = dict(acts)
                acts["spell"] = acts["spell"].at[:, 0].set(best)
                acts["target"] = acts["target"].at[:, 0].set(
                    targets[np.arange(count), best])
            picks.append(np.asarray(acts["spell"])[:, 0].astype(np.int64))
            state = advance(state, acts, tables)
            decisions += 1
        winner = np.asarray(state["winner"])
        done = np.asarray(state["done"])
        if not done.all():
            raise SystemExit("arm %r did not reach a terminal state on every board" % arm)
        if not np.isin(winner, (0, 1, 2)).all():
            raise SystemExit("arm %r produced an unreadable winner" % arm)
        return {"winner": winner, "rounds": np.asarray(state["round"]),
                "decisions": decisions, "seconds": time.perf_counter() - t_play,
                "valueless": round(valueless / max(1, decisions * count), 4),
                "tied": round(tied / max(1, decisions * count), 4),
                "control_disagrees_with_search": round(disagree / max(1, decisions), 4),
                # The sequence of columns this arm actually played, hashed. Comparing outcomes is
                # too weak a witness at small samples -- two different policies can win and lose
                # the same boards -- whereas a identical choice sequence means one arm is not
                # reading what it claims to.
                "choice_signature": hashlib.sha256(
                    np.concatenate(picks).tobytes() if picks else b"").hexdigest()[:16]}

    arms = {}
    play_arms = sorted(set(args.arms) | {"greedy"})
    for arm in play_arms:
        arms[arm] = play(arm)
        print(json.dumps({"arm": arm, "win_rate": round(float((arms[arm]["winner"] == 0).mean()), 4),
                          "seconds": round(arms[arm]["seconds"], 2),
                          "control_disagrees_with_search":
                              arms[arm]["control_disagrees_with_search"]}), flush=True)

    # Wiring witness: two arms that play the same columns in the same order are one arm, however
    # their summaries differ. This is the check that was missing when the first five-arm run
    # reported 0.0957 for both value arms, which was one arm computed twice, not a null result.
    seen = {}
    for a in arms:
        sig = arms[a]["choice_signature"]
        if sig in seen:
            raise SystemExit("arms %r and %r played identical columns throughout; they are the "
                             "same arm, so any difference reported between them is not a "
                             "measurement" % (seen[sig], a))
        seen[sig] = a

    # Witness, not a nicety: the greedy arm is this tool's own loop, and the harness score the
    # whole ladder is quoted against comes from `make_eval_rollout`. If the two disagree on the
    # same boards with the same weights, the search number is measuring my loop and not the game.
    check = np.asarray(finish(params, initial_state(spec, tables, cfg), tables,
                              jax.random.PRNGKey(args.seed))["winner"])
    mismatch = int((check != arms["greedy"]["winner"]).sum())
    if mismatch:
        raise SystemExit("the greedy arm disagrees with the shipped eval rollout on %d of %d "
                         "boards; fix the loop before reading the search number"
                         % (mismatch, count))
    # And the control has to be a control: if the random arm picked the same column as the search
    # almost always, the two arms would be one arm and the decomposition below would be a number
    # without a meaning. 0.4 is a floor set by the legal action count, not a measurement.
    disagree = max((arms[a]["control_disagrees_with_search"] for a in arms if a != "greedy"),
                   default=0.0)
    ref = "search" if "search" in arms else (
        [a for a in arms if a != "greedy"][0] if len(arms) > 1 else "greedy")
    if ref != "greedy" and disagree < 0.4:
        raise SystemExit("the random-legal control agreed with the search on %.1f%% of decisions; "
                         "it is not independent of the values, so `values_buy` below is empty"
                         % (100.0 * (1.0 - disagree)))

    g = arms["greedy"]["winner"]
    wins = {a: float((arms[a]["winner"] == 0).mean()) for a in arms}
    others = [(a, arms[a]["winner"]) for a in arms if a != "greedy"]
    flips = {a: {"loss_to_win": int(((g != 0) & (w == 0)).sum()),
                 "win_to_loss": int(((g == 0) & (w != 0)).sum()),
                 "unchanged": int(((g == 0) == (w == 0)).sum())}
             for a, w in others}
    boot = {}
    for a in arms:
        w = (arms[a]["winner"] == 0).astype(np.float64)
        boot[a] = _boots(w, args.reps, np.random.default_rng(args.seed))
    delta = {a: _boots((w == 0).astype(np.float64) - (g == 0).astype(np.float64), args.reps,
                       np.random.default_rng(args.seed))
             for a, w in others}
    per_decision = {a: round(1000.0 * arms[a]["seconds"] / max(1, arms[a]["decisions"] * count), 2)
                    for a in arms}
    out = {"data": str(data_path), "checkpoint": str(checkpoint), "boards": count,
           "boss_boards": int(boss.sum()), "seed": args.seed,
           "config": cfg.__dict__, "decisions_per_fight": arms[ref]["decisions"],
           "win_rate": {a: round(wins[a], 4) for a in arms},
           "win_rate_ci95": boot,
           "paired_flips_vs_greedy": flips,
           # The decomposition the plan needs: `departing_buy` is what leaving the head's own argmax
           # for a random legal spell achieves, `values_buy` is the extra the rolled-out outcomes
           # add on top. A search arm whose gain sits in `departing_buy` is not a planner, it is a
           # randomiser, and the fix belongs in the head.
           "delta_vs_greedy": {a: round(wins[a] - wins["greedy"], 4) for a, _ in others},
           "delta_ci95": delta,
           "values_buy_beyond_random_legal": (round(wins["search"] - wins["random_legal"], 4)
                                          if {"search", "random_legal"} <= set(wins)
                                          else None),
           "control_disagrees_with_search": disagree,
           "boss_subset_win_rate": {a: round(float((arms[a]["winner"][boss] == 0).mean()), 4)
                                    for a in arms},
           "nonboss_subset_win_rate": {a: round(float((arms[a]["winner"][~boss] == 0).mean()), 4)
                                       for a in arms},
           "mean_rounds": {a: round(float(arms[a]["rounds"].mean()), 2) for a in arms},
           "ms_per_decision_per_board": per_decision,
           "search_cost_vs_greedy": round(per_decision[ref] / max(1e-9, per_decision["greedy"]), 1),
           "reference_arm": ref,
           "choice_signatures": {a: arms[a]["choice_signature"] for a in arms},
           "valueless_decision_share": arms[ref]["valueless"],
           "ties_at_best_share": arms[ref]["tied"],
           "value_artifact": value_payload,
           "score_metric": "terminal_player_win_v1",
           "scope": "abstract simulator; the search arm reads the same env it is scored in, so it "
                    "is a planning result, not evidence about live Touch AI",
           "source_sha256": digest}
    text = json.dumps(out, indent=1, sort_keys=True)
    if args.out:
        pathlib.Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
