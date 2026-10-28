# Brain daemon — one Python process for all instances

**Status:** implemented 2026-10-27, offline-verified. The C# host compiles against
it, the control API answers, and the agent gate is green. The shared WebView2
environment is still a separate change. Per-instance **live** RSS still needs an
actual 1–8 client session; the offline control-plane and memory measurements are
below.

**Update 2026-10-28 — dynamic 40 and the real cost.** Each planner used to load
its own 21.8 MB trimmed quest graph; 40 instances would have paid ~872 MB for the
same rows. `shared_quest_table()` now loads it once per process and planners
reference it read-only. Measured: **40 planners = 22.1 MB total (~0.55 MB each)**.
`test_brain_scaling_40` starts 40 fake instances through `POST /brain/start`,
checks one `brain.lock` and 40 statuses, and stops them; there is no fixed instance
cap in the daemon. The daemon lock now derives `active` from state, not from
`Thread.is_alive()` inside the thread's own `finally`, so the last finisher cannot
publish itself as still active. The knowledge store uses a connection per thread
(`_ThreadLocalConnection`) because one shared `check_same_thread=False` handle
raised `IndexError` under four concurrent readers. The C# host adds
`runtime.ini auto_ai=1` (default): every launched client, and every client already
running at startup, is adopted without pressing Start; a manual Stop wins until
the next launch.

## 1. The problem

The host used to spawn one `run_autopilot.py` per selected instance. Each child
loaded its own Python interpreter, numpy, ONNX Runtime, knowledge cache and model
sessions. Four instances paid for four copies of everything that is actually
shared. The duplicated Python side was estimated at 75–80 MB per instance before
any model was loaded.

## 2. The implemented shape

```
DofusFarm.exe
  └─ one run_brain.py  (:8895 control plane)
       ├─ Autopilot thread  dofus-01  ── HTTP ──┐
       ├─ Autopilot thread  dofus-02  ── HTTP ──┤
       ├─ Autopilot thread  dofus-03  ── HTTP ──┼── agent bridge (8893/8894)
       └─ Autopilot thread  dofus-05  ── HTTP ──┘
```

One process. One import of `agent.autopilot`, `agent.planner`, numpy and ONNX
Runtime. The OS shares the interpreter and read-only pages; the daemon shares the
process-wide knowledge cache and model sessions. Each instance still owns its own
`Autopilot`, recorder, controller, planner cursor and live observation, because
those are per-character state.

The loop itself is synchronous: it does blocking HTTP to the bridge and blocking
ONNX inference. Each loop therefore runs on a thread. The control plane is
asynchronous from the host's point of view; the per-instance work is concurrent
threads. Rewriting every bridge call and inference call as a coroutine is a
separate change, not a prerequisite for removing the duplicated processes.

## 3. Files

| file | role |
|---|---|
| `dofus-network/agent/brain_daemon.py` | `BrainDaemon`, `BrainInstance`, `_DaemonAutopilot`, per-instance threads, lock ownership, status |
| `dofus-network/agent/serve.py` | adds `--brain`, `--brain-policy`, `--brain-log-dir`, the `/brain/*` routes, and holds the daemon |
| `dofus-network/run_brain.py` | script entry point for the embedded runtime; appends `--brain` |
| `desktop/aipanel.cs` | starts the daemon once, POSTs `/brain/start` and `/brain/stop`, keeps the child path as a fallback |
| `dofus-network/docs/BRAIN_DAEMON.md` | this document |

## 4. Control API

All routes are JSON on `127.0.0.1:8895`.

### `POST /brain/start`

```json
{
  "instances": ["dofus-01", "dofus-02"],
  "goals": ["do all the quests"],
  "steps": 0,
  "interval": 0,
  "minutes": 0,
  "policy": "planner",
  "ensemble": "act",
  "level_hint": 60,
  "dry_run": false,
  "fight_boards": true,
  "brain": "auto",
  "command_shadow": "off",
  "verifier_model": null
}
```

An instance already alive is returned, not started twice. A construction failure
is recorded on that instance and does not take the daemon down.

### `POST /brain/stop`

```json
{"instance": "dofus-01", "why": "shortcut"}
{"all": true, "why": "window closed"}
```

Stop writes that instance's `AUTOPILOT_STOP.<instance>` file and joins its thread
for up to two seconds. The thread cannot be killed safely, so a loop blocked in a
bridge call finishes that call and exits at the next stop check.

### `GET /brain/status`

Returns `ok`, `pid`, `active` (the names currently alive) and one status row per
instance: state, alive, start/finish time, log path, stop file, goals, step total,
waits, recoveries, dialog stalls, last step line, error and summary.

### `GET /brain/health`

Returns `status: ok`, service name, pid, active names and instance count.

## 5. Lifecycle and locks

- `_DaemonAutopilot` overrides `_write_lock`/`_clear_lock`: one instance finishing
  must not delete the lock while its siblings are still playing.
- The daemon writes `brain.lock` and the legacy `autopilot.lock` with its pid and
  active instances. The idle trainer keeps reading the legacy path.
- Stale per-instance stop files are deleted when an instance starts.
- `SystemExit` from a failed preflight is caught like any other exit; the instance
  is marked `exited`, not left `running`.
- The daemon process outlives individual instances so the next start does not pay
  the import/model cost again. `Dispose` on the AI panel kills it when the window
  closes.

## 6. Host integration

`desktop/aipanel.cs`:

- `EnsureBrain()` starts `run_brain.py --port 8895 --brain --brain-log-dir …`
  once and waits for `/brain/health`.
- `BrainStart(...)` POSTs the selected instance ids, goals, budget, policy, dry-run,
  verifier and fight-board settings. If the POST refuses, the old per-instance
  child loop runs instead.
- `StopInstance` and `StopAuto` POST `/brain/stop`; `_brainRunning` is refreshed
  from `/brain/status` with a 1.5 s cache so the UI badges and per-instance stop
  still work.
- The existing `autopilot.<instance>.jsonl` files are still the per-instance logs,
  so `session_report.py`, `recovery_report.py` and the live status pill are
  unchanged.
- One brain exit path calls the same idle-training hook the child path used.

## 7. Verified

- `python -m agent.serve --brain --port 8895` starts and answers
  `/brain/health` and `/brain/status`.
- `POST /brain/start` with a dry-run one-step instance returned a running row;
  the instance exited on its own and `/brain/status` reported it.
- `POST /brain/stop` accepted and wrote the stop file.
- The C# host compiles with Roslyn against the new daemon calls.
- `python -m agent.regression` → **922 checks, 0 failed**.

## 8. Shared caches (implemented 2026-10-27)

`dofus-network/agent/model_cache.py` is the process-wide model cache:

- one `onnxruntime.InferenceSession` per `(path, providers, options)`;
- `NNPlanner`, the verifier head, the command ONNX wrapper and the encoder policy
  all use it;
- `agent.regression::test_shared_resources` loads `tool_policy.onnx` twice and
  asserts the object identity.

`agent.knowledge_context.load_default_store` now caches one read-only
`CanonicalStore` per path. The SQLite handle is opened with
`check_same_thread=False` because the daemon builds a planner on the control
thread and runs its loop on a worker thread; four concurrent `meta()` reads pass
in the gate. The mutable `KnowledgeStore` (discoveries) remains per instance.

This removes the duplicate ONNX weights and the duplicate canonical store opens.
It does not yet prove the 2 MB-per-instance target; that is item 1 below.

**Live collection runner.** `dofus-network/tools/collect_live_data.py` uses this
daemon: it preflights `live_ready`, refuses on any FAIL, starts/reuses the daemon,
runs the selected instances with the command shadow, stops them, rebuilds
`data/entity_pointer`, and prints `combat.grid_probe`. `--dry-run` only prints the
request. It never logs in or writes credentials.

## 9. Not done yet

1. **Measure RSS per process with 1 / 2 / 4 / 8 instances.** The one-process claim
   is structural, but the 2 MB-per-instance target is not yet measured. `psutil` or
   `GetProcessMemoryInfo` should record working set after imports, after each
   instance starts, and after the first step.
2. **Shared `CoreWebView2Environment` — decided against (2026-10-27).** WebView2's
   proxy configuration is environment-wide. Every instance uses its own relay port
   and egress, so sharing one environment would force all accounts onto one exit
   IP and break the one-address-per-session model. Keep per-instance environments;
   the Python daemon is the implemented memory win. Revisit only if the relay is
   consolidated to one shared egress.
3. **Per-instance stop latency.** A thread blocked in a 6 s bridge call cannot be
   interrupted. Either lower the bridge timeout or make the bridge client
   cancelable.
4. **Crash isolation.** One process means a hard segfault takes every instance
   down. The fallback child path is the recovery. A watchdog can restart the
   daemon and re-issue the active starts.
5. **Remove the fallback** only after a live four-instance run proves start, stop,
   per-instance logs, idle training and memory.

## 10. Failure modes to watch

| failure | effect | mitigation |
|---|---|---|
| GIL / one CPU-heavy step | other instances step slower | steps are I/O-bound; measure p95 with 4 and 8 |
| SQLite connection shared across threads | corruption or `check_same_thread` error | keep one connection per thread or open read-only |
| ONNX Runtime thread pool | N sessions oversubscribe the CPU | one shared session; cap intra-op threads |
| one instance hangs in HTTP | its thread only; stop waits | cancelable bridge timeout |
| daemon dies | all loops stop | fallback child path + watchdog restart |
| bridge target resolution | a directive reaches the wrong window | one bridge listener with `target_instance`; the daemon passes the instance per call |
| log interleaving | unreadable step logs | one `autopilot.<instance>.jsonl` per instance |
