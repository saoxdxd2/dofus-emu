// The login screen used to need a person. This suite proves the page tool that replaced them is
// still an observer of the game's own UI, not a home-made auth flow. The source checks pin the
// bundle names it is allowed to touch; the behavioural checks then RUN the extracted handler
// against a stub screen, because a source pattern cannot catch an undefined identifier — a
// `guest_saved` that was never declared passed every pattern check and returned a ReferenceError
// on the live client (measured 2026-10-27, build e).
const fs = require('fs');
const path = require('path');

let fail = 0;
function check(name, ok, detail) {
    const good = !!ok;
    if (good) console.log('  [PASS] ' + name);
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const src = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'mobile-disguise.js'), 'utf8');
const start = src.indexOf("'session.login': function(args) {");
check('the page declares session.login', start > 0, 'handler not found');
if (start < 0) process.exit(1);

// brace-match the handler body so the assertions are about this one tool
let depth = 0, end = -1;
for (let i = src.indexOf('{', start); i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') { depth--; if (depth === 0) { end = i; break; } }
}
const body = src.slice(start, end);
check('the handler body was captured', body.length > 400, body.length);

check('it can report without pressing (inspect/dry_run)',
    /inspect \|\| args.dry_run|args\.inspect \|\| args\.dry_run/.test(body)
    && /if \(inspectOnly\) return _agentResult\(true, out\)/.test(body));
check('it reads the game’s own button classes, not invented ones',
    /btnPlay/.test(body) && /guest/.test(body));
check('it ranks the play button first', /btnplay/.test(body.toLowerCase()));
check('it dispatches touch, pointer and mouse (the page is touch-first)',
    /TouchEvent\('touchstart'/.test(body) && /PointerEvent\('pointerdown'/.test(body)
    && /MouseEvent\('click'/.test(body));

// the line it will not cross: no credential writing, no calling the auth API itself
check('it never writes credentials', body.indexOf('setCredentials') === -1);
check('it never calls dofus.login itself', !/dofus\.login\s*\(/.test(body));
check('it only reads the guest account the game stored',
    /all#guestAccount/.test(body) && body.indexOf('setItem') === -1);
check('it reports what it pressed', /out\.pressed = pressed/.test(body));
check('it refuses loudly when nothing could be pressed',
    /nothing on the login screen could be pressed/.test(body));
check('it reports the state it saw (in_world and connected)',
    /in_world:/.test(body) && /connected:/.test(body));
check('it re-reads the screen after pressing', /out\.after = \{ in_world:/.test(body));
check('the DOM press is the fallback, not the first attempt',
    body.indexOf('ls[step]()') < body.indexOf("emit(saved ? '_continueGuestBtn' : '_playGuestBtn')"));

// ---- behavioural: run the extracted handler against a stub login screen
function makeWindow(stored) {
    const calls = [];
    const screen = {
        _name: 'loginScreen',
        _childrenList: [],
        _events: { tap: [{ listener: function () { calls.push('btn-tap'); } }] },
        rootElement: { className: 'loginScreen screen', textContent: '',
                       getBoundingClientRect: () => ({ width: 800, height: 600, left: 0, top: 0 }),
                       dispatchEvent: () => true },
        _playGuestBtn: {
            _name: 'playGuest', rootElement: { className: 'linkGuest Button scaleOnPress',
                textContent: 'Play as a guest',
                getBoundingClientRect: () => ({ width: 90, height: 16, left: 10, top: 400 }) },
            _events: { tap: [{ listener: function () { calls.push('play.tap'); } }] },
            isEnable: () => true,
            emit: function (name) { calls.push('play.emit:' + name); }
        },
        _continueGuestBtn: {
            _name: 'continueGuest', rootElement: { className: 'continueGuestBtn' },
            _events: { tap: [{ listener: function () { calls.push('continue.tap'); } }] },
            isEnable: () => true,
            emit: function (name) { calls.push('continue.emit:' + name); }
        },
        _createGuest: function () { calls.push('create'); },
        _continueGuest: function () { calls.push('continue'); }
    };
    const store = { getItem: (k) => (k === 'dofus.1' && stored)
        ? JSON.stringify({ 'all#guestAccount': { login: 'g', password: 'p' } }) : null };
    const win = { gui: { loginScreen: screen }, localStorage: store,
                  TouchEvent: undefined, document: undefined };
    return { win, calls, screen };
}

const doc = (classes) => ({
    querySelectorAll: (sel) => sel === '[class]'
        ? classes.map(c => ({ className: c, textContent: 'x', type: '', name: '',
                               placeholder: '', value: '', tagName: 'DIV',
                               getBoundingClientRect: () => ({ width: 40, height: 12, left: 1, top: 1 }),
                               dispatchEvent: () => true, click: () => true }))
        : []
});

const agentResult = (ok, data, error) => ({ ok, data, error });
const snapshot = (inWorld) => () => ({ in_world: inWorld, connected: true });

const fnSrc = src.slice(start + "'session.login': ".length, end + 1);
let built = null, buildErr = null;
try {
    built = new Function('window', 'document', '_agentResult', '_agentSnapshot',
                         'PointerEvent', 'MouseEvent', 'TouchEvent',
                         'return (' + fnSrc + ');');
} catch (e) { buildErr = e; }
check('the extracted handler is valid JavaScript', !!built, buildErr);

if (built) {
    // first run: no guest stored, so the game's create step must be the one that runs
    const fresh = makeWindow(false);
    let r1 = null, e1 = null;
    try {
        r1 = built(fresh.win, doc(['loginScreen screen', 'linkGuest Button scaleOnPress',
                                   'LoginsForm rightColumn']),
                   agentResult, snapshot(false), undefined, undefined, undefined)({ target: 'guest' });
    } catch (e) { e1 = e; }
    check('a fresh client runs _createGuest, not _continueGuest',
        !e1 && fresh.calls.indexOf('create') === 0, e1 || fresh.calls);
    check('the press names the step it ran',
        !e1 && r1.data.pressed === 'gui.loginScreen._createGuest()', e1 || (r1 && r1.data.pressed));
    check('it reports the screen after pressing',
        !e1 && !!r1.data.after && r1.data.after.in_world === false, e1 || (r1 && r1.data.after));

    // returning guest: the stored account flips the step
    const returning = makeWindow(true);
    let r2 = null, e2 = null;
    try {
        r2 = built(returning.win, doc(['continueGuestBtn']), agentResult, snapshot(false),
                   undefined, undefined, undefined)({ target: 'guest' });
    } catch (e) { e2 = e; }
    check('a client with a stored guest runs _continueGuest',
        !e2 && returning.calls.indexOf('continue') === 0, e2 || returning.calls);

    // inspect must change nothing
    const idle = makeWindow(false);
    let r3 = null, e3 = null;
    try {
        r3 = built(idle.win, doc(['loginScreen screen']), agentResult, snapshot(false),
                   undefined, undefined, undefined)({ inspect: true });
    } catch (e) { e3 = e; }
    check('inspect reports and presses nothing',
        !e3 && r3.ok === true && idle.calls.length === 0, e3 || idle.calls);
    check('inspect says whether a guest is stored',
        !e3 && r3.data.guest_saved === false, e3 || (r3 && r3.data.guest_saved));

    // already in a world: no press, and the tool says so through the state it reports
    const playing = makeWindow(true);
    let r4 = null, e4 = null;
    try {
        r4 = built(playing.win, doc([]), agentResult, snapshot(true),
                   undefined, undefined, undefined)({ target: 'guest' });
    } catch (e) { e4 = e; }
    check('in a world it still does not fabricate anything',
        !e4 && playing.calls.indexOf('continue') === 0 && r4.data.after.in_world === true,
        e4 || playing.calls);

    // the game's own end-to-end guest call wins when it exists — it is a prototype method, so a
    // key scan cannot see it and a scan-based tool would never call it
    const oneShot = makeWindow(true);
    oneShot.screen.playWithGuest = function () { oneShot.calls.push('playWithGuest'); };
    let r6 = null, e6 = null;
    try {
        r6 = built(oneShot.win, doc(['continueGuestBtn']), agentResult, snapshot(false),
                   undefined, undefined, undefined)({ target: 'guest' });
    } catch (e) { e6 = e; }
    check('playWithGuest is preferred over the step methods',
        !e6 && r6.data.pressed === 'gui.loginScreen.playWithGuest()'
        && oneShot.calls[0] === 'playWithGuest', e6 || [r6 && r6.data.pressed, oneShot.calls]);
    check('it says which guest steps the screen actually offers',
        !e6 && r6.data.guest_steps && r6.data.guest_steps.playWithGuest === true,
        e6 || (r6 && r6.data.guest_steps));

    // a screen with nothing to press must refuse, not report a press (a source pattern cannot
    // catch this: the refusal string is still in the file when the branch is dead)
    const empty = makeWindow(false);
    delete empty.screen._createGuest;
    delete empty.screen._continueGuest;
    empty.screen._playGuestBtn = null;
    empty.screen._continueGuestBtn = null;
    let r5 = null, e5 = null;
    try {
        r5 = built(empty.win, doc([]), agentResult, snapshot(false),
                   undefined, undefined, undefined)({ target: 'guest' });
    } catch (e) { e5 = e; }
    check('an empty screen refuses instead of claiming a press',
        !e5 && r5.ok === false && /nothing on the login screen could be pressed/.test(r5.error || ''),
        e5 || r5);
    check('a refusal still reports what it looked at',
        !e5 && r5.data.pressable === 0 && !!r5.data.seen, e5 || (r5 && r5.data));
}

console.log(fail ? 'FAILURES: ' + fail : 'session.login suite: all checks passed');
process.exit(fail ? 1 : 0);
