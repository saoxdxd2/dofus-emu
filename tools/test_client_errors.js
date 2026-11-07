// The page's capture of the client's own error channel, run rather than grepped.
//
// The served bundle sends every TextInformationMessage to gui.chat.logError
// (msgType ERROR) or gui.chat.logMsg. That is where the server's refusal text
// appears, and the page used to keep none of it: a refused talk and a silent
// no-op were identical in every log. This suite exercises the wrapper itself --
// it must capture without changing what the client sees.
const fs = require('fs');
const path = require('path');

let fail = 0;
function check(name, ok, detail) {
    const good = !!ok;
    if (good) console.log('  [PASS] ' + name);
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const src = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'mobile-disguise.js'), 'utf8');
const marker = 'var _agentMakeErrorHook = function (store)';
const start = src.indexOf(marker);
check('the page declares the error hook', start > 0);
if (start < 0) { console.log('FAILURES: ' + fail); process.exit(1); }
let depth = 0, end = -1;
for (let i = src.indexOf('{', start); i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') { depth--; if (depth === 0) { end = i; break; } }
}
const fnSrc = src.slice(src.indexOf('function', start), end + 1);
const makeHook = new Function('return (' + fnSrc + ');')();

check('the snapshot builder installs it on the client chat object',
    src.indexOf('_agentHookClientErrors(window.gui && window.gui.chat)') > 0,
    'the builder never hooks gui.chat');
check('the builder publishes the captured rows',
    /snap\.client_errors\s*=\s*_agentClientErrors\.slice\(-5\)/.test(src),
    'no client_errors field is attached');

const store = [];
const hook = makeHook(store);
const seen = [];
const chat = {
    logError: function (text) { seen.push({ self: this, text: text }); return 'ok'; }
};
const orig = chat.logError;
const firstInstall = hook(chat);

check('a chat object with logError is hooked', firstInstall === true, String(firstInstall));
check('a second install does not stack a wrapper', hook(chat) === false,
    'the marker did not stop a second wrap');
check('the method is replaced but still callable',
    typeof chat.logError === 'function' && chat.logError !== orig);

const ret = chat.logError('  the server\n  refused  this action  ');
check('the call passes straight through with its own this and arguments',
    seen.length === 1 && seen[0].self === chat
    && seen[0].text === '  the server\n  refused  this action  ',
    JSON.stringify(seen));
check('and its return value is untouched', ret === 'ok', String(ret));
check('the text is collapsed and capped',
    store.length === 1 && store[0].text === 'the server refused this action',
    JSON.stringify(store));
check('the row carries a timestamp', typeof store[0].at === 'number' && store[0].at > 0);

chat.logError('');
chat.logError(null);
check('empty messages are not recorded', store.length === 1, JSON.stringify(store));

const long = 'x'.repeat(500);
chat.logError(long);
check('a long message is stored at 200 characters',
    store.length === 2 && store[1].text.length === 200, String(store[1] && store[1].text.length));

for (let i = 0; i < 8; i++) chat.logError('msg ' + i);
check('only the newest five are kept, in order',
    store.length === 5 && store.map((r) => r.text).join(',')
        === 'msg 3,msg 4,msg 5,msg 6,msg 7', store.map((r) => r.text).join(','));

check('a chat object without logError is ignored',
    hook({}) === false && hook(null) === false && hook(undefined) === false);

let threw = false;
const hostile = {
    logError: function () { throw new Error('boom'); }
};
hook(hostile);
try { hostile.logError('a message'); } catch (e) { threw = true; }
check('a throwing client method still throws to its caller', threw);
check('and the page still read its text first',
    store.length === 5 && store[4].text === 'a message', JSON.stringify(store[4]));

console.log(fail ? 'FAILURES: ' + fail : 'all client-error checks pass');
process.exit(fail ? 1 : 0);
