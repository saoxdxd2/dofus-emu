# KNOWN GAPS — what is not true yet, with the evidence

An honest list of the things the system **cannot** do or **does not** verify
today. Each entry gives the symptom, the code or log evidence, why it matters, and
what closing it would take. This is the counterpart to `CURRENT.md`: that file
maps what runs; this file maps what is missing or unproven.

The rule that generated this file: a gap you can point at in the code is worth
more than a worry. Everything below is pointed at. Anything I could not verify is
labelled *unverified*.

---

## G1. No server-visible keep-alive → vanilla AFK disconnects

**Symptom.** A run ends with the game's own popup — *"Prolonged inactivity will
lead to automatic disconnection… Show some sign of life (move, send a chat
message…)"* — immediately followed by `AI_DISCONNECT reason=INACTIVITY`. This has
looked like a ban ("got kicked for inactivity") and is not one.

**Cause.** The server's AFK timer counts *wire traffic the server can see*. The
loop's observation step is `state.snapshot`, which is a **pure local read**
(`_agentResult(true, _agentSnapshot())` in `scripts/mobile-disguise.js`) — it sends
no packet. A `noop` or `blocked` action likewise changes nothing on the wire. So a
loop that is *busy reading and deciding* is *silent to the server*, and the timer
fires. The only anti-AFK traffic in the client is `GameFightTurnFinishMessage
{isAfk:false}` in `mobile-disguise.js`, and that is **fight-scoped** — it never
fires in the overworld.

**This is not bot inspection.** There is no detection mechanism being tripped; it
is the ordinary inactivity timeout any idle player hits. When the loop also crashed
(`[AI] loop finished (exit -1)`) just before such a disconnect, the crash left the
client genuinely idle and the AFK timer finished the job.

**Fix shape.** An overworld keep-alive that is legal and human-like: a periodic
`chat.say`, a harmless `state`-polling packet, or a small step — gated so it does
not spam. It must be a *real send path*, not `state.snapshot`. *Unverified:* a
read-only wait loop (the "tutorial is holding this step open; waiting…" branch) can
still starve the server of visible activity even after a keep-alive exists, if the
keep-alive is skipped during the wait.

---

## G2. There is no equip tool

**Symptom.** When an NPC instructs the player to *equip* something, the agent has
no verb for it. The 21-tool vocabulary (`data/capabilities.json`, `CURRENT.md` §7)
has no `equip`/`wear`/`slot` action, so the instruction cannot be satisfied.

**Evidence.** No `equip` handler in `agent/schema.py` `_BUILTIN_TOOLS`; no equip
verb in the JS adapter. `state.snapshot` can *read* gear; nothing writes it.

**Fix shape.** A new tool + a JS handler that issues the equip packet, with the
same schema-validation gate as every other tool. Until then, any quest step gated
on "equipped X" is a hard stop, not a delay.

---

## G3. Dialog *question text* is captured but never reasoned over

**Symptom.** The client exposes the open dialog including the NPC's message and its
reply options — `_agentDialogState()` in `scripts/mobile-disguise.js` returns
`{open, npc_id, message, replies:[{index, text, reply_id}]}`. The agent picks a
reply, but not by *understanding the question*.

**Evidence.** `agent/dialog_policy.py` `rank_dialog()` scores each reply by token
overlap with the *quest context* (`_quest_context`), i.e.
`score = len(tokens & context) / len(tokens)`. It **never reads `dialog["message"]`**
— the actual prompt the NPC asked. Ties abstain.

**Consequence.** An NPC that asks a yes/no or a choose-based question is answered by
"which reply shares words with my quest notes", not by reading the question. This is
why dialog choices look arbitrary to a human watching.

**Fix shape.** Feed `message` into the scorer (a small classifier or an encoder
conditioning pass) instead of only quest-context overlap. *Unverified:* no measured
accuracy on reply selection exists, so "dialog works" cannot be claimed.

---

## G4. There is no vision / image encoder

**Fact.** `encoder30_v15` is a **text** encoder (24.9M params, `CURRENT.md` §5).
There is no image model in the repo. The Sentinel's "vision inspector" is regex over
text, not pixel input.

**Consequence.** The image-heavy extraction (the Papycha quest material) was **never
trained into any model**, and the AI does not see the screen. It reads structured
`state.snapshot` JSON. That is fine for most quests; it cannot answer anything that
only exists as a picture. *Unverified:* whether any current quest actually requires
image understanding to finish — none reported so far, because the blocker on
dialog/keep-alive comes first.

---

## G5. Network-health forecaster is dark (W4, carried from `PRETRAIN_GAPS.md`)

**Symptom.** `ai/diagnose_network.py` prints a verdict, but it is the relay's own
telemetry, not a forecast.

**Evidence, and this is the one still-live item from the deleted
`PRETRAIN_GAPS.md` (its W4 row):**
- `networkhealth/sentinel.py:25-27` still does `import torch`, `import torch.nn as
  nn`, `import torch.nn.functional as F` — the Sentinel is a torch service, so it
  cannot run in the no-torch shipped runtime.
- `sentinel.py:52` `CHECKPOINT_PATH = .../networkhealth_artifacts/model.pt`, and
  `sentinel.py:325` `network_engine = MultiInstanceNetworkHealth(CHECKPOINT_PATH)
  if CHECKPOINT_PATH.exists() else None`.
- **`model.pt` is absent on this machine** (verified), so `network_engine` is
  silently `None` and `trm_moe` reports false (`sentinel.py:355`). The old
  checkpoint will not load against the current code anyway: the architecture moved
  from `BatchNorm` (running stats in the checkpoint) to `GroupNorm(1, C)` (no
  running stats), and `SEQ_LEN` changed 128 → 32. Loading with `strict=False` would
  print a confident number from weights that no longer match the graph — worse than
  refusing, so it refuses.

**Fix shape.** Two separate jobs: (a) retrain the forecaster on the existing
artifacts (`python -m networkhealth train`) and re-export; (b) port the Sentinel to
the ONNX path (the original W4) so a torch-free runtime can serve it — that was the
unlock for a smaller installer. Until both are done, treat the network verdict as
telemetry, not prediction. *Caveat:* an idle relay reports its **last** RTT, not a
live one — run it while a client is connected or the samples are flat by
construction.

---

## G6. `verifier_head.onnx` is orphaned

`models/verifier_head.onnx` (33-feature / 4-class) exists but is **never loaded**:
`--verifier-model` defaults to `None` (`agent/autopilot.py:1940`, plumbed at `:189`
and `:212`). The loop's `verify` stage runs on deterministic before/after
comparison, not on this head. Either wire it (`--verifier-model models/
verifier_head.onnx`) and measure it, or treat the artifact as dead weight.

---

## G7. Live combat is unverified against the real server

`CURRENT.md` §6 describes the solver→ranker→live pipeline, but **no fight has been
proven against the client**. Evidence: `agent/executor.py` records
`live_calibrated: false`, and `models/fight_calibrator.json` is absent. Across the
recorded step history the fight tools (`fight.join`, `farm.start`, `fight.pass`)
were essentially never reached, because runs stalled on the *talk* objective before
ever arriving at a kill step. Do not count offline combat tests as live combat
proof.

---

## G8. The test gate is gone — only one self-check remains

The `agent/smoke_verifier.py` gate (last reported 110 checks) was deleted on
2026-10-25 along with the rest of the Python test suite. The only surviving Python
harness self-test in `dofus-network/` is **`advisor/selftest.py`** (run with
`python -m advisor selftest`). The other two gates live outside Python — the JS
`tools/test_claim_guard.js` and the C# `DofusFarm.exe --bundle-selftest` — and are
not the agent-harness contract checks smoke_verifier used to be. Every "guard added
so the fix does not silently come back" that used to live in smoke_verifier is now
unenforced. Restoring a runnable Python gate is a prerequisite to claiming any
regression is caught. *Note:* the project memory pointer that said "tests live under
`dofus-emu-cleanup-2026-10-25`" is stale — that folder no longer holds them.

---

## G9. Identity is keyed by slot, not by account

The bridge/instance layer addresses an instance by its **slot → port** mapping
(`bridge_port.txt`, relay port `8880+slot`). Nothing binds a run to the *account*
logged in on that client. On a multi-instance host, "which character did this step"
is answered by which slot, and a slot that reloaded into a different login is
indistinguishable to the harness. Relevant to the "different exit IP looks like a
hijacked session" class of disconnect.

---

## G10. `kaggle/toolkit/` drifts with no generator

`dofus-network/kaggle/toolkit/` is a hand-assembled copy of `agent/` + `sim/` that
backs the published Kaggle dataset (`tajdinenizar/thx-02-sim-toolkit`) a live kernel
runs. It has **already drifted** (its `autopilot.py` and `planner.py` differ from the
harness) and there is no export script in the repo to regenerate it. It is not a fork
to delete — deleting it breaks the dataset a kernel depends on — but it is a fourth
copy of the harness that will keep diverging. Fix: an export script, or accept the
drift explicitly.

---

## Priority, if these were ordered by what blocks a real run

1. **G1** keep-alive — it is why healthy runs still die.
2. **G3** dialog reasoning — it is why the talk objective never completes, which
   (per G7) is what prevents fights from ever being reached.
3. **G8** test gate — restore it before trusting any further change.
4. **G2** equip, **G5** forecaster, **G7** fight proof, then G4/G6/G9/G10.
