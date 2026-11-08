---

## 2026-11-07 — a test gate, and the toolkit mirror now fails it

### Why

The suites existed and were good — 30 `test_*.py` files plus `agent/regression.py`, which
alone carries **2,157 checks** — but nothing ran them together, so "the tree is green" meant
"my last run looked fine". A suite could rot for weeks and no command would say so. The
`kaggle/toolkit` mirror has had its own `--check` since G10 and it, too, ran nowhere.

### What was added

* `dofus-network/tools/gate.py` — runs every suite in its own process with a timeout, then the
  regression harness; the exit status is 0 only when all of them pass. `--only`, `--list`,
  `--timeout`, `--json`, `--strict`, `--skip-regression`.
* `gate.bat` at the repository root — the same thing on the embedded interpreter, so it runs
  what ships.
* The toolkit mirror check is suite zero, so a drifted copy fails the gate instead of being
  discovered by a Kaggle run that no longer matches this tree.

### Two things the first run taught us

* **`ai/test_egress.py` is not a suite.** The filename pattern matched a proxy-probing CLI that
  dials every proxy in the list; it hung the gate for 120 s. It is now named in `NOT_SUITES`
  with the reason, rather than deleted or renamed — it is a useful tool, it is just not a test.
* **Six suites import `jax`, which the embedded runtime does not ship** (this is the same fact
  that stops the host starting the combat brain). A missing wheel is not a regression, so a
  suite that dies on `No module named 'jax'` is reported as `blocked` and retried under an
  interpreter that has it — on this machine `Python314`, where those six pass. `--strict` turns
  `blocked` back into a failure for a release run, so the distinction cannot hide a real gap.

### Measured

2026-11-07, embedded interpreter:

| | |
|---|---|
| suites | **29 / 30 pass**, 139 s (one needed the fallback interpreter) |
| `agent.regression` | **2,157 checks, 0 failed** |
| toolkit mirror | 236 synced, 8 pinned, 0 drift |

Running `--write` once recorded the new `tools/gate.py` in the mirror and in
`kaggle/toolkit/TOOLKIT_MANIFEST.json`; `--check` is green afterwards.

### Undo

Delete `dofus-network/tools/gate.py` and `gate.bat`. Nothing else in the tree reads them; the
toolkit copy is ordinary mirror content and is removed by the next `--write` after the source
is gone. `NOT_SUITES` disappears with the file, restoring the previous behaviour.
