// Behavioural checks for the quest ACCEPT branch in scripts/mobile-disguise.js (KNOWN_GAPS G33).
//
// Why this exists: in the recorded corpus 0 of 15 start-shaped `quest.advance` rows were
// ever sent inside a dialog, because the dialog block handled `close` and everything else
// as "answer reply 0" -- the QuestStartRequestMessage branch sat after it. A quest offer
// is a LINK in the NPC's own text (G32), so the only way the verb can accept one is to
// run before the reply path and require the id the dialog is offering right now.
//
// These tests run the real branch with stubs, so a regression that looks equivalent but
// sends a reply instead of the start packet, or accepts an id nobody offered, fails here.

const fs = require('fs');
const path = require('path');

let pass = 0, fail = 0;
function check(name, ok, detail) {
    if (ok) { console.log('  [PASS] ' + name); pass++; }
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const src = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'mobile-disguise.js'), 'utf8');
const schema = fs.readFileSync(path.join(__dirname, '..', 'dofus-network', 'agent', 'schema.py'), 'utf8');

function extractFrom(anchor, braceAt) {
    const start = src.indexOf(anchor);
    if (start < 0) return null;
    const open = src.indexOf('{', start + (braceAt || 0));
    let depth = 0;
    for (let i = open; i < src.length; i++) {
        if (src[i] === '{') depth++;
        else if (src[i] === '}') { depth--; if (depth === 0) return src.slice(start, i + 1); }
    }
    return null;
}

const ACCEPT_ANCHOR = 'if (args && args.accept === true) {';
const REFUSE_ANCHOR = 'var bareStartId = (args && (args.quest_id || args.questId)) || null;';
const acceptBlock = extractFrom(ACCEPT_ANCHOR);
const refuseBlock = extractFrom(REFUSE_ANCHOR);
check('the accept branch exists in quest.advance', !!acceptBlock, ACCEPT_ANCHOR);
check('the start-shaped refusal exists in quest.advance', !!refuseBlock, REFUSE_ANCHOR);
if (!acceptBlock || !refuseBlock) {
    console.log('\n' + pass + ' passed, ' + fail + ' failed');
    process.exit(1);
}

// ---- order: accept, then close, then the reply path --------------------------------
const atAccept = src.indexOf(ACCEPT_ANCHOR);
const atClose = src.indexOf('if (args && args.close) {');
const atRefuse = src.indexOf(REFUSE_ANCHOR);
const atReply = src.indexOf('var want = (args && args.reply !== undefined)', atRefuse);
check('the accept branch runs before the close branch', atAccept > 0 && atAccept < atClose,
    atAccept + ' vs ' + atClose);
check('and both run before the code that answers reply 0',
    atClose < atReply && atRefuse < atReply, atClose + '/' + atRefuse + ' vs ' + atReply);

// ---- run the accept branch against stubs ------------------------------------------
const makeAccept = new Function('args', '_agentDialogState', '_cmSend', '_agentResult',
    'window', acceptBlock);
const R = (ok, payload, error) => ({ ok, payload, error });
const OPEN = { open: true, start_quest: 3841 };
const SHUT = { open: false };

function acceptRun(args, opts) {
    opts = opts || {};
    const sent = [];
    const calls = { ui: 0, handler: 0 };
    const win = { gui: { npcDialogUi: { leaveDialog: function () {
        calls.ui++; if (opts.uiThrows) throw new Error('ui');
    } }, npcDialogHandler: { _closeDialog: function () {
        calls.handler++; if (opts.handlerThrows) throw new Error('handler');
    } } } };
    if (opts.pendingAt != null) win.__agentQuestStarts = {3841: {at: opts.pendingAt}};
    let n = 0;
    const states = opts.states || [OPEN, opts.staysOpen ? OPEN : SHUT];
    const result = makeAccept(args, () => states[Math.min(n++, states.length - 1)],
        (name, payload) => {
            sent.push({name, payload});
            if (opts.sendThrows) return {ok: false, error: 'send exploded'};
            return {ok: true};
        }, R, win);
    return {result, sent, calls, win};
}

let run = acceptRun({quest_id: 3841, accept: true});
check('an offered id is accepted with QuestStartRequestMessage',
    run.result.ok === true && run.sent.length === 1
    && run.sent[0].name === 'QuestStartRequestMessage'
    && run.sent[0].payload.questId === 3841, JSON.stringify(run.sent));
check('the accept branch is reached instead of a dialog reply',
    run.result.payload && run.result.payload.accepted_offer === true
    && run.result.payload.replied === undefined, JSON.stringify(run.result));
check('the dialog is closed through both client paths after the send',
    run.calls.ui === 1 && run.calls.handler === 1, JSON.stringify(run.calls));
check('and the close is judged by dialog state, not by the call',
    run.result.payload.closed === true, JSON.stringify(run.result.payload));

run = acceptRun({quest_id: 7, accept: true});
check('an id the dialog is not offering never reaches the wire',
    run.result.ok === false && run.sent.length === 0
    && run.result.payload.stage === 'quest_not_offered', JSON.stringify(run.result));
check('the refusal names both ids, so the stale one is visible',
    /not offering quest 7/.test(String(run.result.error))
    && /offers 3841/.test(String(run.result.error)), String(run.result.error));

run = acceptRun({quest_id: 3841, accept: true}, {states: [{open: true, start_quest: null}]});
check('a dialog offering nothing is refused, not accepted',
    run.result.ok === false && run.sent.length === 0
    && /offers none/.test(String(run.result.error)), String(run.result.error));

run = acceptRun({accept: true});
check('accept without a quest id is refused',
    run.result.ok === false && run.sent.length === 0
    && run.result.payload.stage === 'accept_no_quest_id', JSON.stringify(run.result));

run = acceptRun({quest_id: 3841, accept: true}, {pendingAt: Date.now()});
check('an accept already in flight is not sent again',
    run.result.ok === true && run.sent.length === 0
    && run.result.payload.pending === true, JSON.stringify(run.result));

run = acceptRun({quest_id: 3841, accept: true});
const again = acceptRun({quest_id: 3841, accept: true});
check('the first send arms the in-flight window', again.sent.length === 1,
    'fresh window must send');
const sameWindow = (function () {
    const sent = [];
    const win = {};
    let n = 0;
    const states = [OPEN, OPEN, OPEN, OPEN];
    const fn = makeAccept;
    const call = a => fn(a, () => states[Math.min(n++, states.length - 1)],
        (name, payload) => { sent.push(name); return {ok: true}; }, R, win);
    call({quest_id: 3841, accept: true});
    const second = call({quest_id: 3841, accept: true});
    return {sent, second};
})();
check('a second accept inside 5 s is the pending answer, not a second packet',
    sameWindow.sent.length === 1 && sameWindow.second.payload
    && sameWindow.second.payload.pending === true, JSON.stringify(sameWindow));

run = acceptRun({quest_id: 3841, accept: true}, {states: [OPEN, OPEN]});
check('a dialog that stays open does not turn a successful send into a failure',
    run.result.ok === true && run.result.payload.closed === false,
    JSON.stringify(run.result));
check('but the send failure is reported as a failure',
    acceptRun({quest_id: 3841, accept: true}, {sendThrows: true}).result.ok === false,
    'a failed send must be red');
check('each close path keeps its own try: a throwing view still closes via the handler',
    acceptRun({quest_id: 3841, accept: true}, {uiThrows: true}).calls.handler === 1,
    JSON.stringify(acceptRun({quest_id: 3841, accept: true}, {uiThrows: true}).calls));

// ---- the start-shaped refusal ------------------------------------------------------
const makeRefuse = new Function('args', '_agentDialogState', '_agentResult', 'window',
    '_agentQuestKnown', refuseBlock + '\nreturn "continued";');

function refuseRun(args, known) {
    return makeRefuse(args, () => OPEN, R, {}, () => known);
}
let out = refuseRun({quest_id: 9}, false);
check('a bare start inside a dialog is refused for a quest the client does not know',
    out !== 'continued' && out.ok === false
    && out.payload.stage === 'dialog_start_not_accept', JSON.stringify(out));
check('the refusal says the honest form and keeps the offer visible',
    /accept=true/.test(String(out.error)) && out.payload.offered === 3841, String(out.error));
check('a known quest keeps reply-0 behaviour (the tutorial relies on it)',
    refuseRun({quest_id: 3841}, true) === 'continued');
check('a reply call is not mistaken for a start',
    refuseRun({quest_id: 3841, reply: 1}, false) === 'continued');
check('a talk call is not mistaken for a start',
    refuseRun({quest_id: 3841, npc_id: 3888}, false) === 'continued');
check('the refusal block sends nothing',
    refuseBlock.indexOf('_cmSend(') === -1 && refuseBlock.indexOf('sendMessage(') === -1,
    'a send call appeared in the refusal');

// ---- source and contract pins ------------------------------------------------------
check('the page knows the client quest lists instead of guessing',
    src.indexOf('var _agentQuestKnown = function (questId) {') > 0
    && src.indexOf('isActiveQuestById(questId)') > 0
    && src.indexOf('isFinishedQuestById(questId)') > 0);
check('the offer the guard compares against is the client\'s parsed start_quest',
    acceptBlock.indexOf('acceptState.start_quest') > 0);
check('the schema declares the accept argument, so the validator lets the call through',
    /"accept": _p\("bool"/.test(schema));

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
