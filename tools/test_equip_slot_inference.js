// The equip verb's slot path, executed against a fake client.
//
// Live 2026-10-29: 76 refused `inv.equip` rows, every one saying "equipItem sent
// nothing and the item is not usable; pass slot to send ObjectSetPositionMessage
// directly". The item's type declared no `possiblePositions`, so the page had no
// slot to send -- while another item of the same type was already worn at a known
// position. That sibling is the client's own answer for where the type lives.
//
// This runs the real handler from scripts/mobile-disguise.js.

const fs = require('fs');
const path = require('path');

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
            if (depth === 0) return src.slice(at, i + 1);
        }
    }
    return null;
}

const resultFn = block('var _agentResult = function');
const numFn = block('var _agentNum = function');
const handler = block("'inv.equip': function(args) {");
check('the handler and its helpers are present', !!resultFn && !!numFn && !!handler);

let make = null;
if (resultFn && numFn && handler) {
    const code = [
        resultFn, numFn,
        'var _cmSend = function () { return {ok: false, error: "no connection manager"}; };',
        'var handlers = {' + handler + '};',
        'return handlers["inv.equip"];',
    ].join('\n');
    make = function (opts) {
        const sent = [];
        const bag = {
            objects: opts.objects || {},
            equippedItems: opts.equipped || {},
            equipItem: function () { /* silent, like the client's own gate */ },
        };
        opts.instances.forEach(function (it) {
            if (!(it.objectUID in bag.objects)) bag.objects[it.objectUID] = it;
        });
        const win = {
            gui: {playerData: {
                inventory: bag,
                inventoryManager: {
                    getCorrectInventoryByItemInstance: function () { return bag; },
                },
            }},
            dofus: {sendMessage: function (name, args) { sent.push([name, args]); }},
        };
        const fn = new Function('window', code)(win);
        const raw = fn(opts.args);
        return {result: JSON.parse(raw), sent: sent, bag: bag};
    };
}

function item(uid, typeId, extra) {
    return {objectUID: uid, objectGID: 900 + uid % 7, position: 63,
            item: Object.assign({id: typeId, type: {id: typeId}}, extra || {})};
}

if (make) {
    // 1) The live shape: no declared positions, a same-type sibling already worn.
    const wornRing = {item: {type: {id: 9}}, position: 7};
    const run = make({
        instances: [item(68720788, 9)],
        equipped: {7: wornRing},
        args: {object_uid: 68720788},
    });
    check('a type with no declared positions borrows the worn sibling\'s slot',
          run.sent.length === 1 && run.sent[0][0] === 'ObjectSetPositionMessage'
          && run.sent[0][1].position === 7
          && run.sent[0][1].objectUID === 68720788, run.sent);
    check('the result says it succeeded and names the slot',
          run.result.ok === true && run.result.data.slot === 7
          && run.result.data.via === 'ObjectSetPositionMessage', run.result);

    // 2) The declared-position path still wins when the type declares one.
    const declared = make({
        instances: [item(11, 42, {type: {id: 42, possiblePositions: [2]}})],
        equipped: {},
        args: {object_uid: 11},
    });
    check('a declared position is still used first',
          declared.sent.length === 1 && declared.sent[0][1].position === 2,
          declared.sent);

    // 3) An explicit slot from the caller is never overridden.
    const explicit = make({
        instances: [item(12, 9)],
        equipped: {7: wornRing},
        args: {object_uid: 12, slot: 5},
    });
    check('an explicit slot beats the sibling inference',
          explicit.sent.length === 1 && explicit.sent[0][1].position === 5,
          explicit.sent);

    // 4) Nothing to infer from: the refusal must still be honest and say why.
    const blocked = make({
        instances: [item(13, 77)],
        equipped: {4: {item: {type: {id: 3}}, position: 4}},
        args: {object_uid: 13},
    });
    check('no sibling and no declared position stays a refusal',
          blocked.sent.length === 0 && blocked.result.ok === false
          && /pass slot/.test(String(blocked.result.error)), blocked.result);
    check('and the refusal carries the fields that explain it',
          blocked.result.data && blocked.result.data.has_template === true
          && blocked.result.data.type_id === 77, blocked.result.data);
}

const autoPath = path.join(__dirname, '..', 'dofus-network', 'agent', 'autopilot.py');
const auto = fs.readFileSync(autoPath, 'utf8');
check('the loop keeps a refusal\'s own answer (evidence) for triage',
      auto.includes('_failure_evidence(result)')
      && auto.includes('evidence=(None if step_ok'));

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
