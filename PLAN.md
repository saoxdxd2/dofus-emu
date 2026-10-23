# PLAN.md — what to do next

Written 2026-10-23, late session. Companion to `STATUS.md` (where things stand).

Ordered by **what unblocks the most**, not by effort. Each item has an acceptance test, because
this session produced three wrong theories that a single cheap test would have killed.

---

## 1. Play one full session without a kick

**Why first:** every fix from tonight is unproven in a real session. Three guards close the exit
hole, but only play can confirm it.

**Do:** launch, log in, play 10+ minutes, then read `asset_cache\proxy.log`.

**Acceptance:**
```
[UPSTREAM] session egress pinned to <proxy>     ← exactly once
```
and **no second** `exit changed` afterwards. A second one means the pin moved and the kick
follows.

**If it still kicks:** the log between `session egress pinned` and the disconnect is the whole
diagnosis. There would be a fourth hole I haven't found — that log narrows it to one line.

---

## 2. Key the device identity by account, not by slot

**Why:** `STATUS.md` gap #1. Two accounts in `dofus-05` share one device — the exact
multi-account signal the identity pin was built to avoid.

**Do:** the character/account is known after login (`ACCOUNT_IDENTITY` web message already
carries `login`). Store the identity as `identity/<login>.txt`, falling back to the instance
slot only before the first login.

**Acceptance:** log in as account A, close, log in as B in the same slot → two distinct
`identity/*.txt` files, and the relay's `-serial`/`-uuid` differ between the two runs.

---

## 3. Give the session an exit that persists

**Why:** the only structural fix for the 60-second proxy lifetime. Guards make the address
constant within a session; nothing makes it persist across sessions.

**Options, cheapest first:**

| option | cost | notes |
|---|---|---|
| Oracle Cloud Always Free VM + SOCKS5 | **free** | permanent address, chainable behind hops |
| paid static proxy | ~€3/mo | same shape, no setup |
| WARP | free | **local exit — cannot be chained**; only usable as the plain upstream |

**Do:** run a SOCKS5 on the VM, set `exit_proxy=socks5://<host>:1080` in `network.ini`.
The relay validates it at startup and drops it if no hop can carry a request — a wrong address
degrades to the plain chain instead of failing the launch.

**Acceptance:** `[UPSTREAM] fixed exit … carries … through <hop>` at startup, then
`carrying <ankama-host> over <exit> (exit changed)` exactly once. Kill a hop mid-session and the
address must **not** change.

---

## 4. Put a model in the hop-selection path

**Why:** `ai/rank_proxies.py` already produces the features and `ai/data/proxy_lifetime.jsonl`
already collects rows. The label is the missing half.

**Do:**
1. Let the ranker run while playing; it appends 25 rows per run.
2. Join `proxy_lifetime.jsonl` (features) with `asset_cache/sessions.jsonl` (outcomes) on the
   egress URL — that is the supervised set.
3. Train on Kaggle (the repo already has `kaggle/runs/`); target: probability the proxy survives
   the next 5 minutes.
4. Replace `score()` in `rank_proxies.py` with the checkpoint. It is **one function**.

**Acceptance:** ranked order beats the heuristic on held-out sessions — i.e. sessions on
model-preferred egresses last longer, measured from `sessions.jsonl`.

**Do not** wire a checkpoint before it beats the heuristic. A black box with no measured edge is
worse than the transparent formula.

---

## 5. Close the CDN pool question

**Why:** `-cdn-upstream` spreads asset downloads over up to 3 egresses, round-robin. It is
untested at scale and its failure mode is unknown.

**Do:** play a map with many new tiles and compare `[CACHE]` hit rate and load time with 1 vs 3
asset egresses.

**Acceptance:** more egresses → lower time-to-map, and **no increase in 403s** (a rotating pool
looks more like scraping to a CDN, so this is a real risk, not a formality).

---

## Explicitly not doing

- **`fasthttp` / `gnet`** — the bottleneck is the wire, not the CPU. The process idles.
- **A warm tunnel pool** — measured worse than nothing (20 s / 25 s vs 5.5 s). The dead symbols
  are removed; do not revive the idea.
- **Listening before exit validation** — would let the first requests leave on a different
  address, which is the one thing the exit exists to prevent.
- **Rewriting `WebView2ClientForm.cs`** — the proposed replacement was truncated and, feature
  for feature, the same generation as what is in the repo. No upside, real risk.

---

## How to avoid repeating this session's mistakes

Three theories were wrong tonight (the proxy, the 501 block, `farmengine.cs`). Each died to one
cheap test that could have run first:

1. **Direct connection, no proxy** — proves or kills "the proxy did it".
2. **`git show HEAD:file` vs working tree** — proves or kills "my change did it".
3. **`grep` the log for the actual host** — found `config.edge.skype.com` in one line after two
   hours of theory.

Run the discriminating test before forming the theory.
