// Checks for the manual-intervention instrument in scripts/mobile-disguise.js.
//
// The goal is measured partly as "zero manual interventions" and the live baseline
// could not answer it because nothing distinguished a human taking over from the
// agent acting. The client can: the agent's own clicks are synthesised with
// dispatchEvent (isTrusted false), so trusted input during a run is human.
//
// These tests run the real handler, extracted and fed fake events. The point of
// extracting rather than grepping is that a handler which merely *looks* like it
// counts trusted events still fails if it also counts the agent's own, or if it
// interferes with the click it is only supposed to observe.

const fs = require('fs');
const path = require('path');

let pass = 0, fail = 0;
function check(name, ok, detail) {
    if (ok) { console.log('  [PASS] ' + name); pass++; }
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const src = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'mobile-disguise.js'), 'utf8');

const blockStart = src.indexOf('if (!self._userInputInstalled');
check('instrument installed in bindNetworkHooks', blockStart > 0, 'anchor missing');
if (blockStart < 0) { console.log('\n' + pass + ' passed, ' + fail + ' failed'); process.exit(1); }

const noteStart = src.indexOf('var noteUserInput = function (ev) {', blockStart);
let depth = 0, i = src.indexOf('{', noteStart), noteEnd = -1;
for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') { depth--; if (depth === 0) { noteEnd = i + 1; break; } }
}
const handlerSrc = src.slice(src.indexOf('function (ev) {', noteStart), noteEnd);
check('handler extracted', noteEnd > noteStart, 'unbalanced braces');

// structural guarantees
const installBlock = src.slice(blockStart, src.indexOf('// Record why the game connection ends', blockStart));
check('listeners are passive', /passive:\s*true/.test(installBlock), 'must not alter input handling');
check('listeners are capture-phase and on document', /document\.addEventListener\(/.test(installBlock));
check('never preventDefault', !/preventDefault/.test(installBlock), 'an observer must not cancel input');
check('never stopPropagation', !/stopPropagation/.test(installBlock), 'must not break the game handlers');
check('installed once only', /_userInputInstalled/.test(installBlock), 'rebind would double-count');
check('exposed in state.snapshot', /snap\.user_input\s*=/.test(src) && /__agentUserInput/.test(src));
check('counts only while the loop runs', /self\.aiRunning/.test(installBlock));

// behavioural: run the real handler with a fake ui + self
function harness(aiRunning) {
    const ui = { trusted: 0, during_run: 0, by_type: {}, last_ts: 0 };
    const self = { aiRunning: aiRunning };
    const note = new Function('ui', 'self', 'Date', 'Object',
        'return ' + handlerSrc)(ui, self, Date, Object);
    return { ui, note };
}

let h = harness(true);
h.note({ isTrusted: false, type: 'mousedown' });          // the agent's own click
check('synthetic agent input is not counted', h.ui.trusted === 0, JSON.stringify(h.ui));
h.note({ isTrusted: true, type: 'mousedown' });           // a human
check('trusted input is counted', h.ui.trusted === 1 && h.ui.during_run === 1, JSON.stringify(h.ui));
check('event type is recorded', h.ui.by_type.mousedown === 1, JSON.stringify(h.ui.by_type));
h.note({ isTrusted: true, type: 'keydown' });
h.note({ isTrusted: true, type: 'keydown' });
check('per-type tallies accumulate', h.ui.by_type.keydown === 2 && h.ui.trusted === 3, JSON.stringify(h.ui));
check('last_ts stamped', typeof h.ui.last_ts === 'number' && h.ui.last_ts > 0, String(h.ui.last_ts));

h = harness(false);                                        // paused / human driving alone
h.note({ isTrusted: true, type: 'mousedown' });
check('input while the loop is stopped is NOT an intervention',
    h.ui.trusted === 1 && h.ui.during_run === 0, JSON.stringify(h.ui));

h = harness(true);
h.note(null);
h.note({});
check('events with no trusted flag are ignored, not thrown on',
    h.ui.trusted === 0, JSON.stringify(h.ui));
h.note({ isTrusted: true, type: undefined });
check('a trusted event with an odd type is still counted once and does not throw',
    h.ui.trusted === 1 && h.ui.by_type['undefined'] === 1, JSON.stringify(h.ui));

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
