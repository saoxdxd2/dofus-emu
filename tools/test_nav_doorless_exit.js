// Leaving a map whose exit the client does not publish.
//
// Live 2026-10-29: the character stood in the Albuera solo area, an interior map
// the client reports with four neighbours and zero change-map cells. Every plan
// died on "no exit to the <dir> on map <id>", the loop waited out its windows and
// the character never left. The client's own transition (`_requestMapChange`) is
// the same packet a tapped door sends, so the page can ask the server directly;
// the server still adjudicates. This runs the real `nav.goto` handler.

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

const body = block("'nav.goto': function(args) {");
check('the nav.goto handler is present', !!body);

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
    global._findNpcActor = function () { return null; };
    global.window = global;

    const nav = vm.runInThisContext('(function(args)' + body + ')');

    function run(opts, args) {
        const calls = [];
        global._agentMapDetails = function () {
            return {
                map_id: 100, cell: 5, x: 0, y: 0,
                exits: opts.exits || [],
                neighbours: opts.neighbours || {},
                npcs: [], interactives: [], groups: [], transition: {},
            };
        };
        global._agentTutorial = function () { return opts.tutorial || null; };
        // The door memory is page state; the suite decides what this map remembers.
        global._agentDoorFor = function (fromMap, toMap) {
            return opts.doorFor && Number(opts.doorFor.to) === Number(toMap)
                ? Number(opts.doorFor.cell) : null;
        };
        global.isoEngine = {
            _requestMapChange: function (id, dir) { calls.push(['request', id, dir]); },
            gotoNeighbourMap: function (dir, cell, x, y) { calls.push(['walk', dir, cell]); },
            _movePlayerOnMap: function (cell, adjacent, cb) {
                calls.push(['move', cell]);
                if (cb) cb(null, cell);
                return cell;
            },
        };
        global.__agentLastNav = null;
        return { out: nav(args || {}), calls: calls };
    }

    // 1. Direction with a known neighbour and no published exit cell: the client's
    //    own transition is asked, exactly as a tapped door would.
    let r = run({ neighbours: { left: 200 } }, { direction: 'left' });
    check('a doorless exit asks the client for the neighbour by direction',
          r.out.ok === true && r.calls.length === 1
          && r.calls[0][0] === 'request' && Number(r.calls[0][1]) === 200,
          JSON.stringify([r.out, r.calls]));

    // 2. Same by map id.
    r = run({ neighbours: { top: 300 } }, { map_id: 300 });
    check('a doorless exit asks the client for the neighbour by map id',
          r.out.ok === true && r.calls.length === 1
          && r.calls[0][0] === 'request' && Number(r.calls[0][1]) === 300,
          JSON.stringify([r.out, r.calls]));

    // 3. A map that is not a neighbour is still refused; no packet is invented.
    r = run({ neighbours: { left: 200 } }, { map_id: 999 });
    check('a non-adjacent map is still refused without a packet',
          r.out.ok === false && r.calls.length === 0,
          JSON.stringify([r.out, r.calls]));

    // 4. The tutorial gate outranks the fallback.
    r = run({ neighbours: { left: 200 },
              tutorial: { active: true, can_change_map: { left: false } } },
            { direction: 'left' });
    check('a tutorial that blocks the edge blocks the fallback too',
          r.out.ok === false && r.calls.length === 0,
          JSON.stringify([r.out, r.calls]));

    // 4b. Live 2026-10-29: a house exit is a painted cell, not a border flag.
    //     Once a transition has been seen from this map, the door cell is
    //     remembered and a non-neighbour target is reached by walking onto it.
    r = run({ neighbours: { left: 200 }, doorFor: { cell: 451, to: 145490435 } },
            { map_id: 145490435 });
    check('a remembered door cell is walked onto for a non-neighbour map',
          r.out.ok === true && r.calls.length === 1 && r.calls[0][0] === 'move'
          && Number(r.calls[0][1]) === 451 && /door/.test(String(r.out.data.via)),
          JSON.stringify([r.out, r.calls]));

    // 4c. A door for a different destination must not be used.
    r = run({ neighbours: { left: 200 }, doorFor: { cell: 451, to: 145490435 } },
            { map_id: 999 });
    check('a door for another map is not a shortcut to this one',
          r.out.ok === false && r.calls.length === 0,
          JSON.stringify([r.out, r.calls]));

    // 5. With a published exit cell the normal walk path is still used.
    r = run({ neighbours: { left: 200 },
              exits: [{ direction: 'left', map_id: 200, cells: [9] }] },
            { direction: 'left' });
    check('a published exit still walks instead of requesting',
          r.out.ok === true && r.calls.length === 1 && r.calls[0][0] === 'walk',
          JSON.stringify([r.out, r.calls]));
}

console.log(pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
