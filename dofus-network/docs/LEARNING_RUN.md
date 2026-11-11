# The learning run: one session, two arms

## Why this run exists

Three goals were stated: fight strategically, get past stuck points, and stop re-solving a
learned thing every session. They share one bottleneck — **the agent has to play, in
situations where there is something to choose.** What the machine already holds, measured:

| | |
|---|---|
| decided steps in the logs | 11,388 at **50.6%** `advanced` (95% CI 49.7–51.5) |
| quest objectives gained across them | **146** — one per ~78 steps |
| fights captured | **16**, as 333 boards, 160 on our own turn |
| actionable combat rows from those boards | **3** (`pass_only` 114/160, `tied` 35) |
| durable memory, seeded from the same logs | **318** solutions over 73 contexts, 191 dead ends |

The last two lines are the argument. The memory exists and is populated; whether replaying
from it is better than the loop's own repair is unmeasured. The combat corpus is thin
because the fights are thin, not because capture is broken.

## The two arms

| arm | records dead ends and solutions | dispatches a remembered call when the policy is silent |
|---|---|---|
| `replay` | yes | yes |
| `control` | yes | **no** |

The control arm loses nothing — it writes the same store — so the two differ in exactly
one behaviour. Each run labels itself (`session_end.memory_arm`), so the logs say which arm
they belong to without anyone having to remember.

## Preconditions

1. The app is running and a character is **in the world**. The loop refuses to start
   otherwise (`no client bridge answering on 127.0.0.1:8894`), which is the correct
   behaviour, not a failure.
2. The memory is seeded: `python tools\seed_memory.py` (idempotent — it rebuilds from the
   logs rather than merging, so counts cannot double).
3. Nothing else is driving that client slot. The launcher clears its own stop file; a
   leftover `AUTOPILOT_STOP` from another run will end this one on its first check.

## Run it

```
start-learning-run.bat replay  90
start-learning-run.bat control 90
```

Arguments: `arm [minutes] [goal] [instance]`. The instance defaults to `dofus-01`, which is
the app's name for the slot `ai_settings.ini` targets (`target_slot=1`). The goal defaults to
`farm the mobs on this map` on purpose — fights are the binding constraint for the combat
corpus, and a quests prompt may only farm to gather a delivery item.

Stop early by writing anything into `dofus-network\AUTOPILOT_STOP.<arm>`.

Run **both arms on the same day, at the same character level, on the same map** or the
comparison is between two different situations. Alternate if you can: `replay`, `control`,
`replay`, `control`. A single pair is a sample of one.

## What to watch while it runs

- `[autopilot] memory: <tool> worked here N time(s) before; replaying it` — a replay fired.
  If this never appears, the policy is never silent and the run cannot answer the question.
- `[autopilot] fight wait ...` and growth of `%LOCALAPPDATA%\DofusFarm\fight_boards.jsonl`
  — the combat corpus filling up.
- The session ending on `retirements` or `policy returned no call` — those are the states
  the memory is supposed to convert into progress.

## Read the result

```
python tools\memory_ab.py
```

It prints, per arm: sessions, steps, advance rate with a 95% interval, quest objectives
gained, and for the replay arm the advance rate **of the replayed steps specifically** —
the only population where the memory changed the decision. Then the difference between arms
with its own interval.

Decision rule, fixed in advance so the result cannot be read backwards afterwards:

- interval on the difference excludes zero and is **positive** → replay stays on.
- excludes zero and is **negative** → replay goes off; the memory keeps recording, and the
  text line (`worked here before: …`) stays, because telling the model is not the same as
  acting for it.
- interval contains zero → not separable at this sample size. Say so, and either run more
  or stop claiming the mechanism helps.

## Then combat

After the run, rebuild the corpus and look at one number:

```
python -m combat.grid_dataset --out data\combat_grid_live
```

`actionable` is the count that decides whether GPU time is worth spending. Today it is 3.
The gates in front of it are named in the output (`not_my_turn`, `pass_only`,
`tied_top_plan`), so the next fix follows from whichever one is largest rather than from a
guess. A corpus worth training on needs engagements where the solver has at least two
non-pass options — that is a property of *which mobs are fought*, not of the trainer.

## What would falsify all of this

- Replayed steps advance **no more often** than the arm's ordinary steps. Then the memory is
  a lookup that costs a step, and the honest response is to keep the store for its text and
  drop the dispatch.
- Replays fire mostly on contexts whose step id has moved on, visible as `failed`/`noop`
  replays. That means the key `(objective, type, step, map)` is too coarse, not that the
  idea is wrong.
- Objectives gained per step does not move between arms. The memory then buys nothing that
  the policy did not already do, and requirement (c) needs a different mechanism — most
  likely the learned write gate over the observation summary, which is still unbuilt.
