// The page's own build stamp is the first thing a live run must read.
//
// A client that was not fully restarted keeps the previously injected script, so a fix
// can be present in this file and absent from the running page. The old stamp was a
// hard-coded `2026-10-25d` repeated in three places, which made a new page and a stale
// one report the same string. This pins one constant, one assignment, the two debug
// fallbacks that read it, and the observation that carries it.

const fs = require('fs');
const path = require('path');

let pass = 0, fail = 0;
function check(name, ok, detail) {
    if (ok) { console.log('  [PASS] ' + name); pass++; }
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const src = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'mobile-disguise.js'), 'utf8');

const constant = src.match(/var AGENT_BUILD = '([^']+)';/);
check('the page declares one build constant', !!constant, 'AGENT_BUILD not found');
check('the build constant is this generation',
    !!constant && constant[1] === '2026-10-27q', constant && constant[1]);

const assigns = src.match(/__agentBuild\s*=\s*[^;]+;/g) || [];
check('__agentBuild is assigned exactly once, from the constant',
    assigns.length === 1 && assigns[0].indexOf('AGENT_BUILD') >= 0, assigns);

check('no stale build literal survives', src.indexOf("'2026-10-25d'") === -1,
    'an old build string is still in the page');

const fallbacks = src.match(/window\.__agentBuild \|\| AGENT_BUILD/g) || [];
check('both debug fallbacks read the constant', fallbacks.length === 2, fallbacks.length);
check('the snapshot publishes the build for every observation',
    src.indexOf('snap.agent_build = window.__agentBuild || null;') > 0);

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
