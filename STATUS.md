# STATUS.md — where the project stands

Written 2026-10-23, late session. Companion to `PLAN.md` (what to do next).
`now.md` is the long historical record.

> **Superseded in part — 2026-10-26.** The network egress/kick analysis (§"The
> kick") is still the best record of why the pin works and is kept as-is. Three
> things below are now out of date: (1) the `smoke_verifier` gate and its
> "244 checks" were **removed 2026-10-25** — the only surviving Python gate is
> `advisor selftest` (122 checks), see `dofus-network/docs/KNOWN_GAPS.md` G8;
> (2) the action space is **21 tools**, not 16; (3) `models/tool_policy.onnx` was
> **retrained 2026-10-26** — the current artifact is 42 features → 21 logits,
> 49,365 params, val **0.8599** (the 48,786 / 0.8632 numbers below are the prior
> run). For the live running topology and which model is loaded by default, read
> `dofus-network/docs/CURRENT.md`. This file is the current *reasoning*, not the
> current *status*.

Everything here is **measured** unless marked unknown.

---

## What the system is

```
WebView2 client (dofus.local, virtual host)
   │  HTTP + CONNECT to 127.0.0.1:<relay port>
   ▼
Go relay (scripts/dofus-net-proxy.go)
   │  asset cache (disk) + CDN pool + one pinned egress
   ▼
public proxy / WARP tunnel
   ▼
Ankama (login host, CDN)
```

Three identities matter to the server, and all three are now pinned:

| identity | where it lives | pinned by |
|---|---|---|
| exit IP | relay `lastGood` | chosen once per live session; replaced only after that session is gone |
| device serial + Android ID | `asset_cache/identity/<instance>.txt` | `LoadInstanceIdentity` |
| browser fingerprint | derived from the same seed (`mobile-disguise.js`) | same file, because the seed is `serial:uuid:model` |

## What works

- **The page loads and the game runs.** Fresh profile + fresh account: playable.
- **The AI reaches the game.** It walked to an NPC and talked to it — first confirmed
  objective interaction. The planner is not the blocker.
- **The AI can plan quests the knowledge base has never seen.** The client snapshot now carries
  each objective's `type_id`, parameters and `map_id`, so tutorial quests absent from
  `quests_full.json` are still plannable. A ready objective is validated, then acted on (talk /
  fight / travel), then retired — it is no longer re-validated forever.
- **The read-only advisor exists.** `dofus-network/advisor/` joins the live character report, the
  official class-spell DB, the Touch item/set dump and the client's price cache. It picks an
  element from the spells the character can actually cast, allocates grades under the
  client-derived point cap, scores gear and set acquisition (buy / craft / farm), and ranks
  market buy/sell signals. Entry points: `python -m advisor capture`, `python -m advisor report`,
  `python -m advisor selftest` (122 checks). It never spends points, equips, buys or sells; live
  validation on a logged-in character is still pending.
- **Asset cache is doing its job:** `[CACHE] 268 hits, 4 misses (99% served from disk)`.
- **Chain failover works:** a 4-deep chain (`http://…, socks5://…, socks5://…, socks5://…`)
  survived a dead first proxy in testing.
- **One client script.** `scripts/mobile-disguise.js` is the only copy; the two unreferenced
  duplicates were deleted, and a check now fails if a second copy appears.
- **The tutorial is the authority.** The scenario manager, not the quest graph, decides what
  can happen next while a scripted step runs. The snapshot now carries the step's own gate
  (`active`, `quest_id`, `step_id`, `can_change_map`, `unblocked_cells`, `unblocked_npc_id`)
  and every exit says whether it is `blocked`. `nav.goto` refuses a crossing the step disabled,
  an NPC other than the one it allows, and a same-map cell outside its allowed set (stage
  `tutorial_blocked`). The planner obeys the gate before anything else — answer an open dialog,
  talk to the allowed NPC, walk to an allowed cell — then waits (`state.snapshot`) instead of
  starting unrelated quests. Exploration and travel ignore blocked exits and prefer the
  objective's direction hint, so a tutorial that only allows `right` no longer produces a walk
  to `left`.
- **Giving up on a quest is reversible.** A stall retires that quest *in that situation*
  (`step`, tracker objective, map, tutorial gate); a changed step, a map change, or the
  tutorial ending reopens it. Permanent retirements (no tool can express the objective) are
  kept separate. This is what lets the AI resume after the player advances the quest manually.
- **~~244 automated checks, 0 failing.~~** That was the `smoke_verifier` gate,
  removed 2026-10-25. The surviving gate is `advisor selftest` (122 checks,
  knowledge repository only) — `KNOWN_GAPS.md` G8.

## The kick, and why it happened

Four separate holes were found and fixed, in this order. Each was measured, not guessed.
1. **The relay failed over mid-session.** A dead upstream silently moved the exit IP.
   The first guard refused to move only while `connsActive > 0` — but that count includes
   background sockets and drops to zero during a reconnect. The rule is now about the session,
   not the count: while a game tunnel is moving bytes, the pin never moves; when none has moved
   a byte for 60 s, the session it protected is gone and a new one may use a new address.
   That distinction matters because a dead pin used to strand the client forever on
   "server busy" — the relay refused to move and no login could ever complete.
2. **Background traffic moved the exit.** The relay is the WebView2's proxy for *everything*,
   so Edge/Skype telemetry (`config.edge.skype.com`) won a race and re-pinned the egress.
   The game's next connection inherited that address. Fixed: `isGameDestination()` — only
   `dofus.local` and Ankama hosts can *establish* the pin; everything else races around it and
   can never move it. Ankama hosts can no longer move it either.
3. **Concurrent first dials picked different proxies.** Several game sockets open at once; with
   `lastGood` nil each raced independently, so one account could arrive from two addresses in
   the same second. Fixed: `pinMu` — the first game dial decides, the rest wait. The first dial
   also uses the supervisor's pick (`p.upstreams[0]`) instead of racing the pool, which could
   previously pin an address the supervisor had never recorded.
4. **A hop that accepts CONNECT and then carries nothing trapped the session on it.** Public
   proxies sometimes finish the TCP/SOCKS/CONNECT handshake and then swallow the stream. The
   freshly registered game tunnel counted as "live" (registration called `touch()`), so rule 1
   protected that dead address for a full 60 s while the login page's `config.json` timed out.
   That is the "dofus.local says server busy" alert, followed by a reload into a login screen
   that never completes. Registration is no longer activity, and a game tunnel is now proven by
   the first byte from the far side: the client's ClientHello is held, sent to the pinned hop,
   and a hop that answers nothing is replaced before the tunnel carries the session. Until that
   byte nothing has been delivered, so the swap cannot split a session; after it the pin is
   never touched. The injected script also retries the login `config.json` GET twice (network
   errors and 5xx only), so a request already in flight during the swap survives it.

The result: `setPinnedUpstream` writes only when the pin is nil, so one session has exactly one
address. The only writer that can replace a pin is `recoverPin`, and it runs only when no game
tunnel has moved bytes for 60 s — either because the pinned egress failed, or because it has not
carried the first byte yet. A re-pin before that first byte is not a move: the server has not seen
the session. After it, the address is fixed for the session. The log says
`session egress re-pinned` so it is never mistaken for the old one moving. The `exit changed` log
line no longer exists — the code path that produced it was removed.

A second hole explained the 23:23 incident specifically: a public SOCKS5 proxy was ranked
because its port accepted TCP, then closed the game's first CONNECT. `ProbeSocksEgress` now
performs the SOCKS handshake *and* a CONNECT for remote candidates too, not just a port check.

Verified in this pass: the relay builds and its unit tests pass — destination classification,
the live-traffic hold, the stale-session replacement, the first-byte proof and silent-socket
replacement, and skipping the dead address on re-pin. The rebuilt binaries contain both new
paths, and the 244-check smoke verifier passed *(that gate was since removed —
KNOWN_GAPS.md G8)*. A live session is still the only proof; see
`PLAN.md` #1.

## Measured limits — the numbers that shape every decision

| fact | value |
|---|---|
| public proxy lifetime | **11 alive at t=0, 0 alive at t=60 s** |
| cache hit vs proxy round trip | ~1 ms vs ~600 ms (**~600×**) |
| CONNECT cost through a public proxy | 3–4 RTT (~1.6 s at 400 ms) |
| pool size / healthy at any moment | 25 entries, 1–5 healthy |

Consequence: **a session cannot outlive its exit.** The pin makes the address constant *for a
session*; it cannot make it persist *across* sessions.

## Known gaps

- **Unproven live.** The one-address pin and the observed-quest planner are covered by unit and
  smoke checks only; no live client was run in this pass. `PLAN.md` #1 is the test.
- **Identity is keyed by instance slot, not account.** Swapping accounts inside `dofus-05`
  gives both the same device — the multi-account pattern. Not yet fixed.
- **No persistent exit.** Public proxies die in under a minute. WARP lasts hours but is a
  local exit, so it cannot be chained through remote hops (proven: a hop asked to reach
  `127.0.0.1:40000` dials *its own* localhost).
- **CloudFront 403 on the OAuth login page.** Observed 2026-10-24 on `auth.ankama.com`:
  CloudFront's WAF answered the login popup with `403 Request blocked` /
  `Generated by cloudfront` and a Request ID. The popup deliberately shares the game's pinned
  exit, so a datacenter/proxy exit is refused before Ankama ever sees the credentials — the
  account and the client are not at fault. **Not** the `/maps/*.json` S3 403 below: that one is
  identical on a direct connection, this one follows the exit IP. Fix: a residential or mobile
  TCP egress for the whole session (a phone hotspot on mobile data works). A static datacenter
  VM can still be refused on this host, and WARP cannot run on this ISP because its tunnel needs
  UDP, which is dropped. The popup now replaces the raw page with the named cause and the
  Request ID, and the log emits `[OAUTH-BLOCKED]` for both the page and any 403 subresource.
- **No proxy-lifetime model.** `ai/rank_proxies.py` scores stability from a 6–8 sample series
  and writes `proxies_ranked.txt`; it is a transparent heuristic, not a trained model. `models/`
  contains decision-value and encoder checkpoints, nothing trained on network health.
- **~~The trained planner is not in the selection path.~~ Closed 2026-10-24.** The
  installed 42-feature h192 head (48,786 params, 155,199 travel-cost rows,
  validation 0.8632 — since retrained 2026-10-26 to 49,365 params, val 0.8599)
  is `models/tool_policy.onnx`, and the desktop launches with
  `--policy planner`. On the corrected simulator it scores 83.6 quests vs the
  teacher's 81.7 (12 paired seeds, level 60, 400 steps, noise + travel cost), and
  140.0 vs 137.9 at 800 steps (8 seeds); at level 20 it scores 72.8 vs 72.1. It was
  promoted after beating the previous 16,261-row model on 11/12 seeds at level 60
  and 8/12 at level 20; the earlier 145,732-row candidate was rejected because it
  lost every seed at level 20. The old gap was a missing model file plus a 41-vs-42
  feature skew, not the planner itself. The corpus is still DofusDB-structural, so
  Touch fidelity remains open. See
  `dofus-network/docs/FAILURE_TO_SUPERVISION.md` for the safety-gate side of the
  same loop.

## Things that look like bugs and are not

- **CloudFront 403 on `/maps/*.json`** — S3 answers `AccessDenied` for a missing key instead of
  404. Measured: 16 map JSONs return 200 and 234 return 403 on identical paths, and a **direct**
  connection gets the same 403. Not the proxy, not the relay, and **not** the same failure as the
  OAuth WAF block in Known gaps (that one changes with the exit IP; this one does not).
- **`watchUpstream` flapping every 10 s** — the flag is only ever written, never read to gate a
  dial. Cosmetic.
- **WARP showing `000` in tests** — it goes down on network changes and returns; it is not a
  stable exit.

## Build

```
compile.bat
```

`[1/3]` builds the Go relay, `[2/3]` Build.exe, `[3/3]` packages. The relay step exists because
this script used to package whatever `.exe` it found — a Go change could ship silently stale.
`go.work` at the repo root means `go build` works from the root or from `scripts/`.
