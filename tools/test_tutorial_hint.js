// The tutorial snapshot's step introspection, run rather than grepped.
//
// A scripted step's executeStart names the control it points at. The challenge
// sub-step (checkpoint -1, after FIGHTER_TURN) calls pointToChallenge and waits
// for a TAP_ANYWHERE touchend that combat casts never produce. The page publishes
// that hint plus the step's numeric behaviour list so the planner can act on it.
const fs = require('fs');
const path = require('path');

let fail = 0;
function check(name, ok, detail) {
    const good = !!ok;
    if (good) console.log('  [PASS] ' + name);
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const src = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'mobile-disguise.js'), 'utf8');
const marker = 'var _agentTutorial = function ()';
const start = src.indexOf(marker);
check('the page declares _agentTutorial', start > 0);
if (start < 0) { console.log('FAILURES: ' + fail); process.exit(1); }
let depth = 0, end = -1;
for (let i = src.indexOf('{', start); i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') { depth--; if (depth === 0) { end = i; break; } }
}
const fnSrc = src.slice(src.indexOf('function', start), end + 1);
const build = new Function('window', 'return (' + fnSrc + ');');

function run(step, extra) {
    const sm = Object.assign({
        currentQuestId: 4262,
        currentStepId: 46,
        currentSpecialStepId: -1,
        currentStep: step,
        isBehaviourEnabled: () => false
    }, extra || {});
    return build({ gui: { scenarioManager: sm }, isoEngine: null })();
}

// The live challenge sub-step: -1 checkpoint, behaviours as client numbers, and
// an executeStart that calls the hint helper by name.
const challengeStep = {
    behaviours: [4, 8],
    questStepCheckpoint: -1,
    executeStart: function () { o.pointToChallenge(); }
};
const t1 = run(challengeStep);
check('the challenge hint is published as a client target',
    t1.hint_target === 'challenge', t1.hint_target);
check('the step checkpoint is published, including -1 sub-steps',
    t1.step_checkpoint === -1, String(t1.step_checkpoint));
check('the behaviour list is published as numbers, not names',
    Array.isArray(t1.behaviour_ids) && t1.behaviour_ids.length === 2
    && t1.behaviour_ids.every((n) => typeof n === 'number'), JSON.stringify(t1.behaviour_ids));

const spellStep = {
    behaviours: [],
    questStepCheckpoint: 4980,
    executeStart: function () { o.pointToConfirmUpgradeSpell(); }
};
const t2 = run(spellStep);
check('the spell upgrade hint is published as its own target',
    t2.hint_target === 'spell_upgrade_confirm', t2.hint_target);

const characStep = {
    behaviours: [8],
    questStepCheckpoint: -1,
    executeStart: function () { a.getWindow('characUpdate').changePosition(0, 0); }
};
const t2b = run(characStep);
check('the characteristics step is published as its own client target',
    t2b.hint_target === 'charac_update', t2b.hint_target);

const tapStep = {
    behaviours: [],
    questStepCheckpoint: -1,
    executeStart: function () {},
    endingCondition: function () { return p.TAP_ANYWHERE; }
};
const t2c = run(tapStep);
check('a step that ends on TAP_ANYWHERE is published as a world tap',
    t2c.tap_anywhere === true, String(t2c.tap_anywhere));
check('the same step is not turned into a client-target hint',
    t2c.hint_target === null, t2c.hint_target);

// The live fight step (dofus-05, 2026-10-29): its ending condition is
// FIGHTER_TURN, and the loop kept asking for crossings because nothing published
// that the step was waiting for a fight. The condition names are data now.
const fightStep = {
    behaviours: [18, 41],
    questStepCheckpoint: 4970,
    executeStart: function () {},
    endingCondition: function (e) {
        return e === window.gui.scenarioManager.conditionTypeEnum.FIGHTER_TURN
            && 145490946 !== window.isoEngine ? 1 : 0;
    }
};
const tFight = run(fightStep);
check('a step that ends on a fighter turn publishes FIGHTER_TURN',
    Array.isArray(tFight.ending_conditions)
    && tFight.ending_conditions.indexOf('FIGHTER_TURN') !== -1,
    JSON.stringify(tFight.ending_conditions));
const twoWayStep = {
    behaviours: [],
    questStepCheckpoint: 1,
    executeStart: function () {},
    endingCondition: function (e) {
        return e === window.gui.scenarioManager.conditionTypeEnum.CHANGE_MAP
            || e === window.gui.scenarioManager.conditionTypeEnum.CHANGE_MAP ? 1 : 0;
    }
};
const tTwo = run(twoWayStep);
check('each named condition is published once, in source order',
    Array.isArray(tTwo.ending_conditions)
    && tTwo.ending_conditions.length === 1
    && tTwo.ending_conditions[0] === 'CHANGE_MAP',
    JSON.stringify(tTwo.ending_conditions));
check('a step with no ending condition publishes an empty list, not null',
    Array.isArray(t2b.ending_conditions) && t2b.ending_conditions.length === 0,
    JSON.stringify(t2b.ending_conditions));

const menuStep = {
    behaviours: [],
    questStepCheckpoint: 4573,
    executeStart: function () { o.pointToMenuIcon('Carac'); }
};
const t2d = run(menuStep);
check('a step that points at a menu publishes the client menu name',
    t2d.menu_hint === 'Carac', t2d.menu_hint);
check('the menu hint is not confused with a client-target hint',
    t2d.hint_target === null, t2d.hint_target);

const windowStep = {
    behaviours: [],
    questStepCheckpoint: -1,
    executeStart: function () {},
    endingCondition: function () {
        return e === sm.conditionTypeEnum.WINDOW_OPENED && 'characUpdate' === t.windowId ? 1 : 0;
    }
};
const t2e = run(windowStep);
check('a step that ends on WINDOW_OPENED names the window it waits for',
    t2e.open_window === 'characUpdate', t2e.open_window);

const plusStep = {
    behaviours: [],
    questStepCheckpoint: -1,
    executeStart: function () { window.gui.scenarioManager.displayHighlightedSpellButtons(!0); }
};
const t2f = run(plusStep);
check('the spell upgrade step is published as a spell-plus press',
    t2f.hint_target === 'spell_plus', t2f.hint_target);
check('a step with no behaviours publishes an empty list',
    Array.isArray(t2.behaviour_ids) && t2.behaviour_ids.length === 0,
    JSON.stringify(t2.behaviour_ids));

const plain = run({ behaviours: [8], questStepCheckpoint: 4581,
                    executeStart: function () { window.gui.menuBar.foo(); } });
check('a step with no hint publishes null, not a guess',
    plain.hint_target === null, plain.hint_target);
check('a named checkpoint is not clobbered by the -1 convention',
    plain.step_checkpoint === 4581, String(plain.step_checkpoint));

// The later tutorials name their controls with the same helper convention. Each
// helper maps to the client target the page resolves by the same object path.
const helperCases = [
    ['pointToInteractiveContextualMenuAction(2)', 'contextual_menu:2'],
    ['pointToInteractiveNPCContextualMenuAction(1)', 'npc_menu:1'],
    ['pointToNPCTradeValidateBtn()', 'trade_validate'],
    ['pointToFirstTradeNPCInventorySlot()', 'trade_slot'],
    ['pointToRecipeBtn()', 'craft_recipe'],
    ['pointToMergeBtn()', 'craft_merge'],
    ['pointToFirstCraftItemSlot()', 'craft_slot'],
    ['dragDropItemSlotToCraftActorBox()', 'craft_drag'],
    ['pointToShopCategory(1)', 'shop_category:1'],
    ['pointToShopItem(2)', 'shop_item:2'],
    ['pointToInfoBtnSellShop()', 'sell_info'],
    ['pointToFirstNormalBestiaryMonsterWithSpecificDrop(C)', 'bestiary_monster'],
    ['pointToFirstDropBestiaryMonsterWithSpecificDrop(C)', 'bestiary_drop'],
    ['pointToWindowCloseButton("grimoire")', 'close:grimoire']
];
for (const [call, want] of helperCases) {
    const step = new Function('o', 'return { behaviours: [], questStepCheckpoint: -1,'
        + ' executeStart: function () { o.' + call + '; } }')(new Proxy({}, { get: () => function () {} }));
    const got = run(step);
    check('the ' + call.split('(')[0] + ' hint maps to ' + want,
        got.hint_target === want, got.hint_target);
}

const mapElementStep = {
    behaviours: [],
    questStepCheckpoint: 5306,
    executeStart: function () {
        window.gui.hintAnimationManager.playInteractiveElementTap(514876, { doubleTap: false });
    }
};
const tMap = run(mapElementStep);
check('a step that highlights a map element publishes its element id',
    tMap.interactive_hint === 514876, String(tMap.interactive_hint));
check('a map-element step is not turned into a client-target hint',
    tMap.hint_target === null, tMap.hint_target);

// The real client bundle: evaluate the actual challenge step object and read it
// through the real snapshot. This is the canary for a client rebuild moving the
// hint or renaming the helper.
const bundlePath = path.join(__dirname, '..', 'desktop', 'client', 'www', 'build', 'script.js');
if (fs.existsSync(bundlePath)) {
    const bundle = fs.readFileSync(bundlePath, 'utf8');
    const enums = new Proxy({}, { get: () => 0 });
    const hints = new Proxy({}, { get: () => function () {} });
    function bundleStep(marker) {
        const at = bundle.indexOf(marker);
        check('the live bundle still contains the "' + marker + '" step', at > 0, at);
        if (at <= 0) return null;
        const begin = bundle.lastIndexOf('{behaviours:[', at);
        let d = 0, stop = -1;
        for (let i = begin; i < bundle.length; i++) {
            if (bundle[i] === '{') d++;
            else if (bundle[i] === '}') { d--; if (d === 0) { stop = i; break; } }
        }
        return new Function('n', 'o', 'a', 'return (' + bundle.slice(begin, stop + 1) + ');')(
            enums, hints, { getWindow: () => ({ changePosition: function () {} }) });
    }
    const real = bundleStep('o.pointToChallenge()');
    if (real) {
        const t3 = run(real);
        check('the real bundle step is detected from its executeStart source',
            t3.hint_target === 'challenge', t3.hint_target);
        check('the real step is the -1 checkpoint sub-step',
            t3.step_checkpoint === -1, String(t3.step_checkpoint));
        check('the real step publishes a numeric behaviour list',
            Array.isArray(t3.behaviour_ids)
            && t3.behaviour_ids.length > 0
            && t3.behaviour_ids.every((n) => typeof n === 'number'),
            JSON.stringify(t3.behaviour_ids));
    }
    const realCharac = bundleStep('getWindow("characUpdate")');
    if (realCharac) {
        const t4 = run(realCharac);
        check('the real characteristics step is detected from its executeStart source',
            t4.hint_target === 'charac_update', t4.hint_target);
        check('the real characteristics step is a -1 sub-step',
            t4.step_checkpoint === -1, String(t4.step_checkpoint));
    }
    // The shop chain's post-load step (dialog 26689): waits for TAP_ANYWHERE and
    // rewinds if the market window closes. It must be a world tap, not a shop press.
    const realShopTap = bundleStep('id:26689');
    if (realShopTap) {
        const t5 = run(realShopTap);
        check('the real shop post-load step is published as a world tap',
            t5.tap_anywhere === true, String(t5.tap_anywhere));
        check('the real shop post-load step is a -1 sub-step with no client-target hint',
            t5.step_checkpoint === -1 && t5.hint_target === null,
            JSON.stringify([t5.step_checkpoint, t5.hint_target]));
    }
    const realMenu = bundleStep('pointToMenuIcon("Carac")');
    if (realMenu) {
        const t6 = run(realMenu);
        check('the real characteristics-window step publishes its menu name',
            t6.menu_hint === 'Carac', t6.menu_hint);
        check('the real menu step is the 4573 checkpoint with no client target',
            t6.step_checkpoint === 4573 && t6.hint_target === null,
            JSON.stringify([t6.step_checkpoint, t6.hint_target]));
        check('the real menu step also names the window it waits for',
            t6.open_window === 'characteristics', t6.open_window);
    }
    // The step after it (dialog 25463) waits for the stat window, opened by the
    // "+" cell of a characteristic row.
    const realStatWindow = bundleStep('id:25463');
    if (realStatWindow) {
        const t7 = run(realStatWindow);
        check('the real stat-window step publishes the window it waits for',
            t7.open_window === 'characUpdate', t7.open_window);
        check('the real stat-window step is a -1 sub-step',
            t7.step_checkpoint === -1, String(t7.step_checkpoint));
    }
    const realPlus = bundleStep('displayHighlightedSpellButtons(!0)');
    if (realPlus) {
        const t8 = run(realPlus);
        check('the real spell upgrade step is published as a spell-plus press',
            t8.hint_target === 'spell_plus', t8.hint_target);
        check('the real spell upgrade step is a -1 sub-step',
            t8.step_checkpoint === -1, String(t8.step_checkpoint));
    }
    // The profession tutorial: tap the workshop element, pick the menu action,
    // press the recipe and merge buttons, and drag an ingredient into the box.
    const realInteractive = bundleStep('playInteractiveElementTap(514876');
    if (realInteractive) {
        const t9 = run(realInteractive);
        check('the real workshop step publishes the map element to tap',
            t9.interactive_hint === 514876 && t9.hint_target === null,
            JSON.stringify([t9.interactive_hint, t9.hint_target]));
    }
    const realCtx = bundleStep('o.pointToInteractiveContextualMenuAction(0)');
    if (realCtx) {
        const t10 = run(realCtx);
        check('the real contextual-menu step publishes its action index',
            t10.hint_target === 'contextual_menu:0', t10.hint_target);
    }
    const realRecipe = bundleStep('pointToRecipeBtn()');
    if (realRecipe) {
        const t11 = run(realRecipe);
        check('the real recipe step publishes the craft button target',
            t11.hint_target === 'craft_recipe', t11.hint_target);
    }
    const realMerge = bundleStep('pointToMergeBtn()');
    if (realMerge) {
        const t12 = run(realMerge);
        check('the real merge step publishes the merge button target',
            t12.hint_target === 'craft_merge', t12.hint_target);
    }
    const realDrag = bundleStep('dragDropItemSlotToCraftActorBox()');
    if (realDrag) {
        const t13 = run(realDrag);
        check('the real ingredient step publishes the drag target',
            t13.hint_target === 'craft_drag', t13.hint_target);
    }
    // The trade tutorial: NPC menu action, NPC inventory slot, validate.
    const realNpcMenu = bundleStep('pointToInteractiveNPCContextualMenuAction(0)');
    if (realNpcMenu) {
        const t14 = run(realNpcMenu);
        check('the real NPC menu step publishes its action index',
            t14.hint_target === 'npc_menu:0', t14.hint_target);
    }
    const realTradeSlot = bundleStep('pointToFirstTradeNPCInventorySlot()');
    if (realTradeSlot) {
        const t15 = run(realTradeSlot);
        check('the real trade slot step publishes the slot target',
            t15.hint_target === 'trade_slot', t15.hint_target);
    }
    const realValidate = bundleStep('pointToNPCTradeValidateBtn()');
    if (realValidate) {
        const t16 = run(realValidate);
        check('the real trade validate step publishes the confirm target',
            t16.hint_target === 'trade_validate', t16.hint_target);
    }
    // The bestiary tutorial: monster, then its drop row.
    const realMonster = bundleStep('pointToFirstNormalBestiaryMonsterWithSpecificDrop(C)');
    if (realMonster) {
        const t17 = run(realMonster);
        check('the real bestiary monster step publishes the monster target',
            t17.hint_target === 'bestiary_monster', t17.hint_target);
    }
    const realDrop = bundleStep('pointToFirstDropBestiaryMonsterWithSpecificDrop(C)');
    if (realDrop) {
        const t18 = run(realDrop);
        check('the real bestiary drop step publishes the drop target',
            t18.hint_target === 'bestiary_drop', t18.hint_target);
    }
    // The job tutorial ends on closing the book (pointToWindowCloseButton).
    const realClose = bundleStep('pointToWindowCloseButton("grimoire")');
    if (realClose) {
        const t19 = run(realClose);
        check('the real close-button step publishes a close: target',
            /^close:[A-Za-z]+$/.test(String(t19.hint_target)), t19.hint_target);
    }
} else {
    console.log('  [SKIP] client bundle not present; real-step canary not run');
}

// No scenario at all: the block is absent, which the planner reads as "no
// tutorial" rather than a step with empty fields.
const none = build({ gui: {} })();
check('without a scenario manager the snapshot is null', none === null, none);

console.log(fail ? 'FAILURES: ' + fail : 'tutorial hint suite: all checks passed');
process.exit(fail ? 1 : 0);
