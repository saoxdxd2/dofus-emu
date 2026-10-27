// Guard for the pre-world geometry self-heal in scripts/mobile-disguise.js.
//
// Why this exists: the client measures the viewport once at document creation and
// never re-fits afterwards. The host cannot call the game's `_resizeUi` before the
// world is reached (it throws and can leave a half-applied boot), so an instance
// whose window is resized while it is still at the login screen keeps the layout it
// booted with. Measured live 2026-10-27: tiling four 1024x640 clients into a 2x2
// grid left two of them with a squashed login screen under a black band and no
// further login progress; reloading at the settled size fixed both.
//
// The reload is only safe while there is no character, so that is what this file
// mostly proves: the guard does not fire when it must not.
//
// Copy-pasteable check (the Python smoke_verifier gate was removed 2026-10-25).
// Run: `node tools/test_geometry_watch.js` (exit 0 = green).

const fs = require('fs');
const path = require('path');

const scriptPath = path.join(__dirname, '..', 'scripts', 'mobile-disguise.js');
const src = fs.readFileSync(scriptPath, 'utf8').replace(/\r\n/g, '\n');

const failures = [];
function check(name, ok, detail) {
    console.log((ok ? 'ok   ' : 'FAIL ') + name);
    if (!ok) failures.push(name + (detail ? ': ' + detail : ''));
}

function extractBlock() {
    const anchor = src.indexOf('window.__agentGeometryBoot = boot;');
    if (anchor < 0) return null;
    const start = src.lastIndexOf('(function () {', anchor);
    if (start < 0) return null;
    const end = src.indexOf('\n    })();', anchor);
    if (end < 0) return null;
    return src.slice(start, end + '\n    })();'.length);
}

const block = extractBlock();
check('the geometry guard block is present', block != null);
check('the guard listens for resize', !!block && /window\.addEventListener\('resize'/.test(block));
check('the reload is debounced, not immediate', !!block && /setTimeout\(function \(\) \{/.test(block));
check('the snapshot publishes the geometry witness',
    /snap\.geometry = \(typeof window\.__agentGeometry === 'function'\)/.test(src));

function harness(opts) {
    const o = opts || {};
    let now = o.startMs || 1793000000000;
    const timers = [];
    let nextId = 1;
    const store = Object.assign({}, o.sessionStorage || {});
    const log = [];
    const win = {
        innerWidth: o.width === undefined ? 1540 : o.width,
        innerHeight: o.height === undefined ? 720 : o.height,
        devicePixelRatio: 1,
        listeners: [],
        addEventListener: function (kind, fn) { this.listeners.push([kind, fn]); },
        gui: o.gui === undefined ? null : o.gui,
        __agentMarkActivity: function () { win.marks = (win.marks || 0) + 1; },
        __agentGeometryBoot: null,
        __agentGeometry: null
    };
    const api = {
        window: win,
        sessionStorage: {
            getItem: function (k) { return store[k] === undefined ? null : store[k]; },
            setItem: function (k, v) { store[k] = v; }
        },
        location: {
            reloaded: 0,
            reload: function () {
                this.reloaded += 1;
                // A real reload destroys the document, and with it every timer.
                timers.length = 0;
            }
        },
        setTimeout: function (fn, ms) { const t = { fn: fn, at: now + ms, id: nextId++ }; timers.push(t); return t.id; },
        clearTimeout: function (id) {
            for (let i = 0; i < timers.length; i++) if (timers[i].id === id) { timers.splice(i, 1); return; }
        },
        Date: { now: function () { return now; } },
        console: { log: function (m) { log.push(String(m)); } }
    };
    const names = Object.keys(api);
    // eslint-disable-next-line no-new-func
    new Function(names.join(','), block + '\nreturn null;').apply(null, names.map(function (k) { return api[k]; }));
    return {
        win: win,
        location: api.location,
        store: store,
        log: log,
        fire: function (kind) {
            win.listeners.forEach(function (l) { if (l[0] === kind) l[1]({ type: kind }); });
        },
        advance: function (ms) {
            now += ms;
            const due = timers.filter(function (t) { return t.at <= now; });
            for (let i = timers.length - 1; i >= 0; i--) {
                if (timers[i].at <= now) timers.splice(i, 1);
            }
            due.forEach(function (t) { t.fn(); });
        },
        pending: function () { return timers.length; },
        setViewport: function (w, h) { win.innerWidth = w; win.innerHeight = h; },
        report: function () { return win.__agentGeometry(); }
    };
}

if (block) {
    // --- it does not touch a client that is not drifting -------------------
    const calm = harness();
    calm.fire('resize');
    calm.advance(5000);
    check('an unchanged viewport never reloads', calm.location.reloaded === 0);

    // --- the whole point: a resized pre-world window re-lays itself out ----
    const tiled = harness();
    tiled.setViewport(665, 311);
    tiled.fire('resize');
    check('the resize is held back by the debounce',
        tiled.location.reloaded === 0 && tiled.pending() === 2);
    tiled.advance(1600);
    check('a settled pre-world resize reloads once', tiled.location.reloaded === 1);
    check('the reload says which sizes it is bridging',
        tiled.log.some(function (l) { return /1540x720 -> 665x311/.test(l) && /reloading/.test(l); }),
        JSON.stringify(tiled.log));
    tiled.advance(60000);
    check('one resize does not reload twice', tiled.location.reloaded === 1);

    // Instance 1 booted portrait and settled landscape without any resize event,
    // so the observation and startup paths have to recover it too.
    const observed = harness();
    observed.setViewport(1392, 720);
    observed.fire('pageshow');
    observed.advance(0);
    check('a page show observes the settled geometry', observed.location.reloaded === 1);

    const bootSettled = harness();
    bootSettled.setViewport(1392, 720);
    bootSettled.advance(2600);
    bootSettled.advance(0);
    check('the startup settle check recovers a silent relayout',
        bootSettled.location.reloaded === 1
        && bootSettled.log.some(function (l) { return /\[startup\]/.test(l); }));

    const snap = harness();
    snap.setViewport(1392, 720);
    check('an observation can request recovery',
        snap.win.__agentGeometryRecover('observation') === true
        && snap.report().pending === true);
    snap.advance(0);
    check('the observation recovery reloads once', snap.location.reloaded === 1);

    // --- in-world is never reloaded, however wrong the geometry is ---------
    const live = harness({ gui: { playerData: { character: { id: 4212, name: 'Brruh' } } } });
    live.setViewport(300, 200);
    live.fire('resize');
    live.advance(60000);
    check('a client that reached the world is never reloaded', live.location.reloaded === 0);

    // A `gui` the page cannot read is treated as in-world: a recovery that drops a
    // live session is worse than no recovery.
    const blind = harness({ gui: { get playerData() { throw new Error('detached'); } } });
    blind.setViewport(300, 200);
    blind.fire('resize');
    blind.advance(60000);
    check('an unreadable client is left alone', blind.location.reloaded === 0);
    check('and reported as in-world', blind.report().in_world === true);

    // --- the budget: a drag must not turn into a reload loop ---------------
    const dragged = harness();
    dragged.setViewport(665, 311);
    dragged.fire('resize');
    dragged.advance(1600);
    dragged.setViewport(400, 260);
    dragged.fire('resize');
    dragged.advance(1600);
    dragged.setViewport(300, 200);
    dragged.fire('resize');
    dragged.advance(1600);
    check('the reload budget stops at two per two minutes',
        dragged.location.reloaded === 2, 'got ' + dragged.location.reloaded);
    check('the budget is written where a reload can still see it',
        Array.isArray(JSON.parse(dragged.store.dfGeomReloads)) && JSON.parse(dragged.store.dfGeomReloads).length === 2);

    const spent = harness({ sessionStorage: { dfGeomReloads: JSON.stringify([1793000000000]) } });
    spent.setViewport(665, 311);
    spent.fire('resize');
    spent.advance(1600);
    check('a reload already spent in this window counts against the budget',
        spent.location.reloaded === 1);

    const stale = harness({ sessionStorage: { dfGeomReloads: JSON.stringify([1793000000000 - 300000]) } });
    stale.setViewport(665, 311);
    stale.fire('resize');
    stale.advance(1600);
    check('a budget older than the window is forgotten', stale.location.reloaded === 1);

    // --- the witness the agent reads --------------------------------------
    const witness = harness();
    witness.setViewport(665, 311);
    const before = witness.report();
    check('the witness reports the boot size and the live size',
        before.boot.w === 1540 && before.boot.h === 720 && before.now.w === 665,
        JSON.stringify(before));
    check('the witness says the layout has drifted', before.drifted === true);
    witness.fire('resize');
    check('a pending reload is visible before it fires', witness.report().pending === true);
    witness.advance(1600);
    check('the witness counts the reload', witness.report().reloads_last_2min === 1);

    // A hairline difference is not a re-layout: the auto-zoom rewrites
    // innerWidth/innerHeight on every resize, so a 1px wobble must not cost a boot.
    const tiny = harness();
    tiny.setViewport(1535, 716);
    tiny.fire('resize');
    tiny.advance(60000);
    check('a sub-threshold change does not reload', tiny.location.reloaded === 0);
}

console.log(failures.length ? '\n' + failures.length + ' FAILED: ' + failures.join(' | ')
    : '\nall geometry-watch checks passed');
process.exit(failures.length ? 1 : 0);
