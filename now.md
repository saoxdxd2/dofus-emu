# now.md — where this project stands, and what happens next

Written 2026-10-23, fourth session (server maintenance, no live play). **Supersedes the
version from the night of 22–23 October.** That document's negative results all stand; what
changed is that the agent finally reached the game and produced its first *verified*
progress, and that three separate "the AI does nothing" failures turned out to be plumbing
rather than intelligence.

Everything below is either measured (a number from a run) or marked unknown. Where a claim
was wrong, the correction is kept next to it, because the corrections are the useful part.

---

## What to read for "how do I run it"

`MORNING.md` (the 2026-10-23 night checklist) has been removed as obsolete. For the
order of operations and every running service/port, read
**`dofus-network/docs/CURRENT.md`**; for what is *not* working yet, read
**`dofus-network/docs/KNOWN_GAPS.md`**. This document remains the long-form
record of that night's plumbing fixes.

### The night of 2026-10-23 (00:00–06:00), in one paragraph

The simulator was exhausted as a learning source, so the work moved to the live client, and
there the pattern that finally worked was **read the client's own code instead of
assuming**. Seven interfaces were being used wrongly, and every one of them failed
silently: the snapshot read `playerData.character` (does not exist) instead of
`characterBaseInformations` and `position.mapId`; the verifier compared flat keys against a
nested snapshot, so **every step scored `noop` including real map changes**; the
observation carried no quest state at all, so the planner could not tell which objective was
current; `nav.goto` sent a blind `ChangeMapMessage` instead of driving the client's own
pathfinder; `quest.advance` ignored `npc_id`, scanned `actor.staticInfos` instead of
`actor.data.staticInfos`, sent the template id where the wire wants `contextualId`, and had
no way to answer a dialog; `fight.join` clicked a DOM selector that does not exist instead of
sending `GameRolePlayAttackMonsterRequestMessage`; and `--directive-url` defaulted to a
literal 8893, so the bridge's published port was ignored. On top of that the loop ran eight
steps per goal and exited 0 no matter what "unlimited" said, and a successful read ended the
session. None of this was a modelling problem. The agent was never unable to plan; it was
unable to *see*, and then unable to *act*.

### The day of 2026-10-23 (06:00–08:30), in one paragraph

With the servers down, this was a repair-and-cleanup session, and the repair found a
**fourth layer of the same disease**: the contract between the planner and the client
disagreed with both. `data/capabilities.json` — which `validate_call` enforces *before* a
directive leaves Python — declared `quest.advance {quest_id, step_id}` while the planner
sent `{quest_id, npc_id, objective_id}` and the client read all three plus `reply`. It
declared `farm.start.monster` as a **string** while the planner derived an **int** and the
client did `parseInt` on it. It declared `fight.join {target}` while the planner sent
`monster` and the client read `group_id` — three names with no overlap. The result was
`refused: schema rejected the call (unknown argument(s) for quest.advance: objective_id)`:
**46% of objectives (talk/deliver) and 36.5% (kill) were rejected at the validator**, so
nothing reached the game and the agent looked incapable while it was doing the right thing.
Then, once calls got through, two more silent failures appeared: every walk was judged in
the instant it was issued and therefore scored `noop`, and — the one that mattered most —
**claiming an objective changed no cell, map or HP, so the verifier could not see the
agent's single most valuable action and threw away its progress.** Also found: every
loopback call the harness made was being routed through the user's VPN proxy
(`HTTP_PROXY=127.0.0.1:20056`), and the planner could not see which map it was on. By the
end the log reads `quest.advance ok advanced` — the first verified progress in this
project's live history — and the loop stopped honestly when its window closed instead of
retiring a quest that had done nothing wrong.

## 0. What the previous versions got wrong

| it said | what was actually happening |
|---|---|
| "the AI is not smart enough — train it more" (the user's instinct, and mine) | The contract rejected its plans before they left Python. `refused: schema rejected the call (unknown argument(s) for quest.advance: objective_id)`. Four tools under-declared their own arguments. **Training more would have taught the model to avoid the correct actions.** |
| "claiming a ready objective is progress" (I wrote this, then it still did nothing) | True, and unverifiable: `ready_objectives` was not in `STATE_KEYS`, so the claim scored `noop`, the controller replanned, retired the quest it had just advanced, and reported "goals exhausted". The most valuable action the agent has was invisible to the only component that judges actions. |
| "the AI does nothing when I start it" | Two separate causes. The client script was **executing twice per page load** (`[x2] [FarmBot] Bound to connection manager`), and every loopback HTTP call the harness made was going **through the user's VPN proxy**, returning 502s from its own machine. |
| "planner completes **21 quests / 21 objectives** in 500 steps" | It completed 21, then spent 429 steps re-accepting quest 458, which it had already finished. `sim/world.py` truncated `quests_done` to the last 20 entries and the planner treats that list as the set of finished quests, so the 21st became invisible. After the fix: **119 quests / 148 objectives in 400 steps**. |
| "the corpus covers **2 of 18 tools** — a generator bug" | The generator could only emit what the teacher emitted, and the teacher emitted two tools because **every tool could complete every objective**: `quest.advance` completed a kill, a delivery, anything. The tool choice was not a decision, so there was nothing to learn. |
| "**0.8830** accuracy, real learning over a 62% majority" | Measured on a corpus where objective type 0 — 5,669 open-world kills, the most common objective in the KB — was encoded as *unknown*, because `int(obj.get("type_id") or -1)` turns 0 into −1. That line existed in eight files. |
| "the simulator is deterministic given the features" | True, and it also silently accepted impossible calls and had no way to notice a loop. `score()` now reports `max_repeat` and `stalled`. |

None of that was a modelling problem. It was the world disagreeing with itself, and the
model faithfully learning the disagreement.

---

## 1. In one paragraph

The client works, the agent loop runs, and as of today it reaches the game: `quest.advance
ok advanced` against a live client, with a claim the verifier can actually see. That is a
much smaller result than it sounds and a much larger one than the last three sessions
produced — for the first time the loop is not lying to itself about what happened. The
simulator remains exhausted as a learning source (four negative results, unchanged), and
nothing learned is wired in. The live blocker is no longer "the AI is blind" or "the
contract rejects it"; it is that **no single quest objective has been completed
end-to-end**, which is why the agent has issued zero fight calls in 697 recorded steps:
fights sit behind the first talk objective and that talk has never closed. So the plan is
not "train more" — it is "finish one objective, watch it close, then the fight path becomes
reachable for the first time."

---

## 2. What works, verified

| thing | evidence |
|---|---|
| Client launches, no black screen | STA fix + bundle patches; `START: UI thread apartment: STA` in the log |
| Bundle patcher reproducible | `--bundle-selftest` → IDENTICAL, 13 rules |
| Agent loop end-to-end | `inv.list ok advanced 141ms -> 23 items` against the live bridge |
| **Directives reach the game** | `[AGENT] quest.advance -> dofus-05 ok` ×40 in one session, **zero** `schema rejected` |
| **Claiming an objective is verified** | `2 quest.advance ok advanced -> {'quest_id': 4278, 'objective_id': 13986}` |
| **The loop reports its own end honestly** | `[autopilot] the client is gone: … stopping` / `done: the client window closed` — instead of retiring a quest and saying "goals exhausted" |
| **The planner can see the player** | `flatten()` supplies `map_id`, `level`, `cell`, inventory-by-id; the plan it produces uses a real map (`nav.goto {'map_id': 54165306}`) |
| **Loopback calls bypass the proxy** | `opener_for('http://127.0.0.1:…')` returns a proxy-free opener; two checks pin it |
| Planner completes quests (sim) | Re-measured 2026-10-28, planner L60, 400 steps, seed 7: **119 quests / 148 objectives**, max_repeat 1, stalled false; the intervening 82-quest regression was a fabricated open-dialog observation, now fixed |
| Planner under noise / poor start (sim) | Same command with `--noise`: **94 quests / 99 objectives**, max_repeat 5, no stall; `--kamas 0 --random-start-map`: **119 / 148**, max_repeat 1, no stall. Broad harness quest knowledge means this is not a realistic poor live-start proof |
| Tool policy beats the teacher (free-travel sim) | 7 seeds, `--noise`, 400 steps: 99.6 vs 96.9 quests — **but see §3, it loses 19% once travel is priced** |
| Tool policy beats copying the teacher | val acc **0.8986** vs teacher-agreement **0.8235** (+7.5 pts), 48,594 params |
| Where the label is meaningful, it is near-perfect | margin > 0.1 (60.6% of val): accuracy **0.9691** |
| Verifier is calibrated | val acc 1.0000, ECE **0.0075** |
| Test suite | **127 checks, 0 failed** (`python agent/smoke_verifier.py`; was 79 before today) |

**18 tools** in `data/capabilities.json`; `agent/features.py` reads that file, so the action
head is 18-wide by construction.

### What today's checks pin, and why each one exists

Every fix below is the kind that silently comes back, so each has a guard:

| check | the failure it prevents |
|---|---|
| the client script guards against a second execution | two `FarmBot.init()`, two bindings of `dofus.connectionManager`, two bots on one HUD |
| exactly one copy of the client script exists | three copies drifting, one surviving the build |
| no page loads the client script with a `<script>` tag | a transport re-added by accident |
| the builder does not copy the script into www/js | `Build.exe` recreating the copies |
| every planned call passes the contract | `schema rejected` — the whole of §0's first row |
| the contract declares every argument the client reads | an argument nobody can send (`npc_id`, `objective_id`) |
| the contract declares every key the planner reads | `map_id: None` — the planner going blind |
| claiming an objective counts as progress | the verifier throwing away the agent's best action |
| a move that lands late is seen, not scored noop | judging a walk before it moves |
| leaving the world stops the loop | retrying into a closed client |
| a loopback call bypasses HTTP_PROXY | 502s from your own machine |
| no harness module imports the simulator | inverted dependency |

---

## 3. What is proven dead (do not rebuild these)

**Recursion (TRM, K=12, deep supervision).** Two runs, two worlds:

| world | data | result |
|---|---|---|
| deterministic | 89,152 rows | 1.0000 at **k=1**, ECE 0.000 |
| noisy (15% refusal, stale/partial obs) | 105,522 rows | 0.8830 at **k=1**, flat to k=12, **+0.0000 gain** |

Twelve passes of a GRU cell produced the same answer as one. Tool selection is a lookup;
the paper's claim needs a task where one pass is insufficient.

**The repair decision (second attempt, in the priced world, with better features).** The one
place the teacher has no rule, so the one place a model could still earn its place. Rebuilt
the corpus properly — the old `retry` arm replayed the tool with *empty arguments*, which is
not a retry, and it made retry look far worse than it is — and took the features from 11 to
24 (error class, which tool failed, hops to the objective, bag full, observation freshness).
2,500 post-failure states.

| policy | regret (progress left on the table, lower is better) |
|---|---|
| always continue | **0.2480** |
| always retry | 0.3120 |
| always recover | 0.6667 |
| always replan | 1.0480 |
| learned | 0.2560 — *loses* |

The reason is structural, not statistical: **every failure the simulator can produce is
transient.** A resisted action succeeds on the next attempt 85% of the time, so "continue"
is the correct response to all of them, and it is a constant. `models/decision.onnx` is not
wired in.

**The "first failure → retry" rule.** 2.3× worse than continuing in the simulator; in the
real client 0 of 9 retries worked, because they were retrying an absent client.

**Soft targets from the fork's gain distribution.** Measured: decision gain 0.7031 vs 0.7028
for hard targets (**+0.0003**), and ECE 0.1762 vs 0.0122. Rejected by its own result.

**A smoother fork score (`--score cummean`).** Ambiguity 34.5% vs 27.7%, simulator 98.6 vs
99.6 quests. It made both worse. Default stays `rate`.

**The learned tool policy as a replacement for the teacher.** This one reverses an earlier
claim, and it is the most important entry here:

| policy | quests / 400 steps, 7 seeds, `--noise --travel-cost` |
|---|---|
| teacher (nearest ordering) | **82.6** |
| learned (nearest ordering) | 66.7 (−19%) |
| hybrid: teacher travels, model acts | 82.9 (+0.4%, i.e. nothing) |

The mechanism is exact. 357 of its errors are spurious `nav.goto` (recall 0.95, precision
0.73). When travel is free, over-travelling is cheap insurance. When travel costs up to three
steps, that same habit is the most expensive mistake available. **It won only because its
mistakes were cheap.** `models/tool_policy.onnx` is not wired in.

**More data.** 32k → 90k rows (2.8×): validation accuracy +0.002, simulator score **−0.9
quests**. Data volume is not the lever.

**Capacity.** Kaggle T4 sweep, 189k rows, five architectures, same corpus and seed:

| params | shape | val acc | macro-F1 | ECE |
|---|---|---|---|---|
| 8,018 | h64 d2 | 0.8961 | 0.2859 | 0.0076 |
| 48,594 | h192 d2 | 0.9005 | 0.2867 | 0.0074 |
| 293,394 | h512 d2 | 0.9007 | 0.2867 | 0.0066 |
| 1,111,058 | h1024 d2 | 0.9008 | 0.2871 | 0.0080 |
| 818,706 | h512 d4 | 0.9006 | 0.2864 | 0.0113 |

**138× the parameters buys +0.0045 accuracy.** Third independent measurement that the task
is short of labels rather than of capacity.

---

## 4. What is blocked, and by what

0. **The egress, and it is now measured rather than blamed.** A client cannot launch
   without one healthy exit, because `fail_closed=1` refuses to fall back to a direct
   connection. On 2026-10-23, after a WiFi change, every candidate was dead and the app
   correctly refused. What the network actually does:

   | probe | result | meaning |
   |---|---|---|
   | direct HTTPS | 200 in 0.31 s | the internet is fine |
   | `8.8.8.8:53` UDP | reply, 19 ms | **UDP works** |
   | `1.1.1.1:53` UDP | no reply | **Cloudflare blocked** |
   | `162.159.192.1:2408` (WARP) | no reply | **WARP blocked** |
   | pool proxy `:443` | TCP open, then 403 on every CONNECT | reachable, rejects — an IP allow-list |

   So **WARP is a dead end on this line, and no configuration fixes it.** `bin\warp\` ships
   a complete local stack (wireproxy, warp-plus, wgcf with a real profile) and it starts
   cleanly — `Interface state was Down, requested Up, now Up` — but the WireGuard handshake
   to Cloudflare never answers, so the SOCKS5 port accepts and then times out. Keeping WARP
   in the project was never the problem; the network blocks its endpoint.

   The pool is the viable path, and the pool automation was **present but orphaned**: 
   `scripts/proxy_pool.js` does exactly what it should — re-verify approved, quarantine,
   scrape when stale, deep-check candidates (CONNECT → TLS → `200` on `/build/primus.js`
   and `/config.json`), then rewrite `proxies.txt` atomically — but nothing invoked it and it
   was not in the installer payload. Run once by hand it found `live=0/10` and emptied
   `proxies.txt`: the public pool on this line is exhausted, not misconfigured.

   Fixed: it now ships (`builder/build.cs`), `start-network.bat` drives tunnel + pool +
   report in one command, and `SetupNetworkEgress` in the installer provisions `network.ini`
   inside the install and reports what is missing — without ever overwriting a user's
   settings.

   **The harvester is separate and now real-time.** The pool only scrapes when its own queue
   is low, and with 75,000 candidates waiting it never does — so it kept testing a frozen
   snapshot of a list that rots by the hour. `scripts/harvest_proxies.js` fetches from 17
   sources on its own schedule and only ever appends: one pass took the queue from 75,148 to
   85,901 (+10,753) in 0.8 s. It tests nothing and never touches `proxies.txt`; the pool stays
   the only thing that decides what "working" means.

0. **WARP cannot work on this line, and that is now measured rather than assumed.**
   `ai/test_warp.py` runs a real wireproxy handshake against every published endpoint and
   port. Twenty combinations, every one times out. Three probes that look like evidence are
   not, and were used wrongly earlier in the day: a TCP connect to `:2408` always fails
   (UDP-only port), a bare UDP packet to `:2408` always fails (WireGuard drops invalid
   packets), and `ping 1.1.1.1` says nothing at all (Cloudflare deprioritises ICMP and many
   ISPs drop it). What is solid:

   | probe | result | what it means |
   |---|---|---|
   | `1.1.1.1:443` | timeout | that one address is filtered |
   | `1.0.0.1:443` | **open, 34 ms** | the *same service* on its sibling IP is fine |
   | `104.16.0.1:443` | open, 46 ms | Cloudflare's CDN is fine |
   | `8.8.8.8:443` / `:53` | open / replies | general internet is fine |
   | `dofus-touch.com:443` | **open, 35 ms** | **the game itself is reachable** |
   | `wgcf register --accept-tos` | succeeded | the account API is genuine and reachable |
   | 20 WARP endpoints, real handshake | 0 pass | the WARP ranges are what is filtered |

   So the block is narrow and specific — `1.1.1.1` plus the WARP endpoint ranges — not
   "Cloudflare". `warp-plus --scan`, `--cfon` (Psiphon) and `--gool` all fail at the same
   place. Keeping WARP inside the project was never the issue and is already done.

   **The consequence matters more than the finding:** the game is reachable directly, so the
   only thing between this machine and a running client is `fail_closed=1` insisting on an
   exit. That is a deliberate safety setting, not a bug, and the three ways forward are a
   paid proxy, a long pool hunt, or turning it off and accepting a direct connection.

0. **No objective has been completed end-to-end.** This is the live blocker and it is new.
   The agent claims objectives that the game already accepts (`objectiveStatus 2`) — real
   progress, now verified — but a *talk* objective that needs a walk, an NPC, a dialog and a
   reply has never closed. Measured consequence: **0 `fight.join`, 0 `farm.start`, 0
   `fight.pass` in 697 recorded steps** (histogram: `quest.advance` 404, `state.snapshot`
   264, `quest.list` 25, `knowledge.search` 3). Fights are not failing; they are
   unreachable, because they sit behind the first talk. Which also means the combat handler
   written on the night of the 23rd has **never run against the game** and must not be
   described as working.

0. **The client window closes mid-session and we still do not know why.** Six sessions ended
   this way. It is not a kick: `closing: reason=UserClosing` rules out Windows shutdown, the
   task manager and app exit, and there is no `window.close()` in the game bundle, no
   renderer crash (0 `Process Failed`), and no hotkey that closes an instance. But WinForms
   reports `UserClosing` for a programmatic `Form.Close()` too, so "the player clicked X" and
   "`WebView2Backend.Stop()`" are still indistinguishable — both now record themselves
   (`requested_by=… @ file:line`), and the next run settles it.

0. **The simulator is exhausted.** Not "hard", not "needs more data" — exhausted. Every
   learned policy it can justify has now been tried and lost, and the two failures have the
   same cause: the world's only non-teacher signal is noise, and noise is best answered by a
   constant. The world is built from the teacher's own rules, so it can only teach noise.
   What it cannot produce is a *persistent* failure, and a persistent failure is the only
   thing a learned repair decision could fix.

0. **The label is ambiguous, and it is measured rather than suspected.** 40.1% of validation
   states whose exact 41-feature vector also appears in training carry more than one label.
   **23.8% of the corpus is an exact tie.** Where the fork has an opinion the model is at
   96.9%; the ten-point gap to the headline is entirely rows where the world had no
   preference.

0. **The world only needs four verbs.** Label mix: `farm.start` 36%, `quest.advance` 32%,
   `nav.goto` 24%, `fight.join` 4.6% — 96.9% of all decisions. The old P0 criterion ("≥5
   tools at ≥5% of rows") was a guess; the measured answer is four.

0. **Six of the eighteen tools are placeholders in the world.** `hdv.scan`, `hdv.list`,
   `chat.say`, `knowledge.search`, `knowledge.remember`, `knowledge.forget` have no state
   effect, so no counterfactual can ever prefer them.

0. **No live session *of the kind P1 needs*.** 41 real steps, 15 failures, 14 of 15
   structural. Today added ~40 directives that reached the client — but still zero genuine
   (non-structural) failures, so they cannot rank recovery strategies.
   `ai/reality_check.py` correctly refuses to conclude below 30.

0. **The network-health model is retrained and loads — but it is only advisory on fast pings.**
   The checkpoint had been dead weight since the architecture changed (BatchNorm → GroupNorm,
   `SEQ_LEN` 128 → 32), so it failed `load_state_dict` and `ai/diagnose_network.py` could only
   report telemetry. Retrained on a Kaggle T4 (`kaggle/networkhealth`, ~4 min) after a local
   CPU run proved the point by burning 42 minutes of CPU for 11 of wall time without finishing:

   | | before | after |
   |---|---|---|
   | loads against the code | no | **yes** |
   | RTT log-MAE, horizon 1 | 0.4654 | **0.3597** |
   | train / valid examples | 1,585 / 146 | 96,550 / 6,358 |
   | size | 2.0 MB | 223 KB |

   Verified on real feature rows: predicted 24.8 vs actual 22.3 ms, 373.9 vs 397.2, 165.6 vs
   236.8 — it works on its own distribution.

   **Where it does not work, and why it matters.** `log_gap_seconds` is one of its ten features
   and its training cadence is RIPE Atlas at roughly a minute. Feeding it pings 0.3 s apart is
   out of distribution and the forecast is decoration — measured, not guessed: a 120 ms series
   pinged twice a second came back as "0.8 ms, confidence high". So `ai/score_proxies.py` ranks
   by **measured** loss, jitter and p95, and prints the model's degradation beside them as
   advisory.

   **Phase 5 is now done, and it works.** `ai/collect_local.py` samples this machine at the
   cadence the tools actually use, and `prepare --out` / `train --out-dir` keep the result
   separate from the public-probe artifacts. Corpus: **11,066 observations over 12 series** in
   ~12 minutes (8 reachable hosts, 4 deliberately unreachable so the loss head sees a positive
   at all). Split by **series**, so validation is on hosts the model never saw:

   | checkpoint | cadence | RTT log-MAE h1 | live 0.5 s series: forecast vs actual |
   |---|---|---|---|
   | `local_model/model.pt` | 0.5 s, this machine | **0.1464** | **56.3 ms vs 63.3 ms** |
   | `model.pt` (RIPE) | ~60 s, global probes | 0.3597 | 1.2 ms vs 65.9 ms |

   `score_proxies.py` now prefers the local checkpoint and says when it is falling back. Two
   honest limits remain: the corpus is ~12 minutes from one network, so it describes *this*
   line and not networks in general; and its losses are bimodal (four series at 100%, the rest
   at ~0%), so the loss head learned "some hosts never answer" rather than intermittent loss.
   A corpus that teaches intermittency needs a flaky path to observe, which is exactly what a
   working proxy would provide.



---

### What stays inside the box

The emulator may use the network. It may not change it. Concretely, every mode except one
binds a port on `127.0.0.1` and leaves Windows routing, DNS and proxy settings exactly as
they were:

| mode | what it touches |
|---|---|
| `auto` | `127.0.0.1:40000` (wireproxy) + the relay ports |
| `wireproxy` | the same, local only |
| `custom` | the same, one upstream |
| `direct` | nothing — the relay connects out itself |
| `warp` | **`warp-cli mode proxy`: routing and DNS for the whole PC** |

That last one is now gated behind `allow_system_warp=1`, and if it is not set the engine logs
the refusal and **falls back to the local tunnel** rather than leaving the user with no
egress. A mode that takes over a machine's entire internet and drops it when the tunnel dies
is not something an emulator should be able to do by accident, or by a config file someone
copied.

`agent/smoke_verifier.py` now enforces the rule at source level: no `netsh`, no `route add`,
no `"ProxyEnable"`/`"ProxyServer"` registry writes, no hosts edits, no
`Set-DnsClientServerAddress` — across 55 files — and the WARP guard must appear before the
`warp-cli` call. Three checks, so the box cannot be widened without someone noticing.

## 4b. Audit of the connection path — "cannot connect to the server"

Symptom: login completes, then the client reports the server is unavailable. It happens with the
AI running and not without. Traced from the client to the wire, in this order:

| link | what it does | verdict |
|---|---|---|
| WebView2 client | dials `dofus.local:443` (page origin) and `haapi.ankama.com:443` | fine |
| relay `dofus-net-proxy` | maps `dofus.local` → `dt-proxy-production-login.ankama-games.com`, dials the upstream | two bugs |
| upstream | wireproxy on `127.0.0.1:40000`, or a `proxies.txt` entry | the unstable one was always chosen |
| supervisor | re-probes every 45 s and may rebuild the relays | rebuilt constantly |

**Bug 1 — the relay was rebuilt on every missed probe.** `changed` was computed against
`_active`, and `_active = best` runs unconditionally, so one probe pass that missed everything
nulled it. The next pass re-picked the same tunnel, `_active` was null, `changed` fired, and
`RebuildAllRelays` stopped and restarted the relay on the same port — dropping every live socket,
the game connection included. The log shows `switching egress` a dozen times, each one a dropped
session. Fixed: `changed` is keyed on `_lastAppliedUrl`, the upstream the relays were actually
built with.

**Bug 2 — the flapping tunnel outranked stable proxies.** The sort put `wireguard tunnel` first
whenever it was healthy, and "healthy" only means "answered the last probe". On this line WARP's
UDP is blocked, so the tunnel binds `:40000`, carries a little traffic, then exits — measured:
`:40000` was listening and passing traffic to the game host at 17:32, and refusing connections at
17:38. Ranking it first meant the app kept choosing an egress that died seconds later. Fixed:
candidates carry a `HealthyStreak`, and the tunnel must pass **twice** before it outranks a proxy;
an unproven tunnel ranks below an unproven proxy.

**Why the AI made it worse:** the AI starts instances, which triggers refreshes and relay builds.
More refreshes meant more chances for a missed probe to null `_active`, so the rebuild loop ran
constantly instead of occasionally.

### Noted, not fixed

- `virtualHostRewriteOnce` logs the `dofus.local` mapping once per process. Correct, but it makes
  a session's route look like it only happened once — worth logging the first N instead of one.
- The relay rebuild still drops live sockets; the deferral depends on
  `_webViewBackend.GetActiveInstanceIds()` being non-empty. It is, but a client that is
  connecting-but-not-yet-running would still be cut.
- `curl -x` fails against the local wireproxy while the same CONNECT succeeds from Python. Not
  investigated — wireproxy may not implement CONNECT the way curl expects. It does not affect the
  app, which speaks SOCKS5.

## 5. The plan, in order

### P0 — finish one objective end-to-end (the only thing that matters now)
Not "make the AI play". One talk objective, start to finish: walk to NPC 447, open the
dialog, choose a reply, watch the objective leave `step_objectives` and `objectives_completed`
increase. Everything downstream — fights, deliveries, the label corpus, P1 — is gated on
this and nothing else.

**How to watch it:** start the loop and **do not touch the client window**. The evidence is
already in the log per step (`[autopilot] <n> quest.advance ok <verdict> -> <args>`), and the
close line now says who closed it.

**Done when:** one objective goes from open to finished in a single session, and
`objectives_completed` increases.
**Falsifier:** if the walk reaches the NPC and the dialog never opens, the defect is in the
talk path (`NpcGenericActionRequestMessage` / dialog reply), not in the planner — and the
next step is a DOM-level look at the dialog, not more agent work.

### P0.1 — make a fight reachable, then verify it once
`fight.join` has never been executed against the game. Once a kill objective is reached,
send it and record: which group was chosen, whether `GameRolePlayAttackMonsterRequestMessage`
was accepted, and whether `GameFightStartingMessage` arrived. Until then it is untested code
and should be treated as such.

### P0.2 — settle the window-close question
Read `closing: reason=… requested_by=…` on the next run. If it says the player, the fix is
UX (make progress visible so there is no reason to close it). If it names
`WebView2Backend.Stop`, that call site is the bug.

### P0.5 — the label ceiling (no GPU needed, unchanged)
23.8% of rows are exact ties and another 16.7% are within noise. Attack either the tie-break
or the tie rate. **Done when:** exact ties below 10% with the simulator score unchanged or
better.

### P1 — one real session (the only door left)
Unchanged, and now finally approachable: the loop reaches the client and reports honestly.
**The session has to explore, or it produces nothing learnable** — `--explore 0.3` takes a
different repair on 30% of failures (never a structural one).

```bash
python agent/autopilot.py --goal "farm" --steps 30 --explore 0.3
python ai/reality_check.py
python ai/export_decision_log.py --log "%LOCALAPPDATA%\DofusFarm\autopilot.jsonl"
```

**Done when:** ≥30 genuine (non-structural) failures *and* at least two of the four repairs
observed on the same cause. **Falsifier:** if the real failures turn out to be as transient
as the simulated ones, no learned decision is worth building.

### P2 — the decision model, third attempt — ONLY with P1's labels
Same harness, same metric (regret), same rule: ships only if it beats every constant. Two
attempts have failed; a third on simulated data would be the fourth measurement of the same
thing.

### P3 — plan chunks in the runtime
`plan_chunk.onnx` (1.79M, exact 0.9682) predicts the next 3 tools. Wire it as a hint the
controller can veto, and measure replans per quest. **Falsifier:** if replans don't drop, the
chunk is decoration.

### P4 — the ladder (value → search), only if P2 succeeds

### Housekeeping, not on the critical path
- **Rebuild `Build.exe`.** It is still the 2026-10-22 binary and contains the old
  `TrySyncClientShims`, so it recreates the two `mobile-disguise` copies on every build
  (deleted three times today). `builder/build.cs` is fixed; the binary is not. Harmless —
  no page loads them — but it leaves the "exactly one copy" check red after each build.
- **Retrain the network-health model** if its forecast is wanted (see §4).

---

## 6. Code map

```
dofus-network/
  agent/
    features.py          THE featuriser: 41 dims, 18 tools from capabilities.json
    observe.py           NEW: flatten(state.snapshot) → the flat keys the planner reads
    autopilot.py         the loop: plan → execute → settle → verify → decide
    planner.py           KB → plan. Deterministic teacher + fork() + note_result()
    schema.py            the contract; validate_call runs BEFORE a directive leaves
    bridge.py            where the bridge is, and opener_for() — loopback never proxied
    controller.py        continue/retry/recover/replan + error taxonomy
    verifier.py          did the step work? STATE_KEYS is what counts as a change
    knowledge_store.py   discovered facts, not weights
    smoke_verifier.py    127 checks; every fix above has one
  sim/
    world.py             symbolic world; noise=True; map preconditions; priced travel
    tool_choice.py       counterfactual tool corpus (fork per candidate)
    decisions.py         counterfactual repair corpus
    run.py               planner | nn | random | rule → quests per N steps, --noise
  ai/
    diagnose_network.py  NEW: network-health model + live relay telemetry
    probe_client.py      ask the live client what it sees
    reality_check.py     real failures → can they rank recovery strategies?
    export_decision_log.py  session log → dataset + a coverage verdict
    train_tool_policy.py 18-way trainer + capacity sweep
  networkhealth/         RTT forecaster + anomaly head (see §4: checkpoint is stale)
  data/capabilities.json THE contract. The registry wins over schema.py's built-ins.
  scripts/mobile-disguise.js  THE client agent. Single copy; one execution per page.
```

---

## 7. How to run it

```bash
# tests — the old smoke_verifier gate was removed 2026-10-25 (KNOWN_GAPS.md G8);
# the surviving Python gate is the advisor's, which does not cover the loop:
cd dofus-network && python -m advisor selftest   # 122 checks (knowledge repo only)

# simulator scoreboard (always quote --noise)
python sim/run.py --policy planner --level 60 --steps 400

# what does the live client actually see?
python ai/probe_client.py

# is the relay path healthy? (run it WHILE connected — an idle relay reports its last RTT)
python ai/diagnose_network.py --seconds 20

# one real session, then: is it learnable?
python agent/autopilot.py --goal "farm" --steps 30 --explore 0.3
python ai/reality_check.py
python ai/export_decision_log.py --log "%LOCALAPPDATA%\DofusFarm\autopilot.jsonl"
```

**Reading a run.** Every step is one line: `[autopilot] <n> <tool> <ok|FAIL> <verdict> ->
<args>`. The verdict is the thing to read — `advanced` means the world changed,
`noop` means the call succeeded and nothing moved, `failed` means it did not run. A run that
ends with `done: the client window closed` lost its window, not its mind.

Kaggle: account `tajdinenizar`, credential at `~/.kaggle/kaggle.json`. Private datasets:
`thx-02-sim-toolkit` (code + KB), `thx-02-tool-corpus`. **Keep the output small** — a 134 MB
output broke `kaggle kernels output` twice; the artifact is the model.

---

## 8. Open questions nobody has answered yet

1. **Does the talk objective close?** The single question in front of everything else. The
   walk now happens; whether the dialog opens, whether a reply is needed, and whether the
   objective then completes are all unknown.
2. **Who closes the client window?** Instrumented as of today; unanswered until the next run.
3. Does the real game rank recovery decisions the way the simulator does? **Unknown.** Needs P1.
4. Of the 40% ambiguous states, how much is "the plan's next step is not a feature" and how
   much is the noise draw? **Unknown.**
5. Are reads genuinely worth 1.2% of decisions, or does the fork understate them?
6. Six tools have no effect in the world. When should an agent sell, scan the market, or
   record a discovered fact?
