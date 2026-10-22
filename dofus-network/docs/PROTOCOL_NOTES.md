# Protocol notes — captured from a real session

Every payload below was recorded from an actual play session on 2026-10-22
(`dofus-network/trajectories/play_dofus-05.jsonl`, 526 sends / 668 receives,
12 fights, a character created, quests accepted and validated, maps crossed).

This is the reference the client verbs get written against — not guesses from a
minified bundle.

## How the capture works

`window.__DofusRecorder` wraps `connectionManager.sendMessage`, `send`, the
`eventHandlers` registry and `connect`, so both directions are logged with their
decoded argument objects. Clicks, keys, state deltas and console lines ride in the
same stream. Everything lands in `trajectories/play_<instance>.jsonl`, one JSON
object per line.

## Actions and their exact wire format

### Create a character

```json
CharacterCreationRequestMessage {
  "name": "Myai", "breed": 4, "sex": true,
  "colors": [32161890, 50331630, 54489502, 71266718, 98758585],
  "cosmeticId": 59
}
```

`breed` is the class id, `colors` are five packed RGB values, `sex: true` = female
on this build. The client generates all of it from the creation UI — the emulator
only has to let the player click through.

### Enter / refresh a map

```json
MapInformationsRequestMessage { "mapId": 147062786 }
ChangeMapMessage            { "mapId": 147063298 }
```

`MapInformationsRequestMessage` is the "tell me what is here" call and is sent on
every map entry; `ChangeMapMessage` is the walk-off-the-edge transition.

### Walk

```json
GameMapMovementRequestMessage { "keyMovements": [425, 29098, 24934, 29002, 28989],
                                "mapId": 147062786 }
GameMapMovementConfirmMessagе { }        // note: the 'е' is Cyrillic in the bundle
```

`keyMovements` is the encoded path: the first element is the start cell, the rest
are (cell, direction) packed into one int per step. The client computes it in its
own pathfinder — **do not reimplement it**; drive the client's tap path instead so
path validity, obstacles and map edges stay the game's problem.

### Fight

```json
GameFightReadyMessage      { "isReady": true }
GameFightTurnReadyMessage  { }            // sent when our turn starts
GameFightTurnFinishMessage { }            // end of turn
GameActionAcknowledgementMessage { ... }  // per action, 44 in one session
```

The observed fight loop is: `GameFightTurnReadyRequestMessage` (server) →
`GameFightTurnReadyMessage` (client) → actions → `GameFightTurnFinishMessage`.
That is exactly what `FarmBot`'s auto-fight already drives, which is why fights
worked before any of the walking existed.

### NPC and quests

```json
NpcGenericActionRequestMessage { "npcId": -1, "npcActionId": 3, "npcMapId": 147062786 }
NpcDialogReplyMessage          { "replyId": 41675 }
QuestObjectiveValidationMessage{ "questId": 3841, "objectiveId": 13491 }
```

`npcActionId: 3` is "talk". The dialog is a sequence of `NpcDialogReplyMessage`
with the reply the player clicked. Validating an objective is a single message —
`quest.advance` maps onto it directly.

### Market (HDV) — captured 2026-10-22

```json
ExchangeBidHouseTypeMessage      { "type": 42 }                          // open a category
ExchangeBidHouseListMessage      { "id": 468 }                           // list items of that type
ExchangeBidHouseBuyMessage       { "uid": 315540, "qty": 10, "price": 100 }   // BUY
ExchangeObjectMovePricedMessage  { "objectUID": 83515201, "quantity": 1, "price": 38 }  // LIST FOR SALE
ObjectAveragePricesGetMessage    { }                                     // request average prices
ExchangeLeaveMessage             { "dialogType": 11, "success": false }  // close
```

`ExchangeBidHouseBuyMessage.uid` is the **listing** id (not the item id), and the
price is echoed back with it — that is the contract a bot must respect: it buys a
specific listing at the price it read, not "an item at some price".

Listing is `ExchangeObjectMovePricedMessage` with the inventory `objectUID`, a
quantity and a price per unit. That maps 1:1 onto `hdv.list` and `inv.sell`, so the
`price_rule` field becomes: look up the average (or the cheapest listing) and emit
this message with the computed number.

### Zaap travel — captured 2026-10-22

```json
ZaapOpenDialogRequestMessage { }
ZaapListMessage { "teleporterType": 0, "mapIds": [], "costs": [], "spawnMapId": 145490435 }
```

The client opens the dialog, the server answers with the destinations and their
costs, and the client sends the travel request. `mapIds` was empty in this capture
because the character had discovered no zaaps yet — a session with a known zaap
would fill it.

### Stats

```json
StatsUpgradeRequestMessage { ... }
```

## Server messages worth knowing

| message | meaning |
|---|---|
| `CurrentMapMessage` | you are now on this map |
| `MapComplementaryInformationsDataMessage` | actors, interactive elements, NPCs for the current map |
| `GameContextCreateMessage` | entered the game world |
| `GameFightShowFighterMessage` | a fighter joined the fight |
| `GameFightEndMessage` | fight over — the trigger for the farm counters |
| `FarmSelectionMessage` | farm/selection state update |
| `QuestStepValidatedMessage` / `MapNpcsQuestStatusUpdateMessage` | quest progress |
| `BasicTimeMessage` | server clock, sent regularly |

## Prices (separate channel from the listing)

A scan does not answer with prices. The request opens a category; the numbers
arrive later, as separate messages:

```json
ObjectAveragePricesGetMessage  {}                                    // send
ObjectAveragePricesMessage     {ids: [7733, 15371, ...],              // recv, ~55 KB
                                prices: [1200, 380, ...]}
ExchangeBidHouseInListAddedMessage {objectGID, objectUID, prices: [...], quantities: [...]}
```

`ids[i]` pairs with `prices[i]`. The average table covers **every item on the
server** in one message, which is why the page caches it (`__PriceCache`) instead
of pushing 55 KB through the bridge on every step.

A listing resolves `price_rule` from that cache and returns `price_source`
(`median_minus_1:listing` or `undercut:average`). If neither message has arrived
for the item, the listing is refused — it is never listed at a guessed price.

## Not yet captured

- **Harvest**: `FarmSelectionMessage` appeared 11 times but no explicit
  "collect resource" request was identified. One session standing on a resource
  spot and pressing the interact key would settle it.
- **Zaap destinations**: the dialog and the list are captured, but `mapIds` was
  empty because no zaap had been discovered yet on that character.

## Rules for using this

1. **Drive the client, do not reimplement it.** Pathfinding, line of sight and map
   edges are the game's; our job is to choose a destination.
2. **One message per action.** The client already batches what it needs; sending
   protocol messages by hand duplicates its state machine and desyncs it.
3. **Capture before coding.** Every payload here came from a real session, and the
   three gaps above are exactly the verbs that remain stubbed.
