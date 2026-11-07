// Walk the real tutorial step arrays and classify each step by the event its
// endingCondition waits for and the action its executeStart points at. The point
// is to find a step whose only path is an event no tool produces.
//
// H (Albuera) is the onboarding every fresh guest walks, so an unresolved H step
// fails the run (and the gate). The later tutorials (Astrub, job, ...) are
// reported but do not fail yet: they are not reachable until a live character is
// sent there.
const fs = require('fs');
const path = require('path');

const bundle = fs.readFileSync(
    path.join(__dirname, '..', 'desktop', 'client', 'www', 'build', 'script.js'), 'utf8');
const tutorialsBlock = bundle.indexOf('U[h]={startingCondition');
const ARRAYS = ['H', 'F', 'k', 'B', 'P', 'W'];

const enums = new Proxy({}, { get: () => 0 });
const hints = new Proxy({}, { get: () => function () {} });
const wm = { getWindow: () => ({ changePosition: function () {}, openState: false }) };

function arrayFor(name) {
    const needle = name + '=[{behaviours:';
    const at = bundle.lastIndexOf(needle, tutorialsBlock);
    if (at < 0) return null;
    const begin = at + name.length + 1;
    let depth = 0, end = -1, inStr = null;
    for (let i = begin; i < bundle.length; i++) {
        const ch = bundle[i];
        if (inStr) { if (ch === '\\') i++; else if (ch === inStr) inStr = null; continue; }
        if (ch === '"' || ch === "'") { inStr = ch; continue; }
        if (ch === '[') depth++;
        else if (ch === ']') { depth--; if (depth === 0) { end = i; break; } }
    }
    if (end < 0) return null;
    try {
        return new Function('n', 'o', 'a', 'h', 'D',
            'return (' + bundle.slice(begin, end + 1) + ');')(enums, hints, wm, 0, []);
    } catch (e) {
        return { error: String(e) };
    }
}

// Events the FarmBot/planner already produces (or that are pure game progression:
// the client fires them when data arrives, with no press of its own).
const GAME = new Set([
    'MOVEMENT_CONFIRM', 'NPC_INTERACTION', 'CHANGE_MAP', 'MAP_LOADED',
    'FIGHTER_TURN', 'FIGHT_START', 'PLACEMENT_TAP', 'FIGHT_TAP', 'SPELL_SELECTED',
    'SPELL_CASTED', 'SPELL_CANCELED', 'FINISH_TURN', 'WEAPON_CHANGED',
    'NEW_QUEST_START', 'QUEST_ALREADY_STARTED', 'WINDOW_OPENED', 'WINDOW_CLOSED',
    'WINDOW_OPENING_FINISHED', 'SHOP_LOADED', 'FAKE_SHOP_BUY', 'CHARAC_UPDATE',
    'SHOW_CONFIRM_BUTTON', 'CONFIRM_UPGRADE_SPELL', 'INTERACTIVE_USED',
    'FORCE_NEXT_STEP', 'LEVEL_UP', 'TAP_ANYWHERE', 'FIGHT_PLACEMENT_POSITION',
    // Loaded-flavoured progress: the fake crafting storage and the bestiary's
    // first monster load as soon as their window opens, no press targeted.
    'FAKE_STORAGE_LOADED', 'BESTIARY_MONSTER_LOADED', 'BESTIARY_MONSTER_START_LOADING',
]);
// [executeStart needle, what the page does for it, implemented today]
const START_PATTERNS = [
    ['pointToChallenge', 'client_target challenge', true],
    ['pointToConfirmUpgradeSpell', 'client_target spell_upgrade_confirm', true],
    ['displayHighlightedSpellButtons', 'client_target spell_plus', true],
    ['characUpdate', 'client_target charac_update', true],
    ['pointToMenuIcon', 'menu press', true],
    ['pointToWindowCloseButton', 'client_target close:NAME', true],
    ['pointToFirstArticleShopButton', 'shop.buy', true],
    ['pointToHardBtn', 'shop.buy', true],
    ['playHintOnCell', 'cell walk', true],
    ['setUnblockedCells', 'cell walk', true],
    ['playInteractiveElementTap', 'tap.interactive element id', true],
    ['pointToInteractiveNPCContextualMenuAction', 'client_target npc_menu:N', true],
    ['pointToInteractiveContextualMenuAction', 'client_target contextual_menu:N', true],
    ['pointToFirstNormalBestiaryMonsterWithSpecificDrop', 'client_target bestiary_monster', true],
    ['pointToFirstDropBestiaryMonsterWithSpecificDrop', 'client_target bestiary_drop', true],
    ['pointToFirstTradeNPCInventorySlot', 'client_target trade_slot', true],
    ['pointToNPCTradeValidateBtn', 'client_target trade_validate', true],
    ['pointToFirstCraftItemSlot', 'client_target craft_slot', true],
    ['pointToRecipeBtn', 'client_target craft_recipe', true],
    ['pointToMergeBtn', 'client_target craft_merge', true],
    ['pointToShopCategory', 'client_target shop_category:N', true],
    ['pointToShopItem', 'client_target shop_item:N', true],
    ['pointToInfoBtnSellShop', 'client_target sell_info', true],
    ['npcContextualMenuOnlyKeep', 'context menu close', true],
    ['dragDropItemSlotToCraftActorBox', 'client_target craft_drag', true],
    // Recognized but with no page action yet: the timeline/sell-mode presses
    // have no object path implemented. Steps whose only path is one of these
    // stay on the unresolved list so the audit tells the truth.
    ['pointToTimelineButton', 'fight timeline press (unimplemented)', false],
    ['pointToSellMod', 'sell mode press (unimplemented)', false],
    ['pointToFirstAvailable', 'shop article (unimplemented)', false],
];
// A step whose waits are all about a contextual menu / window is reached by
// interacting with the NPC or element it is about; when an earlier step locked
// one NPC (setUnblockedNpcId), tapping that NPC is the path even if the step's
// own executeStart carries no hint.
const MENU_EVENTS = new Set([
    'CONTEXTUAL_MENU_OPEN', 'CONTEXTUAL_MENU_CLOSE',
    'WINDOW_OPENING_FINISHED', 'WINDOW_CLOSED',
]);

// The events whose branch returns a positive delta (a real advance). A branch
// that only rewinds (WINDOW_CLOSED -> -2) is a guard, not a path: the shop chain
// uses those so closing a window early replays the step.
function forwardEvents(end) {
    const out = new Set();
    const re = /conditionTypeEnum\.([A-Z_]+)/g;
    let m;
    while ((m = re.exec(end))) {
        const rest = end.slice(m.index + m[0].length);
        const q = rest.match(/^[\s\S]{0,120}?\?\s*(-?\d+)/);
        if (q && Number(q[1]) > 0) out.add(m[1]);
    }
    return out;
}

function classify(steps) {
    const unresolved = [];
    let npcLocked = false;
    for (let i = 0; i < steps.length; i++) {
        const step = steps[i] || {};
        const start = String(step.executeStart || '');
        const end = String(step.endingCondition || '');
        const waited = [...new Set([...end.matchAll(/conditionTypeEnum\.([A-Z_]+)/g)]
            .map((m) => m[1]))];
        const forward = [...forwardEvents(end)];
        const known = START_PATTERNS.filter(([needle, , done]) =>
            done && start.indexOf(needle) !== -1);
        const pending = START_PATTERNS.filter(([needle, , done]) =>
            !done && start.indexOf(needle) !== -1);
        const lock = start.match(/setUnblockedNpcId\(([^)]*)\)/);
        if (lock) npcLocked = !/^(0|-1|null)$/.test(lock[1].trim());
        const tapsNpc = start.indexOf('playNPCTap(') !== -1
            || start.indexOf('setUnblockedNpcId(0)') !== -1;
        const menuOnly = waited.length > 0 && waited.every((w) => MENU_EVENTS.has(w));
        const npcPath = npcLocked && menuOnly && known.length === 0;
        // No forward branch at all: the step waits on the quest itself (its
        // condition only guards against a rewind), and the scenario follows the
        // quest's step when the server validates it.
        const stateDriven = forward.length === 0;
        // One advancing event the planner already produces (or data loading on
        // its own) is enough: the other forward events need not all be free.
        const gameOnly = forward.some((w) => GAME.has(w));
        const hasControl = known.length > 0 || tapsNpc || npcPath;
        if (!stateDriven && !gameOnly && !hasControl) {
            unresolved.push({ index: i, cp: step.questStepCheckpoint, waited,
                forward, controls: pending.map(([, label]) => label), npcLocked });
        }
    }
    return unresolved;
}

// argv: bare numbers dump those H steps; `k:10` dumps one step of any array.
const args = process.argv.slice(2);
const showH = args.filter((a) => /^\d+$/.test(a)).map(Number);
const showAny = args.filter((a) => /^[A-Za-z]+:\d+$/.test(a))
    .map((a) => a.split(':')).map(([n, i]) => [n, Number(i)]);
function dump(name, steps, idx) {
    const step = steps[idx] || {};
    console.log('  === ' + name + ' step ' + idx + ' cp ' + step.questStepCheckpoint);
    console.log('  start: ' + String(step.executeStart || '').replace(/\s+/g, ' ').slice(0, 420));
    console.log('  end  : ' + String(step.endingCondition || '').replace(/\s+/g, ' ').slice(0, 480));
}
let failed = false;
for (const name of ARRAYS) {
    const steps = arrayFor(name);
    if (!steps) { console.log(name + ': array not found'); if (name === 'H') failed = true; continue; }
    if (steps.error) { console.log(name + ': evaluation failed: ' + steps.error); if (name === 'H') failed = true; continue; }
    const unresolved = classify(steps);
    const tag = name === 'H' ? 'Albuera' : name;
    console.log(tag + ': ' + steps.length + ' steps, ' + unresolved.length + ' without a known path');
    for (const row of unresolved) {
        console.log('  step ' + row.index + ' cp ' + row.cp
            + ' waits [' + row.waited.join(',') + ']'
            + ' forward [' + row.forward.join(',') + ']'
            + ' controls [' + row.controls.join(',') + ']');
        if (name === 'H') failed = true;
    }
    if (name === 'H') for (const idx of showH) dump(name, steps, idx);
    for (const [n, idx] of showAny) if (n === name) dump(name, steps, idx);
}
process.exit(failed ? 1 : 0);
