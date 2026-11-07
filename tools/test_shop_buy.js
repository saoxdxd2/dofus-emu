// shop.buy, run against stub client objects rather than grepped.
//
// The tutorial's free pack is bought through the client's fake shop: the article's
// hard-price button makes the client emit FAKE_SHOP_BUY and the scenario validates
// the quest objective itself (bundle: purchaseOnAnkama handler's fake branch).
// Outside that mode the same button starts a real-currency purchase, so the tool
// must refuse. The access path is the client's own handle, the one its tutorial
// hint uses: market window -> tabs.getFirstTab() -> target.getFirstArticle() ->
// getButtons().hardButton.
const fs = require('fs');
const path = require('path');

let fail = 0;
function check(name, ok, detail) {
    const good = !!ok;
    if (good) console.log('  [PASS] ' + name);
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const src = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'mobile-disguise.js'), 'utf8');
const start = src.indexOf("'shop.buy': function(args) {");
check('the page declares shop.buy', start > 0, 'handler not found');
if (start < 0) process.exit(1);
check('the fake-shop behaviour is the client enum value 16',
    /isBehaviourEnabled\(16\)/.test(src.slice(start, start + 2200)));

let depth = 0, end = -1;
for (let i = src.indexOf('{', start); i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') { depth--; if (depth === 0) { end = i; break; } }
}
const fnSrc = src.slice(start + "'shop.buy': ".length, end + 1);

let built = null, buildErr = null;
try {
    built = new Function('window', '_agentResult', '_agentWindowManager',
                         '_agentOpenWindows', '_agentUiTap',
                         'return (' + fnSrc + ');');
} catch (e) { buildErr = e; }
check('the extracted handler is valid JavaScript', !!built, buildErr);
if (!built) { console.log('FAILURES: ' + fail); process.exit(1); }

const agentResult = (ok, data, error) => ({ ok, data, error });

function make(behaviour, opts) {
    opts = opts || {};
    const tapped = [];
    const el = { getBoundingClientRect: () => ({ width: 40, height: 20, left: 0, top: 0 }) };
    const article = opts.noArticle ? null : {
        id: 7537,
        getButtons: () => ({ hardButton: opts.noElement ? {} : { rootElement: el } })
    };
    const win = {
        openState: !!opts.windowOpen,
        tabs: { getFirstTab: () => ({ target: { getFirstArticle: () => article } }) }
    };
    const manager = { getWindow: (name) => (name === 'market' ? win : null) };
    const scenario = { isBehaviourEnabled: (v) => (v === 16 ? !!behaviour : false) };
    const handler = built({ gui: { scenarioManager: scenario } }, agentResult,
                          () => manager, () => ['market > shop'],
                          (node) => tapped.push(node));
    return { handler, tapped, el };
}

const off = make(false, { windowOpen: true });
const r1 = off.handler({ which: 'first' });
check('without the fake shop it refuses and presses nothing',
    r1.ok === false && off.tapped.length === 0 && /fake shop/.test(r1.error || ''),
    [r1, off.tapped.length]);
check('the refusal reports the window and the enum state',
    r1.data && r1.data.fake_shop === false && r1.data.window_open === true, r1.data);

const closed = make(true, { windowOpen: false });
const r2 = closed.handler({});
check('with the shop closed it refuses and says so',
    r2.ok === false && closed.tapped.length === 0
    && /shop window is not open/.test(r2.error || ''), r2);

const loading = make(true, { windowOpen: true, noArticle: true });
const r3 = loading.handler({});
check('an empty article list is a retryable read, not a press',
    r3.ok === false && loading.tapped.length === 0
    && /no article to buy yet/.test(r3.error || '')
    && /loading/.test(r3.error || ''), r3);
check('the loading refusal says what it saw',
    r3.data && r3.data.fake_shop === true && r3.data.article === false, r3.data);

const happy = make(true, { windowOpen: true });
const r4 = happy.handler({ which: 'first' });
check('the tutorial article\'s hard button is the element pressed',
    r4.ok === true && happy.tapped.length === 1 && happy.tapped[0] === happy.el,
    [r4, happy.tapped.length]);
check('the result names the article and the button',
    r4.data && r4.data.article_id === 7537 && r4.data.button === 'hardButton', r4.data);
check('the result reports the window set after the press',
    r4.data && Array.isArray(r4.data.open_windows), r4.data && r4.data.open_windows);

const noEl = make(true, { windowOpen: true, noElement: true });
const r5 = noEl.handler({});
check('a button with no element refuses instead of tapping undefined',
    r5.ok === false && noEl.tapped.length === 0
    && /no element to press/.test(r5.error || ''), r5);

const other = make(true, { windowOpen: true });
const r6 = other.handler({ which: 'second' });
check('only the tutorial article is selectable, and the refusal says so',
    r6.ok === false && other.tapped.length === 0
    && /first article only/.test(r6.error || ''), r6);

console.log(fail ? 'FAILURES: ' + fail : 'shop.buy suite: all checks passed');
process.exit(fail ? 1 : 0);
