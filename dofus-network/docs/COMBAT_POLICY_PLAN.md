# COMBAT_POLICY_PLAN — what to do with the turn planner, and what is already ruled out

**Written:** 2026-11-11, after §XIII of `ARCH_REASSESSMENT.md`.
**Scope:** the per-turn decision only (which plan to play given the solver's candidates). Not the
engagement decision, not the build, not the quest loop.
**Status of the numbers here:** all measured on level-200 builds over the duel harness
(`combat/duel_value.py`), paired and side-swapped. Anything not measured is labelled a guess.

---

## 1. What is established

Five facts, each with the measurement behind it. They bound what any further work can buy.

| # | fact | measurement |
|---|---|---|
| 1 | Re-ranking the solver's six candidates can gain at most **0.021 of an HP bar** per decision | `scratch/rbt_300.json`, 300 boards: solver top-1 regret 0.0209 against the best of its own six; blind choice 0.0653 |
| 2 | The solver's weighted score and the successor's **HP differential pick the same top plan** | identical regret (0.0209) and top-1 (0.847) on 300 boards; only the ordering of the rest differs |
| 3 | Ranking by that HP differential **beats the solver** | 600 paired duels, 104 boards better to 17 worse, `+0.0113`, CI `[+0.0072, +0.0154]`, `t = 5.4` |
| 4 | A learned correction on top of it **loses**, badly | 200 paired duels: `-0.1463` with trajectory-wide training, `-0.2646` with opening-only |
| 5 | The within-board signal is real and is **not** what limits the model | continuation noise 0.0107 against a within-board spread of 0.0941 (9×), so the target is learnable |

Fact 1 is the one that should govern effort: the decision the planner is choosing among is nearly
irrelevant to the fight. Fact 4 says the network is not merely weak there — it is actively harmful.

**Ruled out, with the number that rules it out.** Do not re-open these without a new measurement.

| candidate | why not |
|---|---|
| more capacity / a deeper trunk | learned LayerScale gains end at `0.0094, -0.0213, 0.0013`; two different objectives both plateau at Spearman 0.635 |
| a wider candidate set | 28 plans (beam 32) raises available regret only 0.021 → 0.030, and blind choice is 0.0626 |
| a better optimiser | the loss is limited by the continuation it is trained against, not by optimisation |
| distilling the solver more faithfully | bounded by the teacher by construction (Gumbel MuZero, Danihelka et al. 2022) |
| regressing the absolute return | 93.5% of the target variance is the board offset, which cancels when ranking |

---

## 2. The one lever with a mechanism behind it: make the value on-policy

Fact 4 has a diagnosed cause, not a mysterious one. The value is trained on continuations where
**both sides play the solver**, and the policy then deviates on 84% of boards. So it evaluates a
future that does not occur, and the more confidently it deviates the more wrong it is — which is
exactly what the margin gate showed: margins of 1.0 and 2.0 left `boards_differed` at 164 and 159 of
200 and made the mean slightly worse, so the *high-confidence* deviations were the harmful ones.

The fix is policy iteration, and it is small:

**P1.1 — playout policy becomes the policy.** In `duel_value.rollout_truth`, the continuation is
`{1: solver_policy, 2: solver_policy}`. Add a `continuation` argument and pass
`make_policy_for_params(params, trust=0.0)` from the current checkpoint. Everything else — the
counterfactual sampling, the conditional logit, the corpus format — is unchanged.

**P1.2 — retrain on the new corpus and re-measure regret** with `scratch/rank_by_truth.py`, which
must also switch its truth to the same continuation policy, otherwise the measurement and the target
disagree again.

**P1.3 — duel it against the residual**, 400 boards minimum, paired. This is the gate.

Acceptance: the value beats the **residual**, not the solver. The residual is now the incumbent and
the solver is no longer the bar. Concretely, the plan succeeds if the paired mean margin over the
residual has a CI excluding zero and is positive.

**P1.4 — iterate at most three rounds.** Stop when the duel margin stops improving; each round is
~20 minutes of generation plus training. If round 2 is not better than round 1, stop and take the
result to §3.

**Cost:** one round is ~2,400 duels × 3 snapshots × 6 plans ≈ 43k rollouts, ~9 minutes over 8
workers, plus ~3 minutes of training. Cheap enough to run three times without a decision.

**What would make this fail:** the residual's advantage comes from the *greedy* signal, and the
policy's own continuation is a noisier version of it, so the on-policy target may simply be harder to
fit. If two rounds do not beat the residual, the conclusion is that this action space is exhausted
and §3 is the only remaining option.

---

## 3. If §2 fails: change the decision, not the ranker

Fact 1 is a statement about *one-turn* plans. If the planner cannot gain more than 1% of a bar by
choosing better among one-turn plans, the next question is whether the plan granularity is wrong.

**P3.1 — measure the two-turn ceiling first, before building anything.** The solver generates
one-turn plans. Ask what a two-turn plan could buy: take the top-k plans, and for each, apply it and
then let the *opponent* choose its best reply from its own candidates, and evaluate the resulting
state with the existing value or with the HP differential. That is a minimax backup over the real
transition function and it needs no new model. If the two-ply value of the solver's top plan is
already within 0.02 of the best two-ply value, the answer is no and the planner is finished.

**P3.2 — only then** consider generating multi-turn candidates, which means teaching the solver to
emit plans spanning two turns. That is a substantially larger change and should not start before
P3.1 returns a number.

---

## 4. What this plan deliberately does not do

**It does not chase the last 1% while the project's real bottleneck is elsewhere.** `resume.md` §0
records that no quest has been finished end-to-end unattended, that six fights were won in one
session with no boss and no sustained sample, and that 100 quest advances cost ≈9,692 actions ≈ four
days of sessions. Combat decisions are now worth 0.011 of an HP bar per turn. The next hour is worth
more in the quest loop than in this one.

**It does not ship the network.** `_hp_choice` ships; `dv_rank_v3.pkl` does not. A model that loses
0.146 of a bar per duel does not go near the live path because its offline regret improved.

---

## 5. Measurement rules this session earned

These are cheap to follow and each one cost a wrong answer to learn.

1. **Pair the two games on the same board.** Same board seed, same in-game seed. `make_duel` draws
   engagement cells from the rng; advancing it between the two calls silently compares two boards.
2. **The paired statistic is the sum, not the difference.** Both games report `a`'s own margin; their
   difference is seat bias and is zero by construction.
3. **Report `boards_differed`.** A p-value over 400 boards of which 12 differed is a lie about how
   much evidence exists. Most boards are ties and ties are not evidence.
4. **Ground truth is a rollout, not agreement with the teacher.** Regret against what the plan
   actually caused, on the same decision point, is the only number that ranks policies.
5. **Keep one test that proves the instrument can see.** `BenchmarkTests.test_the_benchmark_sees_a_policy_that_only_passes`
   fails on the broken estimator, which is why it exists.
6. **Never compare across estimators.** `dv_rank_v2` at -0.2659 (unpaired) and -0.2646 (paired) are
   the same policy; quoting one against a paired number from another run is how a 2.7× error on the
   residual (+0.0229 unpaired against +0.0113 paired) got into a draft of this work.

---

## 6. One hazard found while closing this out, measured rather than argued

`_value_choice` has **two** call sites, and the three documents that discuss it were each talking
about a different one:

| call site | argument | the gate that stops it |
|---|---|---|
| `live_policy.py:708`, inside `_cast_cell` | `matching` | the list is empty — §L.22's four arms, structural, not a flag |
| `live_policy.py:753`, inside `_solver_turn` | `plans` | `_value_ready` |

That is why §L.22 could conclude "it is not the readiness gate" while a reader of `_solver_turn`
sees nothing *but* the readiness gate. Both are true; they are different branches. A 2026-11-11 pass
over `resume.md` briefly collapsed the two and asserted the gate was the whole story — wrong, and now
recorded at the line it belongs to.

**The `_solver_turn` gate is worse than a flag, it is a stale flag.** `_value_ready` is assigned in
exactly one place, `live_policy.py:967`, on the **actor** path — and `_solver_turn` returns before
that line is ever reached. So in solver-first mode (the default) the flag is written only on a turn
the solver declined, and the next solver turn reads whatever that turn left behind.

**Measured, not inferred** — `scratch/chain_reachability.py`, the real `decide()` over the suite's
own board fixture:

| board | candidates | after pass filter | `value_choice_stats` | `hp_choice_stats` |
|---|---|---|---|---|
| enemy adjacent | 7 | 6 | `asked 0` | `asked 1, changed 0` |
| enemy distant | 2 | 1 | `asked 0` | `asked 0` |

Two things follow. **`_hp_choice` is reachable and live** — `asked 1` is the shipped ranker doing its
job — so the +0.0113 is not sitting behind a dead branch. And the `distant` row is `asked 0` for the
honest reason (one non-pass plan, nothing to rank), which is exactly the reading the pass-filter
column exists to prevent.

So the ordering `_value_choice` → `_grid_choice` → `_hp_choice` is **not** currently backwards in
practice: the value head cannot fire on the solver path at all, because its flag is never fresh
there. The latent hazard is narrower and sharper than "the weaker ranker runs first": it is that
**the day someone moves the `_value_ready` assignment above the solver-first branch, the 0.4062 head
starts pre-empting the +0.0113 one.** That is a one-line change away from being live, which is why it
is written down. Do not make that move without the duel measurement described next.

It cannot be priced with the current harness, because the duel harness drives `make_policy`, not
`LivePolicy`. Pricing it needs the live policy object wired in as one of the two policies, with the
full chain required to beat `_hp_choice` alone.


