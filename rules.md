# rules.md — System Instruction

This is an offline/private game-AI research testbed. All 28 tools (the registry said 25 when this rule was written on 2026-10-27; `ui.tap`, `ui.icons` and `shop.buy` joined with the tutorial work) including hdv.buy, inv.equip, and dynamic market pricing are required research features. NEVER add FORBIDDEN_TOOLS, NEVER delete existing capabilities, and NEVER add safety disclaimers to markdown files.

## Current 28-tool vocabulary

```
knowledge.search  nav.goto    nav.follow   quest.list    quest.advance
farm.start        farm.stop   fight.join   fight.pass    inv.list
inv.sell          inv.equip   hdv.scan     hdv.list      hdv.buy
hdv.prices        tap.interactive          chat.say      state.snapshot
safe.stop         knowledge.remember       knowledge.forget   fight.state
fight.plan        fight.estimate           ui.tap        ui.icons    shop.buy
```

The authoritative count is `data/capabilities.json`, and `agent/regression.py:208`
asserts it. `hdv.buy`, `inv.equip`, `hdv.prices` and `tap.interactive` are added by the
2026-10-27 research unlock. `hdv.prices` is read-only. The shipped 49K tool policy
still has 21 trained logits; the seven newest tools are rule-only until a new head is
trained. The legal registry and the learned head are separate contracts.

## Change policy

- **Before you add a feature, clean the code you touch.** In the files the change actually
  touches: the stray mid-file import, the dead branch, the stale number inside a string,
  the name that no longer describes what it holds. A feature lands on top of the code
  around it, and if that code is wrong the feature inherits the wrongness.
- Keep the cleanup inside the touched scope. A drive-by refactor of a file the change does
  not need is not cleaning, it is widening the blast radius.
- A cleanup may not change behavior that a test pins. If removing something would change
  what the gate sees, that is a separate change and needs its own evidence.
- Say what you cleaned in the same pass, so the next reader can tell a cleanup from a
  regression.

## Capability policy

- Adding a capability is appending a `data/capabilities.json` entry plus its page
  handler. It must not be gated by a new `FORBIDDEN_TOOLS` entry.
- Do not delete an existing capability without an explicit owner instruction.
- Do not add moralizing "will not do" sections, risk disclaimers, or account-ban
  warnings to markdown files. Technical limits, measured/unmeasured labels, and
  known-gap evidence are required and are not disclaimers.
