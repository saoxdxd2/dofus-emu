// Guard for the overworld keep-alive in scripts/mobile-disguise.js (KNOWN_GAPS.md
// G1).
//
// Why this exists: the client disconnects("INACTIVITY") when it has not observed
// activity, and an automated run can be busy *reading* state without sending any
// packet, so a live-but-silent session was logged out for being "idle". The fix
// marks the client's OWN activity flag (`connectionManager.recordActivity()`),
// letting the client's own _pingServer timer send BasicPingMessage — it must not
// hand-roll a packet or touch the egress/session pin.
//
// This is a copy-pasteable check because the Python smoke_verifier gate was
// removed 2026-10-25. Run: `node tools/test_keep_alive.js` (exit 0 = green).

const fs = require('fs');
const path = require('path');

const scriptPath = path.join(__dirname, '..', 'scripts', 'mobile-disguise.js');
const src = fs.readFileSync(scriptPath, 'utf8').replace(/\r\n/g, '\n');

const failures = [];
function check(name, ok, detail) {
    console.log((ok ? 'ok   ' : 'FAIL ') + name);
    if (!ok) failures.push(name + (detail ? ': ' + detail : ''));
}

// --- behavioural: the helper calls recordActivity and nothing else -----------

function extractHelper() {
    // The capture, the walk and the marker are one unit: the marker is only as good
    // as the object the capture found, and the capture's whole job is to not accept
    // something that cannot mark.
    const rawAt = src.indexOf('var rawSetInterval = window.setInterval;');
    const start = rawAt < 0 ? -1 : src.lastIndexOf('(function () {', rawAt);
    const mark = src.indexOf('window.__agentMarkActivity = function');
    if (start < 0 || mark < start) return null;
    const end = src.indexOf('\n    };', mark);
    if (end < 0) return null;
    return src.slice(start, end + '\n    };'.length);
}

const helper = extractHelper();
check('__agentMarkActivity is defined', helper != null);

if (helper) {
    const makeFns = new Function('window', helper +
        '\nreturn [window.__agentMarkActivity, window.__agentActivityTracker];');

    // THE fix, measured live 2026-10-27: the tracker is module-private, is not on
    // `dofus` or `gui` (a depth-3 walk of both found nothing), and the bundle exposes
    // no module registry -- so `connectionManager.recordActivity` was undefined and
    // every keep-alive silently did nothing. What the tracker does do is pass itself
    // to the global timer: setInterval(fn, 120000, this).
    const calls = [];
    const captureWin = {
        Date: Date,
        setInterval: function (fn, ms) { calls.push([fn, ms]); return 7; }
    };
    const pair = makeFns(captureWin);
    const tracker = {
        recordActivity: function () { this.marks = (this.marks || 0) + 1; },
        isActiveSince: function () { return true; },
        _checkActivity: function () {},
        lastActivityTime: 1
    };
    captureWin.setInterval(function (e) { e._checkActivity(); }, 120000, tracker);
    check('the client timer hands its owner over, and it is kept',
        captureWin.__agentActivityTimerOwner === tracker);
    captureWin.setInterval(function () {}, 1000, { sendMessage: function () {} });
    check('a timer owner that cannot mark is not captured',
        captureWin.__agentActivityTimerOwner === tracker,
        'overwritten by a non-tracker');
    check('the real setInterval is still called for both', calls.length === 2,
        'calls=' + calls.length);
    const capturedMark = pair[0], capturedFind = pair[1];
    check('the captured tracker is the one the marker uses',
        capturedFind().via === 'setInterval-owner', JSON.stringify(capturedFind()));
    capturedMark('directive');
    check('marking the captured tracker moves ITS clock', tracker.marks === 1,
        'marks=' + tracker.marks);
    check('the capture is published as covered',
        captureWin.__agentActivity.present === true
        && captureWin.__agentActivity.via === 'setInterval-owner',
        JSON.stringify(captureWin.__agentActivity));
}

if (helper) {
    const makeFns = new Function('window', helper +
        '\nreturn [window.__agentMarkActivity, window.__agentActivityTracker];');

    let recordCalls = 0, sendCalls = 0, sentTypes = [];
    const cm = {
        recordActivity: () => { recordCalls++; },
        isActiveSince: () => true,
        sendMessage: (t) => { sendCalls++; sentTypes.push(t); }
    };
    const win = { dofus: { connectionManager: cm }, Date: Date };
    const mark = makeFns(win)[0];
    const result = mark('directive');

    check('it calls the tracker recordActivity()', recordCalls === 1,
        'recordCalls=' + recordCalls);
    check('it returns true when a tracker was found', result === true);
    check('it does NOT hand-send any packet', sendCalls === 0,
        'sent ' + JSON.stringify(sentTypes));
    check('it does NOT forge BasicPingMessage',
        !sentTypes.includes('BasicPingMessage'));
    // The witness: a prevented kick is silent, so the count is the evidence.
    check('a successful mark is counted, with its source',
        win.__agentActivity.marks === 1 && win.__agentActivity.present === true
        && win.__agentActivity.last_source === 'directive',
        JSON.stringify(win.__agentActivity));

    // Where it was found is published, so a client update that moves the tracker is
    // visible in the log instead of becoming another silent no-op.
    check('the path it found is recorded', win.__agentActivity.via === 'connectionManager',
        JSON.stringify(win.__agentActivity.via));

    const guiWin = { gui: { activity: { recordActivity: () => {}, isActiveSince: () => true } },
                     Date: Date };
    const guiMark = makeFns(guiWin)[0];
    guiMark('timer');
    check('a tracker reached through gui is found too',
        guiWin.__agentActivity.present === true
        && guiWin.__agentActivity.via === 'gui.activity',
        JSON.stringify(guiWin.__agentActivity.via));

    // The regression this whole block exists for: a connection manager WITHOUT
    // recordActivity used to be the only place looked, so every mark silently did
    // nothing while the run kept reporting itself alive.
    const bareWin = { dofus: { connectionManager: { sendMessage: () => {} } }, Date: Date };
    const barePair = makeFns(bareWin);
    check('a manager without recordActivity is not a tracker',
        barePair[1]() === null, 'the shape test accepted an object that cannot mark');
    check('a manager without recordActivity is a safe false',
        barePair[0]('timer') === false);
    // And it must not be *reported* as covered: present:false is the whole point of
    // publishing the field, or a run with no tracker would look kept-alive.
    check('an uncovered client is published as present:false, not as a mark',
        bareWin.__agentActivity.present === false
        && bareWin.__agentActivity.marks === 0
        && bareWin.__agentActivity.failed === 1,
        JSON.stringify(bareWin.__agentActivity));

    const noWin = { Date: Date };
    check('missing window.dofus is a safe false', makeFns(noWin)[0]('timer') === false);
}

// --- structural: heartbeat wiring and lifecycle ------------------------------

check('the client is kept alive by a heartbeat, not by a faked ping',
    /recordActivity/.test(src) && !/sendMessage\(\s*['"]BasicPingMessage/.test(src));

const bind = src.indexOf('bindNetworkHooks: function');
// Slice from bindNetworkHooks up to the next top-level method to bound the search.
const bindEnd = src.indexOf('\n            },\n', bind);
const heartbeatRegion = src.slice(bind, bindEnd > bind ? bindEnd : bind + 4000);

check('bindNetworkHooks starts exactly one keep-alive timer',
    /if \(!self\._keepAliveTimer\)/.test(heartbeatRegion)
    && /_keepAliveTimer = setInterval\(/.test(heartbeatRegion));

check('the heartbeat is gated on enabled || aiRunning (no run = no ping)',
    /if \(!self\.enabled && !self\.aiRunning\) return;/.test(heartbeatRegion));

const intervalMatch = heartbeatRegion.match(/setInterval\(function \(\) \{[\s\S]*?\}, (\d+)\)/);
const intervalMs = intervalMatch ? parseInt(intervalMatch[1], 10) : null;
check('the heartbeat cadence is between 5s and 30s',
    intervalMs != null && intervalMs >= 5000 && intervalMs <= 30000,
    'interval=' + intervalMs);

check('the heartbeat records activity via the helper',
    /__agentMarkActivity\('timer'\)/.test(heartbeatRegion));

// The heartbeat alone was not enough: it is gated on `aiRunning`, which the host
// pushes only when its own answer CHANGES. A page that reloads mid-run comes back
// with its default (false), the gate shuts, and the client's AFK timer drops a live
// session at ten minutes. So the arrival of a directive marks activity itself --
// that needs no flag, and cannot be stale.
const directiveFn = src.slice(src.indexOf('window.__agentDirective = function'),
    src.indexOf('window.__agentDirectiveWithSnapshot'));
check('a directive marks activity without waiting for the pushed flag',
    /__agentMarkActivity\('directive'\)/.test(directiveFn),
    'directive body length=' + directiveFn.length);

// ...and the page asks for the real state when its bus comes up, so the badge and
// the heartbeat gate self-heal after a reload instead of lying until the next push.
check('the page asks the host for its state when the bus is ready',
    /postMessage\(\{ type: 'AI_REQUEST', action: 'state' \}\)/.test(src));

// The witness has to reach the log, not stay in the page.
const kaAt = src.indexOf('snap.keep_alive = ');
const kaBlock = kaAt < 0 ? '' : src.slice(kaAt, kaAt + 500);
check('the observation publishes the keep-alive witness',
    kaAt >= 0 && /marks:/.test(kaBlock) && /present:/.test(kaBlock)
    && /last_source:/.test(kaBlock) && /idle_ms:/.test(kaBlock), kaBlock.slice(0, 80));

// Disconnect must stop the timer so a crashed/closed loop never keeps pinging.
const wrappedStart = src.indexOf('var wrapped = function');
const disconnectWrap = src.indexOf("wrap('disconnect')");
const wrapRegion = (wrappedStart >= 0 && disconnectWrap > wrappedStart)
    ? src.slice(wrappedStart, disconnectWrap) : '';
check('the disconnect hook clears the keep-alive timer',
    /clearInterval\(self\._keepAliveTimer\)/.test(wrapRegion));

// Every real action should also mark activity, not just the heartbeat.
const markCalls = (src.match(/__agentMarkActivity\(['"]/g) || []).length;
// 1 heartbeat + 1 directive + 3 send sites = 5 call sites, each labelled.
check('real directives mark activity at the send sites (>=5 call sites)',
    markCalls >= 5, 'found ' + markCalls);

// Constraint: the keep-alive must not weaken the egress/session pin. It touches
// only recordActivity, so it must not open a new socket or send a custom message.
check('the keep-alive opens no new transport',
    !/__agentMarkActivity[\s\S]{0,200}new WebSocket/.test(src));

// --- against the bundle the app actually serves ------------------------------
//
// The whole keep-alive rests on two client facts: that `recordActivity` exists on
// the connection manager, and how long its inactivity timer waits. Both were read
// off the served bundle rather than assumed, and both can change with a client
// update -- which is exactly the trap that made the fight-state reader invent field
// names (KNOWN_GAPS.md G25). So they are asserted here, not in a comment.

const bundlePath = path.resolve(__dirname, '..', 'desktop', 'client', 'www', 'build', 'script.js');
if (fs.existsSync(bundlePath)) {
    const bundle = fs.readFileSync(bundlePath, 'utf8');
    // The shape the walk matches, read off the served bundle: `recordActivity` and
    // `isActiveSince` sit on ONE prototype, and that prototype is not the connection
    // manager (module 105 there, module 64 here). Both halves are required, so a
    // client update that renames either one fails here rather than silently turning
    // the keep-alive back into a no-op.
    check('the client defines one tracker with the shape the walk matches',
        /prototype\.recordActivity=function\(\)\{this\.hasSeenActivity=!0/.test(bundle)
        && /prototype\.isActiveSince=function\(e\)\{return this\.lastActivityTime>=/.test(bundle));
    check('the drop being prevented is the client timer that calls disconnect("INACTIVITY")',
        /this\._enterInactiveMode\(\)\)\}/.test(bundle) && /disconnect\("INACTIVITY"\)/.test(bundle));
    const lit = bundle.match(/s=(\d+)e(\d),c=(\d+)e(\d),l=(\d+)e(\d),d=(\d+)e(\d)/);
    check('the inactivity thresholds were read from the bundle', lit != null,
        'no s/c/l/d literal block found');
    if (lit) {
        const num = (digits, exp) => parseInt(digits, 10) * Math.pow(10, parseInt(exp, 10));
        const warnMs = num(lit[3], lit[4]);      // idle before the warning popup
        const dropMs = num(lit[5], lit[6]);      // idle before disconnect
        const checkMs = num(lit[7], lit[8]);     // how often it looks
        const heartbeat = intervalMs || 0;
        check('the heartbeat is far inside the warning window',
            heartbeat > 0 && heartbeat * 4 <= warnMs,
            'heartbeat=' + heartbeat + ' warn=' + warnMs);
        check('the heartbeat is far inside the drop window',
            heartbeat > 0 && heartbeat * 4 <= dropMs,
            'heartbeat=' + heartbeat + ' drop=' + dropMs);
        check('the heartbeat also covers a client that checks late',
            heartbeat > 0 && heartbeat <= checkMs,
            'heartbeat=' + heartbeat + ' check=' + checkMs);
    }
} else {
    check('the served bundle is present to check against', false, bundlePath);
}

if (failures.length) {
    console.error('\n' + failures.join('\n'));
    process.exit(1);
}
console.log('\nall keep-alive cases passed');
