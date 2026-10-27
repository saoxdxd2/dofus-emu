// Behavioural checks for the live quest CATALOGUE read (KNOWN_GAPS G29).
//
// Why this exists: the downloaded quest cache holds ids 18…2,546; the live game's quests
// start at 3,841, so the knowledge store has never described a quest the character is
// doing. The client itself holds the graph — each active quest carries dbQuest (stepIds),
// dbSteps (objectiveIds, nameId, descriptionId) and dbObjectives (typeId/_type,
// parameters, mapId, stepId, text). This suite runs the real builder against an object
// shaped like those rows, so a read that drops the type/params (the part the planner
// needs), loses the async-only objectives, or grows unbounded fails here.

const fs = require('fs');
const path = require('path');

let pass = 0, fail = 0;
function check(name, ok, detail) {
    if (ok) { console.log('  [PASS] ' + name); pass++; }
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const src = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'mobile-disguise.js'), 'utf8');
const bundlePath = path.join(__dirname, '..', 'desktop', 'client', 'www', 'build', 'script.js');
const bundle = fs.existsSync(bundlePath) ? fs.readFileSync(bundlePath, 'utf8') : null;

const START = 'var _objParams = function (raw) {';
const END = 'var qs = { active: {}, steps: {}, current: null, ready: [],';
const start = src.indexOf(START);
const end = src.indexOf(END, start);
check('the objective helpers and the catalogue builder are one block',
    start >= 0 && end > start, START);
if (start < 0 || end <= start) {
    console.log('\n' + pass + ' passed, ' + fail + ' failed');
    process.exit(1);
}
const block = src.slice(start, end);
const api = new Function(block + '\nreturn {_agentQuestCatalog: _agentQuestCatalog,'
    + ' _questProgress: _questProgress, _objMeta: _objMeta, _objParams: _objParams};')();

function extractBlock(anchor) {
    const at = src.indexOf(anchor);
    if (at < 0) return null;
    let depth = 0;
    for (let i = src.indexOf('{', at); i < src.length; i++) {
        if (src[i] === '{') depth++;
        else if (src[i] === '}') { depth--; if (depth === 0) return src.slice(at, i + 1); }
    }
    return null;
}

// ---- what the client bundle declares --------------------------------------------
if (bundle) {
    ['dbQuest', 'dbSteps', 'dbObjectives', 'objectiveIds', 'descriptionId',
     'stepIds'].forEach(function (name) {
        check('the client bundle still declares ' + name, bundle.indexOf(name) >= 0, name);
    });
} else {
    check('client bundle present to read names from', false, bundlePath);
}

// ---- a quest shaped like the client's own rows ----------------------------------
function makeQuest(over) {
    over = over || {};
    return {
        stepId: 2002,
        dbQuest: {nameId: 'La quête du pêcheur', stepIds: [2001, 2002, 2003]},
        dbSteps: {
            2001: {nameId: 'Étape 1', descriptionId: 'Parle au pêcheur.',
                   objectiveIds: [14070]},
            2002: {nameId: 'Étape 2', descriptionId: 'Équipe-toi.',
                   objectiveIds: [14071, 14072]},
            2003: {nameId: 'Étape 3', objectiveIds: [14073]}
        },
        dbObjectives: {
            14070: {typeId: 1, _type: 'QuestObjectiveGoToNpc', parameters: [3888],
                    mapId: 123, stepId: 2001, text: 'Parler à Ayin Spyr'},
            14071: {typeId: 0, _type: 'QuestObjectiveFreeForm', parameters: [],
                    mapId: 0, stepId: 2002, text: "S'équiper"},
            14072: {typeId: 4, _type: 'QuestObjectiveFightMonster',
                    parameters: [4194, 3], mapId: 124, stepId: 2002},
            // Filled asynchronously from the game database; no step lists it yet.
            14999: {typeId: 6, _type: 'QuestObjectiveDiscoverMap', parameters: [125],
                    mapId: 125, stepId: 2009, text: 'Découvrir'}
        },
        ...over
    };
}

const prog = api._questProgress(makeQuest());
check('the current step is the position of the live stepId in stepIds',
    prog.step_id === 2002 && prog.index === 1, JSON.stringify(prog));

const cat = api._agentQuestCatalog(makeQuest(), 3841, prog);
check('the quest is keyed by the client id and keeps its display name',
    cat.id === 3841 && cat.name === 'La quête du pêcheur', JSON.stringify(cat.name));
check('the step order is the client\'s stepIds order',
    JSON.stringify(cat.step_ids) === JSON.stringify([2001, 2002, 2003])
    && cat.step_index === 1 && cat.step_id === 2002, JSON.stringify(cat.step_ids));
check('a step carries the client\'s name, description and objective ids',
    cat.steps[0].name === 'Étape 1' && cat.steps[0].description === 'Parle au pêcheur.'
    && JSON.stringify(cat.steps[0].objectives.map(o => o.id)) === JSON.stringify([14070]),
    JSON.stringify(cat.steps[0]));
const npc = (cat.steps[0].objectives || [])[0] || {};
check('an objective carries the class name and type id the planner translates',
    npc.type === 'QuestObjectiveGoToNpc' && npc.type_id === 1, JSON.stringify(npc));
check('and its declared parameters, map and step',
    (npc.params || {}).parameter0 === 3888 && npc.map_id === 123 && npc.step_id === 2001,
    JSON.stringify(npc));
check('the objective text the ranker reads travels with the row',
    npc.text === 'Parler à Ayin Spyr', JSON.stringify(npc.text));
const fight = (cat.steps[1].objectives || [])[1] || {};
check('both declared parameters survive on a fight objective',
    (fight.params || {}).parameter0 === 4194 && (fight.params || {}).parameter1 === 3,
    JSON.stringify(fight.params));

// ---- rows the step graph does not list yet ---------------------------------------
const unlisted = cat.steps.filter(s => s.unlisted)[0];
check('an objective the client holds but no step lists is kept',
    !!unlisted && unlisted.objectives.length === 1
    && unlisted.objectives[0].id === 14999
    && unlisted.objectives[0].step_id === 2009, JSON.stringify(unlisted));

// ---- bounds: an observation per step cannot carry the whole game ------------------
const wideQuest = makeQuest({
    dbQuest: {stepIds: Array.from({length: 100}, (_, i) => 3000 + i)},
    dbSteps: Object.fromEntries(Array.from({length: 100}, (_, i) =>
        [3000 + i, {objectiveIds: Array.from({length: 100}, (_, j) => 40000 + i * 100 + j)}]))
});
const wide = api._agentQuestCatalog(wideQuest, 7, {index: 0, step_id: 3000});
check('the step list is bounded and says it was truncated',
    wide.step_ids.length === 40 && wide.truncated === true, wide.step_ids.length);
check('each step\'s objective list is bounded too',
    wide.steps[0].objectives.length <= 40, wide.steps[0].objectives.length);

const manyRows = makeQuest({
    dbQuest: {stepIds: [1]},
    dbSteps: {1: {objectiveIds: [1]}},
    dbObjectives: Object.fromEntries(Array.from({length: 50}, (_, i) => [500 + i, {typeId: 1}]))
});
const many = api._agentQuestCatalog(manyRows, 8, {index: 0, step_id: 1});
const extra = many.steps.filter(s => s.unlisted)[0];
check('the async-only rows are bounded as well, and the truncation is reported',
    !!extra && extra.objectives.length === 20 && many.truncated === true,
    extra ? extra.objectives.length : 'no unlisted step');

// A per-quest limit is not enough: 12 quests x 40 steps x 40 objectives is 19,200
// rows in every observation. The caller's shared budget must cap the whole graph.
const shared = {steps: 3, objectives: 4, truncated: false};
const capped = api._agentQuestCatalog(wideQuest, 7, {index: 0, step_id: 3000}, shared);
const cappedObjectives = capped.steps.reduce(
    (n, s) => n + (s.objectives || []).length, 0);
check('a shared budget caps the whole graph, not just each list',
    capped.steps.length <= 3 && cappedObjectives <= 4
    && capped.truncated === true && shared.truncated === true,
    JSON.stringify({steps: capped.steps.length, objectives: cappedObjectives,
                    shared: shared}));

const stepWide = makeQuest({
    dbQuest: {stepIds: Array.from({length: 100}, (_, i) => 3000 + i)},
    dbSteps: Object.fromEntries(Array.from({length: 100}, (_, i) =>
        [3000 + i, {objectiveIds: [40000 + i]}])),
    dbObjectives: {}
});
const stepBudget = {steps: 3, objectives: 10, truncated: false};
const stepCat = api._agentQuestCatalog(stepWide, 9, {index: 0, step_id: 3000},
                                       stepBudget);
check('a step budget truncates even when every shown step fits its objective budget',
    stepCat.steps.length === 3 && stepCat.truncated === true
    && stepBudget.truncated === true,
    JSON.stringify({steps: stepCat.steps.length, budget: stepBudget}));

// ---- fail-soft: a broken quest must not take the snapshot down -------------------
const broken = api._agentQuestCatalog({
    get dbQuest() { throw new Error('client table exploded'); }
}, 9, {index: 0, step_id: 0});
check('a throwing quest yields an error row, not an exception',
    broken.id === 9 && /exploded/.test(String(broken.error))
    && Array.isArray(broken.steps), JSON.stringify(broken));

// ---- the snapshot loop actually builds the catalogue ------------------------------
const pushBlock = extractBlock('if (qs.catalog.length < 12) {');
check('the snapshot has a catalogue build block', !!pushBlock);
const runPush = pushBlock ? new Function('qs', 'q', 'qid', 'prog',
    '_agentQuestCatalog', 'catalogBudget', pushBlock) : null;
const freshBudget = () => ({steps: 60, objectives: 80, truncated: false});
const fresh = {catalog: [], catalog_status: {quests: 0, steps: 0, objectives: 0,
                                             truncated: false}};
if (runPush) runPush(fresh, makeQuest(), 3841, prog, api._agentQuestCatalog,
                     freshBudget());
check('the snapshot builds and counts one catalogue entry per active quest',
    fresh.catalog.length === 1 && fresh.catalog_status.quests === 1
    && fresh.catalog_status.objectives === 5, JSON.stringify(fresh.catalog_status));
const full = {catalog: new Array(12).fill({}), catalog_status: {quests: 12, steps: 0,
                                                               objectives: 0, truncated: false}};
if (runPush) runPush(full, makeQuest(), 3841, prog, api._agentQuestCatalog,
                     freshBudget());
check('and stops at the bound instead of growing on every step',
    full.catalog.length === 12, full.catalog.length);
const tight = {catalog: [], catalog_status: {quests: 0, steps: 0, objectives: 0,
                                             truncated: false}};
const tightBudget = {steps: 1, objectives: 1, truncated: false};
if (runPush) runPush(tight, makeQuest(), 3841, prog, api._agentQuestCatalog,
                     tightBudget);
check('the snapshot shares one budget across the whole catalogue',
    tight.catalog.length === 1 && tight.catalog_status.objectives <= 1
    && tight.catalog_status.truncated === true,
    JSON.stringify(tight.catalog_status));

// ---- it stays a read, and the snapshot publishes it -------------------------------
check('the catalogue block sends nothing',
    block.indexOf('_cmSend(') === -1 && block.indexOf('sendMessage(') === -1
    && block.indexOf('.close(') === -1, 'a send call appeared in the capture');
check('the snapshot publishes the catalogue and its census',
    src.indexOf('snap.quests_catalog = qs.catalog;') > 0
    && src.indexOf('snap.quest_catalog_status = qs.catalog_status;') > 0);
check('the snapshot loop passes the shared budget into the builder',
    src.indexOf('_agentQuestCatalog(q, qid, prog,') > 0
    && src.indexOf('catalogBudget') > 0);
check('the census counts what the capture actually produced',
    src.indexOf('qs.catalog_status.objectives +=') > 0
    && src.indexOf('qs.catalog_status.truncated = true;') > 0);

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
