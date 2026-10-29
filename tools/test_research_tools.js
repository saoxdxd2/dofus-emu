// Static contract checks for the 2026-10-27 research tools.
//
// The page is a 7k-line IIFE, so the handlers cannot be imported in isolation.
// This suite pins the handler names, aliases, packets and refusal paths so a
// later edit cannot delete a capability silently. Behaviour is exercised on a
// live client; this is the source-level guard.

const fs = require('fs');
const path = require('path');

let pass = 0, fail = 0;
function check(name, ok, detail) {
    if (ok) { console.log('  [PASS] ' + name); pass++; }
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const src = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'mobile-disguise.js'), 'utf8');

check('hdv.buy handler exists', src.indexOf("'hdv.buy': function(args)") >= 0);
check('inv.equip handler exists', src.indexOf("'inv.equip': function(args)") >= 0);
check('tap.interactive handler exists', src.indexOf("'tap.interactive': function(args)") >= 0);
check('hdv.prices handler exists', src.indexOf("'hdv.prices': function(args)") >= 0);

check('HDV_BUY alias exists', src.indexOf("'HDV_BUY': 'hdv.buy'") >= 0);
check('INV_EQUIP alias exists', src.indexOf("'INV_EQUIP': 'inv.equip'") >= 0);
check('INTERACTIVE alias exists', src.indexOf("'INTERACTIVE': 'tap.interactive'") >= 0);

check('buy uses the captured packet',
    src.indexOf("ExchangeBidHouseBuyMessage") >= 0
    && src.indexOf("uid: uid, qty: qty, price: price") >= 0);
check('buy refuses above max_price',
    src.indexOf('listing price is above max_price') >= 0);
check('buy resolves only cached listings',
    src.indexOf('found no cached listing at or below max_price') >= 0);
check('buy requires a bounded price',
    src.indexOf('needs max_price (or an explicit price)') >= 0);

check('equip uses InventoryPresetUseMessage',
    src.indexOf("InventoryPresetUseMessage', { presetId: chosen.preset_id }") >= 0);
check('equip uses the client equipItem path for a direct UID',
    src.indexOf('bag2.equipItem(directUid)') >= 0
    && src.indexOf("'object_uid_invalid'") >= 0
    && src.indexOf('sent nothing') >= 0);
check('equip returns the available presets',
    src.indexOf('presets: rows') >= 0);

check('interactive uses InteractiveUseRequestMessage',
    src.indexOf("InteractiveUseRequestMessage") >= 0
    && src.indexOf('elemId: target.elemId') >= 0);
check('interactive resolves from the live element list',
    src.indexOf('var list = _interactives();') >= 0);
check('interactive reports a missing skill uid',
    src.indexOf("reason: 'skill_uid_required'") >= 0);

check('the snapshot publishes cached market rows',
    src.indexOf('snap.prices = priceRows') >= 0);
check('NPC rows carry a name when the bundle exposes one',
    src.indexOf('name: typeof npcName === \'string\'') >= 0);
check('interactive rows carry a name when the bundle exposes one',
    src.indexOf('name: typeof elName === \'string\'') >= 0);

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
