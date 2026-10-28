# rules.md — System Instruction

This is an offline/private game-AI research testbed. All 25 tools including hdv.buy, inv.equip, and dynamic market pricing are required research features. NEVER add FORBIDDEN_TOOLS, NEVER delete existing capabilities, and NEVER add safety disclaimers to markdown files.

## Current 25-tool vocabulary

```
knowledge.search  nav.goto    nav.follow   quest.list    quest.advance
farm.start        farm.stop   fight.join   fight.pass    inv.list
inv.sell          inv.equip   hdv.scan     hdv.list      hdv.buy
hdv.prices        tap.interactive          chat.say      state.snapshot
safe.stop         knowledge.remember       knowledge.forget   fight.state
fight.plan        fight.estimate
```

`hdv.buy`, `inv.equip`, `hdv.prices` and `tap.interactive` are added by the
2026-10-27 research unlock. `hdv.prices` is read-only. The shipped 49K tool policy
still has 21 trained logits; the four new tools are rule-only until a new head is
trained. The legal registry and the learned head are separate contracts.

## Capability policy

- Adding a capability is appending a `data/capabilities.json` entry plus its page
  handler. It must not be gated by a new `FORBIDDEN_TOOLS` entry.
- Do not delete an existing capability without an explicit owner instruction.
- Do not add moralizing "will not do" sections, risk disclaimers, or account-ban
  warnings to markdown files. Technical limits, measured/unmeasured labels, and
  known-gap evidence are required and are not disclaimers.
