# CURRENT — how the running system is wired, today

This document answers *"what do I type, what listens on what port, and what model
is actually in the loop?"* It is a map of the **running** system, not a plan.
Status and next moves live in `PLAN.md`; the reasoning behind decisions lives in
`PROJECT_LOG.md`. Every port, path and number below was read from the code on
2026-10-26; anything unmeasured says so.

---

## 1. Services and ports

Five processes cooperate. The host (WinForms, outside this repo) spawns the
Python ones on demand and the autopilot spawns the combat brain; each binds one
loopback port.

| service | entry point | port | job |
|---|---|---|---|
| **Autopilot** | `run_autopilot.py` → `agent.autopilot.main` | (client) | the autonomous loop: observe → choose → guard → execute → settle → verify → decide |
| **Agent HTTP** | `python -m agent.serve` | **8895** | stdlib HTTP front for the agent layer (observe / plan / recorder) |
| **Combat AI** | `combat/combat_ai_server.py` `run_server()`, started by the autopilot through `combat/brain_service.py` | **8891** | `POST /v1/combat/decide` — solver + learned ranker + live policy |
| **Sentinel** | `networkhealth/sentinel.py` `run_sentinel_service()` | **8890** | health/telemetry + `nav.goto` name→map resolution |
| **Directive bridge** | `agent/bridge.py` (host owns the listener) | **8893** (fallback) | `POST /directive` → client tool call; the *real* port is read from `%LOCALAPPDATA%\DofusFarm\bridge_port.txt` |

Facts about ports that have burned time before:

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

* **The bridge port is dynamic.** `DEFAULT_PORT = 8893` (`agent/bridge.py:21`) is
  only a fallback. The app publishes its chosen port to `bridge_port.txt` and the
  loop reads that first. On a multi-slot machine the live port has been observed
  at **8894** while 8893 was held by something else. Trust the file, not the
  constant.
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
3. **guard** — `validate_call()` (schema) + `FORBIDDEN_TOOLS` veto
   (`{hdv.buy}`, `autopilot.py:77`) + the kick-signature guard.
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
  `tool_policy.onnx` does `argmax` over 21 tool logits, but the deterministic
  planner still owns argument filling and the safety gates. A wrong model argmax
  cannot produce an illegal call. `--ensemble act|shadow|off` controls the
  23-leaf decision-tree stack over policy + foresight + K-means state context;
  the default `act` is honoured only when `models/tool_stack.json` carries a
  passed cross-validation gate (§9.3 of `AI_ARCHITECTURE.md`).
* **encoder** — the 30M language encoder via `ai/encoder_serve.py`; `--model-dir`
  defaults to `models/encoder30_v15`.

Training roadmap: `docs/TRAINING_PLAN.md` — one shared ≤30M total / ≤10M active brain trained on the harness first,
then hardcoded decisions replaced component by component (Kaggle GPU).

**Measured learned-vs-rule face-off (2026-10-27).** `python tools/policy_faceoff.py --seeds 30
--steps 300` (noise + travel cost): the learned tool policy **wins 21, ties 9, loses 0**, mean
**66.10 vs 64.87 quests (+1.23, ~1.9 %)**. At 600 steps over 20 seeds it wins 17, ties 3, loses 0
(+1.90 quests). The gain is real but small and **simulation-only**: the harness seeds the planner's
client-confirmed set with the curriculum because the symbolic observation does not report startable
quests, while the live G31 guard is stricter. The shipped default therefore remains `rule` until a
live benchmark justifies switching; the learned planner is a distillation of the same teacher, so it
cannot exceed it by much.

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
ready; the release build is green (setup 100.1 MB). The client bridge is the only blocker, so
no live session has used the hook yet. `tools/live_evidence.py`
turns that into one command: preflight, bounded autopilot with `--command-shadow auto`, then a
combined session/shadow/replay report plus the autopilot summary (`--report-only` re-renders an existing log). The packaged
copy was extracted from setup.exe and its report path verified end-to-end; a relative-log bug
was found and fixed (`resolve_log`). Remaining base failures are `all` for stop, a held-out
knowledge entity, and the French `d'équipe` channel span. See `docs/TRAINING_PLAN.md`.

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

Self-play training (`encounter/` ES + PBT) produces `live_policy` weights. **Live
combat is unverified against the real server** — `executor.py` records
`live_calibrated: false` and `models/fight_calibrator.json` is absent. Treat every
combat number as offline until a fight is proven against the client. (Do not count
offline tests as live combat proof.)

---

## 7. The 21 tools

Runtime order is `data/capabilities.json` (`_registry or _BUILTIN_TOOLS` in
`agent/schema.py`):

```
knowledge.search  nav.goto    nav.follow   quest.list    quest.advance
farm.start        farm.stop   fight.join   fight.pass    inv.list
inv.sell          hdv.scan    hdv.list     chat.say      state.snapshot
safe.stop         knowledge.remember  knowledge.forget   fight.state
fight.plan        fight.estimate
```

`hdv.buy` is **not** in the vocabulary — it is deliberately absent and additionally
vetoed at runtime by `FORBIDDEN_TOOLS` (`autopilot.py:77`, guard at `:700`).
Older docs that say "16 tools" or "18 tools" are stale; the schema is 21.
