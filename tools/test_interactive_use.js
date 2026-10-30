// The page's interactive tap, run rather than grepped.
//
// Recorded live on 2026-10-29: the loop tapped element 513208 (map 145490432, cell
// 330) from cells 504 and 549 and no effect ever followed. The game counts an
// interactive used from its own cell; the client's own `isoEngine.useInteractive`
// orients and walks there first, while sending `InteractiveUseRequestMessage` from
// afar only produced a silent server-side refusal. The page now prefers the client's
// path. This suite executes the handler against a fake isoEngine so a regression to
// the direct message cannot pass unnoticed.
const fs = require('fs');
const path = require('path');

let fail = 0;
function check(name, ok, detail) {
    const good = !!ok;
    if (good) console.log('  [PASS] ' + name);
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const src = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'mobile-disguise.js'), 'utf8');
const marker = "'tap.interactive': function(args) {";
const start = src.indexOf(marker);
check('the page declares the tap.interactive handler', start > 0);
if (start < 0) { console.log('FAILURES: ' + fail); process.exit(1); }
let depth = 0, end = -1;
for (let i = src.indexOf('{', start); i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') { depth--; if (depth === 0) { end = i; break; } }
}
check('the handler body is balanced', end > start);
const fnSrc = src.slice(src.indexOf('function', start), end + 1);
const makeTap = new Function('_agentNum', '_agentResult', '_interactives', '_cmSend',
                             'window', 'return (' + fnSrc + ');');

function agentNum(value) {
    if (value == null || value === '') return null;
    const n = Number(value);
    return isFinite(n) ? n : null;
}
function agentResult(ok, seen, error) {
    return { ok: !!ok, seen: seen || null, error: error || null };
}

function harness(opts) {
    opts = opts || {};
    const sent = [];
    const used = [];
    const elements = opts.elements || [
        { elemId: 513208, skillInstanceUid: 900, enabled_skills: [901, 902],
          disabled_skills: [], cell: 330, name: 'page' }
    ];
    const iso = opts.iso === undefined ? {
        useInteractive: function (elem, skill) {
            used.push({ elem: elem, skill: skill });
            if (opts.throws) throw new Error('boom');
        },
        mapRenderer: { identifiedElements: opts.identified || { 513208: {} } },
        actorManager: { userActor: { cellId: opts.myCell === undefined ? 504 : opts.myCell } }
    } : opts.iso;
    const window = { isoEngine: iso };
    const tap = makeTap(agentNum, agentResult, function () { return elements; },
        function (type, payload) {
            sent.push({ type: type, payload: payload });
            return opts.sendResult || { ok: true };
        }, window);
    return { tap: tap, sent: sent, used: used };
}

// The happy path: the client's own use is called and no packet is hand-built.
let h = harness();
let r = h.tap({ element_id: 513208, skill_uid: 901 });
check('the tap is sent through the client, not around it',
    h.used.length === 1 && h.used[0].elem === 513208 && h.used[0].skill === 901,
    JSON.stringify(h.used));
check('no InteractiveUseRequestMessage is built when the client path exists',
    h.sent.length === 0, JSON.stringify(h.sent));
check('the result names the route and the character cell',
    r.ok === true && r.seen.via === 'isoEngine.useInteractive'
    && r.seen.my_cell === 504 && r.seen.cell === 330, JSON.stringify(r));

// Without the client helper the page must still send the message, and say so.
h = harness({ iso: null });
r = h.tap({ element_id: 513208, skill_uid: 901 });
check('without isoEngine the page falls back to the message',
    h.sent.length === 1
    && h.sent[0].payload.elemId === 513208 && h.sent[0].payload.skillInstanceUid === 901
    && r.seen.via === 'InteractiveUseRequestMessage', JSON.stringify(h.sent));

// A client call that throws is not a licence to lose the request either.
h = harness({ throws: true });
r = h.tap({ element_id: 513208, skill_uid: 901 });
check('a throwing client helper falls back to the message',
    h.used.length === 1 && h.sent.length === 1
    && r.seen.via === 'InteractiveUseRequestMessage', JSON.stringify(r.seen));

// The identified element must exist; otherwise the client path cannot be used.
h = harness({ identified: {} });
r = h.tap({ element_id: 513208, skill_uid: 901 });
check('an element missing from the renderer uses the message',
    h.used.length === 0 && h.sent.length === 1
    && r.seen.via === 'InteractiveUseRequestMessage', JSON.stringify(r.seen));

// Skill resolution: the argument wins, the element's own instance fills the gap,
// and an element with neither is refused before anything is dispatched.
h = harness();
h.tap({ element_id: 513208 });
check('the element\'s own skill instance is used when none is passed',
    h.used.length === 1 && h.used[0].skill === 900, JSON.stringify(h.used));
h = harness({ elements: [{ elemId: 513208, skillInstanceUid: null, enabled_skills: [],
                          disabled_skills: [], cell: 330, name: 'page' }] });
r = h.tap({ element_id: 513208 });
check('an element with no skill is refused, not dispatched',
    r.ok === false && r.seen.reason === 'skill_uid_required'
    && h.used.length === 0 && h.sent.length === 0, JSON.stringify(r));

// Selection by cell and the not-found report.
h = harness();
r = h.tap({ cell: 330, skill_uid: 901 });
check('an element can be selected by its cell',
    r.ok === true && h.used.length === 1 && h.used[0].elem === 513208, JSON.stringify(r));
h = harness();
r = h.tap({ element_id: 42 });
check('an unknown element reports what was visible',
    r.ok === false && r.seen.reason === 'element_not_found'
    && Array.isArray(r.seen.elements) && r.seen.elements.length === 1,
    JSON.stringify(r.seen));

// A message the server rejects is reported as a failure with its error.
h = harness({ iso: null, sendResult: { ok: false, error: 'too_far' } });
r = h.tap({ element_id: 513208, skill_uid: 901 });
check('a rejected message keeps its error',
    r.ok === false && r.error === 'too_far', JSON.stringify(r));

console.log('FAILURES: ' + fail);
process.exit(fail ? 1 : 0);
