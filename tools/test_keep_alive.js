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
    const start = src.indexOf('window.__agentMarkActivity = function');
    if (start < 0) return null;
    const end = src.indexOf('\n    };', start);
    if (end < 0) return null;
    return src.slice(start, end + '\n    };'.length);
}

const helper = extractHelper();
check('__agentMarkActivity is defined', helper != null);

if (helper) {
    const makeFn = new Function('window', helper + '\nreturn window.__agentMarkActivity;');

    let recordCalls = 0, sendCalls = 0, sentTypes = [];
    const cm = {
        recordActivity: () => { recordCalls++; },
        sendMessage: (t) => { sendCalls++; sentTypes.push(t); }
    };
    const win = { dofus: { connectionManager: cm } };
    const mark = makeFn(win);
    const result = mark();

    check('it calls connectionManager.recordActivity()', recordCalls === 1,
        'recordCalls=' + recordCalls);
    check('it returns true when a manager exists', result === true);
    check('it does NOT hand-send any packet', sendCalls === 0,
        'sent ' + JSON.stringify(sentTypes));
    check('it does NOT forge BasicPingMessage',
        !sentTypes.includes('BasicPingMessage'));

    // No manager -> safe no-op, must not throw.
    const noCm = makeFn({});
    check('missing window.dofus is a safe false', noCm() === false);
    const noRecord = makeFn({ dofus: { connectionManager: { sendMessage: () => {} } } });
    check('a manager without recordActivity is a safe false', noRecord() === false);
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
    /__agentMarkActivity\(\)/.test(heartbeatRegion));

// Disconnect must stop the timer so a crashed/closed loop never keeps pinging.
const wrappedStart = src.indexOf('var wrapped = function');
const disconnectWrap = src.indexOf("wrap('disconnect')");
const wrapRegion = (wrappedStart >= 0 && disconnectWrap > wrappedStart)
    ? src.slice(wrappedStart, disconnectWrap) : '';
check('the disconnect hook clears the keep-alive timer',
    /clearInterval\(self\._keepAliveTimer\)/.test(wrapRegion));

// Every real action should also mark activity, not just the heartbeat.
const markCalls = (src.match(/__agentMarkActivity\(\)/g) || []).length;
// 3 send sites (sendPacket, _cmSend, chat.say) + 1 heartbeat = 4 call sites.
check('real directives mark activity at the send sites (>=4 call sites)',
    markCalls >= 4, 'found ' + markCalls);

// Constraint: the keep-alive must not weaken the egress/session pin. It touches
// only recordActivity, so it must not open a new socket or send a custom message.
check('the keep-alive opens no new transport',
    !/__agentMarkActivity[\s\S]{0,200}new WebSocket/.test(src));

if (failures.length) {
    console.error('\n' + failures.join('\n'));
    process.exit(1);
}
console.log('\nall keep-alive cases passed');
