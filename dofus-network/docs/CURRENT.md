# CURRENT — how the running system is wired, today

This document answers *"what do I type, what listens on what port, and what model
is actually in the loop?"* It is a map of the **running** system, not a plan.
Live status is in `resume.md` (session handoff); the reasoning behind decisions lives in
`PROJECT_LOG.md`; `PLAN.md` was the status source through 2026-10-26 and is historical. Every port, path and number below was read from the code on
2026-10-26; anything unmeasured says so.

---

## 1. Services and ports

Five services cooperate. The host starts one brain daemon, and the daemon runs
one Autopilot per instance; the combat brain and Sentinel remain separate
loopback services.

| service | entry point | port | job |
|---|---|---|---|
| **Autopilot (per instance)** | inside the brain daemon, `agent.autopilot` | (client) | the autonomous loop: observe → choose → guard → execute → settle → verify → decide |
| **Brain daemon / Agent HTTP** | `run_brain.py` → `agent.serve --brain` | **8895** | one process: per-instance Autopilot threads, shared models/knowledge, `POST /brain/start` and `/brain/stop`, `GET /brain/status` and `/brain/health`, plus `/agent/*` |
| **Combat AI** | `combat/combat_ai_server.py` `run_server()`, started by the autopilot through `combat/brain_service.py` | **8891** | `POST /v1/combat/decide` — solver + learned ranker + live policy |
| **Sentinel** | `networkhealth/sentinel.py` `run_sentinel_service()` | **8890** | health/telemetry + `nav.goto` name→map resolution |
| **Directive bridge** | `agent/bridge.py` (host owns the listener) | **8893** (fallback) | `POST /directive` → client tool call; the *real* port is read from `%LOCALAPPDATA%\DofusFarm\bridge_port.txt` |

Facts about ports that have burned time before:

* **One Python process, many instances.** `desktop/aipanel.cs` starts
  `run_brain.py` once and drives it through `/brain/start` and `/brain/stop`.
  The daemon runs a `_DaemonAutopilot` thread per selected instance, owns the
  shared `brain.lock` (per-instance `autopilot.lock` writes are disabled), and
  keeps the old per-instance `run_autopilot.py` path only as a fallback. See
  `BRAIN_DAEMON.md`.

* **The host does not start 8891, and cannot.** `desktop/mainform.cs:342
  TryStartSingletonSentinel` is the only Python service the host spawns, and it uses the
  *embedded* interpreter, which has no `jax` (`combat/live_policy.py` needs it). Since
  2026-10-26 the loop ensures the brain itself (`Autopilot._ensure_brain`, `--brain auto|off`),
  probing for an interpreter that can import it and recording the outcome as a `kind:"brain"`
  row. Before that, nothing on this machine had ever started it: see G24.

* **Starting it is only half of a learned fight.** The brain also has to be handed a board it
  can act on, and until 2026-10-26 `fight.state` read the *characteristics* sheet's field names
  off each fighter's stats bag, so every maxima arrived as null and all 156 recorded boards were
  rejected (`observation_not_actionable`). It now reads the names the served bundle actually
  contains (`effectiveMaxLifePoints`, `actionPoints`/`maxActionPoints`,
  `movementPoints`/`maxMovementPoints`, `initiative`) for monsters as well as for the character,
  and the gate pins that no name outside the bundle can be read again. See G25.

* **The bridge port is dynamic.** `DEFAULT_PORT = 8893` (`agent/bridge.py:22`) is
  only a fallback. The host owns the listener and walks a candidate set —
  `CANDIDATE_PORTS = {8893…8899}` (`desktop/agentbridge.cs:39`) — publishing the winner to
  `%LOCALAPPDATA%\DofusFarm\bridge_port.txt`, which the loop reads first. **Measured
  2026-10-30: the file says `8896`.** Ports 8894 and 8896 have both been seen while 8893 was
  held by something else, and `--port 8895` collides with the brain daemon, so trust the
  file and never hardcode a number in new tooling.
* **8892 is dead.** It was the old ModernBERT stance-classifier combat server; the
  combat brain moved to 8891 (`combat/combat_ai_server.py:12`). Nothing should
  still dial 8892.
* **Loopback never goes through the machine proxy.** `agent/bridge.py` builds a
  no-proxy opener (`ProxyHandler({})`) so a `127.0.0.1` request is never routed
  out through `HTTP_PROXY`. A loopback request sent to an external proxy returns
  502, which previously looked like a dead bridge.

`run_autopilot.py` is a script, not `python -m agent.autopilot`, on purpose: the
embedded runtime's `python311._pth` resolves `..\dofus-network` to a directory
that does not exist, so `-m` cannot find the package from the install root. The
script puts its own directory on `sys.path` first and forwards argv untouched, so
the argument surface stays exactly `agent.autopilot`'s.

**How a change reaches the client.** The injected page patch is read from disk by
`desktop/WebView2Backend.cs:121` as `<exe dir>/scripts/mobile-disguise.js`, and `DofusFarm.exe`
sits at the repo root with `scripts/` beside it — so editing that file needs **no rebuild and no
installer**; a page reload is what applies it (`desktop/aipanel.cs:437` says as much). The Python
brain is launched the same way, from this tree. What *is* separately built is the served client
bundle and its resilience rules (see `docs/KNOWN_GAPS.md` G12) — that is a different artifact from
the injected patch, and confusing the two has cost time: a green `--bundle-selftest` says nothing
about `mobile-disguise.js`, and a changed `mobile-disguise.js` says nothing about the bundle.

---

## 2. The agent loop, in seven stages

`_run()` in `agent/autopilot.py` is one iteration per step:

1. **observe** — `state.snapshot` (a pure read, no wire traffic) then `flatten()`
   so the planner sees flat keys (`map_id`, `level`, `inventory…`).
2. **choose** — a policy picks a tool for the current objective.
3. **guard** — `validate_call()` (schema) + the kick-signature guard.
4. **execute** — the directive is posted to the bridge.
5. **settle** — for tools that move the world, re-observe until the state changes.
6. **verify** — label the step `advanced` / `noop` / `failed` / `blocked`.
7. **decide** — `continue` / `retry` / `recover` / `replan` / `abort`; on the third
   repeat failure, the `ThinkingLayer` proposes an *untried, grounded* alternative.

`max_steps=0` means unlimited (Full Auto); `steps_per_goal=8` is only the Order
mode budget.

---

## 3. Think-token pacing (why the loop does not fire ahead of the client)

The loop does not act the instant a call returns. A walk that returns `ok` has
only *started* the walk; judging it in the same instant scored every move as
`noop` and the loop retried an action already in flight. Three timers fix that:

* **`--think` (default 0.25 s, `autopilot.py:1945`)** — the "thinking token":
  after any world-changing action, `self._think_until = now + think_s`
  (`autopilot.py:1231`). The next decision will not be taken before then.
  Read-only and listing tools are exempt, so observation stays cheap.
* **`SETTLE_TOOLS` (`{quest.advance, nav.goto, fight.join, farm.start}`,
  `autopilot.py:90`)** — after one of these returns `ok`, `_settle()` re-observes
  every ~400 ms up to `settle_s` (default 3.0 s, `autopilot.py:196`) and judges the
  first observation that actually differs. A call that changes nothing still ends
  `noop`, but *after* the wait rather than before it.
* **quiet window** — `quiet_s = min(0.8, max(0.4, think_s + 0.3))`
  (`autopilot.py:522`): a step is treated as settled once the state has been
  stable for at least `quiet_s`.

`_action_complete` (`autopilot.py:459`) is the predicate that says whether the
before/after observations differ. This is the mechanism behind "pace the AI so it
doesn't act before the previous action finishes" — it is a dwell + a settle
re-observe, not a fixed sleep.

---

## 4. Policy modes

`--policy` (`autopilot.py:1948`) picks the chooser, choices
`rule` / `encoder` / `planner`, **default `rule`**:

* **rule** — the deterministic `QuestPlanner` only. No model, fully inspectable.
  This is the shipped default.
* **planner** — `NNPlanner` hybrid (`agent/planner_nn.py`): the ONNX
  `tool_policy.onnx` does `argmax` over its 21 trained tool logits, but the
  deterministic planner still owns argument filling and the safety gates. A wrong
  model argmax cannot produce an illegal call. The legal registry is 28<!--f:tools--> tools; the
  seven added since the 21-logit head was trained (the four of 2026-10-27 plus `ui.tap`,
  `ui.icons`, `shop.buy`) are rule-only until the head is retrained. `--ensemble act|shadow|off` controls the
  23-leaf decision-tree stack over policy + foresight + K-means state context;
  the default `act` is honoured only when `models/tool_stack.json` carries a
  passed cross-validation gate (§9.3 of `AI_ARCHITECTURE.md`).
* **encoder** — the 30M language encoder via `ai/encoder_serve.py`; `--model-dir`
  defaults to `models/encoder30_v15`.

Training roadmap: `docs/TRAINING_PLAN.md` — one shared ≤30M total / ≤10M active brain trained on the harness first,
then hardcoded decisions replaced component by component (Kaggle GPU).

**Withdrawn: the learned-vs-rule face-off (measured 2026-10-27, retracted 2026-10-29).**
`python tools/policy_faceoff.py --seeds 30 --steps 300` reported the learned tool policy
winning **21, tying 9, losing 0** at **66.10 vs 64.87 quests**. That comparison is
**invalid** — `KNOWN_GAPS.md` records that `policy_faceoff` scored on corpus seeds rather
than a held-out world, so any win built on it is void. The corrected instrument is
`tools/bench_quest_policies.py`: rule **79.90** vs learned **80.50** quests (5W/5T/0L,
p = 0.0625), and the oracle variant **loses** (79.80, 3W/1T/6L). So the learned planner is
not a demonstrated improvement — within noise on the honest benchmark — and it is a
distillation of the same teacher, so it cannot exceed it by much. The shipped default
remains `rule` at the CLI level; `resume.md` §9.4 carries the same correction. (The
harness caveat survives too: it seeds the planner's client-confirmed set with the curriculum
because the symbolic observation does not report startable quests, while the live G31 guard
is stricter.)

**Command parser (offline, 2026-10-27).** `ai/train_command_encoder.py` trains 0.29M heads on
the frozen 24.9M domain trunk over `data/commands` (1,619/20/23; 13 intents; eval texts and
full-phrase near-copies excluded from train and gated). On the 26 held-out base phrasings the
learned parser reaches **84.6% exact commands vs the rule's 53.8%** (intent 96.2% vs 69.2%);
per-row, including entity-swapped copies, test **intent 100% vs 56.5%**, exact **65.2% vs
34.8%**. It is **not acting**: `parse_command` still defaults to the rule. A read-only shadow is
shipped (`--command-shadow auto`): it logs one `command_shadow` row per goal with both
parses and whether the model would override; fail-open when torch/the artifact is absent.
`tools/command_shadow_report.py` runs the same comparison over recorded logs: 7 real goals /
5,721 uses, 2 override goals (4,300 uses); the dominant "do all the quests" is rule
`quest/list` and model `quest/run` at 0.96. `tools/session_report.py` prints a
`command_shadow` witness (rows, agreement, overrides), so the live readout says whether the
shadow ran. `tools/command_replay.py` pairs goals with their executed steps: 71 groups / 5,721 steps.
The objective counter is flat, but **46/71 groups advanced by the page verdict** (25 stalled);
of the 10 groups the model would override, **9 advanced and 9 executed `quest.advance`**.
The model would change the planner's goal mode on **0/7** real goals (6/7 agree; the only
high-confidence overrides are quest list->run inside the same mode). `intent_tool` now maps
the parse to a registered tool; **9/10 override groups executed the model's tool**
(`quest.advance`) versus 11/61 non-override groups executing the rule's `quest.list`.
The shadow artifact is now a **routed per-instance adapter** (`router.json`, similarity 0.95):
the base answers everything away from the 16 recorded user goals, the fine-tuned heads answer
the exemplars. All 7 real goals map to the expected tool; the rule now also routes `can you play the game ?`
to Full Auto (7/7 mode agreement, 5 quest action overrides / 5,717 uses). Base 43/43, adapter
16/16 and a 10-prompt canary are row-for-row identical. Rollback: remove `router.json` (falls back to the
action adapter in `heads.pt`) or restore `heads_base.pt`. The adapter guard now includes the
action canary (`canary_accepts`), so future per-instance updates cannot change
list/advance/stop/sell/travel/follow. The
25 stalled groups are **24 short failures + 1 fight-start, no read loops** (advanced groups
are long and two-thirds reads), so the parser is not the stall fix; the next live session
must diagnose the short terminations. The command shadow is deployable: `command_encoder_int8.onnx` (25.8 MB, one graph with both
head sets) + `command_onnx.json` + tokenizer, loaded by `agent/command_onnx.py`; the FP32
graph/torch heads are excluded from the installer. INT8 parity is 0/59 effective rows and the
torch-free wrapper matches torch on 0/76 texts. `live_ready` reports the ONNX shadow + router
ready; the release build is green (`setup.exe` **68,086,784<!--f:setup_bytes--> B**, measured
2026-11-12, after `tools/payload_audit.py --gate` cut the JAX grid weights the shipped runtime cannot load,
stopped the installer carrying this machine's credentials, dropped the files nothing in the tree reads
(ARCH_REASSESSMENT.md §L.6), stopped installing the torch tier the product does not start with (§L.8)
and stopped carrying the learned weights whose features are off by default (§L.11) — see
ARCH_REASSESSMENT.md §L. The old
100.1 MB / 214.0 MB figures predate the recompiles of the stale tracked `builder/Build.exe`. What actually blocks a live run today
is not the bridge alone: `tools/live_ready.py` reports **`FAIL egress`** (upstream
`127.0.0.1:40000` not listening under `mode=auto, fail_closed=1`), **`WARN client`**
(DofusFarm not running) and **`FAIL page build`** (last client `2026-10-29bf`, repo
`2026-10-30d`). No live session has used the hook yet. `tools/live_evidence.py`
turns that into one command: preflight, bounded autopilot with `--command-shadow auto`, then a
combined session/shadow/replay report plus the autopilot summary (`--report-only` re-renders an existing log). The packaged
copy was extracted from setup.exe and its report path verified end-to-end; a relative-log bug
was found and fixed (`resolve_log`). Remaining base failures are `all` for stop, a held-out
knowledge entity, and the French `d'équipe` channel span. See `docs/TRAINING_PLAN.md`.

---

## 4.5 The streak gate (`agent/gate.py`) — shadow-only, and now priced

Added 2026-10-30 and missing from every earlier description of the loop: a third decision
layer runs beside the planner and changes nothing.

- **What it is.** `RuleGate` counts consecutive non-`advanced` outcomes per call, keyed by
  the tool plus its arguments **minus the volatile fields** — `VOLATILE_ARGS =
  {lang, skill_uid, cell}` — because three taps of interactive element 513208 carrying
  different `skill_uid`s are one intent repeated three times, not three calls (G60).
  `VETO_AT = 2`; an `advanced` verdict clears the streak.
- **Where it runs.** `agent/autopilot.py:1711-1720` builds `_meta["gate"]` for every
  executed call and `:1815` feeds the verdict back to `observe()`. `Step` has **no** `meta`
  field, so the annotation reaches only the **trajectory** rows
  (`agent/recorder.py:386`), never the loop's own `autopilot.*` log. `clear()` runs per goal
  and per session, so a streak never leaks between runs.
- **Status.** Zero recorded rows exist: the file is newer than every log on disk, so the
  first `2026-10-30d` session is the first evidence. The wiring itself is pinned offline by
  `test_gate_shadow_reaches_the_trajectory` (10 checks).
- **It is not a new idea, it is an earlier version of one the loop already has.** The
  controller's no-progress bound fires at **three** identical/no-change repeats
  (`autopilot.py:2078`, `_recent[-3:]` / `streak >= 3`) and abandons or replans there; the
  gate is the same judgment at **two**, keyed per call rather than per rolling window.
- **Both sides are now measured (G70).** `python tools/live_outcome_probe.py --alternative`:
  wiring it would remove 1.68 % of successes on the temporal test fold and 3.12 % on the
  hash fold (retention 0.9832 / 0.9688), while the next acting call the loop chose pays
  45.5 % / 63.9 % against **0.0 % / 0.5 %** for repeating the same promise, with paired
  segment CIs excluding zero on both folds. That is observational — the loop chose the
  alternative — so the causal claim still needs the randomized arm (G39).
- **And the bar it must clear is real now (G71).** Promotion requires
  `successes_retained >= 0.95` **and** a paired CI above zero **and** a point gain
  `>= PROMOTION_DELTA_POINTS (0.05)`; the third condition was declared and unread until
  a mutation proved it.

---

## 5. Model inventory (what is actually loaded)

| artifact | size / shape | in the loop? | evidence |
|---|---|---|---|
| `models/tool_policy.onnx` + `.json` | 42 features → **21** tool logits, 49,365 params, val accuracy **0.8599** | yes, only under `--policy planner` | `planner_nn.py` argmax over 21 |
| `models/encoder30_v15/` | 24,874,131-param **text** encoder; `encoder_int8.onnx` 25.4 MB shipped | yes, under `--policy encoder` | `ai/encoder_serve.py` |
| `models/verifier_head.onnx` (+ `.json/.pt/_int8`) | 33-feature / 4-class | **gated off** (`aipanel.cs`): auto-loads only with `verifier_head.validation.json` carrying live/human labels, `n >= 60`, above-baseline; no such contract exists, so a normal launch runs rules-only | `--verifier-model` still overrides explicitly; read-only tools never use it (`verifier.py:416`) |
| `combat/encounter_selfplay.pkl` | PPO/PBT actor for live fights | yes, inside `combat_ai_server` | `live_policy.py` default |
| `networkhealth_artifacts/model.pt` | RTT/loss forecaster weights | **absent** → Sentinel has no model and falls back to relay telemetry | load refused; see `KNOWN_GAPS.md` |

The verifier head is *wired but gated*: its 1.0 validation accuracy is circular (rule labels on
rule features), so the GUI auto-loads it only with a live/human validation contract (G6); without
one the loop labels with rules, and an explicit `--verifier-model` still overrides for an experiment.

There is **no vision/image encoder anywhere**. `encoder30_v15` is text-only; the
"vision inspector" in the Sentinel is regex over text, not an image model. See
`KNOWN_GAPS.md`.

---

## 6. Combat pipeline (`combat_ai_server :8891`)

Three stages, each optional, in a fixed order — the solver defines legality, the
learned parts only rank among legal plans:

1. **`battle_solver.py`** — exact beam search over legal turn plans (width 16).
2. **`plan_value.py` `rank_plans()`** — a learned ranker that *reorders* the
   solver's legal plans; it cannot invent an illegal one.
3. **`live_policy.py`** — the PPO/PBT actor, realised onto solver-legal plans,
   served as `POST /v1/combat/decide`.

The client's per-spell `castable` flag is authoritative, including when it vetoes the whole
spellbook: measured on 794 logged live turns, the 135 blanket-vetoed turns with AP left are 113
times a turn the character had *already cast in*, and the app asked the brain again after its own
action was committed — which is what the 443 `policy_pass` rows in
`trajectories/live_policy_decisions.jsonl` mostly are, not a declining policy. A server-side
relaxation of that veto was built, measured (+27 casts on the recorded boards) and **reverted**,
because it proposed casts on turns that have none left and the client re-checks the same call
before it clicks. ARCH_REASSESSMENT §L.9 has the numbers and the surviving ~19% of requests that
should never have been made.

Self-play training (`encounter/` ES + PBT) produces `live_policy` weights. **Live fights
are now proven — what remains unverified is calibration.** `trajectories/fight_outcomes.jsonl`
holds 22 rows, 9 with `won: true`, over `fight_id` 124, 900, 1356, 1358, 1359, 1482, the
last entered by `quest.advance` rather than a hand-made `fight.join`, and the regression gate
fails without such a row (G7). Still offline: `executor.py` records `live_calibrated: false`
and `models/fight_calibrator.json` is absent, so `p_win` is null and unattended fights stay
refused by design; and the `2026-10-28w` battle-log ring has never been read out of a live
end message, so a won fight still contributes no damage sample. All six won ids come from
one session (`7a871278`): no boss, no sustained sample. (Do not count offline tests as live
combat proof — and do not now count live fights as proof of calibration.)

The 30M 560-cell grid model (`combat/grid_model.py`, 30,766,081 params) exists and
passes a finite forward check. The first Kaggle run (synthetic solver distillation,
440 rows, 4 epochs) reached val top-1 0.786 / test top-1 0.60 and saved
`models/combat_grid/model.pkl`; it is **not** a live combat policy. It is wired as an
opt-in read-only ranker (`fight.plan ranker=grid`), default is still `solver`, and
it refuses when the board has no positive AP or the artifact is absent. The live
gate is one actionable fight on the current page build (`2026-11-11a`; `2026-10-29bf` is still the
newest one actually recorded), then a side-swapped win. Note that fights are now won live
(§6 above) — what has not been seen is a board with positive AP reaching the grid ranker, so
the model is still validated only on synthetic distillation (`data/combat_grid_synth_v2`:
1,872 boards, 1,318 actionable, `live: false`).

The normal-build duel benchmark (`python -m combat.normal_build_duel`, recorded in
`benchmarks/normal_build_duel_v4_1.json`) measured on 2026-10-31 that its asymmetric
pairing is decided by the build, not the policy: **all five 20-game matchups of
4,825 HP Ecaflip vs 7,500 HP Cra ended 100% for side 2 with an identical 6.0 mean
turns**, solver and value alike. The `mirror` block (same build both sides, same
seeds, 20 games each) isolates the policy: **the solver wins 20/20 from seat 1 on
both builds; from seat 2 it wins 9/20 (Ecaflip) and 15/20 (Cra) against random's
5/20 and 6/20 from that seat**. Neither the v4.0 value model nor the opponent-reply
(two-ply) policy recovers seat 2 against a seat-1 solver (**0/20 each**): in this
direct-damage model the first mover with the best plan is not beaten, so the next
combat increment is mechanics (denial, shields, push, statuses), not another ranker.
Read policy skill in `mirror`; the asymmetric block only documents the build gap.

Side-swapped verification (`python -m combat.side_swap`) is the harness an acting
fight policy must clear before a promotion is announced. It rebuilds every board from
the same seed twice and swaps the two policies between the seats, with identically
seeded decision rngs, so the only difference is who sits where; an exact two-sided
sign test plus per-seat rates yields `seat_dominated`, `a_better`, `b_better`,
`seat_dependent_edge` or `no_measurable_difference`. Measured 2026-10-31 on the
Ecaflip mirror, 10 boards / 20 games each: **solver vs solver is `seat_dominated`**
(seat 1 won 20/20 with identical policies), **two-ply vs solver is `seat_dominated`**
(10-10, seat 1 won 20/20), and **solver vs random is `a_better`** (17-3, p 0.0026,
100% from seat 1 and 70% from seat 2) -- the only pairing whose policy edge survives
the seat effect. It verifies acting policies, not the p_win calibrator (that one is
verified by calibration on out-of-time fights), and its board source is the duel
simulator; running it on live boards is owed with the live stack.

The shipped fight policy is now measurable inside that simulator as itself, not as an adapter of
it: `Board.to_fight_state()` lets `combat.duel.play_game` call `LivePolicy.decide()`, and
`BattleSolver.plan_for_action()` executes the action that comes back -- a walk, or a cast the beam
pruned -- with the search's own arithmetic. `python tools/wsp_fight.py base --matchups 200
--policy production` scores it over 400 side-swapped games: **0.4062<!--f:fight_head_win_rate--> (CI
0.3787<!--f:fight_head_ci_low-->-0.4325<!--f:fight_head_ci_high-->) against the deterministic solver,
1<!--f:fight_head_wins--> win / 157<!--f:fight_head_draws--> draws / 42<!--f:fight_head_losses--> losses, zero solver
stand-ins.** Against the measured ceiling -- `--abstain 1.01`, which makes the head abstain every
turn so both sides play the solver -- the pure solver scores **0.4938<!--f:fight_ceiling_win_rate-->**, so the
head's own decisions cost **0.0875<!--f:fight_head_cost_vs_solver-->** of the win rate, paired over the same
200 matchups (40 matchups worse, 3 better, sign test p = 3.02e-09; the head is *below* the ceiling,
and the sign lives in this sentence rather than in the number, because a negative literal renders and
re-parses badly). These are references, not retyped digits: re-run the two arms into
`data/fight_head_production.json` and `data/fight_head_ceiling.json`, then
`python tools/wsp_fight.py compare` those two documents into `data/fight_head_vs_solver.json`, and
`doc_facts --check` fails if the prose no longer matches the measurement. That replaces the
0.1512 adapter figure, which was scoring the head's preference among beam-kept plans only; 48.9% of
the casts the real rule plays are pairs the beam never carried. See
`docs/ARCH_REASSESSMENT.md` §L.18-§L.21 for both numbers and for the paired delta.

The live request no longer builds the observation it does not read. `combat.observation.live_view`
answers with the contract plus the four-key board block -- the only parts of it anything consumes --
and `DOFUS_FIGHT_OBSERVATION=full` restores the whole dict for a diagnostic run.
`tools/observation_contract_probe.py` proves the swap lost nothing: **0<!--f:fight_obs_mismatches--> parity
mismatches across contract, board block, slim view and full view over 135<!--f:fight_obs_boards--> recorded
own-turn boards**, and it prices the observation path at 0.57237<!--f:fight_obs_full_ms--> ms/board down to
0.04721<!--f:fight_obs_slim_ms--> ms, which removes 91.8<!--f:fight_obs_removed_pct-->% of it on the shipped interpreter.
The paired `decide()` measurement, the 135/135 identical-action result and the 34.8 MB storage
ledger are in `docs/ARCH_REASSESSMENT.md` §L.34.

What the brain process itself costs, measured by the OS on the shipped runtime over 675 streamed
decides (`python tools/brain_ram_probe.py` on `runtimes/python`): **34.1<!--f:brain_resident_mb--> MB resident,
147.3<!--f:brain_peak_mb--> MB peak working set, 283<!--f:brain_commit_mb--> MB commit, 2.961<!--f:brain_decide_ms--> ms per
`decide()`.** The staged breakdown says where that is: loading and warming the trained head adds
2.4 MB resident, while `import numpy` alone reserves 233 MB of commit, so the weights are not the
memory story and any future trimming should not aim at them (§L.36).
`python tools/brain_ram_probe.py --mode floor|parse|loop` separates the arms: a single decide, a
parse-only sweep of 135 boards and 675 sustained decides all peak at the same ~146 MB and all end at
33-34 MB resident, so the peak is a high-water mark the first unit of work touches rather than a
per-turn cost, and **it does not grow with load**. The number to plan instances against is
34.1<!--f:brain_resident_mb--> MB resident, not the 147.3<!--f:brain_peak_mb--> MB peak -- commit is not residency on
this box (§L.36, #84).

The online loop for `p_win` (`combat/online_learn.py`) is the deployment path for
`models/fight_calibrator.json`, the file `fight_estimate` already consumes. It reads
`trajectories/fight_outcomes.jsonl`, trains on the older fights, scores the newest 30%
out of time, and promotes only when, on that slice, it beats the analytic `raw_score`
by **AUC ≥ 0.02 and Brier ≥ 0.02**, keeps **ECE ≤ 0.10**, passes a 500-draw
permutation check at **p ≤ 0.10**, and has at least **40 resolved fights** (12 in the
evaluation slice). Promotion is versioned under `models/fight_calibrator.versions/`,
the previous version is kept for `--rollback`, and every run appends to
`models/fight_calibrator.history.jsonl`. Evidence is filtered by named rule before it
trains: unresolved outcomes, duplicates, non-live sources, fights with no start row,
and -- measured on 2026-10-31 -- **tutorial-authorised scripted fights**, of which
**7 of the 9 resolved live fights** are instances; `--strict-members` additionally
requires priced monster facts, and `--allow-scripted` overrides the scripted rule.
Measured on the live corpus: **2 of 9 resolved fights are eligible** (both from one
session, both with unpriced monsters), so the loop reports **collecting 2/40** and
**cadence 2/20**; the estimate keeps `p_win: null` and unattended fights stay refused
by design. `--shadow-report` scores the incumbent (or the analytic baseline when none
exists) on the newest slice; today it reports no incumbent, with 1 eligible fight.
Thirteen checks pin the filters, gate, cadence, promotion and rollback; the module is
delegated by the gate next to the other combat suites.

**The close pipeline** (`tools/session_close.py`) turns a finished run into the next
input. The daemon's `Autopilot` spawns it at `session_end` (out of process, 300 s cap;
`AUTOPILOT_SESSION_CLOSE=off` disables it), and the same command runs by hand. It
reports the logs that changed since the last close (delta against the previous
close, so a file total is never re-counted), rebuilds `data/transitions` with the
temporal split, cleans it into `data/transitions_clean` through
`tools/data_janitor.py`, retrains the live move head on that clean corpus and records
its out-of-time verdict, gives `combat/online_learn.py` its event-driven chance to
promote, and appends one row to `data/session_closes.jsonl` plus per-log offsets to
`data/session_close_state.json`. Measured 2026-10-31 on the recorded corpus: **33.5 s**
before the move step (report 11.8 s over 16 logs, rebuild 17.9 s, janitor 3.9 s,
learner 0.02 s) and **89 s** with the three-seed move step (42.8 s); a close with no
new bytes writes `no_new_rows` and does no work, and a rebuild within 300 s of the
previous one is skipped while learning still runs.

The janitor found the on-disk corpus stale (G92) and, after the rebuild, kept **524
of 9,687** rows: **8,865 tutorial-authorised**, 294 without a state vector, 109
unpaired (G93). Of the 392 kept actions, **143 moved and 3 advanced a quest** (client
`step_objectives` done-flips, G94; the label previously saw none), and
**8 of 42 features** are outside the harness reference range (`kamas`, `quest_level`,
`n_params` at 100%). The live move head on that corpus scores its temporal test fold
(123 actions, 75.6% base rate) over three seeds at **median AUC 0.787, worst-case ECE
0.315** (five seeds span AUC 0.603-0.820, so one draw is not evidence), and the
promotion gate (**≥200 actions, median AUC ≥0.60, worst ECE ≤0.10**) refuses it and
writes no artifact; the refusal lands in the ledger on every close.
`tools/progress_report.py` reads those rows as the goal's scoreboard: it sums only
non-baseline session deltas (a legacy row is named and excluded), reports the latest
clean corpus's world-changing rate, and carries each close's move verdict and eligible
fight count. Objective progress is judged on `objectives_done`, the number of client
objective statuses that flipped done in the session, because the published
`objectives_completed` is a step-index sum that reads 0 for single-step quests and can
fall when a quest finishes (G94); the verdict prints both. The per-session rate is the
difference between consecutive corpus
snapshots (a rebuild that loses rows is marked `shifted`, not counted as a negative
session; a rebuild whose `labeler_sha` differs from the previous close is marked
`relabeled` and kept out of the trend, so new labelling code cannot pose as session
work), and the verdict names the trend when two sessions have a rate. On the five
recorded closes it says **"objective progress not yet
measured"** -- three are migration closes that predate the baseline marker and the
state-key fix, and the rest carried no new bytes; the first close with fresh rows is
the first real measurement. Each row also names why fight evidence was ineligible (the
learner's kept/dropped funnel) and carries the three per-seed test AUC and ECE of the
move head, so a corpus that cannot train says so in its own row. Each row also pins
the identity (path and short sha256) of the deployed foresight, the fight calibrator,
the router stack and the move head this close trains (measured but not wired live),
taken *before* its learning steps, so a later side-swapped verification can pair two
sessions by the heads that actually drove them. Fifty-three new checks
across seven suites and twenty-five new mutations pin this; a close lock serializes instance
endings, so a second close stands down instead of rebuilding the same corpus. Every
promoted file artifact keeps the bytes it replaced as `<name>.previous.json`, and
`--rollback` on the move-head and router trainers restores them (`agent/artifacts.py`)
-- the same recoverability the fight calibrator already had. An offline dry run on a
copy of the real logs, with two synthetic live steps (+1 objective with a done flip,
one map change), closed the whole chain end to end: baseline close 55.2 s keeping 524
rows (the `dofus-05` delta carrying **4 newly done** objectives against the published
counter's **-19**, G94), then the delta close 45.6 s with **steps +2 / objectives +1
/ newly done +1**, the clean corpus at 526 rows / **394 actions / 1 advance / 144
moved**, the move gate still refusing (median AUC 0.748, worst ECE 0.263) and the
scoreboard printing **"objectives +1 (1 newly done) over 1 session(s) with new rows;
... last session +2 clean action(s), 1 moved (50.0%), trend unmeasured"** -- both
closes far under the 300 s hook cap. The rows were synthetic and learning was disabled
for that run, so this proves the measurement path, not live progress.

---

### 6.9 Fight weights that exist, and the one that is wired

The live server loads `models/encounter_selfplay.pkl` (1,025,321 B, 96 hidden / 2 blocks) through the
numpy backend. Alongside it sit measured-but-**not-wired** candidates: `encounter_wsp_all.pkl` (the
same geometry plus one rank-1 spell-head write, +0.0525 paired on the pinned dev table) and the
Kaggle teacher pipeline's arms (ARCH_REASSESSMENT §N.10) -- the best of them +0.0650, which is
+0.0125 over the cheap write and *not* resolvable at 200 paired matchups. A 128/3 teacher checkpoint
(2,168,724 B) exists only as a distillation source and was never a shipping candidate at that size.
Nothing here changes what `:8891` loads; promoting an arm is a deliberate decision and an installer
payload rebuild.

### 6.10 Boss fights: what the weights can and cannot be made to do

Touch-derived kit/stat corpora live outside the payload in
`C:/Users/sao/Documents/dofus-evidence/touch-data/`. `train.py --spells` supports six-slot
models. `wsp_fight --harness pvm` evaluates raw tensors against our scripted opponent in an
abstract simulator, not the client's monster AI. The duel harness is network-versus-solver with
seats reversed, not self-play mathematically constrained to 0.5.

Corrected 2026-11-05: the previous 0.2185 was a score including half-credit for draws, not a win
rate. Under the old turn order, terminal wins are **408/2048 = 0.1992** with the policy *sampling*
its actions (**412/2048 = 0.2012** with greedy/masked-argmax selection; boss subset 0.1835 on 1,041
boards).
The existing movement edit reduces the sampled figure to **249/2048 = 0.1216**; it is not promoted.
The seed-8 corpus is development data after repeated inspection. Details and source-bound rechecks
are in ARCH_REASSESSMENT §N.11.6. The two selection policies are never mixed inside one comparison:
`wsp_fight.compare` refuses pairs whose recorded `action_selection` differs.

**The encounter turn order was then corrected (#88), and every number above is pre-fix.**
`action_masks` admits a spell reachable with the actor's MP, and `step` spends MP before casting,
walking the chosen direction and stopping at the first legal distance. On the same unchanged
checkpoint that is worth **+0.025 terminal win** (0.2012 → 0.2266 on the development corpus, greedy
selection), and it takes "no legal spell at all" from 23.8%/38.1% of acting boards to 1.0%/1.5%
(`C:/Users/sao/Documents/dofus-evidence/census_castable.py`). Four new `combat/test_env_math.py`
arms pin both halves, on both backends. The scripted opponent's move rule had to be re-priced with
the change: measured before that, the same fix read +0.35 because the monsters had stopped walking.

Since the fix, the outcome-keyed WSP recipe found 47 turnable decisions in 3,216 but every eta
lowered training outcome, so it refused to write. The headroom that remains is not in the weights:
**39.9% of monster-opening boards kill a solo player before its first decision** -- that is a
pre-fight planning problem (#131), not something an action head can edit. Faithful boss mechanics
(summons, states, glyphs, party), challenge/achievement reading and live outcome verification
(#127) remain unfinished.

**The action head was then audited for dead weight and found to have none, and the defect turned
out to be in the value read (ARCH_REASSESSMENT section W).** On 13,002 boss decisions
`tools/wsp_noise_audit.py` reports 0 inert columns, 0 columns out of contention and 0 of 8
coordinate blocks with no effect, so there is no meaningless weight to delete; the trunk vector has
participation ratio 6.24 but so do its *inputs* (5.09), and no closed-form readout of it beats the
head already reading it (0.2259 against 0.2654 held out). What is defective is `value`: predicting
the terminal outcome of the fight from a decision scores AUC 0.7816 with the shipped value head,
0.7986 with nothing but the health-bar difference, and 0.9009 with a solved readout of the same
forward pass. Solving `value2` in closed form and writing those two arrays moves held-out AUC to
**0.8986 (+0.1170)** and Brier from 0.2293 -- worse than the 0.1950 constant predictor -- to
**0.1188**, with the same refuse-if-it-does-not-improve gate and a post-write check that only
`value2` differs. Separately, `tools/wsp_search_value.py` prices a 1-ply lookahead over the *same*
weights: **+0.0254 terminal win** (0.2188 -> 0.2441, paired CI [+0.0117, +0.0391], 13 losses turned
and none lost) at 10x the batch cost, while picking a random legal spell instead *costs* 0.0430 --
so the gain is the rolled-out outcome, not variety. That is roughly 8x the best action-head weight
edit and the same order as the turn-order fix.

**Correction: the pre-fight promotion claims in W.10–W.11 are withdrawn pending repair.**
The old strike formula assigned the player's full damage to every enemy at once, including padded
slots, and omitted power and the cast cap. Its reported 0.8071 AUC is a correlation for that score,
not evidence of correct kill-time arithmetic. The stepped comparator reused row-zero HP across
boards and did not independently activate each side. Its 0.7479 AUC cannot validate that comparison.

The final `round` field omits the terminal increment, so zero-round deaths were not free fights.
The 92.71/111.48 ratios are not elapsed-time throughput. Keeping every eventual win is not a
throughput ceiling (discarding slow wins may increase the ratio), and the claim that 80% of useful
information is absent from the opening board has no basis. The opening ridge's reported 0.8956 AUC
is a limited corpus result, not a global ceiling; its coefficient artifact is experimental, not
promoted. Closed-form ridge is supervised fitting, not evidence of direct LLM-authored WSP.

`preflight.play` also routed the wrong policies when monsters opened; that is now corrected.
Each side's opening damage is estimated from its own independently initialized turn, and readiness
checks the worst verified outcome before calling a plan favourable. Regression tests exercise both
initiative orders, turn counting, isolated estimates and rejection of verified losses.

Distance remains an actor-local scalar in this simulator: moving the player does not update enemy
distances. The measured correlation does not establish a legal live starting cell. No live
positioning or refusal rule is certified by these experiments; #139 is reopened.

**Mechanics are the front that was actually closed off, and the ceiling is now measured (section X).**
`touch_source.slot()` discarded 949 of 1,775 boss spell levels (53.5%) because they carry no damage
or heal line, and took at least one spell from 456 of 519 boss species; 108 species put a state on
the board and 138 summon. `combat/encounter/mechanics.py` now types all 700 published effect
definitions from the client's own `category`/`bonusType`/`characteristic` fields and emits a 79-number
record per spell, which lifts levels carrying a simulated effect from 826 to 1,503. Two measurements
follow it. The shipped target head's difference between two spells' target rankings is *exactly*
`t_bias[s1] - t_bias[s2]` on all 2,048 boards, standard deviation 0.000 -- the mechanic-aware head's
is 0.477 -- so spell-conditioned routing was unrepresentable, not under-trained, and
`combat/test_mech_net.py` now pins both halves plus the exactness of a closed-form install. And
removing the five resistance columns from the observation changes the opening target on 5.0% of
boards while moving win rate by **−0.0063 [−0.0078, +0.0078]**: the vulnerability data is read and
worth nothing, which is why a lookup tool aimed at this head is a planning-layer change and not a
combat-strength change (the same facts as a derived table cost 149 KB gzipped against 33.7 MB
resident for the network).

**What bounds action-head edits: 8%, not 3%.** Labelling every legal (spell, target) pair by playing
the rest of the fight out, 77.3% of the 194 held-out decision states are won whichever pair is picked
and only **6 of 194 (3.1%)** are decided by the choice. *(Corrected 2026-11-06, §XI.4: 54% of those
denominator states sat on boards the fight had already ended -- `env.step` freezes the acting team on
a done board and the selector asked only for "is this the player's turn". On live states the pair
share is 8.31%, and all 32 deciding states were already live, so no gate verdict changes: what binds
the gates is the count of deciding states, not the share.)* A closed-form relational write, solved in
either a frozen random trunk or identity feature tokens, was therefore priced and refused: it wins
0.0156–0.0293 against uniform-legal 0.0723–0.0767 and the trained checkpoint's 0.244–0.249. The
first acceptance rule reported `accepted` for beating a comparator that wins 0.68%, which was wrong;
the gate now requires a paired gain over uniform-legal choice, enough mixed states, and a ranking
that beats chance on them. The move axis is priced separately because it changes which spells are
legal rather than ordering a set that mostly does not matter, and the first reading of it was wrong:
at 32 boards of labels the solved walk looked worse than the scripted rule, and at 1,024 boards it
is not. Powered result on 2,048 untouched holdout boards -- solved move head **0.2656** against the
trained checkpoint's 0.2441, paired **+0.0215 CI ±0.0102**, against the scripted walk's 0.2500,
paired **+0.0156 ±0.0067**, with the move ranking 0.977 against 0.605 chance on the 43 states where
the walk decides the fight. The pair write on the same data costs **−0.1802 ±0.0172** with the walk
held fixed: the same solver, on the axis whose labels say the choice matters about as much (2.76%
against 3.08%), produces a policy 18 points worse than leaving that head alone, because a one-step
outcome fit is not a Q function -- and note the scope, since it matters for what follows: that is the
**mechanic-free** corpus, a 9-number spell token in which a pushback does not exist. The accepted write is published as an evidence artifact with its own
verification record read back off disk -- `dofus-evidence/encounter_target_wsp.pkl`, sha256 prefix
`e698529deb67e72d`, `changed_leaf_groups: ["move"]`, `move_max_abs_error` 3.3e-07. It is not in
`models/`, not wired into `combat/live_policy.py`, and untested on the live client: promotion is the
owner's call, and every number here is simulator-versus-scripted on boards generated from real
Touch kits.

**Mechanics were then built for real, measured, and they changed the verdict by 0.7 points of
ceiling (sections X.8-X.10).** The environment now applies an execution, a pushback and a timed
AP/MP reduction; the client's tables type them for monsters by effect id and for the player from the
encyclopedia's own wording, with the two encoders agreeing on damage for **1,980 of 1,980** spell
levels after three typing defects were fixed (the critical line was summed into the normal one, a
multi-element spell was priced as the sum of its elements, and "Steals 10 Intelligence" was typed as
an AP drain). `dofus-evidence/mechanic_ablation_env.py` is the probe that can fail, and it failed
usefully: zeroing the **player's** mechanic columns moves 50 of 1,024 boards' final HP and flips 24
outcomes (**0.2803 -> 0.2676**), while zeroing the **boss's** moves nothing -- because effect 141
"Tue la cible" had been written into the *damage* line as a `9999` sentinel, so the kill column could
never fire and the head saw an execution as 1.614 against 1.374 for the hardest real hit. With the
sentinel gone and the scripted monster made to value a removal above damage, the boss still expresses
almost nothing: all 140 enemy kill slots are legal at range 1-1 in a 1v4 board with no geometry, 24
cost 16 AP against a 12-point bar, and enemy push and AP-drain slots are 1 and 0, because boss threat
in this table set is summons (138 species) and states (108 species), which are not simulated.
Re-priced on those boards with the same knobs -- 7,998 labelled candidates, 837 decision states, 512
untouched holdout boards -- the pair-decided share moved **3.08% -> 3.82%**, the closed-form pair
write is still harmful (**−0.2500** against the scripted comparator the checkpoint cannot serve as,
since it reads a 49-wide observation), and the move write keeps its sign and loses its power
(**+0.0137 ±0.0245**, 7 decided states against the 60 the gate needs) so the gate refused it. The
published `move` artifact stays the promoted candidate measured on the wider holdout; #145 records
what would have to be simulated for mechanic routing to have a boss signal to learn.

**A party was built, the wire was priced against it, and both said no (section XI).** `build_dataset`
now draws real allies -- 4v4 corpora at the published seeds, 12,940 player spell slots carrying a
forced effect -- and the parity gate is proven rather than asserted: an `allies=1` rebuild is
**bit-identical** to the corpus the morning's numbers were measured on. Two statistical corrections
came out of doing it honestly. The ceiling: `check_published_ceiling_denominator.py` found **452 of
837** labelled decision states sitting on boards the fight had already ended (the walk keeps asking a
frozen board for decisions), which had understated pair leverage by 2.2x -- **3.82% → 8.31%** on live
states, with an independent instrument agreeing at 9.31% -- while all 32 deciding states were already
live, so no gate verdict changes. The party's own share: **10.9% [5.3%, 19.1%]** once the interval
resamples *boards* instead of pretending states are independent; my first 64-board reading of 5.0% was
small-sample noise in the optimistic direction, and the party interval overlaps the solo one.
`tools/bid_channel.py` says why: the bid -- allies publish their intended target and re-decide,
weighted in units of the score gap it has to overturn -- changes no outcome, because in the
**independent** arm 2.23 of the 4 allies already aim at the same target. A damage-max rule concentrates
by itself, so focus fire is not information a channel adds; forcing it costs 0.047 wins at weight 4.
A wire would have to carry what a damage table cannot imply -- who is disabled rather than damaged, who
is covering, which summon dies next -- and the summon part is now in the fight rather than in the
tables (#145 landed 2026-11-06: the pool is real, and it is worth a pass, not an attack).

---

**The boss now summons, and it is worth a pass, not an attack (2026-11-06, ARCH_REASSESSMENT §XII).**
`env.step` gained a mean-field summon pool per team -- three corpus columns (`spell_summon_bodies`,
`spell_summon_hp`, `spell_summon_damage`) resolved from the client's own species tables, bodies that
intercept damage aimed at their master, a swarm that strikes at the end of its team's turn and whose
output falls as its bodies die, capped at the team's own total HP. Absent columns leave the old
arithmetic bit-for-bit. This is the first enemy-side mechanic that moves a terminal state: clearing
it on 1,024 hold boards changes 68 final-HP outcomes and **20 winners** (was 0 of 512 before), worth
+0.011 to +0.025 player win rate; *forcing* the boss to summon whenever it legally can is worth
+0.085 to +0.097 **to the player**, 99 monster-wins flipped to player-wins against 0 the other way.
So the direction of any learned summon preference is settled: the greedy script, which reaches for a
summon only when nothing hostile is legal, is already on the good side of the curve. The spell token
is 17 numbers wide on these boards (81 per actor), so the mechanic-aware checkpoint of task #142 has
to be trained at that width -- nothing existing reads it, and nothing here is promotable.

**The 81-wide checkpoint pair now exists, and its first honest gate is negative (2026-11-06,
ARCH_REASSESSMENT §XII.3).** `dofus-evidence/models/mech_swarm_{visible,blind}_s1.pkl` each contain
206,338 trained parameters after 20 iterations on 256 boards. Direct array inspection against the
real initializer proves that every blinded mechanic/summon/pool input row stayed at exactly zero
drift, while those rows moved in the visible arm. On 1,024 untouched hold boards, however, visible
wins **0.036133** against blind **0.092773**: paired **−0.056641 ± 0.016104**, interval
[−0.072744, −0.040537]. The scripted reference wins 0.243164, so neither network is close to
promotion. The scoped verdict is that mechanic input **costs wins at this training budget**, not
that mechanics are inherently useless. `tools/read_weights.py` and `tools/mech_input_price.py` are
the reproducible readers; their reports live beside the checkpoints outside the installer payload.

**Direct numerical WSP now has a measured result (§XII.4).** After tracing a hold-instead-of-approach
failure, one existing weight was manually changed: `move[0][89,0]`, −0.3382473 -> +0.1245009.
No fitting or retraining. Reloading the artifact proves only that coordinate changed. On the unused
second half of the hold corpus, wins rise **95 -> 138 / 1,024** (+0.041992, paired 95% interval
[+0.029406, +0.054578]; 44 gained, one lost). A bias-only control wins 137, so this is primarily a
correction to excessive reluctance to approach, not evidence of a decoded tactical neuron. The
scripted reference wins 230. `dofus-evidence/models/mech_swarm_blind_direct_move89.pkl` is retained
as an experimental artifact; the installed policy and original checkpoint are unchanged.
A second literal spell-weight edit (−0.2128431 -> +0.75) was **rejected**: development wins stay
11/64, with two gained and two lost. Both regressions select a minimum-range-2 spell at distance 1
while movement still approaches; replaying with retreat restores the kill (§XII.5). The retained
candidate remained the movement-only artifact at that stage; no additional holdout was used then.

**The larger replication revises that spell verdict (§XII.6).** On fresh seeds 117 and 231,
1,024 fights each, the unchanged two-weight candidate wins **181/161**, versus **159/145** for
movement-only and **107/95** for the original NN. Pooled: **342 versus 202 wins / 2,048**,
+0.068359, paired CI [0.055501, 0.081218], 165 gained and 25 lost. These are exactly two manually
authored weight changes, not fitted weights. A third weight meant to repair range selection passed
eight local controls but reduced wins to 314/2,048 and is rejected. The stronger experimental
candidate is now `dofus-evidence/models/mech_swarm_direct_move89_spell85.pkl`; it still trails the
scripted reference's 465 wins and is not installed or promoted.

**Direct first-decision WSP is now exhausted (§XII.7).** A 512-board counterfactual sweep changes
one action head at a time while holding the rest of the fight fixed: of 106 candidate losses, **97
cannot be rescued by any move, spell, or target change**. Only 9 (8.5%) are single-decision errors,
and their winning choices do not share a repairable numerical pattern. The remaining gap is a
sequential-policy problem. Relevant tests pass: 47 tests, 24 subtests. No installed policy changed.

**A third direct numerical repair fixed the party shape bound (§XII.8).** `t_bias` was hard-coded to
five actor slots. Zeroing all target-prior weights preserved exactly the same solo terminal winners,
96/96 base and 171/171 action candidate on a fresh corpus, and made eight-slot boards addressable.
On two fresh 4v4 sets, original/movement-only/movement+spell arms win 981/1,482/**1,572 of 2,048**
(47.9%, 72.3%, 76.8%); scripted wins 1,790 (87.4%). The action edits pool to **+28.86 points**,
CI [26.87, 30.85], 597 gained and six lost, while the remaining scripted gap is 10.64 points. The
existing PvM harness initially scored 16 wins low by ignoring checkpoint blindness; after propagating
that field it exactly reproduces 793/1,024. The zero-prior checkpoint is evidence-only: no installed
policy changed.

**A fourth one-number spell edit was tested on party boards and rejected (§XII.9).** A 128-board
first-divergence trace of the 218 remaining 4v4 losses classified the divergent head (spell 86,
target 20, move 11). Overriding the first ally action recovers 26 boards; overriding all four
allies' spell+target recovers 59. Changing more actions is not an isolated coordination control,
and the trace's decision counts need an active-row audit. Aggregating
hidden activations put coordinate 91 in 82 wrong slot-0-versus-slot-2 preferences, so `spell[0][91,2]`
was set equal to `spell[0][91,0]` — exactly one changed coordinate. On the two diagnosis corpora it
gives +0.008301 (CI [−0.000091, +0.016693]); on two fresh party seeds never used for selection
(701/901) only **+0.002930, CI [−0.005304, +0.011163]**, and all four pooled +0.005615 with the
interval still crossing zero. The arm is rejected and the re-trace moves the dominant wrong term to
coordinate 71/slot1. A repeated local weight contribution can be real without being a terminal cause.
No installed policy changed.

**The party gap is localized to the opening, and the biggest traced number is a correlate (§XII.10).**
A takeover ladder on 125 traced boards recovers 0/71/109/119/123 boards at 0/1/2/3/4 scripted team
turns, and 125 under full control. Later divergences are movement (1,538 of 1,908), and move-head
coordinate 33 owns 1,527 of them — yet editing that one number in **both** directions loses about a
quarter of the candidate's wins (545 and 552 versus 793 on seed 317; paired −0.242 and −0.235, both
intervals far from zero). Restricting scripted control to a step window instead settles it: opening
steps 0–5 recover **236 of 250** gap boards (94.4%), middle 61, closing 9, opening+middle all 250. The
opening three rounds decide the fight and the later divergences are downstream, so the next numerical
lever is the round-conditioned path (`observe()` already ships `round / max_rounds` into the global
vector), not another head column. Both rejected arms are recorded in `dofus-evidence/models/
ARTIFACT_LEDGER.json`; the accepted candidate and the installed policy are unchanged.

**Round ablation is a measured effect, not proof that history is required (§XII.11).** Zeroing the
96 weights reading normalized round changes paired win rate by -0.00488 on seed 317
(CI [-0.01445, +0.00469]) and -0.01074 on seed 433 (CI [-0.01949, -0.00199]). A nonzero row norm
alone does not establish training drift. This ablation neither closes all chain-conditioning routes
nor establishes that a new input is necessary. Evidence: `dofus-evidence/models/party_round_channel_price.json`.

**The fitted move readout lost wins; the broader causal claim is withdrawn (§XII.12-XII.13).**
The move-readout arm wins 2,966/4,096 versus the candidate's 3,121, so it remains rejected.
Its agreement gains do not establish useful terminal control, and its failure does not refute every
per-decision numerical edit. The historical history/decodability collector did not exclude terminal
states; its decision-level statistics require an active-row audit before reuse. Neither that probe
nor this failed fit proves that cross-ally coordination is the missing capability. No installed policy changed.

**Opening spell-target interventions help; the tested static prior is unproven (§XII.14).**
The opening-only head controls recover 220/250 gap boards with scripted spell+target and 236/250
with all three heads. The +75 over the best single head is not proof of super-additivity. The static
`t_bias` arm's small gain has an interval crossing zero; it is not promoted. These tests neither
exhaust every static edit nor establish that an all-fight overlay reproduces the opening-only ceiling.

**The old score proxy does not eliminate aiming or coordination mechanisms (§XII.15).**
Its collector included terminal rows, and the 98.7% target-reallocation comparison mixed capped and
uncapped totals. Those statistics are not a certification of target optimality. Use the paired
terminal-outcome controls below rather than the earlier proxy-based causal claim.

**The all-fight overlay is strong, but nearly all diagnosis gain survives without coordination
(§XII.16-XII.16c).** The joint HP-capped overlay wins **3,521/4,096**, versus candidate 3,121 and
scripted play 3,564. This uses teacher scores on every player turn, not only the opening. Seeds
701/901 have since been reused and are not genuinely unused holdouts for future selected edits.

On diagnosis seeds 317/433, keeping NN movement and the opponent identical: NN **1,572/2,048**,
independent raw teacher pair scoring **1,770**, independent HP-capped scoring **1,765**, joint
allocation **1,774**, fully scripted **1,790**. Independent raw scoring retains **98.02%** of the
joint overlay's net gain. Joint minus raw is +0.1953 percentage points (paired 95% CI
[-0.3947, +0.7853], 21 gained / 17 lost); reservation helps the capped control by +0.4395 points
(CI [+0.0948, +0.7841], 11 gained / 2 lost). The large gain belongs to teacher pair scoring;
a general need for a communication layer is not established.

The old label export contained **279,999 post-terminal rows out of 298,318**. Its replacement has
**18,319 active decisions**; `raw_score` means uncapped teacher score, not allocation credit.
Controls reproduce every active golden label and terminal winner, with 72 active no-legal-cast
fallbacks. The NPZ contains no observation tensors; replay is required. Old evidence is preserved.

Next: trace actual NN arrays on active spell-target disagreements, author numerical WSP edits, and
test terminal outcomes against the NN and scoring references on genuinely unused boards. No new
weights, installed policy or live behavior are claimed. Evidence:
`dofus-evidence/models/allocation_controls_s317_433_v1.json`; full hashes and paired intervals in
`ARCH_REASSESSMENT.md` §XII.16c. #151 and #152 are complete; #150 and the weight objective remain open.

## 7. The 28<!--f:tools--> tools

Runtime order is `data/capabilities.json` (`_registry or _BUILTIN_TOOLS` in
`agent/schema.py`):

```
knowledge.search  nav.goto    nav.follow   quest.list    quest.advance
farm.start        farm.stop   fight.join   fight.pass    inv.list
inv.sell          inv.equip   hdv.scan     hdv.list      hdv.buy
hdv.prices        tap.interactive          chat.say      state.snapshot
safe.stop         knowledge.remember       knowledge.forget   fight.state
fight.plan        fight.estimate           ui.tap        ui.icons    shop.buy
```

The 2026-10-27 research unlock added `hdv.buy`, `inv.equip`, `hdv.prices` and
`tap.interactive`; the old `FORBIDDEN_TOOLS` set is empty. `hdv.prices` is read-only;
`hdv.buy`, `inv.equip` and `tap.interactive` are directive actions. `ui.tap`, `ui.icons`
and `shop.buy` came with the tutorial work (2026-10-28) and are the reason this section
said 25 for two days. Older docs that say "16 tools", "18 tools", "21 tools" or "25 tools"
are stale; the schema is **28**, and `agent/regression.py:208` asserts both the schema and
`capabilities.json` at that number.

---

## 8. Always-on AI and dynamic instance count (2026-10-28)

Automatic per-client startup is implemented but not yet verified in the UI. The
Python planner shares its quest graph; full live-daemon scaling remains unmeasured.

| part | cost / behaviour | measured by |
|---|---|---|
| quest graph | loaded **once** per process, read-only by convention | tracemalloc: 40 planners = 22.1 MB total; earlier 5 independent planners = 109.1 MB. Not live daemon RSS |
| brain daemon | one process, one thread per instance, no fixed cap | 40 immediately returning fake pilots via direct calls, 40 statuses, one `brain.lock`; not concurrent live-client or HTTP load proof |
| per-instance state | sub-goal stack < 2 KB, own log + stop file | `test_brain_scaling_40`, `test_plan_stack` |
| always-on | `runtime.ini auto_ai=1` (default on): each launch and each client already running at startup is adopted; a manual Stop wins until the next launch | `test_always_on_ai` (source gate), C# Roslyn compile |

`runtime.ini` gains `auto_ai` (Settings → Runtime → "Always-on AI: drive every
launched client automatically"). Adoption calls `StartInstance` for that client,
not the whole farm; the loop's own `_require_world` wait makes starting at the
login screen safe. The number of clients is whatever the grid has — `+ Add`
allocates `dofus-01..dofus-999`, and the relay port is `base_port + slot`.

**Daemon lifecycle, verified offline.** Stop now signals workers without joining
under their shared lock; the old implementation could wait two seconds per client
while blocking worker cleanup. Eight behavioural tests cover scoped/idempotent
Stop, cleanup after success or failure, duplicate starts, and real loopback HTTP with 1, 7 and 43 concurrent
fake workers. These are not live-client load or memory measurements.

**Still open in the host.** Desktop review found pending-start/Stop races, UI-thread
violations and blocking network calls, non-atomic daemon startup, per-client process
fallback on ambiguous failures, and one-shot adoption. Always-on operation is not
yet verified end-to-end. Each WebView2 window also has its own Chromium renderer;
shared Python resources do not establish that 40 live windows fit the host.

**Collection and rebuilds.** `tools/collect_live_data.py` now monitors the changing
fleet read-only by default, with no default instance or deadline. Explicit cohorts
are pinned to their returned session start times; timeout/Ctrl-C never stop any
client. Rebuild is opt-in after successful finite completion. Its 49 fixture tests
pass; this is not a live-run proof.

`tools/build_entity_pointer_dataset.py --check` scans all per-instance trajectory
logs, excludes non-live/unattributed steps, deduplicates copied rows, keeps instance
streams separate, and requires explicit before/outcome snapshots. It refuses to
replace a dataset below 2,000 action rows, 50 progress-labelled action rows or 10
rows for any legal tool. Measured 2026-10-28: **4,018 action pairs, 34 progress-labelled
action rows, 20 undercovered tools**; the four new tools still have zero rows.
Thirteen tests cover rejection and output preservation. No dataset was rebuilt
and no training or promotion followed this check.

**Constraint corpus.** `python -m agent.constraint_corpus --generate --out
data/constraint_synthetic.jsonl` writes **2,600** rows (25 tools — the registry was 25 when that corpus was cut; it is 28 now and
`regression.py:509` requires ≥50 rows for each of the 28 — × 8 causes × 13
variants). The controller routes **100% cause / 100% action** on these taxonomy
probes; this does not establish that their tool/precondition combinations can
occur in the game. The earlier ensemble gate-pass claim is withdrawn: seed 7 has
50/400 validation inputs identical to fit inputs, despite different family names.
FP32 and INT8 reload at 0.855 accuracy / 0.0768 ECE; excluding duplicates gives
292/350 = **0.8343 accuracy**, not a passing independent benchmark. The trainer now
rejects overlapping families or model inputs before training/export. Existing
artifacts remain **unpromoted and unloaded**; the regression gate tests rejection,
not a claimed model-quality pass. Applicability-aware constraints, independent
held-out data and repair-outcome regret remain open.

## 9. Evidence directory: what is on disk right now (2026-11-06)

`C:/Users/sao/Documents/dofus-evidence/touch-data` is outside version control, so its state is a live
fact about the system rather than a detail of one run:

* `encounters_boss_mech_s4_2048.npz` **no longer exists in its original form**. It was overwritten by
  `tools/wsp_gate.py score` (that mode's positional argument is an output path) and cannot be
  regenerated: the corpus generator changed afterwards -- summon-only spell levels now survive
  `slot()`, summon rows are typed friendly, three new columns are written. Its `.json` meta survives,
  so its knobs are on record. Any claim that was computed only on that file is now a citation, not a
  re-runnable measurement; re-derive on `encounters_boss_swarm_hold_s4_2048.npz` or the rebuilt
  `encounters_boss_swarm_s4_2048.npz` (rebuilt from its own meta knobs; counters match the original
  build exactly: 892 summon slots, 1,360 bodies, 184 summoned slots with no damage line).
* Everything else in the directory loads: 13 other encounter corpora, `wsp_mech_labels.npz`, the
  touch tables. The new swarm corpora are `encounters_boss_swarm_s4_2048`,
  `encounters_boss_swarm_hold_s4_2048` (seed 99) and `encounters_party_swarm_s4_2048` (4 allies).
* The class of mistake is closed: `wsp_gate.py score` refuses a target that is not `.json` and refuses
  an existing file that does not start with `{`/`[`, before doing any work, with four arms pinned in
  `tools/test_wsp_gate_report_path.py`. The directory itself still has no backup, and that is the
  user's call -- one disk event takes the whole evidence base with it.
