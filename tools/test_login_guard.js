// The "Server busy" loop, measured on dofus-03 (2026-10-28): the host seeder presses
// playWithGuest 800ms after every document, before the page guard has wrapped the login
// screen. Each press re-armed the lockout, and the host's 60s no-progress reload started
// the cycle again -- 586s left, then a fresh 596s. These checks run the guard block itself:
// a second alert must not extend the window, the host's retry budget must stay spent, the
// reload loop must be blocked, and an expired window must restore the budget.
const fs = require('fs');
const path = require('path');

let fail = 0;
function check(name, ok, detail) {
    const good = !!ok;
    if (good) console.log('  [PASS] ' + name);
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const src = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'mobile-disguise.js'), 'utf8');

check('the 150ms wrap poll is still the race window',
    /_guardTimer = setInterval\(function\(\) \{\s*\n\s*try \{ if \(_guardLoginScreen\(\)\) clearInterval\(_guardTimer\); \} catch \(e\) \{\}\s*\n\s*\}, 150\);/.test(src),
    'the wrap must install before the host seeder presses at 800ms');
check('the host reload is guarded',
    /blocked reload with/.test(src) && /__agentReloadForced/.test(src),
    'a reload at the login screen is another attempt');
check('a second alert does not extend the cooldown',
    /not extending the window/.test(src), 'each extension makes the lockout permanent');
check('the refusal keeps the host budget spent',
    /setItem\('dfGuestTries', '5'\)/.test(src), 'resetting to 0 re-arms the lockout');
check('only one initial login press is allowed at a time',
    /_singleFlight\[name\] && now - _lastLoginPress < 20000/.test(src),
    'the host seeder and an auto-started AI can press inside one round trip');

const start = src.indexOf("var _LOGIN_BUSY_STORE");
const stop = src.indexOf('var _guardTimer');
check('the guard block was located', start > 0 && stop > start, start + ':' + stop);
if (start < 0 || stop <= start) { console.log('FAILURES: ' + (fail || 1)); process.exit(1); }
const block = src.slice(start, stop);

function storage(initial) {
    const map = Object.assign({}, initial || {});
    return {
        getItem: k => (Object.prototype.hasOwnProperty.call(map, k) ? map[k] : null),
        setItem: (k, v) => { map[k] = String(v); },
        removeItem: k => { delete map[k]; },
        _map: map
    };
}

function build(win, doc) {
    const FakeObserver = function () { this.observe = function () {}; };
    const fakeInterval = function () { return 0; };
    const fakeTimeout = function (fn) { fn(); return 0; };
    return new Function('window', 'document', 'MutationObserver', 'setInterval',
                        'setTimeout', block
        + '; return {note: _noteLoginBusy, cool: _loginCooldown, guard: _guardLoginScreen,'
        + ' scan: _popupScan, noteFail: _noteLoginFail, clearFails: _clearLoginFails};')(
        win, doc || { body: { innerText: '' }, documentElement: {} },
        FakeObserver, fakeInterval, fakeTimeout);
}

// --- first alert arms the window
let reloads = 0;
let alerts = 0;
const win = {
    localStorage: storage(),
    sessionStorage: storage(),
    alert: function () { alerts++; },
    location: { reload: function () { reloads++; } }
};
const api = build(win);
const armed = api.note('Server busy');
check('the first busy alert is swallowed and armed', armed === true && alerts === 0,
    'alert reached the page: ' + alerts);
const cool1 = api.cool();
check('the window is about ten minutes',
    cool1 && cool1.remaining_s > 590 && cool1.remaining_s <= 600,
    JSON.stringify(cool1));
check('the host budget is spent on the alert',
    win.sessionStorage.getItem('dfGuestTries') === '5',
    win.sessionStorage.getItem('dfGuestTries'));

// --- a second alert inside the window must not extend it
api.note('Server busy');
const cool2 = api.cool();
check('a second alert does not extend the window',
    cool2 && Math.abs(cool2.at - cool1.at) === 0,
    JSON.stringify(cool2));

// --- the host reload loop is blocked while armed
win.location.reload();
win.location.reload(true);
check('reload is blocked inside the cooldown', reloads === 0, 'reloads=' + reloads);
win.__agentReloadForced = true;
win.location.reload();
check('a forced reload still goes through', reloads === 1, 'reloads=' + reloads);

// --- expiry restores the budget and clears the store
const old = Date.now() - 11 * 60 * 1000;
const win2 = {
    localStorage: storage({ 'dofus.agent.loginBusy.v1': JSON.stringify({ at: old, text: 'Server busy' }) }),
    sessionStorage: storage({ dfGuestTries: '5' }),
    alert: function () {},
    location: { reload: function () {} }
};
const api2 = build(win2);
check('an expired window reports no cooldown', api2.cool() === null, JSON.stringify(api2.cool()));
check('an expired window restores the host budget',
    win2.sessionStorage.getItem('dfGuestTries') === '0',
    win2.sessionStorage.getItem('dfGuestTries'));
check('an expired window clears the stored alert',
    win2.localStorage.getItem('dofus.agent.loginBusy.v1') === null,
    win2.localStorage.getItem('dofus.agent.loginBusy.v1'));

// --- the access-denied popup is DOM, not a JS alert
check('the access-denied popup is watched as text',
    /access has been revoked/.test(src) && /MutationObserver/.test(src)
    && /_popupScan/.test(src), 'the AI keeps pressing into an unarmed lockout');
const win6 = { localStorage: storage(), sessionStorage: storage(), alert: function () {},
               location: { reload: function () {} } };
const api6 = build(win6, { body: { innerText: 'en[?ui.popup.accessDenied] The Dofus access has been revoked' },
                           documentElement: {} });
check('the access-denied popup arms the cooldown',
    api6.scan() === true && !!api6.cool(), JSON.stringify(api6.cool()));
check('arming from the popup spends the host budget',
    win6.sessionStorage.getItem('dfGuestTries') === '5',
    win6.sessionStorage.getItem('dfGuestTries'));

// --- single flight: the host seeder and the AI cannot stack presses
let calls = 0;
const win4 = {
    localStorage: storage(), sessionStorage: storage(),
    alert: function () {}, location: { reload: function () {} },
    gui: { loginScreen: {
        playWithGuest: function () { calls++; },
        _continueGuestFollow: function () { calls++; }
    } }
};
const api4 = build(win4);
api4.guard();
win4.gui.loginScreen.playWithGuest();
win4.gui.loginScreen.playWithGuest();
check('a second press inside twenty seconds is ignored', calls === 1, 'calls=' + calls);
win4.gui.loginScreen._continueGuestFollow();
check('the follow-up step of a press is not throttled', calls === 2, 'calls=' + calls);

// --- a throwing press is retried once (fresh boot, build ad)
let bootCalls = 0, bootThrows = 0;
const win7 = {
    localStorage: storage(), sessionStorage: storage(),
    alert: function () {}, location: { reload: function () {} },
    gui: { loginScreen: {
        playWithGuest: function () {
            bootCalls++;
            if (bootCalls === 1) {
                bootThrows++;
                throw new Error("Cannot read properties of null (reading 'error')");
            }
        }
    } }
};
const api7 = build(win7);
api7.guard();
let threw = false;
try { win7.gui.loginScreen.playWithGuest(); } catch (e) { threw = true; }
check('a press that throws is reported to the caller', threw && bootThrows === 1,
    'threw=' + threw + ' throws=' + bootThrows);
check('the throwing press is retried once', bootCalls === 2, 'calls=' + bootCalls);
win7.gui.loginScreen.playWithGuest();
check('the retry counts as the press for single flight', bootCalls === 2,
    'calls=' + bootCalls + ' (a press stacked inside 20s)');
check('a local throw is not counted as a server refusal', api7.cool() === null,
    JSON.stringify(api7.cool()));

// --- consecutive refusals buy longer quiet (measured on dofus-05, 2026-10-28)
const win8 = { localStorage: storage(), sessionStorage: storage(),
               alert: function () {}, location: { reload: function () {} } };
const api8 = build(win8);
api8.noteFail('Server busy');
const first = api8.cool();
check('the first refusal is a ten-minute quiet',
    first && first.remaining_s > 590 && first.remaining_s <= 600,
    JSON.stringify(first));
api8.noteFail('Server busy');
const second = api8.cool();
check('a second refusal buys an hour, not another ten minutes',
    second && second.remaining_s > 3590 && second.remaining_s <= 3600,
    JSON.stringify(second));
api8.noteFail('Server busy');
const exhausted = api8.cool();
check('a third refusal backs off for three hours',
    exhausted && exhausted.remaining_s > 10790 && /exhausted/.test(exhausted.reason),
    JSON.stringify(exhausted));
check('exhaustion spends the host budget',
    win8.sessionStorage.getItem('dfGuestTries') === '5',
    win8.sessionStorage.getItem('dfGuestTries'));
api8.clearFails();
check('a successful login clears the fail budget and the window',
    api8.cool() === null && win8.localStorage.getItem('dofus.agent.loginFails.v1') === null,
    JSON.stringify(api8.cool()));

// --- the quiet is derived from the refusals, not from the armed record
const oldBusy = Date.now() - 11 * 60 * 1000;
const win9 = {
    localStorage: storage({
        'dofus.agent.loginBusy.v1': JSON.stringify({ at: oldBusy, text: 'Server busy' }),
        'dofus.agent.loginFails.v1': JSON.stringify([oldBusy - 60 * 1000, oldBusy])
    }),
    sessionStorage: storage({ dfGuestTries: '5' }),
    alert: function () {}, location: { reload: function () {} }
};
const api9 = build(win9);
const derived = api9.cool();
check('a reload that keeps the refusal keeps the escalated quiet',
    derived && derived.remaining_s > 2900 && derived.remaining_s <= 3600
    && derived.fails === 2,
    JSON.stringify(derived));

// --- a stale popup must not re-arm the window with nobody pressing
const realNow = Date.now;
let clock = realNow();
Date.now = () => clock;
try {
    const win10 = { localStorage: storage(), sessionStorage: storage(),
                    alert: function () {}, location: { reload: function () {} } };
    const doc10 = { body: { innerText: 'Server busy, please try again later' },
                    documentElement: {} };
    const api10 = build(win10, doc10);
    check('the popup arms the first time', api10.scan() === true && !!api10.cool(),
        JSON.stringify(api10.cool()));
    clock += 11 * 60 * 1000;
    check('the window expires while the popup is still on screen',
        api10.cool() === null, JSON.stringify(api10.cool()));
    clock += 6000;
    api10.scan();
    check('the same popup does not arm a second window',
        api10.cool() === null, JSON.stringify(api10.cool()));
    doc10.body.innerText = '';
    clock += 6000;
    api10.scan();
    doc10.body.innerText = 'Server busy again';
    clock += 6000;
    api10.scan();
    check('a popup that comes back after leaving is a new refusal',
        !!api10.cool(), JSON.stringify(api10.cool()));

    // A real press makes the popup old news.
    let pressed = 0;
    const win11 = { localStorage: storage(), sessionStorage: storage(),
                    alert: function () {}, location: { reload: function () {} },
                    gui: { loginScreen: { playWithGuest: function () { pressed++; } } } };
    const doc11 = { body: { innerText: 'Server busy' }, documentElement: {} };
    const api11 = build(win11, doc11);
    api11.guard();
    api11.scan();
    clock += 11 * 60 * 1000;
    check('the window expired before the press', api11.cool() === null);
    win11.gui.loginScreen.playWithGuest();
    check('the press went through', pressed === 1, 'pressed=' + pressed);
    clock += 6000;
    api11.scan();
    check('a refusal after a real press arms again',
        !!api11.cool(), JSON.stringify(api11.cool()));
} finally {
    Date.now = realNow;
}

// --- an unrelated alert keeps the native behaviour
const win3 = { localStorage: storage(), sessionStorage: storage(), alert: function () { win3.shown = true; },
    location: { reload: function () {} } };
build(win3).note('Are you sure?');
win3.alert('Are you sure?');
check('a non-login alert is not swallowed by the guard',
    build(win3).note('Are you sure?') === false,
    'the guard must only own the login alerts');

console.log(fail ? 'FAILURES: ' + fail : 'login guard suite: all checks passed');
process.exit(fail ? 1 : 0);
