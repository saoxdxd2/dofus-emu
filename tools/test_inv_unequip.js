// Taking an item off is the other half of equipping.
//
// Live 2026-10-29: an ObjectSetPositionMessage for the weapon slot was sent to a
// slot that already held a recruit weapon, and the server moved neither item
// (slot 1 still held gid 18844, the hammer stayed at position 63). A swap needs
// the slot freed first, and the tool had no way to say "remove" -- every path
// either equipped to a slot or fell back to ObjectUseMessage. The bundle's own
// remove sends ObjectSetPositionMessage with position -1; this runs the real
// `inv.equip` handler and checks which message each argument shape produces.

const fs = require('fs');
const path = require('path');
const vm = require('vm');

let pass = 0, fail = 0;
function check(name, ok, detail) {
    if (ok) { console.log('  [PASS] ' + name); pass++; }
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const src = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'mobile-disguise.js'), 'utf8');

function block(marker) {
    const at = src.indexOf(marker);
    if (at < 0) return null;
    const open = src.indexOf('{', at);
    let depth = 0;
    for (let i = open; i < src.length; i++) {
        if (src[i] === '{') depth++;
        else if (src[i] === '}') {
            depth--;
            if (depth === 0) return src.slice(open, i + 1);
        }
    }
    return null;
}

const body = block("'inv.equip': function(args) {");
check('the inv.equip handler is present', !!body);

if (body) {
    global._agentResult = function (ok, data, error) {
        const out = { ok: !!ok };
        if (data !== undefined) out.data = data;
        if (error) out.error = error;
        return out;
    };
    global._agentNum = function (v) {
        if (v === null || v === undefined || v === '') return null;
        const n = Number(v);
        return isFinite(n) ? n : null;
    };
    global.window = global;

    const equip = vm.runInThisContext('(function(args)' + body + ')');

    // One item in the bag, one worn in slot 1, both of the same type. The type
    // table declares slot 1, which is what the live belt template says.
    function makeBag(itemOverrides, sendHook) {
        const item = Object.assign({
            objectUID: 7,
            objectGID: 18844,
            position: 63,
            item: { type: { id: 7, category: 0, possiblePositions: [1] } },
        }, itemOverrides || {});
        const worn = {
            objectUID: 8,
            objectGID: 18844,
            position: 1,
            item: { type: { id: 7, category: 0, possiblePositions: [1] } },
        };
        const calls = [];
        const bag = {
            objects: [item],
            equippedItems: { 1: worn },
            equipItem: function (uid) {
                if (sendHook) sendHook(String(uid), calls);
            },
        };
        return { item: item, bag: bag, calls: calls };
    }

    function run(args, itemOverrides, sendHook) {
        const made = makeBag(itemOverrides, sendHook);
        global.gui = {
            playerData: {
                inventory: { objects: made.bag.objects, presets: null },
                inventoryManager: {
                    getCorrectInventoryByItemInstance: function () { return made.bag; },
                },
            },
        };
        const sent = [];
        global.dofus = {
            sendMessage: function (name, payload) {
                sent.push({ name: String(name), args: payload });
                return true;
            },
        };
        const out = equip(args || {});
        return { out: out, sent: sent, bag: made.bag, item: made.item };
    }

    // The remove is a position -1 on the same message, with quantity 1.
    let r = run({ object_uid: 7, remove: true });
    check('remove:true sends ObjectSetPositionMessage',
          r.sent.length === 1 && r.sent[0].name === 'ObjectSetPositionMessage',
          JSON.stringify(r.sent));
    check('the remove message carries position -1',
          r.sent.length === 1 && r.sent[0].args && r.sent[0].args.position === -1,
          JSON.stringify(r.sent));
    check('the remove message keeps the object uid and quantity 1',
          r.sent.length === 1 && r.sent[0].args
              && r.sent[0].args.objectUID === 7 && r.sent[0].args.quantity === 1,
          JSON.stringify(r.sent));
    check('the result reports the remove and the slot it targets',
          r.out.ok && r.out.data.remove === true && r.out.data.slot === -1,
          JSON.stringify(r.out));
    check('the result names the remove path',
          r.out.ok && String(r.out.data.via).indexOf('remove') !== -1,
          JSON.stringify(r.out.data));

    // slot:-1 and unequip:true are the same intent, spelled the other ways.
    r = run({ object_uid: 7, slot: -1 });
    check('slot:-1 removes instead of equipping into -1',
          r.sent.length === 1 && r.sent[0].args.position === -1 && r.out.data.remove === true,
          JSON.stringify(r.sent));
    r = run({ object_uid: 7, unequip: true });
    check('unequip:true removes as well',
          r.sent.length === 1 && r.sent[0].args.position === -1 && r.out.data.remove === true,
          JSON.stringify(r.sent));

    // The template fallback must not turn a remove back into an equip.
    r = run({ object_uid: 7, remove: true });
    check('the possiblePositions fallback is skipped for a remove',
          r.sent.length === 1 && r.sent[0].args.position === -1,
          JSON.stringify(r.sent));

    // A usable item (ObjectUseMessage) must not fire on a remove either.
    r = run({ object_uid: 7, remove: true }, {
        item: { type: { id: 25, category: 1, possiblePositions: [] }, usable: true },
    });
    check('a usable item removes rather than being used',
          r.sent.length === 1 && r.sent[0].name === 'ObjectSetPositionMessage'
              && r.sent[0].args.position === -1,
          JSON.stringify(r.sent));

    // Equipping is unchanged: the declared position is used when no slot is named.
    r = run({ object_uid: 7 });
    check('a plain equip still uses the declared position',
          r.sent.length === 1 && r.sent[0].args.position === 1 && !r.out.data.remove,
          JSON.stringify(r.sent));
    r = run({ object_uid: 7, slot: 6 });
    check('an explicit slot still wins for equipping',
          r.sent.length === 1 && r.sent[0].args.position === 6 && !r.out.data.remove,
          JSON.stringify(r.sent));

    // The client's own equipItem is tried first; when it sends the message the
    // page must not send a second one for the same click.
    r = run({ object_uid: 7 }, null, function (uid, calls) {
        calls.push(uid);
        global.dofus.sendMessage('ObjectSetPositionMessage',
            { objectUID: Number(uid), position: -1, quantity: 1 });
    });
    check('a client-side remove is not duplicated by the page',
          r.sent.length === 1 && r.sent[0].args.position === -1,
          JSON.stringify(r.sent));
}

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
