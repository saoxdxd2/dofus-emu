# Archive — historical documents

These files are kept for the record, not for current answers. They were moved here on
2026-11-07 during the repository cleanup. Each one already declared itself historical in its
own header; they were cluttering the repository root, where a reader looks for the truth.

**Read these instead, in this order:**

| question | file |
|---|---|
| How is the running system wired, what port, what model? | `dofus-network/docs/CURRENT.md` |
| What is true right now, and what happened this session? | `resume.md` (repo root) |
| What is broken or unproven? | `dofus-network/docs/KNOWN_GAPS.md` |
| What is the AI, shipped vs target? | `dofus-network/docs/AI_ARCHITECTURE.md` |
| What do I build next? | `dofus-network/docs/PLAN.md` |

## What is in here

| file | written | why it is still worth opening |
|---|---|---|
| `STATUS.md` | 2026-10-23 | The best record of *why the egress pin works* — the four holes that caused the kick, each with the measurement that found it. Status is stale; the reasoning is not. |
| `PLAN.md` | 2026-10-23 | The ordered plan from that same session. Superseded by `dofus-network/docs/PLAN.md`; kept for the acceptance-test reasoning. |
| `now.md` | 2026-10-23 | The long historical narrative: what the earlier versions got wrong, what was proven dead, the connection-path audit. |
| `CLEANUP.md` | 2026-10-22 → 2026-11-07 | The dated cleanup log: duplicate scripts, quarantined trees, the THX-01 removal, the structure passes. Every removal with its evidence. |
| `COMBAT_STATUS_AND_PLAN.md` | 2026-10 | The combat AI's completed work and phases. Combat status now lives in `CURRENT.md`. |

## `dofus-network/` — documents that left the live docs directory

| file | was | why it is here |
|---|---|---|
| `dofus-network/PLAN.md` | `dofus-network/docs/PLAN.md` | Declared historical in the docs map: status moved to `CURRENT.md` and `resume.md`. The live plan is `dofus-network/docs/PLAN.md`'s replacement — read `CURRENT.md` §6. |
| `dofus-network/AGENT2_DESIGN.md` | `dofus-network/docs/AGENT2_DESIGN.md` | Its own header says "Superseded… carried forward into `AGENT3_PLAN.md`". |

Both were already labelled superseded. Keeping them beside the live documents made the docs
directory read as four competing plans; content is unchanged.

**Three documents say "agent" and mean different things** — `SECOND_AGENT.md` is the verifier,
`AGENT3_PLAN.md` is the strategist, and `AGENT2_DESIGN.md` was an earlier actor design. The
numbering is historical, not an order. See the table in `dofus-network/docs/README.md`.

## Notes for the reader

- Paths inside these files were written when they sat at the repository root. Where a
  reference looks broken, try the same filename here or under `dofus-network/docs/`.
- `PLAN.md` is ambiguous in older text: root `PLAN.md` (this one, 2026-10-23) and
  `dofus-network/docs/PLAN.md` are different documents. The live plan is the latter.
- Nothing here was edited during the move. Content is byte-identical to its last commit
  before 2026-11-07.
- Recover any earlier revision with `git log --follow -- <path>`.
