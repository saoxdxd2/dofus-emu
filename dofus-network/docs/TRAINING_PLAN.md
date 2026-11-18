# TRAINING_PLAN — one shared learned brain (≤30M total / ≤10M active), the harness as its gym

## Stage II — approved implementation plan (2026-11-17)

**Status:** approved; Module 1 in progress. This section supersedes the historical
next-action list below. Execute Modules 1 -> 5 sequentially; do not launch downstream
training or promote a component before its prerequisites and outcome gates pass.

**Objective:** a hierarchical, uncertainty-aware learned planner that proposes legal
strategies, evaluates their consequences, executes one action, waits for evidence and
replans. Completion means better complete fights and quest episodes, not higher teacher
agreement. No commits, pushes, remote training or live-session launches are implied.

### Architecture and ownership

```text
client observations + confirmed events
  -> versioned facts + bounded task-relevant hypotheses
  -> verified goals and prerequisite graph
  -> learned proposals + legal candidate generation
  -> bounded consequence search + outcome-trained value/cost estimates
  -> validate_call + existing execution/safety guards
  -> one action + confirmation/revision
  -> evidence memory + experience -> next decision

offline: confirmed experience -> stronger search -> policy iteration
         -> held-out paired evaluation -> separately authorized promotion
```

Code owns known mechanics, legality, execution and recovery. Data owns verified quest,
map and spell facts. Models learn proposals, strategic priorities, outcome values and
costs. Search checks combinations. Memory preserves source order, uncertainty and
revisions outside neural weights. Unknown mechanics and missing tools stay explicit;
they are never silently interpreted as zero damage or fabricated executable actions.

Confirmed, observed, predicted, unknown and conflicted facts have distinct meanings.
Retain only bounded, decision-relevant alternative hypotheses; attach probabilities
only when their calibration is supported. The current source-ordered evidence ledger
is a foundation, not an implemented latent-state or exact Bayesian filter.

### Module 1 — transition fidelity and the combat foundation

- Pin the exact live policy chain, artifact identities, corpus and simulator versions.
  Compare the actual chain and `_hp_choice` explicitly; do not assume the HP ranker is
  the only reachable branch.
- Reuse existing whole-turn search and the two-ply evaluator. Their presence is not
  evidence of complete initiative-ordered simulation or a measured gameplay gain.
- Establish a shared post-plan projection for HP, life status, cells and acting-player
  AP/MP. Use the solver's recorded displacement, not a second approximation. Build
  occupancy from surviving fighters at successor positions; never let a dead fighter
  or a vacated cell provide cover.
- Extend supported typed mechanics and structural state cloning: damage/heal/shields,
  AP/MP changes, displacement, cast limits/cooldowns and explicitly known timed states.
  Share immutable geometry and spell records; do not deepcopy the board per candidate.
- Differentially test search, replay and offline transitions against one contract.
  Every mechanic needs a focused witness, an interaction witness and a failing control;
  compare with recorded transitions where live evidence is available.
- Progress from the current damage-reply estimate to initiative-ordered, resource-legal
  responses with actual target selection and resulting state changes. Unsupported
  enemy support, summon and boss-state behavior remains visible as model uncertainty.
- Measure the decision-leverage ceiling before adding horizon. Compare our turn,
  plausible enemy response and our next opportunity; do not spend more search on
  near-equivalent choices without evidence of additional outcome headroom.

**First implementation slice:** shared HP/cell/AP/MP successor projection used by the
reply evaluator and duel replay, with dead-blocker, recorded-displacement, immutable
branch and replay-parity witnesses. This is narrower than a complete mechanics engine;
initiative-aware multi-target replies and state/cooldown evolution remain open.

### Module 2 — expressive proposals and bounded tactical planning

- Add spell-conditioned relational scoring, starting with the rank-16 bilinear head.
  Score spell, target, position and sequence together rather than reusing one target
  ranking for every spell. Prove representability and input leverage before fitting.
- Generate complete legal turns across damage, protection, setup, displacement and
  verified boss-mechanic opportunities. Update the simulated board after each action;
  regenerate legality and collapse equivalent successor states.
- Use constrained within-turn beam search and a bounded inter-turn tree, with learned
  terminal values. Spend additional depth only on decisions with measured leverage.
- Evaluate plausible opponent-response scenarios from verified spellbooks and observed
  behavior. Use catastrophic-risk checks without pretending every monster is omniscient.
- Unblind and verify relevant 8-actor inputs, then measure party reservations separately
  against an identical independent-scoring arm. Existing controlled results favor
  spell-target scoring; they do not establish coordination as the main missing gain.

### Module 3 — contingent quests, useful observations and honest credit

- Compile verified quest/objective identities into prerequisites, alternative routes
  and executable subplans. Reuse the AND/OR planning stack through its decision seam;
  a learning-only adapter is not a decision connection.
- Each subplan carries preconditions, expected effects, completion evidence, duration,
  resource cost, dependencies and invalidation conditions. Preserve it through ordinary
  waiting; replan on relevant changes or contradicted prerequisites.
- Search only known transitions. Unknown dialog or quest consequences require an
  observation or an empirically trained outcome estimate, never an invented transition.
- Gather information only when its possible answers can change the chosen action enough
  to justify the cost. Identical snapshots are not independent information or progress.
- Keep macro objective credit separate from micro execution credit. Use confirmed,
  revision-aware outcome identities; censor unresolved/conflicted examples rather than
  turning missing confirmation into failure. Train dialog/verifier heads on noncircular
  outcomes, retaining calibration and episode-held-out gates.
- Execute one validated action, retain pending ownership, observe and replan. Stop,
  disconnect, identity and repeat guards remain outside learned control.

### Module 4 — stall-only visual guard

Use structured observations first. Invoke the NumPy image guard or optional tiny INT8
ONNX classifier only for a suspected UI/observation mismatch. Gate on detected real
mismatches and false interventions; do not replace structured planning with screen guesses.

### Module 5 — policy iteration, adaptation and targeted WSP

- Learn proposal, terminal value and duration/resource-cost heads from confirmed
  episodes plus provenance-preserving counterfactual transitions in validated domains.
- Continue counterfactual rollouts with the policy being evaluated. Refresh data on
  states the updated policy reaches; do not train a policy-dependent value against a
  continuation that deployment will not follow.
- Treat the requested 50/50 real/counterfactual mix as an experimental arm, with separate
  source metrics and ablations. Real and simulated evidence never become indistinguishable.
- Begin online adaptation with bounded experience estimates and guarded small adapters;
  retrain the shared core offline. Revisions replace old credit rather than duplicating it.
- Apply targeted WSP only after the desired behavior is expressible and useful. Require
  no-op and support/norm-matched random controls, unchanged-leaf/artifact witnesses and
  terminal-outcome gates. Ordinary supervised fitting is training, not WSP.
- Glicko-2 may summarize an opponent pool; it never replaces paired promotion tests.

### Non-negotiable runtime and release gates

- Runtime: stdlib + NumPy + ONNX Runtime only in live agent/combat/world-model/planning/
  networkhealth modules; torch/jax and HTTP remain outside these runtime paths.
- Shared parameters <=30M; active non-text parameters <=10M; full decide p95 <5 ms;
  RAM <=45 MB per brain; installer <=67,108,864 bytes.
- Measure the entire decision path on the shipped interpreter under multi-instance
  load. Model-forward timing alone is not the latency gate. Bound nodes, wall time,
  per-instance history and scratch memory; account separately for background CPU work
  and reject stale cached/background plans.
- Keep heavyweight search offline, batch online candidate scoring and share immutable
  model/geometry data. Return the best validated candidate when the budget expires.
- Combat: >=400 same-seed side-swapped pairs against the actual chain and `_hp_choice`,
  positive paired gain with 95% bootstrap CI excluding zero, INERT spread >0, changed
  decisions, and separately reported wins/losses/draws, legality and runtime. Reprice
  comparators after every shared mechanics change; simulator wins are not live wins.
- Quests: hold out complete chains; measure objectives/quests completed within a fixed
  session budget, stalls, human interventions, ineffective repeated mutations and
  elapsed/resource cost. Neighboring frames are not independent holdouts.
- Attribute gains incrementally: baseline -> transition fixes -> candidate coverage ->
  replies -> learned values -> information gathering -> coordination -> WSP.
- Exclude every new dev artifact in both `tools/payload_audit.py::DEV_TIER` and
  `builder/build.cs::SkipDevPayload`. Sync the toolkit, run sensitivity/anchor checks,
  regressions and shipped-path probes, then rebuild/audit before release. Preserve
  concurrent user changes and do not edit the tree during an active gate or mutation run.

### Current verification and remaining work

**First slice implemented; verification recorded 2026-11-18.** `Board.after_plan` now
projects recorded HP/life/cell/AP/MP outcomes for both the reply evaluator and duel replay.
Dead fighters no longer supply cover; recorded displacement is preserved without rerolling
it. Mutable branch collections are isolated; immutable geometry and spell records are shared.

Observed checks: 126 runtime-only combat tests passed on the shipped interpreter, 61
combat/development tests passed on the JAX-capable development interpreter, and 14 focused
regression/anchor/staging checks passed. These sets overlap; do not sum them as unique tests.
The four new projection witnesses fail on the old implementation. Each of four targeted
successor mutations independently fails a specific behavioral assertion with zero errors.
The narrow shadow sweep also completed with clean preflight/restoration (0 failing checks),
but its mutated runs include unrelated shadow-hook errors: the independent assertion checks,
not a blanket CAUGHT count, establish sensitivity. The sweep's 18 stale suite references were
corrected to their extracted owners; all 134 snippet test references resolve.

**First slice verified (2026-11-18); the architecture remains unfinished.** The final
source-snapshotted regression passed 2,510 checks with 0 failures; start/end source hashes
matched (`e720eecda37a0214619581b56184c6064c19d5a96f6be4d84e16e3bfd568dcde`). This verifies
the projection/cache/verification slice, not the remaining mechanics, planning or learned
components. Prior full runs recorded 2,502 checks/1 stale-documentation
failure, then 2,504 checks/2 failures (20.9 ms timing and a source-based coverage audit).
All 206 module-defined suites were in the execution record. The audit now uses actual
execution counts, with positive, missing-suite and newly-unasked-suite controls. The
snippet counter counts dispatched entries across owners, with decoy/off-by-one controls.

A subsequent source-snapshotted run recorded 2,508 checks/2 failures: development-Python
timing at 27.5 ms and a witness registered after its audit. Start/end code hashes matched
(`77d64cc7138a604b0359eb30c212235124cb1a0c13f905cc68012a9eb0036ccc`). The current runner
places that witness before its audit. Profiling was ruled out; the timing suite had been
launched by development Python 3.14 instead of shipped Python 3.11. A direct same-instrument
comparison measured 26.1 ms versus 19.9 ms. The gate now runs this runtime-only suite via
the shipped interpreter and its direct entrypoint; offline/JAX suites stay on development
Python. The unchanged 20 ms limit and all 12 shipped suite tests passed. A wrong-interpreter
mutation fails its specific assertion without a crash; 26 focused gate checks passed.

A bounded 256-entry cache shares immutable buff-aging results, with mutable expiry deltas
copied per branch. Earlier combined shipped timings were variable (including 21.4 ms);
eight subsequent samples were below 20 ms. A slotted-state toy showed no median advantage
and was not adopted. No small-loop result establishes full-decision p95. The first slice
has cleared regression; full decide p95/RAM, >=400-pair outcomes, remaining Module 1
fidelity/planning, live verification and installer rebuild/audit remain due. The active
toolkit mirror is absent in this
checkout; no archived mirror was recreated.

**Live-policy baseline on recorded boards (2026-11-18).** `tools/policy_over_record_boards.py`
now replays the recorded payload as it is by default and adds the imputation arm only under
`--fill`, each under a distinct label; both arms are reached only through the live
`LivePolicy.decide` chain. Corpus: 333 recorded boards / 160 own turns, 25,734,995 B, sha256
`3250369fb3e3eb2f32fa168835ae49b7fcff1e64452863ac0c352a9042672be8`; checkpoint
`encounter_selfplay.pkl`; shipped interpreter Python 3.11.9 / numpy 2.4.6.

| arm | actionable own turns | casts | moves | passes | no action | p50 / p95 ms |
|---|---|---|---|---|---|---|
| as recorded | 139 / 160 | 13 (8.1%) | 85 | 41 | 21 | 3.0 / 6.6 |
| `--fill` | 139 / 160 | 14 (8.8%) | 85 | 40 | 21 | 3.6 / 8.9 |

The recorded contract already reports 139 of 160 own turns actionable (75 of 333 boards
overall), so supplying the missing `max_hp`/`max_ap`/`max_mp` moves exactly one turn's action:
the imputation hypothesis is not what limits the actor on this corpus. The chain's decisions
are solver 72, solver_move 24, policy_pass 40, observation_not_actionable 21, no_legal_plan 1,
policy_retreat 2. The trained critic reached its realization branch on 14 of the 27 cast
decisions across both arms and picked a plan each time (`asked`/`fired` = 14/14), so the value
head is present in the shipped path, not orphaned. These latency figures are the whole
`decide` call (solver included) on the shipped runtime, not the brain-forward p95 gate, and no
win rate is implied. The run emitted four `damage_calculator` env-parse warnings; noted, not
attributed here.

Re-verification after that change (2026-11-18): the full gate passed 84 of 86 suites (2
advisory inventories), `agent.regression` recorded 2,515 checks / 0 failures, and the source
snapshot over `dofus-network`, `builder`, `desktop` and `tools` was byte-identical before and
after (`4b80b69ab2fdc084b0c038c9c5663ee3bc7924ba5df9316806f474909d2eefd7`). `doc_facts`
rendered one document and its check passed. All 10 mutations that target the replay tool were
caught and the tree restored to 0 failing checks.

**Module 1 outcome gate (2026-11-18): run, and it does not promote the chain.** 400
recorded boards -> 800 same-seed side-swapped games per comparison, round cap 16, shipped
interpreter. Seat 1 wins 94-96% on every arm, so every win/loss verdict is
`seat_dominated`; only the paired efficiency delta is evidence:

| comparison | damage/turn diff | 95% bootstrap CI | boards for A / B | wins A / B |
|---|---|---|---|---|
| live chain vs solver | 0.00 | [0.0, 0.0] | 0 / 0 | 400 / 400 |
| live chain vs `_hp_choice` | 0.00 | [0.0, 0.0] | 0 / 0 | 400 / 400 |
| live chain vs two-ply `_hp_choice` | -1.02 | [-2.131, -0.161] | 0 / 16 | 387 / 413 |
| random vs solver (sensitivity) | -71.42 | [-82.366, -60.975] | 210 / 590 | 210 / 590 |
| solver vs solver (mirror) | 0.00 | [0.0, 0.0] | 400 / 400 | 400 / 400 |
| two-ply vs solver | +1.00 | [0.149, 2.119] | 15 / 0 | 412 / 388 |
| live-wide (beam 16/5) vs two-ply | -0.99 | [-2.095, -0.136] | 1 / 15 | 389 / 411 |

What this establishes. The instrument can rank decisions: a random policy loses 71.42
damage/turn with a CI nowhere near zero, and the mirror is exactly 0. The shipped chain is
**bit-identical in outcome to the solver** on this corpus: the value critic re-picks among
equal-damage plans (302 of 2,817 plans changed, 0.00 damage difference), and `_hp_choice`
was **asked 0 times in 2,817 decisions** because the critic returns a plan 2,799 of 2,799
times -- the `+1.00` two-ply gain is real on its own arm but unreachable behind the critic
in the shipped chain, and widening the beam to 16/5 does not recover it (-0.99). So the
outcome gate stays unmet: no positive paired gain, no promotion, no live-win claim.
Artifacts: `module1_side_swap_summary.json`, `module1_controls_summary.json` and the
per-comparison JSONs under `Documents/dofus-evidence`.

**Reply ledger: initiative order, target choice, state changes (2026-11-18).**
`evaluate_two_ply` no longer sums independent worst-case hits on the player. Replies are
applied in initiative order to a per-ally HP/shield ledger; each enemy picks the target
that costs the most HP share (not necessarily the player); an ally killed by an earlier
reply is dropped as a target and its cell stops blocking line of sight, so the value is a
state differential rather than a damage tally. New witnesses: the ally-target one returns
the no-reply 0.25 under the old player-only model instead of 0.2333, and the same sniper
board is worth 0.01591 or 0.03409 depending on initiative order (the old model returns the
same value in both orders). Three targeted mutations -- dead ally blocks the line, dead
ally stays a target, every reply prices the player -- each fail exactly their witness and
the file restored byte-identical (`213c612b...b0b43d`). All seven paired comparisons above
were repriced after the change and reproduce the pre-change values to the displayed
precision, which is the expected invariance: the duel harness is 1v1, and with one ally the
ledger is algebraically the old sum. Artifacts: `module1_*_pre_reply_ledger.json` hold the
pre-change numbers. Still approximate and documented in the method: movement folded into
the origin ring, zone spells price one target, summon/support/timed-state replies are not
modelled.

Verification for the reply-ledger slice (2026-11-18): the two-ply suite ran 14/14 on the
shipped interpreter three times in a row; standalone `agent.regression` recorded 2,515
checks / 0 failed (`data/last_gate_run.json`); the full gate passed 84 of 86 suites (the
two advisory upgrade inventories) in 800 s. One earlier full-gate run failed only the
`agent.regression` step with no check named in its tail and passed 608 s standalone and on
the rerun -- recorded as a flake, not silently repaired. The mutated disk state restored
to the same hash, and `doc_facts --render/--check` passes.

**Runtime gates measured (2026-11-18).** `tools/policy_over_record_boards.py` run three
times concurrently on the shipped interpreter (3.11.9) over the 160 own turns in the 333
recorded boards. With the solver unbound the same method measured decide p50 4.4-5.1 ms and
**p95 10.6-11.4 ms against the 5 ms ceiling -- unmet**, with a deterministic 13 casts / 85
walks / 41 passes mix; the earlier 8.1-8.7 ms figure was the same method on a less loaded
box, so only same-session comparisons are used below.

`BattleSolver.plan_turn`/`plan_candidates` now take a wall-clock deadline
(`DOFUS_COMBAT_PLAN_BUDGET_MS`, **default 0 = off**), read per expansion, per walk-ring cell
and per candidate through one `expired()` predicate. The first expansion and the turn's
first candidate are never suppressed, so an expired budget returns the best validated
candidate instead of a silent pass -- a strict version that could expire before any
expansion turned 9-10 of the 160 loaded turns from casts into passes. Witnesses:
`combat/test_two_ply_world_model.py::SearchDeadlineTests` (expired budget still returns the
first candidate; mid-search expiry returns the best plan so far; a generous deadline equals
the unbounded search) and `combat/test_live_policy.py::PlanBudgetTests` (default off, a
positive value is a future deadline, 0/off/nonsense disable, `_solver_turn` hands the budget
to the search). The mutations "bounded-search: the wall-clock deadline never fires" and
"bounded-search: the shipped default silently turns the budget on" each fail exactly their
witnesses on the shipped interpreter and restore byte-identical (sha256 checked).

The price of turning it on, same corpus (repriced after the spell cache, below): a 1.5 ms
budget gives decide p50 2.9-3.0 / p95 5.7-6.2 ms under the same three-instance load, but
**36 casts / 62 walks -- 23 of the 160 decisions change**, because most turns' full search
runs longer than the budget and the shallow plan wins the re-ranking; single-instance the
same budget measures p50 2.8 / p95 5.5. Longer budgets stop buying anything and stop being
reproducible: 3 ms yields 17-21 casts depending on load, 4 ms 14 and 10 ms ~13 (cold-start
calls still exceed it). No setting reaches 5 ms under load, and no outcome data ranks the
two mixes, so the
default stays off until a bound can be paid for with evidence. The remaining p95 floor is
the fixed per-decide work outside the solver: a single-instance profile puts board
construction at ~1.4 ms, slot binding ~0.6 ms, plan-value rows ~0.5 ms and the actor call
~0.6 ms per decide, against the solver's own unbound p50 ~2.8 ms -- bounding the search
cannot move that floor, so the next lever is the fixed overhead. `tools/brain_ram_probe.py`
reported final resident 44.2 MB, peak 166.9 MB, commit 292.4 MB over 800 streamed decides
(4.935 ms each; interpreter 3.11.9) -- the 45 MB brain ceiling is met at 44.2 MB with
0.8 MB of margin. Neither number is a release claim.

**Decision-leverage ceiling (2026-11-18): proposals, not horizon, are the limit.** Two
substrates. On the recorded corpus the question has no signal: 78 of the 160 own turns
offer two or more action plans and every one of them is move-only (expected damage 0,
projected HP unchanged), so the one-ply and two-ply values are exactly 0 there and the
probe's "100% near-equivalent" means the instrument cannot separate the plans, not that
they are equal. On the duel harness (`make_normal_duel`, 40 seeded games, the same
`Board`/`plan_candidates`/`evaluate_two_ply` API the live chain uses) 244 decisions offer
two or more action plans and 240 deal damage. The one-ply HP share and `evaluate_two_ply`
pick the same top plan in 244/244, the top two are within 1e-3 in 244/244, and only
**1/244 (0.4%)** of the candidate sets contain two *distinct* projected HP outcomes --
183 of the 244 differ only in the final cell. Walking our plan, then the opponent's
solver-best reply, then our best one-ply damage on the next turn changes the preferred
plan in 5/244 (2.0%) and changes the next-turn damage at all in 8/244 (3.3%); every one
of those sits among plans whose current turn is outcome-identical. So on this harness an
extra ply buys at most ~2% of decisions, and the ceiling is set by proposal collapse (one
distinct outcome per decision), which is Module 2's first item -- the same collapse the
outcome gate already measured (the critic re-picking among equal-damage plans,
`_hp_choice` asked 0 times in 2,817 decisions). Limits: the duel is 1v1 between two fixed
builds and the corpus is move-dominated, so neither is a live-fight distribution, and no
horizon increase is justified by this evidence. Artifacts: `module1_leverage_duel.py`,
`module1_leverage_ceiling.py` and their diagnostics under `Documents/dofus-evidence`.

**Target leverage on the recorded corpus (2026-11-18): a kill is the only target
signal.** Across the 333 recorded boards (160 own turns, 102 multi-enemy), every
multi-enemy board is a single-species pack with one resist line and level, and 922 of
1,396 enemy rows publish no `max_hp`. Replaying the encoder and the target binding over
those boards, the expected-damage spread across targets is exactly 0 (identical
species); only kill availability separates targets, in 11 of 83 spell decisions on 7
boards. The solver's top plan takes that kill on 4 of 7; the target head picks a killer
on 2 of 11 and is spell-invariant (the same target for every damage spell on 28/28
boards). An HP-swap ablation (each enemy's `hp` exchanged with another enemy's, `max_hp`
held) leaves 64/83 picks identical, 12 follow HP, and flips kill status on 11 of 11 --
the probe can see the signal it is looking for. Artifacts:
`module2_target_leverage.py`, `module2_kill_pricing_ab.py` under `Documents/dofus-evidence`.

**The missing max_hp collapsed the kill price; it is now learned (2026-11-18).**
`KILL_BONUS` prices a kill at 0.35 x max_hp, and a board without `max_hp` caps it at 1,
so on a level-5 pack a kill is worth 0.35 against a 49-70 hit on the leader: the top plan
double-casts the 200-HP monster while an 8-HP one stands killable. Holding the solver
fixed and varying only the board, an oracle arm that fills every published `max_hp`
switches 4 of 139 own-turn boards to a finishing blow and loses none; the documented
`max_hp := hp` imputation *loses* 4 kills. The fix is live-honest rather than oracle:
`ThreatMemory.record_hp` keeps the largest `max_hp` ever published per name, the combat
server's observer stores each payload's value in `threat_model.json`, and `BattleSolver`
snapshots the table per decision and prices only the rows the payload leaves blank --
with no table the pricing is exactly what it was. Replay effect: the raw arm now takes
the kills the oracle arm took (0 of 139 boards differ between them), while the emitted
action mix and latency are unchanged (13 casts / 85 moves / 41 passes / 21 none, p50 3.3
/ p95 9.4 ms, shipped interpreter). Witnesses: `KillPricingTests` and the observer test;
all four arms -- "a learned max_hp never prices the kill", "the board never teaches the
table", "a learned max_hp is never stored" and "the observer drops the max_hp hop" --
are caught in place with one failing check each and the tree restored 0/0. Caveat: the
table keys by name only and keeps the maximum, so a higher-level instance of the same
species can overprice a low-level kill; it cannot underprice one.

**Sweep instrument defect found while proving the above (2026-11-18).** The mutation
tool's zero-mutation shadow mode is unusable on this tree: an *unedited* shadow copy of
`battle_solver.py` fails 9 unrelated checks (a `TypeError` in `test_worker_headroom_layer`,
a `NameError` in `test_board_payload_round_trip`), so every arm in that mode reads CAUGHT
no matter what it changed. The same arms in `--in-place` mode fail exactly the intended
check. The tool now runs that no-edit control per target and refuses shadow mode when it
fails, naming `--in-place` instead of printing verdicts.

**Proposal coverage on the recorded corpus (2026-11-18).** `module2_proposal_coverage.py`
replays all 333 recorded boards and, for each of the 160 own turns, separates *available*
(a castable spell carries the category), *proposed* (some top-6 candidate fires it) and
*chosen* (the top plan fires it). Before the change: 99 boards had candidate plans, 40
proposed damage, 9 displacement/push, 72 walk -- and **zero setup plans**, although a
castable glyph was available on 126 boards, a trap on 25 and a summon on 34. The cause was
mechanical, not a tuning choice: `_templates` had a trap branch and a summon branch and no
glyph branch, and `LiveSpell.is_glyph` was defined and used nowhere, so a glyph fell through
to the per-target loop and was dropped by both legs (the enemy leg wants damage or denial,
the ally leg wants a heal). Glyphs now place like traps and are priced by the same traffic
table. After: glyph proposed on 24 boards and chosen on 13, setup bucket populated 0 -> 13,
admitted-but-unproposed spells 87 -> 68, and no plan's actions violate AP order or
dead-target legality on replay (331 plans, 324 distinct successors, 7 equivalent-collapse
duplicates). The live chain changes by exactly one emitted action on the 160 own turns
(13 -> 14 casts, 85 -> 84 moves, 41 passes, 21 none); the latency comparison was taken while
another session was editing the tree and is not quoted as a cost. Unmodelled effects remain
the protection gap: Barricade (effect ids 265/406/792/950), Reinforced Protection, Feca
Shield and Feca Exaltation all parse as kind "other", so protection proposals stay at zero
until shield effect ids are derived and verified from the client table rather than typed.
Artifacts: `module2_proposal_coverage.py/.json`, `module2_glyph_replay_on.log` and
`module2_glyph_replay_off.log` under `Documents/dofus-evidence`. Witnesses:
`combat/test_battle_solver.py::GlyphProposalTests` (a glyph plan exists and is classified
setup; the same spell with an unmodelled effect id produces no plan); the placement branch
removed makes the first test fail with one assertion and the tree restored.

**Fixed per-decide overhead: the spellbook parse is cached (2026-11-18).** A cProfile
of `decide` over the 160 own turns (shipped interpreter) put 4,317 `_spell` calls at 21%
of the profiled time; the corpus has only 45 distinct own spellbooks and 11 enemy ones
across 16 fights, so almost all of that parse is repeated work. `Board._spell` now keys a
512-entry cache on a 16-byte blake2b digest of the raw spell payload's repr and returns
the frozen `LiveSpell`; the per-turn fields (`casts_this_turn`, `castable`,
`castable_error`) are part of the key, so a spent cast cannot ride a hit. Same-process
A/B over the same 160 payloads (cache vs. the uncached parse, arms alternating): boards
identical (spell ids, rolls, turn fields, fighter HP/cell), action mix identical (85
moves / 41 passes / 21 none / 13 casts per pass), board build 0.70-0.86 vs 0.86-1.17
ms/board, whole decide p50 3.2 vs 3.7 ms and p95 7.0 vs 7.4 ms. That is an unloaded
single-process comparison, not the 3-instance gate, and it does not meet 5 ms. Witnesses:
`combat/test_battle_solver.py::SpellParseCacheTests` (identical content reuses the object;
a changed `casts_this_turn` and a changed damage roll each parse a new spell). Mutation
"spell cache: any payload hits the first parsed spell" is caught and the tree restored
(sha256 `def8fcb1c5916e5de68ed7dcf5ffa12a581240edba582ee94d059273d2cf2b6a` before and
after). The 512-entry cap keeps the cache around 1 MB; re-running
`tools/brain_ram_probe.py` with it reports 44.2 MB resident (up 0.6 MB), 166.9 MB peak and
292.4 MB commit over 800 decides, still under the 45 MB ceiling with 0.8 MB of margin.

Repriced after the cache, same session and the same three concurrent instances: budget 0
gives p50 3.2-3.7 / p95 8.2-9.4 ms with the deterministic 13 casts / 85 walks; budget 1.5
gives p50 2.9-3.0 / p95 5.7-6.2 with 36 casts / 62 walks, identical in all three instances;
budget 3 gives p50 3.7-3.9 / p95 6.6-7.3 with **17, 21 and 18 casts** -- a wall-clock bound
makes the decision mix depend on machine load once it straddles the search duration, which
the deterministic 1.5 arm did not show. No setting reaches 5 ms p95 under load; the default
stays off.

Verification for the bounded-search slice (2026-11-18): the full gate passed 84 of 86
suites -- the two advisory upgrade inventories are the rest -- in 937.7 s with exit
status 0, `combat/test_live_policy.py` and `combat/test_two_ply_world_model.py` green
within it. The tree was unmodified for the run; the leverage probes ran only after it
finished.

Verification after the kill-pricing and glyph slices (2026-11-18): all combat suites pass
through the gate (30 of 32, the two advisory inventories the rest, 141.7 s). A full gate
was attempted but `agent.regression` reported 12 failures from a *concurrent*, in-flight
edit to `agent/autopilot.py`, `agent/planner_utils.py` and the new `agent/scheduler.py`
(an unterminated read mid-write, missing mutation anchors, brain-start checks), none of
which touch combat, the solver or the docs; the kill-pricing and doc-number checks that
this session did touch ran green in the same pass (2,507 checks). The earlier failure that
was mine -- the mutation snippet naming `combat.test_kill_pricing` while `run()` never
executed it, which the witness ratchet correctly reported as `not_run` -- is fixed by
calling the test in `agent/regression.py run()`. Rerun the full gate once the concurrent
edit settles; do not edit the tree during its runs.

No component is promoted and no in-game intelligence gain is claimed. Module 1 remains in
progress (full decide p95 against the fixed per-decide overhead, the remaining fidelity
items above, live verification and the installer rebuild/audit); its decision-leverage
item is now measured, and the measurement points at proposal expressiveness rather than
more search. Module 2 now has its target-leverage and kill-pricing measurements, the
rank-16 head's representability and leverage results, and a measured proposal-coverage
audit with the glyph gap closed; its remaining items are the protection vocabulary
(shield effect ids unverified), the bounded inter-turn tree (leverage measured at 3.1% of
pair-decided states), the 8-actor unblinding and the party-reservation arm. Modules 3-5,
complete quest gains, live verification and final release gates remain pending.

**User direction (2026-10-27).** The pipeline is tools and harness; the intelligence must be
learned. Max **30M total shared params / 10M active** (the earlier 70M and 400MB ceilings were
withdrawn as unnecessary); it is **shared across instances**,
with **MoE / per-instance agents** handling each client while the shared model does the thinking.
Kaggle **GPU is free** for training. Train on the harness **first**, then remove the hardcoded
decisions **component by component** — never before a measured win. End state: unattended Full
Auto that can do the whole game — quests, mobs, dungeons — and keeps adapting.

## Refined direction v2 (2026-10-27) — components 1–4, no fifth expert

The user chose components **1–4** and rejected the 100–135M dialog expert. The budget is no longer
the point: **TRM-style recursive, weight-shared computation** is the hypothesis to test (“bigger is
not always better”), the language model only needs **command/entity understanding** (not fluency),
and the loop must **learn online from the game** (NPCs, quests, dialogs, fights) instead of only
imitating a teacher.

**The clue.** The game is a self-labeling environment: `QuestObjectiveValidatedMessage`,
`QuestStepValidatedMessage`, quest completion, XP/loot, dialog close/advance and fight results are
outcome labels produced by the server. The 49K tool policy already matches the 85.7% teacher, so the
missing intelligence is not width — it is **outcome-driven adaptation** over a small, fast reasoner.
TRM is the architectural hint: share weights across a variable number of recursive steps and spend
compute only when the decision is hard, instead of paying for a dense 100M forward on every step.

**Deployed shape (target):**
- **Reflex core** ≤5M, recursive (TRM-style, weight-shared depth, halting head): tool + structured
  args + verifier. Adaptive steps: 1–2 for routine steps, 8–16 for dialogs/bosses/uncertainty;
  latency bounded per decision.
- **Command/entity encoder** 3–10M: intent + slots over French/English commands and NPC/quest text
  (quest id, NPC, item, map, verb). Not a fluent LM; the output is a structured parse.
- **Combat actor** ~0.2M + solver: unchanged; mechanics and live fights are the gate.
- **Per-instance adapters** 0.5–2M each, chosen by a tiny router: the shared core thinks; adapters
  specialize for the instance/class/quest chain. Only adapters update online; the shared core is
  retrained offline.
- **No 100–135M expert.** Total shared ≤30M, active ≤10M, INT8; the 400MB budget is not needed for
  v2 and is reserved for replay/embeddings only if a benchmark justifies it.

**Online learning (the part the earlier plan lacked).** Shadow mode → replay buffer → guarded
updates:
1. The model proposes, the harness/rules act; every `(state, proposal, rule, outcome)` is logged.
2. Outcomes come from the game itself (validation/step/quest, XP/loot, dialog end, fight result);
   reads never count as success.
3. Per-instance adapters update from a replay buffer with a small learning rate and a rollback
   guard; the shared core is distilled offline on Kaggle from the accumulated buffer.
4. Active learning: low confidence or rule disagreement triggers a read/measurement, not a guess.
5. Safety/legality/recovery stay harness checks; online updates can only rank legal actions.

## Decision: base model

The 24.9M `encoder30_v15` is the **teacher/labeler and fallback**, not the deployed shape. Distill it
(and the 49K tool policy) into the recursive core + command encoder; benchmark TRM-style recursion
against a small transformer/MLP on the same held-out outcomes and keep the best per component. No
generic >30M pretrained model; the domain checkpoint has the better prior. Deploy INT8 and measure
p50/p95 with 12 instances sharing the core.

## Who decides what (target end state)

| Decision | Today | Target | Remove the rule when |
|---|---|---|---|
| tool choice | 49K policy (opt-in) | shared trunk head | beats teacher on held-out **outcome** labels + shadow session |
| tool arguments | rules (`args_for_tool`) | structured args head | legal under the schema, no worse than rules, shadow-clean |
| dialog reply | token-overlap heuristic | shared trunk head | beats the ranker on real reply→outcome labels |
| verifier verdict | rules (+ gated head) | outcome-trained head | beats rule baseline on held-out real labels, ECE sane |
| fight policy | PPO actor / page fallback | PPO actor + shared high-level plan | compute-matched, side-swapped, live fight win |
| safety, legality, recovery, knowledge | tools/harness | **stays tools** | never — the model proposes, the harness gates |

## Data factory

1. **Counterfactual tool/args corpus** (`sim/tool_choice.py`): fork the world per candidate tool and
   label by objective gain — an outcome label, not "what the teacher did". Episode-held-out splits.
2. **Sim trajectories** (`sim/run.py`, `sim/rollout.py`): quests, maps, groups, noise/stale reads,
   travel cost, dungeons; broad curricula for every quest/mob type.
3. **Live logs**: `trajectories/*.jsonl`, `tools/dialog_labels.py`, `fight_outcomes.jsonl`,
   `observed_quests.jsonl`, step rows; the only source of real outcomes.
4. **Client data at runtime** (licence-clean): active quest graph, monster/spell/dungeon tables the
   client loads; no DofusDB re-sync (its licence forbids AI use).
5. **Combat self-play** (existing JAX env): GPU, 12/6/6 normal builds, no Temporix.

## Phases and gates

0. **Baseline** — measure the exact checkpoints (49K policy, teacher, 24.9M encoder) on held-out
   episodes and latency; record numbers, no claims from memory.
1. **Data** — generate ≥100k counterfactual rows + an episode-held-out split; verify label variance
   across all 21 tools; freeze the split before training.
2. **Shared trunk SFT** — fusing `_observation_text` with numeric objective features; heads for tool
   + structured args. Gate: held-out **outcome** accuracy above the teacher, params ≤30M total / ≤10M active, p95 within
   the 4 s page budget.
3. **Dialog** — train reply selection on real reply→outcome labels; gate above the ranker, then a
   shadow session.
4. **Verifier** — labels from action→next-state deltas; gate above the rule baseline with honest
   ECE; only then can the GUI load it.
5. **Combat** — PPO/PBT/self-play, compute-matched and side-swapped; first live fight win is the gate.
6. **MoE / per-instance agents** — adapters/LoRA per instance, shared trunk; measure interference
   and latency with 12 instances before enabling.
7. **Latent context bus (after the core works)** — encode the structured observation + command once
   into a latent/KV context shared by the reflex, dialog and verifier heads, and keep a per-instance
   KV cache across steps. Cross-instance sharing (a shared quest/dungeon/world latent bus) comes
   after a single-instance benchmark. It must be bounded, authenticated, decoded to a short audit
   gist and validated; it never bypasses legality/safety/recovery. Motivation: dense latent
   communication across heterogeneous agents (Chen et al., arXiv 2606.13594; 4B–14B models, 2–3×
   lower compute than text) and its attack surface (Huzaifa et al., arXiv 2609.39788: latent
   channels bypass text filters; attack compliance 27.9%→76.9%).
8. **Shadow mode & Mechanistic Alignment** — the model proposes, rules gate; log disagreements,
   outcomes and latency; apply Goodfire-style internal probes to detect reward hacking (cheating on progress
   metrics without moving game state) before actions reach the bridge.
8a. **Sparse Autoencoder (SAE) Disentanglement** — extract monosemantic feature dictionary on the 266-dim
   hidden representations to decouple entangled classes (specifically `nav.goto` <-> `quest.advance`,
   which causes 35% of lost gain) via zero-cost activation steering at inference time.
8b. **Weight-Space Programming (WSP) Surgery** — for persistent edge-case failures, run offline LLM
   parameter inspection on the exported JSON weight manifests (`agent/weight_space.py`), applying
   micro-edits (<=0.05% of weights) directly to projection tensors; promote only after passing the
   deterministic 3,719-row validation gate.
9. **Continual learning** — live outcome buffer → periodic fine-tune on free Kaggle GPUs → nightly
   held-out eval; knowledge stays in the pipeline so game content changes do not require retraining.
   **Zero-budget runtime:** Local INT8 ONNX execution (<5ms per decision, CPU only, $0 API costs).

## Pre-registered first worker, and the calibration it must beat (2026-10-30)

**The worker.** One shared state encoder (the existing relational `mechanism_vector` block) feeding
per-call-form outcome heads, where a form is the promise the call makes: `nav.goto:step`,
`nav.goto:at`, `quest.talk`, `quest.reply`, `quest.accept`, `tap.interactive`, `inv.equip`,
`fight.join`, `farm.start`, `safe.stop`. It predicts `claim_met` (label_version 3: a refused call is
a miss) and only ever ranks legal actions; the rule still acts when the head is absent or unsure.

**Why a count calibration had to be measured first.** The loop's gate is a counter:
`repeat_streak_norm >= 2` vetoes the third identical attempt. A learned head is only worth building
if it beats the best *count* gate at the same veto budget — the counter is cheap, interpretable and
already shipping in shadow. `python tools/live_outcome_probe.py --calibration --data <fold>` fits
p(met | form, streak bucket) with a Beta prior on train only, chooses the veto threshold on train,
and scores on the frozen fold at the counter's own number of vetoes, so no gate can win by vetoing
more calls. Constants: `PROMOTION_RETENTION = 0.95`, `PROMOTION_MIN_COUNTER_VETOES = 10`,
`PROMOTION_DELTA_POINTS = 0.05` in the probe.

**Measured 2026-10-30 on the rebuilt v3 corpus (frozen folds, segment-clustered paired CIs).**

| fold | rows train/test | counter: vetoed, kept met, retained | calibrated at the counter's budget | delta CI |
|---|---|---|---|---|
| temporal | 3,644 / 1,031 (base 0.577) | 168 (16%), 0.678, 0.983 | kept 0.611, retained 0.886 | (−0.121, 0.012) |
| hash (`transitions_v2`) | 3,568 / 709 (base 0.271) | 280 (40%), 0.434, 0.969 | kept 0.431, retained 0.964 | (−0.038, 0.044) |

Per form on the temporal fold: `nav.goto:step` test is 165/178 met (93%) after 17% in train — the
fold's nav mix shifted, and no train-fitted threshold is usable; its calibration ECE is 0.184
(hash fold 0.031). `nav.goto:at` is 0/12 met in train and 238/336 in test (ECE 0.676): a form that
did not exist when the gate was fitted. `quest.talk` counter vetoes 5 calls of which 2 met while the
calibrated gate vetoes 25 of which 16 met — no gain. `quest.reply` is the one form where the
calibrated gate is better at its own threshold (temporal kept 0.67 vs 0.55, CI 0.093..0.143, but
retains 0.896 < 0.95; hash kept 0.61 vs 0.52, CI 0.062..0.119), yet its counter budget is 9 and 8
vetoes, below the pre-registered comparison floor of 10.

**Result: the count calibration does not beat the counter at equal budget on either fold.** Its
apparent wins at its own threshold come from vetoing 1.5–3.7× more calls. The counter stays the
operating rule; nothing new is promoted, and no worker is trained on this corpus.

**Then the only place left to win: inside the counter's kept calls (`--worker`, measured 2026-10-30).**
The state block (the 24 relational mechanism features, streaks excluded) was fitted per form on
train and scored on the frozen fold. Headroom is AUC on the rows the rule keeps; the extension layer
removes the 5% and 10% lowest-scored kept calls on top of the counter, and is judged by
`_extension_ok`: removed set ≤10% successes, ≥5 removals, kept AUC ≥0.55, and a paired segment CI
excluding 0. Temporal fold: `quest.talk` kept AUC **0.472**, `quest.reply` **0.398**,
`nav.goto:step` 0.645 but its kept set is 175 rows with 165 met — the extensions remove calls that
**100% met** (CI −0.056..0.0 and −0.094..0.0). `quest.other` shows 0.707 kept AUC on 43 kept rows
with 2 positives, below any evidence floor. Hash fold: `quest.talk` looks promotable (5% extension
removes 5 calls, 0 met, kept 0.443→0.465, CI 0.004..0.052; 10% removes 10, 0 met, CI 0.013..0.098),
but the temporal fold inverts it and `nav.goto:at`/`:step` extensions remove half to three-quarters
successes. **No form passes on the frozen fold and the rolling cuts, so no worker ships** — the same
within-period pattern that killed the encoder route (G57). The state features simply do not order
outcomes out of time; more architecture cannot fix a null signal. What would change the answer:
live volume (per form ≥400 met rows across ≥3 start periods) and randomization strong enough to vary
state within a form (the epsilon arm), not capacity.

**The bar the worker must clear before it may replace anything** (on the frozen temporal fold,
then on ≥3 of the 4 rolling-origin cuts, with the rule as fallback):
1. at the counter's veto budget, kept success rate ≥ counter + 5 points, paired segment-clustered
   95% CI excluding 0 — the same arithmetic `--calibration` reports as
   `safe_at_counter_budget.delta_vs_counter`;
2. successes retained ≥ 0.95 (a gate that buys rate by discarding wins is rejected);
3. one head per form, and only forms with ≥400 `claim_met` rows over ≥3 distinct start periods are
   eligible; today's folds do not meet that for any form, so the binding constraint is **live
   data**, not capacity.
4. fail open: absent artifact, unseen form, or a non-finite score means the rule acts.

Re-run `--calibration` after every live session and after every dataset rebuild; the headline is
`safe_at_counter_budget.delta_vs_counter`, not an AUC.

## Kaggle execution

Use an interactive GPU notebook (T4×2/P100); the toolkit is synced by
`tools/sync_kaggle_toolkit.py` and is the dataset source. Checkpoints go to
`models/shared_brain/`, with params/FLOPs/p50/p95 recorded in the run manifest. TPU only if a
measured speedup justifies it. The user runs/pastes notebooks; no queued batch dependency.

**First entity-pointer Kaggle run (2026-10-27, pipeline smoke).** Dataset
`tajdinenizar/thx-05-entity-pointer` (4,017 rows; 3,885 / 45 / 87), kernel
`tajdinenizar/dofus-entity-pointer-v1`, GPU, 8 epochs, hidden 256. Metrics: top-1
**0.3778**, moved AUC **0.4257** (base rate 17.8%), moved ECE 0.259, progress AUC
**null** (zero positives in val). The upload, recursive mount lookup, GPU training and
artifact download all work; the model is not promotable. The corpus is 73% reads,
1,003 action rows, 4 progress labels, and the new tools have no live rows yet. Rebuild
`data/entity_pointer` after a live session on page build `2026-10-27s` before another
training run.

**Observed-text actions-only run (2026-10-28, not promotable).** The same corpus was
enriched with the client's own 22 observed step descriptions (4,016 of 4,017 rows carry
`observed_hint`; dataset `tajdinenizar/thx-08-entity-pointer-observed`, kernel
`tajdinenizar/dofus-entity-pointer-observed-v1`) and trained on action rows only —
**1,003 train / 28 val / 53 test**, 20 epochs, hidden 256. Result: val top-1 **0.0357**,
test top-1 **0.0377**, moved AUC **0.434 / 0.362** (below chance), moved ECE 0.29/0.41,
progress AUC **null** (0 test positives). Adding observed text did not move the
pointer; the limits are the hashed-entity scaffold and 1,003 action rows with 325 moves
and 4 progress labels. Do not rerun this table — the next entity-pointer run requires a
live corpus from `tools/collect_live_data.py` with the new tools exercised.

**Constraint corpus and cause head (2026-10-28, scale-up measured).**
`agent/constraint_corpus.py --generate --out data/constraint_synthetic.jsonl` writes
**2,600** rows (25 tools × 8 causes × 13 template families); the controller routes
**100% cause / 100% action** on it (gate floors 95% / 90%, ≥50 rows per tool).
`ai/train_cause_head.py` trains a hashed-feature MLP on 9 families/cause, calibrates
on 2 and validates on 2 **unseen families per cause**. The MLP alone scores mean **0.81 accuracy / 0.19 ECE** (range 0.7625–0.875) and
fails the ≥0.85 / <0.15 gate. The MLP + cosine-kNN blend (weight chosen on calib)
reported mean **0.855 / 0.089**, but that gate-pass claim is withdrawn: an input-level
audit found 50/400 seed-7 validation vectors identical to fit and calibration vectors.
Reloaded FP32/INT8 scores reproduce; excluding duplicates yields **292/350 = 0.8343**
accuracy. This is a diagnostic on a development set, not independent validation.
The trainer now rejects family/input overlaps or missing causes before training
and export; the existing artifacts remain untouched and unpromoted. The text
cross-product is not an applicability-checked game constraint set. Next: meaningful
precondition cases, source-weighted live failures, a frozen independent split,
then cause/repair training and regret evaluation against always-continue. No new
training run is justified by the old score alone.

**Combat-grid data gate (2026-10-27).** `python -m combat.grid_probe` over the
local corpus reports **156 boards parsed, 64 on the controlled turn, 0 with
positive AP, 0 with more than the pass plan**. The raw boards carry
`my_ap/my_mp = null` (decoded as 0), so the solver cannot produce a cast. Do not
train the 30M 560-cell combat core on this corpus. The required input is one live
fight on page build `2026-10-27s` with a real turn and AP > 0; then rerun the probe,
build the grid tensors, and train compute-matched and side-swapped.

The 30M shape is implemented in `combat/grid_model.py` (**30,766,081** params:
560 cell tokens, 16 fighter tokens, 16 plan tokens, hidden 768, heads 8) and
`combat/test_grid_model.py` passes 3/3. `combat/grid_dataset.py` refuses the
156/156 no-positive-AP corpus rather than writing a pass-only dataset. Training
starts only after `grid_probe` reports `multi_plan > 0`.

**First 30M Kaggle run (2026-10-27, synthetic distillation).** The 156 real maps
were swept over four AP/MP variants, yielding **440 actionable solver-labelled
boards (372/28/40)**; 184 variants stayed pass-only and were dropped. Kernel
`tajdinenizar/dofus-combat-grid-30m-v1`, dataset `tajdinenizar/thx-06-combat-grid`,
GPU, 4 epochs, hidden 768. Result: **val top-1 0.786, test top-1 0.60**, test CE
2.40; `model.pkl` 117 MB saved at `models/combat_grid/`. This is a
solver-distillation model, not a live combat policy: test CE exceeds val CE and
the labels are the solver's plans. It is wired as an opt-in `fight.plan
ranker=grid` (default `solver`, read-only, fails open when the artifact is absent or
the board has no positive AP). The live fight / side-swapped win gate remains.

**Next live step (one command).** With the app, tunnel and clients running on
page build `2026-10-27s`:

```bash
python tools/collect_live_data.py --instances dofus-01,dofus-02 --steps 200
```

It runs `live_ready`, refuses on any FAIL, reuses/starts the single brain daemon,
runs the selected instances with the command shadow, stops them, rebuilds
`data/entity_pointer`, and prints `combat.grid_probe`. Use `--dry-run` to inspect
the request without starting anything.

## Acceptance rules (non-negotiable)

- Held-out **real/outcome** labels, not teacher imitation alone; no 100% scores taken at face value.
- Reads are excluded from success; compute-matched comparisons; equal builds and side-swapped
  combat; 12/6/6 caps.
- A rule is removed only after the model beats it on held-out data **and** survives a live shadow
  session; safety, legality and recovery remain harness checks forever.
- **30M total / 10M active cap** and 12-instance latency measured before enabling a larger trunk.

## Historical next actions (2026-10-27)

1. **Command encoder — measured offline, not yet live.** `ai/train_command_encoder.py` freezes
   the 24.9M domain trunk (`models/encoder30_v15`) and trains **0.29M heads** (user-span
   adapter, intent, action conditioned on intent, BIO slot spans) on `data/commands`:
   **1,619 train / 20 val / 23 test**, 13 intents. The builder holds out 26 base phrasings
   (13 val / 13 test), keeps held-out entities disjoint, and rejects eval texts and full-phrase
   near-copies from train; all four properties are gated (922 checks; `MUT_ONLY=command`
   proves the checks, 19/19 caught). Measured by `models/command_encoder30/metrics_eval.json`:
   - 26 held-out base phrasings: **84.6% exact command vs 53.8% rule**; intent **96.2% vs
     69.2%**; slot exact 84.6% vs 69.2%.
   - Per-row, including entity-swapped copies: test **intent 100% vs 56.5%**, exact **65.2% vs
     34.8%**; val exact 70% vs 35%.
   Remaining base failures: Spanish knowledge query with a held-out entity (`busca Bouftou`),
   `all` for `arrête tout` / `para todo`, and the accented `d'équipe` channel span. The flat
   0.25M word-level BiGRU overfits (train ~100%, held-out 30–45% on the earlier corpus) and is
   kept only as a negative baseline. The **rule remains the live parser**: no shadow session
   and no rule removal yet.
2. Build the **TRM-style recursive core prototype** (≤5M) and compare it with the 49K MLP and a small
   transformer on the counterfactual tool corpus: held-out outcome, params, p50/p95, recursion steps.
3. **Shadow + replay loop.** The read-only shadow is shipped: `--command-shadow auto` writes one
   `command_shadow_status` row per session and one `command_shadow` row per goal (rule parse,
   model parse, agreement, `would_override`), fail-open when torch or the artifact is absent; the
   rule still acts. A first real prompt already disagrees usefully: "fais les quêtes" is
   `quest/list` by rule and `quest/run` by the model at 0.85. `tools/command_shadow_report.py`
   runs the same comparison over the recorded autopilot logs: 7 distinct real goals / 5,721
   uses, 2 override goals (4,300 uses); the dominant "do all the quests" is rule `quest/list`
   and model `quest/run` at 0.958. The `plz` framing flipped the model to `list`. A train-only
   `plz/pls` + auto/farm pass fixed that in shadow (4 override goals / 5,709 uses) but cost
   three held-out base rows (test exact 84.6% -> 61.5%), so it was rejected and the data
   reverted; the real prompt strings stay out of train. Next: a pass that adds `plz` without
   shifting the non-quest intents. `agent/commands.py` now carries the bridge the planner needs: `intent_tool(intent, action)`
   maps the learned parse to a registered tool (quest run/advance -> quest.advance, list ->
   quest.list, farm start -> farm.start, ...); every value is gated against the tool schema.
   The replay scores it: **9/10 override groups executed the model's mapped tool**
   (`quest.advance`), while only 11/61 non-override groups executed the rule's `quest.list`.
   The model's action is the one the plan actually carries. The stall taxonomy also closes a
   false lead: the 25 stalled groups are **24 short failures + 1 fight-start, with zero read
   loops**; the 46 advanced groups are long (5,587 steps, 65% reads). The recorded stalls are
   short sessions, not read loops, so the parser is not their fix. The next step is a
   **shadow plan selection** that feeds this tool into the planner without acting, while the
   next live session diagnoses the short terminations.
   **First guarded per-instance adapter (2026-10-27).** `ai/finetune_command_adapter.py`
   fits the action head on the 16 recorded user goals with 160 base-train rehearsal rows,
   keeps only a checkpoint that passes `adapter_accepts` (no held-out intent/exact loss), and
   promotes with `heads_base.pt` for rollback. Result: user-prompt tool hits **5 -> 9** (all
   imperative quest phrasings now map to `quest.advance`), all **43 val/test rows** and a
   **10-prompt action canary** unchanged. The real prompts are adapter data now (per-instance);
   the 26 base phrasings remain the held-out gate. The acceptance guard now also includes
   `canary_accepts`: an update may not change any action-critical canary (list/advance/stop/
   sell/travel/follow), so a future per-instance pass cannot buy the user prompts with a
   collateral regression. The **shared intent head could not be
   specialised**: both intent+action and intent-only passes lost held-out base rows
   (intent-only 25/22 -> 21/18) even with 160 rehearsal rows and per-epoch guarding, so both
   were rejected. The accepted shape is a **routed per-instance adapter** (`router.json`,
   threshold 0.95): the base answers every prompt away from the 16 recorded goals, the
   fine-tuned heads answer the exemplars. Verified row-by-row: base **43/43 identical**,
   adapter **16/16**, canary **10/10**; all **7 real goals** map to the expected tool
   (6 override goals / 5,718 uses), including the first genuine routing change:
   `can you play the game ?` -> auto at 0.885. Rollback: remove `router.json` (falls back to
   the action adapter in `heads.pt`) or restore `heads_base.pt`. Then: run a live session
   with the hook, buffer `(prompt, rule, model, outcome)` rows, and guard per-instance
   adapter updates.
   The replay extractor (`tools/command_replay.py`) is shipped: **71 (session, goal) groups /
   5,721 executed steps**. The objective counter is flat (all 8,526 observations say
   `objectives_completed=0`), but the page verdict is not: **46 groups advanced, 25 stalled**.
   Of the 10 groups where the model would override the rule, **9 advanced and 9 executed
   `quest.advance`** — the parser's quest/run action matched the rule's productive plan in
   9/10 groups (observational: the rule executed it). The mode comparison is still the sharper
   boundary: on the 7 real goals the model agrees with `_goal_mode` 6/7 and would change the
   routing **0/7** at threshold; all overrides are the quest action (list -> run) inside the
   same mode. Replacing `_goal_mode` is therefore not the next lever — the parser has to feed
   the action/tool choice **inside** quest mode. `live_ready` now reports the shadow artifact and deps **ready**; the client
   bridge is the only blocker. With DofusFarm open, collect the first live rows with
   `python -m agent.autopilot --goal "do all the quests" --steps 20 --command-shadow auto`
   (or the host's equivalent), then read them with `tools/command_replay.py`.
4. First removal candidate: **tool choice** — only after phase 2 beats the teacher on held-out
   outcomes and survives a live shadow session.
