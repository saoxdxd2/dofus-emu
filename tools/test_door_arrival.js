// Learning the way out of a house from the way in.
//
// Live 2026-10-29: the loop entered house 146276352 from 145490435 and then spent
// a session repeating `nav.goto {map_id: 145490435}`, refused with "map 145490435
// is not adjacent to 146276352" (29 times). The door store only recorded the cell
// the character left *from*, so the reverse direction had nothing to walk onto.
// A transition lands the party on the door cell of the map it entered, so the
// landing cell is that map's way back. It is a guess: it is tagged `arrival`, it
// never replaces a walked door, and it is dropped when walking it never
// transitions. This runs the real store and the real 400 ms sampler.

const fs = require('fs');
const path = require('path');

let pass = 0, fail = 0;
function check(name, ok, detail) {
    if (ok) { console.log('  [PASS] ' + name); pass++; }
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const src = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'mobile-disguise.js'), 'utf8');
const start = src.indexOf("var _DOOR_STORE = 'dofusFarm.doorCells';");
// The page moved to one shared periodic ticker (2026-11-13): the old one-line
// `setInterval(_agentDoorTick, 400)` became a registration with a fallback. The
// extracted chunk has to end after that whole try/catch, whichever form ships,
// or `new Function` below sees an unclosed block.
const legacyMarker = 'try { window.setInterval(_agentDoorTick, 400); } catch (e) {}';
const sharedMarker = 'window.__agentRegisterPeriodicTick(_agentDoorTick);';
const closing = '} catch (e) {}';
const sharedAt = src.indexOf(sharedMarker, start);
const legacyAt = src.indexOf(legacyMarker, start);
let end = -1;
if (sharedAt > start) {
    const close = src.indexOf(closing, sharedAt);
    end = close > sharedAt ? close + closing.length : -1;
} else if (legacyAt > start) {
    end = legacyAt + legacyMarker.length;
}
check('the door store is present in the page', start >= 0 && end > start);
const chunk = start >= 0 && end > start ? src.slice(start, end) : '';
check('the door sampler registers through the shared ticker with a fallback',
      chunk.indexOf(sharedMarker) >= 0 && chunk.indexOf('window.setInterval(_agentDoorTick, 500)') >= 0);
check('the extracted chunk closes its try/catch', chunk.trimEnd().endsWith(closing));

function makeWorld(opts) {
    const store = {};
    let now = 1700000000000;
    const window = {
        localStorage: {
            getItem: (k) => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
            setItem: (k, v) => { store[k] = String(v); },
        },
        gui: { playerData: { position: opts.pos } },
        isoEngine: opts.iso || {},
        setInterval: () => 0,
    };
    const fakeDate = { now: () => now };
    const load = new Function('window', 'setInterval', 'Date', 'isFinite',
        chunk + '\nreturn { note: _agentDoorNote, forget: _agentDoorForget, ' +
        'list: _agentDoorList, forMap: _agentDoorFor, tick: _agentDoorTick, ' +
        'setHint: function (v) { _agentDoorHint = v; } };');
    const api = load(window, () => 0, fakeDate, isFinite);
    return { api, store, now: () => now, advance: (ms) => { now += ms; }, setPos: (p) => { window.gui.playerData.position = p; } };
}

// 1. Walked transition still records the leaving cell, and the arrival is recorded
//    in the reverse direction from the landing cell.
{
    const w = makeWorld({ pos: { mapId: 145490435, cell: 451 } });
    w.api.tick();
    w.setPos({ mapId: 146276352, cell: 346 });
    w.api.tick();
    check('leaving a map records the cell that performed the transition',
          w.api.forMap(145490435, 146276352) === 451,
          JSON.stringify(w.api.list(145490435)));
    check('entering a map records the landing cell as its way back',
          w.api.forMap(146276352, 145490435) === 346,
          JSON.stringify(w.api.list(146276352)));
}

// 2. A guess never outranks a cell known to have worked.
{
    const w = makeWorld({ pos: { mapId: 1, cell: 10 } });
    w.api.tick();
    w.api.note(1, 700, 2, 'arrival');
    w.api.note(1, 700, 2, 'walk');
    check('a walked door replaces an arrival guess on the same cell',
          w.api.forMap(1, 2) === 700 && w.api.list(1)[0].via === 'walk',
          JSON.stringify(w.api.list(1)));
    w.api.note(1, 800, 9, 'arrival');
    check('a second guess for the same destination does not replace the walked cell',
          w.api.forMap(1, 2) === 700 && w.api.list(1)[0].via === 'walk',
          JSON.stringify(w.api.list(1)));
    // The ordering that matters live: the walk out of a map is recorded first (the
    // character leaves), and the arrival guess for the *next* map lands afterwards.
    // A guess must never overwrite a cell already known to work, whatever
    // destination the guess names.
    w.api.note(1, 700, 3, 'arrival');
    const known = w.api.list(1).filter((d) => d.cell === 700)[0] || {};
    check('an arrival guess never overwrites a walked cell',
          Number(known.to_map) === 2 && known.via === 'walk',
          JSON.stringify(w.api.list(1)));
    check('a guess is still offered when no walked door exists',
          w.api.list(1).some((d) => d.cell === 800 && d.via === 'arrival'),
          JSON.stringify(w.api.list(1)));
}

// 3. A guess that is walked and never transitions is dropped; a walked door is not.
{
    const w = makeWorld({ pos: { mapId: 5, cell: 100 } });
    w.api.tick();
    w.api.note(5, 200, 9, 'arrival');
    w.api.note(5, 300, 8, 'walk');
    w.api.setHint({ at: w.now(), map: 5, cell: 200 });
    w.advance(7000);
    w.api.tick();
    check('a stale arrival guess is forgotten',
          !w.api.list(5).some((d) => d.cell === 200), JSON.stringify(w.api.list(5)));
    check('a walked door survives the same timeout',
          w.api.forMap(5, 8) === 300, JSON.stringify(w.api.list(5)));
}

// 4. Map 0 is "between maps", not a door.
{
    const w = makeWorld({ pos: { mapId: 0, cell: 5 } });
    w.api.tick();
    w.setPos({ mapId: 7, cell: 6 });
    w.api.tick();
    check('no door is learned from map 0',
          Object.keys(w.api.list(0)).length === 0 && w.api.list(7).length === 0,
          JSON.stringify(w.api.list(7)));
}

// 5. The hint names the cell that was walked, not the last idle cell.
{
    const w = makeWorld({ pos: { mapId: 11, cell: 40 } });
    w.api.tick();
    w.api.setHint({ at: w.now(), map: 11, cell: 555 });
    w.setPos({ mapId: 12, cell: 60 });
    w.api.tick();
    check('the walked hint cell is the door, not the cell stood on',
          w.api.forMap(11, 12) === 555, JSON.stringify(w.api.list(11)));
}

// 6. Known static interior doors are available when the door store is fresh/empty.
{
    const w = makeWorld({ pos: { mapId: 145490435, cell: 100 } });
    check('known door from 145490435 to 146276352 is cell 346 on fresh store',
          w.api.forMap(145490435, 146276352) === 346,
          JSON.stringify(w.api.list(145490435)));
    check('known door from 146276352 to 145490435 is cell 451 on fresh store',
          w.api.forMap(146276352, 145490435) === 451,
          JSON.stringify(w.api.list(146276352)));
}

console.log(pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
