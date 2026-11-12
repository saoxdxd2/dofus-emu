// Walking beside a cell the client's own pathfinder refuses.
//
// Live 2026-11-11, map 145752833: the character stood on cell 242 and NPC 3912 on cell
// 269, two cells away. `quest.advance {npc_id: 3912}` answered
// `could not walk next to npc 3912: _movePlayerOnMap noPath:242:269` -- the client's
// "walk adjacent to this cell" form refused -- and the same call failed twelve times in
// one run. Cells 243/241/258/289 on the same map all take a walk, so the map is not the
// problem: the client's adjacency form is.
//
// `_agentWalkBeside` is the fallback: enumerate the target's neighbours, keep the walkable
// ones, try them nearest-first with the form that is verified to work (walk onto a free
// cell), and report which cells were refused. This runs the real function.

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

const body = block('var _agentWalkBeside = function(targetCell, onArrive) {');
check('the walk-beside helper is present', !!body);

if (body) {
    // The helper is page state, so the suite decides what the client says. `opts` per run:
    //   walkable: cells the renderer calls walkable
    //   refuse:   cells the engine refuses (cb(err) + null, as the real one does)
    //   mine:     the player's cell
    function make(walkable, refuse, mine, hasEngine, defer) {
        const moves = [];
        const arrived = [];
        const pending = [];
        global.window = global;
        global.isoEngine = hasEngine === false ? undefined : {
            mapRenderer: {
                isWalkable: function (cell) { return walkable.indexOf(Number(cell)) >= 0; },
            },
            actorManager: { userActor: { cellId: mine } },
            _movePlayerOnMap: function (cell, adjacent, cb) {
                moves.push([Number(cell), !!adjacent]);
                if (refuse.indexOf(Number(cell)) >= 0) {
                    if (cb) cb(new Error('_movePlayerOnMap noPath:' + mine + ':' + cell));
                    return null;
                }
                // The real client calls back on the server's confirmation, not here.
                if (defer) pending.push(function () { if (cb) cb(null, cell); });
                else if (cb) cb(null, cell);
                return cell;
            },
        };
        global.__agentWalkBeside = null;
        const fn = vm.runInThisContext('(function(targetCell, onArrive)' + body + ')');
        const out = fn(269, function (cell) { arrived.push(Number(cell)); });
        pending.forEach(function (fire) { fire(); });
        return { out: out, moves: moves, arrived: arrived,
                 report: global.__agentWalkBeside || {} };
    }

    // 1. A refused neighbour is followed by the next one, and the walk is a plain walk.
    //    The player is on 242, so the neighbours sort 254 (|12|), 255 (13), 256 (14), ...
    let r = make([268, 270, 255, 256, 254, 282, 283, 284], [254, 255], 242);
    check('a refused neighbour does not stop the search',
          r.out === 256 && r.moves.length === 3 && r.moves[2][0] === 256,
          JSON.stringify([r.out, r.moves]));
    check('the walk is the form that works: onto a free cell, not adjacent-to',
          r.moves.every(function (m) { return m[1] === false; }), JSON.stringify(r.moves));
    check('the arrival callback gets the cell that was walked to',
          r.arrived.length === 1 && r.arrived[0] === r.out, JSON.stringify(r.arrived));

    // 2. Nearest first, by cell index: on 242, 254 is the closest neighbour of 269.
    r = make([254, 282, 268, 270, 283, 284, 255, 256], [], 242);
    check('the nearest free neighbour is tried first',
          r.out === 254 && r.moves.length === 1 && r.moves[0][0] === 254,
          JSON.stringify([r.out, r.moves]));

    // 3. Cells the renderer says are not walkable are never tried at all.
    r = make([282], [], 242);
    check('only walkable neighbours are candidates',
          r.out === 282 && r.moves.length === 1, JSON.stringify([r.out, r.moves]));

    // 4. Everything refused: an honest null, and the report names what was tried.
    r = make([268, 270], [268, 270], 242);
    check('with every neighbour refused the helper returns null',
          r.out === null, JSON.stringify(r.out));
    check('and the report says which cells were refused',
          r.report.ok === false && r.report.tried.length === 2
          && r.report.candidates.length === 2, JSON.stringify(r.report));
    check('the arrival callback is never called on a refusal',
          r.arrived.length === 0, JSON.stringify(r.arrived));

    // 5. No client at all: null, not a throw.
    r = make([], [], 242, false);
    check('a page without the movement API returns null instead of throwing',
          r.out === null, JSON.stringify(r.out));

    // 7. The confirmation arrives after the function has returned, which is how the real
    //    client behaves. The cell handed to `onArrive` must still be the one walked to.
    r = make([268, 270, 282], [268], 242, true, true);
    check('a confirmation that arrives later still names the cell walked to',
          r.out === 270 && r.arrived.length === 1 && r.arrived[0] === 270,
          JSON.stringify([r.out, r.arrived, r.moves]));

    // 6. A neighbour occupied by the player is still fine to *walk to* only if free; the
    //    renderer decides, and the suite above already covers filtering. What must not
    //    happen is an infinite loop over eight candidates.
    r = make([], [], 242);
    check('an unreachable pocket costs at most one pass over its neighbours',
          r.out === null && r.moves.length === 0, JSON.stringify(r.moves));
}

console.log(pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
