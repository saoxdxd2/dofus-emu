// ui.tap's client_target path, run rather than grepped.
//
// The step-46 scenario waits for a TAP_ANYWHERE touchend that no combat cast
// produces; the tutorial's own hint taps `challengeIndicator.challengeSlot` by
// object path. This suite extracts the real resolver and the real handler and
// proves each target is found, tapped, and that a missing/hidden one refuses
// instead of pressing some other element.
const fs = require('fs');
const path = require('path');

let fail = 0;
function check(name, ok, detail) {
    const good = !!ok;
    if (good) console.log('  [PASS] ' + name);
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const src = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'mobile-disguise.js'), 'utf8');

function extract(marker) {
    const start = src.indexOf(marker);
    if (start < 0) return null;
    let depth = 0, end = -1;
    for (let i = src.indexOf('{', start); i < src.length; i++) {
        if (src[i] === '{') depth++;
        else if (src[i] === '}') { depth--; if (depth === 0) { end = i; break; } }
    }
    return src.slice(src.indexOf('function', start), end + 1);
}

const targetFnSrc = extract('var _agentClientTarget = function(name)');
const openWindowSrc = extract('var _agentOpenWindowObject = function(name)');
const tapFnSrc = extract("'ui.tap': function(args) {");
const dragFnSrc = extract('var _agentUiDrag = function(src, dst)');
check('the page declares the client-target resolver', !!targetFnSrc);
check('the page declares the open-window helper', !!openWindowSrc);
check('the page declares the drag sequence', !!dragFnSrc);
check('the page declares ui.tap', !!tapFnSrc);
if (!targetFnSrc || !openWindowSrc || !tapFnSrc || !dragFnSrc) {
    console.log('FAILURES: ' + fail);
    process.exit(1);
}

const agentResult = (ok, data, error) => ({ ok, data, error });
const agentNum = (v) => (typeof v === 'number' ? v : null);

function makeElement(rect) {
    const el = {
        className: 'slot',
        taps: 0,
        isConnected: true,
        getBoundingClientRect: () => rect,
        dispatchEvent: () => true,
        click: () => { el.taps++; }
    };
    return el;
}

function build(client) {
    const wm = () => client.windowManager;
    const openWindowObject = new Function('window', '_agentWindowManager',
        'return (' + openWindowSrc + ');')({ gui: client.gui }, wm);
    const resolver = new Function('window', '_agentWindowManager', '_agentOpenWindowObject',
        'return (' + targetFnSrc + ');')({ gui: client.gui }, wm, openWindowObject);
    const handler = new Function('args', 'window', 'document', '_agentResult', '_agentNum',
        '_agentOpenWindows', '_agentUiTap', '_agentUiDrag', '_agentOpenWindowObject',
        '_agentCloseWindow', '_agentClientTarget', '_agentHandlers',
        'return (' + tapFnSrc + ')(args);');
    const openWindows = () => (client.openWindows ? client.openWindows() : []);
    const uiTap = (el) => { el.click(); };
    const uiDrag = (srcEl, dstEl) => {
        (client.dragLog || (client.dragLog = [])).push([srcEl, dstEl]);
        return { from: [0, 0], to: [9, 9], steps: 6 };
    };
    const closeWindow = (name) => (client.closeResult ? client.closeResult(name) : false);
    const calls = { build: [], close: [] };
    const handlers = {
        'char.build': (args) => {
            calls.build.push(args || {});
            return client.buildResult
                || { ok: true, data: { stats_spent: client.buildSpent == null ? 5 : client.buildSpent } };
        }
    };
    const run = (args) => handler(args, { gui: client.gui }, { querySelectorAll: () => [] },
        agentResult, agentNum, openWindows, uiTap, uiDrag, openWindowObject,
        (name) => { calls.close.push(name); return closeWindow(name); },
        resolver, handlers);
    run.calls = calls;
    run.client = client;
    return run;
}

// 1. The fight's challenge slot: the exact object path the hint code points at.
const slot = makeElement({ width: 40, height: 40, left: 10, top: 10 });
const challenge = build({ gui: { challengeIndicator: { challengeSlot: { rootElement: slot } } } });
const r1 = challenge({ client_target: 'challenge' });
check('challenge resolves through challengeIndicator.challengeSlot.rootElement',
    r1.ok === true, r1.error);
check('challenge is tapped exactly once', slot.taps === 1, slot.taps);
check('the result names the client target and no selector',
    r1.data.client_target === 'challenge' && r1.data.selector === null,
    JSON.stringify(r1.data));

// 2. The spell book's upgrade confirm, reached through the grimoire window.
const confirm = makeElement({ width: 30, height: 30, left: 0, top: 0 });
const grimoire = { tabs: { tabsMap: { spells: { target: { spellBook: { confirmButton: { rootElement: confirm } } } } } } };
const spell = build({
    gui: {},
    windowManager: { getWindow: (name) => (name === 'grimoire' ? grimoire : null) }
});
const r2 = spell({ client_target: 'spell_upgrade_confirm' });
check('spell_upgrade_confirm resolves through the grimoire window', r2.ok === true, r2.error);
check('spell_upgrade_confirm is tapped exactly once', confirm.taps === 1, confirm.taps);

// 3. Missing control: refuse, name it, and press nothing.
const absent = build({ gui: {}, windowManager: { getWindow: () => null } });
const r3 = absent({ client_target: 'challenge' });
check('a missing challenge slot is refused', r3.ok === false && r3.data.present === false,
    JSON.stringify(r3.data));
check('the refusal names the target', /challenge/.test(r3.error), r3.error);

// 4. Present but zero-sized: not tappable, so refused rather than clicked.
const hiddenEl = makeElement({ width: 0, height: 0, left: 0, top: 0 });
const hidden = build({ gui: { challengeIndicator: { challengeSlot: hiddenEl } } });
const r4 = hidden({ client_target: 'challenge' });
check('an invisible client target is refused', r4.ok === false && r4.data.present === true,
    JSON.stringify(r4.data));
check('an invisible client target is not clicked', hiddenEl.taps === 0, hiddenEl.taps);

// 5. Ambiguous call: one path only, never a silent precedence.
const both = challenge({ client_target: 'challenge', selector: '.shopBtn' });
check('client_target plus selector is refused',
    both.ok === false && /not both/.test(both.error), both.error);
check('the ambiguous call taps nothing', slot.taps === 1, slot.taps);

// 6. The characteristics step: its Validation button spends the points and then
// fires CHARAC_UPDATE. Neither the "+" nor the confirm has a stable element path,
// so the page reproduces those two effects in that order.
function characClient(points, buildResult, executeStart) {
    const state = { fired: 0, last: null };
    const client = {
        buildResult,
        buildSpent: 5,
        gui: {
            scenarioManager: {
                currentStep: { executeStart: executeStart || function () {
                    a.getWindow('characUpdate').changePosition(0, 0);
                } },
                conditionTypeEnum: { CHARAC_UPDATE: 18 },
                checkCondition: (t) => { state.fired++; state.last = t; return 0; }
            },
            playerData: { characters: { mainCharacter: { characteristics: {
                statsPoints: points } } } }
        }
    };
    const run = build(client);
    run.state = state;
    return run;
}

const c1 = characClient(5);
const r6 = c1({ client_target: 'charac_update' });
check('charac_update spends the unspent points',
    c1.calls.build.length === 1 && c1.calls.build[0].stats === true
    && c1.calls.build[0].spells === false, JSON.stringify(c1.calls.build));
check('charac_update fires the client condition once, after the spend',
    c1.state.fired === 1 && c1.state.last === 18, JSON.stringify(c1.state));
check('the result reports the points and what was spent',
    r6.ok === true && r6.data.stats_points_before === 5 && r6.data.stats_spent === 5,
    JSON.stringify(r6.data));

const c2 = characClient(0);
const r7 = c2({ client_target: 'charac_update' });
check('with no points left the step still completes on the condition',
    r7.ok === true && c2.calls.build.length === 0 && c2.state.fired === 1,
    JSON.stringify([r7, c2.calls.build, c2.state]));

const c3 = characClient(4, { ok: false, error: 'no unspent points to spend',
                              data: { stats_spent: 0 } });
const r8 = c3({ client_target: 'charac_update' });
check('a spend that moved no point refuses instead of claiming the objective',
    r8.ok === false && c3.state.fired === 0 && /could not spend/.test(r8.error),
    JSON.stringify([r8, c3.state]));

const c4 = characClient(3, undefined, function () { window.gui.menuBar.foo(); });
const r9 = c4({ client_target: 'charac_update' });
check('the action refuses on a step that does not ask for characteristics',
    r9.ok === false && /does not ask/.test(r9.error) && c4.state.fired === 0,
    r9.error);

// 7. The world tap: the shop chain has a step that ends on TAP_ANYWHERE and
// rewinds if the market window closes, so the only safe action is a touchend on
// the game body (wui turns a left-button mouseup there into dom.touchend).
function worldClient(kind) {
    const root = makeElement({ width: 1280, height: 720, left: 0, top: 0 });
    const endingFn = kind === 'tap'
        ? function () { return window.gui.scenarioManager.conditionTypeEnum.TAP_ANYWHERE; }
        : function () { return window.gui.scenarioManager.conditionTypeEnum.CHARAC_UPDATE; };
    const client = {
        gui: {
            scenarioManager: { currentStep: { endingCondition: endingFn } },
            wBody: { rootElement: root }
        }
    };
    const run = build(client);
    run.root = root;
    return run;
}

const wt = worldClient('tap');
const r10 = wt({ client_target: 'world' });
check('the world tap presses the game body once',
    r10.ok === true && wt.root.taps === 1, wt.root.taps);
check('the world tap reports how it was produced',
    r10.data.via === 'wBody mouseup', JSON.stringify(r10.data));

const noTap = worldClient('charac');
const r11 = noTap({ client_target: 'world' });
check('a step that does not wait for a tap refuses the world tap',
    r11.ok === false && /does not wait for a tap/.test(r11.error)
    && noTap.root.taps === 0, r11.error);

// 8. The spell upgrade step highlights the spell book and waits for a spell's
// "+" press to reveal the confirm button. The first visible, enabled plus wins.
function plusButton(enabled) {
    const el = makeElement({ width: 18, height: 18, left: 0, top: 0 });
    el.className = 'addSignButton';
    return { rootElement: el, _enable: enabled, isVisible: () => true, el: el };
}
const plusOff = plusButton(false);
const plusOn = plusButton(true);
const plusLater = plusButton(true);
const spellBook = {
    spellMap: { 101: {}, 102: {}, 103: {} },
    table: { getCell: (id) => ({ plusBtn: id === '101' ? plusOff
        : (id === '102' ? plusOn : plusLater) }) }
};
const spellClient = build({
    gui: {},
    windowManager: { getWindow: (name) => (name === 'grimoire'
        ? { tabs: { tabsMap: { spells: { target: { spellBook: spellBook } } } } } : null) }
});
const r12 = spellClient({ client_target: 'spell_plus' });
check('spell_plus presses the first enabled plus button',
    r12.ok === true && plusOn.el.taps === 1, JSON.stringify([r12, plusOn.el.taps]));
check('a disabled spell plus is not pressed',
    plusOff.el.taps === 0 && plusLater.el.taps === 0,
    [plusOff.el.taps, plusLater.el.taps]);

const noBook = build({ gui: {}, windowManager: { getWindow: () => null } });
const r13 = noBook({ client_target: 'spell_plus' });
check('spell_plus refuses when the spell book is not reachable',
    r13.ok === false && /not on screen/.test(r13.error), r13.error);

// 9. The later tutorials' controls, each resolved through the exact object path
// its own hint helper walks.
function laterClient() {
    const ctx0 = makeElement({ width: 20, height: 20, left: 0, top: 0 });
    const ctx1 = makeElement({ width: 20, height: 20, left: 0, top: 0 });
    const npc0 = makeElement({ width: 20, height: 20, left: 0, top: 0 });
    const validate = makeElement({ width: 20, height: 20, left: 0, top: 0 });
    const tradeSlot = makeElement({ width: 20, height: 20, left: 0, top: 0 });
    const craftBtn = makeElement({ width: 20, height: 20, left: 0, top: 0 });
    const mergeBtn = makeElement({ width: 20, height: 20, left: 0, top: 0 });
    const craftSlot = makeElement({ width: 20, height: 20, left: 0, top: 0 });
    const category = makeElement({ width: 20, height: 20, left: 0, top: 0 });
    const sale = makeElement({ width: 20, height: 20, left: 0, top: 0 });
    const info = makeElement({ width: 20, height: 20, left: 0, top: 0 });
    const monster = makeElement({ width: 20, height: 20, left: 0, top: 0 });
    const drop = makeElement({ width: 20, height: 20, left: 0, top: 0 });
    const windows = {
        tradeWithNPC: { openState: true, getValidateBtn: () => ({ rootElement: validate }) },
        tradeWithPlayerAndNPCInventory: {
            openState: true, getStorageFirstSlotForTuto: () => ({ rootElement: tradeSlot })
        },
        crafting: {
            openState: true, _mySlotElems: { rootElement: makeElement({ width: 80, height: 80, left: 0, top: 0 }) },
            getFirstRecipe: () => ({ craftButton: { rootElement: craftBtn } }),
            getMergeBtn: () => ({ rootElement: mergeBtn })
        },
        'craftInv': { openState: true, getStorageFirstSlotForTuto: () => ({ rootElement: craftSlot }) },
        bidHouseShop: {
            openState: true,
            getCategoryDom: (i) => (i === 1 ? { rootElement: category } : null),
            getItemInSaleDom: (i) => (i === 0 ? { rootElement: sale } : null)
        },
        tradeStorage: { openState: true, infoButton: { rootElement: info } },
        grimoire: {
            tabs: { tabsMap: { bestiary: { target: {
                getMonsterWithDrop: () => ({ rootElement: monster }),
                getDropDomFromMonster: () => ({ rootElement: drop })
            } } } }
        }
    };
    const client = {
        gui: { playerData: { jobs: { getCorrectCraftingInventory: () => 'craftInv' } } },
        windowManager: {
            getWindow: (name) => windows[name] || null,
            getContextMenu: (kind) => (kind === 'npc'
                ? { actionsContainer: { getChildren: () => [npc0] } }
                : { actionsContainer: { getChildren: () => [ctx0, ctx1] } })
        }
    };
    const run = build(client);
    run.els = { ctx0, ctx1, npc0, validate, tradeSlot, craftBtn, mergeBtn, craftSlot,
                category, sale, info, monster, drop };
    return run;
}

const later = laterClient();
const cases = [
    ['contextual_menu:1', 'ctx1'],
    ['npc_menu:0', 'npc0'],
    ['trade_validate', 'validate'],
    ['trade_slot', 'tradeSlot'],
    ['craft_recipe', 'craftBtn'],
    ['craft_merge', 'mergeBtn'],
    ['craft_slot', 'craftSlot'],
    ['shop_category:1', 'category'],
    ['shop_item:0', 'sale'],
    ['sell_info', 'info'],
    ['bestiary_monster', 'monster'],
    ['bestiary_drop', 'drop']
];
for (const [target, key] of cases) {
    const before = later.els[key].taps;
    const res = later({ client_target: target });
    check('client_target ' + target + ' resolves and presses ' + key,
        res.ok === true && later.els[key].taps === before + 1,
        JSON.stringify([res, later.els[key].taps]));
}

// A window that is not open is not a target: the helpers all gate on openState.
const shut = build({
    gui: {},
    windowManager: { getWindow: () => ({ openState: false, getValidateBtn: () => ({ rootElement: makeElement({ width: 9, height: 9, left: 0, top: 0 }) }) }) }
});
const rShut = shut({ client_target: 'trade_validate' });
check('a closed window refuses its client target', rShut.ok === false, rShut.error);
check('a closed window is reported as absent', rShut.data.present === false,
    JSON.stringify(rShut.data));

// The category index is the helper's argument, not a fixed row.
const badIdx = later({ client_target: 'shop_category:0' });
check('shop_category:0 finds nothing when only category 1 exists',
    badIdx.ok === false && /not on screen/.test(badIdx.error), badIdx.error);

// 10. close:NAME is a window close, not a press.
const closer = build({
    gui: {},
    windowManager: {},
    closeResult: (name) => name === 'grimoire'
});
const rClose = closer({ client_target: 'close:grimoire' });
check('close:grimoire closes through the client manager',
    rClose.ok === true && rClose.data.closed === 'grimoire'
    && closer.calls.close[0] === 'grimoire', JSON.stringify([rClose, closer.calls.close]));
const rCloseNo = closer({ client_target: 'close:ghost' });
check('closing a window that is not open refuses',
    rCloseNo.ok === false && /not open/.test(rCloseNo.error), rCloseNo.error);
const rCloseBad = closer({ client_target: 'close:bad name' });
check('close: with a non-window name is refused before any close',
    rCloseBad.ok === false && /plain window name/.test(rCloseBad.error), rCloseBad.error);

// 11. The job tutorial's drag: source slot -> crafting box, dispatched as a
// pointer/mouse drag by the real sequence generator.
const dragClient = laterClient();
const rDrag = dragClient({ client_target: 'craft_drag' });
check('craft_drag drags the craft slot onto the crafting box',
    rDrag.ok === true && dragClient.client.dragLog && dragClient.client.dragLog.length === 1,
    JSON.stringify([rDrag, dragClient.client.dragLog]));
const noCraft = build({ gui: {}, windowManager: { getWindow: () => null } });
const rNoDrag = noCraft({ client_target: 'craft_drag' });
check('craft_drag refuses when the crafting window is closed',
    rNoDrag.ok === false && /not open/.test(rNoDrag.error), rNoDrag.error);

// 12. The drag sequence itself: press on the source, move in steps on the game
// body (the surface wui turns into dom.touchmove/touchend), release with the
// destination coordinates so the client's elementFromPoint finds the target.
const log = [];
const fakeEl = (label, rect) => ({
    getBoundingClientRect: () => rect,
    dispatchEvent: (e) => { log.push([label, e.type, e.clientX, e.clientY, e.buttons]); return true; }
});
class FakeMouseEvent {
    constructor(type, o) { Object.assign(this, o || {}); this.type = type; }
}
const bodyRoot = fakeEl('body', { left: 0, top: 0, width: 1280, height: 720 });
const dragSeq = new Function('window', 'document', 'PointerEvent', 'MouseEvent',
    'return (' + dragFnSrc + ');')({ gui: { wBody: { rootElement: bodyRoot } } }, {
    dispatchEvent: (e) => { log.push(['document', e.type, e.clientX, e.clientY, e.buttons]); return true; }
}, FakeMouseEvent, FakeMouseEvent);
const seq = dragSeq(
    fakeEl('src', { left: 0, top: 0, width: 10, height: 10 }),
    fakeEl('dst', { left: 100, top: 100, width: 20, height: 20 }));
check('the drag presses the source first',
    log[0][0] === 'src' && log[0][1] === 'pointerdown' && log[1][1] === 'mousedown',
    JSON.stringify(log.slice(0, 2)));
check('the drag moves over the game body in steps',
    log.filter((row) => row[0] === 'body' && row[1] === 'mousemove').length === 6,
    JSON.stringify(log.filter((row) => row[0] === 'body')));
check('the drag releases on the game body at the destination centre',
    log[log.length - 2][0] === 'body' && log[log.length - 1][1] === 'mouseup'
    && log[log.length - 1][2] === 110 && log[log.length - 1][3] === 110,
    JSON.stringify(log.slice(-2)));
check('the drag reports its endpoints and surface',
    seq.from[0] === 5 && seq.to[0] === 110 && seq.surface === 'wBody',
    JSON.stringify(seq));
// Without a game body (a harness page), the document is the fallback surface.
const log2 = [];
const dragSeq2 = new Function('window', 'document', 'PointerEvent', 'MouseEvent',
    'return (' + dragFnSrc + ');')({}, {
    dispatchEvent: (e) => { log2.push(['document', e.type, e.clientX]); return true; }
}, FakeMouseEvent, FakeMouseEvent);
const seq2 = dragSeq2(
    fakeEl2({ left: 0, top: 0, width: 10, height: 10 }),
    fakeEl2({ left: 50, top: 50, width: 10, height: 10 }));
function fakeEl2(rect) {
    return { getBoundingClientRect: () => rect, dispatchEvent: () => true };
}
check('without a game body the drag falls back to the document',
    seq2.surface === 'document'
    && log2.filter((row) => row[1] === 'mousemove').length === 6, JSON.stringify(seq2));

console.log(fail ? 'FAILURES: ' + fail : 'ui.tap client-target suite: all checks passed');
process.exit(fail ? 1 : 0);
