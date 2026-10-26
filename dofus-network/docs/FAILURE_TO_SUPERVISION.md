# FAILURE → SUPERVISION

Status: active, implemented 2026-10-24. Status lives in `PLAN.md`; the data path
lives in `PIPELINE.md`. This file is the argument and the contracts.

The simulator can generate a million clean episodes. It cannot generate the one
thing that is worth more: the server saying *no*. A kick is expensive, rare and
unambiguous, and it already carries the action that caused it. The plan is to
stop paying for the same lesson twice.

---

## 1. The three loops

| loop | runs on | produces | consumed by | must never |
|---|---|---|---|---|
| **live agent** | client + harness | guard telemetry, kick signatures, labelled outcomes | gate, repair set, eval set | evolve weights or explore actions to "see what happens" |
| **corpus** | CPU, minutes | counterfactual preference rows `(x, gains, margin)` | tool-policy pretraining | pretend it is the real server |
| **evolution / search** | **remote GPU/TPU (Kaggle or Colab) only** | hard worlds, filter thresholds, candidate policies | distillation into frozen ONNX | touch a live Dofus session, or run long on a laptop |

**Hard rule: evolution and neural search run on remote GPU/TPU (Kaggle or Colab).** They take hours
of wall-clock, they are stochastic and unreproducible on CPU, and a search loop
pointed at the real server is indistinguishable from a bot farm. The live loop
only *serves* frozen artifacts; there are no online weight updates. Local runs
are limited to short corpus generation and training sanity checks.

---

## 2. Live failures become labels

### 2.1 Kicks → claim signatures

Measured on 14 attributed kicks: `QuestObjectiveValidationMessage` is followed by
`serverDisconnecting` after a **median of 230 ms** (max 479 ms). Nothing else in
the session predicts the kick as closely.

- `ai/safety_examples.py` scans `trajectories/*.jsonl` and writes:
  - `data/safety/gate_examples.jsonl` — one row per validation: `outcome`
    (`kick` / `ok`), `map_age_ms`, `quest_sync_age_ms`, `gap_ms`. Both classes
    are present, so it can train a gate later.
  - `data/safety/kick_signatures.json` — the `quest/objective` pairs that were
    followed by a disconnect (currently 4 pairs from 54 validations).
- `WebView2Backend` seeds that list into `window.__agentKnownKickSignatures`
  before the page loads.
- The page also learns live: `window.__agentRecordKick` writes the pair to
  `localStorage` when a disconnect follows a validation by ≤ 3 s, so an
  objective that kicked once is not claimed again in a later session.
- The guard refuses `known_kick_signature`; the planner falls back to the
  objective's own action (travel / fight / talk) instead of claiming it, and
  keeps taking that action while `note_result` reports it accepted. It retires
  the quest only after three rejected actions in a row, or ten actions without a
  map change. A single refusal is not evidence that the quest is unfinishable,
  and retiring on it would lose exactly the quest the guard protected.

This is **not a quest list**. The store only holds pairs the server itself
rejected, and live memory keeps growing. Type-level refusals
(`objective_server_counted`: discover-map, fight-monster, dungeon, …) remain the
generic rule; the pair memory covers objectives whose type the client cannot
classify, without guessing.

### 2.2 Refusals → preconditions

Every attempt logs one line:

```
[AgentGuard] claim refused quest=3841 objective=13499 reason=known_kick_signature
```

The trajectory captures console output, so refusals become gate features and
repair examples instead of disappearing.

### 2.3 Regressions → assets

| regression | permanent asset |
|---|---|
| window resize re-read the boot viewport | patch rule 12 in `tools/patch-engine.js`, shipped in `patch-rules` |
| model/featuriser width drift | `features` in the ONNX metadata; `NNPlanner` warns once when it cannot match |
| corpus workers dropped `--travel-cost` / `--ordering` | flags verified in the worker argv |
| guard reasons drifting | `node tools/test_claim_guard.js` |
| harness behaviour | `python -m advisor selftest` (the old `agent/smoke_verifier.py` gate was removed 2026-10-25 — `KNOWN_GAPS.md` G8) |

### 2.4 Accepted but inert → bounded attempts

Two calls return `ok` without changing the world; both used to loop forever:

- **quest start** — the client reports `QuestStartRequestMessage` as sent, but only
  the server's `QuestStartedMessage` activates the quest. The client now remembers
  the send and reports `pending`, then `not_activated`; the planner counts
  observations of `quests_active`, so three starts that never appear there retire
  the quest. A repeated call also retires the quest it *names*, not the first
  active quest (the old autopilot blamed 3841 for a loop on 584).
- **fight gate** — `playerData.lifePoints` is stale 0 in world (a live session
  read 55/55 while every `fight.join` said "0 HP"). The gate now reads
  `playerData.isAlive()` and the selected character's characteristics, and the
  snapshot exposes `alive` / `life_state` so a real death is visible.

Rule refusals (`objective_server_counted`, unmet quest prerequisites) are class
`refused` in the controller, not `unknown`: they never get a retry.

---

## 3. Promotion gates

- **Tool policy**: must beat the teacher in the travel-cost world on quests and
  objectives — not on validation accuracy. Promoted 2026-10-24 (v4, 155,199 rows):
  83.6 vs the teacher's 81.7 quests at 400 steps (12 paired seeds, level 60) and
  140.0 vs 137.9 at 800 steps (8 seeds); at level 20, 72.8 vs 72.1. It also beat
  the previous 16,261-row model on 11/12 seeds at level 60 and 8/12 at level 20.
  A 145,732-row candidate was rejected because it lost every seed at level 20; a
  candidate must beat the installed model consistently across levels, not only on
  average.
- **Safety gate model** (stage 2): must not raise false refusals on held-out
  sessions compared with the rules-only guard, and must keep repeated kicks at
  zero. A gate that is merely accurate is not enough; the cost of refusing a
  legal claim is paid by every quest.
- **Verifier head**: stays unpromoted until `blocked` has support in the
  evaluation set.

---

## 4. Falsifiers

- If pair memory refuses legal claims on held-out sessions (a network drop was
  blamed on the claim), add expiry (30 days or one clean session) or demote the
  pair to "perform the action first".
- If 50 live validations after the guard produce zero kicks, stop growing the
  gate; keep the deterministic guard.
- If a learned gate cannot lower false refusals versus rules-only on held-out
  sessions, it is not promoted.

---

## 5. Current state

- Implemented: claim guard (15 test cases), live kick memory, claim telemetry,
  historical extractor, host-side seeding.
- Measured: 54 validations, 143 disconnects, 14 attributed, 4 signatures;
  attribution median 230 ms.
- Next: rebuild, relaunch, label one live session, compare repeated kicks per
  session against the 10 repeated claims on `4278/13986` in the old logs.
