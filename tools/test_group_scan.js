// Behavioural checks for the monster-group scan census (KNOWN_GAPS G7).
//
// Why this exists: `map.groups` came back `[]` in all 4,782 recorded live observations, and that
// empty list is what the planner was handed every time it wanted to join a fight ("no monster
// groups on this map"). An empty list has three causes with opposite meanings -- no actor
// collection at all, a collection whose actors never match the type filter, or a map that really
// has no group -- and the page reported the same thing for all three. The census added in
// `_listMonsterGroups` separates them. G25 is the warning here: a missing value was read as a
// missing client, and the client turned out to have it all along.
//
// The loop head is extracted from the real file and run against stub actor managers, so a
// histogram that only counts the matches, or that dies when the manager is absent, fails here.

const fs = require('fs');
const path = require('path');

let pass = 0, fail = 0;
function check(name, ok, detail) {
    if (ok) { console.log('  [PASS] ' + name); pass++; }
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const src = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'mobile-disguise.js'), 'utf8');
const HEAD_START = 'var _listMonsterGroups = function () {';
const HEAD_END = 'scan.matched += 1;';
const start = src.indexOf(HEAD_START);
const end = src.indexOf(HEAD_END, start);
check('the group lister still starts where the census is inserted', start >= 0, HEAD_START);
check('the census counts a matching group', end >= 0, HEAD_END);
if (start < 0 || end < 0) {
    console.log('\n' + pass + ' passed, ' + fail + ' failed');
    process.exit(1);
}

// The head, closed by hand: the rest of the function builds the group rows and is not this
// test. Two braces are added -- the loop's, then the function expression's.
const body = src.slice(start, end + HEAD_END.length)
    + '\n            }\n            return out;\n        };\n        _listMonsterGroups();';
function run(window) {
    const fn = new Function('window', body);
    fn(window);
    return window.__agentGroupScan;
}

let scan = run({});
check('no actor manager is recorded as that, not as an empty map',
    scan && scan.manager === false && scan.path === null && scan.seen === 0,
    JSON.stringify(scan));

scan = run({actorManager: {}});
check('a manager with neither collection says which names were tried',
    scan.path === 'none' && scan.seen === 0, JSON.stringify(scan));

scan = run({actorManager: {actorList: {1: {data: {type: 'GameRolePlayGroupMonsterInformations'}}}}});
check('a collection name that is not in the served bundle is not iterated',
    scan.path === 'none' && scan.seen === 0 && scan.matched === 0,
    JSON.stringify(scan));

const crowd = {};
for (let i = 0; i < 12; i++) {
    crowd['p' + i] = {data: {type: i % 2 ? 'GameRolePlayNpcInformation' : 'GameRolePlayFighterTokenInformations'}};
}
crowd['g1'] = {data: {type: 'GameRolePlayGroupMonsterInformations', contextualId: 7001}};
crowd['legacy'] = {_type: 'GameRolePlayGroupMonsterInformations'};
scan = run({actorManager: {actors: crowd}});
check('every actor is counted, matching or not, so a zero is a measured zero',
    scan.seen === 14 && scan.matched === 2, JSON.stringify(scan));
check('the type names are kept, because the name is the thing that would be wrong',
    scan.types.GameRolePlayNpcInformation === 6
    && scan.types.GameRolePlayFighterTokenInformations === 6, JSON.stringify(scan.types));
check('the _type fallback is counted too, not only data.type',
    scan.types.GameRolePlayGroupMonsterInformations === 2, JSON.stringify(scan.types));
check('the matching actors are the groups, so a non-empty list has a matching count',
    scan.matched === 2, JSON.stringify(scan));

// The full lister, not just the census head: a group row used to reference an
// undefined `si`, so the first real group threw and the observation returned
// `groups: []` on the exact tutorial map where the planner needed it.
const fullStart = src.indexOf('var _groupMembers = function (a) {');
const fullEnd = src.indexOf('var _agentTutorial = function', fullStart);
check('the full group lister can be extracted', fullStart >= 0 && fullEnd > fullStart);
if (fullStart >= 0 && fullEnd > fullStart) {
    const fullBody = src.slice(fullStart, fullEnd)
        + '\nreturn _listMonsterGroups();';
    const liveWindow = {actorManager: {actors: {
        g: {actorId: 7001, cellId: 321,
            data: {type: 'GameRolePlayGroupMonsterInformations',
                   scaleLevel: 2,
                   staticInfos: {mainCreatureLightInfos:
                       {creatureGenericId: 4192, level: 3, isBoss: true},
                       underlings: [{creatureGenericId: 4193, level: 2}]}}}
    }}};
    let rows = [];
    let thrown = null;
    try { rows = new Function('window', fullBody)(liveWindow); }
    catch (e) { thrown = e; }
    check('a live group produces a row instead of throwing',
        thrown == null && rows.length === 1 && rows[0].group_id === 7001,
        thrown ? String(thrown) : JSON.stringify(rows));
    check('the group row carries members and the static-key census',
        !thrown && rows[0].monster_ids.join(',') === '4192,4193'
        && rows[0].member_count === 2 && rows[0].max_level === 3
        && rows[0].is_boss === true && rows[0].static_keys.indexOf('mainCreatureLightInfos') >= 0,
        thrown ? String(thrown) : JSON.stringify(rows[0]));
}

const many = {};
for (let i = 0; i < 40; i++) many['k' + i] = {data: {type: 'Type' + i}};
scan = run({actorManager: {actors: many}});
const kinds = Object.keys(scan.types);
check('the histogram is bounded, so a pathological map cannot grow the payload',
    scan.seen === 40 && kinds.length <= 25, kinds.length + ' keys');
check('the overflow lands in one named bucket rather than being dropped',
    scan.types.other === 16, JSON.stringify(scan.types));

// The snapshot has to carry it, or the census exists only inside the page.
check('the map snapshot publishes the census',
    src.indexOf('groups_scan: window.__agentGroupScan || null') >= 0,
    'the recorded observation would still show only an empty list');
check('the group count and the census stay separate facts',
    /group_count: allGroups\.length,/.test(src), 'group_count was renamed or removed');

// --- the shared group-membership and group-search reads ---
// Three handlers used to answer "who is in this group" and "which group has monster N" for
// themselves: the map listing, `fight.join`, and the named-mob branch of `farm.start`. The two
// handlers that decide whether a fight is reachable matched members against `staticInfos.monsters`
// -- the shape of the monster-group *reference* database -- while the live client enumerates
// `[staticInfos.mainCreatureLightInfos].concat(staticInfos.underlings)` (nine sites each in the
// served bundle). A group could therefore be visible on the map and still unjoinable, and a focus
// order could be refused as "not on this map" for a mob that was standing there (KNOWN_GAPS G7).
// Both questions now have exactly one implementation, so this section pins the behaviour and that
// no handler keeps a private copy.
//
// Anchor note: scripts/mobile-disguise.js is CRLF, so every needle in this file is single-line --
// a two-line needle cannot match the bytes that were just read.
const HELPERS_START = 'var _groupMembers = function (a) {';
const HELPERS_END = 'var _listMonsterGroups';
const hStart = src.indexOf(HELPERS_START);
const hEnd = src.indexOf(HELPERS_END, hStart);
check('the shared membership helper exists', hStart >= 0, HELPERS_START);
check('the shared group search exists',
    src.indexOf('var _findMonsterGroup = function (wanted) {') >= 0, '_findMonsterGroup is gone');
if (hStart >= 0 && hEnd > hStart) {
    const helpersSrc = src.slice(hStart, hEnd);
    const load = (win) => new Function('window', helpersSrc
        + '\nreturn {members: _groupMembers, find: _findMonsterGroup};')(win);
    const api = load({});
    const groupMembers = api.members;
    const main = {creatureGenericId: 447, level: 5};
    const under = [{creatureGenericId: 448}, {creatureGenericId: 449}];

    let got = groupMembers({data: {staticInfos: {mainCreatureLightInfos: main,
                                                 underlings: under}}});
    check('a live group yields its leader and both underlings',
        got.length === 3 && got[0] === main && got[1] === under[0], JSON.stringify(got.length));
    got = groupMembers({data: {staticInfos: {underlings: under}}});
    check('underlings alone are still members', got.length === 2, JSON.stringify(got.length));
    got = groupMembers({data: {staticInfos: {mainCreatureLightInfos: main,
                                             underlings: under, monsters: [{x: 1}]}}});
    check('the live shape wins over the reference-database fallback',
        got.length === 3 && got[0] === main, JSON.stringify(got.length));
    got = groupMembers({data: {staticInfos: {monsters: [{creatureGenericId: 9}]}}});
    check('a build that only nests `monsters` is honoured as a fallback',
        got.length === 1 && got[0].creatureGenericId === 9, JSON.stringify(got));
    got = groupMembers({staticInfos: {mainCreatureLightInfos: main}});
    check('staticInfos on the actor itself is found too', got.length === 1,
        JSON.stringify(got.length));
    check('a missing actor yields no members instead of throwing', (() => {
        try {
            return groupMembers(null).length === 0 && groupMembers({}).length === 0;
        } catch (e) { return false; }
    })(), 'null actor must not end a map read');
    check('a non-array monsters field is ignored rather than iterated',
        groupMembers({data: {staticInfos: {monsters: {a: 1}}}}).length === 0, 'iterated junk');

    // The search runs on a stub actor manager -- the same object shape the live page hands it.
    const TYPE = 'GameRolePlayGroupMonsterInformations';
    const grp = (id, leader, minions, extra) => {
        const si = {};
        if (leader != null) si.mainCreatureLightInfos = {creatureGenericId: leader};
        if (minions && minions.length) {
            si.underlings = minions.map((m) => ({creatureGenericId: m}));
        }
        const a = {actorId: id, data: {type: TYPE, staticInfos: si}};
        return Object.assign(a, extra || {});
    };
    const MAP = {a: grp(100, 447, [448, 449]), b: grp(101, 900),
        npc: {actorId: 7, data: {type: 'GameContextNbThingInformation'}}};
    let r = load({actorManager: {actors: MAP}}).find(447);
    check('the leader is found in the group that holds it',
        r.group === MAP.a && r.key === 'a' && r.groups === 2, JSON.stringify(r.groups));
    r = load({actorManager: {actors: MAP}}).find(449);
    check('a named minion is found, not only the leader',
        r.group === MAP.a && r.key === 'a', 'farm "bouftou" targets a member, not always the boss');
    r = load({actorManager: {actors: MAP}}).find(null);
    check('no named monster takes the first group',
        r.group === MAP.a && r.key === 'a', JSON.stringify(r.key));
    r = load({actorManager: {actors: MAP}}).find(1234);
    check('an absent monster reports no group but every id on the map',
        r.group === null && r.groups === 2 && r.monsters.join(',') === '447,448,449,900',
        JSON.stringify(r.monsters));
    r = load({actorManager: {actors: MAP}}).find(447);
    check('non-group actors never count as groups', r.groups === 2, 'the NPC was counted');
    r = load({isoEngine: {actorManager: {actors: MAP}}}).find(900);
    check('the manager reached through isoEngine is searched',
        r.group === MAP.b && r.path === 'actors', JSON.stringify(r.path));
    r = load({isoEngine: {actorManager: {actorList: MAP}}}).find(900);
    check('a manager whose only collection is a name absent from the bundle says so',
        r.group === null && r.path === 'none' && r.manager === true, JSON.stringify(r.path));
    r = load({actorManager: {actors: MAP}}).find('447');
    check('a goal that arrives as a string still matches',
        r.group === MAP.a, 'the client compares with === and so could a string id break the focus');
    r = load({actorManager: {actors: {a: {actorId: 3, data: {type: TYPE,
        creatureGenericId: 310, level: 55}}}}}).find(310);
    check('the group-level creatureGenericId the client itself matches on is read',
        r.group !== null && r.monsters.join(',') === '310', JSON.stringify(r.monsters));
    r = load({}).find(447);
    check('a missing manager is a named cause, not an empty map',
        r.group === null && r.manager === false && r.path === 'none', JSON.stringify(r));
    r = load({actorManager: {actors: {a: grp(1, 447), b: grp(2, 447)}}}).find(1234);
    check('a repeated monster is listed once', r.monsters.length === 1,
        JSON.stringify(r.monsters));
    const MANY = {};
    for (let i = 0; i < 40; i++) MANY['g' + i] = grp(1000 + i, 2000 + i);
    r = load({actorManager: {actors: MANY}}).find(9999);
    check('the id list is bounded so a pathological map cannot grow the refusal',
        r.monsters.length === 24 && r.groups === 40, JSON.stringify(r.monsters.length));
    r = load({actorManager: {actors: {a: {actorId: 5, _type: TYPE,
        staticInfos: {mainCreatureLightInfos: {creatureGenericId: 310}}}}}}).find(310);
    check('the `_type` fallback actor is searchable', r.group !== null && r.groups === 1,
        JSON.stringify(r.groups));
    r = load({actorManager: {actors: {a: grp(1, null, [311])}}}).find(311);
    check('a leaderless group is not skipped', r.group !== null, 'a group with only minions lost');

    check('the map listing uses the shared membership read',
        src.indexOf('var rawMembers = _groupMembers(a);') >= 0, 'the lister enumerated its own way');
    check('the join handler uses the shared search',
        src.indexOf('var found = _findMonsterGroup(wantMonster);') >= 0,
        'fight.join still walks the actors itself');
    check('farm.start uses the shared search',
        src.indexOf('var hunt = _findMonsterGroup(wanted);') >= 0,
        'farm.start still walks the actors itself');
    check('no reader is left matching monsters alone',
        src.indexOf('gsi.monsters') < 0, 'a second membership read reappeared');
    check('no private group walk is left in the join handler',
        src.indexOf('seenMonsters') < 0, 'the old join loop came back');

    const bundleFile = path.resolve(__dirname, '..', 'desktop', 'client', 'www', 'build',
        'script.js');
    const bundle = fs.existsSync(bundleFile) ? fs.readFileSync(bundleFile, 'utf8') : null;
    check('the served bundle is readable for the name guard', bundle !== null, bundleFile);
    if (bundle) {
        ['staticInfos', 'mainCreatureLightInfos', 'underlings', 'monsters',
            'creatureGenericId', 'GameRolePlayGroupMonsterInformations', 'actorManager',
            'actors', 'getActorFromMonsterId', 'getActorsIDList'].forEach((name) => {
            check('"' + name + '" exists in the served bundle', bundle.indexOf(name) >= 0);
        });
        // The other half of the name guard: `actorList` was a fallback the page carried for
        // three readers with zero occurrences in the bundle. A name the client does not have is
        // not a safety net, it is a dead branch that hides the real verdict.
        check('"actorList" is absent from the served bundle', bundle.indexOf('actorList') < 0,
            'the page fallback was pointing at a name this build never exposes');
        check('no reader in the page still asks for `actorList`',
            src.split('\n').filter((l) => l.indexOf('actorList') >= 0
                && l.trim().indexOf('//') !== 0).length === 0,
            'a guessed collection name is back in code');
    }
}

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
