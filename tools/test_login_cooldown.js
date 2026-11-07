// The page's guest-login backoff escalates 10 min -> 60 min -> 3 h per refused
// press. Its failure list lived for 3 h regardless of the wait served, so the
// first retry after a served hour became a 3-hour lockout (live 2026-10-29:
// "Server busy" was still there after every wait). Refusals now decay once the
// longest single backoff has been served, so a slot can recover.
//
// This executes the real _loginCooldown/_noteLoginFail from scripts/mobile-disguise.js
// against a fake clock and fake localStorage.

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

const start = page.indexOf('var _LOGIN_BUSY_STORE');
const fnAt = page.indexOf('var _loginCooldown = function() {');
const fn = fnAt < 0 ? null : block('var _loginCooldown = function() {');
check('the cooldown engine is present', start >= 0 && !!fn);
check('refusals decay after the served backoff',
      /_LOGIN_FAIL_DECAY_MS = 60 \* 60 \* 1000/.test(page)
      && /now - t >= _LOGIN_FAIL_DECAY_MS/.test(page));

let make = null;
if (start >= 0 && fn) {
    const code = page.slice(start, fnAt) + '\n' + fn + ';\n'
        + 'return {cooldown: _loginCooldown, note: _noteLoginFail,'
        + '        fails: function () { return _loginFails.slice(); }};';
    make = function (now, fails, busy) {
        const store = new Map();
        if (fails) store.set('dofus.agent.loginFails.v1', JSON.stringify(fails));
        if (busy) store.set('dofus.agent.loginBusy.v1', JSON.stringify(busy));
        const storage = {
            getItem: function (k) { return store.has(k) ? store.get(k) : null; },
            setItem: function (k, v) { store.set(k, String(v)); },
            removeItem: function (k) { store.delete(k); },
        };
        const win = {localStorage: storage, sessionStorage: storage,
                     alert: function () {}};
        const FakeDate = function () {};
        FakeDate.now = function () { return now; };
        const api = new Function('window', 'document', 'Date', 'setInterval', code)(
            win, {body: {innerText: ''}}, FakeDate, function () {});
        api.store = store;
        return api;
    };
}

if (make) {
    const MIN = 60 * 1000, HOUR = 60 * MIN;
    const now = 1793000000000;

    const clean = make(now, []);
    check('no refusal history is no cooldown', clean.cooldown() === null);

    const first = make(now, [now]);
    check('a single fresh refusal quiets the slot for ten minutes',
          first.cooldown().remaining_s === 600 && first.cooldown().fails === 1,
          first.cooldown());

    const second = make(now, [now, now - MIN]);
    check('a second refusal inside the burst escalates to an hour',
          second.cooldown().remaining_s === 3600 && second.cooldown().fails === 2,
          second.cooldown());

    // The trap: 61 minutes after the last refusal the hour has been served, but
    // the two records survived and the next refusal would jump to three hours.
    const served = make(now, [now - 61 * MIN, now - 62 * MIN]);
    check('refusals from a served burst no longer hold the slot',
          served.cooldown() === null, served.cooldown());
    check('a decayed burst is removed from storage, not just ignored',
          served.fails().length === 0
          && served.store.get('dofus.agent.loginFails.v1') === '[]',
          served.store.get('dofus.agent.loginFails.v1'));
    served.note('Server busy');
    check('the first refusal after a decayed burst restarts at ten minutes',
          served.cooldown().remaining_s === 600 && served.cooldown().fails === 1,
          served.cooldown());

    const stale = make(now, [now - 2 * HOUR]);
    stale.note('Server busy');
    check('a two-hour-old refusal cannot stack a fresh one into three hours',
          stale.cooldown().remaining_s === 600 && stale.cooldown().fails === 1,
          stale.cooldown());

    const eleven = make(now, [now - 11 * MIN]);
    check('an eleven-minute-old single refusal has already been served',
          eleven.cooldown() === null, eleven.cooldown());

    const busy = make(now, [], {at: now - 1000, text: 'Server busy'});
    const busyCool = busy.cooldown();
    check('a busy popup still arms a cooldown with its own reason',
          busyCool && /server busy/i.test(busyCool.reason)
          && busyCool.remaining_s > 0, busyCool);
    const busyServed = make(now, [], {at: now - 11 * MIN, text: 'Server busy'});
    check('a served busy popup releases the slot and its guest budget',
          busyServed.cooldown() === null
          && busyServed.store.get('dfGuestTries') === '0'
          && busyServed.store.get('dofus.agent.loginBusy.v1') === undefined,
          [busyServed.cooldown(), busyServed.store.get('dfGuestTries')]);
}

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
