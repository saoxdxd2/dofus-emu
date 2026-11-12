// Behavioural checks for the fight.state fighter stats: the maxima the combat contract
// requires, the stat-key capture, and the self fallback fill.
//
// Why this exists: the combat brain refuses a board whose fighters have no maxima, and every
// one of the 156 live `fight.state` snapshots the loop recorded was refused
// (dofus-network/docs/KNOWN_GAPS.md G25). The cause was not a client that hides maxima -- it
// was this page reading the *characteristics* sheet's names (`maxLifePoints`,
// `actionPointsCurrent`, and two names that occur nowhere in the bundle: `actionPointsMax`,
// `movementPointsMax`) off the *fighter* stats bag, whose schema is `lifePoints`,
// `effectiveMaxLifePoints`, `actionPoints`, `maxActionPoints`, `movementPoints`,
// `maxMovementPoints`, `initiative`. That schema is one object shared by our own fighter and
// every monster, so the enemy side needed no discovery.
//
// The bundle assertion at the bottom is the check that makes the mistake hard to repeat: a
// field name read off a live object must exist in the bundle the app actually serves.
// These tests run the real blocks, extracted and injected with stubs, so a change that
// invents a field or overwrites a real client value fails.

const fs = require('fs');
const path = require('path');

let pass = 0, fail = 0;
function check(name, ok, detail) {
    if (ok) { console.log('  [PASS] ' + name); pass++; }
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const src = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'mobile-disguise.js'), 'utf8');
const bundleFile = path.resolve(__dirname, '..', 'desktop', 'client', 'www', 'build', 'script.js');
const bundle = fs.existsSync(bundleFile)
    ? fs.readFileSync(bundleFile, 'utf8') : null;

function between(startAnchor, endAnchor, includeEnd) {
    const s = src.indexOf(startAnchor);
    if (s < 0) return null;
    const e = src.indexOf(endAnchor, s);
    if (e < 0) return null;
    return src.slice(s, includeEnd ? e + endAnchor.length : e);
}

const KEYS_START = 'var sampleFightKeys = function (storeName, bag) {';
const KEYS_END = "state.fighter_keys = sampleFightKeys('__agentFightObjectKeys', fighter);";
const FILL_START = 'for (var fi = 0; fi < state.fighters.length; fi++) {';
const FILL_END = 'var spellList = {};';

const keysBlock = between(KEYS_START, KEYS_END, true);
const fillBlock = between(FILL_START, FILL_END, false);
check('the stat-key capture is present in fight.state', !!keysBlock, KEYS_START + ' not found');
check('the self maxima fill is present in fight.state', !!fillBlock, FILL_START + ' not found');
if (!keysBlock || !fillBlock) {
    console.log('\n' + pass + ' passed, ' + fail + ' failed');
    process.exit(1);
}

const num = (value) => {
    if (value === null || value === undefined || value === '') return null;
    const parsed = Number(value);
    return isFinite(parsed) ? parsed : null;
};

// --- the capture block ---
function runKeys(win, state, myId, id, stats, data, fighter) {
    const fn = new Function('window', 'state', 'myId', 'id', 'stats', 'data', 'fighter',
        keysBlock + '\nreturn state;');
    return fn(win, state, myId, id, stats, data, fighter);
}

const bagWindow = {__agentFightStatKeys: null};
let wide = {};
for (let n = 0; n < 80; n++) wide['k' + n] = n;
const st = {fight_id: 42};
const mine = runKeys(bagWindow, st, 7, 7, {lifePoints: 40, actionPointsCurrent: 3}).stat_keys;
check('the character stats are sampled', mine && mine.me
    && mine.me.indexOf('lifePoints') >= 0, JSON.stringify(mine));
const enemyFirst = runKeys(bagWindow, st, 7, -1, wide);
check('an enemy is sampled too, because the missing maxima are the enemy side',
    enemyFirst.stat_keys.enemy && enemyFirst.stat_keys.enemy.length > 0,
    JSON.stringify(enemyFirst.stat_keys));
check('the sample is capped, so the payload stays bounded',
    enemyFirst.stat_keys.enemy.length === 60, String(enemyFirst.stat_keys.enemy.length));
const second = runKeys(bagWindow, st, 7, -1, {changed: 1});
check('one fight samples once, so a long fight does not repeat the work',
    second.stat_keys.enemy === enemyFirst.stat_keys.enemy, 'the cache was bypassed');
const nextFight = runKeys(bagWindow, {fight_id: 43}, 7, -1, {brandNew: 1});
check('a new fight re-samples, because the answer is the point',
    nextFight.stat_keys.enemy.indexOf('brandNew') >= 0,
    JSON.stringify(nextFight.stat_keys.enemy));
check('the capture cannot throw the board away', (() => {
    try {
        runKeys({}, {fight_id: 1}, 1, 1, null, null, null);
        return true;
    } catch (e) { return false; }
})(), 'Object.keys of null must not end a fight read');

// The two bags that answer the still-open nulls: which names a live fighter object and its
// `data` actually carry. Without them the next live fight would still leave
// creature_generic_id and enemy spells as questions.
const bags = runKeys({__agentFightStatKeys: null, __agentFightDataKeys: null,
                      __agentFightObjectKeys: null}, {fight_id: 55}, 7, 7,
                     {lifePoints: 1}, {creatureGenericId: 34, spells: {}},
                     {data: {}, level: 5});
check('the fighter data bag is sampled, which is where the monster id has to live',
    bags.data_keys && bags.data_keys.me
    && bags.data_keys.me.indexOf('creatureGenericId') >= 0, JSON.stringify(bags.data_keys));
check('the fighter object itself is sampled, for the accessors that are not on data',
    bags.fighter_keys && bags.fighter_keys.me
    && bags.fighter_keys.me.indexOf('level') >= 0, JSON.stringify(bags.fighter_keys));
check('the three bags keep separate caches, so one never overwrites another',
    bags.stat_keys.me[0] === 'lifePoints' && bags.data_keys.me[0] === 'creatureGenericId'
    && bags.fighter_keys.me[0] === 'data', JSON.stringify([bags.stat_keys.me,
        bags.data_keys.me, bags.fighter_keys.me]));
const enemyBags = runKeys({__agentFightStatKeys: null, __agentFightDataKeys: null,
                           __agentFightObjectKeys: null}, {fight_id: 56}, 7, -9,
                          {}, {spellList: []}, {spells: {}});
check('an enemy is sampled in all three, because the enemy gap is the one open',
    enemyBags.data_keys.enemy.indexOf('spellList') >= 0
    && enemyBags.fighter_keys.enemy.indexOf('spells') >= 0,
    JSON.stringify([enemyBags.data_keys.enemy, enemyBags.fighter_keys.enemy]));

// --- the fill block ---
function runFill(state, pd) {
    const fn = new Function('state', 'pd', 'num', fillBlock + '\nreturn state;');
    return fn(state, pd, num);
}

const SHEET = {characters: {mainCharacter: {characteristics:
    {effectiveMaxLifePoints: 55, maxLifePoints: 60}}}};

function fighters(extra) {
    const self = Object.assign({id: 7, is_me: true, hp: 40, max_hp: null,
                                max_ap: null, max_mp: null}, extra || {});
    return [self, {id: -1, is_me: false, hp: 185, max_hp: null, max_ap: null, max_mp: null}];
}

let filled = runFill({fighters: fighters(), my_stats: {action_points: 6, movement_points: 3}},
                     SHEET);
check('the character max hp is read from the sheet, preferring the effective value',
    filled.fighters[0].max_hp === 55, String(filled.fighters[0].max_hp));
check('the character maxima come from the sheet stats this handler already built',
    filled.fighters[0].max_ap === 6 && filled.fighters[0].max_mp === 3,
    JSON.stringify(filled.fighters[0]));
check('an enemy is left alone -- its field name is not guessed',
    filled.fighters[1].max_hp === null && filled.fighters[1].max_ap === null,
    JSON.stringify(filled.fighters[1]));

filled = runFill({fighters: fighters({max_hp: 200}), my_stats: {action_points: 6}}, SHEET);
check('a value the client did send is never overwritten',
    filled.fighters[0].max_hp === 200, String(filled.fighters[0].max_hp));

filled = runFill({fighters: fighters(), my_stats: null}, SHEET);
check('without the action-point sheet the maxima stay unknown rather than invented',
    filled.fighters[0].max_ap === null, String(filled.fighters[0].max_ap));

filled = runFill({fighters: fighters(), my_stats: {action_points: 6}}, {});
check('a missing sheet is a null, not a crash',
    filled.fighters[0].max_hp === null && filled.fighters[0].max_ap === 6,
    JSON.stringify(filled.fighters[0]));

const fallback = runFill({fighters: fighters(), my_stats: {}},
                         {characters: {mainCharacter: {characteristics: {maxLifePoints: 60}}}});
check('maxLifePoints is still honoured when the effective value is absent',
    fallback.fighters[0].max_hp === 60, String(fallback.fighters[0].max_hp));

check('only the character is touched by the fill loop',
    /if \(!selfFighter \|\| !selfFighter\.is_me\) continue;/.test(fillBlock),
    'the fill must be scoped to is_me');
check('no enemy field name was invented',
    !/enemy.*maxLife|maxLife.*enemy/i.test(fillBlock), 'a guessed name is a stale fact');

// --- the fighter stats reads: this is where the contract was failing ---
const STATS_START = 'hp: num(stats.lifePoints),';
const STATS_END = 'level: num(fighter && fighter.level)';
const statsBlock = between(STATS_START, STATS_END, false);
check('the fighter stats reads are one contiguous block', !!statsBlock,
    STATS_START + ' .. ' + STATS_END + ' not found');
if (!statsBlock) {
    console.log('\n' + pass + ' passed, ' + fail + ' failed');
    process.exit(1);
}

function runStats(stats) {
    return new Function('stats', 'num', 'return {' + statsBlock + '};')(stats, num);
}

// The contract's own required list, read from the Python side, so a rename on either
// side of the bridge fails here rather than producing another corpus of refused boards.
const readiness = fs.readFileSync(path.join(__dirname, '..', 'dofus-network', 'tools',
    'brain_readiness.py'), 'utf8');
const requiredMatch = readiness.match(/^REQUIRED = \(([^)]*)\)/m);
check('the readiness contract publishes its required fields', !!requiredMatch,
    'REQUIRED not found in brain_readiness.py');
const required = requiredMatch
    ? requiredMatch[1].match(/"[^"]+"/g).map((s) => s.replace(/"/g, '')) : [];

// Shaped from the bundle's own stats literal, so it is the client's schema and not a wish.
const MONSTER = {lifePoints: 185, effectiveMaxLifePoints: 260, actionPoints: 6,
                 maxActionPoints: 6, movementPoints: 4, maxMovementPoints: 4,
                 initiative: 312, shieldPoints: 0, summoner: -1};
const read = runStats(MONSTER);
check('a monster reports its own maxima, so enemy_max_hp_missing is gone',
    read.max_hp === 260, String(read.max_hp));
check('current and max AP/MP are read, which the contract requires per fighter',
    read.ap === 6 && read.max_ap === 6 && read.mp === 4 && read.max_mp === 4,
    JSON.stringify(read));
check('initiative is read, because the board is otherwise unordered',
    read.initiative === 312, String(read.initiative));
check('every field the readiness contract calls REQUIRED is filled',
    required.length > 0 && required.every((k) => read[k] !== null
        && read[k] !== undefined), JSON.stringify(read));

// That the two sides agree on the spelling of the keys, not only on the values.
const emitted = [];
statsBlock.replace(/^\s*([a-z_]+):/gm, (all, key) => { emitted.push(key); return all; });
required.forEach((key) => {
    check('the producer emits "' + key + '", which the contract requires',
        emitted.indexOf(key) >= 0, 'emitted: ' + emitted.join(','));
});

const spent = runStats(Object.assign({}, MONSTER, {actionPoints: 0, movementPoints: 0,
                                                  initiative: 0}));
check('a spent turn reads as zero, not as unknown',
    spent.ap === 0 && spent.mp === 0 && spent.initiative === 0,
    JSON.stringify(spent));

const legacy = runStats({lifePoints: 40, maxLifePoints: 60, actionPointsCurrent: 3,
                         movementPointsCurrent: 2});
check('the characteristics names still work as fallbacks, so another build is not lost',
    legacy.max_hp === 60 && legacy.ap === 3 && legacy.mp === 2,
    JSON.stringify(legacy));

const empty = runStats({});
check('an empty stats bag yields nulls instead of a crash',
    empty.max_hp === null && empty.ap === null && empty.max_ap === null,
    JSON.stringify(empty));

// The check that would have failed on the day this was broken: `actionPointsMax` and
// `movementPointsMax` are not strings the client contains at all.
if (bundle === null) {
    check('the served bundle is readable for the field-name guard', false, bundleFile);
} else {
    const names = [];
    statsBlock.replace(/\bstats\.([A-Za-z0-9_]+)/g, (all, name) => {
        if (names.indexOf(name) < 0) names.push(name);
        return all;
    });
    check('the block reads a recognizable set of fields', names.length >= 8,
        names.join(','));
    names.forEach((name) => {
        check('"' + name + '" exists in the served bundle', bundle.indexOf(name) >= 0);
    });
    check('the guard has teeth: the two names this page used to read are absent',
        bundle.indexOf('actionPointsMax') < 0 && bundle.indexOf('movementPointsMax') < 0,
        'the bundle gained them, so this test needs re-reading');
}

// --- the "do not ask for a board that can only be refused" guard ---
const GUARD_START = 'var liveEnemy = false;';
const GUARD_END = 'if (!liveEnemy) return false;';
const guardBlock = between(GUARD_START, GUARD_END, true);
check('the no-live-enemy guard is present in _askBrain', !!guardBlock, GUARD_START + ' not found');
if (guardBlock) {
    const runGuard = (state) =>
        new Function('state', guardBlock + '\nreturn liveEnemy;')(state);
    // The shape recorded live 15 times: one enemy at 0 hp, marked not alive, plus the self.
    check('a board whose only enemy is dead is not sent to the brain',
        runGuard({fighters: [{id: -1, team: 'enemy', alive: false, hp: 0},
                             {id: 4434090, team: 'player', is_me: true, alive: true, hp: 125}]}) === false);
    check('a board with a standing enemy is asked',
        runGuard({fighters: [{id: -1, team: 'enemy', alive: true, hp: 200},
                             {id: -3, team: 'enemy', alive: true, hp: 140},
                             {id: 4425019, team: 'player', is_me: true, alive: true, hp: 60}]}) === true);
    // Allies are not targets: the brain's own enemy definition is "team other than player".
    check('a party board with only allies left standing is not asked either',
        runGuard({fighters: [{id: -2, team: 'player', alive: true, hp: 100},
                             {id: 4434090, team: 'player', is_me: true, alive: true, hp: 125}]}) === false);
    check('an enemy that never set alive is still live if it has hp',
        runGuard({fighters: [{id: -1, team: 'enemy', hp: 40}]}) === true);
    check('a board with no fighters key is refused quietly, not a crash',
        runGuard({}) === false, 'forEach over an undefined list');
}

// --- last_actions: the enemy's casts, which is where a monster's spells come from
// `last_actions` was [] on all 333 recorded boards while `battle_log` held up to 52 rows
// from the same fight, because this block read `fm.fightMessagesStack` directly and the
// client clears that stack. The block must prefer the tap ring, like `_battleLog` does.
const ACTIONS_START = 'var lastActions = [];';
const ACTIONS_END = 'state.last_actions = lastActions;';
const actionsBlock = between(ACTIONS_START, ACTIONS_END, true);
check('the last_actions assembly is present in fight.state', !!actionsBlock,
    ACTIONS_START + ' not found');
if (actionsBlock) {
    const runActions = (win, fm) => new Function(
        'window', 'fm', 'text', 'num', '_agentHarvestStack',
        'var state = {};\n' + actionsBlock + '\nreturn state.last_actions;')(
            win, fm, (v) => (v == null ? '' : String(v)), num, () => 0);

    // The shape the tap produces: `spell`/`source`/`target`, not `spell_id`/`source_id`.
    const ringRows = runActions({
        __agentBattleRing: [
            {type: 'GameActionFightSpellCastMessage', at_action: 41, source: -1,
             target: 4434090, spell: 6908, spell_level: 5, destination_cell: 322,
             critical: 0, value: null, delta: null, effect: null, cell: null,
             loss: null, permanent_damages: null, shield_loss: null},
            {type: 'GameActionFightLifePointsLostMessage', at_action: 41, source: -1,
             target: 4434090, spell: 6908, cell: null, effect: 96, loss: 33,
             delta: -33, permanent_damages: 0, shield_loss: 0}
        ]
    }, {fightMessagesStack: []});
    check('the tap ring reaches last_actions, not just battle_log',
        Array.isArray(ringRows) && ringRows.length === 2, JSON.stringify(ringRows));
    check('a ring row keeps the names live_data and battle_solver read',
        ringRows && ringRows[0].spell_id === 6908 && ringRows[0].source_id === -1
        && ringRows[0].target_id === 4434090 && ringRows[0].spell_level === 5
        && ringRows[0].action_id === 41 && ringRows[0].destination_cell === 322,
        JSON.stringify(ringRows && ringRows[0]));
    check('the damage numbers survive the remap',
        ringRows && ringRows[1].loss === 33 && ringRows[1].delta === -33
        && ringRows[1].effect_id === 96, JSON.stringify(ringRows && ringRows[1]));

    // A build with no tap still reads the client stack.
    const stackRows = runActions({}, {fightMessagesStack: [
        {_messageType: 'GameActionFightSpellCastMessage', actionId: 9, sourceId: -1,
         spellId: 6942, targetId: 7}
    ]});
    check('an empty ring still falls back to the client stack',
        stackRows && stackRows.length === 1 && stackRows[0].spell_id === 6942,
        JSON.stringify(stackRows));

    // The ring wins when both exist, because the stack may already be cleared.
    const both = runActions({__agentBattleRing: [{type: 'X', spell: 1}]},
                            {fightMessagesStack: [{_messageType: 'Y', spellId: 2}]});
    check('the ring wins over a stale stack',
        both && both.length === 1 && both[0].spell_id === 1, JSON.stringify(both));
}

// --- the spell facts block: what `castable` means when the client cannot answer ---
// `castable: false` removes a spell from every candidate set battle_solver builds, so
// the AI never considers it. The catch around `canCastThisSpell` used to swallow the
// exception into that same `false`, which made "the client refused" and "the call
// threw" the same observation. The recorded corpus cannot show the difference either
// way -- `castable` is absent from all 1,341 spell rows across the 333 boards, because
// the field postdates them -- so the distinction has to be pinned by running the block.
const SPELL_START = 'var prop = function(spell, name, fallback) {';
const SPELL_END = 'var lastActions = [];';
const spellBlock = between(SPELL_START, SPELL_END, false);
check('the spell-facts block is present in fight.state', !!spellBlock,
    SPELL_START + ' .. ' + SPELL_END + ' not found');

if (spellBlock) {
    const numStub = (v) => {
        if (v === null || v === undefined || v === '') return null;
        const n = Number(v);
        return isFinite(n) ? n : null;
    };
    const spellStub = (id) => ({
        id: id, level: 5,
        spellLevel: {id: 900 + id, effects: [], criticalEffect: []},
        getProperty: () => null,
        getSpellLevelId: () => 900 + id,
        getName: () => 'Spell' + id,
        isWeapon: () => false
    });
    const runSpells = (fm, myId, list) => {
        const fn = new Function('spellList', 'num', 'text', 'effects', 'fm', 'myId', 'state',
            spellBlock + '\nreturn state;');
        return fn(list, numStub, (v) => (v == null ? null : String(v)), () => [],
                  fm, myId, {spells: []});
    };
    const list = {11: spellStub(11), 12: spellStub(12)};

    const yes = runSpells({canCastThisSpell: () => true}, 7, list).spells;
    check('a spell the client allows is castable',
        yes.length === 2 && yes[0].castable === true && yes[0].castable_error === null,
        JSON.stringify(yes[0]));

    const no = runSpells({canCastThisSpell: () => false}, 7, list).spells;
    check('a spell the client refuses is not castable, and carries no error',
        no[0].castable === false && no[0].castable_error === null,
        JSON.stringify(no[0]));

    const threw = runSpells({canCastThisSpell: () => { throw new Error('no fighter'); }},
                            7, list).spells;
    check('a castability check that threw is unknown, not refused',
        threw[0].castable === null, JSON.stringify(threw[0]));
    check('the reason it could not be answered is recorded',
        typeof threw[0].castable_error === 'string'
        && threw[0].castable_error.indexOf('no fighter') >= 0,
        JSON.stringify(threw[0]));
    check('one unanswerable spell does not cost the rest of the spellbook',
        threw.length === 2 && threw[1].id === 12 && threw[1].name === 'Spell12',
        JSON.stringify(threw));

    let asked = 0;
    const noId = runSpells({canCastThisSpell: () => { asked++; return true; }}, null, list).spells;
    check('with no fighter id the client is not asked at all',
        asked === 0 && noId[0].castable === false, JSON.stringify([asked, noId[0]]));
}

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
