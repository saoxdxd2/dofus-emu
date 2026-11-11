# DofusFarm — project memory (router)

**Read `dofus-network/docs/HANDOFF.md` first.** It is the one document written so a new
session can act without re-deriving the project. Everything below is a pointer, not a
fact worth quoting.

## What this is
Runs Dofus Touch (Ankama) on Windows as the real Cordova bundle in WebView2, packets
through a local Go relay, driven by a Python agent in `dofus-network/`. Not an emulator.
One operator, no redistribution. The rule that governs design: **the harness is not an
AI — the harness supplies data and tools, the AI makes decisions.**

## Where the truth lives
| question | file |
|---|---|
| onboarding, vocabulary, traps, commands | `dofus-network/docs/HANDOFF.md` |
| live status / this session | `resume.md` §0, §2 |
| what runs, ports | `dofus-network/docs/CURRENT.md` (read from code 2026-10-26) |
| what is broken/unproven | `dofus-network/docs/KNOWN_GAPS.md` (G98–G100 collide; quote dates) |
| why, with falsifiers | `dofus-network/docs/ARCH_REASSESSMENT.md` (newest section wins) |
| combat next steps | `dofus-network/docs/COMBAT_POLICY_PLAN.md` |
| rules of engagement | `rules.md` |

## Precedence rule (earned 2026-11-11)
A number is trusted only from the section that measured it, with its date. `resume.md` §0
is a summary and summaries drift; when §0 and the measuring section disagree, the
measuring section wins and §0 gets corrected.

## Verify, don't trust
From `dofus-network/`:
- `python -m agent.regression` — 2,216 checks, >5 min. Last recorded run: **2 failed**
  (`data/last_gate_run.json` stores only the count). Never assert the gate is green.
- `python -m unittest combat.test_live_policy` — 36, ~0.3 s
- `python scratch/chain_reachability.py` — proves the shipped HP ranker is reached

## Shipped combat rule
`LivePolicy._hp_choice` (env `DOFUS_COMBAT_HP_RANK`, default on) re-ranks the solver's
candidate turns by the projected HP differential. Measured **+0.0113 of an HP bar** over
600 paired duels (104 better / 17 worse, t = 5.4, CI [+0.0072, +0.0154]). The learned
ranker on the same candidates **loses** (−0.1463) and does not ship.

## Standing constraints from the operator
Beat enemies strategically and follow quests autonomously; redo learned things in online
training rather than re-solving; live-test and collect game data freely; the supervisor
never stops on failure unless the operator stops it; prefer modern mathematics over
thousands of lines of code; push harder than a minimal version.
