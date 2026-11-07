// The guest login was refused with the client's own popup
// `en[?ui.popup.accessDenied]` ("the Dofus access has been revoked"). The guard
// recorded it as a cooldown, the autopilot pressed three times, and then waited
// forever because the ban matcher only knew ban phrases — not the access-denied
// family. A denied account cannot be fixed by waiting, so it must read as a ban
// and the run must stop with "reset this slot for a fresh guest".
//
// This executes the real matcher from scripts/mobile-disguise.js against fake
// page text. "Server busy" must stay a cooldown: it is the one message where
// waiting and retrying is correct.

const fs = require('fs');
const path = require('path');

let pass = 0, fail = 0;
function check(name, ok, detail) {
    if (ok) { console.log('  [PASS] ' + name); pass++; }
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const pagePath = path.join(__dirname, '..', 'scripts', 'mobile-disguise.js');
const page = fs.readFileSync(pagePath, 'utf8');

function block(marker) {
    const at = page.indexOf(marker);
    if (at < 0) return null;
    const open = page.indexOf('{', at);
    let depth = 0;
    for (let i = open; i < page.length; i++) {
        if (page[i] === '{') depth++;
        else if (page[i] === '}') {
            depth--;
            if (depth === 0) return page.slice(at, i + 1);
        }
    }
    return null;
}

const atVar = page.match(/var _agentLoginBanAt\s*=\s*[^;]+;/);
const textVar = page.match(/var _agentLoginBanText\s*=\s*[^;]+;/);
const fn = block('var _agentLoginBan = function() {');
check('the ban matcher is present', !!fn && !!atVar && !!textVar);

let make = null;
if (fn && atVar && textVar) {
    const code = [atVar[0], textVar[0], fn].join('\n') + '\nreturn _agentLoginBan;';
    make = function (text, cooldown) {
        return new Function('document', '_loginCooldown', code)(
            {body: {innerText: text}}, function () { return cooldown || null; });
    };
}

function verdict(text, cooldown) {
    try { return make(text, cooldown)(); } catch (e) { return {error: String(e)}; }
}

if (make) {
    const denied = verdict('en[?ui.popup.accessDenied]');
    check('the access-denied popup reads as a ban, not a wait',
          denied.banned === true, denied);
    const revoked = verdict('Your Dofus access has been revoked. Contact support.');
    check('the revoked-access wording reads as a ban', revoked.banned === true, revoked);
    const banned = verdict('Your account has been permanently banned. Sorry!');
    check('an explicit ban still reads as a ban', banned.banned === true, banned);
    const french = verdict('Votre compte a été banni définitivement.');
    check('the French ban wording still reads as a ban', french.banned === true, french);
    const busy = verdict('Server busy. Please try again later.');
    check('server busy stays a cooldown, never a ban', busy.banned !== true, busy);
    const normal = verdict('Choose your character.');
    check('an ordinary screen is not a ban', normal.banned !== true, normal);
    const empty = verdict('');
    check('an empty page is not a ban', empty.banned !== true, empty);
    check('the reported text carries the matched phrase',
          typeof denied.text === 'string' && /accessdenied/i.test(denied.text), denied.text);
    // The popup is gone after a reload, but the cooldown it armed persists with
    // its reason. That record is the same denial and must stop the loop too.
    const persisted = verdict('Choose your character.',
        {reason: 'en[?ui.popup.accessDenied]', remaining_s: 200});
    check('a persisted access-denied cooldown reads as a ban after a reload',
          persisted.banned === true, persisted);
    const persistedBusy = verdict('Choose your character.',
        {reason: 'server busy', remaining_s: 200});
    check('a persisted busy cooldown is still only a cooldown',
          persistedBusy.banned !== true, persistedBusy);
}

const autoPath = path.join(__dirname, '..', 'dofus-network', 'agent', 'autopilot.py');
const auto = fs.readFileSync(autoPath, 'utf8');
check('the autopilot stops on a denied login instead of pressing',
      /reports a permanent ban/.test(auto) && /reset this slot for a fresh guest/.test(auto));

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
