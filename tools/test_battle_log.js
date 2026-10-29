// The battle log is the only place the numbers of a fight exist: who cast what, on
// whom, for how much. The reader used to look only at the client's own message stack,
// and the live evidence (fights 124/125, 2026-10-28) shows that stack is empty by the
// time the end message arrives -- both outcome rows recorded `battle_log: []`. The tap
// copies each fight message as it arrives, so the reader has to prefer the tap, fall
// back to the stack for old builds, and keep the same row shape either way.
const fs = require('fs');
const path = require('path');

let fail = 0;
function check(name, ok, detail) {
    const good = !!ok;
    if (good) console.log('  [PASS] ' + name);
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const src = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'mobile-disguise.js'), 'utf8');

function extract(signature) {
    const start = src.indexOf(signature);
    if (start < 0) return null;
    const open = src.indexOf('{', start);
    let depth = 0, end = -1;
    for (let i = open; i < src.length; i++) {
        if (src[i] === '{') depth++;
        else if (src[i] === '}') { depth--; if (depth === 0) { end = i; break; } }
    }
    return end < 0 ? null : src.slice(start, end + 1);
}

const rowSrc = extract('var _agentBattleRow = function(msg, type)');
const logSrc = extract('var _battleLog = function(limit)');
const harvestSrc = extract('var _agentHarvestStack = function()');
check('the row builder exists', !!rowSrc, 'var _agentBattleRow not found');
check('the reader exists', !!logSrc, 'var _battleLog not found');
check('the stack harvester exists', !!harvestSrc, 'var _agentHarvestStack not found');
if (!rowSrc || !logSrc || !harvestSrc) { console.log('FAILURES: ' + (fail || 1)); process.exit(1); }

// static: the tap is in the incoming-message wrapper and only for fight names
check('the incoming tap feeds the fight ring',
    /__agentBattleRing/.test(src) && /\/\^Game\(Action\)\?Fight\//.test(src),
    'no ring tap in the emit wrapper');
check('the tap is capped', /fightRing\.length > 800/.test(src), 'ring must not grow forever');
check('sequence batches are expanded into the ring',
    /\/sequence\/i\.test\(rowName\)/.test(src) && /a\.sequence \|\| a\.messages/.test(src),
    'action messages arrive inside messageSequence on this client');
check('the stack is polled while the page runs',
    /__agentBattleHarvestTimer = setInterval/.test(src),
    'nothing copies the stack between message arrivals');
check('the ring is scoped to one fight',
    /GameFightStartingMessage[\s\S]{0,700}window\.__agentBattleRing = \[\]/.test(src)
    && /GameFightStartingMessage[\s\S]{0,700}window\.__agentBattleSigs = \{\}/.test(src),
    'an uncleared ring re-reads the previous fight (fight 137 did)');

let win = {};
const build = new Function('window', 'return (function(){' + rowSrc + ';' + logSrc
    + ';' + harvestSrc
    + '; return {row: _agentBattleRow, log: _battleLog, harvest: _agentHarvestStack}; })();');
const api = build(win);

// --- row shape
const row = api.row({ _messageType: 'GameActionFightLifePointsLostMessage', actionId: 7,
                      sourceId: 11, targetId: -4, spellId: 123, cellId: 342, effectId: 96,
                      loss: 25, delta: -25, shieldLoss: 3, permanentDamages: 1, fightId: 124 },
                    undefined);
check('a message maps to a row with its numbers',
    row && row.type === 'GameActionFightLifePointsLostMessage' && row.loss === 25
    && row.delta === -25 && row.shield_loss === 3 && row.permanent_damages === 1
    && row.source === 11 && row.target === -4 && row.spell === 123 && row.fight_id === 124,
    JSON.stringify(row));
check('a message with no type is not a row', api.row({ loss: 1 }) === null,
    'an untyped message must be dropped');
check('a missing field stays null, not NaN',
    api.row({ _messageType: 'GameFightTurnStartMessage' }).loss === null,
    'undefined coerced into a number would poison the log');

// --- reader: tap first
const tapWin = {
    __agentBattleRing: [
        { type: 'GameActionFightSpellCastMessage', spell: 1, loss: null },
        { type: 'GameActionFightLifePointsLostMessage', loss: 12 }
    ],
    gui: { fightManager: { fightMessagesStack: [{ _messageType: 'Old', loss: 99 }] } }
};
const apiTap = build(tapWin);
const tapLog = apiTap.log(12);
check('the tap wins over the client stack',
    tapLog.length === 2 && tapLog[1].loss === 12, JSON.stringify(tapLog));

// --- reader: fall back to the stack for a build without the tap
const stackWin = {
    gui: { fightManager: { fightMessagesStack: [
        { _messageType: 'GameActionFightLifePointsLostMessage', value: 5 } ] } }
};
const stackLog = build(stackWin).log(12);
check('an empty tap falls back to the client stack',
    stackLog.length === 1 && stackLog[0].value === 5, JSON.stringify(stackLog));

// --- reader: the limit is honoured at the tail
const many = { __agentBattleRing: Array.from({ length: 900 }, (_, i) =>
    ({ type: 'GameActionFightLifePointsLostMessage', loss: i })) };
const capped = build(many).log(240);
check('the reader returns the newest rows up to the limit',
    capped.length === 240 && capped[239].loss === 899, capped.length + ':' +
    (capped[capped.length - 1] || {}).loss);

// --- harvester: action rows from the client stack, deduped across polls
const harvestWin = {
    gui: { fightManager: { fightMessagesStack: [
        { _messageType: 'GameFightTurnStartMessage', id: 1 },
        { _messageType: 'GameActionFightSpellCastMessage', actionId: 41,
          sourceId: 7, spellId: 19 },
        { _messageType: 'GameActionFightLifePointsLostMessage', actionId: 41,
          sourceId: 7, targetId: -4, loss: 33, delta: -33 }
    ] } },
    __agentBattleRing: []
};
const apiHarvest = build(harvestWin);
const first = apiHarvest.harvest();
check('the harvester copies the action rows',
    first === 2 && harvestWin.__agentBattleRing.length === 2,
    first + ':' + harvestWin.__agentBattleRing.length);
const second = apiHarvest.harvest();
check('a repoll does not double-count',
    second === 0 && harvestWin.__agentBattleRing.length === 2,
    second + ':' + harvestWin.__agentBattleRing.length);
check('lifecycle messages are not copied by the harvester',
    !harvestWin.__agentBattleRing.some(r => /TurnStart/.test(r.type)),
    'the tap already has them; the harvester exists for the numbers');
const harvestedLog = apiHarvest.log(240);
check('the reader sees harvested rows',
    harvestedLog.length === 2 && harvestedLog[1].loss === 33,
    JSON.stringify(harvestedLog));

console.log(fail ? 'FAILURES: ' + fail : 'battle log suite: all checks passed');
process.exit(fail ? 1 : 0);
