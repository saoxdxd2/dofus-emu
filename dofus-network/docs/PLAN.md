# PLAN — the living plan

This supersedes the status tables scattered across `ARCH_REASSESSMENT.md`,
`PRETRAIN_GAPS.md` and `PIPELINE.md`. Those remain as the reasoning; this file is
what is true **now** and what happens **next**.

Last rewritten: 2026-10-22 (evening), after the client-update incident, the
network rework and the second-agent link.

---

## 1. Goal

A Dofus Touch agent that takes a goal in natural language ("I want kamas", "do the
quests", "beat this monster") and carries it out unattended: plan, move, fight,
farm, trade, learn from outcomes. Two agents inside ~200 MB per instance, no cloud
at runtime.

---

## 2. Verified state

### Client (the part that was broken today)

| item | value |
|---|---|
| game bundle | **1.74.9**, downloaded from the server (was a cached 1.74.8) |
| login | **works** — guest and Google both complete |
| window resize | **fixed 2026-10-24** — `updateScreen` re-reads the live viewport every call (patch rule 12); the old build froze the boot-time size |
| patches | 11 active, patch 4 skipped (markers moved in 1.74.9) |
| updater | **transactional**: staging tree → re-apply patches → validate → commit with `.prev`; refuses a bundle that fails validation |
| patch rules | one source (`tools/patch-rules.txt`, generated from `patch-engine.js`); `DofusFarm.exe --bundle-selftest` proves the runtime rules equal the build |
| self-heal | a missing/truncated `script.js` is restored from `script.js.bak` before the page loads |

### Model

| checkpoint | val tool | val call | gold tool | gold call |
|---|---|---|---|---|
| v11 | 92.9% | 68.5% | 87.3% | 71.8% |
| **v15** (resume) | **93.9%** | **69.8%** | **94.4%** | **77.5%** |

INT8: 25.4 MB, 62 ms/call, **zero** accuracy delta vs fp32 on the serving path.

### Harness

Observation piggyback wired and unit-tested. Farm counters derive
fights/wins/losses/kills/xp/kamas/drops from state diffs.

**`agent/autopilot.py` now closes the loop**: observe -> choose -> guard ->
execute -> label -> decide, for minutes or hours, with no human in it. Every step
is written to `trajectories/autopilot.jsonl` with a verdict (`advanced` / `noop` /
`failed` / `blocked`), the latency and the observation cell/map. Those verdicts are
the second agent's training labels, so the loop is also its data generator.

Verified end to end in `--dry-run` (choose + guard, never send). **Not yet run
against a live instance** — that is the next step, and the first thing it will
prove is whether the observation piggyback works under a real directive.

### Network (rewritten 2026-10-22 evening)

| item | value |
|---|---|
| egress owner | **one** `ProxySupervisor`; `auto` is the default mode |
| candidates | userspace tunnel, configured upstream, every `proxies.txt` entry |
| probe | real request + exit IP; a candidate whose exit IP equals ours is rejected |
| failover | re-probe every 45 s; on a switch the running relays are rebuilt on the same ports |
| fail-closed | covers **launching**, not just dialling: no healthy egress = the client does not start |
| measured here | Maroc Telecom drops UDP outright (WireGuard, MASQUE/QUIC and UDP DNS all fail); only a TCP proxy carries traffic |
| pool health | 1 of 12 known entries alive at the time of measurement |

`warp` mode (the system client) rewrites routing/DNS for the whole machine and is
the reason a dropped tunnel took the WiFi with it. **Repair my network** exists
for exactly that: disconnect, stop the service, flush DNS, probe.

### Second agent (linked 2026-10-22 evening)

| item | value |
|---|---|
| module | `agent/verifier.py` — verdict + residual + drift, one call |
| sources | human (`verdicts_human.jsonl`) > model (ONNX hook, inert) > rule (always) |
| loop | `autopilot._verdict` returns the full `Verdict`; `Step` carries `session` + `source` |
| drift | 3 explainable contradictions; two in a row prints `REPLAN` |
| test | `python -m agent.smoke_verifier` → 25 checks, offline |

Deliberately **rules first**: waiting for a dataset inverts the dependency. The
loop has to run and fail, and the labels fall out of it.

### Window layout: pages, not one long column

The window used to be a single column -- header, toolbar, grid, a 184 px AI strip,
status bar -- so the instance list, which is the thing you actually watch, kept
losing rows to panels touched once a session. It is a page shell now:

| page | contents |
|---|---|
| **Instances** | the toolbar and the grid, nothing else |
| **AI** | mode, target, policy, dry run, goals, budgets, quick actions, live output |
| **Settings** | login mode, client-update opt-out, WARP status + reconnect, folder shortcuts, reset AI settings |
| **Shortcuts** | rebind the five actions by pressing a key |
| **Logs** | live tails of `DofusFarm.log` and `autopilot.jsonl` |

The status bar stays global: it reports the active instance and the quick-focus
buttons regardless of which page is open.

### AI page

The old "AI Commander" strip could not work: it POSTed text to
`/sentinel/parse_command` (a route the sentinel does not serve), swallowed the
error, then matched keywords with `AUTO_FARM` as the default branch -- so **any**
sentence started farming -- and handed the result to a DOM-click hack that guesses
CSS selectors. Removed.

What the AI page does instead:

| control | behaviour |
|---|---|
| Mode `Off / Order / Auto` | Off does nothing; Order runs one goal for 4 steps; Auto runs the goal list for the full budget |
| Target | all instances, or slot #1-4 (mapped through `GetInstanceIdFromSlot`) |
| Policy | `rule` (deterministic) or `encoder v15` (the trained model) |
| Dry run | chooses and guards, sends nothing |
| Start / Stop / Send order | spawns `run_autopilot.py`, writes the stop file, waits 4 s then kills |
| Join / Pass / Halt | single tool calls posted straight to the bridge on 8893 |
| Shortcuts... | jumps to the Shortcuts page |

Settings persist to `ai_settings.ini` next to the exe. Shortcuts are stored as
virtual-key ints, not strings: `Keys.ToString()` ordering for modifier
combinations is not stable enough to compare. Defaults: **F5** start, **F6** stop,
**F7** send order, **F8** dry run, **F12** emergency stop, handled in
`ProcessCmdKey` so they work whatever has focus.

The page decides nothing: every guard (no buying, no invented prices, no-progress
detection) stays in `agent/autopilot.py`, where the tests can see it.

### Protocol

Captured from three real sessions (1.16 MB, 9.2 MB, 10.2 MB): character creation,
map entry, movement, fights, NPC dialog, quest validation, market buy/list, zaap,
**harvest** (`InteractiveUseRequestMessage`), **teleport potions**
(`TeleportRequestMessage`), **item use** (`ObjectUseMessage`).

Prices are a separate channel and are now cached in the page: `hdv.scan` sends the
category, and `ObjectAveragePricesMessage` / `ExchangeBidHouseInListAddedMessage`
arrive later and are absorbed by `__PriceCache`. A listing resolves its rule from
that cache and reports `price_source`, so a price can always be traced to the
server message it came from.

---

## 3. What the incident changed about the plan

The client update (1.74.8 → 1.74.9) took the whole stack down for a night. The
lesson is structural, not incidental:

> **The emulator is coupled to a minified bundle that changes without notice.**

Consequences now baked into the plan:

1. **Auto-update is mandatory**, not a nice-to-have (`EnsureClientUpToDate`).
2. **Patches must fail soft** — a stale boundary is a `[SKIP]`, never an abort, and
   a patch that matches an absurd span (>60 KB) is refused outright.
3. **Every native dependency needs a Proxy stub**, because the client adds calls we
   do not ship (Adjust was the first; there will be others).
4. **Capture before coding.** Every client verb is now written from recorded
   traffic, never from reading the bundle.

---

## 4. Ordered work list

Status of the original list (`ARCH_REASSESSMENT.md` §I.2), plus what the incident
added.

| # | work | gate | status |
|---|---|---|---|
| 1 | Corpus regeneration | unsolvable 18.9% → 0% | **done** |
| 2 | Post-processor scope (fill-only, refuse-not-guess) | Δ ≥ 0 per tool | **done** |
| 3 | `farm.start` key canonicalisation | call 14.6% → >70% | **open** — needs label regeneration |
| 4 | `quest_id` as entity reference | call → >85% | **done** (94.5%) |
| 5 | Gazetteer boundary snapping | item errors −36 | **done** |
| 6 | Class imbalance for never-selected tools | per-tool floor ≥80% | **open** |
| 7 | Observation in the harness | ≥1,000 non-null records | **implemented, not exercised** — unit-tested only |
| 8 | Click accuracy | measurable | **unblocked by 7**, not started |
| 9 | **Client auto-update** | a build change needs no human | **done** (2026-10-22) |
| 10 | **Patch-engine hardening** | one stale patch cannot discard the rest | **done** (2026-10-22) |
| 11 | **Market verbs** (`hdv.scan` / `hdv.list` / `inv.sell`) | buy + list round-trip on a live instance | **built** — needs a live test |
| 12 | **Quest verb** (`quest.advance`) | talk → dialog → objective validated | **built** — needs a live test |
| 13 | **Harvest** (`InteractiveUseRequestMessage`) | collect a resource node | **built** — needs a live test |
| 14 | **Price resolution** | `price_rule` becomes a concrete number | **open — the linking gap** |
| 15 | **Account strategy** | instances survive a client update | **decision needed** |
| 16 | **One owner for egress** (`ProxySupervisor`, `auto` default) | a dead candidate is replaced without a relaunch | **done** (2026-10-22) |
| 17 | **Update is a transaction** (staging → patch → validate → commit) | a server update cannot leave an unpatched bundle | **done** (2026-10-22) |
| 18 | **Second agent linked** (rules + human, model optional) | labels on every step, offline smoke test green | **done** (2026-10-22) |
| 19 | **Drift → real replan** | `REPLAN` calls `ScheduleManager.replan` | **open** — one wire, needs a live plan |
| 20 | **Verifier model** (`build_verifier_sft` → `train_verifier` → INT8) | beats the rule baseline on the same session | **open** — needs one live session first |

---

## 4b. What "built" means here

The three verbs now exist in `scripts/mobile-disguise.js` and send exactly the
messages captured on 2026-10-22:

| verb | message | precondition |
|---|---|---|
| `quest.advance` | `QuestObjectiveValidationMessage {questId, objectiveId}` or `NpcGenericActionRequestMessage {npcId, npcActionId:3, npcMapId}` | an NPC on the current map, or an explicit objective id |
| `hdv.scan` | `ExchangeBidHouseTypeMessage {type}` + `ExchangeBidHouseListMessage {id}` | an **open exchange** — the server ignores these otherwise |
| `hdv.list` / `inv.sell` | `ExchangeObjectMovePricedMessage {objectUID, quantity, price}` | open exchange, item in inventory, **explicit price** |
| `farm.start` + resource | `InteractiveUseRequestMessage {elemId, skillInstanceUid}` | an interactive element on the current map |

Each one reports its missing precondition by name instead of silently doing
nothing, and each refuses rather than inventing a value — `hdv.list` will not list
at a guessed price.

### The market model, corrected

The first version of this plan assumed "sell an item" was one action that either
worked or did not. It is not. **Completing a sale is not controllable** — you can
put an item on the market at a price, and then a buyer either appears or does not.
What *is* controllable:

| controllable | not controllable |
|---|---|
| read the market price (`hdv.scan`) | whether anyone buys |
| list an item you own at a chosen price (`hdv.list`) | when it sells, or if it ever does |
| manage what you have listed | the price someone else lists at tomorrow |

So the loop is **supply**, not revenue: check the price, list, manage, wait. Any
planner that treats a listing as income will over-report kamas and learn a drop
rate that never happened. The autopilot therefore records a successful listing as
`listing_open` with `state: "open"`, and never as kamas earned — there is a test
for exactly this (`test_autopilot_listing_is_not_revenue`).

**Buying is not automated at all.** `ExchangeBidHouseBuyMessage` spends money
irreversibly at whatever price is on the listing, with no undo and no negotiation.
`hdv.buy` is in `FORBIDDEN_TOOLS` and the guard refuses it before it can reach the
client.

### How the price is resolved now

`hdv.list` accepts `price_rule: median_minus_1 | undercut | fixed`:

* `fixed` needs an explicit integer — if the caller did not supply one, the call is
  refused rather than guessed;
* `median_minus_1` / `undercut` are resolved **in the page** from `__PriceCache`,
  which only ever holds values the server actually sent
  (`ObjectAveragePricesMessage`, `ExchangeBidHouseInListAddedMessage`);
* the result reports `price_source` (e.g. `median_minus_1:listing`), and the
  autopilot refuses to count a listing whose price it cannot trace.

That is the compromise the earlier draft argued against — but with a twist that
keeps it honest: the *arithmetic* (one kama under the measured price, floor of 1)
lives in the page, while the *decision* (may this listing happen at all) lives in
`agent/autopilot.py` where the tests can see it. The guard is the thing that would
lose money, not the subtraction.

## 5. Next three things, in order

1. **One live session.** Launch an instance, let the loop run for a few hundred
   steps, and keep `autopilot.jsonl`. This is not a feature — it is the only thing
   that turns three "built, needs a live test" rows into evidence, and it is the
   input for the verifier dataset. Everything below is blocked on it.
2. **Verify the verbs on that session**: `hdv.scan` → `hdv.list` round-trip,
   `quest.advance` (NPC → dialog → objective), one harvest. Each already reports
   its missing precondition by name, so a failure tells you which one.
3. **Drift → replan.** The loop prints `REPLAN`; wire it to
   `ScheduleManager.replan("drop_rate_drift")` and measure whether replanning
   helps. Small, and it closes the only loop the second agent can influence.

Then the model work resumes: item 3 (`farm.start` labels) and item 6 (class
imbalance), then the verifier head (item 20).

---

## 6. Decisions needed

1. **Accounts.** Guests are wiped by updates and disposable-mail signup is blocked
   by CloudFront. The durable answer is real accounts in `accounts.txt`, one per
   slot. Automated signup is rejected: CAPTCHA + CloudFront, and it is the one
   thing that plausibly gets accounts banned.
2. **Patch 4.** `launchMapTransition` no longer matches in 1.74.9. Either port it
   against the new code or accept the unpatched transition (map changes work, the
   watchdog is missing).
3. **Harvest capture.** Who does the 60-second recording.

---

## 7. Invariants

1. **Drive the client, do not reimplement it.** Pathfinding, line of sight, map
   edges and the fight state machine are the game's.
2. **Never connect direct.** The proxy fails closed; WARP exists to keep the IP
   away from Ankama. No upstream, no traffic.
3. **Capture before coding.** A verb without recorded traffic is a guess.
4. **Fail soft on the client, hard on privacy.** A stale patch, a missing SDK or a
   failed download must degrade; an IP leak must stop the instance.
5. **Numbers in docs are measured.** Anything unmeasured is labelled as such.
6. **A listing is supply, not revenue.** Only a completed sale is income, and only
   the server can say one happened. `listing_open` != kamas earned.
7. **Nothing irreversible runs unattended.** Buying spends money with no undo, so
   it stays a human action; the autonomous loop may only do what can be undone or
   waited out.
8. **A bound port is not a working tunnel.** `wireproxy` binds before the
   handshake completes, so an ISP that drops WARP's UDP looks healthy to a port
   check. Probe with a real CONNECT, or do not claim it works.
9. **An update is a transaction.** Download to staging, re-apply the patches,
   validate, commit with a backup. Never delete a live file before its
   replacement exists — that is how a 403 became a black screen.
10. **Fail closed covers launching.** If egress cannot be wired and the mode is
    not `direct`, the client does not open. A leak is worse than a no-show.
11. **An outage must not require a relaunch.** WARP drops on a network change
   (wifi <-> ethernet) and returns by itself; the proxy keeps listening and keeps
   failing closed, and the host reconnects WARP before starting an instance. A
   forty-second tunnel gap must cost forty seconds, not a restart.
