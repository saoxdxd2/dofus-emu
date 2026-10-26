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
  cannot produce an illegal call.
* **encoder** — the 30M language encoder via `ai/encoder_serve.py`; `--model-dir`
  defaults to `models/encoder30_v15`.

---

## 5. Model inventory (what is actually loaded)

| artifact | size / shape | in the loop? | evidence |
|---|---|---|---|
| `models/tool_policy.onnx` + `.json` | 42 features → **21** tool logits, 49,365 params, val accuracy **0.8599** | yes, only under `--policy planner` | `planner_nn.py` argmax over 21 |
| `models/encoder30_v15/` | 24,874,131-param **text** encoder; `encoder_int8.onnx` 25.4 MB shipped | yes, under `--policy encoder` | `ai/encoder_serve.py` |
| `models/verifier_head.onnx` (+ `.json/.pt/_int8`) | 33-feature / 4-class | **CLI default `None`, but the GUI auto-passes it** (`aipanel.cs:705-708`) → loaded in a normal launch | `--verifier-model`; read-only tools never use it (`verifier.py:416`) |
| `combat/encounter_selfplay.pkl` | PPO/PBT actor for live fights | yes, inside `combat_ai_server` | `live_policy.py` default |
| `networkhealth_artifacts/model.pt` | RTT/loss forecaster weights | **absent** → Sentinel has no model and falls back to relay telemetry | load refused; see `KNOWN_GAPS.md` |

The verifier head is *wired by the shipped launcher* (not orphaned), but its
action-label accuracy is **unmeasured**, so it is only trusted for non-read-only
tools and abstains below 0.5 confidence — see `KNOWN_GAPS.md` G6.

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
