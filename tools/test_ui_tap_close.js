// ui.tap's close-first path, run rather than grepped.
//
// The tutorial's shop step ends on SHOP_LOADED, fired when the shop opens and its
// articles arrive. The live press on 2026-10-29 reported open_before
// ["market > shop"] and changed:false, so the step never saw an opening. The page
// now closes the named windows first; this suite proves the close happens before
// the press, that the result carries what was closed, and that a press without
// close_first still closes nothing.
const fs = require('fs');
const path = require('path');

let fail = 0;
function check(name, ok, detail) {
    const good = !!ok;
    if (good) console.log('  [PASS] ' + name);
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const src = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'mobile-disguise.js'), 'utf8');
const start = src.indexOf("'ui.tap': function(args) {");
check('the page declares ui.tap', start > 0, 'handler not found');
if (start < 0) process.exit(1);

let depth = 0, end = -1;
for (let i = src.indexOf('{', start); i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') { depth--; if (depth === 0) { end = i; break; } }
}
const fnSrc = src.slice(start + "'ui.tap': ".length, end + 1);

let built = null, buildErr = null;
try {
    built = new Function('window', 'document', '_agentResult', '_agentNum',
                         '_agentOpenWindows', '_agentUiTap', '_agentCloseWindow',
                         'return (' + fnSrc + ');');
} catch (e) { buildErr = e; }
check('the extracted handler is valid JavaScript', !!built, buildErr);
if (!built) { console.log('FAILURES: ' + fail); process.exit(1); }

const agentResult = (ok, data, error) => ({ ok, data, error });
const agentNum = (v) => (typeof v === 'number' ? v : null);

function makeWorld(open, closeable) {
    const events = [];
    const state = { open: open.slice() };
    const doc = {
        querySelectorAll: () => [{
            className: 'shopBtn',
            getBoundingClientRect: () => ({ width: 20, height: 20, left: 0, top: 0 }),
            dispatchEvent: () => true,
            click: () => true,
            isConnected: true
        }]
    };
    const openWindows = () => state.open.slice();
    const uiTap = () => {
        events.push('tap');
        // The client shows the shop again when the floating button is pressed.
        if (state.open.indexOf('market > shop') === -1) state.open.push('market > shop');
    };
    const closeWindow = (name) => {
        // The page's helper checks the window's own openState before closing it.
        const isOpen = state.open.some((row) => String(row).split(' > ')[0] === name);
        if (!isOpen || !closeable) return false;
        events.push('close:' + name);
        state.open = state.open.filter((row) => String(row).split(' > ')[0] !== name);
        return true;
    };
    const handler = built({}, doc, agentResult, agentNum, openWindows, uiTap, closeWindow);
    return { handler, events };
}

// A shop window left open from an earlier step: the press must close it first,
// then press, so the client sees a real opening. The planner sends one window
// name (the runtime contract types close_first as a string); the page also
// accepts a list, which the empty-window case below exercises.
const w = makeWorld(['market > shop'], true);
const r = w.handler({ selector: '.shopBtn', close_first: 'market' });
check('the named window is closed before the press',
    w.events.join(',') === 'close:market,tap', w.events.join(','));
check('the result says what was closed',
    r.ok && r.data.closed_before && r.data.closed_before.length === 1
    && r.data.closed_before[0] === 'market', r.data && r.data.closed_before);
check('open_before is read after the close, so the diff is the opening',
    r.data.open_before && r.data.open_before.length === 0
    && r.data.changed === true, JSON.stringify(r.data));
check('the opened window is the shop tab in the market window',
    r.data.open_windows && r.data.open_windows.join(',') === 'market > shop',
    r.data.open_windows);

// Nothing open: nothing to close, and the press still happens.
const empty = makeWorld([], true);
const r2 = empty.handler({ selector: '.shopBtn', close_first: ['market'] });
check('a closed window is not "closed" again',
    empty.events.join(',') === 'tap' && r2.data.closed_before.length === 0,
    [empty.events.join(','), r2.data.closed_before]);

// A press with no close_first must not close anything (the menu surfaces rely on
// the cycle to give the step its press back).
const plain = makeWorld(['market > shop'], true);
plain.handler({ selector: '.shopBtn' });
check('without close_first nothing is closed',
    plain.events.join(',') === 'tap', plain.events.join(','));
check('without close_first the result has no closed_before key',
    !('closed_before' in plain.handler({ selector: '.shopBtn' }).data));

console.log(fail ? 'FAILURES: ' + fail : 'ui.tap close-first suite: all checks passed');
process.exit(fail ? 1 : 0);
