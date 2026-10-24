// Tests for the claim guard in scripts/mobile-disguise.js.
//
// The guard runs inside the injected page script, not in Python, so it is easy
// to change its reasons without noticing. These cases pin the refusals that
// were each learned from a live disconnect or a client-state bug. The kick
// memory is tested both ways: seeded from a host list and recorded live from a
// disconnect inside the attribution window.

const fs = require('fs');
const path = require('path');

const scriptPath = path.join(__dirname, '..', 'scripts', 'mobile-disguise.js');
const src = fs.readFileSync(scriptPath, 'utf8').replace(/\r\n/g, '\n');
const start = src.indexOf('var _KICK_STORE');
const endMarker = 'return { ok: true };\n        };';
const end = src.indexOf(endMarker, start);
if (start < 0 || end < 0) {
    console.error('cannot extract _questClaimCheck from ' + scriptPath);
    process.exit(2);
}
const body = src.slice(start, end + endMarker.length);
const makeGuard = new Function('window', 'console', body + '\nreturn _questClaimCheck;');

const STORE = 'dofus.agent.unsafeClaims.v1';

function makeQuest(overrides) {
    const quest = {
        objectives: [
            { objectiveId: 200, objectiveStatus: 2, curCompletion: 1, maxCompletion: 1, typeId: 1 },
            { objectiveId: 201, objectiveStatus: 1, curCompletion: 0, maxCompletion: 1, typeId: 1 }
        ],
        dbObjectives: { 200: { _type: 'QuestObjectiveTalkToNpc' } }
    };
    return Object.assign(quest, overrides || {});
}

function makeWindow(quest, extra) {
    const store = {};
    const window = {
        gui: quest === null ? {} : {
            playerData: { quests: { standardQuests: { getActiveQuestById: () => quest } } }
        },
        localStorage: {
            getItem: key => (Object.prototype.hasOwnProperty.call(store, key) ? store[key] : null),
            setItem: (key, value) => { store[key] = String(value); }
        }
    };
    const preset = extra && extra.__store;
    Object.assign(window, extra || {});
    window.__store = store;
    if (preset) {
        for (const key of Object.keys(preset)) store[key] = preset[key];
    }
    return window;
}

const cases = [
    ['ready objective is allowed', makeQuest(), {}, 200, 200, true, null],
    ['seeded kick signature refuses', makeQuest(),
        { __agentKnownKickSignatures: ['200/200'] }, 200, 200, false, 'known_kick_signature'],
    ['stored kick signature refuses', makeQuest(),
        { __store: { [STORE]: JSON.stringify({ '200/200': { at: 1, count: 1 } }) } },
        200, 200, false, 'known_kick_signature'],
    ['server-counted type refuses', makeQuest({ dbObjectives: { 200: { _type: 'QuestObjectiveFightMonster' } } }),
        {}, 200, 200, false, 'objective_server_counted'],
    ['discover-map numeric fallback refuses', makeQuest({ dbObjectives: { 200: { typeId: 4 } } }),
        {}, 200, 200, false, 'objective_server_counted'],
    ['not ready refuses', makeQuest({
        objectives: [{ objectiveId: 200, objectiveStatus: 1, curCompletion: 0, maxCompletion: 1 }]
    }), {}, 200, 200, false, 'objective_not_ready'],
    ['incomplete counter refuses', makeQuest({
        objectives: [{ objectiveId: 200, objectiveStatus: 2, curCompletion: 0, maxCompletion: 2 }]
    }), {}, 200, 200, false, 'objective_count_incomplete'],
    ['map settling refuses', makeQuest(), { __agentLastMapChange: Date.now() - 500 },
        200, 200, false, 'map_change_settling'],
    ['quest sync refuses', makeQuest(), { __agentLastQuestSync: Date.now() - 200 },
        200, 200, false, 'quest_state_syncing'],
    ['validation cooldown refuses', makeQuest(),
        { __agentLastValidate: { quest_id: 200, objective_id: 200, at: Date.now() - 100 } },
        200, 200, false, 'validation_cooldown'],
    ['unknown quest refuses', null, {}, 200, 200, false, 'objective_not_in_client_model'],
    ['not the current objective refuses', makeQuest(), {}, 200, 201, false, 'objective_not_current'],
    ['objective absent from step refuses', makeQuest(), {}, 200, 999, false, 'objective_not_in_current_step']
];

let failures = 0;
for (const [name, quest, extra, qid, oid, wantOk, wantReason] of cases) {
    const window = makeWindow(quest, extra);
    const guard = makeGuard(window, { log: () => {}, warn: () => {}, error: () => {} });
    let result;
    try {
        result = guard(qid, oid);
    } catch (err) {
        result = { ok: false, reason: 'threw: ' + err.message };
    }
    const ok = result.ok === wantOk && (wantReason === null || result.reason === wantReason);
    if (!ok) {
        failures++;
        console.error('FAIL ' + name + ': got ' + JSON.stringify(result)
            + ', wanted ok=' + wantOk + (wantReason ? ' reason=' + wantReason : ''));
    } else {
        console.log('ok   ' + name);
    }
}

// Live memory: a kick recorded 200 ms after a validation must blacklist exactly
// that pair; a kick five seconds later must not.
{
    const window = makeWindow(makeQuest(), { __agentLastValidate: { quest_id: 200, objective_id: 200, at: Date.now() - 200 } });
    const guard = makeGuard(window, { log: () => {}, warn: () => {}, error: () => {} });
    const recorded = window.__agentRecordKick ? window.__agentRecordKick('serverDisconnecting') : false;
    const store = JSON.parse(window.__store[STORE] || '{}');
    const refused = guard(200, 200);
    const ok = recorded === true && store['200/200'] && store['200/200'].count === 1
        && refused.ok === false && refused.reason === 'known_kick_signature';
    console.log((ok ? 'ok   ' : 'FAIL ') + 'live kick record blacklists the pair');
    if (!ok) failures++;
}
{
    const window = makeWindow(makeQuest(), { __agentLastValidate: { quest_id: 200, objective_id: 200, at: Date.now() - 5000 } });
    makeGuard(window, { log: () => {}, warn: () => {}, error: () => {} });
    const recorded = window.__agentRecordKick ? window.__agentRecordKick('serverDisconnecting') : false;
    const store = window.__store[STORE];
    const ok = recorded === false && !store;
    console.log((ok ? 'ok   ' : 'FAIL ') + 'stale disconnect does not blacklist');
    if (!ok) failures++;
}

if (failures) {
    console.error('\n' + failures + ' guard case(s) failed');
    process.exit(1);
}
console.log('\nall claim-guard cases passed');
