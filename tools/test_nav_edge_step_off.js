// Leaving through a border cell the character is already standing on.
//
// Live 2026-10-29 (page build 2026-10-29bf), map 145490434 cells 535 and 21:
// twelve `nav.goto` calls ended with `change_requested_at_edge` and zero map
// changes, three repeats at each cell. The client fires a transition on the
// movement that *ends* on the border cell; a request sent while already there
// has no movement to finish. The page now steps one cell off and lets the
// client's own `gotoNeighbourMap` walk back on. This runs the real handler.

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
// The page schedules the walk back 700 ms after the step off; the suite runs it
// at once so the sequence is observable.
global.setTimeout = function (fn) { fn(); return 0; };

// The real helper from the page, so the suite tests the shipped neighbours and
// the walkability rule rather than a copy.
const helperBody = block('var _agentStepOffCell = function(cell, avoid)');
check('the step-off helper is present', !!helperBody);
const stepOff = helperBody
    ? vm.runInThisContext('(function(cell, avoid)' + helperBody + ')') : null;

const nav = vm.runInThisContext('(function(args)' + body + ')');

function run(opts, args) {
    const calls = [];
    global._agentStepOffCell = function (cell, avoid) {
        return stepOff ? stepOff(cell, avoid) : null;
    };
    global._agentMapDetails = function () {
        return {
            map_id: 100, cell: opts.cell, x: 0, y: 0,
            exits: opts.exits || [],
            neighbours: opts.neighbours || {},
            npcs: [], interactives: [], groups: [], transition: {},
        };
    };
    global._agentTutorial = function () { return opts.tutorial || null; };
    global._agentDoorFor = function () { return null; };
    global.isoEngine = {
        mapRenderer: {
            isWalkable: function (cell) {
                return (opts.walkable || []).indexOf(Number(cell)) >= 0;
            },
        },
        _requestMapChange: function (id, dir) { calls.push(['request', id, dir]); },
        gotoNeighbourMap: function (dir, cell) { calls.push(['walk', dir, cell]); },
        _movePlayerOnMap: opts.noMove ? undefined
            : function (cell, adjacent, cb) {
                calls.push(['move', cell]);
                if (cb) cb(null, cell);
                return cell;
            },
    };
    // Which border cell this map tried last, per "<map>:<direction>". Page state,
    // so the suite decides it; a null `__agentLastNav` means nothing was tried yet.
    global._agentEdgeTry = {};
    global.__agentLastNav = null;
    return { out: nav(args || {}), calls: calls };
}

const EXITS = [{ direction: 'left', map_id: 200, cells: [5, 9] }];
const NEIGHBOURS = { left: 200 };

// 1. Standing on the border cell (5 of [5, 9]): step off first, walk back on.
let r = run({ cell: 5, exits: EXITS, neighbours: NEIGHBOURS,
              walkable: [4, 6, 8] }, { direction: 'left' });
check('a character standing on the border cell steps off before the change',
      r.out.ok === true && r.calls.length === 2
      && r.calls[0][0] === 'move' && r.calls[0][1] === 4
      && r.calls[1][0] === 'walk' && r.calls[1][1] === 'left'
      && Number(r.calls[1][2]) === 5,
      JSON.stringify([r.out, r.calls]));
check('the direct request is not used when a step off is possible',
      r.calls.every(function (c) { return c[0] !== 'request'; }),
      JSON.stringify(r.calls));
check('the outcome names the step-off route',
      /step off/.test(String(r.out.data.via)), String(r.out.data.via));

// 2. The step off never lands on another exit cell: here 6 (right next to 5)
//    is itself a left border, and it is the only walkable neighbour, so there is
//    nowhere safe to step. The old direct request is kept instead of stepping
//    into a crossing that goes somewhere else.
const EDGE_PAIR = [{ direction: 'left', map_id: 200, cells: [5, 6] }];
r = run({ cell: 5, exits: EDGE_PAIR, neighbours: NEIGHBOURS, walkable: [6] },
        { direction: 'left' });
check('the step off never lands on another border cell',
      r.out.ok === true && r.calls.length === 1 && r.calls[0][0] === 'request',
      JSON.stringify([r.out, r.calls]));

// 3. No walkable cell beside it: the old direct request is still the answer.
r = run({ cell: 5, exits: EXITS, neighbours: NEIGHBOURS, walkable: [] },
        { direction: 'left' });
check('without a walkable step off the client request is kept',
      r.out.ok === true && r.calls.length === 1 && r.calls[0][0] === 'request',
      JSON.stringify([r.out, r.calls]));

// 4. A tutorial that pins cells is not moved off its gate.
r = run({ cell: 5, exits: EXITS, neighbours: NEIGHBOURS, walkable: [4],
          tutorial: { active: true, can_change_map: { left: true },
                      unblocked_cells: [5, 9] } },
        { direction: 'left' });
check('a cell-pinned tutorial keeps its direct request',
      r.out.ok === true && r.calls.length === 1 && r.calls[0][0] === 'request',
      JSON.stringify([r.out, r.calls]));

// 5. Walking to the border from elsewhere is unchanged: no step off, one walk.
r = run({ cell: 20, exits: EXITS, neighbours: NEIGHBOURS, walkable: [19] },
        { direction: 'left' });
check('a character away from the border still just walks there',
      r.out.ok === true && r.calls.length === 1 && r.calls[0][0] === 'walk'
      && Number(r.calls[0][2]) === 5, JSON.stringify([r.out, r.calls]));

// 6. A client without `_movePlayerOnMap` keeps the request.
r = run({ cell: 5, exits: EXITS, neighbours: NEIGHBOURS, walkable: [4],
          noMove: true }, { direction: 'left' });
check('a client without the move method keeps the request',
      r.out.ok === true && r.calls.length === 1 && r.calls[0][0] === 'request',
      JSON.stringify([r.out, r.calls]));

console.log(pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
