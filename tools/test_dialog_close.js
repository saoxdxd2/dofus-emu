// Behavioural checks for the quest.advance close branch in scripts/mobile-disguise.js.
//
// Why this exists: the live baseline (dofus-network/docs/BASELINE.md) counted 148
// failures reading "dialog close failed: TypeError: Cannot read properties of null
// (reading 'id')" - one shared try meant a single throwing client path lost the
// other, and a `sendMessage('LeaveDialogRequestMessage', null)` guess was the most
// likely source of the null deref. The fix closes through the client's own paths and
// judges the result by whether the dialog actually closed. These tests run the real
// branch, extracted and injected with stubs, so a regression that only *looks*
// equivalent still fails here.

const fs = require('fs');
const path = require('path');

let pass = 0, fail = 0;
function check(name, ok, detail) {
    if (ok) { console.log('  [PASS] ' + name); pass++; }
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const src = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'mobile-disguise.js'), 'utf8');

// --- extract the close branch, brace-matched ---
const anchor = "if (args && args.close) {";
const start = src.indexOf(anchor);
check('close branch present in quest.advance', start > 0, 'anchor not found');
if (start < 0) { console.log('\n' + pass + ' passed, ' + fail + ' failed'); process.exit(1); }
let depth = 0, i = start + anchor.length - 1, end = -1;
for (; i < src.length; i++) {
    const c = src[i];
    if (c === '{') depth++;
    else if (c === '}') { depth--; if (depth === 0) { end = i + 1; break; } }
}
const block = src.slice(start, end);
check('close branch extracted cleanly', end > start, 'unbalanced braces');
check('no guessed LeaveDialogRequestMessage packet',
    !/sendMessage\(\s*['"]LeaveDialogRequestMessage['"]\s*,\s*null/.test(block),
    'a null-payload dialog packet risks the session');
check('each client close path has its own try',
    (block.match(/try\s*\{/g) || []).length >= 2,
    'one shared try loses the second path when the first throws');
check('verifies the outcome instead of trusting the call',
    /_agentDialogState\(\)/.test(block) && /open === false/.test(block),
    'must re-read dialog state after closing');

// --- run it against stubs ---
const makeFn = new Function('args', '_agentDialogState', '_agentResult', 'window', block);
const R = (ok, payload, error) => ({ ok, payload, error });

function run(ui, handler, states) {
    let n = 0;
    const win = { gui: {} };
    if (ui) win.gui.npcDialogUi = { leaveDialog: ui };
    if (handler) win.gui.npcDialogHandler = { _closeDialog: handler };
    return makeFn({ close: true }, () => states[Math.min(n++, states.length - 1)], R, win);
}
const OPEN = { open: true, reply_count: 0 };
const SHUT = { open: false };
const THROW = () => { throw new TypeError("Cannot read properties of null (reading 'id')"); };

let r = run(() => {}, () => {}, [OPEN, SHUT]);
check('clean close reports success', r.ok === true, JSON.stringify(r));

r = run(THROW, () => {}, [OPEN, SHUT]);
check('UI path throws, handler closes -> still success', r.ok === true, JSON.stringify(r));
check('  the throwing path is recorded, not swallowed',
    r.payload && /npcDialogUi\.leaveDialog:/.test(String(r.payload.attempts)), JSON.stringify(r.payload));

r = run(() => {}, null, [OPEN, OPEN]);
check('dialog still open after close -> failure', r.ok === false, JSON.stringify(r));
check('  failure names the dialog, not a bare null',
    /still open/i.test(String(r.error)), String(r.error));

r = run(null, null, [OPEN, OPEN]);
check('no close path available -> failure', r.ok === false && /no dialog close path/.test(String(r.error)),
    String(r.error));

r = run(THROW, THROW, [OPEN, OPEN]);
check('both paths throw and dialog stays open -> failure with both attempts',
    r.ok === false && /leaveDialog/.test(String(r.error)) && /_closeDialog/.test(String(r.error)),
    String(r.error));

r = run(() => {}, () => {}, [SHUT, SHUT]);
check('nothing was open is success, not an error', r.ok === true, JSON.stringify(r));

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
