// Behavioural checks for the page's own fight budget: the AP/MP it believes it has, and the
// spells it will therefore try to cast.
//
// Why this exists: `_localTick` is the policy that has actually played every live turn so far
// (the brain was never started until G24, and refused every board until G25). It planned from
// `stats.actionPointsCurrent != null ? ... : 6` and `... : 3` -- read off the character sheet,
// where the fighter's own stats bag (`actionPoints`/`movementPoints`, refilled to
// `maxActionPoints`/`maxMovementPoints` on turn confirm) is the authoritative source -- and the
// candidate loop never compared a spell's AP cost against that budget at all, so an unaffordable
// spell could be offered and refused by the client, wasting a step of a turn that has a fixed
// number of them.
//
// Both blocks are extracted from scripts/mobile-disguise.js and run against stubs, so the
// ladder going back to a constant, zero being treated as missing, or an invented budget being
// able to veto a real spell all fail here. The name guard at the bottom is the G25 rule: a
// property read off a live object must exist in the bundle the app serves.

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

function between(startAnchor, endAnchor, includeEnd) {
    const s = src.indexOf(startAnchor);
    if (s < 0) return null;
    const e = src.indexOf(endAnchor, s);
    if (e < 0) return null;
    return src.slice(s, includeEnd ? e + endAnchor.length : e);
}

const A_START = 'var fighterBag = null;';
const A_END = "if (mp == null || isNaN(mp)) { mp = 3; mpFrom = 'assumed'; }";
const B_START = 'var cost = desc.apCost != null ? desc.apCost : 1;';
const B_END = 'candidates.push({ id: sid, spell: spell, cost: cost });';

const budgetBlock = between(A_START, A_END, true);
const filterBlock = between(B_START, B_END, true);
check('the budget ladder is present in the fight context', !!budgetBlock, A_START);
check('the affordability gate is present in the candidate loop', !!filterBlock, B_START);
if (!budgetBlock || !filterBlock) {
    console.log('\n' + pass + ' passed, ' + fail + ' failed');
    process.exit(1);
}

function budget(fm, stats, myId) {
    const fn = new Function('fm', 'stats', 'myId',
        budgetBlock + '\nreturn {ap: ap, mp: mp, ap_source: apFrom, mp_source: mpFrom};');
    return fn(fm, stats, myId || 7);
}

// Stub shapes, written out so the brace count is not part of the test.
function managerWithFighter(bag) {
    return {getFighter: function () { return {data: {stats: bag}}; }};
}
const MANAGER_GONE = {getFighter: function () { return null; }};
const MANAGER_THROWS = {getFighter: function () { throw new Error('no such fighter'); }};
const SHEET = {actionPointsCurrent: 6, movementPointsCurrent: 3};

let b = budget(managerWithFighter({actionPoints: 3, movementPoints: 5,
                                  maxActionPoints: 6, maxMovementPoints: 4}),
               {actionPointsCurrent: 6, movementPointsCurrent: 3});
check('the fighter bag wins, because it is what the turn consumes',
    b.ap === 3 && b.mp === 5 && b.ap_source === 'fighter' && b.mp_source === 'fighter',
    JSON.stringify(b));

b = budget(managerWithFighter({actionPoints: 0, movementPoints: 0}), SHEET);
check('a spent turn reads as zero, not as the old default of 6 and 3',
    b.ap === 0 && b.mp === 0 && b.ap_source === 'fighter', JSON.stringify(b));

b = budget(MANAGER_GONE, SHEET);
check('the sheet mirror is the second source, for a fighter the manager cannot name',
    b.ap === 6 && b.mp === 3 && b.ap_source === 'sheet' && b.mp_source === 'sheet',
    JSON.stringify(b));

b = budget(managerWithFighter({maxActionPoints: 8, maxMovementPoints: 5}), {});
check('a bag that only knows its maxima is still better than a constant',
    b.ap === 8 && b.mp === 5 && b.ap_source === 'fighterMax' && b.mp_source === 'fighterMax',
    JSON.stringify(b));

b = budget(MANAGER_GONE, {});
check('nothing answering says so as an assumption, and is not called a reading',
    b.ap === 6 && b.mp === 3 && b.ap_source === 'assumed' && b.mp_source === 'assumed',
    JSON.stringify(b));

b = budget(MANAGER_THROWS, SHEET);
check('a throwing accessor falls through instead of ending the turn',
    b.ap === 6 && b.ap_source === 'sheet', JSON.stringify(b));

b = budget(managerWithFighter({actionPoints: '4', movementPoints: '2'}), {});
check('a numeric string from the client is still a number here',
    b.ap === 4 && b.mp === 2 && b.ap_source === 'fighter', JSON.stringify(b));

// --- the candidate gate ---
// The block starts at the cost line, so the descriptor is supplied rather than re-derived:
// what is under test is the budget comparison, not the client's spell lookup.
function gate(spell, ctx) {
    const candidates = [];
    // Wrapped in a one-iteration loop because the extracted block uses `continue`, which is
    // only legal inside one -- exactly the candidate loop it was lifted out of.
    const fn = new Function('desc', 'spell', 'sid', 'ctx', 'candidates',
        'for (var _i = 0; _i < 1; _i++) {\n' + filterBlock + '\n}\nreturn candidates;');
    return fn({apCost: spell.cost}, spell, spell.id, ctx, candidates);
}

let out = gate({id: 1, cost: 4}, {ap: 2, ap_source: 'fighter'});
check('a spell that costs more than the turn has is not offered',
    out.length === 0, JSON.stringify(out));
out = gate({id: 1, cost: 4}, {ap: 4, ap_source: 'fighter'});
check('exactly affordable counts as affordable',
    out.length === 1 && out[0].cost === 4, JSON.stringify(out));
out = gate({id: 1, cost: 12}, {ap: 6, ap_source: 'assumed'});
check('an invented budget cannot veto a real spell',
    out.length === 1, JSON.stringify(out));
out = gate({id: 1, cost: null}, {ap: 1, ap_source: 'fighter'});
check('a spell with no published cost keeps the client\'s own legality gate',
    out.length === 1 && out[0].cost === 1, JSON.stringify(out));

check('the fallback is labelled as an assumption at the point of use',
    /ap = 6; apFrom = 'assumed';/.test(budgetBlock)
    && /mp = 3; mpFrom = 'assumed';/.test(budgetBlock), 'the constants lost their marker');

// The name guard: every property this ladder reads has to exist in the served bundle.
const reads = [];
budgetBlock.replace(/\b(?:fighterBag|stats)\.([A-Za-z_][A-Za-z0-9_]*)/g, (all, name) => {
    if (reads.indexOf(name) < 0) reads.push(name);
    return all;
});
if (bundle === null) {
    check('the served bundle is readable for the name guard', false, bundleFile);
} else {
    check('the ladder reads a recognisable set of fields', reads.length >= 6, reads.join(','));
    reads.forEach((name) => {
        check('"' + name + '" exists in the served bundle', bundle.indexOf(name) >= 0);
    });
}

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
