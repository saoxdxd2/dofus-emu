// Tests for the NPC actor reader in scripts/mobile-disguise.js.
//
// The client constructs roleplay actors with `actorId` (the contextual id sent
// in NpcGenericActionRequestMessage) and stores the template id on `data.npcId`.
// The previous reader looked for `contextualId`, which does not exist, so the
// wire message carried no npcId and the server ignored every talk request. These
// cases pin the real shape.

const fs = require('fs');
const path = require('path');

const scriptPath = path.join(__dirname, '..', 'scripts', 'mobile-disguise.js');
const src = fs.readFileSync(scriptPath, 'utf8').replace(/\r\n/g, '\n');
const start = src.indexOf('var _listNpcActors');
const end = src.indexOf('var _agentTutorial', start);
if (start < 0 || end < 0) {
    console.error('cannot extract the NPC helpers from ' + scriptPath);
    process.exit(2);
}
const body = src.slice(start, end);
const makeHelpers = new Function('window',
    body + '\nreturn { list: _listNpcActors, find: _findNpcActor,'
        + ' adjacent: _cellsAdjacent, mapId: _currentMapId };');

function actor(actorId, cellId, npcId) {
    return {
        actorId: actorId,
        cellId: cellId,
        isNPC: () => npcId != null,
        data: npcId != null
            ? { npcId: npcId, contextualId: actorId, npcData: { id: npcId } }
            : { contextualId: actorId }
    };
}

const npc = actor(123456, 255, 3888);
const player = actor(42, 300, null);
const window = {
    actorManager: {
        actors: { 123456: npc, 42: player },
        userActor: player,
        getActorFromNpcId: id => (Number(id) === 3888 ? npc : null)
    },
    gui: { playerData: { position: { mapId: 147064834 } } },
    isoEngine: {}
};
const h = makeHelpers(window, { log: () => {}, warn: () => {}, error: () => {} });

const failures = [];
function check(name, got, want) {
    const ok = JSON.stringify(got) === JSON.stringify(want);
    console.log((ok ? 'ok   ' : 'FAIL ') + name);
    if (!ok) failures.push(name + ': got ' + JSON.stringify(got)
        + ', wanted ' + JSON.stringify(want));
}

check('lists only real NPC actors', h.list(), [
    { templateId: 3888, contextualId: 123456, cell: 255 }
]);
const found = h.find(3888);
check('resolves the actor id and cell', {
    templateId: found.actor.templateId,
    contextualId: found.actor.contextualId,
    cell: found.actor.cell
}, { templateId: 3888, contextualId: 123456, cell: 255 });
check('seen lists the template ids here', found.seen, [3888]);
check('a missing npc is reported as missing', h.find(9999).actor, null);
check('the map comes from playerData.position', h.mapId(), 147064834);

// Find a real neighbour with the same grid formula, then check the far cell.
function point(cell) {
    const t = cell % 14 - Math.floor(cell / 28);
    return { x: t + 19, y: t + Math.floor(cell / 14) };
}
const here = point(255);
let neighbour = null;
for (let c = 0; c < 500 && neighbour == null; c++) {
    const p = point(c);
    if (Math.max(Math.abs(p.x - here.x), Math.abs(p.y - here.y)) === 1) neighbour = c;
}
check('an adjacent cell is adjacent', h.adjacent(255, neighbour), true);
check('a distant cell is not', h.adjacent(255, 0), false);

if (failures.length) {
    console.error('\n' + failures.join('\n'));
    process.exit(1);
}
console.log('\nall npc actor cases passed');
