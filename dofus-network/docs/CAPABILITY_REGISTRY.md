# Capability registry — the action space is data, not code

The rule:

> Actions are not hard-coded verbs. They are capabilities described in text, held
> in a registry, and selected by a model that reads the description. Adding one is
> a data change, not a code change and not a retraining run.

> **Status note (2026-10-30).** The live schema is **28 tools**, not the 16
> described below — `CURRENT.md` §7 lists them (it said 25 until 2026-10-30, when
> `ui.tap`, `ui.icons` and `shop.buy` from the tutorial work were finally counted). The
> `hdv.buy` example further down
> is now a shipped research capability; the 28-tool vocabulary also includes
> `inv.equip`, `hdv.prices` and `tap.interactive`. The mechanism the example
> demonstrates (a tool added as data, widening the head without retraining the
> trunk) is still exactly right.

## What was wrong before

`agent/schema.py` declared 16 tool calls as Python objects: `farm.start`,
`nav.goto`, `inv.sell` … Every one of them existed in three places (the literal
list, the system prompt, the encoder's fixed 16-way head), and adding a capability
meant editing code, regenerating the corpus, and retraining a classifier whose
output layer had a fixed width. The action space was frozen at training time.

Those verbs are also examples, not requirements. "farm" and "go to" are *game*
concepts; the framework should not know them.

## What it is now

```
data/capabilities.json     capability registry: id, description, backend,
                           directive, typed params
        │
        ├── agent/schema.py          builds ToolSpec objects from the registry
        │                             (falls back to the built-in list if absent)
        │
        └── ai/encoder_policy.py     action head = embeddings of the descriptions
```

### The head is derived, not learned

`EncoderConfig.use_anchors` switches the tool head from a learned 16-way
classifier to a **cosine similarity against capability anchors**:

```
tool_logits = normalize(pooled) · normalize(anchors)ᵀ × 10
```

`refresh_capability_anchors()` encodes each capability description with the same
encoder and writes the vectors into `tool_head.weight`. Consequences:

| | fixed classifier | description-derived anchors |
| --- | --- | --- |
| new capability | new class id, retrain the head | encode one description, append a row |
| head width | baked into the checkpoint | a config detail, growable at load |
| what the model compares | an opaque class index | goal text ↔ capability text |
| refusal ("no capability fits") | needs a 17th class | a threshold on the margin |

Because the anchors live in the ordinary classifier weights, the loss, the decoder,
`validate_call` and the ONNX export are all unchanged — only the *content* of the
weight matrix changes.

During training the anchors are refreshed every `--anchor-every` steps, so they
track the encoder instead of being a random projection frozen at step 0. The head
itself is frozen (`requires_grad=False`); the encoder learns to align with it.

### Adding a capability

```python
from agent.schema import add_capability, ToolSpec, _p

index = add_capability(ToolSpec(
    name="hdv.buy",
    description="Exchange kamas for an item listed on the market and receive it in the inventory.",
    params={"item": _p("str", "Item name, any language.", required=True),
            "quantity": _p("int", "Units to buy.", default=1),
            "max_price": _p("int", "Never pay more than this per unit.")},
    backend="directive",
    directive="HDV_BUY",
))
```

Verified: a 16-capability model widens to 17, refreshes its anchors, and produces
17 logits — **no retraining of the trunk, no new class id in the corpus.**

`hdv.buy` above is exactly the case the planner was blocked on: it emitted a
`buy` node whose tool did not exist. Now it can be added in three lines, and the
encoder can learn it from a handful of examples (or none at all, since the anchor
is a description).

## What stays typed, and why

The *capability contract* is still closed: `validate_call` coerces types, checks
enums and enforces cross-parameter rules (`nav.goto` needs both x and y). A
dynamic model must not be able to emit a malformed call — that is a safety
property, not a vocabulary limit. What became dynamic is the **set of things that
can be called**, not the rules for calling them.

## What this changes for the training run

The corpus already maps goals to capability ids, so it trains unchanged — the
labels are still 0..15. What differs is that the head's meaning comes from
descriptions, so the model can score a capability it has never seen a labelled
example of, and the action space can grow after deployment.

Corrections the run also needed (from the benchmark diagnosis):

| change | why |
| --- | --- |
| `--early-stop tool` | val loss fell while tool accuracy sat at chance; stop on the metric that matters |
| `--tool-weights off` | sqrt inverse-frequency weighting biased the head toward the rare tools it collapsed onto |
| `--anchor-mode` | removes the frozen class list |
