// Behavioural checks for the inventory equip read (KNOWN_GAPS G2's prerequisite).
//
// Why this exists: the measured dialog stall is the tutorial's "Go and equip your new items."
// page -- it was re-answered 87 times and the server answered the reply with LeaveDialogMessage
// 91 times -- because the loop has no equip verb. The send side already exists in the client
// (`InventoryPresetUseMessage {presetId}`, gated behind `isInFight()`/`blockPreset()`), but the
// snapshot never exposed what is worn or which presets exist, so a preset id could not be chosen
// without inventing one. This block is the read; the verb waits for a live capture of the shapes.
//
// The bundle guard at the bottom is the G25 lesson applied before the mistake is made: every
// property name read off the live inventory has to exist in the bundle the app actually serves,
// and field names that are genuinely unknown (a worn item's uid) are captured as keys instead of
// guessed.

const fs = require('fs');
const path = require('path');

let pass = 0, fail = 0;
function check(name, ok, detail) {
    if (ok) { console.log('  [PASS] ' + name); pass++; }
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const src = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'mobile-disguise.js'), 'utf8');
const bundleFile = path.resolve(__dirname, '..', 'desktop', 'client', 'www', 'build', 'script.js');
const bundle = fs.existsSync(bundleFile) ? fs.readFileSync(bundleFile, 'utf8') : null;

const START = 'var bag = inv;';
const start = src.indexOf(START);
check('the equip read is present in the snapshot builder', start >= 0, START);
const tail = src.indexOf('snap.inventory.in_fight_inventory', start);
check('the send guard is read too, because it decides whether a preset can be applied',
    tail >= 0, 'in_fight_inventory');
const stopMarker = '} catch (e) {}';
const end = src.indexOf(stopMarker, tail);
check('the block ends where the extraction expects', end >= 0, stopMarker);
if (start < 0 || tail < 0 || end < 0) {
    console.log('\n' + pass + ' passed, ' + fail + ' failed');
    process.exit(1);
}
const block = src.slice(start, end + stopMarker.length);

function run(inv) {
    const snap = {inventory: {kamas: 100, items: [], raw_keys: null}};
    new Function('inv', 'snap', block + '\nreturn snap;')(inv, snap);
    return snap.inventory;
}

const FULL = {
    kamas: 5,
    objects: {11: {objectGID: 900, quantity: 2}},
    equippedItems: {1: {objectGID: 500, quantity: 1, objectUID: 9001},
                    2: {objectGID: 501, objectUID: 9002},
                    10: {objectGID: 510}},
    presets: {7: {name: 'Farm', items: [1, 2, 3]}},
    itemSets: [{name: 'Set A', items: [1]}, {name: 'Set B', items: []}],
    blockPreset: function () { return false; },
    isInFight: function () { return false; }
};

let out = run(FULL);
check('worn items are listed by slot, in numeric form',
    out.equipped.length === 3 && out.equipped[0].slot === 1
    && out.equipped[1].slot === 2 && out.equipped[2].slot === 10,
    JSON.stringify(out.equipped));
check('a worn item carries its object gid, which is how the loop learns what is already on',
    out.equipped[0].gid === 500, JSON.stringify(out.equipped[0]));
check('a worn item names its own object uid, so a step never re-equips it (G62)',
    out.equipped[0].object_uid === 9001 && out.equipped[1].object_uid === 9002,
    JSON.stringify(out.equipped));
check('an entry with no object uid says null rather than inventing one',
    out.equipped[2].object_uid === null, JSON.stringify(out.equipped[2]));
check('the verb\'s argument is readable: a preset id, its name and its size',
    out.presets.length === 1 && out.presets[0].preset_id === 7
    && out.presets[0].name === 'Farm' && out.presets[0].items === 3,
    JSON.stringify(out.presets));
check('item sets are counted, not dumped, so the payload stays bounded',
    out.item_sets.length === 2 && out.item_sets[0].items === 1
    && out.item_sets[1].items === 0, JSON.stringify(out.item_sets));
check('the equip-entry keys are captured rather than guessed',
    Array.isArray(out.equipped_entry_keys)
    && out.equipped_entry_keys.indexOf('objectGID') >= 0,
    JSON.stringify(out.equipped_entry_keys));
check('a free inventory says so, because that is the difference between waiting and blocked',
    out.preset_blocked === false && out.in_fight_inventory === false,
    JSON.stringify([out.preset_blocked, out.in_fight_inventory]));

out = run(Object.assign({}, FULL, {blockPreset: function () { return true; },
                                 isInFight: function () { return true; }}));
check('the client\'s own guards are reported, not bypassed',
    out.preset_blocked === true && out.in_fight_inventory === true,
    JSON.stringify([out.preset_blocked, out.in_fight_inventory]));

const delegated = {
    kamas: 1,
    _type: 0,
    getCorrectInventory: function () {
        return {equippedItems: {4: {objectGID: 700}}, presets: {2: {name: 'P', items: [1]}},
                itemSets: [], _type: 0};
    }
};
out = run(delegated);
check('a manager that hands out the real per-mode inventory is followed',
    out.equipped.length === 1 && out.equipped[0].gid === 700
    && out.presets[0].preset_id === 2, JSON.stringify([out.equipped, out.presets]));

out = run({kamas: 1, objects: {}});
check('an inventory with nothing equipped reports empty lists instead of failing',
    out.equipped.length === 0 && out.presets.length === 0 && out.item_sets.length === 0,
    JSON.stringify(out));

const many = {};
for (let i = 0; i < 40; i++) many['s' + i] = {objectGID: 1000 + i};
const manyPresets = {};
for (let i = 0; i < 30; i++) manyPresets[i] = {name: 'p' + i, items: []};
out = run({equippedItems: many, presets: manyPresets,
           itemSets: new Array(20).fill({name: 'x', items: [1, 2]})});
check('slots are capped at 24', out.equipped.length === 24, String(out.equipped.length));
check('presets and item sets are capped at 12 each',
    out.presets.length === 12 && out.item_sets.length === 12,
    String([out.presets.length, out.item_sets.length]));
check('a numeric slot key that is not a number stays a string instead of becoming NaN',
    out.equipped.every((e) => typeof e.slot !== 'number' || !isNaN(e.slot)),
    JSON.stringify(out.equipped.slice(0, 2)));

const arrayPresets = run({presets: [{presetId: 3, name: 'A', items: [1]}]});
check('an array-shaped preset list still yields its id',
    arrayPresets.presets.length === 1 && arrayPresets.presets[0].preset_id === 3,
    JSON.stringify(arrayPresets.presets));

check('a broken inventory read cannot take the snapshot down', (() => {
    try {
        run({equippedItems: null, presets: null, itemSets: null,
             getCorrectInventory: function () { throw new Error('boom'); }});
        return true;
    } catch (e) { return false; }
})(), 'getCorrectInventory throwing must not end the observation');

const reads = [];
block.replace(/\b(?:bag|inv|worn|preset|set)\.([A-Za-z_][A-Za-z0-9_]*)/g, (all, name) => {
    if (reads.indexOf(name) < 0) reads.push(name);
    return all;
});
if (bundle === null) {
    check('the served bundle is readable for the name guard', false, bundleFile);
} else {
    check('the block reads a recognisable set of fields', reads.length >= 8, reads.join(','));
    reads.forEach((name) => {
        check('"' + name + '" exists in the served bundle', bundle.indexOf(name) >= 0);
    });
}

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
