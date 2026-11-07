"""Terminal-outcome harness for weight-space edits to the fight policy.

Why this exists: the shipped ranking path (`combat/plan_value.rank_plans`) reads only
`forward()["value"]`, so an edit to the *action* heads -- the move/spell/target logits that
`/v1/combat/decide` actually plays -- cannot show up in that benchmark. This measures the thing
that matters instead: does the network win duels, played to a terminal result through the same
`combat.duel.play_game` engine `duel_benchmark` uses.

Design rules this obeys (they are the difference between a measurement and a story):

* **Side-neutral.** Every matchup is played twice, the network on each side, and the score is the
  average. Initiative and map advantage otherwise get credited to the weights.
* **Paired.** Arms share the matchup list, the shuffle and the per-game RNG seed, so an edit's
  effect is read per game (win/loss flips), not as two marginal rates.
* **A control that can move the number.** `random` flips the signs of `--edits` distinct
  coordinates of the chosen head. If a motivated edit moves the metric less than the control of the
  same size, the eval is measuring noise. (Until 2026-11-03 this branch flipped exactly one
  coordinate whatever `--edits` said, so a "control moved it more than the edit" reading at n=40 was
  comparing a no-op to a no-op.)
* **An edit is named, not vibes.** `--head move` takes the three move-direction biases in
  `_move_dest` / `env.step` order: **approach, hold, retreat** (index 0 closes distance, 2 opens it).
  The harness docstring used to say retreat/stay/approach and to cite "97 of 105 boards chose
  retreat"; the 135 recorded boards now answer approach 42 / retreat 11, so that premise is stale
  and the arms are read from the tiers, not from this paragraph.

    python tools/wsp_fight.py base [--matchups 40] [--policy adapter|production]
    python tools/wsp_fight.py bias --head move --bias 8,0,0      # favor approach
    python tools/wsp_fight.py random --head spell --edits 4 --arm-seed 1
    python tools/wsp_fight.py compare base.json other.json

`--harness pvm` evaluates raw encounter tensors against a scripted heuristic or random opponent.
Its win metric counts only terminal player wins; draws receive no win credit. This is an abstract
simulation, not the client's monster AI or evidence of live boss/challenge completion. The default
duel harness is seat-averaged network versus solver, not self-play constrained to a 0.5 score.

    python tools/wsp_fight.py base --harness pvm --data encounters_boss_s4_2048.npz --out pvm_base.json
    python tools/wsp_fight.py base --harness pvm --checkpoint arm.pkl --out pvm_arm.json
    python tools/wsp_fight.py compare pvm_base.json pvm_arm.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import pickle
import random
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
BRAIN = REPO / "dofus-network"
if str(BRAIN) not in sys.path:
    sys.path.insert(0, str(BRAIN))

import numpy as np  # noqa: E402

CHECKPOINT = BRAIN / "models" / "encounter_selfplay.pkl"


def network_policy(nn, stats):
    """Pick among the solver's candidate plans with the trained action heads.

    The network emits (move, spell_slot, target_slot); those are matched back onto a candidate
    plan, because `play_game` consumes TurnPlans. When nothing matches, the solver's own best is
    used and the miss is counted -- a high fallback rate is itself a finding about the head.
    """
    from combat.duel import apply_plan, board_for
    from combat.plan_value import board_encounter

    def choose(state, actor, plans, rng, _model=None):
        from combat.battle_solver import BattleSolver  # noqa: F401  (kept for clarity)
        plans = list(plans)
        board = board_for(state, actor)
        try:
            slots, player_rows = nn._bind_slots(board)
            targets = nn._bind_targets(board)
            tables, spec = board_encounter(board, plans[0], nn.cfg, first_team=0,
                                           player_rows=player_rows, as_jax=False)
            logits, masks = nn._bundle(nn.params, spec, tables, float(max(0, board.my_ap)),
                                       float(max(0, board.my_mp)), int(board.round or 0))
            move, spell_slot, target_slot, probability, value = nn._choose(logits, masks)
        except Exception:  # noqa: BLE001 - the harness must not die on one board
            stats["errors"] += 1
            return plans[0]
        stats["games_turns"] += 1
        stats["actions"] += 1
        stats["probability_actions"] += 1
        stats["probability_sum"] += probability
        want_spell = slots[spell_slot]["spell_id"] if 0 <= spell_slot < len(slots) else None
        want_target = (targets[target_slot].get("fighter_id")
                       if 0 <= target_slot < len(targets) else None)
        for plan in plans:
            first = plan.actions[0] if plan.actions else None
            if first is None:
                continue
            if first.kind == "pass" and want_spell is None:
                return plan
            if first.kind == "cast" and getattr(first, "spell_id", None) == want_spell \
                    and getattr(first, "target_id", None) == want_target:
                return plan
        stats["fallbacks"] += 1
        return plans[0]
    return choose


def load_arm(mode, head, bias, edits, seed, checkpoint=None):
    """Return (checkpoint_path, arm_note) after applying the requested weight-space edit.

    `mode=base` must be byte-identical to the checkpoint it is given, and every other mode reports how
    many coordinates it actually touched, because an edit the adapter cannot express shows up as a
    suspiciously clean zero rather than as the non-result it is.

    `checkpoint` exists because the shipped pkl is not the only arm worth scoring: a distilled or
    fine-tuned checkpoint is a whole file, not an edit of this one, and without this parameter the
    harness could only ever re-measure the incumbent. The note names the file whenever it is not the
    incumbent, so a result JSON says which weights produced it.
    """
    checkpoint = CHECKPOINT if checkpoint is None else pathlib.Path(checkpoint)
    payload = pickle.loads(checkpoint.read_bytes())
    if mode == "base":
        note = ("unmodified checkpoint" if checkpoint == CHECKPOINT
                else "unmodified checkpoint %s" % checkpoint.name)
        return str(checkpoint), note
    params = payload["params"]
    original = np.asarray(params[head][1], dtype=np.float32)
    if mode == "bias":
        delta = np.asarray(bias, dtype=np.float32)
        if delta.size != original.size:
            raise SystemExit("--bias needs %d values for head %s" % (original.size, head))
        params[head] = (np.asarray(params[head][0], dtype=np.float32), original + delta)
        note = "%s-head bias %s -> %s" % (head, list(np.round(original, 4)),
                                           list(np.round(original + delta, 4)))
        touched = int((delta != 0).sum())
    elif mode == "random":
        rng = random.Random(seed)
        flat = original.copy()
        count = max(1, min(int(edits), flat.size))
        picked = rng.sample(range(flat.size), count)
        for index in picked:
            flat[index] = -float(flat[index])
        params[head] = (np.asarray(params[head][0], dtype=np.float32), flat)
        # A sign flip of a zero coordinate moves nothing, so the note reports the coordinates that
        # actually changed -- the control has to be able to alter a decision, and say how much.
        moved = int((np.abs(flat - original) > 0.0).sum())
        note = "random sign flip of %d %s coordinate(s) %s; %d non-zero and moved" % (
            count, head, picked[:6], moved)
        touched = moved
    else:
        raise SystemExit("unknown mode " + mode)
    out = pathlib.Path(os.environ.get("TEMP", str(pathlib.Path(os.environ["LOCALAPPDATA"]) / "Temp")))
    path = out / ("arm_%s_%s_%d.pkl" % (mode, head, int(time.time() * 1000) % 100000))
    payload["params"] = params
    path.write_bytes(pickle.dumps(payload))
    return str(path), "%s; coordinates touched=%d" % (note, touched)


def production_policy(nn, stats, solver, max_actions, abstain=0.0, drop_ap0_walks=False,
                      abstain_class="none"):
    """The network side played by `LivePolicy.decide()` itself, not by an adapter of it.

    The reason this exists: every head-vs-solver number before §L.18 came from a hand-rolled
    adapter that matched the actor's (spell, target) onto a candidate plan and took the solver's
    best when nothing matched. That is not the rule the shipped client runs -- `_realize` also has
    a direct-cast branch, a walk-to-cast-cell branch and the value ranker. `Board.to_fight_state()`
    makes the real payload expressible from a duel state, and `solver.plan_for_action()` turns the
    action back into something `play_game` can execute, so the production function is asked
    exactly as the page asks it.

    One engine turn is therefore several `decide()` calls: the page replans after every action, and
    the walk it chooses is not a decision the solver pays for either -- the beam folds its walk
    into the cast at no cost in actions. So casts are capped at the beam's own `max_actions` and
    walks are charged MP but no cast slot. The cap is reported in `turn_cast_cap`, not assumed.

    The mapping is scored, never hidden: `tiers` counts actions applied as cast / as walk / as an
    ending pass, actions the duel model could not realize, and turns that fell back to the solver's
    top plan. `cast_outside_beam` is the interesting one -- it is the shipped rule playing a pair
    the beam never offered. A run that leans on the fallback is the old adapter wearing a different
    hat, and the summary says so.
    """
    from combat.battle_solver import PlannedAction, TurnPlan
    from combat.duel import apply_plan, board_for

    def choose(state, actor, plans, rng, _model=None):
        plans = list(plans)

        def record(tier):
            stats["tiers"][tier] = stats["tiers"].get(tier, 0) + 1

        stats["games_turns"] += 1
        # The (spell, target) pairs the beam carried for this turn's opening state. Computed once:
        # the `--abstain-class outside_beam` gate and the `cast_outside_beam` tier are the same
        # question, and asking it per cast rebuilt the same set.
        beam_pairs = {(first.spell_id, first.target_id) for first
                      in (p.actions[0] if p.actions else None for p in plans)
                      if first is not None and first.kind == "cast"}
        work = state.clone()
        actions = []
        scores = []
        damage = healing = 0.0
        kills = []
        declared_pass = False
        blocked = False
        casts = 0
        # The beam charges nothing for the walk it folds into a cast, so a walk cannot be
        # allowed to spend the turn's action budget here or the two sides would not be
        # playing the same game. AP buys casts, MP buys steps, and both are charged for
        # real; the count only stops the loop from running past the beam's own depth.
        for _step in range(int(max_actions) + 2):
            board = board_for(work, work.by_id(actor.id))
            if work.actor.ap <= 0 and board.my_mp <= 0:
                break
            try:
                decision = nn.decide(board.to_fight_state())
            except Exception:  # noqa: BLE001 - the harness must not die on one board
                stats["errors"] += 1
                record("decide_failed")
                blocked = True
                break
            action = decision.get("action") or {}
            policy = decision.get("policy") or {}
            reason = policy.get("reason") or decision.get("reason") or "?"
            stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1
            stats["probability_sum"] += float(policy.get("probability") or 0.0)
            stats["probability_actions"] += 1
            kind = action.get("type")
            if kind == "move" and drop_ap0_walks and int(board.my_ap) <= 0:
                # 21% of the recorded live asks arrive with no AP left, where the only thing the
                # brain can do is walk. Whether that walk is worth the round trip is measurable
                # rather than arguable: end the turn instead and read the paired delta against the
                # arm that plays it. It is the client's "do not ask at all" decision, expressed as
                # the action the ask would have produced.
                record("dropped_walk_with_no_ap")
                break
            in_class = (kind == "move" and abstain_class == "any_walk") or (
                kind == "cast" and abstain_class == "outside_beam"
                and (action.get("spellId"), action.get("targetId")) not in beam_pairs)
            if in_class:
                # §L.29 says the head's deficit is concentrated in a small class of decisions, so
                # gate on the class rather than on confidence and read the paired delta. Stopping is
                # the honest mid-turn action -- the beam's candidates were built for the opening cell
                # and cannot be appended after actions already spent this turn. With nothing played
                # yet the turn goes to the beam, which is a solver stand-in and counted as one,
                # because a win rate has to travel with the share the beam decided (§L.25's lesson).
                record("abstained_" + abstain_class)
                if not actions:
                    stats["fallbacks"] += 1
                    return plans[0]
                break
            if abstain and kind in ("cast", "move") \
                    and float(policy.get("probability") or 0.0) < float(abstain):
                # The head is not sure: hand the turn to the beam instead of playing a coin-flip
                # cast. This is a solver stand-in, so it counts as one -- a rate that hides its own
                # substitution is how §L.25's control nearly read as an improvement in the policy.
                record("abstained_below_confidence")
                stats["fallbacks"] += 1
                return plans[0]
            if kind == "pass":
                record("pass_as_declared")
                # A pass is only honest if nothing else was available. The `plans` handed to this
                # callback were built for the turn's opening cell, and the loop has already spent
                # AP and MP since, so they are recomputed here -- an "it could have cast" verdict
                # against a stale candidate set would blame the policy for a spell it had already
                # paid for.
                if not actions:
                    fresh = plans
                else:
                    try:
                        fresh = nn.solver.plan_candidates(board, nn.plan_k)
                    except Exception:  # noqa: BLE001 - the counter must never cost the turn
                        fresh = plans
                if fresh and fresh[0].actions and fresh[0].actions[0].kind != "pass":
                    record("passed_while_a_cast_existed")
                declared_pass = (not actions)
                break
            if kind == "cast" and casts >= int(max_actions):
                record("turn_capped_at_solver_depth")
                break
            plan = solver.plan_for_action(board, action)
            if plan is None:
                record("unrealizable_" + str(kind))
                blocked = True
                break
            if kind == "cast":
                casts += 1
                record("cast_outside_beam" if (action.get("spellId"),
                                                action.get("targetId")) not in beam_pairs
                       else "cast_in_beam")
            record("cast_applied" if kind == "cast" else "walk_applied")
            actions.extend(plan.actions)
            scores.append(plan.score)
            damage += plan.expected_damage
            healing += plan.expected_healing
            kills.extend(plan.kills)
            stats["actions"] += 1
            apply_plan(work, plan)
            if work.done:
                break
        if not actions:
            # A pass the network chose is a decision, not a miss: it is played. Only a turn the
            # duel model could not realize at all stands in with the solver, and that is the
            # number the summary's fallback rate is about.
            if declared_pass or not blocked:
                return TurnPlan(actions=[PlannedAction("pass")],
                                final_cell=int(actor.cell),
                                remaining_ap=int(round(actor.ap)),
                                remaining_mp=float(actor.mp), expected_damage=0.0,
                                expected_healing=0.0, kills=[], score=0.0,
                                final_hp={f.id: int(round(f.hp))
                                          for f in state.fighters})
            stats["fallbacks"] += 1
            record("solver_best_for_the_turn")
            return plans[0]
        live = work.actor
        return TurnPlan(actions=actions, final_cell=int(live.cell),
                        remaining_ap=int(round(live.ap)),
                        remaining_mp=float(live.mp), expected_damage=damage,
                        expected_healing=healing, kills=kills,
                        score=float(sum(scores)),
                        notes=["live realization: %d action(s)" % len(actions)],
                        final_hp={f.id: int(round(f.hp)) for f in work.fighters})
    return choose


def wrap_origin(nn, mode, counter):
    """Swap how `_cast_cell` picks among the legal reachable origins (#75).

    `cells.reachable` comes back in BFS order, so the shipped rule takes the *cheapest* cell the
    cast lands from and ends the turn there -- while `policy_approach`, one branch over, spends the
    whole MP bar moving as close as it can. `closest` applies that intent to the cast branch too:
    among the cells the chosen pair is legal from, the one nearest the target.

    Both arms compute both answers, so `counter` records how often the two rules disagree even when
    the shipped one is played: an arm that changes nothing is a non-result, and the run should say
    so rather than present a zero delta as a measured refusal to move.
    """
    if mode not in ("first", "closest"):
        raise SystemExit("unknown --origin " + mode)

    def chosen(board, slot, target):
        pair = nn._castable_pair(board, slot, target)
        if pair is None:
            return None
        spell, _fighter, target_cell = pair
        occupied = frozenset(f.cell for f in board.fighters
                             if f.alive and f.id != board.my_id)
        anchor = int(target.get("cell"))
        first = None
        best = None
        best_distance = None
        for cell in board.cells.reachable(board.my_cell, board.my_mp, occupied):
            if cell == board.my_cell:
                continue
            if not nn._legal(board, spell, cell, target_cell, occupied):
                continue
            if first is None:
                first = cell
            distance = board.cells.distance(cell, anchor)
            if best_distance is None or distance < best_distance:
                best, best_distance = cell, distance
        if first is not None:
            counter["calls"] += 1
            counter["differ"] += int(best != first)
        return first if mode == "first" else best

    nn._cast_cell = chosen


def play(arm_path, matchups, beam_width, max_actions, k, seed, policy="adapter",
         origin="first", abstain=0.0, drop_ap0_walks=False, abstain_class="none",
         data_path=None):
    from combat import duel as du
    from combat.battle_solver import BattleSolver
    from combat.duel import make_duel, play_game, solver_policy
    from combat.live_policy import LivePolicy

    nn = LivePolicy(checkpoint=pathlib.Path(arm_path), beam_width=beam_width,
                    max_actions=max_actions)
    cells = du.load_map(du.DEFAULT_MAP)
    data = np.load(str(data_path or du.DEFAULT_DATA))
    solver = BattleSolver(beam_width=beam_width, max_actions=max_actions)
    stats = {"errors": 0, "fallbacks": 0, "games_turns": 0, "probability_sum": 0.0,
             "probability_actions": 0, "actions": 0, "tiers": {}, "reasons": {}}
    # The counters live on the policy itself, so the arm reports the delta it caused rather than
    # whatever an earlier arm accumulated in the same process.
    baseline = nn.value_choice_stats
    origin_counter = {"calls": 0, "differ": 0}
    if policy == "production":
        wrap_origin(nn, origin, origin_counter)
    network_choose = (production_policy(nn, stats, solver, max_actions, abstain,
                                        drop_ap0_walks, abstain_class)
                      if policy == "production" else network_policy(nn, stats))
    results = []
    for index, (row_a, row_b) in enumerate(matchups):
        score = 0.0
        game_turns = game_falls = 0
        for network_side in (1, 2):
            rng = random.Random(seed * 100003 + index * 7 + network_side)
            state = make_duel(data, row_a, row_b, cells, rng=rng)
            if state is None:
                score = None
                break
            before = (stats["games_turns"], stats["fallbacks"])
            policies = {1: solver_policy, 2: solver_policy}
            policies[network_side] = network_choose
            final, _records = play_game(state, policies, solver, rng, k=k)
            game_turns += stats["games_turns"] - before[0]
            game_falls += stats["fallbacks"] - before[1]
            winner = final.winner
            if winner is None:
                score += 0.5
            else:
                score += 1.0 if winner == network_side else 0.0
        if score is not None:
            results.append({"matchup": [row_a, row_b], "network_score": score / 2.0,
                            "turns": game_turns, "fallbacks": game_falls})
    turns = max(1, stats["games_turns"])
    after = nn.value_choice_stats
    value_choice = {key: after[key] - baseline[key] for key in ("asked", "fired")}
    paid_actions = max(1, stats["probability_actions"])
    return results, {"games": len(results) * 2, "network_turns": stats["games_turns"],
                     "fallbacks": stats["fallbacks"],
                     "fallback_rate": round(stats["fallbacks"] / turns, 4),
                     "mean_probability": round(stats["probability_sum"] / paid_actions, 4),
                     "network_actions": stats["actions"],
                     "actions_per_turn": round(stats["actions"] / turns, 3),
                     "turn_cast_cap": (max_actions if policy == "production" else 1),
                     # `fallback_rate` counts only the harness standing in with a solver plan, and
                     # that is not enough: §L.25's control raised the win rate by +0.041 by making
                     # `decide()` let its own beam choose on 11.1% of calls instead of 3.2%, with the
                     # fallback rate pinned at 0.0 the whole time. A win rate has to travel with the
                     # share of it the solver decided, or it can be a measurement of the solver.
                     "solver_decided_rate": round(
                         (stats["reasons"].get("solver_fallback", 0)
                          + stats["reasons"].get("solver_move", 0))
                         / max(1, sum(stats["reasons"].values())), 4),
                     "value_choice": dict(value_choice),
                     "cast_origin": dict(origin_counter, mode=origin), "abstain": abstain,
                     "abstain_class": abstain_class,
                     "drop_ap0_walks": bool(drop_ap0_walks),
                     "errors": stats["errors"], "policy_source": policy,
                     "action_tiers": stats["tiers"], "reasons": stats["reasons"]}


def summary(results, meta, note):
    scores = [r["network_score"] for r in results]
    # A turn where the network's (spell, target) pair matched no candidate fell back to the
    # solver's pick, so that game is partly the solver playing. Report both views: the raw rate and
    # the rate over games the network actually decided end to end.
    decided = [r["network_score"] for r in results if r.get("fallbacks") == 0]
    out = {"arm": note, "meta": meta, "matchups": len(scores),
           "network_win_rate": round(float(np.mean(scores)), 4) if scores else None,
           "solver_decided_rate": meta.get("solver_decided_rate"),
           "games_fully_decided": len(decided),
           "win_rate_when_decided": round(float(np.mean(decided)), 4) if decided else None,
           "wins": sum(1 for s in scores if s > 0.5), "draws": sum(1 for s in scores if s == 0.5),
           "losses": sum(1 for s in scores if s < 0.5)}
    # The matchup is the unit of replication (each is played twice, network on each side), so a
    # bare 0.1875 without an interval is not a result -- at 40 matchups the random-edit control
    # moved the number further than a motivated edit did. Bootstrap the mean over matchups, and
    # give the exact one-sided sign probability against parity, which is the question the shipped
    # path actually asks: is what `decide()` plays worse than the deterministic solver it replaced?
    if scores:
        rng = random.Random(0)
        boots = sorted(float(np.mean(rng.choices(scores, k=len(scores))))
                       for _ in range(4000))
        out["win_rate_bootstrap_ci95"] = [round(boots[int(0.025 * len(boots))], 4),
                                         round(boots[int(0.975 * len(boots))], 4)]
        n = out["wins"] + out["losses"]
        if n:
            from math import comb
            tail = sum(comb(n, i) for i in range(0, out["wins"] + 1)) / (2.0 ** n)
            out["sign_test_p_worse_than_solver"] = round(tail, 8)
    return out


def compare(base_path, other_path):
    a = json.loads(pathlib.Path(base_path).read_text(encoding="utf-8"))
    b = json.loads(pathlib.Path(other_path).read_text(encoding="utf-8"))
    ac, bc = a.get("config", {}), b.get("config", {})
    if ac.get("data_sha256") != bc.get("data_sha256"):
        raise ValueError("paired results must use the same verified encounter table")
    for field in ("seed", "matchup_seed", "beam_width", "max_actions", "plan_k",
                  "harness", "opponent", "samples"):
        if ac.get(field) != bc.get(field):
            raise ValueError("paired results differ in " + field)
    if ac.get("harness") == "pvm":
        am, bm = a["summary"]["meta"], b["summary"]["meta"]
        for field in ("score_metric", "source_sha256", "config", "backend",
                      "jax_version", "numpy_version", "start_row", "action_selection"):
            if field not in am or field not in bm or am[field] != bm[field]:
                raise ValueError("PvM paired results lack matching " + field)
        if am["score_metric"] != "terminal_player_win_v1":
            raise ValueError("PvM results must score terminal player wins")
        for document in (a, b):
            for row in document["results"]:
                if row.get("winner") not in (0, 1, 2) or row["network_score"] != float(row["winner"] == 0):
                    raise ValueError("PvM result is not a verified terminal player-win score")
    key = lambda r: tuple(r["matchup"])
    if any(len({key(r) for r in d["results"]}) != len(d["results"]) for d in (a, b)):
        raise ValueError("paired results contain duplicate matchups")
    left = {key(r): r["network_score"] for r in a["results"]}
    right = {key(r): r["network_score"] for r in b["results"]}
    shared = sorted(set(left) & set(right))
    if not shared or set(left) != set(right):
        raise ValueError("paired results must cover identical nonempty matchups")
    deltas = [right[m] - left[m] for m in shared]
    ups = sum(1 for d in deltas if d > 0)
    downs = sum(1 for d in deltas if d < 0)
    draws = len(deltas) - ups - downs
    mean = float(np.mean(deltas)) if deltas else 0.0
    boot = []
    rng = random.Random(0)
    for _ in range(4000):
        boot.append(float(np.mean(rng.choices(deltas, k=len(deltas)))) if deltas else 0.0)
    boot.sort()
    n = ups + downs
    p_two = 1.0
    if n:
        from math import comb
        # The tail is the probability that the *smaller* side of a coin comes up that few times,
        # so it sums 0..min(ups, downs). Summing min..n instead returns P(X >= 19) ~ 1 for every
        # outcome whatsoever: the field printed 1.0 for a 140-0 split and for a 70-70 split alike,
        # which is how it stayed unnoticed while every paired delta was called inconclusive.
        tail = sum(comb(n, i) for i in range(0, min(ups, downs) + 1)) / (2.0 ** n)
        p_two = min(1.0, 2.0 * tail)
    return {"paired_matchups": len(shared), "mean_score_delta": round(mean, 4),
            "bootstrap_ci95": [round(boot[int(0.025 * len(boot))], 4),
                               round(boot[int(0.975 * len(boot))], 4)] if boot else None,
            "improved": ups, "worsened": downs, "unchanged": draws,
            "sign_test_p_two_sided": float("%.3g" % p_two),
            "base_win_rate": a["summary"]["network_win_rate"],
            "arm_win_rate": b["summary"]["network_win_rate"],
            "arm_notes": b["summary"]["arm"],
            # An edit that changes no decision is a non-result, not a null result: say so, because
            # "delta = 0.0, 40 unchanged" otherwise reads like a measured refusal to move.
            "verdict": ("NOT OBSERVABLE through this adapter (no matchup changed, and the run "
                        "statistics are identical)"
                        if not (ups or downs or abs(mean) > 0.0)
                        and a["summary"].get("meta") == b["summary"].get("meta")
                        else "read mean_score_delta with bootstrap_ci95 and the sign test")}


def summary_pvm(results, meta, note):
    """Report terminal wins separately from draws in the abstract encounter simulator."""
    if not results or any(r["winner"] not in (0, 1, 2) for r in results):
        raise ValueError("PvM summary requires nonempty terminal outcomes")
    scores = [float(r["winner"] == 0) for r in results]
    if any(r["network_score"] != score for r, score in zip(results, scores)):
        raise ValueError("PvM network_score must count only terminal player wins")
    out = {"arm": note, "meta": meta, "matchups": len(scores),
           "network_win_rate": round(float(np.mean(scores)), 4),
           "wins": sum(r["winner"] == 0 for r in results),
           "draws": sum(r["winner"] == 2 for r in results),
           "losses": sum(r["winner"] == 1 for r in results)}
    for name, keep in (("boss", [r for r in results if r["boss"]]),
                       ("non_boss", [r for r in results if not r["boss"]])):
        out[name] = {"boards": len(keep), "win_rate":
                     float(np.mean([r["winner"] == 0 for r in keep])) if keep else None}
    rng = random.Random(0)
    boots = sorted(float(np.mean(rng.choices(scores, k=len(scores))))
                   for _ in range(4000))
    out["win_rate_bootstrap_ci95"] = [round(boots[int(0.025 * len(boots))], 4),
                                      round(boots[int(0.975 * len(boots))], 4)]
    return out


def play_pvm(arm_path, data_path, samples, seed, opponent="heuristic", action_selection="sample"):
    """Play every board of a corpus to a terminal result with the arm's own weights.

    No solver, no adapter, no `LivePolicy.decide()`: the action heads choose directly through
    `combat.encounter.env`, so what is measured is the weights the edit wrote and nothing else. The
    monster side is `heuristic_actions`, the scripted policy the training loop reports against.
    """
    import jax
    import jax.numpy as jnp

    from combat.encounter import backend
    if backend.BACKEND != "jax":
        raise SystemExit("the pvm harness needs the jax backend (%s); run it on the training "
                         "interpreter, not an installed copy" % backend.JAX_IMPORT_ERROR)
    from combat.encounter.checkpoint import config_from_payload
    from combat.encounter.env import initial_state
    from combat.encounter.train import load_encounters, make_eval_rollout

    payload = pickle.loads(pathlib.Path(arm_path).read_bytes())
    cfg = config_from_payload(payload)
    blind = tuple(payload.get("blind") or ())
    params = jax.tree_util.tree_map(jnp.asarray, payload["params"])
    if samples is not None and samples < 1:
        raise ValueError("--samples must be positive")
    tables, spec = load_encounters(pathlib.Path(data_path), samples)
    count = int(spec["hp"].shape[0])
    if count == 0:
        raise ValueError("PvM corpus is empty")
    if any(t.shape != (count, cfg.n_actors, cfg.spells) for t in tables.values()):
        raise ValueError("PvM corpus actor/spell dimensions do not match checkpoint config")
    if payload["params"]["spell"][0].shape[1] != cfg.n_spells:
        raise ValueError("PvM checkpoint spell head does not match config")
    with np.load(data_path, allow_pickle=False) as data:
        start = int(data["hp"].shape[0]) - count
    state0 = initial_state(spec, tables, cfg)
    if action_selection not in ("sample", "greedy"):
        raise ValueError("unknown PvM action selection")
    rollout = jax.jit(make_eval_rollout(cfg, 2 * cfg.max_rounds, opponent=opponent,
                                      player="greedy" if action_selection == "greedy" else "learned",
                                      blind=blind))
    final = rollout(params, state0, tables, jax.random.PRNGKey(seed))
    winner = np.asarray(final["winner"])
    rounds = np.asarray(final["round"])
    if not np.asarray(final["done"]).all() or not np.isin(winner, (0, 1, 2)).all():
        raise ValueError("PvM rollout ended without verified terminal outcomes")
    boss = np.any(np.asarray(spec["boss"]), axis=1) if "boss" in spec else np.zeros(count, bool)
    results = [{"matchup": [start + index, -1], "winner": int(w),
                "network_score": float(w == 0), "boss": bool(boss[index]),
                "rounds": int(rounds[index])}
               for index, w in enumerate(winner)]
    source_files = [pathlib.Path(__file__)] + [BRAIN / "combat" / "encounter" / name
                    for name in ("env.py", "model.py", "train.py", "heuristic.py",
                                 "checkpoint.py", "backend.py")]
    meta = {"harness": "pvm", "opponent": opponent, "boards": len(results),
            "scope": "abstract simulator with scripted opposition, not live Touch AI",
            "score_metric": "terminal_player_win_v1", "backend": backend.BACKEND,
            "action_selection": action_selection,
            "jax_version": jax.__version__, "numpy_version": np.__version__,
            "source_sha256": {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in source_files},
            "mean_rounds": round(float(rounds.mean()), 3),
            "config": cfg.__dict__, "blind": list(blind), "start_row": start}
    return results, meta


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", nargs="?", default="base")
    ap.add_argument("paths", nargs="*",
                    help="for compare: <base.json> <arm.json>; other modes ignore positionals")
    ap.add_argument("--matchups", type=int, default=40)
    ap.add_argument("--data", default=None, help="encounter table reserved for this evaluation")
    ap.add_argument("--head", default="spell", choices=("spell", "move", "target_q", "value1"),
                    help="which action head's bias vector the edit touches")
    ap.add_argument("--bias", default="0,0,0,0,0")
    ap.add_argument("--edits", type=int, default=32)
    ap.add_argument("--arm-seed", type=int, default=None,
                    help="coordinate-selection seed, independent of the evaluation --seed")
    ap.add_argument("--seed", type=int, default=7, help="per-game RNG and arm randomness")
    ap.add_argument("--matchup-seed", type=int, default=11,
                    help="which rows become matchups; every arm of a comparison must share it")
    ap.add_argument("--beam-width", type=int, default=8)
    ap.add_argument("--max-actions", type=int, default=2)
    ap.add_argument("--plan-k", type=int, default=6)
    ap.add_argument("--policy", default="adapter", choices=("adapter", "production"),
                    help="'adapter' matches the actor's (spell, target) onto a candidate plan; "
                         "'production' asks LivePolicy.decide() after every action, the way the "
                         "client does, and caps the turn at --max-actions like the solver's beam")
    ap.add_argument("--origin", default="first", choices=("first", "closest"),
                    help="production arm only: which legal cast origin `_cast_cell` hands back -- "
                         "'first' is the shipped cheapest-in-BFS-order cell, 'closest' the one "
                         "nearest the target (#75)")
    ap.add_argument("--abstain", type=float, default=0.0,
                    help="production arm only: under this claimed probability the head hands the "
                         "turn to the solver's top plan instead of playing it (0 = never, which is "
                         "the shipped rule)")
    ap.add_argument("--ap0-walks", default="play", choices=("play", "drop"),
                    help="production arm only: on a turn with no AP left the brain can only walk. "
                         "'drop' ends the turn instead, which is what the client would do if it "
                         "stopped asking -- the paired delta prices those asks (#66)")
    ap.add_argument("--abstain-class", default="none",
                    choices=("none", "outside_beam", "any_walk"),
                    help="production arm only: hand the turn to the beam when the head's action is "
                         "in this class -- 'outside_beam' casts a (spell, target) pair the beam "
                         "never offered, 'any_walk' any walk (#80)")
    ap.add_argument("--action-selection", default="sample", choices=("sample", "greedy"),
                    help="PvM player action selection; greedy uses masked argmax")
    ap.add_argument("--harness", default="duel", choices=("duel", "pvm"),
                    help="duel: seat-averaged network versus solver; "
                         "pvm: abstract encounter simulator versus scripted opposition")
    ap.add_argument("--opponent", default="heuristic", choices=("heuristic", "random"),
                    help="pvm harness only: the monster policy. 'heuristic' is combat.encounter."
                         "heuristic_actions, the same reference the training loop reports")
    ap.add_argument("--samples", type=int, default=None,
                    help="pvm harness only: how many boards of --data to play, taken as the tail "
                         "the way the training loader slices them (default: all of them)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--checkpoint", default=None,
                    help="weights to score instead of the shipped %s: a distilled or fine-tuned "
                         "pkl of the same geometry. Edits (--head/--bias/--edits) are applied on "
                         "top of this file, so an arm is always (checkpoint, edit) and the run "
                         "records both." % CHECKPOINT.name)
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    if args.mode == "compare":
        if len(args.paths) != 2:
            raise SystemExit("compare needs exactly two json paths")
        print(json.dumps(compare(args.paths[0], args.paths[1]), indent=1))
        return 0
    if __name__ == "__main__":
        os.environ.setdefault("DOFUS_FIGHT_BACKEND", "jax" if args.harness == "pvm" else "numpy")
    data_path = pathlib.Path(args.data or BRAIN / "kaggle" / "tpu_selfplay" /
                             "dataset" / "encounters.npz").resolve()
    data_sha256 = hashlib.sha256(data_path.read_bytes()).hexdigest()
    data = np.load(data_path)
    rows = int(data["hp"].shape[0])
    picker = random.Random(args.matchup_seed)
    picks = picker.sample(range(rows), min(args.matchups * 2, rows))
    matchups = [(picks[i], picks[i + 1]) for i in range(0, len(picks) - 1, 2)][:args.matchups]
    arm_path, note = load_arm(args.mode, args.head, [float(x) for x in args.bias.split(",")],
                              args.edits, args.seed if args.arm_seed is None else args.arm_seed, args.checkpoint)
    started = time.time()
    if args.harness == "pvm":
        results, meta = play_pvm(arm_path, data_path, args.samples, args.seed, args.opponent,
                                 args.action_selection)
    else:
        results, meta = play(arm_path, matchups, args.beam_width, args.max_actions, args.plan_k,
                             args.seed, args.policy, args.origin, args.abstain,
                             args.ap0_walks == "drop", args.abstain_class, data_path)
    if hashlib.sha256(data_path.read_bytes()).hexdigest() != data_sha256:
        raise ValueError("evaluation table changed during run")
    document = {"summary": (summary_pvm if args.harness == "pvm" else summary)(
                    results, meta, note),
                "elapsed_s": round(time.time() - started, 1),
                "config": {"matchups": len(results), "seed": args.seed,
                           "arm_seed": args.seed if args.arm_seed is None else args.arm_seed,
                           "matchup_seed": args.matchup_seed, "mode": args.mode,
                           "harness": args.harness, "opponent": args.opponent,
                           "samples": args.samples,
                           "head": args.head, "policy": args.policy, "abstain": args.abstain,
                           "checkpoint": str(arm_path),
                           "checkpoint_sha256": hashlib.sha256(pathlib.Path(arm_path).read_bytes()).hexdigest(),
                           "data": str(data_path), "data_sha256": data_sha256,
                           "beam_width": args.beam_width, "max_actions": args.max_actions,
                           "plan_k": args.plan_k,
                           "origin": args.origin, "ap0_walks": args.ap0_walks,
                           "abstain_class": args.abstain_class},
                "results": results}
    out = args.out or str(pathlib.Path(os.environ.get("TEMP", ".")) / ("wsp_%s.json" % args.mode))
    pathlib.Path(out).write_text(json.dumps(document), encoding="utf-8")
    print(json.dumps(document["summary"], indent=1))
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
