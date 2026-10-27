// Behavioural checks for the map-transition watchdog (KNOWN_GAPS G12).
//
// Why this exists: the resilience guards were only ever checked for *presence* in the bundle.
// A stalled `loadAssets`/`setMap` leaves `foreground.lock("loadMap")` set forever, and that was
// the second reason healthy runs die. Presence is not behaviour: a watchdog that never fires,
// unlocks the wrong keys, or fires after a healthy transition would still pass a grep.
//
// This executes the real generator rule (`tools/patch-engine.js`, `resil22New`) with fake
// timers and a mocked IsoEngine, pins that the same text is in the served bundle, and checks
// the cancel half (`resil23New`). The live stall is still the proof; this is the behaviour
// offline.

const fs = require('fs');
const path = require('path');

let pass = 0, fail = 0;
function check(name, ok, detail) {
    if (ok) { console.log('  [PASS] ' + name); pass++; }
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const engine = fs.readFileSync(path.join(__dirname, '..', 'tools', 'patch-engine.js'), 'utf8');
const bundlePath = path.join(__dirname, '..', 'desktop', 'client', 'www', 'build', 'script.js');
const bundle = fs.existsSync(bundlePath) ? fs.readFileSync(bundlePath, 'utf8') : null;

function literal(name) {
    const at = engine.indexOf('const ' + name + " = '");
    if (at < 0) return null;
    let i = at + ('const ' + name + " = '").length;
    let out = '';
    while (i < engine.length) {
        const c = engine[i];
        if (c === '\\') { out += engine[i + 1]; i += 2; continue; }
        if (c === "'") break;
        out += c; i += 1;
    }
    return out;
}

const rule = literal('resil22New');
const cancelRule = literal('resil23New');
check('the watchdog rule exists in the generator', !!rule, 'resil22New not found');
check('the cancel rule exists in the generator', !!cancelRule, 'resil23New not found');
if (!rule || !cancelRule) {
    console.log('\n' + pass + ' passed, ' + fail + ' failed');
    process.exit(1);
}
check('the served bundle carries the watchdog rule', !!bundle && bundle.indexOf(rule) >= 0);
check('the served bundle carries the cancel rule', !!bundle && bundle.indexOf(cancelRule) >= 0);
check('the rule arms at 3500 ms', rule.indexOf(',3500);') > 0);

let nextId = 1;
const timers = [];
function fakeSetTimeout(fn, ms) {
    const timer = {id: nextId++, fn: fn, ms: ms, cleared: false, fired: false};
    timers.push(timer);
    return timer.id;
}
function fakeClearTimeout(id) {
    const timer = timers.filter(t => t.id === id)[0];
    if (timer) timer.cleared = true;
}
const warnings = [], errors = [];
const fakeConsole = {warn: m => warnings.push(m), error: (...a) => errors.push(a)};

function makeWorld() {
    const calls = {unlocks: [], shaders: [], refreshes: 0, hidden: 0, started: 0,
                   saved: 0, arrow: false, removed: false};
    const foreground = {
        locked: true,
        unlock: name => calls.unlocks.push(name),
        hideBorderArrow: () => { calls.arrow = true; }
    };
    const self = {
        _isMapLoading: false,
        _loadingFadeInTransitionRunning: false,
        _waitingForMessage: true,
        _transitionWatchdog: null,
        _mapSceneTransitionGraphic: {
            remove: () => { calls.removed = true; }
        },
        mapScene: {
            renderingParams: {ratio: 1},
            setShader: s => calls.shaders.push(s),
            requireCompleteRefresh: () => { calls.refreshes += 1; }
        },
        _loadingProgress: {loadAssets: cb => cb(), progressEgg: {alpha: 0}},
        _hideLoadingProgress: () => { calls.hidden += 1; },
        _startLoading: () => { calls.started += 1; self._isMapLoading = true; },
        _activateMapScene: () => true,
        _saveImageForTransition: () => { calls.saved += 1; },
        emit: () => {},
        _makeUserActorWalkInDirection: () => {},
        _runLoadingFadeInTransition: () => {}
    };
    return {self, foreground, calls};
}

function makeProto() {
    return new Function('s', 'setTimeout', 'clearTimeout', 'window', 'console',
        rule + '\nreturn s.prototype;')(
        {prototype: {}}, fakeSetTimeout, fakeClearTimeout,
        {foreground: null}, fakeConsole);
}

let world = makeWorld();
const windowStub = {foreground: world.foreground};
const proto = new Function('s', 'setTimeout', 'clearTimeout', 'window', 'console',
    rule + '\nreturn s.prototype;')(
    {prototype: {}}, fakeSetTimeout, fakeClearTimeout, windowStub, fakeConsole);
proto.launchMapTransition.call(world.self, null);
check('a launch arms exactly one watchdog', timers.length === 1 && !timers[0].cleared,
    timers.length);
check('the started transition leaves the loading flag set (the stall precondition)',
    world.self._isMapLoading === true && world.foreground.locked === true);

timers[0].fn();
check('a stalled transition is cleared', world.self._isMapLoading === false
    && world.self._waitingForMessage === false
    && world.self._loadingFadeInTransitionRunning === false, JSON.stringify({
        loading: world.self._isMapLoading, waiting: world.self._waitingForMessage}));
check('both foreground locks are released',
    world.calls.unlocks.indexOf('loadMap') >= 0
    && world.calls.unlocks.indexOf('quickReconnection') >= 0
    && world.foreground.locked === false, JSON.stringify(world.calls.unlocks));
check('the black-screen graphic is removed and the scene refreshed',
    world.calls.removed === true && world.calls.refreshes === 1
    && world.calls.shaders.indexOf('unfiltering') >= 0
    && world.self.mapScene.renderingParams.ratio === 0.15,
    JSON.stringify(world.calls));
check('the watchdog warns once and hid the loading progress',
    warnings.length === 1 && world.calls.hidden === 1, warnings);

// A healthy transition must be left alone: the guard is a condition, not an unconditional reset.
world = makeWorld();
world.foreground.locked = false;
world.self._isMapLoading = false;
world.self._mapSceneTransitionGraphic = null;
// A non-activating launch does not set the loading flag, which is the healthy case.
world.self._activateMapScene = () => false;
world.self._loadingFadeInTransitionRunning = false;
const healthyProto = new Function('s', 'setTimeout', 'clearTimeout', 'window', 'console',
    rule + '\nreturn s.prototype;')(
    {prototype: {}}, fakeSetTimeout, fakeClearTimeout,
    {foreground: world.foreground}, fakeConsole);
healthyProto.launchMapTransition.call(world.self, null);
timers[timers.length - 1].fn();
check('a healthy map is not reset by the watchdog',
    timers.length > 0 && world.calls.unlocks.length === 0 && warnings.length === 1
    && world.calls.hidden === 0, JSON.stringify(world.calls));

// A relaunch must not leave the previous timer armed.
world = makeWorld();
const relaunchStart = timers.length;
const relaunchProto = new Function('s', 'setTimeout', 'clearTimeout', 'window', 'console',
    rule + '\nreturn s.prototype;')(
    {prototype: {}}, fakeSetTimeout, fakeClearTimeout,
    {foreground: world.foreground}, fakeConsole);
relaunchProto.launchMapTransition.call(world.self, null);
relaunchProto.launchMapTransition.call(world.self, null);
const relaunchTimers = timers.slice(relaunchStart);
check('a relaunch clears the previous watchdog',
    relaunchTimers.length === 2 && relaunchTimers[0].cleared === true
    && relaunchTimers[1].cleared === false,
    JSON.stringify(relaunchTimers.map(t => ({id: t.id, cleared: t.cleared}))));

// The fade-out path must cancel the watchdog: a successful transition is not a stall.
const clearBlock = 'if(this._transitionWatchdog){clearTimeout(this._transitionWatchdog);this._transitionWatchdog=null;}';
check('the cancel rule is the fade-out cancellation', cancelRule.indexOf(clearBlock) >= 0);
world = makeWorld();
const armedProto = new Function('s', 'setTimeout', 'clearTimeout', 'window', 'console',
    rule + '\nreturn s.prototype;')(
    {prototype: {}}, fakeSetTimeout, fakeClearTimeout,
    {foreground: world.foreground}, fakeConsole);
armedProto.launchMapTransition.call(world.self, null);
const armedId = world.self._transitionWatchdog;
const clearFn = new Function('clearTimeout', 'return function(){' + clearBlock + '};')(
    fakeClearTimeout);
clearFn.call(world.self);
check('a completed transition cancels the watchdog',
    world.self._transitionWatchdog === null
    && timers.filter(t => t.id === armedId)[0].cleared === true,
    String(armedId));

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
