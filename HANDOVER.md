# HANDOVER — how to pick this project up cold

Written 2026-10-22, at the end of a long session. If you are an AI (or a person)
continuing this work, read this file first, then §1. Everything below is stated
as fact about the current tree, with the evidence that supports it.

---

## 0. Security — do this before anything else

**Real account credentials are in a public GitHub repository.**

* `accounts.txt` contains **14 real `login:password` entries** and is tracked.
* `proxies.txt` is tracked too (proxy credentials, if any were ever used).
* The remote is `https://github.com/saoxdxd2/dofus-emu.git`, `origin/main`
  contains both files (`git ls-tree origin/main accounts.txt`), and
  `https://github.com/saoxdxd2/dofus-emu` returns **HTTP 200** — i.e. public.
* Last commit touching it: `17596c7` (2026-10-22).

Done already, in this session:

```bat
git rm --cached accounts.txt proxies.txt     :: untracked, files kept on disk
:: .gitignore now blocks accounts.txt, proxies.txt, network.ini, ai_settings.ini, *.token, ...
```

**Not done, and only the owner can do it:**

1. **Treat those 14 accounts as compromised and change their passwords.** History
   rewriting does not un-publish anything that has already been fetched or cached.
2. Purge history and force-push, if the repo must stay public:
   `git filter-repo --path accounts.txt --path proxies.txt --invert-paths`
   then `git push --force --all` (destructive; get explicit permission).
   Making the repo private is the cheaper mitigation.
3. Never commit a real account again. `accounts.txt` stays local.

An AI continuing this work must not push, force-push, or rewrite history without
explicit instruction.

---

## 1. Read order (about an hour, in this order)

| # | file | what you get |
|---|---|---|
| 1 | `README.md` | the whole system: processes, ports, bundle, updater, network, AI stack, config, troubleshooting |
| 2 | `dofus-network/docs/PROJECT_LOG.md` | every decision D1–D21 **with the falsifier** — why things are the way they are |
| 3 | `dofus-network/docs/PLAN.md` | the phase list and what "done" means per phase |
| 4 | `dofus-network/docs/SECOND_AGENT.md` | the verifier: what is wired, the pipeline commands, what is left |
| 5 | `.workbuddy-ai/memory/2026-10-22.md` | the session log: what changed today and the measurements behind it |

Optional but useful: `PLANNER_DESIGN.md` (RAM budget, why no token CoT),
`PROTOCOL_NOTES.md` (captured Dofus messages).

---

## 2. Ground truth

* **The working tree is the truth.** The last commit is `17596c7` with the message
  `.` — it tells you nothing. There are ~57 modified/untracked files on top of it.
  Do not assume `git checkout` restores a working state; it does not.
* Build artefacts in the root (`DofusFarm.exe`, `setup.exe`, `dofus-net-proxy.exe`)
  are outputs. Rebuild rather than edit.
* `*.pre-*.bak` files are manual safety copies from earlier sessions; ignore them.

---

## 3. Environment (verified on this machine)

| thing | state | consequence |
|---|---|---|
| Go | 1.26.4 | `compile.bat` rebuilds the relay; `go test` works with explicit file names (§7) |
| .NET SDK | 10.0.103 → Roslyn | C# 6+ syntax allowed in `desktop/*.cs`; the builder falls back to legacy C# 5 if absent, so **avoid string interpolation** to stay compatible |
| Node | not bundled in the payload | the runtime updater cannot run `patch-engine.js`; that is why patches are data (§6) |
| Python (local) | 3.13/3.14, **no numpy, no torch, no onnxruntime** | anything you write must run without them, or degrade cleanly; the trainer is pure Python for this reason |
| Python (payload) | `runtimes/python` (3.11 embedded) | the app spawns this one; keep `agent/` import-light |
| Ports | 8880+slot relay, 8890 sentinel, 8893 directive bridge, 40000 tunnel | a stale listener on one of these looks like "the app is broken" |
| Network | Maroc Telecom; WARP UDP was blocked at 13:35 and working again at 14:41 the same day | never hard-code an egress assumption |

---

## 4. Who owns what

| file | owns |
|---|---|
| `desktop/mainform.cs` | shell, nav, grid, launch paths, the live AI pill |
| `desktop/farmengine.cs` | instances, relays, tunnels, proxy pool, `ResolveUpstream` |
| `desktop/proxysupervisor.cs` | candidate probing, failover, relay rebuild on the same ports |
| `desktop/netsettings.cs` | `network.ini` model (mode/ports/upstream/fail-closed) |
| `desktop/clientbundle.cs` | staged updater + runtime patcher + self-heal |
| `desktop/WebView2ClientForm.cs` | the client window, message bridge, OAuth, play log |
| `desktop/aipanel.cs` | AI page, `AiSettings`, spawning the loop, `FormatStatus` |
| `scripts/dofus-net-proxy.go` | the relay: routing, cache, egress, fail-closed |
| `scripts/mobile-disguise.js` | everything injected into the page (fingerprint, HUD, tools) |
| `tools/patch-engine.js` + `patch-rules.*` | the 11 bundle patches, build-time and runtime |
| `agent/autopilot.py` | the loop: observe → choose → execute → label |
| `agent/verifier.py` | the second agent: rule / model / human labels |
| `ai/*.py` | encoder training, dataset builders, verifier baseline |
| `planning/*.py` | planner, cost model, KB, bandit, schedule, trace |

---

## 5. What is solid, what is stubbed, what is unverified

**Solid (measured, tested):**

* The relay: routing, cache, fail-closed, `-config`. A live 200 through it.
* The proxy supervisor: probes, rejects transparent proxies, fails over, rebuilds
  relays. Observed switching between tunnel and pool on the same day.
* The bundle updater: `--bundle-selftest` reports `IDENTICAL` (13 rules, 9 matched).
* The encoder policy: 24.87 M params, 0.9391 tool / 0.6980 call, INT8 25.4 MB.
* The agent loop + verifier: 30 offline checks, 0 failures.

**Stubbed / inert:**

* The neural verifier head. `Verifier._load_model` accepts `.onnx`; no such model
  exists. The shipped `models/verifier_baseline.json` is a 33-feature logistic
  regression trained on rule labels — it mirrors the rules by construction, and
  its 1.0 validation score measures nothing.
* `hdv.buy` is forbidden and there is no purchase path at all.
* Legacy Android backend: compiled, unreachable from the UI, excluded from the
  payload.
* `networkhealth_artifacts/` is a finished research run, not runtime data, and is
  deliberately excluded from the installer.

**Unverified (claims in docs with no measurement behind them):**

* The 137 MB figure for two models in one process — never measured with both
  loaded.
* The veto threshold: exists as a flag, defaults to 0 (never veto), no ECE run.
* Drift → replan: the loop sets `replan_requested`, nothing consumes it.
* `hdv.scan` / `quest.advance` against a live client: protocol is documented,
  not exercised end to end in this session.

---

## 6. Invariants — break these and the thing stops being trustworthy

1. **Fail closed.** No path may reach the internet on the real IP unless the mode
   is explicitly `direct`. This covers launching too (`WireWebView2LocalProxy`
   returns `false` and the client is not started).
2. **One egress owner.** `ProxySupervisor` decides; everything else asks it. Do
   not add a second place that picks an upstream.
3. **A bound SOCKS port is not a working tunnel.** Probe with a real CONNECT.
4. **One description of a patch.** Rules live in `tools/patch-rules.*`, generated
   by `tools/make-patch-rules.py`. Never edit the C# patcher's copy separately.
5. **Nothing is deleted before its replacement exists** (updater: staging → `.prev`
   → commit; `script.js.bak` always holds the unpatched original).
6. **Sessions, not steps.** Dataset splits are by session; a step-level split
   leaks the answer.
7. **The verifier's input must be recorded.** If you add a feature to
   `agent.verifier.features`, extend `_compact()` in autopilot too, or old rows
   become untrainable.
8. **No new runtime dependency** in `agent/` or `ai/build_verifier_sft.py` unless
   it is optional and degrades to rules.
9. **C# 5 syntax in `desktop/*.cs`** (no string interpolation, no expression-bodied
   members) so the legacy compiler path still works.
10. **No `hdv.buy`, no invented price, no listing counted as revenue.** These are
    money-losing or self-deceiving; the comments say why.

---

## 7. Verify any change (four commands, no game needed)

```bat
DofusFarm.exe --bundle-selftest        :: expect: RESULT: IDENTICAL (exit 0)
DofusFarm.exe --ai-status-selftest     :: expect: four formatted states (exit 0)
```

```bat
cd dofus-network
python -m agent.smoke_verifier         :: expect: 30 checks, 0 failed
python -m agent.selftest               :: expect: 0 FAIL lines
```

```bat
cd scripts
go test dofus-net-proxy.go dofus_net_proxy_bench_test.go dofus_net_proxy_equiv_test.go dofus_net_proxy_relay_test.go
```

Full rebuild: `compile.bat` (≈50 s, produces `DofusFarm.exe`, `uninstall.exe`,
`setup.exe` ≈288 MB). Run it after any C# change; a clean compile is not proof of
behaviour, so also start the app and read `DofusFarm.log` for the
`[NET] egress:` line.

---

## 8. Work queue, in order

| # | task | entry point | done when |
|---|---|---|---|
| 1 | Rotate the leaked accounts, purge history or privatise the repo | §0 | passwords changed; `git ls-tree origin/main accounts.txt` is empty |
| 2 | One live session of the loop | `python run_autopilot.py --goals-file goals.txt --steps 0` | `%LOCALAPPDATA%\DofusFarm\autopilot.jsonl` has >100 rows with verdicts |
| 3 | Build the verifier corpus from it | `python -m ai.build_verifier_sft` | `data/verifier_sft/{train,val}.jsonl` with ≥3 sessions in val |
| 4 | Neural verifier head | `ai/train_verifier.py` (does not exist yet) | `.onnx` loads via `--verifier-model`, beats the baseline on the same val split |
| 5 | Veto threshold from ECE | `Verifier.veto_threshold`, `preflight()` | measured ECE + a threshold that is a number, not a guess |
| 6 | Drift → real replan | `autopilot.replan_requested` → `planning/schedule.py` | a replan actually fires and changes the plan |
| 7 | Measure two-model RSS | new script under `ai/` | a number replaces the 137 MB assumption |
| 8 | Decide the Android path | `EmulatorBackend.cs` | deleted, or documented as maintained |
| 9 | Shrink the repo | `networkhealth_artifacts/` (100 MB) | moved to a release asset or deleted with a note |

Ask the owner for: the game account to test with, permission before any
destructive git operation, and a Kaggle/GPU path if task 4 needs training time.

---

## 9. Traps that already cost time

* **TLS 1.0.** .NET Framework's default breaks every HTTPS probe through a proxy
  while `curl` works. `ServicePointManager.SecurityProtocol |= 3072` is set in
  `TestHttpProxy` — keep it.
* **`String.replace` semantics.** JS replaces the first occurrence; the Python
  reference applier and the C# patcher must use "first only" too.
* **A BOM in an ini file** turns the first line into an unmatched key. Write with
  `new UTF8Encoding(false)`.
* **`File.Copy` into a missing directory throws** — `builder/build.cs` staging
  creates the destination directory first (that is how `tools\patch-rules.txt`
  ships).
* **`go test ./scripts/` fails**: several `main` packages in one folder. Name the
  files.
* **`DofusFarm.exe` is a winexe**: `Console.WriteLine` goes nowhere. Self-tests
  write a `.txt` and use the exit code.
* **Python heredocs eat backslashes** in Windows paths (`\v`, `\m`). Use the Edit
  tool for C# string literals, or `Path.Combine("a", "b")`.
* **The AI pill is fed by the log, not the process.** Do not "simplify" it to
  `IsRunning`; a live process with a stalled loop looks healthy.
* **`wireproxy` binds before the handshake completes.** Port-open is not proof.

---

## 10. What not to do

* Do not rewrite a subsystem that works because it looks old. The updater, the
  supervisor and the patcher each exist because something specific broke; the
  comments say what.
* Do not add a dependency to make a test pass.
* Do not enable `direct` to "make it run" — that is the one outcome the proxy
  exists to prevent.
* Do not commit `accounts.txt`, `proxies.txt`, `network.ini`, logs, models or
  trajectories.
* Do not trust a green compile as evidence of behaviour. Run the four checks in §7
  and read the log.

---

## 11. How to report back

State the change, the evidence (command + output), and what you did **not**
verify. This project has a habit of "looks done" turning out to be "wired but
never exercised" — the docs are full of numbers precisely because that happened.
