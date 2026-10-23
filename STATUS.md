# STATUS.md — where the project stands

Written 2026-10-23, late session. Companion to `PLAN.md` (what to do next).
`now.md` is the long historical record; this file is the current truth.

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
| exit IP | relay `lastGood` | three guards, see below |
| device serial + Android ID | `asset_cache/identity/<instance>.txt` | `LoadInstanceIdentity` |
| browser fingerprint | derived from the same seed (`mobile-disguise.js`) | same file, because the seed is `serial:uuid:model` |

## What works

- **The page loads and the game runs.** Fresh profile + fresh account: playable.
- **The AI reaches the game.** It walked to an NPC and talked to it — first confirmed
  objective interaction. The planner is not the blocker.
- **Asset cache is doing its job:** `[CACHE] 268 hits, 4 misses (99% served from disk)`.
- **Chain failover works:** a 4-deep chain (`http://…, socks5://…, socks5://…, socks5://…`)
  survived a dead first proxy in testing.
- **134 automated checks, 0 failing.**

## The kick, and why it happened

Three separate causes were found and fixed, in this order. Each was measured, not guessed.

1. **The relay failed over mid-session.** A dead upstream silently moved the exit IP.
   Fixed: `connsActive > 0` refuses to move the exit while connections are live.
2. **Background traffic moved the exit.** The relay is the WebView2's proxy for *everything*,
   so Edge/Skype telemetry (`config.edge.skype.com`) won a race and re-pinned the egress.
   The game's next connection inherited that address. Fixed: `isGameDestination()` — only
   `dofus.local` and `*.ankama.com` / `*.ankama-games.com` / `*.dofus-touch.com` may move the pin.
3. **Concurrent first dials picked different proxies.** Several game sockets open at once; with
   `lastGood` nil each raced independently, so one account could arrive from two addresses in
   the same second. Fixed: `pinMu` — the first game dial decides, the rest wait.

Verified: five simultaneous CONNECTs → `c1..c5: 200`, `pinned: 1`, `exit changed: 1`.

## Measured limits — the numbers that shape every decision

| fact | value |
|---|---|
| public proxy lifetime | **11 alive at t=0, 0 alive at t=60 s** |
| cache hit vs proxy round trip | ~1 ms vs ~600 ms (**~600×**) |
| CONNECT cost through a public proxy | 3–4 RTT (~1.6 s at 400 ms) |
| pool size / healthy at any moment | 25 entries, 1–5 healthy |

Consequence: **a session cannot outlive its exit.** The guards make the address constant *for a
session*; they cannot make it persist *across* sessions.

## Known gaps

- **Identity is keyed by instance slot, not account.** Swapping accounts inside `dofus-05`
  gives both the same device — the multi-account pattern. Not yet fixed.
- **No persistent exit.** Public proxies die in under a minute. WARP lasts hours but is a
  local exit, so it cannot be chained through remote hops (proven: a hop asked to reach
  `127.0.0.1:40000` dials *its own* localhost).
- **No proxy-lifetime model.** `ai/rank_proxies.py` scores stability from a 6–8 sample series
  and writes `proxies_ranked.txt`; it is a transparent heuristic, not a trained model. `models/`
  contains decision-value and encoder checkpoints, nothing trained on network health.
- **The trained planner is not in the selection path.** The log says
  `policy: no model (deterministic planner)`.

## Things that look like bugs and are not

- **CloudFront 403 on `/maps/*.json`** — S3 answers `AccessDenied` for a missing key instead of
  404. Measured: 16 map JSONs return 200 and 234 return 403 on identical paths, and a **direct**
  connection gets the same 403. Not the proxy, not the relay.
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
