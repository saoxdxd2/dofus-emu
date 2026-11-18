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

const body = block('_agentWalkBeside = function(targetCell, onArrive) {');
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

const navBody = block("'nav.goto': function(args) {");
const fightBody = block("'fight.join': function(args) {");
const directiveBody = block('window.__agentDirective = function(payload) {');
check('the live handlers and dispatcher are extractable', !!navBody && !!fightBody && !!directiveBody);
if (navBody && fightBody && directiveBody) {
    const callbacks = [], packets = [];
    const sandbox = {
        console: { log: function () {} }, Date: Date,
        _agentDirectiveAliases: {}, _agentDoorHint: null,
        _agentMapDetails: function () { return { map_id: 7, cell: 1, exits: [], doors: [] }; },
        _agentTutorial: function () { return null; },
        _agentLife: function () { return { alive: true }; },
        _agentResult: function (ok, data, error) { return { ok: ok, data: data, error: error }; },
        _cmSend: function (name, data) { packets.push([name, data]); return { ok: true }; }
    };
    sandbox.window = sandbox;
    sandbox.__agentObservationEpoch = 'page';
    sandbox.__agentEvidenceSequence = 0;
    const evidenceBody = block('const _agentInstallEvidence = function () {');
    check('the actual source-stamping installer is extractable', !!evidenceBody);
    if (!evidenceBody) process.exit(1);
    vm.runInNewContext('(function()' + evidenceBody + ')', sandbox)();
    sandbox.isoEngine = {
        actorManager: { actors: { 9: { actorId: 9, cellId: 42 } }, userActor: { cellId: 1 } },
        _movePlayerOnMap: function (cell, adjacent, cb) { callbacks.push(cb); return cell; }
    };
    sandbox._agentHandlers = {
        'nav.goto': vm.runInNewContext('(function(args)' + navBody + ')', sandbox),
        'fight.join': vm.runInNewContext('(function(args)' + fightBody + ')', sandbox)
    };
    const dispatch = vm.runInNewContext('(function(payload)' + directiveBody + ')', sandbox);
    const requested = { cell: 42, __action_id: 'forged' };
    dispatch({ id: 'A', tool: 'nav.goto', args: requested });
    check('the dispatcher binds the transport id without mutating caller arguments',
          sandbox.__agentLastNav.action_id === 'A' && requested.__action_id === 'forged');
    dispatch({ id: 'B', tool: 'nav.goto', args: { cell: 43 } });
    callbacks[0](new Error('A cannot walk'));
    check('a late callback keeps its own action id after another directive',
          sandbox.__agentLastNav.action_id === 'A' && sandbox.__agentLastNav.stage === 'walk_failed');
    callbacks[1](null, 43);
    check('server-confirmed arrival carries the matching id and cell',
          sandbox.__agentLastNav.action_id === 'B' && sandbox.__agentLastNav.stage === 'walk_confirmed'
          && sandbox.__agentLastNav.cell === 43);
    dispatch({ id: 'C', tool: 'fight.join', args: { group_id: 9 } });
    callbacks[2](new Error('group unreachable'));
    check('a failed fight approach does not send an attack',
          packets.length === 0 && sandbox.__agentLastFight.action_id === 'C'
          && sandbox.__agentLastFight.stage === 'walk_failed');
    dispatch({ id: 'D', tool: 'fight.join', args: { group_id: 9 } });
    callbacks[3](null, 42);
    check('a completed approach sends one attack and preserves the directive id',
          packets.length === 1 && packets[0][0] === 'GameRolePlayAttackMonsterRequestMessage'
          && sandbox.__agentLastFight.action_id === 'D' && sandbox.__agentLastFight.stage === 'attacked');
    dispatch({ id: 'E', tool: 'nav.goto', args: { cell: 44 } });
    callbacks[4](null, 45);
    check('a walk ending on the wrong cell is not a confirmed arrival',
          sandbox.__agentLastNav.action_id === 'E' && sandbox.__agentLastNav.stage === 'walk_stopped_short');
    const history = sandbox.__agentEvidenceLog;
    check('an overwritten callback remains in the evidence ring',
          history.some(e => e.action_id === 'A' && e.stage === 'walk_failed')
          && history.some(e => e.action_id === 'B' && e.stage === 'walk_confirmed'));
    check('source evidence gets independent monotonic identities',
          history.every((e, i) => e.source_epoch === 'page'
              && (i === 0 || history[i-1].source_sequence < e.source_sequence)));
    const retained = history[0];
    const retainedValue = JSON.stringify(retained);
    for (let i=0; i<80; i++) sandbox.__agentLastNav = {action_id:'bounded',stage:'walking'};
    check('the client history stays at its fixed retention bound', sandbox.__agentEvidenceLog.length === 64);
    check('future assignments cannot mutate retained event records', JSON.stringify(retained) === retainedValue);
}

console.log(pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
