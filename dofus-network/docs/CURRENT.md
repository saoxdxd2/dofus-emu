# CURRENT — how the running system is wired, today

This document answers *"what do I type, what listens on what port, and what model
is actually in the loop?"* It is a map of the **running** system, not a plan.
Status and next moves live in `PLAN.md`; the reasoning behind decisions lives in
`PROJECT_LOG.md`. Every port, path and number below was read from the code on
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
  model argmax cannot produce an illegal call. The legal registry is 28 tools; the
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
ready; the release build is green (`setup.exe` **224,413,696 B = 214.0 MiB**, measured
2026-10-30 — the old 100.1 MB figure predates the standalone-WebView2 payload and the
recompile of the stale tracked `builder/Build.exe`). What actually blocks a live run today
is not the bridge alone: `tools/live_ready.py` reports **`FAIL egress`** (upstream
`127.0.0.1:40000` not listening under `mode=auto, fail_closed=1`), **`WARN client`**
(DofusFarm not running) and **`FAIL page build`** (last client `2026-10-29bf`, repo
`2026-10-30a`). No live session has used the hook yet. `tools/live_evidence.py`
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
  first `2026-10-30a` session is the first evidence. The wiring itself is pinned offline by
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
gate is one actionable fight on the current page build (`2026-10-30a`; `2026-10-29bf` is the
newest one actually recorded), then a side-swapped win. Note that fights are now won live
(§6 above) — what has not been seen is a board with positive AP reaching the grid ranker, so
the model is still validated only on synthetic distillation (`data/combat_grid_synth_v2`:
1,872 boards, 1,318 actionable, `live: false`).

---

## 7. The 28 tools

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
