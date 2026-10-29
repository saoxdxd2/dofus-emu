// Behavioural checks for the fallback that has played every live fight turn (KNOWN_GAPS G7).
//
// The brain was never started until G24 and refused every board until G25, so `_planFightAction`
// is the policy the server actually saw. Only its AP budget was tested. This suite extracts the
// real target/range/move code from the page and runs it against bundle-shaped spells and enemies:
// lowest-HP targeting, min-range exclusion, the AP gate, movement toward the nearest enemy, the
// pass when nothing is reachable, and the non-damage filter.

const fs = require('fs');
const path = require('path');

let pass = 0, fail = 0;
function check(name, ok, detail) {
    if (ok) { console.log('  [PASS] ' + name); pass++; }
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const src = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'mobile-disguise.js'), 'utf8');
const START = '_damageSpell: function(spell) {';
const END = "// The bundle's compressPath";
const s = src.indexOf(START);
const e = src.indexOf(END, s);
check('the fight planner block is present', s >= 0 && e > s, START);
if (s < 0 || e <= s) {
    console.log('\n' + pass + ' passed, ' + fail + ' failed');
    process.exit(1);
}
const planner = new Function('return ({' + src.slice(s, e) + '})')();

function spell(id, cost, minRange, range, damage) {
    const props = {apCost: cost, minRange: minRange, range: range,
                   castInLine: false, castInDiagonal: false, castTestLos: true};
    return {
        id: id,
        level: cost,
        spellLevel: {effects: damage === false ? [] : [{effectId: 82}]},
        getProperty: function (name) { return props[name]; },
        getName: function () { return 'spell' + id; }
    };
}

const myCell = 100;
let near = null, far = null;
for (let cell = 0; cell < 560; cell++) {
    const d = planner._cellDistance(myCell, cell);
    if (d === 2 && near === null) near = cell;
    if (d === 8 && far === null) far = cell;
}
check('the grid offers a distance-2 and a distance-8 cell', near !== null && far !== null,
    JSON.stringify({near: near, far: far}));

function world(over) {
    const base = {
        myCell: myCell, myId: 7, ap: 6, mp: 3, ap_source: 'fighter',
        fm: {canCastThisSpell: function () { return true; }},
        iso: null, am: null, spells: {}, enemies: []
    };
    Object.keys(over || {}).forEach(function (key) { base[key] = over[key]; });
    return base;
}

const short = spell(1, 2, 1, 2);
const long = spell(2, 5, 5, 10);
const buff = spell(3, 1, 1, 10, false);

// Lowest HP wins even when it is farther away, and a spell whose min range excludes
// the closer enemy is not chosen just because it is first in the cost sort.
let action = planner._planFightAction(world({
    spells: {1: short, 2: long, 3: buff},
    enemies: [{id: 11, cell: far, hp: 10}, {id: 12, cell: near, hp: 50}]
}));
check('the lowest-HP enemy is targeted even when it is far',
    !!action && action.type === 'cast' && action.targetId === 11
    && action.spellId === 2 && action.cell === far, JSON.stringify(action));

action = planner._planFightAction(world({
    spells: {1: short, 2: long, 3: buff},
    enemies: [{id: 11, cell: far, hp: 50}, {id: 12, cell: near, hp: 10}]
}));
check('a min-range spell does not reach a closer, lower-HP enemy',
    !!action && action.type === 'cast' && action.targetId === 12
    && action.spellId === 1, JSON.stringify(action));

// No spell reaches the enemy: spend MP moving toward it, using the client's pathfinder.
const fakeIso = {
    _displayPathInFight: function (cell) {
        return {reachable: [myCell, cell], costMP: 1};
    }
};
const fakeAm = {getActorsOnCell: function () { return []; }};
action = planner._planFightAction(world({
    spells: {1: short},
    enemies: [{id: 11, cell: far, hp: 10}],
    iso: fakeIso, am: fakeAm
}));
check('with nothing reachable, the fallback moves toward the enemy',
    !!action && action.type === 'move'
    && planner._cellDistance(action.cell, far) < planner._cellDistance(myCell, far),
    JSON.stringify(action));

// No MP: the only honest answer is a pass.
action = planner._planFightAction(world({
    spells: {1: short},
    enemies: [{id: 11, cell: far, hp: 10}],
    mp: 0
}));
check('with no MP and nothing reachable, it passes', action === null, JSON.stringify(action));

// AP is a reading, so an unaffordable spell is not offered.
action = planner._planFightAction(world({
    spells: {1: short, 2: long},
    enemies: [{id: 11, cell: far, hp: 10}],
    ap: 2, mp: 0
}));
check('an unaffordable spell is filtered out even if it reaches',
    action === null, JSON.stringify(action));

// The client is the legality gate.
action = planner._planFightAction(world({
    spells: {1: short},
    enemies: [{id: 11, cell: near, hp: 10}],
    fm: {canCastThisSpell: function () { return false; }},
    mp: 0
}));
check('a spell the client refuses is never offered', action === null, JSON.stringify(action));

// Buffs and heals share the spell list; the fallback must not cast them at an enemy.
action = planner._planFightAction(world({
    spells: {3: buff},
    enemies: [{id: 11, cell: near, hp: 10}],
    mp: 0
}));
check('a non-damage spell is not cast as an attack', action === null, JSON.stringify(action));

check('the passed cell is the enemy cell for a cast',
    (function () {
        const a = planner._planFightAction(world({
            spells: {1: short},
            enemies: [{id: 11, cell: near, hp: 10}]
        }));
        return !!a && a.type === 'cast' && a.cell === near;
    })());

// A class kit that is locked behind a state (Pandawa's spells need Boozer) must
// spend the cheap enabler before swinging a reachable weapon, or the good spells
// stay locked for the whole fight.
function gated(id, cost, minRange, range, required) {
    const props = {apCost: cost, minRange: minRange, range: range,
                   castInLine: false, castInDiagonal: false, castTestLos: true,
                   statesRequired: required};
    return {
        id: id, level: cost,
        spellLevel: {effects: [{effectId: 82}]},
        getProperty: function (name) { return props[name]; },
        getName: function () { return 'gated' + id; }
    };
}
function enabler(id, cost, state) {
    const props = {apCost: cost, minRange: 0, range: 0};
    return {
        id: id, level: cost,
        spellLevel: {effects: [{effectId: 950, value: state}]},
        getProperty: function (name) { return props[name]; },
        getName: function () { return 'enabler' + id; }
    };
}
const locked = gated(4, 3, 1, 4, [1]);
const booze = enabler(9, 1, 1);
action = planner._planFightAction(world({
    spells: {1: short, 4: locked, 9: booze},
    enemies: [{id: 11, cell: near, hp: 50}],
    states: [],
    fm: {canCastThisSpell: function (my, id) { return id !== 4; }}
}));
check('a state-gated spell casts its enabler before a reachable weapon',
    !!action && action.type === 'cast' && action.spellId === 9
    && action.cell === myCell && action.targetId === 7, JSON.stringify(action));

action = planner._planFightAction(world({
    spells: {1: short, 4: locked, 9: booze},
    enemies: [{id: 11, cell: near, hp: 50}],
    states: [1],
    fm: {canCastThisSpell: function () { return true; }}
}));
check('with the state present the gated spell is cast normally',
    !!action && action.type === 'cast' && action.spellId === 4, JSON.stringify(action));

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
