// Guard for the "Server busy" fatal-boot fallback in scripts/mobile-disguise.js.
//
// The client calls window.alert("Server busy") and waits 30 s before reloading.
// That modal blocks the page thread, so a relay failover cannot rescue the page.
// The injected guard may suppress only that exact alert, with a bounded budget.

const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'mobile-disguise.js'), 'utf8')
    .replace(/\r\n/g, '\n');
const failures = [];
function check(name, ok, detail) {
    console.log((ok ? 'ok   ' : 'FAIL ') + name);
    if (!ok) failures.push(name + (detail ? ': ' + detail : ''));
}

function extract() {
    const anchor = src.indexOf('window.__dofusFarmBootAlertGuard = true;');
    if (anchor < 0) return null;
    const start = src.lastIndexOf('(function () {', anchor);
    const end = src.indexOf('\n    })();', anchor);
    if (start < 0 || end < 0) return null;
    return src.slice(start, end + '\n    })();'.length);
}

const block = extract();
check('the boot-alert guard exists', block != null);
check('it suppresses only the exact Server busy message',
    !!block && /\\s\*server busy\\s\*/i.test(block));
check('it defers to the native alert for other messages',
    !!block && /nativeAlert\.apply\(window, arguments\)/.test(block));
check('it records a bounded retry budget',
    !!block && /maxRetries = 3/.test(block) && /dfBusyReloads/.test(block));
check('it reloads rather than leaving the modal open',
    !!block && /location\.reload\(\)/.test(block));

function harness(opts) {
    const o = opts || {};
    let now = o.now || 1793000000000;
    const timers = [];
    let nextId = 1;
    const store = Object.assign({}, o.sessionStorage || {});
    const events = [];
    const win = {
        alert: function (msg) { events.push('native:' + msg); },
        __agentMarkActivity: function (source) { events.push('activity:' + source); },
        __dofusFarmBootAlertGuard: false
    };
    const api = {
        window: win,
        sessionStorage: {
            getItem: function (key) { return store[key] === undefined ? null : store[key]; },
            setItem: function (key, value) { store[key] = value; }
        },
        location: {
            reloaded: 0,
            reload: function () { this.reloaded += 1; timers.length = 0; }
        },
        setTimeout: function (fn, ms) {
            const timer = { fn: fn, at: now + ms, id: nextId++ };
            timers.push(timer);
            return timer.id;
        },
        clearTimeout: function (id) {
            for (let i = 0; i < timers.length; i++) {
                if (timers[i].id === id) { timers.splice(i, 1); return; }
            }
        },
        Date: { now: function () { return now; } },
        console: {
            warn: function (msg) { events.push('warn:' + msg); },
            error: function (msg) { events.push('error:' + msg); }
        }
    };
    const names = Object.keys(api);
    new Function(names.join(','), block + '\nreturn null;')
        .apply(null, names.map(function (key) { return api[key]; }));
    return {
        win: win,
        store: store,
        events: events,
        location: api.location,
        advance: function (ms) {
            now += ms;
            const due = timers.filter(function (timer) { return timer.at <= now; });
            for (let i = timers.length - 1; i >= 0; i--) {
                if (timers[i].at <= now) timers.splice(i, 1);
            }
            due.forEach(function (timer) { timer.fn(); });
        }
    };
}

if (block) {
    const normal = harness();
    normal.win.alert('A real warning');
    normal.advance(5000);
    check('a normal alert is shown unchanged',
        normal.events.indexOf('native:A real warning') >= 0
        && normal.location.reloaded === 0);

    const busy = harness();
    busy.win.alert('Server busy');
    check('Server busy is suppressed immediately',
        busy.location.reloaded === 0
        && busy.events.some(function (event) { return /suppressed Server busy/.test(event); }),
        JSON.stringify(busy.events));
    busy.advance(1500);
    check('the suppressed fatal boot reloads once', busy.location.reloaded === 1);
    check('the retry is counted in session storage',
        JSON.parse(busy.store.dfBusyReloads).length === 1);
    check('the retry is marked as agent activity',
        busy.events.indexOf('activity:boot-retry') >= 0);

    const exhausted = harness();
    for (let i = 0; i < 4; i++) exhausted.win.alert('Server busy');
    check('after three retries the real alert is shown',
        exhausted.events.indexOf('native:Server busy') >= 0
        && exhausted.events.some(function (event) { return /persisted through 3 retries/.test(event); }),
        JSON.stringify(exhausted.events));

    const stale = harness({
        sessionStorage: { dfBusyReloads: JSON.stringify([1793000000000 - 300000]) }
    });
    stale.win.alert('Server busy');
    check('an old retry budget is forgotten',
        stale.location.reloaded === 0
        && JSON.parse(stale.store.dfBusyReloads).length === 1,
        stale.store.dfBusyReloads);
}

console.log(failures.length ? '\n' + failures.length + ' FAILED: ' + failures.join(' | ')
    : '\nall boot-alert checks passed');
process.exit(failures.length ? 1 : 0);
