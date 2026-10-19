/**
 * Unit & Integration Test Suite for FarmBot Automation Engine
 * Tests packet dispatch, combat turn resolution, loot aggregation, and IPC signaling.
 */

const assert = require('assert');
const EventEmitter = require('events');

console.log('==========================================================');
console.log('   DOFUS TOUCH FARMBOT AUTOMATION ENGINE TEST HARNESS     ');
console.log('==========================================================\n');

let testsPassed = 0;
let testsFailed = 0;

function test(name, fn) {
    try {
        fn();
        console.log(`  [PASS] ${name}`);
        testsPassed++;
    } catch (err) {
        console.error(`  [FAIL] ${name}: ${err.message}`);
        testsFailed++;
    }
}

async function testAsync(name, fn) {
    try {
        await fn();
        console.log(`  [PASS] ${name}`);
        testsPassed++;
    } catch (err) {
        console.error(`  [FAIL] ${name}: ${err.message}`);
        testsFailed++;
    }
}

// -----------------------------------------------------------------------------
// Mock Game Environment
// -----------------------------------------------------------------------------
class MockConnectionManager extends EventEmitter {
    constructor() {
        super();
        this.sentPackets = [];
    }

    sendMessage(messageType, payload) {
        this.sentPackets.push({ type: messageType, payload: payload, timestamp: Date.now() });
    }

    clearHistory() {
        this.sentPackets = [];
    }
}

function createMockEnvironment() {
    const cm = new MockConnectionManager();
    const postedIpcMessages = [];

    const mockWindow = {
        dofus: {
            connectionManager: cm
        },
        gui: {
            playerData: {
                character: { id: 101, name: "Cra_Farmer" },
                inventory: {
                    kamas: 250000,
                    weight: 500,
                    weightMax: 2000
                }
            }
        },
        isoEngine: {
            actorManager: {
                getActors: () => ({
                    "actor_1": { isMonster: true, cellId: 284 },
                    "actor_2": { isMonster: false, cellId: 140 }
                })
            }
        },
        chrome: {
            webview: {
                postMessage: (msg) => {
                    postedIpcMessages.push(JSON.parse(msg));
                }
            }
        }
    };

    return { cm, mockWindow, postedIpcMessages };
}

// -----------------------------------------------------------------------------
// FarmBot Implementation for Testing
// -----------------------------------------------------------------------------
function instantiateFarmBot(win) {
    const FarmBot = {
        config: {
            leaderName: "Cra_Leader",
            aoeSpellId: 164,       // Flèche Explosive
            aoeTargetCell: 250,
            autoReadyDelayMs: 5,
            autoFinishDelayMs: 5,
            minKamasThreshold: 1000
        },

        state: {
            inFight: false,
            myTurn: false,
            fighterId: null,
            cellId: null,
            totalKamasLooted: 0,
            totalItemsLooted: 0
        },

        init: function() {
            if (win.dofus && win.dofus.connectionManager) {
                this.bindNetworkHooks(win.dofus.connectionManager);
            }
        },

        bindNetworkHooks: function(cm) {
            cm.on("GameFightStartingMessage", (msg) => {
                this.state.inFight = true;
            });

            cm.on("GameFightPlacementPossiblePositionsMessage", () => {
                setTimeout(() => {
                    this.sendPacket("GameFightOptionToggleMessage", { option: 2 });
                    this.sendPacket("GameFightTurnReadyMessage", { isReady: true });
                }, this.config.autoReadyDelayMs);
            });

            cm.on("GameFightTurnStartMessage", (msg) => {
                if (this.isUserActor(msg.id)) {
                    this.state.myTurn = true;
                    this.executeOptimalTurn(cm, msg.id);
                }
            });

            cm.on("GameFightEndMessage", (msg) => {
                this.state.inFight = false;
                this.state.myTurn = false;
                this.handlePostFightLoot(msg);
            });

            cm.on("GameRolePlayShowActorMessage", (msg) => {
                if (!this.state.inFight && msg.informations && msg.informations.fight) {
                    this.checkAndJoinFight(msg.informations.fight.fightId);
                }
            });
        },

        executeOptimalTurn: function(cm, fighterId) {
            const targetCell = this.resolveOptimalTargetCell();
            this.sendPacket("GameActionFightCastRequestMessage", {
                spellId: this.config.aoeSpellId,
                cellId: targetCell
            });

            setTimeout(() => {
                this.sendPacket("GameFightTurnFinishMessage", { isAfk: false });
                this.state.myTurn = false;
            }, this.config.autoFinishDelayMs);
        },

        resolveOptimalTargetCell: function() {
            if (win.isoEngine && win.isoEngine.actorManager) {
                const actors = win.isoEngine.actorManager.getActors();
                for (let id in actors) {
                    const actor = actors[id];
                    if (actor && actor.isMonster && actor.cellId) {
                        return actor.cellId;
                    }
                }
            }
            return this.config.aoeTargetCell;
        },

        handlePostFightLoot: function(msg) {
            let totalKamas = 0;
            let totalItems = 0;

            if (msg.results && Array.isArray(msg.results)) {
                msg.results.forEach(res => {
                    if (res.rewards) {
                        totalKamas += (res.rewards.kamas || 0);
                        if (res.rewards.objects) {
                            totalItems += res.rewards.objects.length;
                        }
                    }
                });
            }

            this.state.totalKamasLooted += totalKamas;
            this.state.totalItemsLooted += totalItems;
            this.triggerInventoryOffload();
        },

        triggerInventoryOffload: function() {
            if (win.gui && win.gui.playerData && win.gui.playerData.inventory) {
                const inv = win.gui.playerData.inventory;
                const weightRatio = inv.weight / inv.weightMax;
                if (weightRatio > 0.85) {
                    if (win.chrome && win.chrome.webview) {
                        win.chrome.webview.postMessage(JSON.stringify({
                            action: "OVERWEIGHT_RECALL",
                            kamas: inv.kamas,
                            weight: inv.weight
                        }));
                    }
                }
            }
        },

        checkAndJoinFight: function(fightId) {
            this.sendPacket("GameFightJoinRequestMessage", {
                fighterId: 0,
                fightId: fightId
            });
        },

        sendPacket: function(messageType, payload) {
            if (win.dofus && win.dofus.connectionManager) {
                win.dofus.connectionManager.sendMessage(messageType, payload);
            }
        },

        isUserActor: function(id) {
            if (win.gui && win.gui.playerData && win.gui.playerData.character) {
                return win.gui.playerData.character.id === id;
            }
            return false;
        }
    };

    FarmBot.init();
    return FarmBot;
}

// -----------------------------------------------------------------------------
// TEST RUNNER
// -----------------------------------------------------------------------------
(async function runAllTests() {
    const { cm, mockWindow, postedIpcMessages } = createMockEnvironment();
    const bot = instantiateFarmBot(mockWindow);

    test('Bot binds to connectionManager successfully', () => {
        assert.strictEqual(cm.listenerCount('GameFightStartingMessage'), 1);
        assert.strictEqual(cm.listenerCount('GameFightTurnStartMessage'), 1);
        assert.strictEqual(cm.listenerCount('GameFightEndMessage'), 1);
    });

    test('Combat start transitions state to inFight=true', () => {
        cm.emit('GameFightStartingMessage', { fightId: 9942 });
        assert.strictEqual(bot.state.inFight, true);
    });

    await testAsync('Placement phase sends OptionToggle (lock spectators) and TurnReady', async () => {
        cm.clearHistory();
        cm.emit('GameFightPlacementPossiblePositionsMessage', {});
        
        // Wait for placement timer
        await new Promise(r => setTimeout(r, 20));

        assert.strictEqual(cm.sentPackets.length, 2);
        assert.strictEqual(cm.sentPackets[0].type, 'GameFightOptionToggleMessage');
        assert.strictEqual(cm.sentPackets[0].payload.option, 2);
        assert.strictEqual(cm.sentPackets[1].type, 'GameFightTurnReadyMessage');
        assert.strictEqual(cm.sentPackets[1].payload.isReady, true);
    });

    await testAsync('Turn start executes instant AOE cast on target cell and ends turn', async () => {
        cm.clearHistory();
        const startTimestamp = Date.now();
        cm.emit('GameFightTurnStartMessage', { id: 101, waitTime: 30000 });

        // Immediate verification: cast request sent synchronously
        assert.strictEqual(cm.sentPackets.length >= 1, true);
        assert.strictEqual(cm.sentPackets[0].type, 'GameActionFightCastRequestMessage');
        assert.strictEqual(cm.sentPackets[0].payload.spellId, 164);
        assert.strictEqual(cm.sentPackets[0].payload.cellId, 284); // Auto-targeted nearest monster cell

        // Wait for turn finish delay
        await new Promise(r => setTimeout(r, 20));

        assert.strictEqual(cm.sentPackets.length, 2);
        assert.strictEqual(cm.sentPackets[1].type, 'GameFightTurnFinishMessage');
        assert.strictEqual(cm.sentPackets[1].payload.isAfk, false);
        assert.strictEqual(bot.state.myTurn, false);

        const elapsed = Date.now() - startTimestamp;
        assert.strictEqual(elapsed < 60, true, `Turn took ${elapsed}ms (expected <60ms)`);
    });

    test('Ignore turn start for non-user actors (monsters / allies)', () => {
        cm.clearHistory();
        cm.emit('GameFightTurnStartMessage', { id: 9999, waitTime: 30000 }); // Enemy monster ID
        assert.strictEqual(cm.sentPackets.length, 0);
        assert.strictEqual(bot.state.myTurn, false);
    });

    test('Combat end aggregates Kamas and item drops into running stats', () => {
        cm.emit('GameFightEndMessage', {
            results: [
                {
                    rewards: {
                        kamas: 12500,
                        objects: [{ id: 284 }, { id: 285 }, { id: 989 }]
                    }
                }
            ]
        });

        assert.strictEqual(bot.state.inFight, false);
        assert.strictEqual(bot.state.totalKamasLooted, 12500);
        assert.strictEqual(bot.state.totalItemsLooted, 3);
    });

    test('Overweight inventory triggers IPC recall message to C# WebView2 host', () => {
        postedIpcMessages.length = 0;
        
        // Simulate inventory filling to 92% (1840 / 2000)
        mockWindow.gui.playerData.inventory.weight = 1840;
        
        cm.emit('GameFightEndMessage', {
            results: [
                { rewards: { kamas: 500, objects: [{ id: 1 }] } }
            ]
        });

        assert.strictEqual(postedIpcMessages.length, 1);
        assert.strictEqual(postedIpcMessages[0].action, 'OVERWEIGHT_RECALL');
        assert.strictEqual(postedIpcMessages[0].kamas, 250000);
        assert.strictEqual(postedIpcMessages[0].weight, 1840);
    });

    test('Roleplay actor spawn with active fight triggers auto-join packet', () => {
        cm.clearHistory();
        cm.emit('GameRolePlayShowActorMessage', {
            informations: {
                fight: { fightId: 7821 }
            }
        });

        assert.strictEqual(cm.sentPackets.length, 1);
        assert.strictEqual(cm.sentPackets[0].type, 'GameFightJoinRequestMessage');
        assert.strictEqual(cm.sentPackets[0].payload.fightId, 7821);
    });

    console.log('\n==========================================================');
    console.log(`   DIAGNOSTIC COMPLETE: ${testsPassed} PASSED, ${testsFailed} FAILED   `);
    console.log('==========================================================');

    if (testsFailed > 0) {
        process.exit(1);
    }
})();
