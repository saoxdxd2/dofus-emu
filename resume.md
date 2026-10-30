# DofusFarm — Full Project Resume

**Snapshot date:** 2026-10-30 (source page build `2026-10-30a`; full-tree re-verification
pass — every countable claim in §3–§5, §7–§12, §13–§18 and §22 was re-measured today)
**Repository:** `C:\Users\sao\Documents\dofus-emu`
**HEAD at snapshot:** `c875cde` (plus an uncommitted working tree — see §14)
**Newest live rows:** 2026-10-29 18:50:38 UTC (step) / 19:04:57 UTC (capture), slot dofus-05
**Purpose of this file:** one document that explains what the project is, how every
piece works, what is proven, what is only offline, what is broken, and what to do
next. It is written so a new session (human or agent) can resume without reading
10,000 lines of docs first.

> Everything here is measured from the code, the docs, or the live logs unless it is
> explicitly labelled *unknown*, *offline*, or *prediction*. Where two documents
> disagree, the newest document wins; the known stale numbers are listed in §22.

---

## 0. TL;DR

DofusFarm runs **Dofus Touch** as the real Cordova/WebView2 client on Windows,
routes every packet through a local Go relay that owns a pinned egress, and drives
the game with a Python agent. The agent is a hybrid:

- a deterministic planner and rule loop that **acts** today,
- a learned command parser that is **shadow-only** (84.6% vs 53.8% exact on held-out
  phrasings, but it does not act),
- a learned tool policy / foresight ensemble that is **opt-in** (`--policy planner`),
- a combat solver plus a learned move head that is **executed live** (approach /
  retreat / hold) and a learned critic that ranks legal realizations when its inputs
  are complete.

The current measured reality on the live server (runs through 2026-10-29 18:50 UTC;
re-measured 2026-10-30, four slots with rows — 01, 02, 03, 05):

- the client logs in, stays connected (AFK keep-alive pins the client's own
  inactivity timer), captures its full quest catalogue, **finished the tutorial**, and
  completes objectives in the 4260/4262/4264 chains;
- **fights are no longer unproven**: `trajectories/fight_outcomes.jsonl` holds 22
  outcome rows, 9 with `won: true`, over distinct `fight_id` 124, 900, 1356, 1358,
  1359, 1482 — the newest (1482) is `started_by: "quest.advance"` at 2026-10-29
  17:37:33 UTC. dofus-05's autopilot log independently counts 52 fight calls, 23 named,
  10 ok.
- **equip is half-proven**: dofus-02's autopilot log shows `equip {refused: 76,
  uids: 41, unaddressed: 41, ok: 6}` — six real `inv.equip` successes, and the
  refusals are now read from the client's own error text (G64) instead of guessed.
- **no quest is proven finished end-to-end**: no `QuestCompletedMessage` row exists in
  any capture. `tools/session_report.py`'s `quests completed` column is a delta of the
  page's cumulative counter and the two log views disagree (+1/+5/+1 in the
  trajectory logs, +0 in the autopilot logs). Treat the counter as a hint, not proof.
- the most recent run ended on a **manual stop file**, not a kick: `brain_console.log`
  records `stopping: stop file present (AUTOPILOT_STOP.dofus-05)` with
  `{'advanced': 69, 'failed': 19, 'noop': 9}`; the host window closed at 22:38:02.
  Two permanent guest bans (`ui.popup.accessDenied`) are recorded earlier in the same
  log — the account-level risk is still the fleet's real ceiling.
- **no learned worker ships, and nothing actually vetoes yet.** The measured incumbent is
  the rule `repeat_streak_norm >= 2`, and four attempts to beat it failed their own
  pre-registered bars on 2026-10-29/30: the world-model encoder (G57), the frozen forest
  (G59), per-form count calibration at equal veto budget (G66), and a state-block second
  layer (G67). `agent/gate.py` runs that counter **shadow-only** in the live loop — each
  decision records what it would have refused, and the planner still owns the decision —
  because the replacement action is still unpriced (G39/G46/G52/G55). **The shadow has
  produced zero rows so far:** `agent/gate.py` landed 2026-10-30 and the newest logs
  (2026-10-29, 593 rows on dofus-05 + 454 on dofus-02) contain no `"gate"` key at all. Its
  wiring is now proven offline rather than at the live run
  (`test_gate_shadow_reaches_the_trajectory`, 10 checks), which corrected two things this
  document had loosely implied: the annotation lands in the **trajectory** rows the recorder
  writes (`recorder.py:386` persists `meta`) and **not** in the loop's own step rows, because
  `Step` has no `meta` field (`autopilot.py:258-292`) — so the `autopilot.dofus-XX.jsonl`
  view will never show it; and the gate's `VETO_AT = 2` is the loop's own no-progress call
  **one repeat earlier** than the mechanism already in the code (`streak >= 3` /
  `_recent[-3:]`, `autopilot.py:2078`), which is what makes it a replacement candidate rather
  than a new idea. The binding constraint is the volume of lived outcomes,
  not capacity.

The project's own rule is the right summary: **the harness is not an AI. The harness
supplies data and tools; the AI makes decisions.** Today the decision layer is mostly
still rules; the model work is being promoted one measured component at a time.

---

## 1. What this project is

A personal automation project for **Dofus Touch** (Ankama). It is not an emulator:
there is no Android, no APK, no QEMU in the default build. The real Cordova bundle is
served from disk under the virtual origin `https://dofus.local/`, Edge WebView2 renders
it, and an injected script disguises the browser as a phone and exposes a tool API.

Five things run in one process tree:

| # | thing | runs as | job |
|---|---|---|---|
| 1 | **Host** (`DofusFarm.exe`) | WinForms, one process | instance grid, AI panel, settings, shortcuts, logs |
| 2 | **Client** (WebView2) | one child window per instance | the real Dofus Touch bundle, served from disk |
| 3 | **Relay** (`dofus-net-proxy.exe`) | one child process per instance | HTTP/CONNECT proxy, asset cache, egress |
| 4 | **Brain** (`dofus-network/`) | Python, spawned on demand | planner, encoder policy, verifier, agent loop, combat brain |
| 5 | **Sentinel** (`networkhealth/sentinel.py`) | Python, port 8890 | health/telemetry + `nav.goto` name→map resolution |

---

## 2. Where it stands right now

### 2.1 Last measured live state (rows through 2026-10-29 18:50 UTC; re-measured 2026-10-30)

Four slots have in-world rows — 01, 02, 03, 05. Slots 04/06 stop at 2026-10-27 11:42 and
07–13 at 2026-10-22, so this is a four-client fleet, not a larger one.

`tools/session_report.py` gives two views of the same fleet because the counters live in
different files. The **autopilot view** (`%LOCALAPPDATA%\DofusFarm\autopilot.dofus-0X.jsonl`)
is the one that carries manual interventions and recoveries:

| instance | sessions | wall | steps | ok/fail | objective delta | quests completed | maps | manual | recoveries (retry/recover/replan) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| dofus-01 | 11 | 138.7 min | 848 | 802/46 | ±16 | 0 | 12 | **25** | 58/453/228 |
| dofus-02 | 12 | 212.6 min | 1,455 | 1,302/153 | ±11 | 0 | 22 | 16 | 62/321/214 |
| dofus-03 | 14 | 115.5 min | 523 | 474/49 | ±19 | 0 | 13 | 13 | 30/286/160 |
| dofus-05 | 21 | 201.6 min | 1,177 | 1,025/152 | ±6 | 0 | 37 | **140** | 80/325/199 |

The **trajectory view** (`dofus-network/trajectories/dofus-0X.jsonl`, the brain's own step
log) is the one that carries the proof counters:

| instance | sessions | wall | steps | ok/fail | objective delta | quests completed | maps | proofs (equip / buy / fight) |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| dofus-01 | 27 | 105.9 min | 891 | 889/2 | ±11 | **+1** | 29 | fight 2 calls / 2 ok (1 named) |
| dofus-02 | 4 | 32.9 min | 35 | 33/2 | ±14 | +0 | 2 | none |
| dofus-03 | 32 | 98.0 min | 556 | 550/6 | ±19 | **+5** | 30 | fight 2 / 2 ok |
| dofus-05 | 2 | 32.7 min | 300 | 300/0 | ±19 | **+1** | 12 | fight 4 / 4 ok |

How to read those two tables honestly:

- **`objective delta` is signed and is not "objectives completed by the agent".** It is the
  change in the page's cumulative `objectives_completed` counter across session boundaries,
  and a new chain resets that counter — hence `−11`, `−19` rows at level 8→13. The G53 work
  (the counter's own history) is the reason the project stopped trusting it. The hard proof
  is `tools/quest_witness.py` (attributed objective validations) and
  `trajectories/fight_outcomes.jsonl`.
- **`quests completed` disagrees between the views** (+1/+5/+1 vs +0) because the autopilot
  rows do not all carry the counter. Neither view is a completion *proof*: no
  `QuestCompletedMessage` exists in any capture (quest traffic is 111
  `QuestObjectiveValidatedMessage`, 72 `QuestStepValidatedMessage`, 6 `QuestsUpdatedMessage`).
- **The equip refusals are real and now explained.** dofus-02's autopilot view prints
  `equip {refused: 76, uids: 41, unaddressed: 41, ok: 6}` — six executed equips, and the
  76 refusals are the tutorial-gate/"already worn"/quest-object cases G62 settled offline.
- **Recover/replan still dominates the budget**: 1,385 recover + 801 replan + 230 retry rows
  against 4,003 steps across the four autopilot logs. Manual intervention is the other honest
  number — 194 in total, 140 of them on dofus-05. Full Auto is not yet zero-intervention.

Other readouts from the same logs:

- **Page build:** the newest source stamp is `2026-10-30a`
  (`scripts/mobile-disguise.js` line 31). The logs above ran `2026-10-29bf` (593 rows on
  dofus-05, 343 on its autopilot log), `2026-10-29aw/ax/ay` and `2026-10-28ah/x`, so the
  interactive-use walk-first fix (G69) is **not yet exercised live**.
  `tools/live_ready.py` now says the same thing (`last client recorded 2026-10-29bf`);
  until today it read **only** `trajectories/default.jsonl` — the pre-daemon single log —
  and therefore claimed the last client was on `2026-10-27s`, three builds and a day behind
  the instance log that actually spoke. Fixed as part of this pass: with no `--log` named,
  it reads the newest `*.jsonl` in that directory by mtime, and a named log is still read
  as itself.
- **Protocol footprint, measurable for the first time:** the 13
  `trajectories/play_dofus-*.jsonl` captures now report instead of crashing — **306
  sessions / 3,025.4 min (50.4 h)** of connection time against the 668.4 min (11.1 h) of
  driven autopilot wall time in the table above. By slot: dofus-05 140 sessions /
  1,054.1 min, dofus-03 59 / 1,151.8, dofus-02 49 / 397.8, dofus-01 38 / 415.1, dofus-06
  9 / 1.0, dofus-04 4 / 1.0, and slots 07–13 one session each of ≤3.1 min. Every one
  reports `steps 0`, which is honest — they are wire captures, not loop logs. The gap
  between 50.4 h connected and 11.1 h driven *is* the idle share the AFK keep-alive and
  guest-ban work has always been about, and it was invisible until today.
- **Knowledge / quest catalogue:** dofus-05 captured 1,522 quests / 21,624 objectives and
  dofus-01 1,750 / 38,320 into the client index, with **0 not in store and 0 collisions** on
  every slot. This is G29/G40's capture working live.
- **Identity:** the trajectory logs report `no bound/drift rows` (those step rows predate
  the guard), but the autopilot logs do carry it: dofus-01 **bound 15 / drift 0**
  (`Brruh, Bruuh`), dofus-02 **bound 23 / drift 1** (`Myaixd, Bruuh`), dofus-05
  **bound 38 / drift 0** (`Bruih`). One drift event on one slot is the live number G9's
  fail-closed binding has to explain, and the relogin proof is still owed.
- **Accept rows:** none in these logs, and `command_shadow` was never requested
  (`--command-shadow` absent from every run), so the parser still has no live shadow row.
- **Run tail / stop reason:** the last rows are a manual stop, not a wait and not a kick —
  `stopping: stop file present (AUTOPILOT_STOP.dofus-05)`, `done: … {'advanced': 69,
  'failed': 19, 'noop': 9}` (console log mtime 2026-10-29 18:12:45 local); root
  `AUTOPILOT_STOP` reads `window closed at 22:38:02`. The 2026-10-27 "policy had no action
  to take" tail that this section used to report is explained: G61/G63 showed the loop was
  bouncing the same map pair (216 of 229 changes were A→B→A on dofus-02) and G63 added the
  three-return park. Diagnosing it is closed; *proving* the fix live is not.
- **Instrument fixed during this pass:** `tools/session_report.py` used to raise
  `OSError: [Errno 22]` on `play_*.jsonl`, because protocol captures stamp `at` in epoch
  **milliseconds** and the reporter passed the raw value to `datetime.fromtimestamp`.
  `_ts()` now normalizes anything above `1e11`, so the captures holding the only successful
  interactive uses are reportable: `play_dofus-03.jsonl` reads **59 sessions / 1,151.8 min
  wall** and correctly says `steps 0` — it is a wire capture, not a loop log.

### 2.2 What is proven live

- The page loads, the client logs in, and the game runs.
- Directives reach the client and come back (the bridge is alive; the real port is read
  from `%LOCALAPPDATA%\DofusFarm\bridge_port.txt`, currently **8896**).
- `quest.advance` has produced verified progress (`advanced`), and a later six-step
  run advanced a quest through the NPC reply, changed maps, and moved a character
  from **level 1 / 3 objectives to level 2 / 6 objectives** with `present: true`
  keep-alive and no disconnect (AI_ARCHITECTURE §9.4).
- The AFK kick is the client's own timer and the keep-alive now reaches it:
  measured `marks 347 / age_ms 1 / inactive false / via setInterval-owner` after
  3 minutes on two driven clients (G1).
- The client version gate works: the updater moved the bundle from 1.74.9 to
  **1.74.10**, re-patched 23/23 rules (1/1 core), re-stamped asset URLs, and the guest
  press then reached the world (AI_ARCHITECTURE §11.1).
- Multi-instance addressing works: `target_instance=all` returned 4 envelopes on four
  open clients; comma lists and single ids behave as documented (§11.2).
- The asset cache serves ~99% of requests from disk (268 hits / 4 misses measured).
- The egress pin holds for a session; a session is one address.
- **A fight is proven end-to-end, six times over.** `trajectories/fight_outcomes.jsonl`
  holds 22 outcome rows, 9 with `won: true`, over distinct `fight_id` 124, 900, 1356,
  1358, 1359, 1482. `124` carries the server's own `GameFightEndMessage` with
  `started_by: fight.join`; **1482** (2026-10-29 17:37:33 UTC) is
  `started_by: quest.advance` — the quest loop walked into a fight and won it. The
  regression gate fails without such a row, so this proof is pinned, not folklore (G7).
- **The interactive-use mechanic behind the stuck quest objectives is proven.**
  `tools/quest_witness.py` over every capture reports `events 216,975 / accepted uses 39
  / validations 111 / attributed 3`, and the three attributed rows are 4262/**13948**
  (`use 513208 skill 184 from cell 345`, element at 330, distance 1, **+67 ms**) and
  4262/**13950** ×2 (`use 513212 … from cell 299`, element 285, **+255/+256 ms**) — each
  validated within a second of the server's `InteractiveUsedMessage` ack. That is G69:
  pages are earned by a use from an *adjacent* cell, never by a tap from across the map.
- **The client's own refusal text is read, not guessed** (G64/G65): a `logError` hook
  keeps the newest 5 refusal strings (200-char cap each), and a refused call is now
  scored as a *miss* in the outcome labels (`label_version: 3`).
- **The release build works again.** `builder/Build.exe` was a stale tracked binary that
  predated `build.cs` and omitted four sources (every release failed with CS0246 while
  the gate's C# compile passed). After recompiling it: release exits 0, 11 bundle
  patches, `DofusFarm.exe` 565,248 B, `uninstall.exe` 17,920 B, `setup.exe` 214.0 MB
  carrying a 213.8 MB standalone-WebView2 payload.
- **The test gate is green: `python -m agent.regression` 1,540 checks / 0 failed, exit
  0** (measured 2026-10-30 on this tree, after re-syncing the Kaggle mirror for
  `tools/quest_witness.py` — the one failure was toolkit drift).

### 2.3 What is proven only offline

- **Command parser**: 84.6% exact vs 53.8% rule on 26 held-out base phrasings
  (intent 96.2% vs 69.2%); per-row test intent 100% vs 56.5%, exact 65.2% vs 34.8%.
  Shadow only, default off; no live shadow session has run.
- **Tool policy**: 49,365 params, val 0.8599; paired simulator benchmark +1.25 quests
  at level 60 (11/12 seeds) and +1.50 at 800 steps (7/8). Simulation only; the live
  default is still `rule` at the CLI level (this machine's GUI is configured
  `policy=planner`, see §16).
- **Foresight**: 31,865 params; harness `gain_capture` 0.9679 vs teacher 0.9660 on the
  v2 corpus (stored artifact metrics on a later corpus: 0.9553 vs 0.9433); top-1
  0.8641; move AUC 0.7489; zero flats called promising. **Live transfer fails**
  (move AUC ≈0.5; artifact 0.336 on 223 rows; ECE 0.92; 100% of rows outside the
  harness range). Capacity sweep 31K → 4.4M params did not fix it; coverage is the
  bottleneck.
- **Tree ensemble**: 23 leaves, gain capture 0.97905 vs base 0.97734 (+0.00171),
  top-1 0.91137 vs 0.90591 (+0.00546), ahead in 3/5 folds. Harness-only; active only
  under `--policy planner --ensemble act`.
- **Combat**: solver, damage calculator, 50 solver tests, 1,980 spell levels
  validated, learned actor answers 18 of 64 own turns with a cast at p50 3.3 ms.
  Those *numbers* are offline; the fights themselves are now proven live (§2.2) —
  what is still unproven is damage calibration, because `fight_calibrator.json` is absent.
- **Verifier**: rules are the authority. The neural head is gated off unless a
  `verifier_head.validation.json` contract with live/human labels, n ≥ 60 and
  `above_baseline: true` exists. No such contract exists, so normal launches run
  rules-only.

### 2.4 What is not working / unproven

- **No quest is finished end-to-end under agent control.** Objectives validate (111
  captured validations, 3 attributed to an agent-visible cause), but zero
  `QuestCompletedMessage` rows exist in any capture. The last step of the chain — the
  claim/turn-in — is still the unproven one, and 4262/13952 "Talk to Roki" is where the
  frontier sits.
- **The fight sample is thin.** 9 won rows over 6 `fight_id`s, all from one session
  (`7a871278`). No boss, no sustained multi-fight sample, and the battle-log ring added
  in build `2026-10-28w` has still never been read out of a live end message — so the
  damage numbers of a won fight are not yet in the corpus.
- **Equip works only partly (G2/G62).** `inv.equip` has a shipped preset path and dofus-02
  recorded `equip {refused: 76, uids: 41, unaddressed: 41, ok: 6}` — six real successes —
  but direct-UID equip is refused by design until a live capture proves the path, and the
  tutorial equip wall (`18974` is a `QUEST_OBJECT`, `possible_positions: []`) is settled
  offline, not live.
- **No learned worker ships.** Four routes were measured and rejected against their own
  pre-registered bars: the world-model encoder (G57), the frozen forest ranker (G59),
  per-form count calibration at equal veto budget (G66), and a state-block second layer
  (G67). The incumbent `repeat_streak_norm >= 2` runs **shadow-only** in `agent/gate.py`:
  it annotates every decision with the veto it would have made and refuses nothing,
  because what the loop would do *instead* is still unpriced (G39/G46/G52/G55).
- **`2026-10-30a` has never run.** The walk-first `isoEngine.useInteractive` route, the
  3 s interactive settle, and the refusal-credit fix are all implemented and pinned by
  offline suites (G65/G68/G69), but no live row exists on that build. That single session
  is the highest-value missing evidence in the project.
- No vision or image model (G4).
- Network forecaster is dark: the Sentinel still imports torch, and
  `networkhealth_artifacts/model.pt` is absent (G5).
- Live fight calibrator absent (`models/fight_calibrator.json`), so `p_win` is null
  and unattended fights are refused by design.
- No live command-shadow session, so the parser cannot yet be promoted: every log in §2.1
  prints `command_shadow: no status/row`.
- Identity-by-account binding is shipped and does bind live (15/23/38 bound rows), but
  dofus-02 shows **1 drift** and a relogin proof is still owed (G9).
- **No instrument gap left in the reporting path:** the `OSError: [Errno 22]` that stopped
  `tools/session_report.py` from reading `play_*.jsonl` was fixed on 2026-10-30 (§2.1) and
  pinned by new checks in `test_session_report_census`.
- Test coverage is still split: `agent.regression` (**1,540 checks**, exit 0) + JS suites +
  Go relay tests + C# selftests. The combat suites, the simulator floor (119 quests /
  148 objectives, no stall), the 40-instance daemon control plane, the C# host compile and
  the checked-in Kaggle mirror are inside that gate; the C# bundle selftest and the full
  release build remain outside it (G8).

---

## 3. Architecture

### 3.1 Data flow

```
                    ┌──────────────────────────────────────────┐
                    │ DofusFarm.exe (WinForms)                 │
                    │  Instances | AI | Settings | Shortcuts   │
                    └───────┬───────────────┬──────────────────┘
                            │               │
         one per instance   │               │  spawns on demand
                ┌───────────▼──────┐   ┌────▼───────────────────┐
                │ WebView2ClientForm│   │ run_autopilot.py       │
                │  CoreWebView2     │   │  agent.autopilot       │
                │  + mobile-disguise│   │  agent.verifier        │
                └───────────┬──────┘   └────┬───────────────────┘
                            │ HTTP proxy    │ HTTP directives
                ┌───────────▼───────────────▼───────────────────┐
                │ dofus-net-proxy.exe  :8880+n                  │
                │  routing, asset cache, egress, fail-closed     │
                └───────────┬───────────────────────────────────┘
                            │
                ┌───────────▼───────────┐
                │ ProxySupervisor (C#)  │  picks + keeps egress
                └───────────┬───────────┘
                            │
          ┌─────────────────┼──────────────────┐
          │                 │                  │
  userspace WireGuard   HTTP/SOCKS proxy   (never) direct
  bin\warp\wireproxy    proxies.txt
```

(The `+ mobile-disguise` box is labelled by its window, but the injection is done in
`desktop/WebView2Backend.cs:121-125` — `WebView2ClientForm.cs` never mentions the file.
Anyone looking for the attach point should open the backend, not the form.)

### 3.2 Ports

| port | owner | purpose |
|---|---|---|
| `8880 + slot` | relay | HTTP/CONNECT proxy the client is pointed at |
| `8890` | Sentinel | health/telemetry HTTP service + `nav.goto` name→map |
| `8891` | Combat AI | `POST /v1/combat/decide` (solver → ranker → live policy) |
| `8893`…`8899` (fallback set) | directive bridge | `POST /directive` → client tool call; the real port is whatever `%LOCALAPPDATA%\DofusFarm\bridge_port.txt` says — **`8896` measured 2026-10-30**. `desktop/agentbridge.cs:39` walks `CANDIDATE_PORTS {8893…8899}` and writes the winner, so 8893 is only the first choice and may be taken by a stale relay or a second host run |
| `8895` | Brain daemon / agent HTTP | `run_brain.py` → `agent.serve --brain` — one process, per-instance Autopilot threads, `/brain/*` and `/agent/*` |
| `40000` | tunnel | local SOCKS5 (userspace WireGuard or system WARP) |
| `5554, 5556, …` | legacy Android | emulator console ports, derived as `5554 + 2*(slot-1)` (`farmengine.cs:1571`) — there is no literal `5556` in the source; only with `--include-legacy-emulator` |

**8892 is retired** (old ModernBERT stance server). Nothing should dial it.

### 3.3 Port facts that have burned time

- The host does not start the combat brain. Since 2026-10-27 it starts **one**
`run_brain.py` daemon on port 8895 instead of one `run_autopilot.py` per instance.
The daemon runs one `Autopilot` thread per selected instance, shares the Python
interpreter and process-wide models/knowledge, and exposes `POST /brain/start`,
`POST /brain/stop`, `GET /brain/status` and `GET /brain/health`. The old
per-instance child path remains as a fallback. See `BRAIN_DAEMON.md`.
- A bound port is not a working tunnel. `wireproxy` binds before the WireGuard
  handshake completes; probes must do a real SOCKS5/CONNECT.
- Loopback never goes through the machine proxy: `agent/bridge.py` builds a
  `ProxyHandler({})` opener. A 502 from loopback previously looked like a dead bridge.
- `run_autopilot.py` is a script, not `python -m agent.autopilot`, because the
  embedded Python's `_pth` cannot resolve `..\dofus-network` from the install root.

---

## 4. Repository map and size

```
/
├─ DofusFarm.exe               host (built, 565,248 B = 552 KiB)
├─ dofus-net-proxy.exe         relay (built, 8,152,064 B = 8.15 MB)
├─ setup.exe                   installer (224,413,696 B = 214.0 MiB, rebuilt 2026-10-30; embeds the standalone-WebView2 payload)
├─ uninstall.exe
├─ compile.bat                 one-click build
├─ network.ini                 egress configuration
├─ ai_settings.ini             AI panel state + shortcuts
├─ proxies.txt                 upstream proxy pool (public; unreliable)
├─ accounts.txt                optional login:password pairs (sensitive)
├─ manual-login.flag           disables auto-login / guest seeding
├─ no-client-update.flag       disables the bundle updater
├─ bundle-selftest.txt         last C# patch-equality result
├─ ai-status-selftest.txt      last AI-pill formatting result
├─ asset_cache/                relay cache (assets, .meta, proxy.log) — 108,623,908 B = 103.6 MiB
├─ builder/                    Build.cs → Build.exe (the whole pipeline)
├─ desktop/                    C# host sources — 23,117 lines
│  ├─ mainform.cs              shell, grid, nav, launch paths (4,132)
│  ├─ pages.cs                 Settings / Shortcuts / Logs (1,094)
│  ├─ aipanel.cs               AI page + AiSettings (2,061)
│  ├─ farmengine.cs            instances, relays, tunnels, proxies (3,143)
│  ├─ proxysupervisor.cs       egress selection + failover (878)
│  ├─ netsettings.cs           network.ini model (290)
│  ├─ clientbundle.cs          transactional updater + runtime patcher (548)
│  ├─ WebView2ClientForm.cs    the client window (3,437)
│  ├─ WebView2Backend.cs       instance lifecycle + the disguise injection (596)
│  ├─ agentbridge.cs           directive HTTP bridge (624)
│  ├─ prioritygovernor.cs      CPU/RAM arbitration (642)
│  ├─ watchdog.cs              hang detection (282)
│  ├─ hardware.cs              device profiles (218)
│  ├─ installer.cs             setup.exe logic (2,746)
│  ├─ uninstaller.cs           (530)
│  └─ client/www/              the served client bundle (14,907,452 B ≈ 14.2 MiB)
├─ scripts/
│  ├─ dofus-net-proxy.go       the relay (3,471 lines)
│  ├─ mobile-disguise.js       injected page agent (11,254 lines — the game adapter)
│  ├─ proxy_pool.js            verify/quarantine/scrape/write pool
│  ├─ harvest_proxies.js       append-only candidate harvester
│  └─ *.go, *.py               probes, proxy tools, data extraction
├─ tools/
│  ├─ patch-engine.js          build-time bundle patches
│  ├─ make-patch-rules.py      extracts them → patch-rules.json/.txt
│  ├─ apply-patch-rules.py     Python reference applier
│  ├─ apply-map-resilience.js  map-transition watchdog
│  ├─ test_*.js                36 page/JS suites (see §17)
│  └─ js-error-finder.js       bundle checker (currently red — G11)
├─ bin/warp/                   wireproxy, wgcf, warp-plus + configs
├─ bin/webview2/               Edge WebView2 bootstrapper
├─ runtimes/                   embedded Python 3.11 + win-x64/x86 helpers
├─ dofus-network/              the brain — 99,335 Python lines in 259 files (excluding the toolkit mirror)
│  ├─ agent/                   loop, planner, verifier, controller, tools, recorder
│  ├─ ai/                     training, serving, ONNX export, command model
│  ├─ combat/                 damage math, solver, live policy, duels, encounter
│  ├─ planning/               cost model, AND/OR planner, bandit, schedule
│  ├─ advisor/                read-only gear/spell/market advisor
│  ├─ knowledge/              canonical SQLite spine builder
│  ├─ navigation/             world/quest/resource graphs
│  ├─ networkhealth/          RTT/loss forecaster + Sentinel
│  ├─ sim/                    symbolic world + counterfactual corpora
│  ├─ pipeline/               THX-01 training/serving path (legacy-ish)
│  ├─ models/                 checkpoints + ONNX exports (488,651,611 B = 466.0 MiB)
│  ├─ data/                   corpora, cache, knowledge DB (347,640,557 B = 331.5 MiB)
│  ├─ trajectories/           29 JSONL logs at the top level (32 counting nested dirs), 459,787,953 B = 438.5 MiB
│  └─ docs/                   21 markdown documents (+9 JSON benchmark artefacts)
├─ client/www/                 older bundle tree (not served; see CLEANUP)
├─ dofus-emu-cleanup-2026-10-25/  historical cleanup snapshot
├─ kaggle/                     networkhealth + tpu_selfplay kernels
├─ trajectories/               live policy decisions + sim fight outcomes
└─ resume.md                   this document
```

**Line counts, re-measured 2026-10-30:** C# **24,033** (desktop 23,117 + `builder/build.cs`
916), Go/JS **24,332** (Go 4,946 + JS 19,386 across `scripts/` and `tools/`), Python
**99,335** in 259 files, docs **12,721**. The 2026-10-27 snapshot of this document claimed
23,064 / 13,516 / 76,699 / ~10,500 — every one of those was understated, the Python figure
by 22,636 lines, and part of the rest is this day's own work: the shadow, alternative and
promotion-bar tests plus the probe modes. Globs matter here: `dofus-network/kaggle/toolkit/` holds a further 82,360
lines in 187 files and is deliberately excluded (it is a mirror), and `__pycache__` is
excluded. The five files added by the 2026-10-29/30 programme are 1,021 lines
(`agent/gate.py` 63, `agent/state_features.py` 153, `tools/state_encoder_probe.py` 368,
`tools/train_veto_shadow.py` 202, `tools/quest_witness.py` 235), and the JS suite count is
**36** (`tools/test_*.js`).

**Disk, re-measured 2026-10-30:** data 331.5 MiB, trajectories 438.5 MiB, models 466.0 MiB,
asset cache 103.6 MiB, client bundle 14.2 MiB, installer **224,413,696 B (214.0 MiB)**.
The previous figures (289 / 257 / 350 / 82 / 15 MB and a 105.7 MB installer) are all stale —
trajectories nearly doubled because the `play_*.jsonl` protocol captures keep growing, and
the installer more than doubled when the payload started carrying a standalone WebView2.

---

## 5. Host application (C# WinForms)

### 5.1 Window shell

One window, top nav, five pages. Pages are built once and toggled, not recreated:
the grid keeps its selection and the AI panel keeps its process handle.

| page | contents |
|---|---|
| **Instances** | toolbar + instance grid |
| **AI** | mode, targets, dry-run, goals, quick actions, live output. **There is no policy combo and no step-budget field**: `PinDecisions()` forces `Policy = "planner"` and `Steps = 0` (`desktop/aipanel.cs:66-70`, called at `:361`), so the GUI always runs Full Auto on the planner while the CLI still takes `--policy` / `--steps` |
| **Settings** | login flags, network card, folders, AI reset |
| **Shortcuts** | one row per action; press a key to rebind |
| **Logs** | live tail of `DofusFarm.log` and `autopilot.jsonl` |

Pages refresh when opened (`ShowPage` → `ReloadState`), not on a hidden timer.

### 5.2 Instance model

The grid is **12 columns** and none of them is a port
(`mainform.cs:776-798`): STATUS, INSTANCE NAME, SLOT, HOST MEMORY, HOST CPU, DEVICE
PROFILE, NETWORK, then the row actions Edit / Clone / Reset / Delete. Each row has a
name, slot, memory/CPU, optional per-instance proxy, and a phone profile. The slot
number *i* (1-based, so `dofus-01` is *i* = 1) maps to:

```
relay port  = base_port (default 8880) + slot
sdk port    = 5554 + 2*(i-1)     (legacy Android only; there is no literal 5556)
profile dir = %LOCALAPPDATA%\DofusFarm\webview2_profiles\<instance>
```

(`farmengine.cs:1571`, `:2090-2092` derive the slot back as `(port-5554)/2`, and
`netsettings.cs:51-53` owns the relay base port.)

Accounts are claimed so two instances cannot use the same login.

### 5.3 Shortcuts

Virtual-key integers in `ai_settings.ini`, applied through `ProcessCmdKey` so they
work anywhere in the window. Defaults: `F5` start, `F6` stop, `F7` send order,
`F8` toggle dry run, `F12` emergency stop. A key can only mean one thing; the editor
refuses duplicates.

Since 2026-10-28 they are **also registered system-wide** with `RegisterHotKey`
(`mainform.cs:1011-1055`, `:1111`), so the keys work while the *game* window has focus,
not only the manager. Registration can partially fail — the live log shows
`[AI] shortcut emergency (F12) could not be registered` then
`4 global shortcut(s) registered`: five actions, four bound. Read that line if an
emergency stop appears to do nothing.

### 5.4 Live AI indicator

Visible from every page: a nav pill updated once a second and a green AI nav button.
The pill has **four** shapes, not three: `AI running - step 42 - hdv.list advanced
(model)`, `AI idle`, `AI stopped - last step 42`, and **`AI running - waiting for the
first step`** (`aipanel.cs:1859-1883`). Green now means *the daemon reports a running loop
for some instance* — `IsRunning` is `BrainAnyRunning()` **or** the child process, so the
process handle alone no longer decides the colour (`mainform.cs:1315-1327`). With several
slots live the pill is an aggregate from `AggregateAiStatus()`:
`N running: dofus-01: … | dofus-02: …`. The text still comes from the last
`autopilot.<instance>.jsonl` line, and per-instance state is real
(`IsRunningFor` / `RunningInstances` / `PushAiState` / `LiveStatusFor` / `StepLogFor`,
`mainform.cs:1207-1300`, `aipanel.cs:1085-1145`) — each window's badge reflects that
window's own loop, not a farm-wide flag.

The AI page streams the loop's stdout line by line (`PYTHONUNBUFFERED=1`).

### 5.5 Logging

- `DofusFarm.log` — one line per event, `[HH:mm:ss] message`; rotated at **64 MB**
  (`LogMaxBytes = 64L*1024*1024`, `mainform.cs:269`, enforced at `:2104` — it was 10 MB
  when this document last said 10 MB).
- `asset_cache/proxy.log` — relay log.
- `%LOCALAPPDATA%\DofusFarm\autopilot.jsonl` — one JSON object per agent step.
- `%LOCALAPPDATA%\DofusFarm\autopilot.<instance>.jsonl` — per-instance loop logs
  (two loops on one JSONL interleave two characters).
- `%LOCALAPPDATA%\DofusFarm\verdicts_human.jsonl` — human corrections. **The host does not
  write this**: no C# source touches it, it is `dofus-network/agent/verifier.py:46`.
- Startup always logs one line answering "why is nothing loading":
  `[NET] egress: auto: supervisor picks (see Settings > Test)  |  relay 8880+slot  |  fail closed`
  (`netsettings.cs:257-264` builds it).

### 5.6 Process hygiene

- Single instance via `Global\DofusFarmManager_SingleInstance_Mutex_2026`; a second
  launch focuses the first.
- `--bundle-selftest` runs headless, writes `bundle-selftest.txt`, exits **0 (identical),
  1 (mismatch), 2 (SKIP) or 3 (ERROR)** — `desktop/Program.cs:128-156`. Treating anything
  but 0/1 as "the test failed" hides a skip; check the file's `RESULT:` line.
- `--ai-status-selftest` runs headless, writes `ai-status-selftest.txt`.
- `Activate()` is **one-shot** (`WindowState = Normal; BringToFront(); Activate(); Focus()`,
  `WebView2Backend.cs:81-85`) — there is no retry loop anywhere in the host, so the
  "minimized WebView2 reports no window" bug this document claimed was fixed with a retry
  is not fixed by a retry. If a click verification fails again, the missing piece is a
  wait-and-retry around activation, not a doc note.

### 5.7 Multi-instance addressing (2026-10-27)

`desktop/agentbridge.cs` resolves `target_instance` as one id, a comma-separated
list, or `all` (actually-open windows from `GetActiveInstanceIds()`). `RunOne(...)`
answers per instance: one addressee keeps the old envelope; several get
`{"targets": "...", "results": [...]}`. The stale-name fallback ("dofus-05 is not
active; using dofus-01") is limited to a single addressee.

`desktop/aipanel.cs` selects through a checklist (`TargetSlots`, persisted as
`target_slots=1,3`; "All instances" is exclusive). The start path is now **the daemon
first**: `aipanel.cs:345-352,1505-1543` drives one `run_brain.py` process on
`BrainPort = 8895` through `/brain/start`, `/brain/stop`, `/brain/status`,
`/brain/health`, and the per-instance `run_autopilot.py` child survives only as the
**fallback** (`:882`, the comment at `:1426` explains why it is kept). Each instance still
gets its own log, and idle training and the UI flip wait for the last child, not the
first.

Measured live: `target_instance=all` → 4 envelopes; `dofus-01,dofus-02` → 2;
`target_slot=3` → the old single shape, unchanged.

### 5.8 Instance reset (2026-10-28)

The manager grid has a **Reset** button per row (and the same action in the row
menu). It exists because a banned guest cannot be repaired: the identity is gone
at the server, so the slot has to be rebuilt from zero.

`Reset` → stop that instance's AI loop → stop the client and wait for the
WebView2 form to be disposed → `ForceDeleteDirectory` on
`%LOCALAPPDATA%\DofusFarm\webview2_profiles\<slot>` → verify the directory is
actually gone → create it empty and write `force_fresh_identity.flag` (the same
guard the account-collision path uses, so `accounts.txt` can never seed a saved
`[GUEST]` entry into the fresh profile) → relaunch through the normal
`StartWebView2Instance` path.

A wipe that does not finish (WebView2 still holds `EBWebView` locked) aborts
without relaunching, so a "fresh" guest can never start on top of old state.
The installer already provisions each slot as an empty profile directory with no
template copy, which is the same invariant: installation never transfers data.

### 5.9 Quest interactive elements, the fight witness, and the "AI kicks me" chain (2026-10-28)

**Interactive elements live on the renderer, not on `isoEngine`.** The page's
enumerator read `isoEngine.interactiveElements` (and `mapData.interactiveElements`)
and returned `[]` on every map, so `tap.interactive` answered
"no visible interactive element" for every free-form quest objective. The live client
keeps them at `isoEngine.mapRenderer.interactiveElements[elemId]`
(`{elementId, elementTypeId, enabledSkills, disabledSkills}`) with the cell at
`isoEngine.mapRenderer.identifiedElements[elemId].position`. The reader now prefers
that store and carries `enabled_skills`/`disabled_skills`, and `tap.interactive` uses
the client's own `isoEngine.useInteractive(elemId, skillUid)` — which orients, walks
to the element when it is not adjacent, and only then sends
`InteractiveUseRequestMessage`. Sending the message from a distance was a silent
server-side refusal.

Measured: map 145490432 holds exactly one element (id 513208, cell 330, skill 184
"Use", job Base). The *claim* (`quest.advance {quest_id, objective_id}`) is what
completed 13948 and then 13950 in the 18:13–18:24 window of
`trajectories/dofus-03.jsonl`; the interactive reader was fixed later and has not yet
been exercised live. The client's own step list is captured in
`trajectories/quest_state.dofus-03.jsonl` (`13948 done: true`, `13950 done: true`,
current `13952` "Talk to Roki").

**The fight witness closed.** `dofus-network/trajectories/fight_outcomes.jsonl` now
holds a row with `started_by: "fight.join"`, `fight_id: 124`, `won: true`, and the
page's `GameFightEndMessage` in `end` (`won: true`, `fight_id: 124`,
`results: [{id: 4434090, alive: true}, {id: -1, alive: false}]`, duration 15,965 ms).
`trajectories/` is gitignored runtime data, so that row is also kept verbatim in
`dofus-network/data/live_evidence/fight_outcomes.jsonl`;
`agent.regression` has `test_fight_end_witness`, which fails until such a row exists in
either file (and skips only in the Kaggle code mirror, which has no live logs).

Two bugs surfaced while producing it:
`FightOutcomeLog.start` read `fight_id` from the pre-join observation, where the
client still holds the *previous* fight's id — the second fight wrote 124 into a row
whose end message said 125. It now reads the id only from an in-fight observation.
And the rule policy answered "join the fight" with a targetless `fight.join {}`, which
the advisor's guard refuses on any map with more than one group (every map on this
route). `_join_args` now names the first group the observation's `fight_estimates`
marks `allowed`, keeping the refusal for the genuinely ambiguous case.

**Why the client appeared to "kick" when the AI was used.** Three separate effects,
none of them a ban:
1. The page's 20 s keep-alive is gated on the host's `aiRunning` flag. With no AI loop
   running, the session marks no activity and the server sends "Prolonged inactivity
   will lead to automatic disconnection" — observed in the host log as
   `AI_DISCONNECT reason=INACTIVITY`, ~4 minutes after the last directive.
2. `mobile-disguise.js` reloads the page when the viewport drifts ≥8% from its boot
   size *before* the world is reached. Resizing/maximizing the window at the login
   screen therefore reloads the login page (budget: 2 per 2 minutes), and the reload
   never touches the game session — it only makes it look like a kick.
3. The login page retries guest login up to five times; a retry while the old session
   is still alive is answered "You have just disconnected a character that was already
   using that account", and that *does* end the old session. Answer: one client, one
   login, and keep the AI loop (or a heartbeat) running while the character is idle.

**Challenge and battle-log capture (2026-10-28).** The client already receives everything
the AI was missing and the page discarded it:
- `fm.fightMessagesStack` holds the fight's action messages, and
  `GameActionFightLifePointsLostMessage` carries the server's own `loss` and
  `permanentDamages`, plus `shieldLoss`, AP/MP deltas, spell casts and deaths. The
  page's `last_actions` kept spell/source/target and dropped the amounts; `fight.state`
  now also carries `battle_log` (the whole stack, capped at 240 entries) with those
  fields, and the snapshot carries `battle_log_tail` (12).
- `ChallengeInfoMessage` / `ChallengeResultMessage` describe the optional fight
  challenges (`challengeId`, `_name`, `_description`, `targetId`, `dropBonus`,
  `xpBonus`, `_points`, `success`). Nothing read them; the page now keeps them in
  `window.__agentChallenges`, clears them on `GameFightStartMessage`, and exposes
  `challenges` in `fight.state`, in the snapshot, and in `__agentLastFightEnd`.
- The outcome row now carries `challenges`, `challenges_done`, `challenge_bonus` and
  `reward`: `reward = won + 0.25 × completed`, with a miss worth exactly nothing.
  The bonus is only credited from a fresh, same-fight end message.

**Corpora from the new live rows.** `data/combat_grid` was rebuilt from the 171 live
boards: exactly 1 actionable board (158 captured with no positive AP, 6 not on the
player's turn, 6 pass-only), manifest `verdict: actionable`. The entity-pointer
rebuild was attempted and **refused by its own coverage gate** (4,091 action rows but
42 progress rows against a floor of 50, and 18 tools with zero live rows), so the
dataset on disk is unchanged by design. Live coverage, not the builder, is now the
blocker for that corpus.

### 5.10 Free-form taps, the executed move head, and fast-fail guards (2026-10-28)

**Free-form objectives now have an action.** `QuestObjectiveFreeForm` (type 0) fell
into the kill branch; `declares_monster_target` correctly says the parameter is text,
and the planner then returned travel or nothing. The claim works when the server
accepts it (13948/13950 above), but when it does not, nothing performed the objective.
`QuestPlanner._free_form_calls` now offers the current map's interactive elements one
at a time (`tap.interactive`, with the element's own `enabled_skills[0]` when the
renderer has no `skillInstanceUid` yet), remembers `(map, element)` per objective so
the same one is not tapped forever, and leaves travel/talk untouched for every other
type. `parameter0` is a text id (704145 against element 513208), so it is only used as
a preference when it happens to equal an `element_type_id` — never as an id lookup.
`tap.interactive` is also in `SETTLE_TOOLS`: it answers ok before the walk to the
element starts, and observing immediately would score it `noop` (the same failure the
settle window was built for on `quest.advance`).

**A claim is blamed only for a close the server announced.** The page recorded a
"kick signature" (quest/objective pair in `localStorage`) for any disconnect within
3 s of a claim, and the recorder fired for *every* client-side close including
`INACTIVITY`. That permanently retired 13950/4262 minutes after the server had
accepted it. Now: only `serverDisconnecting` sets `__agentServerClosing` and only that
flag makes the disconnect wrapper record a signature; an accepted
`QuestObjectiveValidatedMessage` clears the pair (`__agentClearKick`), and signatures
expire after 24 h. A genuine rejection is re-learned with one disconnect.

**The trained move head is executed.** The encounter env applies the actor's move
(0 toward, 1 hold, 2 away) after every cast; the live `_realize` dropped it and ended
the turn in `policy_pass` once AP ran out — the page's local heuristic then walked
forward, which is the "it just fights forward" report. `LivePolicy` now emits a real
`move` (a path on the cell graph, MP-bounded, strict distance improvement) for
`pass + move 0/2`, for an unreachable cast with `move == 2` (retreat instead of
walking into range), and when no legal plan exists. When several plans realize the
chosen spell/target, the trained critic (`plan_value.rank_plans`, P(win) over the
plan's resulting board) now picks between them instead of cell distance — but only
when the observation contract says `value_ready` (the enemy's own spells are loaded);
otherwise the old choice stands. Tests in
`combat/test_live_policy.py` pin approach, retreat, hold, zero MP, the
retreat-over-approach case, and the critic's three gates (not ready, ready, ranker
failure).

Offline replay of all 171 recorded boards through the updated policy: 164 rejected by
the observation contract (placement / other team's turn), and of the 7 actionable
decisions 1 `policy_direct` cast, 3 `policy_retreat` moves (all with the trained move
head choosing "away"), 2 `policy_pass`, 1 `no_legal_plan`. The critic was value-ready
on 2 of the 7 and disagreed with the distance tiebreak on one, so the wiring is live
but only where its inputs are complete.

**Routing and guards.**
- `rule_parse("do all the quests")` labelled `quest/list` while the router and the
  learned parser said run. The rule now answers a list only for a read ("list my
  quests", the quest log, a named area) and otherwise falls through to
  `intent=quest, action=run`, so the layer that labels prompts agrees with the layer
  that routes them.
- `ToolExecutor` has a fast-fail circuit: two consecutive page stalls ("timed out",
  "not ready", no client instance) open it for 15 s, during which calls return
  immediately instead of paying the C# 10 s wait; one answered probe closes it. A
  stale instance name while other windows are alive does **not** open it.
- `combat_ai_server` decision rows now keep the fight's `challenges`, so a later
  policy can be taught to respect one.
- The planner's own `_server_counted_types` was `{1,4,5,6}` while the page refuses a
  claim for every class in its `SERVER_TYPES` list; it now mirrors the page
  (`{1,2,3,4,5,6,12,14,16,17}`), so a delivery or multi-fight objective no longer
  spends one step on a claim the client will refuse.

**Damage calibration harness.** `tools/damage_calibration.py` joins the solver's
predicted `slots[].damage` (decision log) with the server's own `loss` numbers
(`end.battle_log`) by the Dofus `actionId` that a cast and its loss messages share;
unpaired casts are reported, not dropped, and fewer than five pairs reads
`not_enough_casts`. `agent.regression` proves the join on a synthetic fight; with no
live battle log yet the live report says so honestly. It becomes evidence on the
first fight the page records.

**Measured verdict on training (2026-10-28, corrected).** No promotable training is
possible from the data on disk: entity-pointer coverage is 4,091 action / 42 progress
rows (floor 50) with 18 tools at zero and `tap.interactive` at 1, and only 7 of the 171
recorded combat boards pass the observation contract. Those 7 come from the two most
recent fights (ids 124/125); the other 156 boards predate the fighter-stats fix
(`enemy_max_hp_missing` fires 445 times across them) and can never train anything. The
synthetic solver-distillation corpus (`data/combat_grid_synth_v2`, 1,318 rows from 156
boards) trains the grid model the live brain does not use
(`fight.plan --ranker grid` only). The unblock is live fights on the fixed build, not
another Kaggle run.

### 5.11 The login block, and a PCA signature of a board (2026-10-28)

**"Server busy" is a login block, and the host was feeding it.** After a stop/relaunch
of dofus-03 at 20:20 the first login answered `en?[ui.popup.accessDenied]` (dismissible,
login then succeeded). The next stop/relaunch at 20:25 produced `dofus.local says:
Server busy`, and it stayed for more than 25 minutes: it re-appeared on a page reload at
20:33 and again on a single `session.login` press at 20:44. Two properties of that alert
matter for the agent:

1. A native `alert()` blocks the page's JS thread. Every directive to that page timed
   out while the dialog was on screen (`state.snapshot -> timed out`), so the agent could
   not even read the state it was in.
2. The host's own guest seeder (`WebView2ClientForm.cs`) presses
   `loginScreen.playWithGuest()` up to five times and reloads the document after 60 s
   with no progress. Inside the block every one of those presses is one more attempt,
   and the reload is a new login page load. The manager log shows the chain ending with
   `[AgentGuard] Server busy persisted through 3 retries; letting the client show its
   alert`.

The page now owns a login cooldown (`_LOGIN_BUSY_STORE` = `dofus.agent.loginBusy.v1`,
10 minutes): `window.alert` is captured before the game's scripts run, a matching
message (`server busy`, `accessDenied`, `too many`, `try again later`) arms the cooldown
and is **swallowed** (recorded in the snapshot as `login_cooldown`, logged, not shown), so
the page stays responsive; `session.login` and `debug.reload` refuse inside the window
unless called with `force:true`; and the login screen's own guest methods
(`playWithGuest`, `_createGuest`, `_continueGuest`, `_continueGuestFollow`) are wrapped
once the screen object exists and re-wrapped if it is replaced, so the host seeder and the
game's own retry hit the same rule. `tools/test_session_login.js` runs the extracted
handler against a stub cooldown (refuse, remaining seconds, `force:true`, inspect
allowed) and the gate pins the wrap and the swallow.

**Why the block kept coming back, and the fix in build `2026-10-28z`.** Restarting
dofus-03 at 21:33 showed the loop live: the cooldown was armed at 21:33:55 with 586 s
left, and at 21:36:00 it had been **re-armed to a fresh 596 s**. The host's seeder presses
`playWithGuest()` 800 ms after every document, while the page's wrap poll only ran every
500 ms and needs `gui.loginScreen` to exist; a press that slipped through before the wrap
re-armed the lockout, and the seeder's 60 s no-progress reload started the cycle again
(a reload re-runs the seeder, and the guard's old refusal path reset `dfGuestTries` to 0,
restoring the five-attempt budget the seeder is supposed to exhaust). Build z closes it
four ways: a second "busy" alert inside an active window **does not extend** it; arming
spends the host budget (`dfGuestTries = 5`) instead of resetting it; the wrap poll runs at
150 ms; and `window.location.reload` is guarded so the host's no-progress reload is
blocked while the window is open (`debug.reload force:true` sets `__agentReloadForced`
to bypass once). On expiry the stored alert is cleared and the budget is restored to 0.
Build ab adds a 20 s **single flight** on the initial guest presses (`playWithGuest`,
`_createGuest`, `_continueGuest`; `_continueGuestFollow` is a follow-up and stays
unthrottled), so the host seeder and an auto-started AI loop cannot press inside one
server round trip even before any cooldown exists -- that overlap is the remaining way
to earn a "Server busy" from a healthy server.

**The other popup is DOM, not a JS alert (build ac).** Closing a client window and
relaunching it twice inside five minutes (21:31-21:39) put both dofus-03 and dofus-05 on
`en[?ui.popup.accessDenied]` / "The Dofus access has been revoked". That popup is the
game's own window, so `window.alert` never sees it, the cooldown stayed unarmed, and an
auto-started AI could keep pressing into the lockout -- exactly the "server busy keeps
coming back when it auto-starts the AI" report. The page now scans **visible** text
(`document.body.innerText`, so hidden templates cannot false-positive) on a MutationObserver
plus a 5 s tick, throttled to one scan per 5 s, and routes `access has been revoked` /
`accessDenied` / `server busy` through the same cooldown and budget rules. Recovery that
afternoon: dofus-03 reached the world on ab by itself; dofus-05 needed one trusted click
on CONTINUE AS A GUEST (21:47) after its AI loop was paused with the project's own
`AUTOPILOT_STOP.dofus-05` file (removed once it was in the world).

**A fresh boot can throw inside `playWithGuest` (build ae).** The first press after the
22:06 restart failed with `TypeError: Cannot read properties of null (reading 'error')`
in the client's own login manager, and the host seeder presses only once per document, so
the instance would have waited for its 60 s no-progress reload. The wrapped guest methods
now retry a throwing press once after 2.5 s (still refusing inside a cooldown) and record
that retry as the single-flight press. The restart also earned a real "Server busy" (the
previous session was still warm): the page armed the cooldown on its own, spent the host
budget and held the window steady for the full ten minutes -- the guard working on a
fresh document rather than a stale one.

**A refused guest is not retried forever (build af).** The retry does not help when the
server itself refuses: at 23:17:44, the moment the 10-minute window expired, the press
threw again and the "Server busy" alert armed a **fresh** 10 minutes (581 s at 23:19:04).
One refusal per window forever is still a storm, so the page keeps a persisted fail
budget: three refused attempts inside 30 minutes (`playWithGuest` throw or busy alert)
arm a **30-minute** window whose reason reads `login exhausted after N refused guest
attempts`, and a login that reaches the world clears both the fails and the window
(`dofus.agent.loginFails.v1` / `dofus.agent.loginBusy.v1`). The live consequence is a
policy, not just a guard: after a refused press, stop pressing for much longer than ten
minutes; repeated presses extend the server's own lockout. `tools/test_login_guard.js`
pins the budget (two failures are free, the third backs off, the host budget is spent,
success clears it).
Measured live on build z: the cooldown `at` stayed `1793219809288` across 21:37:54,
21:39:09 and 21:40:24 (536 -> 461 -> 386 s), where the old build had re-armed twice in
the same span. `tools/test_login_guard.js` (new, in the gate) runs the guard block against
fake storage and a fake location: no extension, budget spent, reload blocked/forced,
expiry restores, and non-login alerts keep the native behaviour. Gate: **1,038 checks,
0 failed**.

**Ten minutes is too short, and the page was re-arming itself (build ag).** The cooldown
that armed at 22:28:46 expired at 22:38:46; at **22:39:28, with no press from anyone**, a
fresh ten-minute window was armed. The source was the agent's own popup scan: a WebView2
window in the background runs its 5 s tick about once a minute, the refused "Server busy"
text was still in `document.body.innerText`, and the scan matched it again. The server's own
block also outlasts our window: a single `session.login {"target":"guest"}` at **23:50:48**
(nothing pressed for ~70 minutes) logged in on the first press as Bruih (id 4434148, map
147064834), where the press at +10 minutes had been refused. Build ag therefore identifies
the popup instead of matching words: the matched text (digits collapsed) is stored in
`dofus.agent.loginPopup.v1`, the same signature never re-arms, a real press clears it, and it
is cleared when the words leave the screen. The quiet is derived from the stored refusal
times (`lastFail + backoff(n)`), not from the armed record, so a reload cannot shorten it:
**10 min, then 60 min, then 3 h**. A local `playWithGuest` throw no longer counts as a server
refusal. `tools/test_login_guard.js` pins all of it (stale popup, press clears it, a popup
that returns is new, escalation, reload-derived quiet). Gate: **1,056 checks, 0 failed**.

**A free-form step can be a person, not an element (live, 23:56-23:59).** Step 4974 of
quest 3841 is "Go to Albuera with Ayin Spyr": a free-form objective, no monster, one
interactive already spent, three NPCs on the map, and the planner answered
`state.snapshot` for 600+ waits ("the policy had no action to take"). Talking to the
nearest NPC (3888) opened Ayin Spyr's own dialog; five replies (introduce, listen, "go and
equip your new items", talk again, "one last gift") took `objectives_completed` 13 -> 15 and
the step to 4976 ("Get the Albuera Elite Bonus Pack"). The three equippable pieces of the
granted set went on with `inv.equip {object_uid}` (slots 1/6/7); the fourth (gid 18974,
type 210) has `possible_positions: []` and the client sends nothing for it. The planner now
gives a free-form objective the same NPC question the fight objectives use: every NPC of the
map, once, nearest first (`_npc_talk_calls(..., why_fmt=...)`), after its interactives are
spent and only when the step names no map to travel to. The check that a text-only wrapper
"plans no hunt" still holds -- it now asks an NPC instead of sending nothing.

**A tutorial UI step is a press, not a claim (build ah, live 00:22-00:23).** The onboarding
quest 3841 has 22 objectives, and six of them name a window instead of an action: "Open the
Spell Interface", "Open your inventory from the menu", "Open the Characteristics
Interface", "Open the quest book", "Open the World Map", "Open the Encyclopaedia", plus the
floating Shop button. The client completes each of them from its own menu bar
(`WINDOW_OPENED` / `WINDOW_CLOSED` in `scenarioManager.conditionTypeEnum`), and the page
already knew the DOM classes (`menuIconBook`, `menuIconMap`, ...). Driving them by hand
closed the quest-book step (quest step 4581 -> 4968, objectives 16 -> 17) and the world-map
step (4968 -> 4970, objectives 17 -> 18) through the client's own
`QuestObjectiveValidationMessage`, with no disconnect. A claim on those objectives is the
opposite: they are server-counted (type 0 `QuestObjectiveFreeForm`), and the two claims we
did send were each followed 0.3 s later by `serverDisconnecting SERVER_GONE`.
So build ah adds a first-class capability, `ui.tap` (menu key or CSS selector, optional
`cycle` that presses again after the window opens, so an "open and then close" step is given
back), and `ui.icons` (the visible menu keys and the open windows, read-only). The planner's
tutorial branch now reads the *current step's own name and description* from the quest
catalogue the page publishes (`quests_catalog`), maps the surface word to the client's menu
key, and emits `ui.tap {menu, cycle:true}` at most twice per (quest, step, surface) before
holding. This is plumbing, not per-quest logic: the vocabulary is window names
(map/book/spell/bag/carac/shop), and any step that names one is covered. The tool surface is
27 (schema + `data/capabilities.json` + the Kaggle toolkit mirror). The "no visible element"
refusal routes to `wrong_target` (re-observe) in the controller, and a press with neither
`menu` nor `selector` is refused as an invalid assumption. Gate: **1,067 checks, 0 failed**
(now 1,076 with the control-plane checks).
Not yet exercised live: bit 4980 ("Open the Encyclopaedia") names no menu key this build
exposes; it will be mapped from the live DOM when the step arrives.

**An instanced solo map can have no exit at all (live, 00:23-00:35).** After the shop step the
character stood in `146276352` ("Albuera Village - Solo Area"). The map reports four
neighbours but `map.cells` is 560 cells and `mapRenderer.getChangeMapFlags(c)` is false for
every one of them: no exit cell in any direction, `interactiveElements` empty, no
`animatedElements` that is a door, and clicking the black margin outside the floor clamps to
the nearest walkable cell instead of leaving. Tapping the ladder, the wall, the map-bar
house icon and the star icon changed nothing, and only one of the three NPCs
("Forgelance Intellectual", 3966) answers a talk; the other two (3894, 3892) return no
dialog to `npcActionId` 1 or 3. The tutorial step itself is 4970 ("Start a Fight with a
Monster"), which waits for `FIGHT_START` and there is no group on the map, so the script
cannot advance by itself. A page reload re-entered the world on the same map and step, so
the scenario's start hook does not re-run on reconnect. This is the one shape the agent
cannot solve with movement: a map that the server must teleport it out of. Note that the
four neighbours' map assets return **403** from the CDN relay while the current map is
served from cache, so even a scripted crossing would need those assets first.

**Egress re-pin can outlive the dead address (live, 01:36-01:44 local).** The proxy
re-pinned the session egress at 01:36:57 (the old pin was dead and no game traffic was
live), and the next guest login through the new pin failed before the game was reached:
`assetsVersions.json` fetch **REJECTED** and the client's own "Unable to log in - The
connection failed" popup. That is the triage skill's egress branch, not a game refusal:
the login guard must not treat it as a "Server busy" refusal, and a retry should wait for
the proxy to pick a working address rather than pressing repeatedly. Do not force an exit;
the egress identity is the operator's.

**A blackholing hop is invisible to the relay (measured 01:44-01:59).** The pinned address
`182.18.83.37:7890` passes a SOCKS5 handshake and then delivers nothing: `curl` through it
returns exit 35 after 16 s while the same request through `127.0.0.1:40000` answers 200 in
0.7 s. `dialOne` reports success on the handshake, so `recoverPin` never runs (it is only
reached when the dial *errors*), no `[UPSTREAM]` line is written, and every login fetch
times out. The recovery that works today is a **relaunch of the slot**: the manager's
per-row Launch re-picks the egress (`Auto-Random`), and the first guarded login after a
fresh pick succeeded on one press. Per-instance WebView2 uses `--proxy-server=
http://127.0.0.1:<slotPort>` (the relay) for *all* traffic, including the login host, and
`isGameDestination` includes the login host on purpose (the OAuth popup must share the
game's exit IP), which is why a dead pin strands the login phase and not just the game.
A real fix belongs in the relay: prove the tunnel with a first-byte deadline when no game
traffic is live, then `recoverPin`. Not shipped -- it needs a Go rebuild and a relay
restart, and the relaunch already recovers the session.

**`ui.tap` proven live on build ah (01:10).** `ui.tap {menu:"Book", cycle:true}` on the
world screen pressed `.menuIconBook`, `open_before: []` -> `open_windows:
["grimoire > quests"]`, `changed: true`, and the scheduled second press closed it again
(`open_windows: []` three seconds later). No disconnect: `connected: true`,
`last_disconnect: null`, quest state unchanged. `ui.icons` listed 25 live menu keys with
their enabled flags (Carac/Spell/Bag/Book/Map/Goultine enabled; Help/Zaap/BidHouse/... all
disabled by the tutorial). The step-level proof (a tutorial UI objective advancing on the
press) is still owed: the only live account sits on step 4970, a fight step, in a map with
no group.

**dofus-05 is parked at tutorial step 4970 (01:10).** After the relaunch and one successful
login, the character re-entered map `146276352`, step 4970, zero groups, zero exits -- the
sealed-room finding above. No in-game action can leave it and no group exists to start the
scripted fight, so the loop stays stopped: a loop there would only burn the session. The
account needs a server teleport (tutorial restart) or a fresh guest to continue the chain.

**Two brain daemons can share one port (live, 01:57 and 02:29-02:31).** `netstat` showed
pids 17476 and 9996 both LISTENING on `:8895`, each with its own instance table.
`HTTPServer` sets `SO_REUSEADDR`, which on Windows behaves like `SO_REUSEPORT`: the second
`run_brain.py` bound the occupied port with no error, and from then on the two answered an
arbitrary share of `/brain/status`, `/brain/start` and `/brain/stop`. The host caused it in
two ways. `EnsureBrain()` tracked only the python child *it* had spawned (`BrainProcessAlive`
looks at `_brain`), so a panel restart or the always-on autostart spawned another daemon over
the live one. And every success check was `body.IndexOf("\"ok\":true")`, while the embedded
runtime has no orjson and stdlib `json.dumps` writes `"ok": true` with a space: the host read
**every** accepted start as a refusal (log: `brain daemon refused the start request; falling
back to per-instance children: {"ok": true, ...}`) and spawned duplicate children that raced
the daemon for the instance lock, and every Stop as a failure. Fixed on both sides:
`agent/serve.py` probes the port with a TCP connect before building anything and exits **3**
(`EXIT_PORT_TAKEN`) if a service already answers; `desktop/aipanel.cs` probes
`/brain/health` for `"dofus-brain"` before spawning (attaching instead), parses ok with
`JsonOk()` (`"ok"\s*:\s*true`), polls status for a daemon it did not spawn, and lets Stop
reach that daemon. Live proof: a second `run_brain.py` on `:8895` prints the refusal and
exits 3 while pid 5884 keeps serving. The C# half ships on the next app start (the running
`DofusFarm.exe` is file-locked); until then the agent-side guard alone stops the split brain.
Gate: **1,076 checks, 0 failed**; both mutations (guard removed, `JsonOk` reverted) are
caught by `test_single_control_plane`.

**The skill-less element is not an answer (live, 01:31-01:34).** dofus-02 stood on step 4974
with `_free_form_calls` proposing element 513203 (`skill_uid: null`, `enabled_skills: []`) --
a call the page always refuses -- and the ready scan taking only that first call, so the NPC
question behind it was never reached (1,386 waits in 44 steps). `_element_usable` now drops
interactives without a skill (or enabled skill) before the choice, and the same loop resumed:
`quest.advance {npc_id: 3920}` opened the Class-Changing Sorcerer, `tap.interactive
{element_id: 513195, skill_uid: 136122302}` on a *skill-bearing* element advanced, and the
dialog replies walked through to "Go and equip your new item" (quest 4260's equip step).

**A loop at the login screen can now come back by itself (live, 02:20).** A session that
ended at the guest screen parked the loop for 90+ minutes ("still waiting for the world"),
because `session.login` is not one of the policy's tools. `Autopilot._require_world` now
presses a guarded guest login through `ToolExecutor.recovery_call` (a directive the 27-tool
vocabulary does not contain): at most 3 presses, 20 s apart, only when the snapshot says
`in_world: false` **and** the character has no name -- the live login screen keeps the old
character id with `name: null`, which the first version of the guard missed. The page's own
cooldown stays the authority, so this cannot become a stampede. Live proof: "login press ->
ok=True", then "the character is in the world; starting", with no manual press.

**A repair must not hunt a text id or walk back where it came from (live, 02:10-02:11).**
After a failed step the controller asks the thinking layer; for `type_id 0` it assumed
"kill/farm" and turned the objective's `parameter0` (715354, a text id) into
`fight.join {monster: 715354}`, then `farm.start`, then a walk that ping-ponged the character
between 145490435 and 145490947 on every replan. The log showed twenty steps reported
`advanced` that never touched the objective. Two rules now: a free-form objective whose
client class declares text only (`declares_monster_target(obj) is False`) is repaired by
asking the map's NPCs and by the objective's own map, never by a hunt; and exit proposals
skip the map the character just left (`_exits(obs, avoid=_came_from(obs))`). The gate's
mutation (removing the declares check) reproduces the exact live calls.

**A failed close is not retried (live, 02:16-02:19).** The close that landed a beat after the
page looked was answered `failed`, the controller retried it once, and the retry ran against
a closed dialog: "quest is active but no npc_id was given". `Controller.on_step` now receives
the call's `args` and turns any `quest.advance {close}` failure into a replan; the page
(build ai) also answers a close with nothing open as success, so both halves agree.

**An equip objective with no preset uses the bag's own uids (live, 02:11).** Quest 4260's
"Equip the Albueran recruit set, then talk to Ayin Spyr" had four items, zero presets, and the
preset-only branch answered `inv.list` forever. `_next_equip_uid` offers the bag's uids
least-tried first (two tries each, then retired) and falls through to the talk half. Live:
`inv.equip {object_uid}` fired on the four items; the page refused each with "equipItem sent
nothing and the item is not usable; pass slot", so build ai uses the item's own
`template.possiblePositions` for one `ObjectSetPositionMessage` (the bundle's own `_equip`
path) when `equipItem` sent nothing -- never a slot guessed from the gid. After the relaunch
on build ai the equip step was behind the character: step 4974 -> 4976.

**Step 4976 ends on the shop *opening*, not on a claim or a press (bundle evidence, 03:00).**
"Open the Shop Interface": press the floating Shop button and get the Albueran Elite Bonus
Pack. `open_windows` already reported `market > shop`, and the `.shopBtn` press answered
`changed: false`, because the window had been opened by an earlier step. The client's own
step table settles it: `questStepCheckpoint: 4976` has `executeStart` enabling
`ENABLE_FAKE_SHOP` and pointing the hint at `gui.shopFloatingToolbar`, and its
`endingCondition(e)` is `e === conditionTypeEnum.SHOP_LOADED`. `SHOP_LOADED` is checked when
the shop's articles arrive (`onShopOpenCategorySuccess` -> `addArticles`), and the fake shop
runs only when the window is opened *while the step is active*. So the action is: close the
market window if it is open, then press the button -- one real opening. Build `2026-10-29al`
carries `close_first` in `ui.tap` (it closes named windows through the client's own
`windowManager.close`, reports `closed_before`, and re-reads `open_before` afterwards), and the
planner's shop surface now carries `close_first: ["market"]` with no `cycle` (the cycle would
close the shop the step is waiting to load). With the window already open the step no longer
presses at all. `tools/test_ui_tap_close.js` runs the handler and pins close-before-press.

**The pack is a fake-shop purchase, and only the client's own condition completes it
(bundle evidence, 04:40).** The scripted step that follows 4976 (`questStepCheckpoint: -1`,
dialog portrait `happy_spear`) ends on `conditionTypeEnum.FAKE_SHOP_BUY`: the article's
hard-price button, pressed while `ENABLE_FAKE_SHOP` (behaviour 16) is on, makes the client
emit the condition and the scenario itself sends
`QuestObjectiveValidationMessage {objectiveId: 14072}`. Outside that mode the same button
starts a real-currency purchase through an account confirm window. New capability
`shop.buy` (the 28th tool) uses the client's own hint path -- `market` window ->
`tabs.getFirstTab()` -> `target.getFirstArticle()` -> `getButtons().hardButton` -- and
refuses unless `scenarioManager.isBehaviourEnabled(16)` is true, reporting the article id
and the window set. The planner's shop branch now buys the first article when the window
is already open (bounded at four attempts), instead of pressing the floating button again.
`tools/test_shop_buy.js` runs the handler against stubbed client objects (12 checks); the
constraint corpus gained the three refusal texts, and the controller now routes an
`unavailable` cause to replan regardless of how the refusal is worded. Build `2026-10-29al`.

**A tutorial hint can name a control with no CSS class, so `ui.tap` takes `client_target`
(bundle evidence + code, 05:30).** The step after the shop's fight (`questStepCheckpoint:
-1`, dialog 25477) starts with `o.pointToChallenge()` and its endingCondition is
`TAP_ANYWHERE`; the scenario listens for `gui.wBody`'s `dom.touchend`, while the bot's
combat casts go through `gui.fightManager.castSpell` and never produce that event -- so
the step would wait forever. The hint code itself is the specification:
`pointToChallenge` taps `gui.challengeIndicator.challengeSlot`, and
`pointToConfirmUpgradeSpell` taps
`grimoire.tabs.tabsMap.spells.target.spellBook.confirmButton`. The scenario's listener
is satisfied by a synthetic press: wui binds `mouseup` on `wBody`'s root element and
emits `dom.touchend` for a left button (`which === 1`) unless a real touch happened in
the last 500 ms, and `_agentUiTap` dispatches exactly that mouseup. Build `2026-10-29an`
resolves both by that object path (`_agentClientTarget`) and `ui.tap {client_target}`
refuses a missing or zero-sized control instead of pressing some other element. The
tutorial snapshot now publishes `step_checkpoint`, `hint_target` (read from the step's
own `executeStart` source: `pointToChallenge` / `pointToConfirmUpgradeSpell`) and
`behaviour_ids` (the numeric list, additive: `can_change_map` keeps its name-based read,
which is all-true and harmless because live crossings succeeded with those flags set).
The planner acts on `hint_target` before any stale NPC/cell gate and bounds the press at
four attempts per step; the autopilot, which owns the loop while `in_fight` is true (the
planner returns `state.snapshot` there), also presses once per fight, spaced three
seconds and capped at five, only while the hint names the challenge. Both presses pass
the *runtime* contract (`data/capabilities.json`, loaded instead of `schema.py`'s
builtins): the first version sent `close_first: ["market"]` against a string parameter,
and the executor would have refused the shop press before it reached the page -- so
`close_first` is now one window name, the registry carries `client_target` and
`close_first`, and `ui.tap`'s special rule accepts a client target alone. The gate now
runs every press the planner produces through `validate_call`. `tools/
test_ui_tap_client_target.js` (13 checks) and `tools/test_tutorial_hint.js` (9 checks)
run the extracted page code; the gate also gained five planner assertions and seven
autopilot ones for this path.

**The characteristics step needs the window's Validation *effect*, not its window
(bundle evidence, 05:44).** The later step that waits on `CHARAC_UPDATE` (objective
13531) cannot be satisfied by opening the Carac menu: the window's Validation button
spends the points (`StatsUpgradeRequestMessage`) and *then* calls
`scenarioManager.checkCondition(CHARAC_UPDATE)`, and neither the "+" nor the confirm
has a stable element path. The snapshot now publishes `hint_target: "charac_update"`
when the current step's `executeStart` touches the `characUpdate` window, and
`ui.tap {client_target: "charac_update"}` reproduces the button's two effects in that
order: `char.build` spends the unspent stat points (spells untouched), then the
condition is fired. A spend that moved no point refuses and does not claim the
objective; the action also re-checks the current step's own `executeStart`, so a stale
caller cannot fire the condition on an unrelated step. `tools/test_tutorial_hint.js`
now evaluates the *real* `characUpdate` step object from the bundle and pins its `-1`
checkpoint and hint, beside the real challenge step.

One more step sits between them: the step whose endingCondition is `WINDOW_OPENED`
with windowId `characUpdate` (the stat-spending mini-window). Its executeStart has no
hint, so the snapshot now publishes `open_window`, parsed from the endingCondition,
and the planner presses `_WINDOW_CONTROLS["characUpdate"]` -- `.upgradeButton`, the
"+" cell of a characteristic row and the client's own opener for that window --
before the generic surface path. The real dialog-25463 step is pinned in the same
canary.

**The spell upgrade step needs the plus press, not just the confirm (bundle evidence,
06:02).** The step before `pointToConfirmUpgradeSpell` calls
`displayHighlightedSpellButtons(true)` and ends on `SHOW_CONFIRM_BUTTON` with
`canConfirm`; its only action is a spell's "+" (`addSignButton`), which raises
`levelWanted` and reveals the confirm button the next step taps. The snapshot publishes
`hint_target: "spell_plus"` from that executeStart, and `ui.tap {client_target:
"spell_plus"}` walks the spell book's `spellMap` -> `table.getCell(id, "rank").plusBtn`
and presses the first visible enabled plus (DOM `.addSignButton` as the fallback), so
the confirm press has a button to press. The real step is pinned in the same canary.

**The whole Albuera chain is audited against the bundle (06:04).**
`tools/audit_tutorial_chain.js` evaluates the real 51-step array and classifies every
step: each one is either game-progress (fight, map, quest, window, shop) or has a
control path the page can press. The nine steps with no condition are state-driven
transitions (level 10, quest-tracker moves, the terminal step) and need no action. The
audit runs inside the node gate, so a client rebuild that adds an unreachable step
fails the gate instead of stalling a fresh guest silently.

**The C# host is now compiled by the gate (06:25).** `aipanel.cs` changed at 02:29 and
had never been compiled, so a broken host was invisible until the next release (the
running exe is locked and predates the change). `agent.regression` now finds the same
compiler `builder/Build.cs` picks (Roslyn `csc.dll` from the newest .NET SDK, else the
framework `csc.exe`), compiles the exact source list with the same references into a
temp directory -- never the shipped exe -- and fails on a compile error; it skips with
a printed SKIP when no compiler exists. The current sources compile clean.

**A fall-out that lands on character selection used to wait forever (log triage, 06:30).**
The last live session (`dofus-02`, session 5bef45f2) ended with `kind:"recovery",
trigger:"fell_out", recovered:false, waited_s:751.8`: the client left the world 6 s
after its last `nav.goto`, and the recovery waited 12.5 minutes without pressing
anything. The reason: recovery only pressed the *guest form*, and only when the
snapshot had no character name. Character selection is a different screen -- the
client holds the last character, so `at_login` was false -- and nothing pressed
there. The client's own play action on that screen is `selectCharacter(id)`, which
sends exactly `CharacterSelectionMessage`; `session.login {target:"character",
character_id}` now sends that message (refused while already in the world, refused
without an id, and still under the page's login cooldown and ban guard), and
`_require_world` uses the bound identity's id, pressing at most three times per
fall-out. The press budgets are now per wait, reset when the world returns: before,
a second dropout in the same run could never be answered. The run-away triage also
showed every `closing:` line in the log is a user/host close (`UserClosing`,
`TaskManagerClosing`), not a kick, and that the host seed
`data/safety/kick_signatures.json` is behind this profile's localStorage store
(5 pairs the client refuses: 3841/13537, 3841/14070, 4262/13948, 4262/13950,
4262/13956) -- the seed file protects fresh profiles, so those pairs will need a
review before they are copied in; the log that recorded their reasons has rotated
away. Build `2026-10-29au`.

**The later tutorials' controls are resolved the same way (bundle evidence, 06:15).**
The same bundle holds five more step arrays after Albuera: `F` (Astrub, 9),
`k` (profession/craft, 20), `B` (job, 3), `P` (bestiary, 5) and `W` (trade, 6).
Their steps point at controls through the same helper convention, so the snapshot's
hint table now reads the whole helper family from `executeStart` and `ui.tap` resolves
each name by the exact object path the helper itself walks:
`contextual_menu:N` / `npc_menu:N` -> `getContextMenu(kind).actionsContainer
.getChildren()[N]`; `trade_validate` -> `tradeWithNPC.getValidateBtn()`;
`trade_slot` -> `tradeWithPlayerAndNPCInventory.getStorageFirstSlotForTuto()`;
`craft_recipe` -> `crafting.getFirstRecipe().craftButton`; `craft_merge` ->
`crafting.getMergeBtn()`; `craft_slot` -> the correct crafting inventory's first slot;
`shop_category:N` / `shop_item:N` -> `bidHouseShop.getCategoryDom/getItemInSaleDom`;
`sell_info` -> `tradeStorage.infoButton`; `bestiary_monster` / `bestiary_drop` ->
the bestiary tab's `getMonsterWithDrop` / `getDropDomFromMonster`; and
`close:NAME` closes a window through the client manager (the bestiary and job steps
end on `pointToWindowCloseButton("grimoire")` -> `WINDOW_CLOSED`, and two Albuera
steps do the same for the spell book at checkpoint 4950 and the equipment window,
whose close sends the objective validation itself). The manager's `closed` listener
fires `checkCondition(WINDOW_CLOSED, {windowId})`, so a manager close is the same
event the close button produces. Every window path gates on `openState`, exactly
like the helpers. `playInteractiveElementTap(<id>)`
publishes `interactive_hint`, and the planner answers it with
`tap.interactive {element_id}`, which is `isoEngine.useInteractive` (it walks there
and sends the use request) -- the profession chain opens two workshop menus that way.
The job chain's add-ingredient step cannot be tapped (`DISABLE_SLOT_TAP`) and waits
for `CRAFT_ITEM_ADDED`, so `ui.tap {client_target: "craft_drag"}` performs a real
pointer drag from the crafting inventory's first slot onto the crafting window's
`_mySlotElems`: press on the slot, six stepped held moves on `wBody.rootElement`
(wui converts those and the release into `dom.touchmove`/`dom.touchend`, the events
the drag manager binds), and the release carries the destination centre as its
client coordinates -- the manager resolves the drop target with
`document.elementFromPoint` and dispatches a bubbling native `drop` there. The audit now tags every control
with whether the page can perform it, treats data-load events (`FAKE_STORAGE_LOADED`,
`BESTIARY_MONSTER_LOADED`) and condition-less/rewind-only steps as progression, and
carries an NPC lock across steps so a menu step that re-taps the NPC counts; all six
arrays report zero unhandled steps, and `H` still fails the gate on any regression.
`tools/test_ui_tap_client_target.js` grew to 51 checks (twelve later-tutorial targets,
a closed-window refusal, `close:`, the drag focus and the raw drag sequence) and
`tools/test_tutorial_hint.js` to 74 (a synthetic and a real-bundle canary per helper,
plus `interactive_hint`). Build `2026-10-29au`.

**Travel is routed over the map index, one border at a time (07:00).** `_travel_call`
used to send `nav.goto {map_id: <destination>}` whenever the destination appeared
anywhere in `data/maps/_index.json`. The client only crosses an exit of the map it
stands on, so a destination four crossings away was either refused ("map X is not
adjacent") or walked one edge and asked for again. The live dofus-02 session of
2026-10-29 (03:25-03:26, session `5bef45f2`, objectives 4262/13940 and 4264/13960)
alternated maps 145489920 and 145489921 for twenty steps, every leg `ok advanced`:
one ready objective's map was the map just left, and the other is four crossings
away. `features.map_graph()` now returns the id set **and** each map's
`neighbours` from the same read, `route_first_hop`/`route_distance` BFS that
graph, and `_travel_call` asks for the first crossing -- matched against the exits
the client actually reports, with the index direction as the fallback -- never the
raw destination. When the index does not hold both ends (a newer map, or the
offline simulator's synthetic ids) the old request is kept, so the explore branch
still owns discovery. Travel that does not come nearer is bounded by `_route_tries`
(12 decisions without a shorter distance) because the ordinary claim counters are
wiped whenever the scene key changes and a crossing *is* a scene change; the
counter survives crossings, resets when the distance drops, and after the bound the
planner explores instead of re-sending the same request. The ready scan's
`_exhausted_at` record (a map stood on with nothing to do for that objective) now
also gates `plan_objective`, so the active-quest path cannot walk back to a map it
already found empty -- that path was the one still emitting the return leg. A
forward simulation of the live state now walks 145489920 -> 145489921 -> 145489922
-> 145490434 -> 145490946 (four crossings) instead of bouncing.
`test_index_route_travel` pins the first hop, the distance reset on arrival and the
expiry after twelve non-improving requests; `test_exhausted_ready_maps` now expects
the first crossing rather than the destination. Build `2026-10-29au`.

**The tutorial's NPC gate is bounded too (07:20).** `_tutorial_call`'s
`unblocked_npc_id` branch returned `quest.advance {npc_id, quest_id}` on every
decision and remembered nothing. A talk either opens the dialogue in that one call
or it does not, so a repeat asks the same NPC the same question: the recorded
dofus-05 session of 2026-10-27 (build `2026-10-27l`, step 1 "Parler a Jin Spyr")
dispatched 277 identical no-op talks, and dofus-03 dispatched 272 in the same
window. The branch now shares the tutorial press budget (four attempts keyed by
quest, step, special step and npc), after which the step's other gates and the
ready objective get their turn and the caller waits on the step. Replaying that
session against today's planner now emits five talks and then reads. The
autopilot's call hold and dead-talk receipt still sit on top; this is the
planner-level bound for callers that do not hold (brain threads, corpus
generation, replay). Build `2026-10-29au`.

**An interior is not a map you can walk out of (07:45).** The client publishes
each map's `neighbours` and, separately, only the borders it can actually cross in
`exits`. A house interior reported four neighbours and zero exits, and the planner
kept requesting another map's id from inside it (dofus-05, 2026-10-27, session
`cabda584`, map 146276352: ten `nav.goto {map_id: 145489920}` on a map whose way
out is a door the snapshot did not list). `_travel_call` now distinguishes *no edge
information* (a partial snapshot, or the offline simulator's synthetic maps -- the
destination request is kept) from *edges known and none crossable* (an interior):
for the interior it returns `state.snapshot`, which the ready scan treats as a
detour, so a locally actionable ready objective or a read takes the step instead of
a change request that cannot be satisfied. Both shapes are pinned in
`test_index_route_travel`. Build `2026-10-29au`.

**Recorded turns are now a gate, not a one-off measurement (08:20).** The
autopilot logs rotate, so the numbers quoted above were only reproducible while
the files survived. `tools/freeze_planner_replay.py` samples up to 30 decision
rows per recorded session from `autopilot.dofus-02/05/03.jsonl`, trims the heavy
and volatile blocks (geometry, keep-alive, user input, last disconnect/nav, farm
state, ensemble) plus the `map.transition` packet log (it carries the
AuthenticationTicket), and writes `data/replay/planner_obs.jsonl`: 420 rows over
17 sessions. `agent.regression.test_planner_replay` replays every row through
`QuestPlanner().choose` and pins the invariants the live bugs violated: zero
map-change requests naming a non-adjacent map, zero requests from an interior
with no crossable border, at most 3 turns with no decision (measured 1), plus
non-vacuity (>=3 sessions, >0 map requests, >=5 distinct tools; measured 2 map
requests, 270 `quest.advance`, 109 `tap.interactive`, 19 `state.snapshot`, 18
`shop.buy`, 1 `ui.tap`). Regenerate deliberately with
`python tools/freeze_planner_replay.py`; `--check` reports drift. The baseline
turns are trimmed snapshots only -- no credentials, tokens or packet tickets.
Build `2026-10-29au`.

**The 751.8 s rescue was ended by the operator, and now leaves the screen behind
(08:45).** Triage of dofus-02 session `5bef45f2` (the nav-bounce session): the
farm log carries no `closing`/`AI_DISCONNECT` line for dofus-02 near it and the
recovery row's `reason` is null, so the loss itself was unlabelled -- the shape
a page reload leaves when it wipes `dofus.disconnect(reason)` before it can be
read. The wait ended at 03:39:11, one second after `AUTOPILOT_STOP.dofus-02` was
written at 03:39:10: recovery did not give up, the operator stopped the slot.
Why the world never returned during those 751.8 s is *not* in the surviving
logs -- that loop's console lived in the daemon process and was overwritten by
the next restart -- and neither press branch can be shown to have fired. The
wait now records what it is looking at: `_world_screen` classifies
login / character selection / in-world / dead page (`no_snapshot`) / unknown,
every 30 s report names the screen and the login/enter press counts, the stop
line prints the last screen, and the persisted `kind: recovery` row carries a
`screen` field so the next triage does not depend on a console that rotates.
The selection-enter and guest-login presses already existed and are pinned in
`test_login_recovery`, together with the new screen fields. Build
`2026-10-29au`.

**A session ticket was being written to every corpus (09:20).** The page's
map-transition ring copied up to eight scalar arguments of every outgoing
packet so a refused map change could name its map. `AuthenticationTicketMessage`
carries the session `ticket`, so the snapshot -- and everything built from it --
contained a live credential: 1,582 values across 11 files, three autopilot logs
and eight trajectory files, including the corpus Kaggle runs read. Four layers
now stop it: the ring filters credential-shaped names at the source
(`_agentPacketArgs`, page build `2026-10-29av`), `agent.observe.redact_packet_secrets`
strips them from any snapshot a runtime records (so an older page build cannot
leak again), the manager's own play recorder strips the value in C# before its
final write (`WebView2ClientForm.RedactSecrets`, the writer that holds
`play_*.jsonl` open), and `tools/scrub_packet_secrets.py` rewrites what was
already stored (dry run by default, atomic per file, skips `[redacted]`). The scrub
removed 1,014 values; 568 remain in `trajectories/play_dofus-0{2,3,5}.jsonl`,
which another process holds open -- re-run `python tools/scrub_packet_secrets.py
--write` once they are released, and the tool reports `0` when the corpora are
clean. `test_packet_redaction` (Python) and `tools/test_packet_redaction.js`
(page) pin the filter, the surviving map/refusal fields and the clean frozen
corpus, and `test_cs_secret_redaction` compiles the recorder's own regex into a
probe and executes it (ticket redacted; other keys, escapes and spacing
untouched), so the C# layer is verified rather than grepped. Build `2026-10-29av`.

**A seeded kick signature can now be proven wrong (09:35).** The disconnect
triager found five pairs the client's own store blames but the checked-in seed
does not list (`3841/13537`, `3841/14070`, `4262/13948`, `4262/13950`,
`4262/13956`). They were not merged into `data/safety/kick_signatures.json`:
a seed is permanent, while the learned rows expire after a day, so a wrong seed
would disable an objective in every future session. The asymmetry that made
that dangerous is fixed instead -- `__agentClearKick` used to return early when
localStorage had no row (a fresh profile, i.e. the normal case) and never
touched `window.__agentKnownKickSignatures`, so the accepted-validation handler
that is supposed to lift a false signature did nothing for a seeded one. It now
removes the pair from both stores and reports whether anything was cleared;
learned rows still expire after 24 h and the accepted validation still clears
them. `tools/test_kick_signature_clear.js` executes the real reader and clear
action against a fake window (seeded, learned, stale, unknown, missing args).
Page build `2026-10-29aw`.

**The shop chain's post-load step is a tap, not a purchase (bundle evidence, 05:48).**
Between `SHOP_LOADED` (4976) and the fake-shop purchase there is a `-1` step (dialog
26689) that advances on `TAP_ANYWHERE` and *rewinds* if the market window closes.
Buying on that step loses the `FAKE_SHOP_BUY` event -- the next step is the one that
listens for it -- and pressing the floating button again closes the market window,
which is exactly the rewind branch. The snapshot now publishes `tap_anywhere`, read
from the current step's `endingCondition` source, and `ui.tap {client_target: "world"}`
dispatches the mouseup on `wBody.rootElement` (wui turns a left-button mouseup into
`dom.touchend`, the event the step watches) only while that source names
`TAP_ANYWHERE`. The planner runs this before the shop surface's close-first press, and
a spent hint falls back to the world tap because the step ends on any touchend.
`tools/test_tutorial_hint.js` evaluates the real dialog-26689 step and pins
`tap_anywhere: true` with no client-target hint on it; `tools/
test_ui_tap_client_target.js` runs the world tap and its refusal on a non-tap step.
Press budgets are keyed by the scenario's array index (`step_id`), not by the
checkpoint: every sub-step carries `questStepCheckpoint: -1`, so a checkpoint-keyed
budget is one budget shared across unrelated steps (the gate proves two sub-steps
with the same checkpoint each get their own).

**A step that points at a menu now names it in the data (bundle evidence, 05:51).**
`executeStart` calls `pointToMenuIcon("Carac")` (or Book, Map, Help, ...); the
snapshot publishes `menu_hint`, and the planner presses exactly that menu with the
press-back cycle before falling back to matching the step's English name against
`_UI_SURFACES`. The real checkpoint-4573 step is evaluated in the same canary and
pins `menu_hint: "Carac"` with `checkpoint: 4573`.

The same table shows what the run must expect next, and the planner now expresses it:
step 4978 idles until the character is **level 10** (its endingCondition sends objective
14074 when `level >= 10` and no fight is running), step 4980 opens the **Help** window
(`pointToMenuIcon("Help")`, `WINDOW_OPENED "help"`) and the step after it ends on that
window's close (objective 14076), and a later step waits on the characteristic update
(13531). `_UI_SURFACES` gained `("help", {"menu": "Help"})`, and surface matching is now
**name-first**: a step's name is the client's own label ("Open the Help Interface") while
its description is prose ("ask him to help you"), so only a step with no name falls back
to the full text. The pinned case: a step named "Talk to Ayin Spyr" with a description
saying "help" must not press Help.

**A permanent ban ends the run instead of pressing into it (live, 03:26).** The dofus-02
guest came back from the login server with `popup ... message=Your account has been
permanently banned.` (`DofusFarm.log` lines 99795-99811, the same answer dofus-01's guest got
earlier). The page's cooldown scanner only knew "server busy"/"access denied", so the new
self-login recovery would have pressed three times and then parked forever. The page now
reads the visible text (`_agentLoginBan`, cached 2 s), publishes `snap.login = {banned, text}`,
and `session.login` refuses with `banned: true`, `login_banned: true` -- `force` does not get
through a fact. `_require_world` checks it before every press and ends the loop with "reset
this slot for a fresh guest"; `_recover_world` inherits the same check. The loop was parked
with a stop file rather than left pressing. The ban itself is an account state, not a code
defect: only a per-slot Reset (fresh guest) changes it, and that wipes the profile, so it is
the operator's call.

**Two exhausted ready objectives no longer walk back and forth (live, 03:06-03:26, steps
71-80).** Quests 4262 and 4264 each held a ready free-form "go back and see X" objective on
adjacent maps (145489920 and 145490946). Standing on either map, every NPC had already been
asked, so `plan_objective` returned nothing and the only available call was the walk to the
*other* objective's map; the log shows ten alternating `nav.goto` steps. The planner now
records `(quest, objective, map)` when it stands on the objective's own map with no action
left (`_exhausted_at`), and the ready scan refuses to travel for a triple it has already
exhausted -- the loop waits (`state.snapshot`) instead of pacing between two maps. Only a new
tutorial step clears the record; a map change must not, because that is exactly the loop. The
oscillation was a symptom of the blocked shop step, and this bound is what makes the wait
honest when a step cannot complete. Replayed against the recorded `obs_before` of steps
68-80 (`autopilot.dofus-02.jsonl`), the live planner alternated `145489920`/`145490946`
every step; the new one plans `145490946` on every observation after the first arrival and
never walks back.

**Live session on build `2026-10-28x` (20:14-20:22).** A stop/relaunch finally
happened through the manager's row context menu (the per-row ACTION cell ignored
synthetic clicks; "Launch / Stop" in the right-click menu worked). The client came up
on build `x`, **logged in on the first press** (`gui.loginScreen.playWithGuest()` ->
`in_world: true` in under 6 s, no cooldown), walked four maps, and fought: during a
fight `battle_log_tail` held **12 rows** and the ending row for fight 900 recorded
**`battle_log: 22`** where the two morning rows had `[]`. That is the ring tap proven
live; the client's own stack was indeed empty at the end.

**`battle_log: 22` was lifecycle only (found 21:30).** Those 22 rows were
`GameFightStartingMessage` ... `GameFightEndMessage` with no cast or damage row, so
`tools/damage_calibration.py` honestly printed `0 matched cast(s)`. The emit tap sees
messages the client dispatches standalone, but damaging actions on this client arrive
inside `messageSequence` batches; build z expands those batches in the tap **and** copies
action rows out of `fightMessagesStack` on a 250 ms poll and inside `_battleLog`, deduped
by `(type, actionId, source, target, spell, loss, value, delta)` so a re-poll cannot
double-count. `tools/test_battle_log.js` grew from 10 to 18 checks (harvester copies,
dedupes, skips lifecycle; reader sees harvested rows; sequence expansion pinned).
The same fight exposed a
second bug: **two `fight_outcome` rows were written 127 ms apart** for one fight, because
the host's own autopilot and the brain daemon's per-instance thread both drove dofus-05.
`Autopilot.run()` now takes an **exclusive OS lock per instance**
(`msvcrt.locking`/`flock` on `autopilot.<instance>.lock`) and a second driver prints
"another loop already drives this instance; standing down" instead of doubling every
directive and row. The keep-alive is also gated on the client actually being in the
world: with the AI auto-started at launch, it used to flip the activity tracker and
ping every 20 s while the guest login was still in flight, which is the mechanism that
kept the half-open session alive and the account "busy" (the user's hypothesis, now
pinned by `keep_alive.marks: 0, failed: 11, present: false` at the login screen).
After the loop stopped, the client was kicked for inactivity about four minutes later,
exactly as the AFK model predicts, and came straight back on one press.

**First live damage calibration: fights 133 and 138, build aa.** With the harvest in
place the fight-end row carried **59 battle-log rows / 22 with an action id** for fight
133 and then 240/109 for fight 138. `tools/capture_fight_outcome.py` (new) copies
`state.snapshot.last_fight_end` into `data/live_evidence/fight_outcomes.jsonl`, so a
fight played outside the autopilot loop still becomes evidence. Two bugs surfaced while
building the join:

* `report()` had been reading the `{kind, outcome}` envelope as if it were the outcome,
  which is why every earlier run said `0 matched` even when rows existed; it now unwraps.
* The ring outlived a fight: fight 137's 194-row log contained fight 133's casts and
  produced duplicate pairs (the same 26/18/2 three times). `GameFightStartingMessage`
  now clears the ring and the harvest signature set, so a fight's log starts at its own
  start message (build aa). The ring/read cap also went 400->800 (build ab) because a
  three-monster fight passed 240 messages and lost its opening rows.

The clean result is **2 fights, 9 matched casts, 0 unmatched, MAE 9.44, bias -7.48**:
spell 7041 (2 casts, MAE 8.95), spell 7045 (6 casts, MAE 10.50, bias -8.92), spell
7051 (1 cast, MAE 4.05). The solver **overestimates** damage by ~7.5 on average, which
matches the contract's own warning (`enemy_stats_not_exposed`: no enemy resistances in
the model) and is the correction a learned calibration head should learn, not a constant
to hardcode. The join also had to learn two protocol facts: the cast's action id (300)
is not the effect's (96/97) and is reused within a fight, so pairs are formed by log
order (the newest cast by my side owns the losses that follow it), and a summon's loss
(`source: -22358`) is still my side's damage while an enemy source is not. Self-casts are
counted separately as non-damage, not as misses.

**Challenges are live.** Every recorded fight on builds z/aa carried two
`ChallengeInfoMessage` rows with their result (`{id: 40, success: true}`, `{id: 366,
success: true}` for fight 133; `{id: 25, success: false}`, `{id: 368, ...}` for fight
138). The message's own text fields are null on this build, but the names are not
missing from the client: the bundle's challenge panel fills
`gui.challengeIndicator.challengesData[id]` in `setChallenge` from
`_name`/`_description`/`dropBonus`/`xpBonus`/`points`/`_gfxId`/`_categoryId` (verified
in `client/www/build/script.js`), and the path resolves live on dofus-05 (empty outside a
fight). Build ad merges that panel into `_challenges()` (message first, so a raw value
still wins; icon and category are carried too), so the AI can name a challenge, read its
bonus and target, and the reward can stay small and optional -- a miss is never a
failure. `tools/test_challenges.js` runs the reader against a stub panel (panel fills a
missing name, message wins, bonuses/target/icon merge, no panel still works) and is in
the gate. Live confirmation of a named challenge lands with the first fight on ad.

**PCA of a board: measured, and not wired into the live path yet.** A board is
`560 x 12` cell features plus `16 x 20` fighter features = 7,040 numbers.
`combat/board_pca.py` (fit/project/reconstruct/save/load, centring only) plus
`tools/pca_probe.py` measured the recorded corpus: **2 components carry 90% of the
variance, 3 carry 95%, 11 carry 99%**; reconstruction MSE is 4.4e-4 at k=4 and 2e-4 at
k=8; projection costs **0.03 ms per board**; and **10.6% of board pairs are closer than
1% of the median distance**, i.e. the corpus holds redundant frames. The spectrum is
dominated by map geometry and occupancy, which is exactly why the raw width is not the
problem: the grid transformer already consumes 12 features per cell, and the live
bottleneck is 7 actionable boards, not 7,040 columns. A PCA signature therefore stays a
tool for clustering/de-duplication and a future value head's board id, not a live input;
it will be wired only when there are labelled boards on which a PCA-space head can be
measured against the raw one.

---

### 5.10 Host behavior §5 omitted until 2026-10-30

Things the sources do that a reader of the old §5 could not know:

- **Always-on AI is the default.** `runtime.ini auto_ai=1` (default true,
  `runtimesettings.cs:17-23`) makes every `StartWebView2Instance` also start that
  instance's loop (`mainform.cs:1900`, comment `:1903-1906`). "I launched a client and it
  began acting" is designed behavior, not a bug — and `runtime.ini` did not exist on this
  machine at audit time, so the defaults were in force.
- **A Runtime settings card** with 5 toggles plus a grid-refresh combo (8 checkboxes on
  the Settings page in total, `pages.cs:165-183,217-262`), next to `refresh_ms`,
  `watchdog`, `governor`, `telemetry`, `fight_boards`, `auto_ai`.
- **Fail-closed is a launch gate, not a log line.** `ProxySupervisor` probes every
  candidate (tunnel, configured upstream, `proxies.txt`), drops any whose exit IP equals
  this machine's own, keeps a sticky pin, fails over and rebuilds live relays
  (`proxysupervisor.cs:1-26,471,525,533`), and instances **refuse to start** when nothing
  is healthy — the live log line is `[PROXY] no healthy egress (14 tested)`.
- **Per-instance AI state** (`IsRunningFor`, `LiveStatusFor`, `StepLogFor`) as described in
  §5.4, which is what makes the multi-instance pill an aggregate.
- **Self-heal and arbitration:** `watchdog.cs` hang detection, `prioritygovernor.cs`
  CPU/GPU priority, live memory telemetry on client windows, and `hardware.cs` gating the
  device profile against the real machine (`mainform.cs:597`).
- **Buttons with no §5 entry:** `Reload client` (picks up a rebuilt `mobile-disguise.js`
  without a restart), the quick Focus toolbar, `Repair my network`, and the proxy-pool
  actions (`pages.cs:322-331,333-341`).
- **One stale string in the source, and it is not user-visible:** `aipanel.cs:493-499`
  builds a hint label reading `"All 28 tools enabled; …"` (corrected 2026-10-30 — it said
  25) and then sets `Visible = false`, never touching it again. So it misleads a reader of
  the source, not the user. If a future pass wants the count to stay honest without a manual
  edit, the label has to be made visible *and* fed from the registry — nothing in C# reads
  `data/capabilities.json` today (`grep capabilities.json desktop/*.cs` is empty).

---

## 6. The client and its disguise

### 6.1 What is served

`desktop/client/www` is the Cordova bundle: `index.html`, `cordova.js`, `js/*`
shims, `build/script.js` (~5.4 MB, the whole game), `build/styles-native.css`.
It is served under `https://dofus.local/`, which the relay remaps to the real login
host for sockets. The current bundle carries `window.buildVersion="1.74.10"`; the
updater moved it from 1.74.9 on 2026-10-27.

`client/www/` is an older tree that is not served; `CLEANUP.md` documents why it is
kept but not used.

### 6.2 The shim layer

The bundle expects a phone. The shim provides just enough:

- **Cordova** — `cordova.js`, `CordovaFileSystem`, `CordovaFileCache`, `AppLoader`,
  `AppLoadingProgress`, `autoupdate`.
- **Adjust SDK** — `Proxy`-based no-op stubs. The client calls methods we do not ship;
  a Proxy answers anything, so the stub does not go stale.
- **`scripts/mobile-disguise.js`** — injected before the game boots. It overrides the
  device fingerprint (UA, WebGL vendor/renderer, screen metrics, serial, Android ID),
  installs the recorder, the FarmBot, the tool handlers, and the HUD.

**Only one copy exists.** `scripts/mobile-disguise.js` is the source the host injects
(`WebView2Backend.cs` reads it from `<exe dir>/scripts/mobile-disguise.js`). Editing
it needs no rebuild; a page reload applies it. The two duplicated copies were deleted
and a check fails if a second copy appears.

### 6.3 HUD

A small pill in the game window:

- label `DofusFarm AI: ON/OFF` — click toggles the fight AI (same as F2);
- draggable; position saved per instance in `localStorage["__dofusfarm_hud_pos"]`;
- **▶ Auto** posts `{"type":"AI_REQUEST","action":"start_auto"}` to the host, which
  opens the AI page and starts the loop. The page cannot spawn processes.

### 6.4 Page → host messages

| type | meaning |
|---|---|
| `CONSOLE_LOG` | console line, forwarded to the host log |
| `PLAY_LOG` | one human action, written verbatim to the play log |
| `FARMBOT_STATE` | fight AI on/off |
| `AI_REQUEST` | start the autonomous loop / request host state |
| `TOGGLE_FULLSCREEN` | window command |
| `diagnostics_report` | capability probe (WebGL, storage, UA) |
| `open_oauth` / `close_oauth` | Google login window |
| `webauth_exchange` | guest token exchange |
| `ping` | liveness |

### 6.5 Login paths

1. **Guest** — `POST https://auth.ankama.com/token/create-guest`, exchanged for a
   session. Guests are wiped by client updates and rate-limited per IP. A 403 from
   CloudFront means the guest quota for that address is exhausted, not that the code
   is broken.
2. **Real account** — manual login (`manual-login.flag`) or Google OAuth through the
   popup. In manual mode the host injects nothing and clears any stored guest,
   because a persisted guest identity otherwise overwrites the real session on reload.

`session.login` drives the login screen through the game's own handler:
`gui.loginScreen._playGuestBtn` registers a `tap` listener, so the tool emits that
event (a synthetic DOM click is ignored because `isTrusted:false`). It never writes
credentials, never calls `dofus.setCredentials`/`dofus.login`, and never opens an
OAuth popup.

### 6.6 Known black-screen causes (all fixed, kept as history)

| cause | symptom | fix |
|---|---|---|
| `styles-native.css` missing | blank page, no JS error | bundle validation |
| updater deleted `script.js` | blank page, no bundle | download to staging, commit after validation |
| `AdjustConfig` returning `{}` | boot exception | Proxy stubs |
| `appVersion` in the wrong query position | `SOCKET_LOST` | version param emitted last |
| patches lost after an update | login error / black screen | runtime re-patch |

---

## 7. The updater and the patch engine

The bundle is **not** shipped inside the APK: Ankama serves `manifest.json` and the
client downloads `script.js` at runtime. That is why a cached copy goes stale silently
and why "the update broke it again" was a recurring failure.

### 7.1 Root cause

`script.js` is patched at build time by `tools/patch-engine.js`. The old runtime
updater replaced `script.js` with the server's **raw** copy, so every patch vanished
on the first server update. Login failed or the page came up black, and nothing in
the logs pointed at the cause.

### 7.2 The updater is now a transaction

```
manifest.json
   ↓
download changed files → client/www/staging-<ticks>/
   ↓
apply tools\patch-rules.txt to the staged script.js
   ↓
validate only the staged files (size, not HTML, CSS non-empty, index.html if present)
   ↓
commit each file with a .prev copy, then drop .prev
   ↓
write client_version.json
```

Any failure discards staging and **keeps the working bundle**. Nothing is deleted
before its replacement exists.

- `build/script.js.bak` always holds the unpatched original.
- Self-heal on launch: if the live bundle is unusable (missing, <1 MB, or starts with
  `<`), it is restored from `.bak`.
- `no-client-update.flag` disables all of it.
- Adoption is refused when no core patch matched.
- One update transaction at a time (`lock (_gate)`) because `Launch All` fires one
  updater per instance at the same `script.js`.
- The asset `?v=` is re-stamped **after** the swap.

### 7.3 One description of a patch

`tools/make-patch-rules.py` extracts rules from `patch-engine.js` into:

- `tools/patch-rules.json` — readable, used by the Python reference applier;
- `tools/patch-rules.txt` — base64 lines, parsed in ~20 lines of C#.

The build-time JS engine and the runtime C# patcher read the same rules. Proof is
`DofusFarm.exe --bundle-selftest`:

```
rules: 23
matched: 0, 1, 2, 3, 6, 7, 8, 9, 10, 11, 12, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31
patched 5365280 chars, live 5365280 chars
RESULT: IDENTICAL
```

`IDENTICAL` means applying the shipped rules to the unpatched backup reproduces the
live patched bundle character for character. If a future build moves the code a rule
targets, that rule is skipped and the bundle is still adopted (a degraded patch set
still runs the game).

### 7.4 The 1.74.10 update (2026-10-27)

Two measured bugs had prevented the updater from ever succeeding:

1. **A reused `WebClient` loses its headers.** .NET applies `wc.Headers[...]` to the
   first request and empties the collection afterwards, so the second request went out
   with `UserAgent = null`; the origin answered 403 without the mobile UA. Fixed by
   setting the UA inside `GetWebRequest`.
2. **`Validate(staging)` demanded `index.html`.** A manifest update carries only
   `build/*`, so every successful download was rejected. Fixed to judge only staged
   files.

Live evidence: `patched staged bundle: 23/23 rules, 1/1 core`, `2 file(s) updated and
patched`, `asset URLs re-stamped: ?v=5e0ce3abcf`, `client_version.json` written, and
the footer changed from `Build v1.74.9` to `Build v1.74.10`, after which the same
guest press reached the world.

**Rule for the future:** a 403 from Ankama is evidence about *our request* until
proven otherwise. Reproduce with two clients (curl and .NET, fresh and reused) before
blaming the network.

---

## 8. Network layer

Three layers, each with one job.

### 8.1 Relay — `dofus-net-proxy.exe` (Go)

An HTTP proxy with `CONNECT` support that also caches the game's static assets.

**Routing.** The client resolves its socket against the page origin, so it asks for
`dofus.local:443`, a name that exists only inside the WebView2 mapping. The relay
rewrites it:

```
dofus.local:443  →  dt-proxy-production-login.ankama-games.com:443
```

**Egress.** Two upstream shapes:

- `socks5://host:port` — destination resolved locally before the handshake.
  Cloudflare's SOCKS cannot resolve Ankama's game domains (measured: CONNECT by name
  → code 0x04; by IP → 0x00).
- `http://host:port` (optionally with credentials) — plain CONNECT.

**Fail-closed.** If the upstream dies, dials are refused with an explicit error. They
are not retried direct. `-allow-direct` is the only opt-out and it logs every time.

**Cache.** `asset_cache/` holds every static asset plus a `.meta` sidecar, a
hard-linked mirror at `files/<host>/<path>`, a negative cache for deterministic
403/404s, neighbour-map prefetch, and an optional graph crawler. Measured 268 hits /
4 misses (~99% served from disk); ~9,700 files, 82 MB.

**Mods.** `<cache>/mods/rules.json` rewrites/blocks/patches CDN responses on the fly;
unpatched originals stay in the cache.

**Key flags:** `-port`, `-host`, `-upstream`, `-config`, `-dns`, `-cache`, `-quiet`,
`-log`, `-allow-direct`, `-token`, `-serial/-uuid/-model/-manufacturer/-ua/-gl-vendor/
-gl-renderer`, `-texture-size`, `-referer`, `-assets-url`, `-precache-maps`,
`-precache-workers`, `-prefetch-neighbours/-radius/-tiles-depth/-workers`, `-crawl`,
`-crawl-max`, `-crawl-rate`, `-crawl-tiles`, `-mods-dir`, `-login-host`.

### 8.2 Supervisor — `ProxySupervisor` (C#)

One owner for "how does traffic leave". In `auto` mode it:

1. builds candidates — userspace tunnel, configured upstream, every entry in
   `proxies.txt`;
2. probes each for real (fetch `https://api.ipify.org`, record exit IP and latency);
3. **rejects any candidate whose exit IP equals this machine's own address** — a
   transparent proxy is not a proxy;
4. ranks: local tunnel first, then lowest latency;
5. re-tests every 45 s and fails over;
6. on a switch, rebuilds the running relays on the same ports, so a live instance
   reconnects instead of showing "Server busy" until relaunch.

A bound SOCKS port proves nothing: `wireproxy` binds before the WireGuard handshake
completes. The tunnel is probed with a real SOCKS5 `CONNECT`.

### 8.3 Modes

| mode | what runs | touches Windows routing? |
|---|---|---|
| `auto` (default) | supervisor picks | no |
| `wireproxy` | `bin\warp\wireproxy.exe` → SOCKS5 | no |
| `warp` | installed Cloudflare WARP via `warp-cli` | yes, all of it |
| `custom` | `upstream=` or `proxies.txt` round-robin | no |
| `direct` | nothing | no, and your IP is exposed |

System WARP installs an adapter and rewrites DNS for the whole PC. When it drops, the
machine loses normal internet too. **Settings → Repair my network** runs
`warp-cli disconnect`, `sc stop CloudflareWARP`, flushes DNS, then probes
`auth.ankama.com` and `1.1.1.1:443` and reports.

`warp` is gated behind `allow_system_warp=1`; without it the engine refuses and falls
back to the local tunnel.

### 8.4 The one-address pin (anti-kick)

Three identities matter to the server, and all three are pinned:

| identity | where it lives | pinned by |
|---|---|---|
| exit IP | relay `lastGood` | chosen once per live session; replaced only after the session is gone |
| device serial + Android ID | `asset_cache/identity/<instance>.txt` | `LoadInstanceIdentity` |
| browser fingerprint | derived from the same seed (`mobile-disguise.js`) | same file (seed = `serial:uuid:model`) |

Four holes were found and fixed:

1. **Relay failed over mid-session.** The first guard only refused to move while
   `connsActive > 0`, which drops to zero during a reconnect. The rule is now about
   the session: while a game tunnel is moving bytes, the pin never moves; after
   60 s with no bytes, the session is gone and a new address is allowed.
2. **Background traffic moved the exit.** Edge/Skype telemetry won a race and
   re-pinned the egress. `isGameDestination()` now lets only `dofus.local` and Ankama
   hosts establish the pin; everything else races around it.
3. **Concurrent first dials picked different proxies.** `pinMu` makes the first game
   dial decide; the rest wait. The first dial uses the supervisor's pick.
4. **A hop that accepts CONNECT then carries nothing trapped the session.** Registration
   is no longer activity; a game tunnel is proven by the first byte from the far side.
   The client's ClientHello is held, sent to the pinned hop, and a hop that answers
   nothing is replaced before the tunnel carries the session. The page retries the
   login `config.json` GET twice (network errors and 5xx only).

`setPinnedUpstream` writes only when the pin is nil, so one session has exactly one
address. `recoverPin` is the only writer that can replace a pin, and it runs only when
no game tunnel has moved bytes for 60 s. The log says `session egress re-pinned` so it
is not mistaken for a move; `exit changed` no longer exists.

**2026-10-27 refinement:** page HTTPS traffic (`config.json`, `primus.js`) was counted
as a live game connection because `isGameDestination()` covers every Ankama host, so
it pinned a dead egress for 60 s. Now only the real socket (`dofus.local` /
`dofus.localhost`) counts as a live session; page HTTPS still uses the pin but cannot
preserve it. Liveness requires bytes **received from the server**, not a client write
into a half-open socket. If every hop is in the 30 s death cooldown, `pinCandidates`
retries the chain. The page suppresses only the exact "Server busy" alert and reloads
after 1.5 s, with a three-reload budget.

### 8.5 What the line actually does (measured)

Maroc Telecom, 2026-10-22, and the result changed within the same day:

| time | UDP to Cloudflare | what worked |
|---|---|---|
| 13:35 | dropped outright — WireGuard handshake never answered, MASQUE/QUIC timed out, even UDP DNS to 1.1.1.1 failed | only a TCP proxy (`proxies.txt`) |
| 14:41 | normal — handshake completes, exit `104.28.220.131` | the userspace tunnel |

`wireproxy`, `warp-plus` (WireGuard and Psiphon modes) and `usque` (MASQUE) were all
tested while blocked; none could carry a byte. Nothing in the code changed between
the rows — the supervisor re-probed and switched. A hard-coded "use WARP" or "use the
pool" would have been wrong in one of those states.

Other measured facts:

- Public proxy lifetime: **11 alive at t=0, 0 alive at t=60 s**.
- Cache hit ~1 ms vs proxy round trip ~600 ms (~600×).
- CONNECT through a public proxy ~3–4 RTT (~1.6 s at 400 ms).
- Pool: 25 entries, 1–5 healthy at any moment.
- A 4-deep chain (`http→socks5→socks5→socks5`) survived a dead first proxy.

**Consequence:** a session cannot outlive its exit. The pin makes the address constant
*for a session*; it cannot make it persist *across* sessions.

### 8.6 CloudFront / CDN facts (not bugs)

- `auth.ankama.com` is behind CloudFront's WAF. A datacenter/proxy exit can get
  `403 Request blocked / Generated by cloudfront` before Ankama sees credentials. The
  popup names the cause and prints the Request ID; the log emits `[OAUTH-BLOCKED]`.
  A residential/mobile TCP egress for the whole session is the verified shape; a phone
  hotspot on mobile data works.
- `dofustouch.cdn.ankama.com/maps/*.json` returns 403 for a missing key (S3
  `AccessDenied`), identical on a direct connection. 16 map JSONs returned 200 and 234
  returned 403 on identical paths. Not the proxy, not the relay.
- Historical "skype telemetry moved the exit" is fixed; `config.edge.skype.com` is no
  longer able to establish or move the pin.

---

## 9. AI architecture

### 9.1 The division of labour (permanent)

| layer | owner | examples | can a model own it? |
|---|---|---|---|
| **Legality** | code | tool registry (21), arg schema, `args_for_tool` | never |
| **Safety / recovery** | code | stop file, kick/disconnect recovery, `_farm_allowed`, repeat/no-op guards | never |
| **Knowledge** | data | quest graph, map graph, client-reported observed quests | never (content, not decision) |
| **Decision** | model | what next, which tool, which dialog reply, whether the world moved | yes — that is the project |

The model may only **rank legal candidates**. A rejected proposal is a refusal, and
refusals are supervision (`FAILURE_TO_SUPERVISION.md`), not noise.

### 9.2 The agent loop (7 stages)

`agent/autopilot.py::_run()`:

1. **observe** — `state.snapshot` (pure read, no wire traffic) then `flatten()` so the
   planner sees flat keys (`map_id`, `level`, `inventory…`).
2. **choose** — a policy picks a tool for the current objective.
3. **guard** — `validate_call()` (schema) + kick-signature guard.
4. **execute** — the directive is posted to the bridge.
5. **settle** — for world-changing tools, re-observe until the state changes.
6. **verify** — label `advanced` / `noop` / `failed` / `blocked`.
7. **decide** — `continue` / `retry` / `recover` / `replan` / `abort`; on the third
   repeat failure, `ThinkingLayer` proposes an untried, grounded alternative.

`max_steps=0` means unlimited (Full Auto); `steps_per_goal=8` is only the Order mode
budget. No wall-clock budget by default. Stops on goals met, no progress, `Ctrl-C`,
`--stop-file`, or `AUTOPILOT_STOP`. On exit it calls `safe.stop`.

Every step is appended with the observation before and after, the call, latency, and
label. `summary()` reports waits, keep-alive, identity, group refusals, braid rows,
etc.

### 9.3 Think-token pacing

The loop does not act the instant a call returns. A walk that returns `ok` has only
*started* the walk; judging it immediately scored every move as `noop` and retried an
action already in flight. Three timers fix that:

- **`--think` (default 0.25 s)** — after any world-changing action, the next decision
  is not taken before then. Read-only/listing tools are exempt.
- **`SETTLE_TOOLS`** — **five** tools since the interactive work:
  `{quest.advance, nav.goto, fight.join, farm.start, tap.interactive}`
  (`agent/autopilot.py:97`) — after
  `ok`, `_settle()` re-observes every ~400 ms up to `settle_s` (default 3.0 s) and
  judges the first observation that differs.
- **quiet window** — `quiet_s = min(0.8, max(0.4, think_s + 0.3))`: settled once the
  state is stable for at least that long.

`_action_complete` is the predicate. This is a dwell + settle re-observe, not a fixed
sleep.

### 9.4 Policy modes

`--policy` choices: `rule` / `encoder` / `planner`; **CLI default `rule`**.

- **rule** — deterministic `QuestPlanner` only. No model, fully inspectable. The
  shipped default.
- **planner** — `NNPlanner` hybrid: the ONNX `tool_policy.onnx` does `argmax` over 21
  tool logits, but the deterministic planner still owns argument filling and safety
  gates. A wrong model argmax cannot produce an illegal call. `--ensemble act|shadow|
  off` controls the 23-leaf decision-tree stack over policy + foresight + K-means state
  context. The default `act` is honoured only when `models/tool_stack.json` carries a
  passed cross-validation gate.
- **encoder** — the 24.9M text encoder via `ai/encoder_serve.py`; `--model-dir`
  defaults to `models/encoder30_v15`.

**The learned-vs-rule face-off was measured, then withdrawn (2026-10-27 → 10-29).** The
original number was `policy_faceoff.py --seeds 30 --steps 300`: learned wins **21, ties 9,
loses 0**, mean **66.10 vs 64.87 quests**. That comparison is **invalid** —
`KNOWN_GAPS.md` records that `policy_faceoff` scored on corpus seeds rather than a held-out
world, so any win built on it is void. The honest replacement is
`tools/bench_quest_policies.py`: rule **79.90** vs learned **80.50** quests (5W/5T/0L,
p = 0.0625), and the oracle variant **loses** (79.80, 3W/1T/6L). So the learned planner is
not a proven improvement: it is within noise on the corrected instrument, and it is a
distillation of the same teacher, so it cannot exceed it by much. The shipped default
remains `rule` at the CLI level pending a live benchmark.

### 9.5 The 28 tools (research vocabulary)

Runtime order is `data/capabilities.json` — **28 entries**, and `agent/regression.py:208`
asserts exactly that ("schema declares 28 tools" / "capabilities.json lists 28 tools"),
as does the ≥50-rows-per-tool check on the constraint corpus (`regression.py:509`):

```
knowledge.search  nav.goto    nav.follow   quest.list    quest.advance
farm.start        farm.stop   fight.join   fight.pass    inv.list
inv.sell          hdv.scan    hdv.list     hdv.buy       hdv.prices
inv.equip         tap.interactive          chat.say      state.snapshot
safe.stop         knowledge.remember       knowledge.forget   fight.state
fight.plan        fight.estimate           ui.tap        ui.icons    shop.buy
```

The 2026-10-27 research unlock added `hdv.buy`, `inv.equip`, `hdv.prices` and
`tap.interactive`; `FORBIDDEN_TOOLS` is now empty. `ui.tap`, `ui.icons` and `shop.buy`
arrived with the tutorial work (menu-bar presses, challenge/confirm controls, the
tutorial's fake shop) and are documented **nowhere except this list and the gate** —
`CURRENT.md` §7 and `CAPABILITY_REGISTRY.md` said 25 until 2026-10-30, which was true on
2026-10-27; both now carry 28. The shipped
49K tool policy still has 21 output logits, so the seven newest tools are
rule-planner-only until a new model is trained; the legal registry and the learned head
are separate contracts.

Tool backends:

- **local** — `knowledge.search`, `quest.list`, `knowledge.remember`,
  `knowledge.forget`, `fight.plan`, `fight.estimate`.
- **directive** — `nav.goto`, `nav.follow`, `farm.start`, `farm.stop`, `fight.join`,
  `fight.pass`, `inv.list`, `inv.sell`, `inv.equip`, `hdv.scan`, `hdv.list`,
  `hdv.buy`, `hdv.prices`, `tap.interactive`, `chat.say`, `state.snapshot`,
  `safe.stop`, `fight.state`, `quest.advance`, `ui.tap`, `ui.icons`, `shop.buy`.

`directive` means: the host posts JSON to `127.0.0.1:<bridge port>/directive`; the
page's handler performs it and reports back. The bridge port is read from
`bridge_port.txt` first; 8893 is only a fallback.

### 9.6 Deterministic planner and knowledge

`agent/planner.py` is the teacher. It reads the quest graph and produces legal tool
calls. Key behaviors:

- objective types and their required tool;
- `quest_id` as an entity reference;
- `DiscoverMap` uses only `map_id` (a `parameter0` is a text id, not a map);
- generic discovery walks an exit and prefers an unvisited neighbour;
- a tutorial gate blocks crossings/NPCs/cells the scenario forbids
  (`tutorial_blocked`);
- a stall retires a quest *in that situation*; a changed step/map/tutorial reopens it;
- permanent retirements (no tool can express the objective) are separate;
- named mobs stay named; a quests prompt does not farm unless the objective is a
  delivery;
- G31: only client-reported quests may be started;
- G33: a dialog offering a quest is accepted by `quest.advance {quest_id, accept:true}`
  only when the offer matches.

**Knowledge stores — two deliberately separate things:**

1. `knowledge.CanonicalStore` — static reference, built into
   `data/knowledge/dofus_knowledge.sqlite` (44,044,288 B = 42.0 MiB): 1,976 quests, 2,225 steps, 15,543
   objectives, 2,781 achievements, 9,173 achievement objectives, 2,000 monsters,
   6,472 NPCs, 21,554 items. All rows `source=dofusdb_cache`, `verified_live=0`.
2. `agent.knowledge_store.KnowledgeStore` — mutable discovery log with provenance,
   confidence, dispute, expiry (45 days), and a delete path. Only discoveries go in
   (never a copy of the cache). Sources are human > observed > derived > guess; a
   weaker source can only dispute a stronger one; three disputes drop a fact.

The planner reads the store, so a learned `npc_map` changes the next plan with no
retraining: `[quest.advance]` → `[nav.goto, quest.advance]`.

**Compact knowledge base:** 40.5 MB JSON → 3.0 MB on disk / 5.3 MB in RAM as compact
typed arrays plus one UTF-8 name blob (23× less memory than `json.load`).

**Observed quest catalogue (G29):** the page now publishes the client's own quest
catalogue (`quests_catalog` rows); `tools/ingest_observed_quests.py` persists one row
per objective to `data/knowledge/observed_quests.jsonl`, and the runtime loads it via
a stat-keyed cache. Live logs show 834–868 quests and 9,350–19,096 objectives per
character, 0 collisions. This is the replacement for the DofusDB-id mismatch (live
quest ids start at 3,841; the old cache stops at 2,546).

**DofusDB licence:** downloaded data/cache and the knowledge store are under a No-AI
licence. No re-syncs; capture from the client instead.

### 9.7 Command parser (offline, shadow-only)

`ai/train_command_encoder.py` freezes the 24.9M domain trunk and trains **0.29M heads**
(user-span adapter, intent, action conditioned on intent, BIO slot spans) on
`data/commands`: **1,619 train / 20 val / 23 test**, 13 intents.

Measured (`models/command_encoder30/metrics_eval.json`):

- 26 held-out base phrasings: **84.6% exact command vs 53.8% rule**; intent **96.2% vs
  69.2%**; slot exact 84.6% vs 69.2%.
- Per-row, including entity-swapped copies: test **intent 100% vs 56.5%**, exact
  **65.2% vs 34.8%**; val exact 70% vs 35%.
- Remaining base failures: Spanish knowledge query with a held-out entity
  (`busca Bouftou`), `all` for `arrête tout` / `para todo`, accented `d'équipe` span.
- A flat 0.25M word-level BiGRU overfits (train ~100%, held-out 30–45%) and is kept
  only as a negative baseline.

**It is not acting.** `parse_command` still defaults to the rule. `--command-shadow
auto` logs one `command_shadow` row per goal with both parses, agreement, and whether
the model would override; fail-open when torch/the artifact is absent. `--command-shadow`
is default off. `tools/command_shadow_report.py` compares over recorded logs:
**5 override goals / 5,717 uses** (the recorded totals are 7 real goals / 5,721 uses) —
this document used to say "2 override goals (4,300 uses)", which is the pre-adapter count
and contradicts its own §9.7; the accepted adapter maps all 7 goals to the expected tool.
The dominant "do all the quests" is rule `quest/list` vs model `quest/run` at 0.958.
`tools/session_report.py`
prints a shadow witness (rows, agreement, overrides). `tools/command_replay.py` pairs
goals with executed steps: 71 groups / 5,721 steps.

**Replay results:** the objective counter is flat, but 46/71 groups advanced by the
page verdict (25 stalled). Of the 10 groups the model would override, 9 advanced and
9 executed `quest.advance`. The model would change the planner's goal mode on **0/7**
real goals (6/7 agree; the only high-confidence overrides are quest list→run inside
the same mode). `intent_tool` now maps the parse to a registered tool; 9/10 override
groups executed the model's tool (`quest.advance`) versus 11/61 non-override groups
executing the rule's `quest.list`.

The 25 stalled groups are **24 short failures + 1 fight-start**, no read loops
(advanced groups are long, 65% reads). So the parser is not the stall fix.

**Routed per-instance adapter:** `router.json` (cosine, threshold 0.95) sends the 16
recorded user goals to fine-tuned heads and everything else to the base. All 7 real
goals map to the expected tool; the rule now routes `can you play the game ?` to Full
Auto (7/7 mode agreement, 5 quest action overrides / 5,717 uses). Base 43/43, adapter
16/16, 10-prompt canary row-for-row identical. Rollback: remove `router.json` or
restore `heads_base.pt`. The adapter guard includes `canary_accepts`, so future
per-instance updates cannot change list/advance/stop/sell/travel/follow.

**Deployable form:** `command_encoder_int8.onnx` (25,813,919 B, one graph with both
head sets) + `command_onnx.json` + tokenizer, loaded by `agent/command_onnx.py`.
INT8 parity is 0/59 effective rows; the torch-free wrapper matches torch on 0/76
texts. The FP32 graph/torch heads are excluded from the installer. `live_ready`
reports the ONNX shadow + router ready; the client bridge is the only blocker, so no
live session has used the hook yet.

**Rejected:** `plz/pls` + auto/farm data pass (fixed live prompts but cost three
held-out base rows: test exact 84.6% → 61.5%; reverted); shared intent+action and
intent-only fine-tunes (lost base rows 25/22 → 21/18 even with 160 rehearsal rows);
pooled whole-utterance token adapter (tags got worse).

### 9.8 Foresight and the ensemble

The v2 architecture bet is a **learned progress model** restricted to the observable
delta the client can report, used to rank legal candidates, plus an explicit
escalation decision when all candidates look flat.

Shape:

```
observation → objective_features() → 42 floats
            + candidate tool one-hot (21)
            → 2-layer MLP (~0.4–2.0M target; v1 is 31,865)
            → gain_hat[1..DEPTH], move_prob, flat_state flag
```

Training labels come from harness forks: `data/tool_sft` rows carry per-candidate
`gains`, per-depth `curves`, `margin`, `trivial`. The target is a fact about the
world, not the teacher's opinion. Live transfer is tested on real logged transitions,
with `moved` defined only from observable deltas, reads labelled `moved=false,
was_read=true`.

**Foresight v1 measured** (`ai/train_foresight.py` + `agent/foresight.py`, 31,865
params, 51 s CPU, JSON arrays + numpy, no torch/ONNX):

- Harness v2 corpus (17,039 train / 2,349 val): `gain_capture` **0.9679 vs teacher
  0.9660**; top-1 0.8641; move AUC 0.7489; 0 of 282 true flats called promising.
- Numpy-vs-torch parity: gain 0.0, move logit 1.2e-7.
- Stored v1 artifact metrics on a later corpus: 0.9553 vs 0.9433, top-1 0.8171,
  move AUC 0.965, 6,649 ranked states.
- **Live transfer fails:** move AUC ≈0.5 (artifact 0.3363 on 223 rows), ECE 0.92,
  predicted gains in the tens of thousands, 100% of rows outside harness range. Two
  causes: a zero-variance z-score bug (fixed; `std < 1e-3` treated as 1.0) and the
  harness not covering the live state distribution (level 3 / 0 kamas / no accepted
  quest vs a 20–100 curriculum).

**Capacity sweep (Kaggle GPU, matched compute 4,000 steps, batch 1,024, lr 3e-4):**

| hidden | params | harness capture | harness move AUC | live move AUC | live progress AUC | live ECE |
|---|---:|---:|---:|---:|---:|---:|
| 128 | 31,865 | 0.9779 | 0.9149 | **0.4125** | 0.5903 | 0.8238 |
| 512 | 321,401 | 0.9781 | 0.8909 | **0.2958** | 0.6330 | 0.8179 |
| 2048 | 4,428,665 | 0.9813 | 0.8803 | **0.4299** | 0.6729 | 0.7830 |

139× the parameters moved harness capture +0.0034 and made harness move-AUC worse,
while live move-AUC stayed under chance. **Capacity is not the bottleneck; coverage
is.**

**Coverage gap measured** (`data/tool_sft_v3`, 366 real states; 92% featurised):

| feature | harness range | live range | verdict |
|---|---|---|---|
| `quest_level` | 0.005–0.500 | **0.000 only** | harness never starts with no accepted quest |
| `kamas` | 0.500–1.000 | **0.000–0.042** | harness never starts poor |
| `n_params` | 0.333–0.667 | 0.000–0.667 | harness has no zero-parameter objective |
| `active_count` | 0.200–1.000 | 0.200–0.400 | partly covered |
| `last_tool` | 0.059–0.588 | 0.000–0.824 | live is wider |
| `level` | 0.005–0.500 | 0.005–0.090 | covered — the level ladder worked |

100% of rows remain outside the harness range; the missing diversity is the **initial
state** (wallet, accepted-quest state, zero-parameter objectives), not the level
ladder. Until `sim/world.py` seeds those, gate 2 must not be claimed.

**Move head trained on real labels** (`ai/train_move_head.py`, 12,281 params, 1.6 s):
val AUC moved 0.8942 / progress 0.8416; test AUC moved **0.685**, progress 0.508;
base rate 6.05% moved on test. This is the honest live-label model; it is not wired
into the acting policy.

**Tool ensemble** (`ai/train_stacker.py` → `models/tool_stack.json`,
`agent/ensemble.py`): stacks the 49K ONNX tool policy (`policy_logp`, `policy_rank`)
and the 31K foresight net (`gain_z`, `gain_rank`, `move_logit`, `curve_z`) plus four
K-means state-cluster distances. Three meta-learners are fitted on identical inputs:
linear listwise, depth-5 CART (23 leaves), and 40-round gradient boosting. Whole-
episode 5-fold CV on `data/tool_sft_v3/val` (1,385–1,500 states/fold, 107 episodes):

| ranker | gain capture | top-1 vs fork winner |
|---|---:|---:|
| tool policy (base) | 0.97734 | 0.90591 |
| foresight (base) | 0.97696 | 0.90429 |
| linear stack | 0.97663 | 0.90407 |
| gradient boosting (307 leaves) | 0.97735 | 0.90567 |
| **decision tree (23 leaves)** | **0.97905** | **0.91137** |

The tree is ahead of both bases in 3/5 folds: +0.00171 capture, +0.00546 top-1. It
passes the activation gate and is used by `--policy planner --ensemble act`; `shadow`
logs the choice, `off` disables it. The deterministic planner still fills arguments
and owns every safety guard. **Caveat:** harness-outcome evidence only; and the live
corpus it must eventually beat is now `data/transitions_v2` at **9,687 rows / 5,289
actions / 30.33% move rate / 1,322 claims met** (`label_version: 3`), so gate 2 is
unclaimed.

### 9.9 Verifier and controller (the second agent)

`agent/verifier.py` labels each step from three sources in priority order:

| source | when | what |
|---|---|---|
| **human** | a reviewer wrote a verdict | `verdicts_human.jsonl`, wins over both |
| **model** | `--verifier-model` points at an artifact | `verifier_head.onnx`; the GUI auto-loads only with a validation contract |
| **rule** | always | the baseline the model must beat; fallback for read-only tools |

Each label is `verdict` (`advanced` / `noop` / `failed` / `blocked`) + `residual_s` +
`drift` + `source`. Drift is three explainable contradictions: kamas dropped without
a purchase, the map changed on a non-movement tool, the observation lost fields it
had before.

`agent/controller.py` owns the objective, progress, a per-cause strategy table, and
failure memory. It returns a decision the loop must obey:

| decision | when | effect |
|---|---|---|
| `continue` | step advanced | ask the policy as usual |
| `retry` | first failure of a tool | same call once more |
| `recover` | second failure | a concrete different call (`state.snapshot`) |
| `replan` | `not_mapped`, 3 failures of one tool, or two fresh drifts | host/scheduler asked to replan |
| `abort` | no recovery left | the goal stops |

Failure causes are separated: `wrong_target`, `stale_state`, `incomplete`,
`invalid_assumption`, `plan_wrong`, plus `unknown`/`unavailable`. Drift on a stale or
identical observation is not counted.

**Verifier head status:** four artifacts exist (`verifier_head.{json,onnx,pt}` +
`_int8.onnx`), 33 features / 4 classes. The reported 1.0 validation accuracy is
circular (rule labels on rule features). Since 2026-10-27 the GUI auto-loads it only
if `models/verifier_head.validation.json` exists with `labels: live|human`, `n >= 60`,
`above_baseline: true`. No such contract exists, so normal launches run rules-only.
Read-only tools are never model-labelled regardless. An explicit `--verifier-model`
still overrides for an experiment.

### 9.10 Model inventory (what is actually loaded)

| artifact | size / shape | in the loop? | evidence |
|---|---|---|---|
| `models/tool_policy.onnx` + `.json` | 42 features → **21** logits, 49,365 params, val **0.8599** | yes, only under `--policy planner` | `planner_nn.py` argmax over 21 |
| `models/encoder30_v15/` | 24,874,131-param text encoder; `encoder_int8.onnx` 25.4 MB | yes, under `--policy encoder` | `ai/encoder_serve.py` |
| `models/command_encoder30/` | 24.9M frozen + 0.29M heads + routed adapter; INT8 ONNX 25.8 MB | shadow only | `agent/command_shadow.py`, `command_onnx.py` |
| `models/verifier_head.onnx` | 33-feature / 4-class | **gated off** | no validation contract |
| `models/foresight_v1/`, `foresight_live_v1/` | 31,865 + 12,281 params, JSON | off (measured, not wired) | `agent/foresight.py` |
| `models/tool_stack.json` | 21 KB, 23 leaves | with `--policy planner --ensemble act` | `agent/ensemble.py` |
| `models/encounter_selfplay.pkl` | PPO/PBT actor ~0.2M | yes, inside `combat_ai_server` | `live_policy.py` |
| `models/encounter_pbt.pkl`, `duel_value_v4_0.pkl` | combat experiments | optional | benchmarks |
| `networkhealth_artifacts/model.pt` | RTT/loss forecaster | **absent** | Sentinel falls back to telemetry |

No vision/image encoder exists anywhere. `encoder30_v15` is text-only; the
"vision inspector" in the Sentinel is regex over text.

### 9.11 Parameter budget (target architecture)

| piece | params | active per decision |
|---|---:|---:|
| frozen domain trunk | 24.9M | 24.9M (text rows only) |
| command heads (+ adapter) | 0.29M + 0.29M | one branch, routed |
| foresight net (target) | ≤2.0M | ≤2.0M |
| combat actor | ~0.2M | 0.2M |
| **total shared** | **≤30M** | **≤10M active** for the non-text path |

The trunk is only paid for on text (a prompt or NPC text). A pure state→tool decision
never runs it, keeping 12 instances inside the p95 budget. The earlier 70M and 400 MB
ceilings were withdrawn as unnecessary. No 100–135M dialog expert (rejected by the
user).

### 9.12 Data factory

| dataset | producer | rows | labels | limits |
|---|---|---:|---|---|
| `data/commands` | `tools/build_command_dataset.py` | 1,619 / 20 / 23 | intent, action, BIO slots | 26 held-out base phrasings; near-copies excluded |
| `data/tool_sft` | `sim/tool_choice.py` | 25,023 / 2,782 (v1); v2/v3 variants | per-candidate gains/curves/margin/trivial | harness world |
| `data/tool_sft_v3` | regenerated 2026-10-27 | 14 MB | same | low-level ladder still not the coverage gap |
| `trajectories/*.jsonl` | live pages | **29 files / 236,961 rows** (re-counted 2026-10-30; the root `trajectories/` adds 3 files / 2,858 rows) | none until transitions | the 4.5% move rate and `objectives_completed = 0` were label bugs (G42/G43/G28) |
| `data/transitions/` | `tools/build_live_transitions.py` | 5,510 rows (superseded) | moved/progress | 14.0% moved / 1.5% progressed was the **broken** label; see `transitions_v2` |
| `data/transitions_v2/`, `data/transitions_temporal/` | the same builder, post-G43/G60/G65/G68 | **9,687 rows / 457 segments / 5,289 actions** | claims per form | 1,322 claims met, `label_version: 3`, 8.05 h span |
| `data/auto_live/` | `tools/auto_live.py` | unattended session output | outcomes | new, uncommitted |
| `data/command_adapters/user_goals.jsonl` | hand-labelled live prompts | 16 | intent/action | per-instance, may never enter base train |

**Live transitions (target, being built):** `(obs_before, tool, args, obs_after,
moved)` from consecutive `kind:"step"` rows in one session, keyed by instance and ts.
`moved` is the OR of observable deltas only: map, x/y, dialog presence/reply_count/
reply_id, objective `stepId`/`finishedCount`/parameter counters, `in_fight`, group
ids, inventory/kamas. Reads are `moved=false, was_read=true` so a model can never earn
credit for looking.

**Harness integrity findings (2026-10-27):**

1. The counterfactual factory was generating **zero rows** and exiting 0. Cause: G31
   only lets the planner start a client-reported quest, and the harness reported
   nothing (no `dialog` block), so every curriculum id was skipped. Fix (sim side
   only; guard not weakened): `sim/world.py` publishes the offer (`offer_here()`,
   `observe()` emits `{"open": true, "start_quest": id, "npc_id": …}`), and
   `place_at()` starts episodes where an offer exists. `_parallel()` now fails loudly
   on a zero-row run. Gated by `test_counterfactual_factory_alive`.
2. `QuestPlanner.focus` meant two things: an objective type (0–17) for the harness,
   and a quest id for the offer guard. The guard now consults `focus` only when it is
   not a member of `OBJECTIVE_TYPES`.
3. Feature-width drift: the Oct-22 `tool_sft` corpus was 41 features; runtime is 42.
   `ai/train_foresight.py` infers the width, refuses a ragged file, and refuses a
   stale width with the regeneration command. The regenerated corpus is
   `data/tool_sft_v2/`; the Oct-22 corpus is kept.

### 9.13 Training and Kaggle

- Pipeline: `prepare_encoder_sft.py` → `build_core_sft.py` → `train_from_scratch.py`
  / `thx_finetune.py` → `export_encoder.py` (ONNX + INT8) → `release_gate.py`. The
  gate refuses an export whose INT8 output differs from fp32.
- Training runs offline on Kaggle (account `tajdinenizar`; credential in
  `~/.kaggle/kaggle.json`, never logged). `--resume` continues from a checkpoint and
  corrects `planned_steps` so the schedule does not restart.
- Interactive GPU notebook (T4×2/P100) is preferred; the toolkit is synced by
  `tools/sync_kaggle_toolkit.py`. TPU is used only when a measured speedup justifies
  it.
- Keep outputs small (a 134 MB output broke `kaggle kernels output` twice; the
  artifact is the model).
- Encoder training: 3,000 steps, 987 s on the reference GPU; early stop on **call**,
  not tool (tool saturates at ~0.94 while call still moves).

**2026-10-28 constraint corpus / cause head (measured, not promoted).** The generator
writes 2,600 rows (25 tools × 8 causes × 13 families — the registry was 25 when that
corpus was cut; it is 28 now and the gate requires ≥50 rows for each of the 28) and the
controller routes 100%
cause / 100% action. `ai/train_cause_head.py` was trained on 9 families/cause and
validated on 2 unseen families/cause over five splits: the MLP alone has mean
accuracy **0.81** / ECE **0.19**; the MLP + cosine-kNN ensemble has mean **0.855** /
**0.089** (single seed 0.855 / 0.077, one weak fold 0.807 / 0.163). **The gate-pass
claim is withdrawn:** 50/400 validation inputs duplicate fit and calibration inputs;
excluding them gives 292/350 = **0.8343 accuracy**. Reloaded FP32/INT8 artifacts
reproduce the old score, but this is not independent validation. Eight new tests
pin input/family leakage rejection before training or export. Artifacts remain
**unpromoted and unloaded**; applicable constraint scenarios, live failure data,
independent evaluation and repair-regret proof remain open.

---

## 10. Combat AI

### 10.1 Data and damage math

- Official class-spell database: 15 classes, 22 spells per class, 330 unique spells,
  1,980 spell levels, 2,040 parsed normal/critical damage lines, 204 healing lines.
  AP, range, restrictions, cooldowns, target limits, AoE, descriptions, source URLs
  retained. All 1,980 levels pass structural validation and solver-adapter conversion.
- Built from the official encyclopedia supplied by the user; the 17 common spells
  were not on those pages and were **not invented**. Official IDs differ from parts
  of the 1.74.9 local cache; the datasets remain separate.
- `combat/damage_calculator.py` order: scale base roll by characteristic + Power →
  add flat bonuses → subtract fixed resistance → apply percentage resistance →
  subtract armor. Integer arithmetic; player percentage resistance capped at 35%;
  monsters/summons not incorrectly capped; critical fixed resistance only on crits;
  crit chance = base + Critical Hit stat clamped 1–100; healing scales with
  Intelligence/Heals, not Damage/Crit Damage; healing no longer inflates offensive
  rotation damage.

### 10.2 Solver (`combat/battle_solver.py`)

Geometry and rules ported from the 1.74.9 client, not guessed:

- cell id → map point: `t = id % 14 - id // 28`, point `(t + 19, t + id // 14)`; all
  560 points unique;
- fight walkability: `(cell.l & 5) === 1`;
- fight movement cardinal only, one MP per step (the bundle's `sqrt(2)` diagonals are
  overworld pathfinding, not legal fight moves);
- line of sight: client Bresenham variant; actors block every line cell except the
  target; every line cell needs sight bit 2;
- spell ranges are rings: diamond by default, cross for `castInLine`, diagonals for
  `castInDiagonal`, over `minRange..range`;
- effect IDs: 96–100 damage water/earth/air/fire/neutral, 91–95 steal, 108/143 heal,
  81 heal;
- rolls mirror `getMinMaxDamageFromRawEffect`: `diceNum` min, `diceSide` max, zero
  `diceSide` = fixed.

The solver generates legal move-and-cast nodes only when AP, MP, range, LOS,
occupancy, castability, per-turn/per-target limits, cast interval, and cell
restrictions permit. It prices damage/healing through `DofusDamageCalculator`,
including the resistance order, 35% player cap, shields, crit rolls, crit chance.
It runs a one-turn beam search (width 16) rewarding effective damage, lethal kills,
and effective healing, caps damage at remaining HP, prefers not to spend MP when
already in range, and reports unmodelled utility/area spells in `notes` instead of
treating them as no-op damage. It returns a plan only; it never casts, moves, ends a
turn, or sends a packet.

Known limits: only direct single-target damage/steal/heals are scored; buffs, traps,
glyphs, push/pull, AP/MP removal, AoE footprints are not planned; `max_cast_per_fight`
is exposed but not enforced; team sides need live confirmation; crit chance and the
35% vs bundle's `MAX_RESISTANCE: 50` need live verification.

`fight.plan` exposes the solver through the tool contract. `fight.state` is a
zero-argument, read-only directive with the board fields the solver needs. Both are
kept out of the verifier's width-locked head because `agent/verifier.py:82`
`TOOL_ORDER` is **16 entries** and the head's full input is 33 features / 4 classes
(`advanced`, `noop`, `failed`, `blocked`) — appending a tool class would break the
current ONNX head.

### 10.3 Live pipeline (`:8891`)

Three stages, each optional, fixed order — the solver defines legality, learned parts
only rank among legal plans:

1. `battle_solver.py` — exact beam search over legal turn plans (width 16).
2. `plan_value.py::rank_plans()` — a learned ranker that reorders the solver's legal
   plans; it cannot invent an illegal one.
3. `live_policy.py` — the PPO/PBT actor, realised onto solver-legal plans, served as
   `POST /v1/combat/decide`.

The actor is `models/encounter_selfplay.pkl` (~0.2M), started by the autopilot through
`combat/brain_service.py` (`--brain auto|off`). The loop records a `kind:"brain"` row
(`started`/`reused`/`no_interpreter`/`port_held`) so "who decided the turns" is a
field, not an assumption.

### 10.4 Measured status

- Offline: 50 battle-solver tests passed; 60 combat/data tests; solver self-check.
- All 156 recorded `fight.state` boards initially failed the actionability contract
  because the page read characteristic-sheet field names (`max_hp`, `ap`, `max_ap`,
  `mp`, `max_mp`, `initiative`) that are not in the served bundle. The page now reads
  `effectiveMaxLifePoints`, `actionPoints`/`maxActionPoints`,
  `movementPoints`/`maxMovementPoints`, `initiative` for monsters and the character;
  the gate pins that no name outside the bundle can be read again (G25).
- With the missing max values supplied, all 64 of the model's own turns become
  actionable and the trained actor answers **18 of 64 with a cast** at p50 3.3 ms —
  inside the page's 4 s fetch budget. Offline measurement on real geometry.
- **Live fights are now proven, calibration is not.** `executor.py` still records
  `live_calibrated: false` and `models/fight_calibrator.json` is absent, so `p_win` is
  null and unattended fights are refused by design. What changed on 2026-10-28/29 is the
  proof itself: 9 `won: true` outcome rows over `fight_id` 124, 900, 1356, 1358, 1359,
  1482, including one entered by `quest.advance` (§2.2). The battle-log ring
  (`2026-10-28w`) has still never been read out of a live end message, so those wins
  carry no damage sample.
- **30M 560-cell grid scaffold:** `combat/grid_model.py` is **30,766,081 params**
  (560 cell tokens, 16 fighter tokens, 16 plan tokens) and its forward pass is finite;
  `combat/test_grid_model.py` is 3/3. It **is now trained** — `models/combat_grid/model.pkl`
  (123,068,683 B, `hidden` 768, 4 epochs) scores val top-1 **0.7857** on 28 rows and test
  top-1 **0.60** on 40 rows (CE 2.3997). The earlier "not trained" line was true while the
  156 recorded boards were all pass-only, and `combat/grid_probe` rightly refused them; the
  distillation corpus `data/combat_grid_synth_v2` (1,872 boards → **1,318 actionable**,
  554 pass-only, 1,114/84/120 split) came from live `fight_boards.jsonl`.
  `combat/grid_dataset.py` is the tensor builder. It is **solver distillation on synthetic
  labels, not live-verified** — `live: false` in that manifest — and `--ranker grid` is
  opt-in and fails open.
- **30M Kaggle synthetic run:** 30,766,081 params trained on 440 solver-labelled
  boards from the real maps (372/28/40), 4 epochs, GPU. Val top-1 **0.786**, test
  top-1 **0.60**, test CE 2.40; artifact `models/combat_grid/model.pkl` (117 MB).
  This is solver distillation, not live combat; the live fight / side-swapped win
  gate is still owed. It is opt-in as `fight.plan ranker=grid` (default `solver`,
  read-only, fails open when the board has no positive AP or the artifact is absent).
- **Entity-pointer Kaggle runs (two, neither promotable):** `ai/train_entity_pointer.py`
  (hashed entity/quest encoder + pointer CE + move/progress heads) was trained on the
  4,017-row live corpus. v1 (`thx-05-entity-pointer`, 8 epochs): top-1 0.3778, moved
  AUC 0.4257 (base rate 17.8%), progress AUC null. v2 added the client's own 22 observed
  step descriptions (`thx-08-entity-pointer-observed`; 4,016/4,017 rows carry
  `observed_hint`), actions only 1,003/28/53, 20 epochs, hidden 256: top-1 **0.0357**
  val / **0.0377** test, moved AUC 0.434/0.362, progress AUC null. The hashed-entity
  scaffold and 1,003 action rows are the limit; the next run needs a live corpus with
  `tap.interactive`, `inv.equip`, `hdv.buy`, `hdv.prices` exercised.
- The fallback fight planner (`_planFightAction`) is what the server has actually seen.
  It is now behaviorally tested (10 checks): targets lowest-HP enemy, respects min
  range, filters unaffordable/refused spells, ignores buffs/heals as attacks, moves
  toward the nearest enemy with the client pathfinder, passes with no MP.
- `fight.estimate` gives turns-to-kill, incoming damage, expected HP loss, missing
  inputs, and `p_win` (null until ≥20 resolved live fights with wins and losses; ECE
  gate 0.10). Unattended fights are refused by design while `p_win` is null.

### 10.5 Ladder and self-play

- `combat/duel.py` — 1v1 duels on real map cells with real class kits.
- `combat/normal_build_duel.py` — symmetric level-200 normal builds, 12/6/6 caps,
  Temporix excluded.
- `combat/encounter/` — batched JAX env (AP/MP, ranges, resists, heals, initiative,
  team turn order), shared policy/value head, PPO self-play, scripted first opponent,
  sim-label exporter.
- `kaggle/tpu_selfplay/` — two-phase TPU kernel (heuristic bootstrap, then self-play)
  and a self-contained dataset builder.
- 64-row CPU smoke produced a silver calibrator at ECE 0.0299, which proves the
  pipeline but is **not** a live-calibrated probability. Sim rows carry
  `source: "sim"`; the default calibrator must remain a live artifact.
- A specialist ranker may rank solver-generated plans later, but it must not invent
  spell IDs, cells, or illegal actions.

---

## 11. Supporting packages

### 11.1 `advisor/` — read-only character advisor

Joins the live character report, the official class-spell DB, the Touch item/set dump,
and the client's price cache. Picks an element from spells the character can actually
cast, allocates grades under the client-derived point cap, scores gear and set
acquisition (buy/craft/farm), and ranks market buy/sell signals.

Entry points:

```bash
python -m advisor capture
python -m advisor report
python -m advisor selftest     # 91 `check(` sites, knowledge repo only
```

It never spends points, equips, buys, or sells. Live validation on a logged-in
character is still pending.

### 11.2 `planning/` — cost model and search

- `economy.py` — everything converted to minutes; `kamas_per_minute(player_level)` is
  the pivot: best `sum(dropped value × drop rate) × kills/min` over monsters you can
  beat. Buying and farming become comparable; `min()` does the rest.
- `planner.py` — AND/OR search over the game database:
  `acquire(item,qty)` → buy (kamas) OR farm (drops, level ≤ player+20) OR craft (AND
  over ingredients, recursive, cycle-guarded, depth 3) OR quest fallback.
- `bandit.py` — Thompson sampling over strategies.
- `schedule.py` — runs a plan, watches the clock, replans on `level_changed`,
  `cheaper_branch`, `drop_rate_drift`, `overrun`, `stale`.
- `trace.py` — residual percentiles, verified vs unverified.
- `experience.py`, `graph_memory.py`, `outcome.py`, `thinking_cache.py`, `kb.py`.

A level-1 character asking for a level-200 item gets a real answer: *buy it in 369
minutes by farming Boufton Pâlichon at 113 kamas/min*, with the rejection line
(*crafting would take 3,318 minutes*).

### 11.3 `navigation/`

`world_graph.py` (walkability/map graphs), `quest_graph.py`, `resource_graph.py`,
`nlp_intent_engine.py`. `nav.goto` name→map resolution is answered by the Sentinel.

### 11.4 `networkhealth/` — RTT/loss forecaster

Causal TCN `NetworkHealthTCN`, per-horizon heads for log-RTT,
RTT variance, jitter, loss, degradation logit, plus one anomaly score shared across
horizons. The default `ModelConfig` is now `hidden=96, levels=5, kernel_size=3,
use_identity_mask=True` with `architecture` selectable between `"tcn"` and
`"trm_moe"` (`num_experts=4, top_k=2`), which instantiates **144,016 params (0.576 MB)** —
this document's "52,816 params, 0.20 MB" is the pre-identity-mask model. Either way the
forecaster is **dark**: `networkhealth_artifacts/model.pt` does not exist, so nothing is
served (§2.4, G5). `networkhealth/inference/` is a package, not `inference.py`.

Pipeline: `collectors/` (RIPE Atlas), `clean.py` (schema/timestamp/plausibility,
dedup, failure preservation), `features.py` (windowing, `TARGET_BLOCK`), `splits.py`
(time + series split with purge/gap), `baselines.py` (persistence/MA/EWMA/seasonal
naive), `training.py`/`evaluation.py`, `inference.py`
(`MultiInstanceNetworkHealth`), `bridge.py` (polls relays 8880–8883), `sentinel.py`
(port 8890), `provenance.py` (sha256 of every input/output).

**Historical measured artifacts (RIPE run 2026-10-17):** 156,748 observations, 143,049
examples, 103 series, 66 probes, seq 128, horizons 1/3/6; log-MAE 0.4654 / 0.2296 /
0.2684; 2.163 ms median (p95 2.718 ms). Baselines were computed on a different split,
so the report leaves cells `n/a` rather than mixing splits. The report warns that
public RIPE data cannot characterise the game client.

**Local run (Phase 5):** 11,066 observations / 12 series, 0.5 s cadence, split by
series; log-MAE h1 **0.1464**, live forecast 56.3 ms vs actual 63.3 ms. The RIPE
checkpoint was 0.3597 and failed on fast pings (log-gap feature out of distribution).

**Current status: dark.** `sentinel.py` imports torch (no torch in the shipped
runtime); `networkhealth_artifacts/model.pt` is absent; the architecture changed from
BatchNorm to GroupNorm(1,C) and SEQ_LEN 128→32, so old checkpoints are refused rather
than loaded with `strict=False`. Treat the network verdict as telemetry, not
prediction (G5).

### 11.5 `sim/` — the offline harness

`world.py` (symbolic world; noise, map preconditions, priced travel), `tool_choice.py`
(counterfactual tool corpus, fork per candidate), `decisions.py` (repair corpus),
`run.py` (planner | nn | random | rule), `rollout.py`.

The harness is the gym, not proof: it is built from the teacher's own rules, so it can
teach noise but not persistent failure. It is exhausted as a source of *repair*
learning; its remaining value is counterfactual tool/args data and curriculum coverage.

### 11.6 `pipeline/` — THX-01 path

`train_thx01.py`, `serve_thx01.py`, `benchmark_thx01.py`, `dofus_eval.py`,
`data_filter.py`, `kaggle_encoder.py`. The THX-01 lane was killed as a deployment
path; its benchmarks are superseded. Do not revive without a new measured reason.

---

## 12. Measured results (the numbers that matter)

### 12.1 Live baseline before the 2026-10-26 fixes (`BASELINE.md`)

```
trajectory view : 4 sessions, 57.0 min wall, 4012 steps, 3982 ok, 30 failed, objectives +0, maps 9
autopilot view  : 7 sessions, 40.6 min wall, 4287 steps, 3770 ok, 517 failed, objectives +0, maps 15
                  recoveries: recover 523, replan 407, retry 21
combined span   : 8 sessions, 172.5 min total, longest single session 125.1 min
verdicts        : advanced 3063, noop 707, failed 493, blocked 24
productive view : of 1,277 world-changing steps, 58 moved it (4.5%)
                  quest.advance 32/972 (3.3%)   nav.goto 26/222 (11.7%)
                  farm.start 0/59              fight.join 0/24
                  (3,005 of 3,063 advanced verdicts are state.snapshot reads)
decision causes : none 3063, unknown 455, plan_wrong 381, incomplete 361, unavailable 26
disconnects     : INACTIVITY 5106, (in_world:false with no reason) 142
failed by tool  : quest.advance 504, fight.join 48, nav.goto 25, state.snapshot 15, farm.start 3
```

Farm counters: of 9,164 rows carrying `farm_session`, 8,797 show `fights: 0`; the
maximum ever reached was `{"fights":2,"wins":0,"losses":2,"kills":0,"xp":0,
"drop_count":0,"drops":{},"kamas":2,"minutes":0}`. Kamas went 418 → 0 (peak 1,773):
net -418. `farm.start` succeeded 78/78 with identical args
(`{monster: 698229, kill_count: 100, auto_sell: false}`), i.e. accepted but inert.

This baseline predates every 2026-10-26 fix. It is a valid *before* picture and must
not be compared against anything except a re-run of the same commands.

### 12.2 Latest live sessions (four slots, rows through 2026-10-29)

See §2.1 for both per-instance tables. Totals across the four **autopilot** logs:
58 sessions, 668.4 min wall, **4,003 steps** (3,603 ok / 400 failed), **194 manual
interventions**, and 230 retry / 1,385 recover / 801 replan — i.e. 55% of the step
budget is still spent recovering or replanning, and one slot (dofus-05) accounts for
140 of the 194 interventions. The newest page build those rows ran is `2026-10-29bf`;
the source is `2026-10-30a`, which has not run yet, so these numbers do not test the
interactive-use fix (§2.4).

### 12.2b The outcome-label research programme (G41–G69, 2026-10-29/30)

One corpus, rebuilt as each defect was found: `data/transitions_temporal` /
`transitions_v2` at **9,687 rows / 457 segments / 5,289 actions / 1,322 claims met**,
`label_version: 3`. The headline measurements, all from
`python tools/live_outcome_probe.py`:

| finding | number | consequence |
|---|---|---|
| labels validated on their own training days (G41) | hash-fold AUC 0.830 → temporal 0.634 | every earlier "the model learns" claim was leakage |
| the label counted walking (G42) | 636 "moved" of 2,848 advance calls, **318 changed only the cell** | label redefined to a state change with direction |
| the label read the *pre-action* snapshot (G43) | moved 1,030 → **1,863** (19.47% → 35.22%), claims 345 → 788 | observation pairing fixed; corpus rebuilt |
| context features predict nothing inside a tool (G44) | mechanism block 0.856 vs tool-rate floor 0.723 was an illusion; `state_only` 0.724 | the "state" story was a tool-identity story |
| travel is the weak primitive (G45) | direction form 185/1,804 map changes (10.4%) vs map form 268/338 (74.3%) | travel = adjacent hops, not free jumps |
| the loop acted on stale state (G47) | 4,153 of 4,344 steps (95.6%) carried state; median age 707 ms | freshness rule, not a model |
| repeating a failed call is the dominant failure (G48) | after 1 miss 22% repeat, after 2 → 0/18, ≥3 → 1/41 (2%); streak AUC 0.851 | the veto's signal |
| `repeat_streak >= 2` is a shippable veto (G51/G54) | vetoes 11–18% of calls that meet ~2–3%, keeps **99%+** of successes; replicates across 4 rolling cuts (nav AUC 0.939/0.753/0.655/0.853) | shipped as `agent/gate.py`, **shadow-only** — it annotates, it does not refuse |
| progress, not the gate, is the constraint (G53) | 100 quest advances = 9,692 actions ≈ 15.4 h of action time ≈ **four days** of sessions | the case for live volume |
| a learned ranker is identical where it would ship (G56/G59) | forest 0.849 AUC vs rule 0.683 *ranked*, but at the shipping point both keep 99.4%; frozen forest on 10-29 nav tied 0.899 vs 0.899 and lost on quest (0.691 vs 0.664, CI −0.80..−0.10) | nothing shadowed |
| the world-model encoder route is dead (G57) | encoder 0.571 vs hand-built 0.658; best combined 0.647, rule 0.778; bar was +0.03 | revisit at ≥40 test sessions |
| per-form calibration is not a better picker (G66) | at the counter's 168 vetoes: keeps 0.611 vs 0.678, CI (−0.121, 0.012); its wins come from vetoing 1.5–3.7× more | equal-budget comparison is now mandatory |
| the state block has no headroom on kept calls (G67) | `quest.talk` kept AUC **0.472**, `quest.reply` **0.398**; the 5%/10% layers remove calls that all met | no worker ships |
| refused calls were credited as met (G65) | 945 refused steps, **63 labelled `claim_met: true`**; after the fix advance is 452/1,942 = 23.3% (was 26.3%) | `label_version: 3` |
| the gate's call identity was volatile (G60) | 3 taps of element 513208 with different `skill_uid` = 3 rules, streak never reached 2 | normalized key in `agent/gate.py` |
| the veto's alternative was never priced (G70) | temporal test: 168 candidates at streak ≥ 2, 10 met (5.95%), retention if wired **0.9832**; the next *different* call met 61/134 = **0.455**, the next *same* call 0/30 = **0.000**, difference **+0.455** CI (+0.304, +0.603). hash test: 280 candidates, retention **0.9688**; 46/72 = 0.639 vs 1/195 = 0.005, **+0.634** CI (+0.296, +0.872) | the veto is now costed on **both** sides; G55's 76 % was a label artifact, and causality still needs the epsilon arm |

**One methodological correction that reaches back into this table.** The mutation driver
ran `test_kaggle_toolkit_sync` *inside* every mutated section, and a mutation edits a
mirrored source file — so mirror drift fails for every mutation and any batch reads "CAUGHT"
no matter what the checks think. It is out of the snippet now (still in the gate; the driver
`--check`s before and after). The exposure is bounded by where a mutation lands: the mirror
covers `dofus-network/` only, so the `calib:` 7/7 and `worker:` 2/2 verdicts quoted in §21
(targeting `tools/live_outcome_probe.py`) may have been witnessed by drift rather than by
their own checks and need a re-run, while `use:` (into `scripts/mobile-disguise.js`) and the
C# mutations named in §5 were never visible to the drift check and stand as recorded. The
G70 batches were run
after the fix: the threshold mutant is caught **by name** (6 failing checks, including
`test_veto_alternative_priced`'s own labels), and one mutant — counting reads as acting
calls — was legitimately **MISSED**, because the corpus already gives a read no `claim`, so
the filter is redundant and the mutant is equivalent.

The promotion bar the programme ended with (constants in the probe, prose in
`docs/TRAINING_PLAN.md`): `PROMOTION_RETENTION = 0.95`,
`PROMOTION_MIN_COUNTER_VETOES = 10`, `PROMOTION_DELTA_POINTS = 0.05`, ≥3 of 4 rolling
cuts, and a form must have ≥400 met rows over ≥3 start periods — **and, since G71, the
"five points" in that sentence is actually enforced**: `_promotion_ok` previously required
only a positive bootstrap interval, so the point floor was decoration. **No form qualifies
today**, which is the measured reason no learned worker is trained.

### 12.3 Encoder (text policy)

| checkpoint | val tool | val call | gold tool | gold call |
|---|---:|---:|---:|---:|
| v11 | 92.9% | 68.5% | 87.3% | 71.8% |
| **v15** | **93.9%** | **69.8%** | **94.4%** | **77.5%** |

INT8: 25.4 MB, 62 ms/call, zero accuracy delta vs fp32 on the serving path.

### 12.4 Tool policy

49,365 params, hidden 192, depth 2, val **0.8599**, teacher agreement 0.857, lift
+0.003, macro-F1 0.2001 (common-class 0.5252), ECE 0.0304, best epoch 50. Per-class
recall is strong for the four common tools (`nav.goto` 0.825, `quest.advance` 0.956,
`farm.start` 0.847, `fight.join` 0.989) and zero for never-selected tools.

Paired simulator benchmark (corrected instruments, noise + travel cost + nearest):
at level 20 72.75 vs 72.08 installed (+0.67, 8/12 wins); level 60 83.58 vs 82.33
(+1.25, 11/12); level 100 repeats level 60 because quest availability saturates; at
800 steps 140.0 vs 138.5 (+1.50, 7/8 wins). The model is installed at
`models/tool_policy.onnx`; the previous 16k model is archived. Still DofusDB-
structural, not Touch-fidelity.

### 12.5 Combat

- Official DB: 330 spells / 1,980 levels.
- Solver: 50 tests, 1,980/1,980 levels convert.
- Live policy over 64 recorded turn boards: 18 casts, p50 3.3 ms.
- Live fight: **proven** (was "not proven" until 2026-10-28). `fight_outcomes.jsonl`
  carries 22 rows, 9 `won: true`, over `fight_id` 124, 900, 1356, 1358, 1359, 1482 —
  all inside one session (`7a871278`), and 1482 was entered by `quest.advance`. The
  calibrator is still absent, so `live_calibrated: false` and unattended fights remain
  refused by design.
- The trained **move head executes live** (approach / retreat / hold) and the trained
  critic ranks legal realizations when its inputs are complete; neither has a boss
  benchmark, and the `2026-10-28w` battle-log ring has never been read out of a live end
  message, so the damage numbers of a won fight are not yet in the corpus.
- The root `trajectories/live_policy_decisions.jsonl` now has **810 rows** (last:
  `fight_id 1482`, round 0, `my_id 4434148`), not the 14 this document used to report.
  The baseline's first 8 rows did not come from the page, so treat the file as instrument
  data, not a win rate — the win rate lives in `fight_outcomes.jsonl` (§12.5).

### 12.6 NetworkHealth

See §11.4. Best honest local number: log-MAE h1 0.1464 on this line, 12 series,
0.5 s cadence. Not currently served.

### 12.7 Knowledge / data size

Compact knowledge artefact measured 2026-10-30: `data/kb/dofus_kb.npz` (250,560 B) +
`data/kb/dofus_names.bin` (2,700,874 B) = **2,951,434 B ≈ 2.8 MiB**, which matches the
"3.0 MB on disk" claim; the 5.3 MB RAM figure is a runtime measurement and was not
re-taken. The **"40.5 MB JSON" source no longer exists as one file** — `data/cache/*.json`
now totals 32,626,819 B with the largest members `items_full.json` (24.6 MB) and
`quests_full.json` (8.0 MB), so quote that figure as historical. SQLite spine
44,044,288 B; observed quest catalogue up to 21,624 objectives on one character (§2.1);
asset cache 103.6 MiB with the ~99% hit rate measured on the 2026-10-27 run.

---

## 13. Open gaps (G1–G71)

This is the project's own gap register, condensed. The full evidence is in
`dofus-network/docs/KNOWN_GAPS.md` (6,698 lines / ~299 KB). Read the numbered entry before
touching the area.

| # | gap | status |
|---|---|---|
| G1 | AFK disconnect is the client's own 5/10-minute timer; keep-alive never reached it | **fixed 2026-10-27, measured live** (capture of the tracker via `setInterval` owner; marks/age/inactive now in every snapshot) |
| G2 | Equip: preset path shipped | `inv.equip` applies a saved inventory preset (`InventoryPresetUseMessage`); direct `object_uid` equip is refused until the live capture proves a path; live preset proof owed |
| G3 | Dialog question text now consulted | interim heuristic; talk path is where the run loses most points |
| G4 | No vision/image encoder | open by design |
| G5 | Network-health forecaster dark | torch import + missing checkpoint; ONNX port + retrain owed |
| G6 | Verifier head wired but unmeasured/circular | GUI auto-load gated on a live/human validation contract; no contract → rules-only |
| G7 | Live combat unverified | open; fallback planner now behaviorally tested; brain auto-start wired |
| G8 | Test gate was deleted; partially restored | `agent.regression` **1,540 checks, 0 failed, exit 0** (re-measured 2026-10-30 on build `2026-10-30a`, after the session_report, mutation-journal and preflight checks landed); combat suites, sim floor, 40-instance daemon, C# compile and live toolkit drift are gated; C# selftest/release still outside |
| G9 | Identity keyed by slot, not account | fail-closed binding shipped 2026-10-27, offline-verified; live relogin proof owed |
| G10 | `kaggle/toolkit/` drifts with no generator | generator + real-tree drift gate shipped 2026-10-28; dataset re-upload owed |
| G11 | `js-error-finder.js` red (9 bundle checks) | checker vs runtime-patch mismatch |
| G12 | Map-transition watchdog | pipeline closed 2026-10-26; rule executed offline; live stall owed |
| G13 | Claim that ended the connection re-sent 287 times | fixed (retry bound survives scene reset) |
| G14 | 455 repairs labelled `unknown` | fixed (decision rows log game error; cause classifier covers the phrasings) |
| G15 | No client window → 27 doomed directives | fixed (client-absent rescue) |
| G16 | Recovery table certifying itself (348/348) | fixed |
| G17 | 480 accepted claims scored noop because settle stopped too early | fixed (settle window; `_start_waits`) |
| G18 | 70% of steps were reads; 3,005 scored success | fixed (waits are `kind:"wait"`, cost no step/verdict/budget) |
| G19 | Repair queued for one goal dispatched under the next goal's label | fixed |
| G20 | Dialog ranker's primary signal decided nothing; no outcome recorded | fixed (dialog rows with outcome) |
| G21 | Dialog row could not say who ended the conversation | fixed (`ended_by`, `via`, `reply_id`) |
| G22 | Same reply asked 87 times on the same page | fixed (re-ask bound) |
| G23 | Recorded corpus cannot label a dialog choice | fixed instrument (`tools/dialog_labels.py` reconstructs reply→server outcome) |
| G24 | Nothing started the combat brain | wired 2026-10-26 (auto-start probe; `kind:"brain"` rows) |
| G25 | Live board not actionable — 156/156 snapshots rejected | fixed field names; gate pins bundle membership |
| G26 | `objectives_completed` 0 while a claimable objective sat at index 1 | fixed (scan the whole ready list) |
| G27 | Free-form `parameter0` planned as a monster | fixed (free-form plans nothing; scan moves on) |
| G28 | Every quest-progress number structurally 0 | fixed (`stepId` position in `dbQuest.stepIds`; completed list from client) |
| G29 | Quest cache ids stop at 2,546; live ids start at 3,841 | observed catalogue shipped; live population working (0 collisions); full store merge owed |
| G30 | Knowledge store resolved live objective ids to another game's objectives | fixed (`objective_id_collision` instead of foreign text) |
| G31 | 366 live quest-start requests named a quest the game never showed | fixed guard (session-scoped client-reported set; never-reported starts must be 0) |
| G32 | Quest offer is a link in the NPC text; page deleted it | read half fixed; accept half G33 |
| G33 | `quest.advance` cannot accept while a dialog is open | fixed 2026-10-27, offline-verified; live proof owed |
| G34 | An ignored action looked retryable (119× talk, 120× crossing) | fixed 2026-10-27, measured live (receipts + tutorial hold; 45 steps, max 4–9 repeats) |
| G35 | Cause-head validation leaked identical inputs | **gate-pass claim withdrawn** 2026-10-28: 50 of 400 val vectors identical to the fit/calibration inputs → 0.8343 against the 0.85 bar. Needs genuinely fresh rows |
| G36 | Simulator reported an open quest dialog merely for standing on the start map | fixed, measured 2026-10-28 (teacher 119/148 max_repeat 1 → honest floor 82/177 max_repeat 13) |
| G37 | Damage spells gated behind a state the planner never cast | fixed, **measured live** 2026-10-28 (enabler 7001, AP 1; gate `missing_state_1`; +2,000 XP) |
| G38 | Level-ups never spent — base stats with 35 points banked | fixed, **measured live** 2026-10-28 (7045 rank 2→4, agility 35→40; rank 5 correctly refused) |
| G39 | Pipeline scored itself against a teacher it could not beat | measured 2026-10-29 (val 0.8599 vs teacher agreement 0.857, lift +0.003; ICC 0.241, ESS 19–44 rows). The randomized arm is still unauthorised |
| G40 | Downloaded knowledge store is legacy; the client index is the authority | measured 2026-10-29 (44.0 MB, 1,976 quests, ids stop at 2546; 0 of 3841/4260/4262/4264/4266 present). Do not re-sync it — the licence forbids AI use |
| G41 | Live-outcome numbers were validated on the days they trained on | measured 2026-10-29 (hash AUC 0.830 → temporal 0.634). Temporal folds are now mandatory |
| G42 | The outcome label counted walking | measured 2026-10-29 (636 "moved" of 2,848 advance; **318 changed only the cell**) |
| G43 | The outcome label read the *pre-action* snapshot | measured/fixed 2026-10-29 (moved 1,030 → **1,863**; claims 345 → 788) |
| G44 | Context features do not predict success inside a tool | measured 2026-10-29 (`state_only` 0.724 vs tool-rate floor 0.723; `(tool, arg-kind)` lookup 0.902) |
| G45 | The travel primitive fails nine times out of ten | measured 2026-10-29 (direction 185/1,804 = 10.4% vs map 268/338 = 74.3%); live check owed |
| G46 | The planner's reasons were thrown away and are not reconstructable offline | measured 2026-10-29 (9,687 rows carry `why: null`); coverage starts with the next live run |
| G47 | The loop acted on state carried from the previous step | measured 2026-10-29 (**4,153 of 4,344 steps = 95.6% carried**, median age 707 ms) |
| G48 | Re-issuing an identical request is the dominant failure mode | measured 2026-10-29 (22% repeat after one miss, 0/18 after two, 1/41 after three; streak AUC 0.851) |
| G49 | `quest.advance`'s claim was too strong for a talk | measured 2026-10-29 (the `npc_id` form meets 13/1,639 = 0.8%) |
| G50 | The reply's claim counted the wrong event | measured 2026-10-29 (119 of 160 judgeable replies changed the dialog node) |
| G51 | Rule-keyed streak is a shippable veto; tool-keyed is not | shipped **shadow-only** 2026-10-29/30 (`agent/gate.py` records the would-be veto on every decision; acting on it needs the alternative priced first — G39/G55). **Zero shadow rows exist**: the newest logs (2026-10-29) predate the file, so 593 + 454 rows contain no `"gate"` key. The wiring is proven offline by `test_gate_shadow_reaches_the_trajectory` (10 checks): `VETO_AT = 2` annotates into `meta.gate`, the recorder persists it into the trajectory row, the loop's step rows structurally cannot carry it, and a read never builds a streak |
| G52 | The retry decision is a step at the second miss | measured 2026-10-29 (immediate retry 1/711; changing the request 26/28 = 90%) |
| G53 | The gate preserves progress; progress is the binding constraint | measured 2026-10-29 (100 advances = 9,692 actions ≈ 15.4 h ≈ **four days** of sessions) |
| G54 | The gate replicates across four rolling-origin cuts | measured 2026-10-29 (nav AUC 0.939/0.753/0.655/0.853 over base rates 1%–80%) |
| G55 | The loop already contains the replacement and takes it 17% of the time | measured 2026-10-29 (different action with no read → 349/459 = 76% met, vs 0.4% for the same rule) |
| G56 | The forest ranks better than the counter; at the shipping point they are identical | measured 2026-10-29 (forest 0.849 vs rule 0.683 ranked; both 99.4% kept / 2.6% vetoed-met where it matters) |
| G57 | The world-model encoder does not beat the hand-built features | measured, **route stopped** 2026-10-29 (encoder 0.571 vs hand-built 0.658; bar was +0.03). Revisit at ≥40 test sessions |
| G58 | At the rule's veto count the forest keeps more nav successes in 3 of 4 cuts | measured 2026-10-29, **superseded the same day by G59** (it retrained inside each cut) |
| G59 | The frozen veto model loses to the rule out of time, so nothing was shadowed | measured, **rejected** 2026-10-29 (nav 0.899 = 0.899; quest 0.691 vs 0.664, CI −0.80..−0.10). No model wired, no shadow added |
| G60 | The gate counted interactive retries as new calls | fixed 2026-10-30 (normalized key drops `lang`/`skill_uid`/`cell`; `agent/gate.py`) |
| G61 | Sessions bounce between two maps and no row says why | measured 2026-10-30 (dofus-02 216/229 map changes are A→B→A; 0 of 617 rows had `why`) |
| G62 | The equip gate is the tutorial wall; the loop re-offered worn pieces | settled **offline** 2026-10-30 (`18974` is `QUEST_OBJECT:14`, `possible_positions: []`; `slot: 8..16` probe withdrawn). Ayin Spyr's answer is owed |
| G63 | An objective may not return to the map it just left more than three times | shipped 2026-10-30 (three-return park; frozen replay 420 turns over 17 sessions passes unchanged) |
| G64 | The client's own refusal text was never read | shipped 2026-10-30 (`logError` hook, newest 5 strings, 200-char cap; 16 checks) |
| G65 | A refused call was credited as a met claim | fixed 2026-10-30 (945 refused steps, **63 wrongly `claim_met`**; `label_version: 3`; advance meets 23.3% not 26.3%). Live proof owed |
| G66 | Per-form count calibration does not beat the counter at equal veto budget | measured 2026-10-30 (keeps 0.611 vs 0.678 at the same 168 vetoes, CI −0.121..0.012). Equal-budget comparison is now mandatory |
| G67 | The state block has no out-of-time headroom on the kept calls | measured 2026-10-30 (`quest.talk` 0.472, `quest.reply` 0.398) — **no worker ships** |
| G68 | The transition context dropped the element's skill instance and enabled list | fixed 2026-10-30 (all 9,687 rows had `skill: null`; rebuilt corpus labels identical) |
| G69 | The aqueduct pages were earned by an interactive use the corpus could not see | measured 2026-10-30 (13948 validated 67 ms after the ack of `use 513208` from cell 345). Mechanic implemented; **a live run on `2026-10-30a` is what is owed** |
| G70 | The veto's alternative was never priced, so it could not be wired | priced 2026-10-30 (`--alternative`, 10 checks): cost 1.68 % / 3.12 % of successes (retention 0.9832 / 0.9688); the alternative the loop already takes meets **45.5 % / 63.9 %** against **0.0 % / 0.5 %** for repeating, both CIs excluding zero. G55's 76 % was a label artifact. Observational, not causal — that still needs the epsilon arm |
| G71 | `PROMOTION_DELTA_POINTS` was declared and read by nothing | fixed 2026-10-30: `_promotion_ok` now demands the point gain ≥ 0.05 alongside CI > 0 and retention ≥ 0.95, and `promotion` reports `min_delta_points` + `counter_kept_success`. Found because a mutation got away — and the *other* escaped mutant was **equivalent** (one-form fixture, so the Beta prior is invisible); fixed with a two-form fixture rather than another assert |

**Unnumbered but real:**

- Manual interventions are instrumented (trusted input events only), and the newest logs
  count **194 across four slots** — 140 of them on dofus-05. Unattended Full Auto is not
  yet zero-intervention.
- Recover/replan is still 2,186 of 4,003 steps (2,416 counting retries) — see §12.2. The
  three-return park (G63) is the fix; its effect has not been measured live yet.
- `tools/session_report.py` **no longer** refuses `play_*.jsonl`: the millisecond-epoch
  crash was fixed on 2026-10-30 and is pinned by `test_session_report_census`. `quest_
  witness.py` remains the tool that attributes an objective to the use that earned it.
- `watchUpstream` flapping every 10 s is cosmetic (the flag is written, never read to
  gate a dial).
- WARP showing `000` is expected on this line (UDP blocked), not a regression.
- Guest bans remain the fleet's real ceiling: two permanent
  `ui.popup.accessDenied` bans are recorded in `brain_console.log`.

---

## 14. Current uncommitted work (working tree at snapshot)

`git status` shows **42 files modified (+26,134 / −19,923)** versus HEAD `c875cde`, plus
13 untracked paths. (The count moved from the 30 recorded earlier in the day because the
fixes below also rewrite their `kaggle/toolkit/` mirrors — that is the invariant working as
intended, not drift.) This is the newest work and is **not committed** (the project's rule:
no commits or pushes unless the user asks). Since the previous snapshot of this document
(HEAD `0990695`) four commits landed the 2026-10-27/28 work: `f690da0` (73 files,
+43,184/−45,064), `cbc0e31` (106 files, +13,422/−407), `5e84e79` (158 files,
+54,812/−265) and `c875cde` (137 files, +60,082/−736) — so the research-unlock, brain
daemon, entity-pointer and command-parser work that this section used to list as
uncommitted **is now committed**.

**Modified — the 2026-10-29/30 outcome-label programme:**

- `dofus-network/tools/live_outcome_probe.py` — the whole G41–G69 measurement surface:
  `--calibration` (equal-veto-budget comparison) and `--worker` (state-block headroom),
  with the promotion constants `PROMOTION_RETENTION` / `PROMOTION_MIN_COUNTER_VETOES` /
  `PROMOTION_DELTA_POINTS` pinned in the file.
- `dofus-network/tools/build_live_transitions.py` — post-G43/G60/G65/G68 label rules:
  after-state pairing, normalized call identity (imports `agent.gate.VOLATILE_ARGS`),
  refused calls scored as misses (`label_version: 3`), element `skill_uid` /
  `enabled_skills` / `type` / `door` carried into the context.
- `dofus-network/data/transitions_temporal/*` and `data/transitions_v2/*` — the corpus
  rebuilt on 2026-10-30: 9,687 rows / 457 segments / 5,289 actions / 1,322 claims met.
- `dofus-network/agent/autopilot.py` — gate shadow wiring (`_meta["gate"]`), observation
  age/source reporting, refusal payload in `note_result`, three-return park (G63).
- `dofus-network/agent/planner.py` — travel as adjacent hops (G45), `why` publication
  (G46/G61), the claim/plan changes behind G62/G63.
- `dofus-network/agent/regression.py` — the new sections: `test_claim_calibration` (16
  checks), `test_worker_headroom_layer` (14), the `use:` and `worker:` mutation entries.
- `scripts/mobile-disguise.js` — page build `2026-10-30a`: `isoEngine.useInteractive`
  (walks first), a 3 s interactive settle, the `logError` refusal capture (G64) and the
  interactive element list with `skill_uid`/`enabled_skills` (G68).
- `dofus-network/data/knowledge/client_index.json` + `client_index.manifest.json` — the
  client-captured index, refreshed (the 44 MB DofusDB store stays untouched: its licence
  forbids this use).
- `builder/Build.exe` — the stale tracked builder recompiled from `build.cs`; the release
  build exits 0 again (§2.2).
- `dofus-network/docs/KNOWN_GAPS.md` (G35–G69 added) and `docs/TRAINING_PLAN.md` (the
  pre-registered bar).
- `dofus-network/kaggle/toolkit/**` + `TOOLKIT_MANIFEST.json` — the mirror, re-synced.
  **This was the one failing check when this snapshot was taken:** `--check` reported
  `tools/quest_witness.py` as drift, `--write` copied it, and the gate went back to
  1,540/0. Run `python -m tools.sync_kaggle_toolkit --write` after every source edit.
  The same check then caught a second, self-inflicted drift minutes later: an interrupted
  `tools/mutate_cause_gate.py` run had left a real mutation in `agent/autopilot.py`
  (`ok=None, error=None`) and a line-ending change in `agent/controller.py`; both were
  restored from the mirror (§17).
- `tools/test_inventory_equip.js` (32 checks), `tools/test_page_build.js`, `resume.md`.

**Modified in the same-day re-verification pass (2026-10-30b), after the audit of §3–§18:**

- `tools/session_report.py` — `_ts()` normalizes epoch-**millisecond** stamps so
  `play_*.jsonl` reads instead of raising `OSError: [Errno 22]`; the docstring now states
  what a wire capture does and does not report. Five new checks in
  `test_session_report_census`.
- `tools/live_ready.py` — `last_log_build` split into `_scan_build` plus a fan-out over the
  newest log in the directory when no `--log` was named; the preflight's page-build verdict
  moved from a false `2026-10-27s` to the true `2026-10-29bf`.
- `tools/mutate_cause_gate.py` — a write-ahead journal (`.mutate-journal.json`), a
  `--recover` mode, and a startup repair, so a hard-killed sweep no longer leaves broken
  code in the tree; the duplicate mid-file `import os` removed while in there.
- `agent/regression.py` — `test_mutation_journal_recovery` (6 checks) plus the new
  millisecond and preflight checks; registered in `run()`.
- `desktop/aipanel.cs` — the (never-displayed) hint string corrected from 25 to 28 tools.
- `rules.md` — the new **Change policy** (clean the code you touch before adding to it) and
  its own stale 25-tool vocabulary corrected to 28.

**Untracked (new files, none committed):**

- `dofus-network/agent/gate.py` (63 lines) — `RuleGate`: the normalized call key
  (`VOLATILE_ARGS` drops `lang`/`skill_uid`/`cell`, G60) and the `repeat_streak_norm`
  counter, **shadow-only by construction** — it has no method that changes what is sent.
- `dofus-network/agent/state_features.py` (153 lines) — the 24 relational features
  (entity vectors with cells relative to the character, client's 14-per-row grid) used by
  G57/G67.
- `dofus-network/tools/state_encoder_probe.py` (368 lines) — the world-model encoder test
  that produced G57's kill.
- `dofus-network/tools/train_veto_shadow.py` (202 lines) — the frozen-model deployment
  rehearsal behind G58/G59.
- `dofus-network/tools/quest_witness.py` (235 lines) — attributes an objective validation
  to the last interactive use on the same map (G69). Read-only over any capture.
- `tools/test_client_errors.js` — executes the refusal-capture wrapper (G64).
- `tools/test_interactive_use.js` (13 checks) — pins the walk-first use route (G69).
- `_obs_05.json` — a single captured observation dump (2026-10-30 00:03), scratch.
- `dofus-network/kaggle/toolkit/**` mirrors of the five Python files above.

The last commits are the "golf" optimization pass and the command-model commit. The
golf staging tree was deleted in `54ecd23`; only the encoder span-pruning merge survived.
Do not resurrect the staging tree unless you intend to re-run its gates.

---

## 15. Build and release

`compile.bat` compiles `builder/Build.cs` into `Build.exe`, then runs it:

| step | what happens |
|---|---|
| 0 | compile the Go relay (skipped if `go` is absent; the shipped exe is reused) |
| 0b | apply `patch-engine.js` to the client bundle |
| 0c | sync client shims into both distribution trees |
| 1 | `DofusFarm.exe` (Roslyn if available, else legacy csc) |
| 2 | `uninstall.exe` |
| 3 | `setup.exe`: stage the payload, compile with the embedded zip as a resource |

**Payload:** host, relay, client bundle, `bin/warp`, `bin/webview2`,
`tools/patch-rules.txt`, `runtimes/python`, `dofus-network` (selected models, INT8
command model, tool policy, encounter selfplay), `network.ini`, `proxies.txt`,
`accounts.txt`. Current payload: the host, relay, client bundle, tools, runtimes and the
selected models **plus a 213.8 MB standalone WebView2**, so `setup.exe` measured
**224,413,696 B (214.0 MiB)** on 2026-10-30. The 100.1 MiB / 513-entry figure was the
pre-rebuild non-standalone installer; do not quote it.

**Excluded from the payload:** `networkhealth_artifacts/`, training corpora, model
`encoder30_v15/model.pt` and fp32 `encoder.onnx`, fp32 command ONNX, verifier `.pt`,
`models/encounter_pbt.pkl`, `models/archive`, `_tune`, `kaggle/toolkit` duplicates.

**Legacy Android path** (SDK + system image, ~950 MB) is excluded by default;
`--include-legacy-emulator` puts it back. Nothing in the UI can select it; the engine
selector was removed.

**Requirements:** .NET Framework 4.x (or any .NET SDK for Roslyn), Go 1.21+, Node
(build-time patch engine only), Python 3.11 for the brain.

---

## 16. Configuration reference

### 16.1 `network.ini` (current on disk)

Current values: `mode=auto`, `socks_port=40000`,
`upstream=socks5://127.0.0.1:40000`, `base_port=8880`, `fail_closed=1`,
`extra_args=`, `wireproxy_conf=`, `log_path=`, `exit_proxy=` (empty).

| key | default | meaning |
|---|---|---|
| `mode` | `auto` | `auto` / `wireproxy` / `warp` / `custom` / `direct` |
| `socks_port` | `40000` | local SOCKS5 port the tunnel binds |
| `upstream` | empty | custom mode: `socks5://…` or `http://…`; empty = `proxies.txt` |
| `base_port` | `8880` | relay port for slot *n* is `base + n` |
| `fail_closed` | `1` | `0` allows direct when nothing is healthy — **exposes your IP** |
| `extra_args` | empty | raw flags appended to the relay command line |
| `wireproxy_conf` | empty | blank = `bin\warp\wireproxy.conf` |
| `log_path` | empty | blank = `<install>\asset_cache\proxy.log` |
| `exit_proxy` | empty | a proxy you own, for a sticky session address |
| `allow_system_warp` | unset | required before `warp` may rewrite system routing |

**Note:** the current `mode=auto` with a local `upstream` is a deliberate combination;
`auto` still tests every candidate, and `EnsureTunnel` brings up the userspace tunnel
when `custom` names our own tunnel port. The AI_ARCHITECTURE §11.1 measurement that
said `mode=custom` describes the moment of that bug; the file has since been set back
to `auto`.

### 16.2 `ai_settings.ini` (current on disk)

```
mode=auto
target_slot=1
target_slots=1,2,3,4
policy=planner
dry=0
steps=0
goals=do all the quests
order=
shortcut.start_auto=116
shortcut.stop_all=117
shortcut.send_order=118
shortcut.emergency=123
shortcut.toggle_dry=119
```

This machine therefore launches with the **hybrid planner** policy, not the CLI
default `rule`. Full Auto is `steps=0`, no wall-clock budget.

### 16.3 `client-version.ini` (optional)

Pins the version the page announces without a rebuild:

```ini
app=3.15.0
code=3015000
build=1.75.2
```

Without it the version is resolved from the installed bundle
(`DynamicVersionResolver`); if that fails the field is left alone rather than filled
with a stale constant. `mobile-disguise.js` never invents a version.

`app_version.json` currently reports `appVersion 3.14.3`, `versionCode 3014003`,
`buildVersion 1.74.9`, source `GooglePlayStore`, last checked 2026-10-27T20:52Z.
That is the **Play-store check**, not the served bundle; `build/script.js` carries
`1.74.10`, which is what the client uses.

### 16.4 Environment

| variable | effect |
|---|---|
| `DOFUS_FARM_HOME` | alternate install root for SDK/proxy lookup |
| `ANDROID_SDK_ROOT` / `ANDROID_HOME` | legacy Android only |
| `ANDROID_AVD_HOME` | legacy Android AVD location (`farmengine.cs:160,2004`) |
| `DOFUS_MOD_WEBHOOK` | mod-webhook target (`WebView2ClientForm.cs:1759`) |
| `LOCALAPPDATA` | profiles, logs, autopilot/human labels |

### 16.5 Relay flags — and the `network.ini` trap

**The host does not pass `-config` to the relay.** `grep -n "\-config" desktop/*.cs` is
empty, so `dofus-net-proxy.exe` starts with explicit flags only, and
`configArgValue(...)` returns `""` for an absent argument (`scripts/dofus-net-proxy.go:3250-3262`)
— which means **`network.ini` is not read by the relay when the manager launches it**. The
ini is the C# side's model (`netsettings.cs:147-156` parses the ten keys in §16.1) and the
C# translates them into per-instance command lines. Editing `network.ini` by hand still
works, because the C# re-reads it — but the key names differ between the two layers
(`base_port` vs `-port`, `log_path` vs `-log`, `fail_closed` vs the absence of
`-allow-direct`), so the old sentence here ("`dofus-net-proxy.exe -config network.ini`
reads `port, host, upstream, dns, cache, quiet, log, allow_direct, referer, mods_dir` as
defaults") is only true of a **manual** run.

For a manual run the full flag set is: `-port -host -upstream -cdn-upstream -exit-proxy
-config -dns -cache -quiet -log -allow-direct -token -serial -uuid -model -manufacturer
-ua -gl-vendor -gl-renderer -texture-size -referer -assets-url -precache-maps
-precache-workers -precache-neighbours -precache-radius -precache-tiles-depth
-precache-workers -crawl -crawl-max -crawl-rate -crawl-tiles -mods-dir -login-host`
(`dofus-net-proxy.go:3306-3339`). Note `-cdn-upstream` and `-exit-proxy` exist and were
never documented here. The C# passes `-cache asset_cache` explicitly
(`farmengine.cs:1292`), overriding the Go default `proxy_cache`.

### 16.6 Files §16 did not document (added 2026-10-30)

| file | where | what it actually is |
|---|---|---|
| `runtime.ini` | install root **or** `%LOCALAPPDATA%\DofusFarm\` — **absent on this machine, so defaults are in force** | `refresh_ms` (default 4000, clamped to 0 or 1000-60000), `watchdog`, `governor`, `telemetry`, `fight_boards`, `auto_ai` — the last five are 0/1 and `auto_ai=1` is why launching a client also starts its loop (`runtimesettings.cs:18-25,42-47,98-104`) |
| `ai_goals.txt` | `%LOCALAPPDATA%\DofusFarm\` | the goals the AI page edits (`aipanel.cs:906`). There is **no** `goals.txt` at the repo root; the Python side takes `--goals-file` (`agent/autopilot.py:3490`) |
| `AUTOPILOT_STOP` | **two places**: `%LOCALAPPDATA%\DofusFarm\AUTOPILOT_STOP[.<instance>]` and `REPO_ROOT\AUTOPILOT_STOP` | the panel writes the LOCALAPPDATA ones (`aipanel.cs:1075-1083`); the CLI default is the repo-root file (`agent/autopilot.py:78`). Both LOCALAPPDATA files exist right now (`AUTOPILOT_STOP` 25 B from 10-29 22:38, `AUTOPILOT_STOP.dofus-01` 27 B from 10-28); the panel clears a stale **root** file at launch (`aipanel.cs:897-904`) |
| `bridge_port.txt` | `%LOCALAPPDATA%\DofusFarm\` | the only authority for the directive bridge port; contains **`8896`**. `agentbridge.cs:39` walks `{8893…8899}`, `agent/bridge.py:22` still defaults to 8893 |
| `kick_signatures.json` | `dofus-network/data/safety/kick_signatures.json` (98 B) | the kick-pattern list; the host searches 4 candidate roots for it (`WebView2Backend.cs:179-182`) — it is not under `knowledge/` |
| `app_version.json` | `%LOCALAPPDATA%\DofusFarm\` | the updater's cache; UTF-8 **BOM** (read it with `utf-8-sig`), versions 3.14.3 / 3014003 against bundle 1.74.10. `lastCheckedUtc` moves every launch — never quote it from a document |
| flags | install root | `manual-login.flag`, `no-client-update.flag`, `oauth-login.flag` — **none present now**, only `oauth-login.flag.paused` (so OAuth is paused). `force_fresh_identity.flag` is **per-profile** (`webview2_profiles\<slot>\`), not a root file (`WebView2ClientForm.cs:611`, `mainform.cs:3021`) |
| exploration | CLI only | there is no epsilon ini key: the randomized arm is `--explore-objectives` (`autopilot.py:3441,3506`) and it is off by default |

And one correction to a widely repeated premise: **there is no max-instance constant in the
code.** The "12/6/6" figure is `NORMAL_CAPS` for combat AP/MP/range
(`combat/normal_build_duel.py:45`), not a fleet limit. The real operational limits are the
governor's ≤4-core share (`prioritygovernor.cs:163`, 62-core ceiling at `:168`), 3 watchdog
restarts per hour (`watchdog.cs:16`), `RETURN_LIMIT = 3` (G63, `planner.py:193`) and the
page's 3-reload busy budget (`scripts/mobile-disguise.js:256`).

---

## 17. Running it and testing it

### 17.1 Install / launch

1. Run `setup.exe`; it unpacks the runtime, client bundle, relay, proxy binaries, and
   Python agent.
2. Launch `DofusFarm.exe`.
3. **Settings → Network**: leave mode **Auto**; press **Test**; you want `active: …`
   and at least one healthy candidate.
4. **Instances → Add slot**, then **Launch**.
5. First launch verifies the bundle, starts the relay, and creates the profile under
   `%LOCALAPPDATA%\DofusFarm\webview2_profiles\`.

### 17.2 Build from source

```bat
compile.bat
compile.bat --include-legacy-emulator
```

A running `DofusFarm.exe` locks its own image, so `compile.bat` only succeeds with
the app closed. The checked-in `desktop/aipanel.cs` fixes (attach flow, `JsonOk`
reply, per-instance status/stop) are therefore source-only until the next
rebuild; the running exe keeps the older page until then.

### 17.3 Tests and self-checks

**C# headless:**

```bat
DofusFarm.exe --bundle-selftest
type bundle-selftest.txt

DofusFarm.exe --ai-status-selftest
type ai-status-selftest.txt
```

**Relay:**

```bat
cd scripts
go test dofus-net-proxy.go dofus_net_proxy_equiv_test.go
```

**Agent (offline: no game, no network, no torch):**

```bat
cd dofus-network
python -m agent.regression       :: 1,540 checks, 0 failed (measured 2026-10-30)
python -m agent.constraint_corpus --check  :: 39 hand cases, 28-tool preconditions and causes
python -m agent.constraint_corpus --generate --out data/constraint_synthetic.jsonl
                                  :: 2,600 synthetic rows, 100% cause/action agreement
python -m advisor selftest       :: 91 check sites
```

**Mutation driver (proves the checks are non-vacuous):**

```bat
python tools/mutate_cause_gate.py
:: 432 mutations (counted from MUTATIONS on 2026-10-30c; the driver also prints how many
:: anchors resolve exactly once); MUT_ONLY=<batch> runs one batch

**Do not interrupt a mutation run — and if you do, it now repairs itself.** The driver
writes a real mutation into a source file, runs one gate section in a throwaway process,
and restores the file in a `finally`; a hard kill skips that `finally`. That happened while
this snapshot was written: the source `agent/autopilot.py` was left with
`ok=None, error=None` at the decision site (mutation *"game-level outcome dropped at the
step site"*) and `agent/controller.py` with different line endings, which the drift check
caught as two extra failures. Both were restored byte-for-byte from `kaggle/toolkit/agent/`
(the mirror still held the pre-mutation content).

The fix is in the driver now: it journals the pre-mutation bytes to
`dofus-network/.mutate-journal.json` **before** each write, deletes the journal after the
restore, and replays it at startup — so the next start, or an explicit
`python tools/mutate_cause_gate.py --recover`, repairs what a killed run left behind.
`test_mutation_journal_recovery` (6 checks) pins it. After any interrupted run still do:
`python -m tools.sync_kaggle_toolkit --check`, `git status`, and read the diff of every
file the run touched before trusting the tree.

**How to read a batch verdict (fixed 2026-10-30c).** The snippet used to run
`test_kaggle_toolkit_sync` inside every mutation. A mutation edits a mirrored source file,
so drift failed for *every* mutation and every batch printed "CAUGHT" regardless of the
checks. It is out of the snippet now. Two consequences: earlier `calib:` 7/7, `worker:` 2/2
verdicts are of unknown value and should be re-run; and a batch can now
report **MISSED** legitimately — `alt: a read is counted as an acting call` is missed
because the corpus already gives a read no `claim`, i.e. an equivalent mutant. A crash is
also reported as caught, which says nothing about the checks; look for named failing checks.

The exposure is bounded by where the mutation lands: the mirror covers `dofus-network/`
only, so the batches whose targets are `agent/*.py` or `tools/*.py` (`PROBE`, `AUTO`,
`CTRL`, `PLAN`, `SREPORT`, `REPORT`, `BRAIN`, `READINESS`) were contaminated, while
mutations against `scripts/mobile-disguise.js` (`use:`) and the C# host — including §5's
`guard removed` / `JsonOk reverted` pair and `test_page_build`-style checks — were **never**
affected by the drift check and stand as recorded.

**Re-audit result, same day.** With drift out of the snippet, `calib:` came back **5 of 7
caught, not the 7 of 7 on record**: `a form is not shrunk toward the global rate` and
`a tie with the counter is promoted` were MISSED. One was an equivalent mutant (a
one-form fixture makes the Beta prior invisible — the check looked like it tested
shrinkage and could not fail); the other was a genuine hole, because the point floor was
declared and never read (G71). Both are fixed, and the batch is now **8 of 8 caught** with
`restored: 0 failing checks`. `worker:` (2/2) and `use:` (2/2) were confirmed as recorded.
A third outcome to watch: a mutation whose anchor no longer matches prints **SKIP**, counts
as "no check noticed", and disappears from a filtered log — after editing a mutated file,
re-check its anchors or you are measuring a mutant that never ran.
```

**JS page suites (36 `tools/test_*.js` files):** `tools/test_boot_alert.js`, `test_battle_log.js`,
`test_challenges.js`, `test_char_build.js`, `test_dialog_close.js`,
`test_dialog_links.js`, `test_fight_budget.js`, `test_fight_planner.js`,
`test_fight_state_fields.js`, `test_geometry_watch.js`, `test_group_scan.js`,
`test_intervention_instrument.js`, `test_inventory_equip.js` (32 checks), `test_keep_alive.js`,
`test_login_guard.js`, `test_npc_actor.js`, `test_page_build.js`, `test_quest_accept.js`,
`test_quest_catalog.js`, `test_quest_progress.js`, `test_research_tools.js`,
`test_session_login.js`, `test_shop_buy.js`, `test_transition_watchdog.js`,
`test_tutorial_hint.js`, `test_ui_tap_client_target.js`, `test_ui_tap_close.js`, plus the
nine added in the 2026-10-28/30 work: `test_interactive_use.js` (walk-first use route,
13 checks — G69), `test_client_errors.js` (refusal capture — G64),
`test_equip_slot_inference.js`, `test_inv_unequip.js`, `test_nav_doorless_exit.js`,
`test_login_ban.js`, `test_login_cooldown.js`, `test_kick_signature_clear.js`,
`test_packet_redaction.js`. Not `test_*.js` but also run by hand:
`audit_tutorial_chain.js`, `js-error-finder.js` (red — G11) and `auth-bypass-suite.js`.
`agent.regression` delegates several of them to `node`.

**Live preflight:**

```bat
cd dofus-network
python tools/live_ready.py --log trajectories/default.jsonl
python tools/live_ready.py --check-installer
```

`FAIL page build` means the last client recorded an older `agent_build` than the repo
(currently `2026-10-30a`); restart the app fully, because a reload keeps the old
injected page. "Last client" is now the newest `*.jsonl` in `trajectories/` by mtime —
until 2026-10-30 the preflight read `default.jsonl` alone, which the per-instance daemon
no longer writes, so it always reported a stale build; name one file with `--log` and it
is read as itself. `FAIL stop file` means a leftover `AUTOPILOT_STOP` would end the run on
its first step. `WARN installer` means `setup.exe` is older than the page;
`WARN client`/`WARN brain` mean the app or combat brain is not up yet.

**One-command live evidence:**

```bat
python tools/live_evidence.py
```

Preflight → bounded autopilot with `--command-shadow auto` → combined session/shadow/
replay report + autopilot summary. `--report-only` re-renders an existing log.

### 17.4 Manual commands

```bat
cd dofus-network
python run_autopilot.py --goals-file goals.txt --steps 0
python -m agent.autopilot --goal "do all the quests" --steps 20 --command-shadow auto
python -m agent.serve                 :: port 8895
python sim/run.py --policy planner --level 60 --steps 400 --noise --travel-cost
python tools/policy_faceoff.py --seeds 30 --steps 300
python tools/session_report.py <log1> <log2>
python tools/recovery_report.py <log1> <log2>
python tools/dialog_labels.py
python tools/brain_readiness.py
python tools/group_scan.py
python tools/policy_over_record_boards.py
python tools/ingest_observed_quests.py --coverage
python tools/quest_advance_shapes.py <log>
```

**Reading a run:** every step is one line
`[autopilot] <n> <tool> <ok|FAIL> <verdict> -> <args>`. `advanced` means the world
changed; `noop` means the call succeeded and nothing moved; `failed` means it did not
run. A run ending with `done: the client window closed` lost its window, not its mind.

**Always-on AI and dynamic instances (2026-10-28).** `runtime.ini auto_ai=1`
(default) implements automatic startup, but desktop review found pending-start/Stop
races, UI-thread violations, blocking requests, non-atomic readiness and a
per-client process fallback. End-to-end always-on behavior is not yet verified.
The daemon now signals Stop without joining under its global lock; eight tests
cover scoped/idempotent Stop, cleanup and real loopback HTTP with **1, 7 and 43
concurrent fake workers**. This is not live-client scaling proof. Separately,
tracemalloc measured **40 planners = 22.1 MB total** with one shared quest graph,
versus 109.1 MB for five independent planners. Full daemon/WebView2 RSS is unmeasured.

### 17.5 Kaggle

Account `tajdinenizar`; credential at `~/.kaggle/kaggle.json` (never read or logged by
the agent). Private datasets: `thx-02-sim-toolkit` (code + KB), `thx-02-tool-corpus`.
Keep output small; the artifact is the model. TPU = instant interactive notebook,
not a queued batch push.

---

## 18. Troubleshooting

| symptom | most likely cause | what to do |
|---|---|---|
| "Server busy" / nothing loads | no healthy egress, or a dead login pin | Settings → Network → **Test**; read per-candidate reasons; see §8.4 |
| Instance does not start at all | fail-closed with no candidate | same as above; or switch to **custom** and press **Use proxies.txt** |
| Tunnel "listening but not passing traffic" | ISP drops WARP's UDP | use `custom` + a live proxy, or system WARP (443) |
| WiFi died after enabling WARP | system WARP took routing/DNS | Settings → **Repair my network**; stay on `auto`/`wireproxy` |
| Black screen, no error | bundle broken or unpatched | check `[UPDATER]`; `DofusFarm.exe --bundle-selftest` |
| Login fails right after a client update | patches not applied | updater should re-apply; verify `[UPDATER] patched staged bundle` |
| Guest login 403 | guest quota for your IP | real account, or wait |
| `SOCKET_LOST` | malformed `appVersion` param | version param must be emitted last |
| Relay port already in use | stale `dofus-net-proxy.exe` | the engine kills the one bound to that port on launch |
| Two hosts fighting | single-instance mutex | the second launch focuses the first |
| AI loop exits immediately | no goals, or a stop file is present | goals live in `%LOCALAPPDATA%\DofusFarm\ai_goals.txt`; **check both stop locations** — `AUTOPILOT_STOP[.<instance>]` under `%LOCALAPPDATA%\DofusFarm\` (the panel's) *and* `REPO_ROOT\AUTOPILOT_STOP` (the CLI's). Both LOCALAPPDATA files exist right now; the panel clears a stale root file at launch (`aipanel.cs:897-904,1075-1083`) |
| Long waits "policy had no action" | *was* the newest open symptom; it is now explained and bounded | the loop was bouncing a map pair (G61: 216 of 229 changes were A→B→A) and the three-return park now stops it (G63). Read for `[planner] objective … parked after N immediate returns` instead. If a run still idles, the open question is what the loop should do *instead* (G39/G55), not why it waited |
| `Server busy` after a network change | a page fetch kept a dead pin alive | see §8.4; page now reloads with a 3-reload budget |
| Every directive fails while the UI looks normal | the bridge landed on a port other than 8893 | read `%LOCALAPPDATA%\DofusFarm\bridge_port.txt` (**8896** measured 2026-10-30); `agent/bridge.py:22` still *defaults* to 8893, so a hardcoded 8893 in any new tool is a bug |
| The AI page says started but nothing moves, or the pill lies | the start path differs: brain daemon vs per-instance child fallback | check the AI console for `[ai] attached to the brain daemon on port` vs `fell back to per-instance children` (`aipanel.cs:342-346,992-998,1651`) |
| Triage is reading yesterday's session | logs rotate at **64 MB** (`mainform.cs:269,2104`; `agent/recorder.py:141,272-292`) and a second rotation runs at startup (`Program.cs:271`) | open the newest `autopilot*.jsonl` / `*.log` **by mtime**, not by the fixed name; rotated siblings are named `dofus-05.20261029-170756-*.jsonl` |
| `session_report.py` used to die with `OSError: [Errno 22]` on `play_*.jsonl` | it fed epoch **milliseconds** into `fromtimestamp` (`_ts` → `:346`); protocol captures use ms, step rows use seconds | **Fixed 2026-10-30** — `_ts` normalizes `>1e11` to seconds and `test_session_report_census` pins it. If you are on an older page build of this tool, `tools/quest_witness.py` still reads the captures directly |
| The gate fails on "the checked-in Kaggle toolkit matches the harness" | a source edit was not mirrored (`test_kaggle_toolkit_sync`, `agent/regression.py:4611`) | `python -m tools.sync_kaggle_toolkit --check`, then `--write`. If a mutation run was interrupted, also `git status` and read each touched file's diff — the driver restores in a `finally` it never reached (§17) |
| An objective stops being planned at all | the three-return park (`planner.py:193,3397-3403`, G63) | read the `parked after N immediate returns` line; it suppresses that walk for that objective, it does not drop the quest |

---

## 19. History, decisions, and rejected paths

### 19.1 Timeline (condensed)

- **2026-10-17 → 10-21** — client bundle, WebView2 host, relay, proxy pool, first
  encoder training (v11/v15), knowledge base, simulator.
- **2026-10-22** — network rewrite: `ProxySupervisor`, `auto` mode, fail-closed,
  transactional client updater, patch rules, one-owner egress. Measured that WARP's
  UDP is blocked on this line.
- **2026-10-23** — live plumbing night: seven interfaces used wrongly; the agent was
  blind, then unable to act. First `quest.advance ok advanced`; the simulator was
  declared exhausted as a learning source. Window-close question instrumented.
- **2026-10-24** — combat DB and solver; client-exact geometry; quest observation
  contract (groups, dialogs); knowledge spine (SQLite); v4 tool policy promoted after
  paired benchmark; dialog/quest plan.
- **2026-10-25** — the whole Python test suite was deleted (`smoke_verifier` and the
  rest); the live baseline was captured. This is why `agent.regression` exists.
- **2026-10-26** — the big gap-fix day: G13–G33, command parser dataset/heads,
  observed quests, keep-alive investigation, combat brain auto-start, board field fix,
  map resilience, identity binding. The baseline's `unknown` repairs and read-only
  "advanced" inflation were root-caused.
- **2026-10-27** — command adapter + INT8 ONNX; foresight/ensemble/move head; GPU
  capacity sweep; AFK keep-alive fixed live; G34 tutorial hold; multi-instance
  addressing; client 1.74.10 update; first live tutorial run with a fight join,
  quest advance, map change and level 2/6 objectives. Page build q.
- **Golf pass (2026-10-26/27)** — an isolated byte-optimization campaign in `golf/`;
  ledger claims 7 files at −28.4% bytes, planner expected −61%; a FLOP-doubling
  encoder pass was rejected; the staging tree was deleted after the encoder span-
  pruning merge. Only that merge survives.
- **2026-10-28** — the first live fight proof (`fight_id 124`, `GameFightEndMessage` in
  the outcome row, a gate that fails without it); interactives, optional challenges, the
  quest step graph and dialog replies all captured into the observation; the tutorial UI
  tools (`ui.tap`, `ui.icons`, `shop.buy`); the simulator's false "quest dialog open"
  bug fixed (G36); state-gated spells and 35 unspent stat points fixed **live**
  (G37/G38); and the cause-head's gate-pass claim **withdrawn** after an input-level
  audit found 50 of 400 validation vectors identical to the fit inputs (G35).
- **2026-10-29** — the outcome-label programme, one defect per entry, G40–G59: store vs
  client-index authority (G40), day-leakage in every earlier number (G41), the label
  that counted walking (G42), the label that read the pre-action snapshot (G43), context
  features that turned out to be tool identity (G44), travel that fails 9/10 (G45),
  `why: null` (G46), 95.6% stale observations (G47), the repeat-streak discovery (G48),
  the claim rules for talk/reply (G49/G50), the veto and its four rolling cuts (G51/G54),
  the volume arithmetic (G53), what the loop does instead (G55), and the forest/encoder
  routes killed by their own bars (G56–G59).
- **2026-10-29 night → 2026-10-30** — the tutorial finished live; house doors settled as
  painted star cells; G60–G69: normalized call identity, the A→B→A map bounce and its
  three-return park, the equip gate explained offline, the client's refusal text read,
  refused-as-met corrected (`label_version: 3`), equal-budget calibration, the state
  block's missing headroom, the dropped skill-instance context, and the interactive-use
  attribution that finally explained the aqueduct pages. Page build **`2026-10-30a`**.
  The stale tracked `builder/Build.exe` was found and recompiled, so the release build
  exits 0 again — a failure that had been invisible because the gate compiled from
  `build.cs` rather than running the shipped binary.

### 19.2 Decisions D1–D24 (the important ones)

| # | decision | why |
|---|---|---|
| D1 | Encoder, not decoder | 55–62 ms vs 17.7 s for the same task |
| D2 | Early stop on **call**, not tool | tool saturates ~0.94 while call still moves |
| D3 | Anchors refreshed once per epoch | a 50-step clock made targets non-stationary |
| D4 | `price_rule=fixed` without a price is dropped, not guessed | 459/800 val calls rejected over one optional field |
| D5 | Resume, not restart | starts from 0.685 call instead of 0.20 |
| D6 | Latent bus + text sidecar | intent in latent, audit in text |
| D7 | User-facing language from templates, not a decoder | a decoder can hallucinate a price |
| D8 | Fast weights only in the L-cycle | bounded time correction; drift cannot cause a bad purchase |
| D9 | No evolutionary weight search | a farm episode is minutes; one generation ≈33 h |
| D10 | MoE deferred | +30 MB INT8 for a corpus too small |
| D11 | HDV through the in-game UI, not an API | UI is the precise source |
| D12 | Auto-update the client bundle at launch | a cached copy silently goes stale |
| D13 | Patches fail soft; a match >60 KB is refused | one stale boundary used to abort everything |
| D14 | Every unsupported native SDK gets a Proxy stub | client adds calls we do not ship |
| D15 | Drive the client's own path/tap | reimplementing duplicates obstacles and edges |
| D16 | Egress is a setting, never an assumption | system WARP rewrote the whole PC's routing |
| D17 | No wall-clock/step budget in the loop | a goal-driven loop ends when goals are met |
| D18 | One proxy supervisor owns egress; `auto` default | three places used to disagree |
| D19 | A bound SOCKS port is not a working tunnel | wireproxy binds before the handshake |
| D20 | Verifier ships as rules, not a model | the loop must run and fail to produce labels |
| D21 | Updater re-applies build-time patches | raw replacement erased every patch |
| D22 | Agent 2 is a controller, not a commentator | a verdict must change the next action |
| D23 | Labels weighted by source | human 1.0 / model 0.6 / rule 0.3 |
| D24 | Client version resolved, never hardcoded | a disguise announcing the wrong version is visible |

### 19.3 Rejected paths (do not rebuild without new evidence)

- **TRM-style recursion as the trunk** — tested twice (K=12, deep supervision) in two
  worlds; no gain over k=1. Kept only as the halting/escalation idea.
- **First failure → retry** — 2.3× worse than continuing in the simulator; 0/9 retries
  worked live because they retried an absent client.
- **Soft targets from the fork gain distribution** — +0.0003 gain, ECE 0.176 vs 0.012.
- **Smoother fork score (`--score cummean`)** — made ambiguity and quests worse.
- **Learned tool policy as a replacement for the teacher under priced travel** — the
  early model lost 19% (spurious `nav.goto`); later instrument fixes and the v4 corpus
  produced a consistent paired win, which is why it is opt-in now. Do not cite the old
  "learned loses" number without the instrument correction.
- **More data** — 32k → 90k rows: +0.002 val, −0.9 simulator quests.
- **Capacity** — 138× params for +0.0045 accuracy (tool policy); 139× for +0.0034
  capture (foresight); capacity is not the bottleneck.
- **100–135M dialog expert** — rejected by the user; the budget is not the bottleneck.
- **Shared-head specialisation** — lost held-out base rows; routed adapter won.
- **`plz/pls` data pass** — cost held-out rows; reverted.
- **Warm tunnel pool** — measured worse than nothing (20/25 s vs 5.5 s); do not revive.
- **`fasthttp`/`gnet` for the relay** — the bottleneck is the wire, not the CPU.
- **Listening before exit validation** — would let the first request leave on a
  different address.
- **Rewriting `WebView2ClientForm.cs`** — no feature upside, real risk.
- **Automated account signup** — CAPTCHA + CloudFront, and the one thing that
  plausibly gets accounts banned. Real accounts in `accounts.txt`, one per slot.
- **A learned state representation as the encoder route (G57, 2026-10-29)** — masked
  world-model pretraining plus a tiny head: 0.571 against hand-built 0.658, best
  combined 0.647, rule 0.778; the bar was +0.03 and no seed cleared it. A leaky
  pretrain scored 0.806 and an honest one 0.50–0.61. Revisit at ≥40 test sessions, not
  before.
- **A frozen learned veto in place of the streak rule (G58/G59, 2026-10-29)** —
  retraining inside each rolling cut flattered the forest; one frozen file scored out of
  time ties on nav (0.899 vs 0.899, CI −0.17..0.17) and *loses* on quest (0.691 vs
  0.664, vetoed-met 0.029 vs 0.143, CI −0.80..−0.10). Nothing was shadowed and nothing
  was wired — that is the correct outcome, not an unfinished step.
- **Per-form count calibration as the replacement (G66, 2026-10-30)** — at the
  counter's own 168 vetoes it keeps 0.611 met against the counter's 0.678 (CI
  −0.121..0.012); its apparent wins come from vetoing 1.5–3.7× more calls. The form
  rates do not transfer (`nav.goto:at` 0/12 in train vs 238/336 in test; form-level ECE
  0.676).
- **A state-block second layer behind the counter (G67, 2026-10-30)** — the only place
  left to win, and it has no headroom: kept-call AUC 0.472 (`quest.talk`) and 0.398
  (`quest.reply`) out of time, and the 5%/10% extra-veto layers removed calls that had
  **all** met.
- **`inv.equip slot: 8..16` (G62, withdrawn)** — the client sets position 23 itself;
  0–16 is the send window. Do not re-add the probe; the answer was that `18974` is a
  `QUEST_OBJECT` with `possible_positions: []`.

---

## 20. Non-negotiable invariants

1. **Drive the client, do not reimplement it.** Pathfinding, line of sight, map
   edges, and the fight state machine are the game's.
2. **Never connect direct.** The proxy fails closed; no upstream, no traffic.
3. **Capture before coding.** A verb without recorded traffic is a guess.
4. **Fail soft on the client, hard on privacy.** A stale patch may degrade; an IP leak
   must stop the instance.
5. **Numbers in docs are measured.** Anything unmeasured is labelled.
6. **A bound port is not a working tunnel.** Probe with a real CONNECT.
7. **An update is a transaction.** Never delete a live file before its replacement
   exists.
8. **Fail closed covers launching.** If egress cannot be wired and the mode is not
   `direct`, the client does not open.
9. **An outage must not require a relaunch.** A 40 s tunnel gap costs 40 s.
10. **Text is an observation, not a program.** The model proposes `reply_text`; the
    client resolves it to a reply id; the handler sends it.
11. **Unknown is not zero.** A missing level/price/monster input stays null and forces
    an abstain.
12. **Reference is not training.** Papycha's content signal forbids AI training;
    guides are RAG with attribution, never labels.
13. **The planner may estimate; only the server decides.** A win probability is a
    falsifiable claim; the outcome row is the answer.
14. **Every head abstains when its inputs are missing.** An abstain is a logged, valid
    decision; a guess is not.
15. **The model ranks legal candidates only.** Safety, legality, recovery, and
    knowledge stay in code/data.
16. **No commits or pushes unless the user explicitly requests them.**
17. **A gate is compared at the counter's own veto budget** (G66). A picker that looks
    better by refusing 1.5–3.7× more calls is a different trade, not a better one —
    `--calibration` enforces the equal count, and the constants are in the probe.
18. **Folds are temporal, not hash-only** (G41). The same model scored 0.830 on the hash
    cut and 0.634 out of time; a hash-only number is not evidence.
19. **A refused call is always a miss** (G65). If the page said no before the wire saw
    the request, it cannot be credited as a met claim — 63 such rows were removed.
20. **A call's identity is its normalized argument set** (G60). `lang`, `skill_uid` and
    `cell` never make the same promise into a new call; three re-taps of one element are
    one streak of three.
21. **After every source edit, `python -m tools.sync_kaggle_toolkit --write`** (G10).
    Toolkit drift is the one gate failure that is always self-inflicted — it was the
    *only* failure at this snapshot (`tools/quest_witness.py`).
22. **No DofusDB re-syncs.** The 44 MB store's licence forbids AI use; the client index
    is the live authority and is captured, not downloaded (G40).
23. **Quest logic stays generic.** No per-quest hard-coding: generic actions plus the
    map/NPC dialogue the client reports. A hand-coded quest is a regression of the
    research goal, not a shortcut.
24. **Clean the code you touch before you add to it** (added to `rules.md` 2026-10-30).
    Inside the files a change touches, remove the stray mid-file import, the dead branch,
    the stale number inside a string, the name that no longer fits. Not a repo-wide
    refactor — the cleanup stops where the change does. It may not change behavior a test
    pins; if it would, that is a separate change with its own evidence.

---

## 21. What to do next (ordered by unblocking power)

This reconciles the three live plans (`docs/PLAN.md`, `AI_ARCHITECTURE.md`,
`TRAINING_PLAN.md`). The subsections below are **chronological, newest last**
(P-1 → P-1l); each records what shipped, what it measured, and what it left owed. They
are no longer a priority order — the current ordering is **P-1j**'s last bullet and
§24's "Next correct move", and they agree: one live session on `2026-10-30a`, read the
proofs (including the first gate-shadow rows, of which there are currently zero), then let
the outcome labels accumulate before training anything else.

### P-1 — shipped today, the loop is proven to its first hard stop (2026-10-29)

**Why the loop sat on "the policy had no action to take" (467 passes, 172 s).**
Replayed from `autopilot.dofus-02.jsonl` (index 16206): the policy returned a read
every pass, the wait escalation proposed `farm.start` at every 40-pass window, the
quest gate refused it (*"a farm.start was refused because this prompt is a quest
goal"*), and the refusal only reset the wait counter -- so the same window repeated
~11 times and the run could neither act nor stop. Three fixes, all gated:
a kill objective bound to its own map now waits for a respawn for one window
(`SPAWN_WAIT_PASSES = 90`, ~half a minute) and then walks an exit to search
(`test_spawn_wait_is_bounded`); the escalation skips a `farm.start` the goal gate
would refuse, so the next candidate (a walk) is tried; and four whole windows that
end with nothing actionable end the run with *"the step needs a human or a new
plan"* (`DEAD_WINDOW_LIMIT`) instead of parking forever. The daemon in flight still
runs the pre-fix code: restart it before the next session.

**And the free-form half of the same stall (same day).** Objective 14070 is a
free-form step with no map: the planner tried the map's element, asked its NPCs, then
returned *nothing*, so the policy fell back to a read every pass. A different map
carries different elements and NPCs, so the step now walks to a neighbour once this
map is retired for it (`_exhausted_at`), and the new map's element is tried in turn
(`test_free_form_searches_new_maps`). If the search runs out of maps, the dead-window
bound ends the run instead of spinning.

**The equip verb is being refused by the client, and the reason was being
dropped (2026-10-29 audit).** `tools/session_report.py` now prints a `proofs:`
line per file: on `autopilot.dofus-02.jsonl` it reads `equip 6 ok / 76 refused`,
`fight 24 joins (12 named)`, `buy 0`, because the report can only be as honest as
the rows. Every refusal says *"equipItem sent nothing and the item is not usable;
pass slot to send ObjectSetPositionMessage directly"* and the page's own answer
(which positions the type allows, whether the template loaded, whether the bag
held the instance) was not on the step row. A failed step now carries
`evidence` with exactly those fields (`test_refusal_evidence`), so the next live
session names the cause instead of repeating the sentence.

**The equip slot is now inferred from a worn twin (page `ba`, planner today).**
The page's inventory publishes the bag as `{gid, qty, object_uid}` and what is
worn as `{slot, gid}`, so a second copy of a worn template names its own slot.
The planner passes `slot` with the uid when that join succeeds
(`_worn_slot_for`), and the page does the same client-side when `equipItem` sends
nothing and the type declares no `possiblePositions` — the exact shape of all 76
refusals. `tools/test_equip_slot_inference.js` executes the real handler; the
planner cases are in `test_free_form_uses_interactive`. Works on the page build
already loaded (`ay` honours `args.slot`), so the next live session can prove the
equip without a client reload.

**Waking a locked-out slot** (`tools/wake_login.py`): reads the snapshot, never
presses inside the cooldown, sleeps to its stated expiry, spends exactly one guarded
`session.login {target: guest}`, and reports the world or the new refusal. It is an
operator tool -- a banned slot is never pressed. With `--start-loop` it hands the
recovered world to `/brain/start` in the same step, so a lockout costs one press and
no operator. Running for dofus-02 while this note was written (cooldown expiry
~12:04Z).

**The live character was trapped in a map with no exit the client can use, and
three separate mechanisms kept it there (2026-10-29 15:00-15:20, dofus-05).**
The guest logged in on map 146276352 ("Albuera Village - Solo Area"), an interior
the client reports with four neighbours, `exits: []`, no `interactiveElements`
and no monster group; its only transition had been a scripted NPC reply, and the
dialogs no longer offered it. Diagnosis, in order:

1. **The loop's retirement was undone on the next step.** `_reopen` discarded
   every *active* quest from `_skipped` whenever a tutorial step was running, so
   the dead-window retirement of quest 3841 lasted exactly one step and the loop
   kept returning `state.snapshot`. Retirements now reopen only when their own
   situation signature changes (step, tracker step, map, gate) -- the same rule
   non-tutorial quests already followed.
2. **The planner waited for a door that never appears.** A map with neighbours
   and no crossable border made `_travel_call` answer `state.snapshot` ("waiting
   for its door") forever. It now asks the server for the neighbour with the
   client's own transition (`nav.goto {map_id, direction}`, rotated over the four
   edges per attempt). The page side (`bb`) sends `isoEngine._requestMapChange`
   when the client knows the neighbour but publishes no change cell -- the same
   `ChangeMapMessage` a tapped door sends, adjudicated by the server. The
   tutorial's own `can_change_map` gate is still honoured first.
3. **The dialog policy chose a courtesy line and closed the only exit.** The
   agent answered "Wish him good luck." to a quest NPC and lost the conversation
   that had offered "Bow and take your leave." (the actual map transition). The
   ranker keeps objective overlap primary, then inside a tie prefers: a reply that
   moves to another map when the objective is on one ("travel reply"), the reply
   the client marks with a quest icon, a reply that keeps the dialog open
   (courtesy closers last), and only then the question text.

`tools/test_nav_doorless_exit.js` runs the real `nav.goto` handler against a fake
client; `test_doorless_map_asks_for_the_neighbour` covers the planner rotation,
the retirement surviving `_reopen`, and the tutorial release; the dialog cases are
in `test_dialog_ranking`. Page build **`2026-10-29bb`**, gate **1,241 checks, 0
failed**. The page fix needs one client relaunch to take effect; the running
session was left alone until its login window cooled (~45 min after 15:00).

**Live result of that relaunch (15:46, dofus-05, build `bb`).** The guest logged
back in on the same map and the doorless fallback fired for real: the packet ring
shows `ChangeMapMessage {mapId: 146407936}` and `{mapId: 146276864}`, `ok: true`,
stage `change_requested_no_cell`. The server accepted none of them -- the map never
changed -- and the client's own CDN answered 403 for neighbour map 146276864, so
those neighbours are not loadable maps. A human must leave this solo area through
the scripted reply that the dialog no longer offers; the page cannot conjure it.
Two consequences shipped: the loop now counts crossings that never change the map
and ends the run with that reason after 12 (`NAV_DECLINE_LIMIT`), instead of ~100
silent refusals in two minutes (which is also anti-bot signal); and the guest was
left parked in the world with the loop stopped rather than hammering a dead exit.
The dialog ranker was verified on the live NPC that caused the original failure
(Forgelance Intellectual): it now picks "Ask what he means by that." and marks
"Wish him good luck." as a closer instead of choosing it.

1. **Done: rebuilt and fully restarted the host.** Page build `2026-10-29az`
   (ticket filter in the packet ring, seeded kick signatures clearable by an
   accepted validation, access-denied popup read as a ban, refusal decay), C#
   play-recorder redaction, `--launch <instance>` CLI and the bounded AI-autostart
   retry all ship in the rebuilt `DofusFarm.exe` (release suite, 213.0 MB).
2. **Done: the ticket scrub.** `python tools/scrub_packet_secrets.py --write`
   scrubbed the last 568 values once the host released `play_dofus-0{2,3,5}.jsonl`;
   the tool now exits 0 only when the corpora are clean.
3. **Live evidence on build `ay`**: the guest slot was refused ("Server busy"
   popup), the page armed an hour-long cooldown after two presses, and the loop
   now *ends* with
   `the guest login is locked out (Server busy; 55 min left after 2 refused
   attempts); ending the run -- wait out the cooldown or reset this slot for a
   fresh guest` instead of parking under "still waiting for the world". Refusals
   now decay once the served backoff passes, so the retry after that hour starts
   at ten minutes instead of jumping to three hours.
4. **Live proof owed**: equip (`inv.equip` with slot), buy (`hdv.buy`), one fight,
   and relogin identity — each is blocked on the locked-out guest; a per-slot
   Reset wipes the profile and needs the operator's consent. The lockout clears
   about an hour after the last refusal; run the session then.

### P-1b — the live proofs are account-blocked; equip gained its missing half (2026-10-29, later)

**What dofus-05 can and cannot do.** The guest is alive and in the world (same
identity, character 4434148, level 5, 11 kamas), but it is inside map
`146276352`: four neighbours, zero crossable cells, zero interactive elements,
zero monster groups, and three NPCs that answer nothing. (1) The tutorial is at
step 4970, *"Start a fight with a monster"* — its live step object ends on
`FIGHTER_TURN` — so the script itself now waits for a fight this map cannot
offer. (2) A full page reload re-entered the world in 9 s on the same map with
the same zero groups: the server does not spawn a scripted group on map load,
so re-entry is not the way out. (3) Talking was retried the correct way this
time — the client addresses NPCs by **contextual id** (`-1/-3/-4`, not the
template id) and the character stood on the adjacent cell 312 — and the server
still answered nothing; the three NPCs are mute from this map. The scripted
"Bow and take your leave." reply of P-1 is gone for good.

**Equip, measured.** The tutorial hammer (gid 1512, uid 85997419, type 25,
`possiblePositions: []`) is genuinely non-equippable: `equipItem` sends
nothing, `ObjectUseMessage` is accepted and changes nothing, and an explicit
`ObjectSetPositionMessage {position: 1}` is sent and moves nothing because
**slot 1 already holds gid 18844** (type 7, declared position `[1]`) — an
occupied slot does not swap. The tool had no way to free a slot, so equip had
no other half.

**Shipped: `inv.equip {remove: true}`** (aliases `unequip: true`, `slot: -1`).
It sends the bundle's own remove — `ObjectSetPositionMessage {objectUID,
position: -1, quantity: 1}` — and the three equip fallbacks (declared
position, worn sibling, `ObjectUseMessage`) all refuse to run for a remove.
`tools/test_inv_unequip.js` (13 checks, runs the real handler) covers it.

**Shipped: `tutorial.ending_conditions`** (build `2026-10-29bd`). The live step
object's `endingCondition` source names what the step waits for
(`conditionTypeEnum.FIGHTER_TURN` on step 4970); the page published
`tap_anywhere`/`hint_target` but not the condition itself, so nothing could say
"this step is waiting for a fight" while the loop re-asked for crossings. The
snapshot now carries the names in source order (`[]` when a step has none), and
`tools/test_tutorial_hint.js` grew three checks (FIGHTER_TURN published, no
duplicates, empty list for a step with no condition).

Page build **`2026-10-29bd`**, gate **1,245 checks / 0 failed**, Kaggle toolkit
synced. The running page is still `bb`: the live remove proof and the first read
of the new field both need the next client launch, and are owed together with
the buy and fight proofs.

**Accounts, as of this session.** dofus-01 permanently banned; dofus-02's
fresh guest banned within 40 s; dofus-03 banned on one press; dofus-05 is the
only live guest. Its day: login 13:46Z, page reload + re-entry 15:05Z, then at
15:09Z an `accessDenied` popup and a 434 s cold-down that the tooling read as a
permanent ban — wrongly. The page's cooldown record clears when the window
expires, and **one press at 15:18Z put the same guest back in the world** on the
same character. That re-login also settled the last open question about the
trap: the map came back with **zero groups**, so no tutorial fight is spawned on
login either. Every route to the three remaining proofs runs through this
character's map or a new account; a per-slot Reset wipes the profile and still
needs the operator's consent. Relogin identity is the one proof that is done,
three times over.

### P-1c — correction: it was a house, not a trap; doors are learned cells (2026-10-29, evening)

The operator proved it: map `146276352` is an ordinary house interior and the way
out is a **star painted on the floor**, not a border cell. Stepping on cell 451
moves the party to 145490435 (landing on 346); stepping on 346 outside moves it
back in (landing on 424). Nothing in the client publishes these cells — no `c`
flag, no interactive element, no door element — and *standing* on the door does
not trigger it, only stepping onto it does. So the P-1/P-1b conclusion was wrong
in the one way that mattered: the exit exists.

**Shipped (offline-verified; gate 1,254/0; build `bf` live, `bg` on disk):**
- a **door memory** in the page: every observed transition (agent's or a human's)
  records `(from_map, cell) -> to_map` in localStorage and exposes it as
  `map.doors`; ids of 0 (the login/transition "no map") are never recorded;
- `nav.goto {map_id}` uses a remembered door for a non-neighbour target, and when
  the character is already standing on the door it steps off and back on, because
  a transition lands you on the door cell;
- the planner walks a remembered door instead of asking for a border;
- interactives now carry `is_door`.

**Live proof, build `bf`, two consecutive cycles by map id only:**
`nav.goto {map_id: 145490435}` answered `via: ... (remembered door cell)` and left
the house; `nav.goto {map_id: 146276352}` answered `via: ... (door step off then
on)` and entered it; repeated twice, the session stayed in the world throughout,
and the learned cells survived a full host restart in between.

**The fight proof is done in the same run.** `fight.join` on the level-7 Merkaptan
group (map 145490945) started fight 647; the page's auto-fight won it in 18.8 s;
level 5 -> 6; the tutorial's step 45 ended on `FIGHTER_TURN` exactly as its own
bundle source says; a world tap then validated objective 14062. The tutorial now
asks for level 10.

First-visit discovery is still manual: a house the bot has never entered or left
has no remembered door and nothing in the client data names the cell (the star is
painted into the map art). After one transition the cell is known for good.

### P-1d — the tutorial is finished; objectives are now planned together (2026-10-29, night)

The operator's diagnosis was exact: the loop was "going around talking, no
quests". The log agreed — 54 waits and five dialog stalls over three maps, all of
them `quest.advance {quest_id: 3841}` against a step that says **"Reach level 10"**.
A level gate is not a conversation; the planner read one ready objective at a time
and answered the free-form gate with the only fallback it had (ask an NPC).

**Finished live, dofus-05, build `bf`:** level 8 -> 11 (and 13 by the end of the
evening) by fighting the tutorial-allowed groups on 145490434/145490945;
objective 14074 validated; quest 3841 left `quests_active`; `tutorial.active`
went `false` with the Help window opened; `quests_finished` 3. All further work
below ran on that same live session.

**Shipped (gate 1,269/0; brain daemon restarted, page unchanged at `bf`):**
- **objectives read together** (`_combined_objective_call`, planner): while a
  level gate is open, every ready/current objective is priced at once. A group
  holding a kill objective's monster wins ("one fight advances both"), a usable
  group on this map beats a trip, then the kill objective's map serves gate+kill
  in one walk, then the map where groups were last seen, then one edge search.
  Guaranteed progress goes first; the gate never falls through to a conversation.
- **quest pins from the quest graph** (`_quest_target_npcs`): the client paints a
  marker over quest NPCs but exposes no field for it (no `questIndicator` /
  `hasQuest` anywhere in the served bundle). The graph names the NPC in a
  talk/go-to-NPC objective's `parameter0`, so a pinned NPC is asked before a
  nearer bystander, and the `why` says which objective named it.
- **the ready set is a plan, not a queue**: free claims first (validation works
  from anywhere), then objectives whose map is here or adjacent, then the
  nearest. The live run had walked 145490434 <-> 145490946 because each step
  obeyed a different entry and its travel contradicted the previous one.
- **a crossing alone no longer reopens a retired quest**: `_situation_sig` no
  longer includes the map. That entry made the loop retire 4262 on 145490432,
  walk to 145490946 for its sibling, and reopen 4262 just because the map had
  changed.
- the ready loop now logs its own travel reasons (`[planner] objective ...`), the
  one decision path that used to be invisible in the daemon log.

**Live evidence of the new planner:** `[planner] ... fight.join {'group_id': -34472}
-- level gate 3841/14074: the character is level 8 and only a fight moves it`,
then `level 9` on the next two joins; after the gate closed, the ready set
travelled 145489921 -> 145489920 -> 145490432, tapped the aqueduct element
(513208, skill 136137092) and spoke to NPC 3950 — the chain's own step.

**Where the chain stops (new, measured):** quest 4262 "Personae Non Gratae in
Albuera", step 4954, objective 13948 "Recover the first page of the aqueduct
blueprints" (free-form, map 145490432, `parameter0` 704145 = a text id). The map
lists one interactive (513208); using it answers ok but the objective does not
complete, and no NPC on the map moves it. The run then retires both quests and
ends ("goals exhausted") instead of churning — which is the intended failure
behaviour, but the step itself is unsolved. Also seen: the ThinkingLayer's
`free-form step; ask a map NPC` heuristic forced talks on the wrong map, and one
`nav.goto {map_id}` was refused with "the right exit leads to 145490945, not
145490946" — a stale map read, refused by the page rather than acted on blind.

### P0 — diagnose and finish the live loop

1. **Explain the long "policy had no action to take" wait** seen at the end of the
   latest four logs (28–384 consecutive waits; three instances above 280). Read the
   last step/dialog/wait rows,
   the quest gate, and the tutorial hold. This is the immediate live blocker and is
   not yet in `KNOWN_GAPS.md`.
2. **Run one full session with page build `2026-10-29aw`** (full app restart, not a
   reload). Acceptance: `session_report` shows the `aw` build, `accept:` rows when a
   dialog offers a quest, `objectives +N` with N > 0, no `INACTIVITY`, no manual
   intervention, and `keep_alive.present` true.
3. **Prove one complete live fight** (G7): `fight.join` attacks one group, placement/
   ready complete, only the player's turns drive, a legal cast/move ends the turn,
   `GameFightEndMessage` arrives, and the quest resumes afterwards. Read the `brain`
   row and `live_policy_decisions.jsonl` `contract.value_ready` to answer "who decided
   the turns".
4. **Add the equip verb** (G2) because the first recorded quest chain dies on
   "Go and equip your new items": read-only inventory/presets shipped; capture the
   live accessor and the drag/preset packet, then add `equip.preset` behind the schema
   gate.
5. **Identity by account** (G9): live relogin proof that account A and B in the same
   slot get distinct `identity/*.txt` and distinct relay `-serial/-uuid`.

### P1 — make the learned parser and foresight real

6. **Run a live command-shadow session** (`--command-shadow auto`) and read
   `command_shadow_report.py` + `command_replay.py`. The parser is deployable but has
   never seen a live prompt. Gate: overrides are legal and advance; no held-out base
   loss.
7. **Fix the simulator's initial-state diversity** (§9.2): seed 0-kamas, no accepted
   quest, zero-parameter objectives; regenerate `tool_sft`; re-read `feature_skew`.
   This is the blocker for any sim→live foresight claim; bigger nets do not fix it.
8. **Re-measure foresight gate 2** (live move AUC, calibration, base rate) on the
   regenerated corpus and real transitions. If it still cannot beat chance, do not
   wire it.
9. **Run unattended sessions** (`tools/auto_live.py`) so outcome labels accumulate
   without a human. The runner already does preflight → verified launch → guest press →
   run; the missing piece is a session that produces non-zero objectives.
   `python tools/collect_live_data.py` now follows the changing daemon fleet
   read-only, without a default instance or deadline. Explicit `--instances` starts
   or reuses only that cohort; `--steps 200 --rebuild` additionally requests a rebuild
   after successful finite completion. Timeout/Ctrl-C never stop clients.
   `python tools/build_entity_pointer_dataset.py --check` scans all trajectory logs
   without writing. It requires ≥2,000 action pairs, ≥50 progress-labelled action
   rows and ≥10 rows per tool before any rebuild. The current check finds **4,018 /
   34**, with **20 undercovered tools** and zero rows for the four new tools; it
   exits 1 and preserves the old dataset. Collector/coverage tests pass (49/13);
   no live proof or new training follows from those tests.

### P2 — promote one component at a time

10. **Escalation head + macro/micro split** once the curves are calibrated. The
    foresight net's `flat_state` flag is the seed of the escalation decision.
11. **First rule removal candidate: tool choice under a quest objective** — only after
    held-out outcome + live shadow gates. Keep the fallback.
12. **Dialog policy** — train on real reply→outcome labels (`tools/dialog_labels.py`);
    gate above the ranker, then shadow. The talk path is where the run loses points.
13. **Verifier** — labels from action→next-state deltas; gate above the rule baseline
    with honest ECE; only then may the GUI auto-load it.

### P3 — combat and world coverage

14. **Combat mechanics** — AoE footprints, buffs, traps, push/pull, AP/MP removal;
    predicted-vs-observed damage calibration from live fights.
15. **Fight calibrator** — ≥20 resolved live fights with wins and losses; ECE < 0.10;
    then `p_win` can gate unattended fights.
16. **Combat ranker** — only after the solver plan execution is live-proven; it must
    not invent spell IDs/cells/actions.
17. **Knowledge spine M1** — merge the observed Touch quest catalogue (18k objectives
    per character) into the store; resolve every live objective to typed entities;
    achievements and interior map names.
18. **Persistent exit / proxy model** — a VM or paid static proxy for session
    persistence (auth may still refuse datacenter IPs); train the hop-selection model
    only after it beats the heuristic on held-out sessions (`PLAN.md` #4).
19. **CDN pool question** — compare 1 vs 3 asset egresses; no increase in 403s.

### Explicitly not next

- A larger language model, a 100–135M dialog expert, MoE, or a latent bus. The budget
  is not the bottleneck; coverage and live outcomes are.
- Rewriting the client form or the relay for microbenchmarks.
- A warm tunnel pool.
- Unattended buying or account creation.

### P-1e — training-pipeline audit: the labels, not the model, were the problem (2026-10-29, night)

The audit asked one question: can anything here learn a better tool choice than
`QuestPlanner`? Measured answers, in code and in `docs/KNOWN_GAPS.md` G39:

- **The sim label recipe has no headroom.** `tools/bench_quest_policies.py` on 10
  held-out seeds (400 steps): rule 79.90 objectives, learned 80.50 (5W/5T/0L),
  **oracle 79.80 (3W/1T/6L)**. The oracle *is* the fork-argmax label; it loses to the
  teacher. The old `policy_faceoff` was scoring on corpus seeds.
- **The derived dataset was two days stale** because only `auto_live` rebuilt it for
  its own logs. `tools/refresh_live_dataset.py` rebuilds from every trajectory
  (deterministic, 9,687 rows / 457 segments). `BrainDaemon._ingest_live` now runs it
  when a run's thread ends (one job, 60 s debounce; active at the next daemon start).
- **The `moved` label credited reads.** 195 reads carried `moved`; 173 sat right
  after a `quest.advance`. The builder now attributes a change first seen in a run of
  reads to the acting step (`label_version` 2): 170 move- and 6 progress-credits moved
  back, action move rate 16.26% -> 19.47%, progress 71 -> 77.
- **Global live AUC is phase, not action.** The 42-feature row describes the objective
  and the run; the action enters only as a one-hot. Phase-only within-segment accuracy
  on progress goes 0.902 (val) -> 0.297 (test) - it inverts. Action-only looks strong
  (0.901/0.897 tool-discordant) but that is action-vs-read, which is 1.000 by
  construction once a read cannot own an outcome.
- **Among actions, tool order is not identified.** Rates fitted on train, scored on
  held-out segments: moved 0.708 val / 0.355 test; progress 0.560 / 0.860 on 150/57
  pairs. The rates come from 21-46 rows per tool. The log is observational - one
  action per state, no counterfactual - so more rows cannot identify a better tool.
  The simulator had counterfactuals and does not transfer (live move AUC 0.46 vs 0.64
  for a live-trained head).

- **`progress` was 6x too generous; the strict label is `advance`.** Of the 77
  progress rows only **11 complete an objective** (the rest are "became claimable"
  or "quest accepted"); the test split holds **zero** advancement events. The builder
  now emits `advance` beside `progress`.
- **The corpus is worth ~600 independent rows, not 9,393.** ICC by segment 0.241 /
  0.287 / 0.082 -> n_eff 604 / 513 / 1,580; the minimum relative change visible at
  80% power is **45.6% (moved), 192% (progress), 289% (advance)**.
- **Off-policy questions are not identifiable here.** Cross-fitted propensities
  (out-of-fold tool accuracy 0.423) give ESS **19-44 rows (0.2-0.5%)** for uniform and
  no-read targets, single weights up to 125,000. An unrandomized corpus prices an
  off-policy question at ~0.4% of its rows; a 50/50 randomized arm gives ~50%.
- **The alternative supply exists.** On 420 recorded live turns, 351 (84%) have a
  distinct second plan (retire the chosen quest, ask again); 56% of derived rows hold
  >=2 ready objectives and 3,090 read-steps had two objectives waiting.
- **`fork()` was dropping seven attributes** (`_interactive_tries` et al), so any
  counterfactual that reached a free-form/tap/equip/spawn-wait branch crashed; every
  simulator label came from forks that avoided them. Fixed and guarded.
- **The exploration arm is built, tagged, priced, and off.** `agent/explore.py`
  computes the planner's second plan at every offered state *before* the coin, so
  each row carries `has_alternative` and the exact `p_explore`; epsilon outside
  (0, 0.5] is refused. Tags ride `ToolCall.meta -> executor -> recorder ->
  transitions`. Simulator: 197 objectives / 70 quests at epsilon 0-0.5, 0 stalls,
  0 explorations (one ready objective per state) - so the sim cannot price it.
  `tools/explore_report.py` is the pre-registered read (randomized-only comparison,
  segment-clustered CI, propensity audit, balance, IPW) and, with no data, prints the
  sample-size table priced at the measured **5.8 s/step**: progress base 1.46%,
  **epsilon 0.2 -> +/-1pp in 3,445 steps (~5.6 h), epsilon 0.05 -> 11,605 (~18.7 h)**.
  The live run is the user's call.
- **The 44 MB knowledge store is legacy for live quests (G40).** Measured: 1,976
  quests, ids 18..2546, all `dofusdb_cache`; none of the five live quests; 2 of 3
  sampled live objective ids exist under *other* quests (the G30 collision);
  `verified_live` 0. No-AI licence, so no re-sync and no training. It stays a
  fallback plus collision evidence. `tools/knowledge_index.py` is the live
  authority: 57 objectives / 9 quests from four client sources, 0 conflicts, text
  78.9%, map 45.6%, 217 step objectives with no catalogue owner, `field_sources`
  per field, `--check` reproducible, atomic writes; an independent recount matches
  it 57/57 with 0 capture mismatches. Deferred step binding fixes a file-order
  dependency (10,843/19,527 in-pass vs 19,310). The daemon's post-run ingest now
  rebuilds the index after the dataset, so a new session's objectives are indexed
  before the next run asks for one. Runtime order: snapshot catalogue -> client
  index -> store.

- **Every live-outcome number was cross-validated across the same days (G41).** The
  hash split puts all 8 days, 6 characters and every phase in train, val and test;
  `data/transitions_temporal` cuts the same 9,687 rows by segment start time (test =
  10-28/10-29, 1,060 actions). Moved AUC on test: hash `both` **0.830**, temporal
  `both` **0.634**, `phase_only` **0.375**, `action_only` **0.724**, and a state-free
  **tool-rate floor 0.723**. The state features memorise phases and invert out of
  time; only the tool identity travels. The builder now emits `at` + character
  {id, level, breed} and the manifest carries characters/levels/days/span; `--split
  temporal` cuts by segment start order; the probe prints the tool-rate floor on
  every fold; the daemon's post-run ingest rebuilds both datasets before the index.
  Corpus census: 6 characters, levels 1..22 (median 3), 8 days, 20 instance-days,
  8.05 h; strict `advance` = 0 in temporal train, 8 val, 3 test. The next learned
  scorer must beat 0.723 on the temporal fold before it is wired anywhere.

- **The outcome label counted walking (G42).** `moved` = any watched key changed; over
  2,848 `quest.advance` calls, 636 moved but **318 changed only `cell`** and only 59
  touched quest state. The builder now derives a per-tool `claim`/`claim_met` from the
  args and the returned snapshot (nav.goto at:<map> or one step, fight.join in_fight,
  quest.advance quest state, tap.interactive an effect, inv.equip inventory, farm.start
  farm_state, reads none). Claims met: nav.goto 13.1% (moved 16.2), quest.advance 2.1%
  (22.3), fight.join 4.8% (52.4 — 1 of 21 entered a fight), tap.interactive 4.3% (67.4),
  inv.equip 4.9% (0.0), farm.start 0%, safe.stop 0%. The probe reports `claim_met` as a
  third target on the judgeable rows: temporal test phase 0.641 / action 0.724 / both
  0.714 / tool rate 0.723 (hash: both 0.865) — the target no longer inverts out of time,
  but still does not beat the floor. Two live questions stand: `fight.join` ok 21 times,
  `in_fight` once; `inv.equip` ok 41 times, inventory never changes.

- **The after-state was the pre-action snapshot (G43).** Over 2,139 `nav.goto` calls the
  directive snapshot showed the move 5 times; the next step's observation showed it 449.
  The builder now uses the next observation as the after-state (snapshot only for the last
  step of a segment): moved 1,030 -> **1,863**, progress 77 -> **130**, strict advance 11 ->
  **51**, claims met 345 -> 788, nav.goto claim 13.1% -> 32.2%. Folds now hold events
  (hash test 23 advances, temporal 13/24/14). Re-probed: moved both 0.789 vs floor 0.727
  but within-segment 0.646 vs 0.637; progress floor 0.774 still wins; claim_met
  action_only 0.877 = floor, both 0.757.
- **Context features do not predict success inside a tool (G44).** The 24-feature mechanism
  block scored 0.856 on claim_met before the repair; the decomposition showed `args_only`
  0.891, `state_only` 0.724, and inside `nav.goto` state-only **0.468**. A lookup on (tool,
  argument kinds) scored 0.902 - the signal is call shape, a rule, not a model. After the
  after-state fix, mechanism_only fell to **0.434** on the temporal fold. Nothing wired;
  `test_mechanism_features` pins the vector's contract (no tool identity inside).

- **Travel success is set by the map/period, not by the call form (G45, corrected).**
  Corpus aggregate: direction 25.0% moved, map-targeted 82.0% — but split by the temporal
  folds it is a Simpson's paradox: train direction 255/1,494 (17.1%) and map **0/12**;
  val 27.4% / 81.2%; test 81.9% / 85.2%. Map calls concentrate in the later period where
  movement worked at all. The real finding is the fallback rate: in train the planner
  emitted 1,494 direction hops against 12 map-targeted ones (planner.py:1321 fires when
  its route's hop map is not among the live exits). Travel is 62% of action time, so this
  is the largest throughput lever measured. Instrumented as `nav_form` and
  `nav_form_by_split`; the earlier "map beats direction by 20 points" gate assertion
  encoded the confound and was replaced.
- **The loop decides on a carried state (G47).** 4,153 of 4,344 steps (95.6%) acted on a
  carried observation, median 707 ms old (p90 1.77 s); rows now carry `obs_age_ms` /
  `obs_source`. Age is not a predictor (temporal test AUC 0.428 nav.goto, 0.485
  quest.advance), so staleness does not explain away G41/G44.

- **Planner reasons were thrown away, and decisions cannot be replayed offline (G46).**
  `why` went to stderr only; `ToolCall.why` now rides `record_meta()` into the recorder and
  the transition row (args untouched), and context exits keep `direction`. Replaying a
  fresh `QuestPlanner` on 300 recorded nav observations reproduces only 2: the rest become
  `quest.advance`, because retired quests and attempt counters decide travel. Offline
  counterfactuals are not reproducible, not merely unidentifiable — the epsilon arm stays
  the only source. Existing rows carry `why: null`; coverage starts with the next run.

- **An identical retry almost never works (G48).** After a `nav.goto` claim miss, the
  same request re-issued 1,330 times met **2.3%** (3% with no read between, 1% with a
  read); changing the direction met 38%, changing the map 78%. The control after a *met*
  call is 77%/93%, so the gap is specific to retries, and the map never moved during an
  identical retry - these are not late arrivals. The row now carries `repeat_streak`
  (same tool **and** same args, canonical JSON), which predicts failure at **AUC 0.851**
  inside `nav.goto` on the temporal test fold (0.720 inside `quest.advance`; hash test
  0.920/0.708). The pooled `claim_met` model still does not transfer: the val fold's
  0.736 vs floor 0.528 becomes 0.556 vs floor **0.877** on test, because the test period
  is 78% nav-met / 6% quest-met - tool identity is the only thing that transfers there.

- **No learned head beats the counter (G48).** `--within-tool` fits one head per tool and
  compares it to the two streaks with a *paired* segment bootstrap: temporal `nav.goto`
  delta -0.135 (-0.503, 0.151) on test (0.888 streaks vs 0.753 context), `quest.advance`
  -0.014 on val but +0.116 (0.040, 0.180) on test - a sign flip between folds, so not
  evidence. The counter is the shippable artifact.
- **`quest.advance`'s claim counted a talk as a failure, and was fixed (G49).** A talk
  (`npc_id`, no reply) promised a quest-state change and met it 0.8% of the time (13/1,639),
  although 401 of 1,546 talks issued outside a dialog **opened one** by the next
  observation. The claim is now per form - talk -> `in_dialog`, reply -> quest state,
  accept -> active - and `quest.advance` met goes 93/2,848 (3.3%) to 344/2,039 hash train,
  85/463 hash test (18.4%), 133/408 temporal test (32.6%). Changing args never helped this
  tool (2% either way, 1,223 tries), so the nav retry lever does not generalise; the quest
  failure was a label defect plus a state gate. Inside `quest.advance` the counter is now
  0.780 and the mechanism model 0.800 (133 positives), context delta +0.020 (0.006, 0.067)
  on test, +0.099 (-0.060, 0.166) on val - small and consistent. Pooled it is still the
  floor's game (0.750 floor vs 0.740 mechanism_only), so nothing is promoted.

- **A reply's claim counted the wrong event (G50).** The page publishes the dialog node
  (`message` + reply ids) whenever a dialog is open; the corpus recorded only open/closed,
  so replies were scored on quest-state change (6.7% met). Against the node identity, 119
  of 160 judgeable replies (74%) moved the node or closed the dialog. The reply claim is
  now "the node changed" (unknown when there is no before-node); replies are met 146/157 in
  hash train and 104/128 on temporal test (81%). The lesson: with the mixed G49 label the
  mechanism model scored 0.800 inside quest.advance, with the mechanical label it scores
  0.652 and the counter 0.769 - the more honest the mechanics label, the less state predicts
  it, because a node move is decided by the dialog script. Keep `claim_met` (mechanics) and
  `advance` (progress, still only ~51 events) separate.

- **The counter is a gate; the state-change policy is not (G51, G52).** On the temporal test
  fold `repeat_streak >= 2` vetoes 11-18% of calls that would have met ~2-3% of the time and
  keeps 99%+ of the successes (paired delta CI 0.658..0.719 for quests, 0.0..0.574 for nav).
  `miss_streak` must not gate quests: the calls it would veto met 65-77% (CI -0.29..-0.06),
  because a talk miss is followed by a reply that usually works. The natural richer policy -
  retry only after the state changed - looks decisive on train (nav 26/28 = 90% vs 0.3%) and
  **fails out of time** (13% vs 16%, quests 19% vs 26%); it is a 28-row artifact. The hazard
  is a step at the second consecutive miss, not a smooth decay, and the raw counter is not a
  clock (train has 21/37 = 57% at exactly 3, because the counter persists across state
  changes). Fit the step; do not fit a line through the counter.

- **Progress, not claims, is the binding constraint (G53).** The `repeat_streak >= 2` gate
  loses 0 of 11 advances and 0 of 27 progress events on the temporal test fold (hash: 3 of
  39, all train). Over 4,943 judgeable actions: 1,385 claims met (280/1,000), 126 progress
  (25.5/1,000), 51 advance (**10.3/1,000**). Action time is 7.8 h over a 157.8 h span - a
  ~5% of wall time inside segments, and 100 advance events need ~9.7k actions = 15.4 h
  of action time = ~4 days wall-clock. The 5% is a lower bound, not a duty cycle: segments
  split on 120 s silences, so every long hold becomes a new segment and the corpus cannot
  separate offline from waiting (0.0 h of within-segment waiting). Later content is 7x
  denser than the tutorial (25 vs 3.5 advances per 1,000 quest calls), so the fix is
  advance-dense content, not more tutorial hours or a bigger model.

- **The gate replicates across four rolling-origin cuts (G54).** Train on the past, score
  the next 15% of segments, at 40/55/70/85% of the timeline: every cut has kept-met above
  vetoed-met and AUC(fail) above chance (nav 0.655-0.939, quest 0.592-0.973) across base
  rates from 1% to 80%. The weakest is the 70% cut (19 and 9 vetoed calls), thin not
  negative. `repeat_streak >= 2` is a rule, not one period's luck; what the loop does
  instead of a refused call is still unpriced. What the loop does next after 2,707
  high-streak failures: repeats the same rule 55% of the time (0.4% met), reads 28%, and
  switches to a different action 17% of the time (**76% met**) - the replacement exists and
  works; the loop just rarely takes it. A read is not a recovery: read-then-same-rule meets
  0.2% (worse than repeating directly), read-then-different 30%. The gate must force a
  re-plan, not an idle wait.

- **A forest ranks better, the counter still wins the veto (G56).** Temporal fold, per tool:
  forest AUC 0.930 on nav vs the rule's 0.760, but at 15% veto it keeps 98.5% of successes
  against the rule's 99.8% at 11%; boosting (AUC 0.888) is the worst veto (93.5%). On quests
  every model lands on 99.1%. AUC and the operating point are different objectives - the
  forest is a candidate ranker for choosing among kept calls, not a better veto. A first
  run of this comparison was invalid (reads counted as failures); the corrected numbers are
  in G56.

Gates: 1,410 checks / 0 failed; `tools/sync_kaggle_toolkit.py --write` run after the
edits. New guards: `test_dataset_split_integrity`, `test_temporal_split_plan` (start
order, undated segments, one-segment case), `test_claim_labels` (each tool's promise,
walking is not a quest advance, a read makes no claim, manifest `claim_pct < moved_pct`),
`test_live_within_segment_eval` (tool-rate floor), `test_ingest_after_run`,
`test_ope_estimators`, `test_fork_carries_the_planner_state`, `test_knowledge_index`
(ordering, manifest, provenance, atomic write), plus the lag case in
`test_live_transitions` and hermetic index paths in the two knowledge tests.

The next correct move is **identification, not capacity**: either bounded live
exploration (teacher plus a legal alternative on a small, safety-gated fraction of
steps) or a hand-built candidate prior, evaluated only on within-segment
tool-discordant pairs. Until one exists, the pipeline can calibrate the teacher but not
beat it; the game-side wall is still quest 4262 objective 13948 on dofus-05.

### P-1f — the representation route was tried and stopped by its own bar (2026-10-29, late)

- **Phase 0 shipped, shadow-only.** `agent/gate.py` holds a `RuleGate` that counts
  consecutive misses per identical rule (`tool|args`); the autopilot attaches
  `_meta["gate"] = self._gate.shadow(...)` to every call, observes the verdict after it,
  and clears on the same two session-reset paths as the no-op memory. The corpus row
  carries `gate`, and `tools/live_outcome_probe.py --shadow` prices what the veto would
  have done and whether the loop repeated the rule anyway. It sends nothing.
- **Phase 1/2 merged.** The recorded observation already contains `map.npcs`,
  `interactives`, `groups`, `exits` with cells, so no page change was needed.
  `agent/state_features.py` turns it into a 20-value global vector and 12-value
  entity rows relative to the character.
- **G57: the encoder failed the kill criterion and is stopped.** Pre-registered bar:
  beat hand-built features by >= 0.03 AUC on the temporal fold. Within the clean label
  regime (10-28/29, first 60% -> last 40%): hand-built 0.658, world-model predicted
  deltas 0.645, encoder+deltas 0.647 (3 seeds). Across days the encoder ties (+0.02) on
  a vocabulary-shifted fold. At the operating point the counter vetoes 7.6% of successes
  against 23-65% for every learned variant. Keep hand-built features + the counter.
- **Two protocol traps found on the way.** (1) Pretraining the world model on the test
  rows' own `(state, action) -> next state` pairs lifted the encoder to 0.806; masking
  those 457 deltas dropped it to 0.50-0.61, so the lift was memorisation. (2) The
  per-row "segment" id made both the cluster CI and the boundary cut no-ops; with real
  ids the within-regime test is **8 sessions**, which cannot separate 0.61 from 0.66.
  Claim-kind tables are now printed per split before any AUC (early days have no `at:`
  claims and dialog means the opposite side of the G49/G50 fix).
- **The binding constraint is still sessions, not architecture.** The next correct move
  remains identification: bounded live exploration or a hand-built candidate prior, on
  within-segment tool-discordant pairs. G57 adds that no representation-side trick
  substitutes for it.

Gates: 1,436 checks / 0 failed; `tools/sync_kaggle_toolkit.py --write` run after the
edits. New guards: `test_rule_gate` (shadow semantics, reads do not reset, args are the
rule), `test_gate_shadow` (the record survives the corpus build and the report),
`test_state_features` (fixed widths, relative cells, nearest exit cell, zero-degrade,
delta flags), `test_state_encoder_split` (temporal cut on a segment boundary, no
overlap, held-out deltas masked from pretraining).

- **G58: the forest at the rule's own veto count is a candidate ranker, not a replacement.**
  G56 compared at a fixed 15% veto; `--rolling-models` scores both at the number of
  vetoes the rule actually takes on each cut, with the counter as a model feature.
  `nav.goto` successes kept, forest − rule: +12 at cut 0.40 (0.449 vs 0.275 kept-met),
  +6 at 0.55, +3 at 0.70, **−3 at 0.85**; pair CIs touch 0 and the gains ride on 2-4
  sessions. `quest.advance` shows nothing at any cut. The counter stays the veto; the
  forest is a candidate to shadow (its score beside `gate.would_veto`), bar = kept-met
  above the rule at matched veto across the next two live sessions. Not a change, a
  request.

- **G59: the deployment rehearsal reversed G58, so nothing is shadowed.** `tools/train_veto_shadow.py`
  freezes one model on every day before a cut and scores the cut day only. Cut 10-29:
  `nav.goto` kept-met 0.899 vs the rule's 0.899, `quest.advance` **0.664 vs 0.691** with
  14.3% of its vetoes hitting successes against the rule's 2.9% (paired delta −0.40 per
  session, CI −0.80..−0.10); pooled kept 0.740 vs 0.744. Train AUC 0.983 vs test 0.774
  (rule alone 0.762), and lower capacity does not rescue it (d2x60 0.801, logistic 0.824,
  both still below the rule at the operating point). The rule is correctly set on the new
  day (nav 0→93%, 1→26%, 2→0%; quest 0→71%, 1→53%, 2→8%). So G58's +12/+6/+3 was an
  artifact of retraining inside each cut; the frozen artifact under `models/veto_shadow/`
  is evidence of the rejection, the loop loads nothing, and `test_veto_shadow_guard`
  pins that. Any future veto model must pass the trainer's unseen-day test first.

Gates: 1,439 checks / 0 failed; `tools/sync_kaggle_toolkit.py --write` run after the
edits (184 sync, 8 pinned, 0 drift).

### P-1g — the gate's rule identity dropped its volatile fields (2026-10-30)

- **G60.** The three taps on element 513208 carried skill_uid 136137029/136137092/136137181
  and the character's cell, so `tool|args` saw three rules and the streak never reached 2
  on the tool that fails 44 of 46 judged calls. `RuleGate.key` now drops `lang`,
  `skill_uid`, `cell`; the builder writes `repeat_streak_norm` beside the untouched
  `repeat_streak`. On four rolling cuts the change is exactly additive: nav and quest
  identical, taps +2 vetoes (3→4 at 0.70, 7→9 at 0.85) with no extra success taken.
  `test_rule_gate` and `test_gate_shadow` pin both keys.
- **The task now needs live evidence (G59).** The loop loads no learned veto; the frozen
  model lost to the counter out of time, and the counter only improves by ignoring
  volatile args. Everything else measured this pass says the same thing: the next gain
  is a live session, not a model.
- **Wall note.** dofus-05 ends on quest 4262 / objective 13948 "Recover the first page"
  (item 704145, map 145490432); the map's only interactive returns ok with no effect and
  the item is not in its 6-item inventory. dofus-03 completed 13948 on 10-28 right after
  a claim on that map, so the claim path works once the condition holds; how the page is
  acquired (interactive, group drop, or dialog) is the open question, and the inventory
  now records gid/qty/object_uid so the next session can see it arrive.

Gates: 1,444 checks / 0 failed; `tools/sync_kaggle_toolkit.py --write` run after the
edits.

- **G61: the live loop ping-pongs between two maps, and now says why.** On 10-29 dofus-02
  made 229 map changes of which **216 were immediate A->B->A returns** (175 between
  145489923/145490435) in a 12.6 h run; dofus-05 made 204 changes with 132 returns (101
  between 145490434/145490946) in 17.8 h, alternating while holding ready objectives on
  the two maps. Neither a stateful replay of the 519 live decisions (279/519 tool
  agreement, diverges onto quest 4274) nor a clean two-map fixture (planner taps once,
  travels, waits - no alternation) reproduced it, so the cause is not established. The
  blocker to attribution was the corpus: **0 of 617 rows carried `why`**. `_log_plan`
  now stores the deciding objective and reason, `choose` stamps it onto `ToolCall.why`,
  and `record_meta()` already writes it to the row — `test_plan_reason_recorded` pins it.
  The next session turns the 101/175 returns into attributable branch records.

Gates: 1,447 checks / 0 failed; `tools/sync_kaggle_toolkit.py --write` run after the
edits (1 fix before the sync, 0 after).

- **G62: the bounce is the tutorial equip gate.** dofus-02 alternates between the map
  holding the tutorial's interactive (145489923) and Ayin Spyr's map (145490435): tap ->
  walk back -> talk -> "Go and equip your new items" -> repeat. The page reports each set
  item's declared position: 18844 type 7 -> slot 1, 18846 type 16 -> 6, 18848 type 17 -> 7
  (all equipped), and **18974 type 210 category 3 declares none** (`equipItem` silent; it
  has never been worn in any session, and 18976's slot 23 was client-set, outside the
  page's 0-16 window). A real bug surfaced: `equipped` carries `{slot, gid}` and no
  object uid, so the planner's worn set was always empty and it re-offered worn pieces
  (70331924-27 "already worn" at 01:41). Fixed by retiring a template when the worn count
  covers every bag copy; `test_equip_skips_worn` covers worn/free/ambiguous and the twin
  fixture now matches the client (the bag lists equipped copies). The page now also
  publishes each worn row's `object_uid` (the live ObjectItem carries `objectUID`;
  `test_inventory_equip.js` 32/32), so on the next build the skip is exact. The decisive
  live probe is `inv.equip {object_uid, slot: 8}` (then 9..16) then re-talk to 3888.

Gates: 1,450 checks / 0 failed; `tools/sync_kaggle_toolkit.py --write` run after the
edits.

- **G63: the planner bounds immediate returns.** The cause of the G61 bounce is still
  unattributed, so the loop now bounds the *pattern*: a ready objective whose travel
  target is the map the character just left counts a return, the first three are made,
  and the next one parks that objective for the session with one log line. Parking
  suppresses only the walk - the objective still acts when it is already on its map - and
  the counter resets on a different destination or when it leaves the ready set.
  `test_return_bound` covers walk/refuse/per-objective/no-false-park; `fork()` carries the
  new state; the frozen replay still passes unchanged.

Gates: 1,454 checks / 0 failed; `tools/sync_kaggle_toolkit.py --write` run after the
edits.

### P-1h — the equip question is settled offline; the client's refusals are now read (2026-10-30)

- **G62 correction, from the client's own tables.** The served bundle assigns each item
  type its `category` and `possiblePositions` from `superTypeId`; the category enum is
  `{equipment:0, consumables:1, resources:2, quest:3, ...}` and `superTypeNotEquippable`
  includes `QUEST_OBJECT:14`. The live row for gid 18974 is `type_id 210,
  type_category 3, possible_positions [], usable false`: a quest object that is not
  usable. `equipItem` sends nothing for it by design and the `ObjectUseMessage` fallback
  is ruled out, so **G62's `slot: 8..16` probe is withdrawn** - a direct
  `ObjectSetPositionMessage` has no semantics to learn from. The same trajectory pairs a
  four-item bag with three worn rows (18844/1 weapon, 18846/6 hat, 18848/7 cape): every
  warable item in the bag is on. If those three are the recruit set, 13902 only has its
  talk half left; the client's own reply is what will say so. dofus-02's last run predates
  G63, which is why it still read as a bounce.
- **G64: the refusal text is captured.** The bundle routes TextInformationMessage to
  `gui.chat.logError` (ERROR) or `logMsg`; the page now wraps `logError` once, keeps the
  newest five messages (collapsed, 200 chars) and publishes `client_errors` only when
  non-empty. `Autopilot._note_client_error` prints each new text once on a non-advance
  verdict and resets on progress; it is a witness, not a decision - no counter, gate or
  plan changes. `tools/test_client_errors.js` (16 checks) plus
  `test_client_errors_reach_the_loop` pin the wrapper and the wiring.
- **G65: a refused call was credited as a met claim (labels v3).** The bridge nests the
  page's refusal at `result.response.result.ok=false`; the builder judged a call only by
  the world diff, so 63 of 945 refused steps read `claim_met: true` because the world
  moved for another reason. Now a refusal is a miss, carries the reason in `error` and a
  new `refused` field, owns no `moved`/`progress`/`advance`, and `_attribute_lagged` never
  hands it a later read's change. Datasets rebuilt (label_version 3): 621 refused train
  rows at met 0 / moved 0 / progress 0; quest claim met 452/1,942 = 23.3% (was 26.3% with
  the false credits). The rule re-measured: `repeat_streak>=2` vetoes 66% of quest calls
  at 2% met, keeps 40.9% met and 97.2% of successes (nav: 47% veto, 2% met, 64.3% kept,
  96.4% of successes); AUC(fail) 0.655-0.973 over four cuts. `test_refused_calls_are_not_credited`
  pins it. **Correction:** dofus-03's "13948 completed right after a claim" on 10-28 was
  this bug - every claim there was refused as `known_kick_signature`, and the objective
  moved with no agent action (tutorial special step 1 ended between the two snapshots).
  No validation claim has ever been shown to complete 13948; the world action is still
  the open question, and the current build refuses the claim one step earlier with
  `objective_server_counted`.

Gates: see the run below; `tools/sync_kaggle_toolkit.py --write` run after the edits.

---

### P-1i — the first learned worker is pre-registered, and the count gate already wins (2026-10-30)

- **New mode:** `python tools/live_outcome_probe.py --calibration --data data/transitions_temporal`.
  It fits p(met | call form, normalized streak bucket) with a Beta prior on train only, chooses the
  veto threshold on train, and scores the frozen fold at the counter's own number of vetoes - so no
  gate can look better by vetoing more calls. Forms are the promise the call makes
  (`nav.goto:step`/`:at`, `quest.talk`/`reply`/`accept`, `tap.interactive`, ...).
- **Measured (both folds):** the calibrated count gate **does not beat `repeat_streak_norm>=2` at
  equal budget**. Temporal: counter vetoes 168 (6% of them met), keeps 0.678 met and 98.3% of
  successes; calibrated at the same 168 vetoes keeps 0.611 and 88.6%, paired CI (-0.121, 0.012).
  Hash: 0.431 vs 0.434, CI (-0.038, 0.044). Its wins at its own threshold come from vetoing
  1.5-3.7x more calls, which is a different trade, not a better picker.
- **Why the counts do not transfer:** `nav.goto:at` is 0/12 met in temporal train and 238/336 in
  test (form-level ECE 0.676); `nav.goto:step` is 17% met in train and 93% in test. `quest.reply`
  is the one form where the calibrated score wins at its own threshold (kept 0.67 vs 0.55, CI
  0.093..0.143; hash 0.61 vs 0.52, CI 0.062..0.119) but retains 0.896 and the counter's budget is
  9-8 vetoes, below the pre-registered comparison floor of 10.
- **The bar, now written down (`docs/TRAINING_PLAN.md`, constants in the probe):** one shared state
  encoder + per-form outcome heads, and it may not replace anything unless, at the counter's veto
  budget on the frozen temporal fold and >=3 of 4 rolling cuts, it keeps >=5 points more successes
  with a paired segment CI excluding 0, retains >=95% of successes, and fails open to the rule.
  A form is eligible only with >=400 met rows over >=3 start periods. No form qualifies today, so
  **no worker is trained**; the binding constraint is live data, not capacity. Gate:
  `test_claim_calibration` (16 checks), registered in `run()`.
- **Follow-up, the only place left to win (`--worker`, G67).** The state block (24 relational
  mechanism features, streaks excluded) was fitted per form on train and scored on the frozen fold.
  Temporal: `quest.talk` kept AUC **0.472**, `quest.reply` **0.398**; `nav.goto:step` 0.645 but its
  kept set is 175 rows with 165 met, so the 5%/10% extra-veto layers remove 8 and 17 calls that
  **all met** (CI -0.056..0.0 and -0.094..0.0). Hash: `quest.talk` looks promotable (5% layer
  removes 5 calls, 0 met, CI 0.004..0.052), but the temporal fold inverts it and the nav layers
  remove half to three-quarters successes - the same within-period pattern as G57. **No worker
  ships.** The bar for a second layer is fixed in `_extension_ok`; `test_worker_headroom_layer`
  (14 checks) pins it and the two `worker:` mutations are in the driver.
- **Live remains blocked:** `tools/live_ready.py` reports the egress upstream not listening, the app
  not running and a stale page build, so the repeatable-4260/4262 session cannot start this pass.
- **The release build was broken by a stale tracked builder.** `builder/Build.exe` (tracked, Oct 17)
  predates `build.cs` (Oct 27) and its embedded source list omits `agentbridge.cs`/`aipanel.cs`/
  `pages.cs`/`netsettings.cs`, so every build failed with CS0246 even though the gate's
  `test_csharp_compile` (which reads `build.cs`) passed. Recompiled `build.cs` with the Framework
  csc and replaced the tracked binary; the release build now exits 0 (11 bundle patches,
  `DofusFarm.exe` 565,248 B, `uninstall.exe` 17,920 B, `setup.exe` 214.0 MB with a 213.8 MB
  standalone-WebView2 payload) and `live_ready` reports the installer PASS. Egress and the app
  remain the only live blockers.
- **The 13948 tapes, read properly (G65 addendum + G68).** The transition context dropped every
  element's `skill_uid` and `enabled_skills` (it read `skill_id`, which the page never writes), so
  no row could say whether the page element offered a usable skill. Fixed and both datasets rebuilt
  with identical labels (9,687 rows / 457 segments / 5,289 actions / 1,322 claims met; probe numbers
  unchanged). The rebuilt rows show element 513208 at cell 330 carrying skill instance
  136137181/136137092/136137029 while the character stood at 504/549 on every recorded tap - a
  hand-built message from another cell is a silent refusal. The page now uses
  `isoEngine.useInteractive` (walks first); `tools/test_interactive_use.js` (13 checks) pins that
  route and its fallback, and 2 `use:` mutations are caught. The context now also keeps the
  element's `type`/`door` (the old logs say `element_type_id: -1` for all five, so the kind was
  never resolvable offline); the captured client cache shows map 145490432's midground layer
  places 513208 at cell 330 as sprite gid 401228 - an ordinary map object, not a ground item.
  Whether the use completes 13948 from cell 330 is still owed a live run: dofus-05's post-fix
  attempt did not.
- **G69 - the pages were earned, and the corpus could not see it.** The builder excludes
  `play_*.jsonl`, and the only successful use lives there: dofus-03 (2026-10-28) walked from 517 to
  cell **345** (adjacent to element 513208 at 330), sent `InteractiveUseRequestMessage
  {elemId 513208, skillInstanceUid 136113675}` at 1793211230499, the server acked
  `InteractiveUsedMessage {skillId 184}` at ...232719, and validated **13948** at ...232786 (67 ms
  later). 13950 followed the same way 603 ms after using 513212 from cell 299 (adjacent to 285).
  Every recorded agent tap was sent from a distant cell (504/549, or 299-328 without a skill), which
  is why "nothing worked". The current page walks first via `isoEngine.useInteractive` and
  `tap.interactive` settles for up to 3 s (the successful request-to-ack took 2.2 s), so the
  mechanic is implemented; a live run on `2026-10-30a` is what is owed. No claim is involved.

### P-1j — the summary was audited against the tree, and the tree disagreed (2026-10-30b)

This document had been written from measurements for §0–§2 and §12–§14, but §3–§5, §7–§12
and §16–§18 were still the 2026-10-27 reading. Re-measuring all of it produced corrections,
four defects, and one new rule.

- **§3–§4 (topology and size):** `bridge_port.txt` is **8896**, not the documented 8893,
  and `agentbridge.cs:39` walks 8893–8899, so any hardcoded 8893 is a bug. The disguise is
  injected in `WebView2Backend.cs`, not `WebView2ClientForm.cs`. Sizes were understated
  across the board: Python 76,699 → **98,704** lines in 259 files (now 99,335 after this
  day's own tests and probe modes), Go/JS 13,516 → 24,332,
  `mobile-disguise.js` 7,834 → 11,254, trajectories 257 MB → **438.5 MiB** in 29 top-level
  logs, models 466.0 MiB, asset cache 103.6 MiB, docs 21 files.
- **§5 (host):** log rotation is **64 MB**, not 10; the AI page has **no policy combo and
  no step-budget field** (`PinDecisions()` forces `planner` + `steps 0`); the grid has no
  port column (12 columns, `mainform.cs:776-798`); `Activate()` has **no retry** (the doc
  claimed a fix that does not exist); `--bundle-selftest` exits 0/1/2/3, not 0/1; the start
  path is the **daemon**, the per-instance child is the fallback; `verdicts_human.jsonl` is
  written by `agent/verifier.py`, not the host. New §5.10 records what the chapter had never
  mentioned: system-wide `RegisterHotKey` shortcuts that can partially fail, `auto_ai=1` as
  the default (launching a client starts its loop), fail-closed as a launch gate, per-instance
  AI state, the watchdog/governor/hardware layers, and `Reload client`.
- **§7/§9–§12:** the patch selftest now matches **23/23 rules with 0 skipped** at 5,365,280
  chars (the doc described the 1.74.9 16/7 split); the learned-vs-rule **face-off was
  withdrawn** by its own gap entry, so "21W/9T/0L, 66.10 vs 64.87" is now shown as invalid
  with the honest 79.90 vs 80.50 (5W/5T/0L, p = 0.0625); `live_policy_decisions.jsonl` is
  **810 rows** not 14; `SETTLE_TOOLS` is **5** not 4; the 30M grid model **is trained**
  (val top-1 0.7857 / test 0.60 on 1,318 actionable distilled boards, `live: false`); the
  NetworkHealth TCN is **144,016 params** not 52,816 and still has no checkpoint; the
  verifier head is 16 tool slots inside 33 features; the corpora were restated to
  9,687 rows / 5,289 actions / 1,322 claims; the SQLite spine is 44,044,288 B; and the
  "40.5 MB JSON" source no longer exists as a file.
- **§16 (the one that actually bites):** the host **never passes `-config`** to the relay,
  so `network.ini` is not read by the launched proxy at all — only the C# model parses it
  and re-emits per-instance flags; §16.6 now documents `runtime.ini` (absent, so `auto_ai=1`
  etc. are defaults), `ai_goals.txt`, the **two** `AUTOPILOT_STOP` locations (both currently
  present in LOCALAPPDATA), `bridge_port.txt`, `kick_signatures.json`, the BOM'd
  `app_version.json`, the flag files, and `--explore-objectives`. The "12/6/6 instance caps"
  premise was false: that is `NORMAL_CAPS` for combat AP/MP/range, and no max-instance
  constant exists.
- **§18:** two rows rewritten and six added, including the bridge-port, daemon-vs-child,
  64 MB rotation, `session_report` and toolkit-drift cases.
- **Four defects fixed, each with checks (gate 1,503 → 1,540, exit 0):** toolkit drift on
  `tools/quest_witness.py`; `session_report.py` raising `OSError: [Errno 22]` on the
  millisecond stamps of `play_*.jsonl` (§2.1's footprint numbers are its output);
  `mutate_cause_gate.py` leaving a live mutation in `agent/autopilot.py` after a hard kill —
  it now journals and self-repairs (`--recover`, `test_mutation_journal_recovery`); and
  `live_ready.py` judging the page build from `default.jsonl`, which the per-instance daemon
  no longer writes, so it reported `2026-10-27s` while a `2026-10-29bf` run was in the same
  directory.
- **The new rule came first, and paid for itself immediately:** `rules.md` now has a Change
  policy (clean the code you touch before adding to it). Applying it removed a duplicate
  mid-file `import os`, corrected `rules.md`'s own stale 25-tool vocabulary plus the same
  count in `CURRENT.md` §7 and `CAPABILITY_REGISTRY.md`, and found that the "All 25 tools"
  string in `aipanel.cs` sits on a label with `Visible = false` — dead source text, so the
  §5 claim that users were reading a wrong number was itself wrong and has been corrected.
- **`README.md` was in the same state, so it was corrected too** (2026-10-30b): installer
  224,413,696 B rather than "≈288 MB"; bridge candidates 8893–8899 with `bridge_port.txt`
  = **8896** instead of "seen at 8894"; **23 patch rules, 23 matched, 0 skipped** at
  5,365,280 chars instead of the 13-rule/11-patch sample; **28 tools** with `ui.tap`,
  `ui.icons` and `shop.buy` added to its table; gate **1,540** checks and `advisor` 91
  instead of 276/122, with the C# compile, combat/data suites and mirror drift now inside
  the gate; the AI page's missing policy/budget fields, the 12-column grid with **no port
  column**, the pill's **four** shapes and daemon-driven colour, **64 MB** log rotation,
  `verdicts_human.jsonl` attributed to `agent/verifier.py`, selftest exits 0/1/2/3,
  `data/cache` at 32,626,819 B, `asset_cache` at 7,428 files / 103.6 MiB, trajectories at
  29 files / 236,961 rows, `networkhealth_artifacts/` **absent** (that is G5), the TCN at
  144,016 params, `inference/` as a package, the `-config` caveat, the `exit_proxy` key,
  the missing-instance-ceiling truth, and its docs index (G1–G69, D1–D24, CURRENT's 28
  tools). Its §14 troubleshooting gained the bridge-port and daemon-vs-child rows.
- **The gate shadow's wiring was proven rather than hoped.** No log has ever carried
  `agent/gate.py`, so the plan for the next live session rested on an untested assumption
  that the annotation reaches a row. `test_gate_shadow_reaches_the_trajectory` (10 checks,
  registered in `run()`) now drives the real `RuleGate`, the real `TrajectoryRecorder` and
  the real `Autopilot._run()` against a capturing executor and establishes three things:
  the streak behaves as measured (`0 → 1 → 2`, `would_veto` at two, cleared by one
  `advanced`, never started by a read-only tool, and three re-taps of one element counting
  as **one** streak because `VOLATILE_ARGS` drops `cell`/`skill_uid`/`lang` — G60); the
  annotation reaches the **trajectory** row through `recorder.py:386` but structurally
  cannot reach the loop's own step row, because `Step` (`autopilot.py:258-292`) has no
  `meta` field — so §24's live checklist was pointed at the wrong file and now names the
  right one; and `VETO_AT = 2` is the loop's existing no-progress judgment
  (`streak >= 3` / `_recent[-3:]`, `autopilot.py:2078`) reached **one repeat earlier**,
  per-call instead of per-rolling-three. The test's own run output showed the whole
  sequence: three `noop` nav.gotos, then `no progress: nav.goto repeated 3x with no
  change`, `REPLAN (plan_wrong)`, `abandoning 'push right'`.
- **Still owed after this pass:** any run that carries a gate-shadow row (the newest logs
  contain **zero** `"gate"` keys, so the shadow is proven in code and in no way in data),
  a `2026-10-30a` live session, and `CURRENT.md`'s remaining pre-G41 numbers.

### P-1k — the veto got its price, and the mutation driver lost a false witness (2026-10-30c)

- **G70 measured.** `python tools/live_outcome_probe.py --alternative --data
  data/transitions_temporal`. Cost of wiring the gate: 1.68 % of successes on the temporal
  test fold (retention **0.9832**), 3.12 % on the hash fold (**0.9688**) — G51's "keeps 99 %+"
  survives the corrected labels. Benefit side: when the loop is at streak ≥ 2 it already
  changes the request most of the time, and the changed request meets
  **45.5 %** (temporal) / **63.9 %** (hash), while repeating the same promise meets
  **0.0 % / 0.5 %**; the paired segment CIs are (+0.304, +0.603) and (+0.296, +0.872), so
  neither touches zero. G55's 76 % was a label artifact, which is the second time this
  programme has had to re-price its own decision input.
- **What that changes for the plan.** The veto no longer has an unmeasured side, so
  "wire it" is now a proposal with numbers instead of a missing measurement. The probe
  states the remaining limit in its own `verdict` block: the alternative was *chosen* by
  the loop, not assigned, so the causal claim still needs the epsilon arm (G39). What the
  arm is now worth can be bounded: ~18 % of the 168 candidates repeat, and their observed
  payoff is zero.
- **Pinned:** `test_veto_alternative_priced` (10 checks) registered in `run()` and in the
  mutation driver's snippet; two `alt:` mutations, one caught by name, one honestly missed
  as an equivalent mutant.
- **Driver flaw found while doing it:** the mutation snippet ran the Kaggle-mirror drift
  check, and since a mutation edits a mirrored file, drift fails for every mutation — so
  "CAUGHT" was guaranteed and the batches proved nothing about the checks. Removed from the
  snippet (still in the gate). The `calib:`/`worker:`/`use:` verdicts recorded in P-1g/h/i
  are therefore of unknown value and should be re-run before being quoted.
- **Also this pass:** `test_gate_shadow_reaches_the_trajectory` (10 checks) proved the
  shadow's wiring offline — and corrected §24, which had pointed the live checklist at the
  loop's step rows when only the recorder's **trajectory** rows can carry `meta`
  (`Step` has no such field, `autopilot.py:258-292`).

### P-1l — the mutation re-audit found the promotion bar was not enforcing its own rule (2026-10-30d)

- Re-ran `calib:` / `worker:` / `use:` after removing the mirror-drift witness from the
  mutation snippet (P-1k). **`calib:` was 5/7, not the 7/7 this document had recorded.**
  `worker:` 2/2 and `use:` 2/2 confirmed.
- **`calib: a tie with the counter is promoted` escaped for a real reason:**
  `PROMOTION_DELTA_POINTS = 0.05` — "a learned head must beat this count gate by this
  much" — was read by nothing. Any interval clearing zero promoted. Fixed in
  `_promotion_ok(stat, counter_kept)`: the point gain must clear the floor too, and
  `promotion` now reports `min_delta_points` and `counter_kept_success` so a reader can
  see which part of the bar failed. Recorded as **G71**.
- **`calib: a form is not shrunk toward the global rate` escaped for the opposite reason:**
  an equivalent mutant. The fixture has one form, so the global rate *is* that form's
  rate and the Beta prior changes no number — the check could not fail whatever the code
  did. Fixed by making the mutant observable rather than by asserting harder: a second,
  nearly-empty form (`fight.join`, one met row) pulls `1.0` to `(1 + 8·global)/(1+8) ≈ 0.57`.
- Batch now **8/8 caught**, each named by its own check, with `restored: 0 failing checks`
  and the tree verified unchanged afterwards. The tie mutant's anchor had to be rewritten
  after the fix, which is its own lesson: an anchor that no longer matches prints SKIP,
  counts as uncaught, and vanishes from a filtered log.
- Consequence for the plan, not just the test suite: promotion got **harder**. Nothing in
  G66/G67 changes — the calibrated score and the state block still lose — but a future
  candidate now has to beat the counter by five points, which is what the pre-registration
  always said.
- **The same defect class, swept.** G71 is "a constant that states a policy and is read by
  nothing", so every module-level `UPPER_CASE` constant in `dofus-network` was counted in one
  token pass over 237 files: **exactly 6 have no read anywhere**. Each was then checked
  rather than deleted — an unreferenced constant is not automatically a bug.
  `agent/foresight.py`'s `DEPTH = 5` / `HIDDEN = 128` are **correct**: the shipped
  `models/foresight_v1/foresight.json` reports `depth 5, hidden 128, feature_count 42,
  params 31,865` and a 21-entry tool list, so the documentation-of-shape matches the
  artifact (this is the check the TCN failed, where the doc carried 52,816 params for a
  144,016-param model). `AREA_TYPES = {5}` in `agent/features.py` is not a missing bucket:
  a type-5 "find the area" objective has no nameable destination, so
  `QuestPlanner._explore_call` answers it structurally by walking an exit — now annotated
  in place, because an unused entry in a list of buckets reads like a hole.
  `networkhealth/training.py`'s `ARTIFACT_DIR` names the directory G5 says is absent and is
  not a default (every writer takes an explicit `out_dir`) — annotated. The remaining three
  (`advisor/spells.UNLIMITED`, `agent/tool_eval.GOLD_BY_ID`,
  `pipeline/dofus_eval.VALID_NPC_OPTIONS`) are dead or legacy-derived and were left alone.
  **One silent policy hole in the whole brain, found and closed; five explained.**

### P-1m — the canonical running-system doc was corrected, not just indexed (2026-10-30e)

- §22.1 called `dofus-network/docs/CURRENT.md` the map of the running system, and P-1j left
  it holding pre-G41 numbers. A doc a new session is told to read first is not neutral when
  it is stale, so every stale claim there was replaced with the measured one:
  - **§4 face-off** — the "learned 5W/5T/0L" run that G57-style measurement retracted is now
    labelled **Withdrawn (measured 2026-10-27, retracted 2026-10-29)** and states the two
    numbers the retraction actually produced: rule retention **79.90**, learned **80.50**,
    5W/5T/0L, `p = 0.0625`, and the oracle losing. The sentence that had presented it as
    current fact is gone.
  - **new §4.5 "The streak gate (`agent/gate.py`) — shadow-only, and now priced"** — what
    `RuleGate` counts, why `VOLATILE_ARGS` drops `lang`/`skill_uid`/`cell`, that
    `_meta["gate"]` lands only in recorder **trajectory** rows, that **zero** such rows
    exist, that `VETO_AT = 2` is the loop's own `streak >= 3` bound one repeat earlier, and
    the G70 price plus G71's now-enforced three-part bar.
  - **§1 bridge port** — `CANDIDATE_PORTS` is `{8893…8899}`, `DEFAULT_PORT = 8893`
    (`agent/bridge.py:22`), and the live `bridge_port.txt` says **8896**; the doc no longer
    implies 8893 is the running port.
  - **§4 release/blocker** — `setup.exe` **224,413,696 B = 214.0 MiB**, and the real
    `live_ready` verdict instead of a claim of readiness: **FAIL egress** (no
    `127.0.0.1:40000`, `fail_closed=1`), **WARN client** (host not running), **FAIL page
    build** (last client log is `2026-10-29bf`, the build is `2026-10-30a`).
  - **§6 combat** — rewritten to what the data supports: fights **are** won live (22 rows,
    9 `won: true`, `fight_id` 124, 900, 1356, 1358, 1359, 1482); what remains unverified is
    **calibration** and the battle-log read. Same section, the live gate now names the
    current page build rather than the retired one.
  - **§7** already carried all 28 tools from the earlier pass.
- **The same pass cleaned prose it touched in `resume.md`**: §9's "CURRENT.md and
  CAPABILITY_REGISTRY.md both still say 25" was false by then — `CAPABILITY_REGISTRY.md` has
  carried a 28-tool status note since 2026-10-30 — so it now states which doc said what and
  when, and §22.1's CURRENT.md row lists each correction instead of dismissing the file as
  "pre-G41".
- **What this closes from P-1j's owed list:** only the doc item. Still owed, and all of it
  behind the same user-side gate: a `2026-10-30a` live session — the first gate-shadow row,
  the first command-shadow row, the 4262 claim, the battle-log read.
  The stale-count defect class is now closed for the three docs a new session reads first
  (`resume.md`, `README.md`, `CURRENT.md`); `AI_ARCHITECTURE.md` and `TRAINING_PLAN.md`
  remain dated snapshots and §22.1 labels them as such.

---

## 22. Documentation index and freshness

### 22.1 Where to read what

| file | role | freshness |
|---|---|---|
| `dofus-network/docs/CURRENT.md` | running-system map: services/ports, 7-stage loop, pacing, policy modes, model inventory, combat pipeline, tools | **current as of 2026-10-30e**: §7 lists all 28 tools; §1 carries the measured bridge port (8896, candidates 8893–8899); a new §4.5 documents the shadow-only streak gate with G70's price and G71's enforced bar; §4's learned-vs-rule face-off is marked withdrawn with the honest numbers; §6 says fights are won live while calibration and the battle-log read are not; the release/blocker line carries the real `live_ready` output; the grid gate names the current build. Remaining offline caveat: its model-inventory metrics still trace to the 2026-10-27 artifacts |
| `dofus-network/docs/BRAIN_DAEMON.md` | one-process daemon: `/brain/*` API, per-instance threads, locks, host integration, remaining RSS/WebView2 work | **current** (2026-10-27) |
| `dofus-network/docs/ENTITY_POINTER_ARCHITECTURE.md` | target 80M/40M/30M entity-pointer design, pointer action space, sub-goal stack, Kaggle training and gates | **design** (2026-10-27); blocked on live data, not on design |
| `dofus-network/docs/AI_ARCHITECTURE.md` | canonical AI map: shipped/shadow/target, component inventory, gates, measured/rejected, build order | **2026-10-28** — still says the first live fight is unproven and 1,024 checks; §2/§12/§13 here supersede both |
| `dofus-network/docs/KNOWN_GAPS.md` | open defects with evidence (**G1–G71**) | **current** (2026-10-30), 6,698 lines |
| `dofus-network/docs/BASELINE.md` | live baseline before the 2026-10-26 fixes | **historical before-picture** |
| `dofus-network/docs/TRAINING_PLAN.md` | training programme, budget, phases, gates | **current** (2026-10-30: carries the pre-registered promotion bar and both negative results) |
| `dofus-network/docs/PLAN.md` | older living status/ordered work list | partly stale; status superseded by CURRENT/AI_ARCHITECTURE |
| `dofus-network/docs/PROJECT_LOG.md` | decisions D1–D24 with falsifiers | current reasoning, not status |
| `dofus-network/docs/ARCH_REASSESSMENT.md` | architectural argument | reasoning; status columns superseded |
| `dofus-network/docs/AGENT3_PLAN.md` | three-agent/dialog/knowledge design | design, not shipped |
| `dofus-network/docs/AGENT2_DESIGN.md`, `SECOND_AGENT.md` | earlier second-agent designs | historical |
| `dofus-network/docs/FAILURE_TO_SUPERVISION.md` | how failures become labels/tests | current |
| `dofus-network/docs/PROTOCOL_NOTES.md` | captured wire formats | current, grows with captures |
| `dofus-network/docs/PIPELINE.md` | data path corpus→train→serve→log | current, widths superseded by CURRENT |
| `dofus-network/docs/PLANNER_DESIGN.md` | tier architecture, RAM budget, no token CoT | its `hdv.buy` example predates the 25-tool vocabulary |
| `dofus-network/docs/EXPERIENCE_LEARNING.md` | outcomes → posteriors without a reward model | current |
| `dofus-network/docs/LATENT_AND_ONLINE_LEARNING.md` | latent bus/online updates judgement | current decision, not shipped |
| `dofus-network/docs/CAPABILITY_REGISTRY.md` | tools as data | **note corrected 2026-10-30 to 28 tools** (it said 25; its worked example still discusses the 16-object era by design) |
| `rules.md` (root) | the owner's standing instruction file: capability policy, the "no moralizing disclaimers" rule, and (added 2026-10-30) the **change policy** — clean the code you touch before you add to it | **current** (2026-10-30). Its tool count was stale at 25 and now says 28 with the list; `data/capabilities.json` remains the authority and the gate asserts it |
| `README.md` (root) | high-level overview, quick start, architecture | **corrected 2026-10-30b**: installer 224,413,696 B, bridge 8893–8899 (`bridge_port.txt` = 8896), 23 patch rules matched 23/23, 28 tools, gate 1,540 / advisor 91, pill's four shapes, 64 MB log rotation, no policy/budget field in the GUI, `-config` is never passed to the relay, `networkhealth_artifacts/` is gone. Its §1–§9 prose still describes the 2026-10-27 build in places it was not touched |
| `STATUS.md` | 2026-10-23 reasoning + kick history | superseded in part |
| `PLAN.md` (root) | 2026-10-23 next steps | historical |
| `now.md` | long historical record, 2026-10-23 | historical |
| `COMBAT_STATUS_AND_PLAN.md` | combat work + phases | historical; ordering caveat in its header |
| `CLEANUP.md` | cleanup findings (duplicate scripts, dead copies) | historical |
| `resume.md` | this document | **snapshot 2026-10-30, re-verified against the tree.** §0, §2, §12–§14, §19–§20 and §22 were rewritten from measurements; the 2026-10-30b pass then re-measured §3 (ports), §4 (every size and line count), §5 (host claims, plus a new §5.10 of behavior the chapter had never documented), §7.3 (patch selftest), §9–§12 (corpus sizes, model widths, the withdrawn face-off, the trained grid model, the TCN, verifier width, SETTLE_TOOLS), §16 (including the `-config` trap) and §18 (six new rows). §6 and §8 were claim-checked and found accurate. What is **not** verified is stated where it appears: anything requiring a live session is labelled owed, and anything measured only by reading a doc is labelled as such |

### 22.2 Numbers that changed; do not quote the old ones

| stale claim | current truth |
|---|---|
| 16 tools (`STATUS.md`) | **28 tools** |
| 18 tools (`now.md`, older command notes) | **28 tools** |
| 21 tools (older docs) | **28 tools** |
| 25 tools (`CURRENT.md`, `CAPABILITY_REGISTRY.md`, this document — all corrected 2026-10-30) | **28 tools** — `ui.tap`, `ui.icons` and `shop.buy` were added with the tutorial work and never propagated to the prose; the gate has asserted 28 since (`regression.py:208`). `TRAINING_PLAN.md`'s "25 tools × 8 causes × 13 families" is a description of when that corpus was cut, not a registry claim |
| 11 patches (`README.md`) | **23 rules, 23 matched, 0 skipped** — `bundle-selftest.txt` (re-read 2026-10-30) lists all 23 ids and `RESULT: IDENTICAL` at 5,365,280 chars. This document's earlier "16 matched, 7 skipped" was the 1.74.9 bundle |
| 276 checks (`README.md`) | **1,540 checks / 0 failed** in `agent.regression` (measured 2026-10-30; `README.md` §15 corrected the same day) |
| 892 / 1,024 / 1,241 / 1,486 / 1,503 / 1,514 / 1,517 / 1,527 / 1,537 checks (this document's own earlier snapshots, `AI_ARCHITECTURE.md`) | **1,540 checks** — the gate grows every session; quote the number you just ran |
| 13 patch rules / 11 active | **23 rules** after 1.74.10 |
| 288 MB `setup.exe` (`README.md`) | **224,413,696 B = 214.0 MiB** since the stale `builder/Build.exe` was recompiled; it carries a 213.8 MB standalone-WebView2 payload (the 100.1 MiB / 105.7 MB / 288 MB numbers were earlier builds — `README.md` now gives the measured byte count) |
| G1–G34 (`KNOWN_GAPS.md`, this document until now) | **G1–G69**, 6,619 lines |
| "10 objectives, 0 quests completed" as the live score (this document §0 until now) | the counter is a signed delta across session boundaries and the two log views disagree; the hard proofs are 9 won fight rows and 3 attributed objective validations (§2.1) |
| "the first end-to-end fight is unproven" | proven six times over: `fight_id` 124, 900, 1356, 1358, 1359, 1482 |
| "live fight: not proven" (`AI_ARCHITECTURE.md` §9.4 era) | see the row above; the *boss* fight is still unproven |
| verifier auto-loads on every GUI launch | **gated** on a live/human validation contract; rules-only today |
| "the client update broke everything" | updater is transactional and re-patches; 1.74.10 proven live |
| "WARP works" | WARP's UDP is blocked on this line; `auto`/TCP proxy is the working path |
| "objectives_completed 0" as a model failure | it was a structural instrument bug (G28), fixed; latest logs show gains |
| "the learned policy is not wired" | `--policy planner` is wired; this machine's GUI selects it |
| "the model is 24.9M" alone | 24.9M is the **text trunk**; the command heads are 0.29M, the tool policy 49K, foresight 31K, combat ~0.2M |

---

## 23. Glossary

- **Egress** — how traffic leaves the machine (tunnel, SOCKS/HTTP proxy, or direct).
- **Pin** — the one exit IP a live game session is locked to.
- **Relay** — `dofus-net-proxy.exe`, the local HTTP/CONNECT proxy + asset cache.
- **Bridge** — the host's local HTTP endpoint that turns a directive into a page call.
- **Directive** — one JSON tool call posted to the bridge and executed by the page.
- **Tool** — one of the 21 legal capabilities in `data/capabilities.json`.
- **Step** — one observe→choose→guard→execute→settle→verify→decide iteration.
- **Verdict** — `advanced` / `noop` / `failed` / `blocked`.
- **Drift** — an explainable contradiction between the before/after observation.
- **Gain capture** — share of the best fork's progress a ranker achieves.
- **Fork / counterfactual** — replaying a state with each candidate tool to label the
  outcome without asking the teacher.
- **Harness** — the offline symbolic world (`sim/`) used to generate labels and score
  policies; not proof of live behaviour.
- **SFT** — supervised fine-tuning.
- **INT8 / ONNX** — the shipped quantized/runtime model formats.
- **ECE** — expected calibration error.
- **AUC** — area under the ROC curve; 0.5 is chance.
- **TRM** — Tiny Recursive Model; weight-shared recursion, tested and rejected as the
  trunk here.
- **MoE** — mixture of experts; deferred.
- **Full Auto** — `steps=0`, no wall-clock, autonomous goal loop.
- **Order mode** — one goal for a small step budget.
- **M0–M5** — the milestone ladder in `AGENT3_PLAN.md`: observation contract, dialog,
  fight estimate, strategist merge, TPU self-play.

---

## 24. Final honest summary

**State at the 2026-10-30 snapshot** (this is the current one; everything below it in
this section is history, kept because its warnings still apply):

What is measured now, with the number and where it came from:
- **fights are won live**: 22 rows in `trajectories/fight_outcomes.jsonl`, 9 `won: true`
  over `fight_id` 124, 900, 1356, 1358, 1359, 1482 — the last entered by
  `quest.advance`, not by a hand-made `fight.join`;
- **the stuck quest objectives have a cause and a fix**: `tools/quest_witness.py` reads
  `events 216,975 / accepted uses 39 / validations 111 / attributed 3`, and the
  attributed rows show 13948 validated **+67 ms** after an interactive use on the
  adjacent cell, and 13950 ×2 **+255/+256 ms** after another. Every failed agent tap was
  sent from a non-adjacent cell (G69);
- **the training corpus is honest for the first time**: five label defects were found and
  fixed in sequence (G41 leakage across days, G42 walking, G43 pre-action snapshot, G65
  refused calls credited as met, G60 volatile call identity, G68 dropped skill instance),
  ending at 9,687 rows / 1,322 claims met with `label_version: 3`;
- **four routes to a learned veto were measured and all four failed their own bar**
  (G57 encoder, G59 frozen forest, G66 count calibration at equal budget, G67 state
  block). `agent/gate.py` shadows the incumbent counter and refuses nothing;
- **the gate is green**: `python -m agent.regression` → **1,540 checks / 0 failed, exit
  0** on this tree. Four defects were found and fixed while writing this snapshot: Kaggle
  toolkit drift on `tools/quest_witness.py` (cured with `sync_kaggle_toolkit --write`), the
  `session_report.py` millisecond crash that made every protocol capture unreadable, the
  mutation driver leaving deliberately broken code after a hard kill (it now journals and
  self-repairs via `--recover`), and `live_ready.py` reporting the page build from
  `default.jsonl` — a log the per-instance daemon no longer writes — so it claimed a stale
  client while a `2026-10-29bf` run sat in the directory. Each fix carried its own checks,
  which is why the count moved 1,503 → 1,540;
- **the release build works again** after the stale tracked `builder/Build.exe` was
  recompiled: `setup.exe` 214.0 MB, `DofusFarm.exe` 565,248 B, exit 0;
- **and the one thing that is not done**: page build `2026-10-30a` has never run. Every
  fix in the two bullets above that concern the interactive use, the refusal credit and
  the three-return park is offline-verified only. Four slots have live rows and the newest
  of them is 2026-10-29 18:50 UTC.

Honest scorecard (0–10, measured, not aspirational):

| Capability | Score | Why |
|---|---|---|
| Client shim, relay, host, bridge | 9 | Survives updates, one egress, per-instance addressing, reset button; bridge fast-fails on a wedged page; the release path exits 0 again as of 2026-10-30 |
| Quest automation | 5 | Objectives validate live and the interactive mechanic is now attributed (G69), but `2026-10-30a` has never run and **no quest is finished end-to-end** — zero `QuestCompletedMessage` rows exist |
| Combat (tutorial mobs) | 7 | Solver mechanics (cells, LoS, push/pull/teleport, AP/MP) work; move head executed live; **six won fight ids** — still no boss and the battle-log ring has never survived a live end message |
| Combat (bosses/strategy) | 3 | A real move policy and the critic exist; zero boss data, zero kiting benchmarks |
| Learned command parser | 4 | 93.9% tool / 69.8% call offline, routed per-instance adapter accepted, **still shadow-only** — every log prints `command_shadow: no status/row` |
| Learned decision replacement | 2 | Four measured rejections (G57/G59/G66/G67); the incumbent streak counter is correct on four rolling cuts but runs **shadow-only** and prices no alternative — and it has **zero recorded rows**, since every existing log predates `agent/gate.py` |
| Data and observability | 8 | Five label defects found and fixed in one programme; refusal text, witness attribution, `proofs:` counters and per-instance recovery all in the reports. Docked one point because `session_report.py` still crashes on `play_*.jsonl` |
| Always-on fleet | 3 | 194 manual interventions across four slots (140 on one), two permanent guest bans on record; 40-instance scaling unproven |

**The dream model** is not a bigger network. It is one always-on per-instance brain
that sees the client's own state (quest graph and dialogs, battle log and challenges,
map interactives, inventory/market), chooses among the 28 tools, lets the solver own
legality and the learned critic own plan choice, and improves from the outcome rows it
writes. Every input that model needs is now captured; what it lacks is volume of lived
outcomes and promotion gates for the learned heads. The gap is data and verification,
not architecture or capacity.

**Next correct move (2026-10-30):** one live session on page build `2026-10-30a` — a full
app restart, not a reload — that walks 4262 from 13948 through the claim, takes 3–5
fights so the battle-log ring is read out of a real end message, and runs with
`--command-shadow auto` so the parser gets its first live row. It must also produce the
first gate-shadow evidence: check `trajectories/dofus-XX.jsonl` for
`"meta": {"gate": ...}` — the loop's own `autopilot.*` step rows cannot carry it, because
`Step` has no `meta` field (`autopilot.py:258-292`). Then
`tools/quest_witness.py`, `tools/session_report.py` and `tools/damage_calibration.py` on
the new capture. Only after that is another training run worth compute, and no second
representation route should be attempted before the ≥40-test-session revisit bar G57
set.

The project has also solved the hard *plumbing* problems:

- a real client that survives updates and stays disguised;
- a relay that caches, routes, pins one egress, and fails closed;
- a host that launches, logs, and addresses many instances;
- an agent loop that observes, acts, settles, verifies, and recovers;
- instruments that can now see keep-alive, quest progress, dialog outcomes, group
  scans, identity binding, and command shadow;
- a live quest catalogue captured from the client itself.

It has not yet solved the hard *decision* problem:

- no quest finished end-to-end unattended, and the claim is now provable rather than
  inferred: zero `QuestCompletedMessage` rows in any capture;
- six won `fight_id`s, but all in one session, no boss, and no sustained fight sample;
- six executed `inv.equip` calls and still no live purchase (`hdv.buy`);
- a learned parser that wins offline but has never acted;
- a correct out-of-time veto that only shadows: the loop still re-issues refused calls,
  and nothing prices the action it would take instead (G39/G46/G52/G55);
- 2,186 of 4,003 steps spent on recover/replan, and the three-return park (G63) that is
  meant to cut that has never run live;
- progress is a **volume** problem, measured: 100 quest advances ≈ 9,692 actions ≈ four
  days of sessions (G53).

The next correct move is not a bigger model. It is: **run one clean live session on
`2026-10-30a`, carry one quest to its claim, win a few fights with the battle log
captured, take the first equip/purchase proof and the first command-shadow row, and let
the outcome labels accumulate.** The measured reason no learned head is installed is not
architecture or capacity — four routes were killed by their own bars in two days — it is
that the corpus, though now honest, is still too small to beat a correct rule. That is
the sentence to carry into the next session.
