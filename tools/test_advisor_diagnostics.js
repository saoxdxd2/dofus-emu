// Tests for the read-only advisor diagnostics in scripts/mobile-disguise.js.
//
// Those helpers run inside the injected page script and read live client DB
// rows, so a schema mistake only shows up after a login. This harness extracts
// the diagnostics block and runs it against synthetic rows built from the
// shapes the game bundle reads (Items, ItemSets, ItemTypes, SpellLevels,
// Effects, inventory, spellData). It also pins the market message field names:
// the old cache used prices/objectGID and never stored a single average.

const fs = require('fs');
const path = require('path');

const scriptPath = path.join(__dirname, '..', 'scripts', 'mobile-disguise.js');
const src = fs.readFileSync(scriptPath, 'utf8').replace(/\r\n/g, '\n');
const startMarker = '// Read-only advisor diagnostics.';
const endMarker = '\n        var _agentHandlers = {';
const start = src.indexOf(startMarker);
const end = src.indexOf(endMarker, start);
if (start < 0 || end < 0) {
    console.error('cannot extract advisor diagnostics from ' + scriptPath);
    process.exit(2);
}
const body = src.slice(start, end);

function makeEnv(overrides) {
    const dbs = {
        Items: {
            100: {
                id: 100, nameId: 500, typeId: 16, level: 20,
                itemSetIds: [7],
                possibleEffects: [{ effectId: 125, diceNum: 10, diceSide: 20 }]
            },
            101: {
                id: 101, nameId: 501, typeId: 10, level: 20,
                itemSetIds: [7],
                possibleEffects: [{ effectId: 111, diceNum: 1 }]
            },
            102: {
                id: 102, nameId: 502, typeId: 10, level: 80,
                itemSetIds: [],
                possibleEffects: []
            }
        },
        ItemTypes: {
            16: { id: 16, nameId: 900, superTypeId: 2 },
            10: { id: 10, nameId: 901, superTypeId: 10 }
        },
        ItemSets: {
            7: {
                id: 7, nameId: 77, items: [100, 101],
                effects: [
                    [{ effectId: 125, diceNum: 5 }],
                    [{ effectId: 125, diceNum: 10 }, { effectId: 111, diceNum: 1 }]
                ]
            }
        },
        SpellLevels: {
            1001: { id: 1001, spellId: 50, minPlayerLevel: 1, apCost: 3, minRange: 1, range: 6, maxCastPerTurn: 1 },
            1002: { id: 1002, spellId: 50, minPlayerLevel: 5, apCost: 4, minRange: 1, range: 7, maxCastPerTurn: 1 },
            1003: { id: 1003, spellId: 50, minPlayerLevel: 10, apCost: 4, minRange: 1, range: 8, maxCastPerTurn: 1 }
        },
        Effects: {
            125: { id: 125, descriptionId: 11, characteristic: 9, category: 1, bonusType: 1, elementId: -1 },
            111: { id: 111, descriptionId: 12, characteristic: 1, category: 1, bonusType: 1, elementId: -1 }
        }
    };
    const stat = total => ({
        getTotalStat: () => total,
        getBasePts: () => total - 1,
        getEquipmentPts: () => 1,
        getBonusPts: () => 0,
        getAdditionalPts: () => 0
    });
    const spell = {
        id: 50, level: 2, isItem: false,
        spellLevels: [1001, 1002, 1003],
        getProperty: name => (name === 'nameId' ? 42 : null),
        getUpgradeCost: () => 1
    };
    const equipped = {
        0: { objectUID: 1, objectGID: 100, quantity: 1, position: 0,
             effects: { 0: { effectId: 125, diceNum: 10, diceSide: 20 } } },
        2: { objectUID: 2, objectGID: 101, quantity: 1, position: 2,
             effects: { 0: { effectId: 111, diceNum: 1 } } }
    };
    const window = {
        gui: {
            databases: dbs,
            playerData: {
                characterBaseInformations: { id: 9, name: 'Test', level: 7, breed: 1, sex: 0 },
                position: { mapId: 123, cell: 456, coordinates: { posX: 4, posY: -21 } },
                inventory: {
                    kamas: 1234, weight: 10, maxWeight: 1000,
                    equippedItems: equipped,
                    objects: Object.assign({}, equipped, {
                        3: { objectUID: 3, objectGID: 102, quantity: 2, position: 63 }
                    })
                },
                characters: {
                    mainCharacter: {
                        characteristics: {
                            strength: stat(60),
                            intelligence: stat(20),
                            chance: stat(30),
                            agility: stat(40),
                            wisdom: stat(50),
                            vitality: stat(70),
                            actionPointsCurrent: 6,
                            movementPointsCurrent: 3,
                            lifePoints: 900,
                            maxLifePoints: 1000,
                            spellsPoints: 3,
                            accountSpellsPoints: 5,
                            statsPoints: 0
                        },
                        spellData: {
                            spells: { 50: spell },
                            getUsedSpellPoints: () => 1
                        }
                    }
                }
            }
        }
    };
    Object.assign(window, overrides && overrides.window || {});
    return window;
}

function makeApi(env) {
    const sent = [];
    const _cmSend = (name, args) => { sent.push({ name, args }); return { ok: true }; };
    const _agentResult = (ok, data, error) => ({ ok, data, error: error || null });
    const __PriceCache = {
        average: {}, listings: {}, averageAt: 0, listingAt: 0, averageRequestedAt: 0,
        absorb: () => {},
        stats: () => null,
        averageOf: () => null
    };
    const factory = new Function(
        'window', 'console', '_cmSend', '__PriceCache', '_agentResult',
        body + '\nreturn {character:_agentCharacterReport,' +
        'itemdb:_agentItemDbReport,effectdb:_agentEffectDbReport,' +
        'market:_agentMarketCapture,itemRow:_agentItemRow,' +
        'effectList:_agentEffectList,upgradeCost:_agentUpgradeCost};');
    const api = factory(env, console, _cmSend, __PriceCache, _agentResult);
    return { api, sent, __PriceCache };
}

let failures = 0;
function check(label, cond) {
    if (!cond) {
        failures++;
        console.error('FAIL ' + label);
    } else {
        console.log('ok   ' + label);
    }
}

const env = makeEnv();
const { api, sent, __PriceCache } = makeApi(env);

const report = api.character({});
check('character report ok', report.ok === true);
check('character level', report.data.character.level === 7);
check('position map/cell', report.data.position.map_id === 123 && report.data.position.cell === 456);
check('strength total', report.data.stats.strength.total === 60);
check('spell budget raw', report.data.points.raw_spells_points === 3 &&
    report.data.points.raw_account_spells_points === 5);
check('spell budget cap', report.data.points.character_budget === 6 &&
    report.data.points.character_cap_left === 5);
check('one spell', report.data.spells.length === 1);
const s = report.data.spells[0];
check('spell grade cap at level 7', s.max_grade_for_level === 2);
check('spell next grade gated', s.next_grade_available === false);
check('spell total cost at grade 2', s.total_cost_at_level === 1);
check('spell refund if lowered', s.refund_if_lowered === 1);
check('spell grade rows', s.grades.length === 3 && s.grades[1].ap_cost === 4);
check('equipment positions', report.data.equipment.length === 2 &&
    report.data.equipment[0].position_name === 'amulet' &&
    report.data.equipment[1].position_name === 'ring_left');
check('equipment live effect', report.data.equipment[0].effects[0].effect_id === 125);
check('active set count', report.data.sets.length === 1 && report.data.sets[0].equipped_count === 2);
check('active set threshold effects', report.data.sets[0].active_effects.some(e => e.effect_id === 111));
check('set missing pieces', report.data.sets[0].missing_ids.length === 0);
check('owned set piece count', report.data.sets[0].owned_piece_count === 2);
check('inventory gid map', report.data.inventory.gids[102] === 2 &&
    report.data.inventory.gids[100] === 1);
check('db counts', report.data.db.counts.Items === 3 && report.data.db.counts.Effects === 2);

const db = api.itemdb({ type_id: 16, with_effects: true });
check('itemdb type filter', db.ok && db.data.total_matched === 1 &&
    db.data.rows[0].effects[0].effect_id === 125);
const dbSet = api.itemdb({ set_id: 7 });
check('itemdb set filter', dbSet.data.total_matched === 2);
const dbLevel = api.itemdb({ level_min: 50 });
check('itemdb level filter', dbLevel.data.total_matched === 1 && dbLevel.data.rows[0].gid === 102);
const dbGid = api.itemdb({ gid: 100, with_effects: true });
check('itemdb gid lookup', dbGid.data.rows.length === 1 &&
    dbGid.data.rows[0].set_ids[0] === 7 &&
    dbGid.data.rows[0].is_weapon === true);

const eff = api.effectdb({ ids: [125] });
check('effectdb row characteristic', eff.ok && eff.data.rows[0].characteristic === 9 &&
    eff.data.rows[0].descriptionId === 11);
check('effectdb sample', eff.data.count === 2 && eff.data.sample_ids.length === 2);

__PriceCache.average = { 100: 1000, 101: 2000 };
__PriceCache.averageAt = Date.now() - 1000;
__PriceCache.averageRequestedAt = Date.now();
__PriceCache.listings = { 100: [{ price: 900 }, { price: 1100 }] };
__PriceCache.listingAt = Date.now() - 500;
__PriceCache.stats = gid => (gid === 100
    ? { count: 2, min: 900, median: 1000, max: 1100, source: 'listing', age_ms: 500 }
    : null);
const mk = api.market({ gids: [100] });
check('market gid prices', mk.ok && mk.data.prices.length === 1 &&
    mk.data.prices[0][0] === 100 && mk.data.prices[0][1] === 1000);
check('market listing stats', mk.data.listings[100].median === 1000);
check('market totals', mk.data.average_count === 2 && mk.data.listing_count === 1);
const mkPage = api.market({ offset: 1, limit: 1 });
check('market paging sorted', mkPage.data.prices.length === 1 && mkPage.data.prices[0][0] === 101);
const mkRefresh = api.market({ refresh: true });
check('market refresh rate-limited', mkRefresh.data.requested === false);
__PriceCache.averageRequestedAt = 0;
const mkRefresh2 = api.market({ refresh: true });
check('market refresh sends average request', mkRefresh2.data.requested === true &&
    sent.some(m => m.name === 'ObjectAveragePricesGetMessage'));

const inst = api.effectList({ 0: { effectId: 128, diceNum: 1, effect: { characteristic: 2 } } });
check('effect list map shape', inst.length === 1 && inst[0].effect_id === 128 &&
    inst[0].characteristic === 2);
const row = api.itemRow(100, true);
check('item row fields', row.gid === 100 && row.type_id === 16 &&
    row.item_type.super_type_id === 2 && row.is_weapon === true &&
    row.possible_positions[0] === 1);

if (failures) {
    console.error(failures + ' advisor diagnostic test(s) failed');
    process.exit(1);
}
console.log('all advisor diagnostic tests passed');
