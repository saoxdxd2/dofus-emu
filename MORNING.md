# Morning — what to run, in order

Everything below is committed. The client-side changes need **one page reload**; the
C# changes need a **rebuild**. Nothing needs a fresh login.

## 1. One click to load the new agent script

The app is (or was) running with the previous script. Two ways, either is fine:

- **Rebuild** (`compile.bat`), start the app, then press **Reload client** in the AI panel
  (new button), or
- **Without rebuilding:** restart the app and launch an instance as usual.

The page reload is what makes every fix below take effect. `ai/verify_live.py` tells you
whether it worked — its first check after the bridge is `agent script is current`.

## 2. The check that matters

```bash
cd dofus-network
python ai/verify_live.py
```

It prints one line per primitive: **PASS / FAIL / SKIP**, with the reason. Read the first
FAIL — the later checks depend on it. SKIP means "could not run", never "works".

Expected shape once things are right:

```
[PASS] bridge answers                     port 8894
[PASS] a client window is open            instances=1
[PASS] agent script is current            2026-10-23a
[PASS] snapshot returns state             24 fields
[PASS] character is in the world          Aiismine level 18 on map 150736645 cell 342
[PASS] the character has health           120/240
[PASS] talk opens a dialog                npc template 447 (actor 1234567)
[PASS] a dialog reply is accepted         replied
[PASS] the character moves                cell 342 -> 305 (ok)
[PASS] NPC actors are visible             3 found
[PASS] monster groups are visible         0 found
```

## 3. If health is 0/0

`hp 0/0` means the character is dead (or the stats are not loaded in this client state).
A dead character can walk but cannot talk, so revive first. `verify_live` flags this
before the talk check for exactly that reason.

## 4. Then the agent

Press the in-game badge (it is the AI button) or the panel's **Start**. The goal is
already `plz do all the quests`, policy is pinned to the planner, steps are unlimited.

Watch `%LOCALAPPDATA%\DofusFarm\autopilot.jsonl` and the panel log. What you want to see:

- `quest.advance ... ok advanced` — not `noop`. `advanced` now means the observation
  really changed (cell, map, dialog or quest counter).
- `[autopilot] retiring quest N; taking another` when a quest cannot progress — the loop
  keeps going instead of exiting.
- No `[AI] loop finished (exit 0)` after a handful of steps.

## What changed tonight, and why each one mattered

| fix | symptom it removes |
|---|---|
| `--directive-url` no longer defaults to 8893 | "bridge not reachable" while the bridge was on 8894 |
| snapshot reads the real fields | character/map/HP were null on a live character |
| verifier reads the nested shape | **every step scored `noop`** — the agent was blind |
| snapshot carries quest state | planner could not tell which quest/objective was current |
| planner claims ready objectives (status 2) | free progress that used to be invisible |
| `quest.advance` answers dialogs | talk + deliver (46% of objectives) could not complete |
| `quest.advance` walks to the NPC first | talking from across the map did nothing |
| NPC lookup via `data.staticInfos.npcId` + `contextualId` | "no NPC on this map" while standing next to one |
| `fight.join` sends `GameRolePlayAttackMonsterRequestMessage` | it clicked a DOM selector that does not exist |
| `nav.goto` drives the client's own pathfinder | travel was one blind edge hop, so nothing multi-map |
| unlimited really unlimited | every Auto run did 8 steps then exited 0 |
| read-only success no longer ends the run | one `state.snapshot` ended the session |
| `_skipped` filters active quests | a stuck quest was re-planned forever |
| stall detection on `objectives_completed` | a retry/recover livelock was invisible |
| badge acknowledges the press | a working button looked dead |
| "Reload client" button + `debug.reload` | every JS fix needed an app restart |

## Still not built

- **Kill objectives (36.5%).** `fight.join` now attacks a group properly, and the
  auto-fight bot passes turns, but nothing targets *a specific monster* or counts kills
  toward an objective. If the right monster is not on the map, the objective cannot
  progress.
- **Cross-map routing.** `nav.goto` changes one map through the client, and walks within
  a map. There is no route across several maps; the client's GPS/zaap path is not wired.
- **`_resizeUi` uncaught JS error** in the patched bundle (fires on resize). Harmless to
  the agent, but it is a real error in the client.
