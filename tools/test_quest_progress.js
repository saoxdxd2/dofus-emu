// Behavioural checks for the quest-progress read (KNOWN_GAPS G28).
//
// Why this exists: `objectives_completed` was 0 in all 14,899 recorded live rows across 38
// sessions and three logs, and `quests_active` was 0 in all 18,587 (quest, value) pairs -- for
// every quest, in every era of the corpus. Not because nothing was achieved: the character
// levelled 3 -> 4 and the ready list kept changing shape. Because the page built both numbers
// from `q.finishedCount`, and in the served bundle that property is written only by the
// QuestValidatedMessage handler -- after `removeActiveQuestById`, on the quest it is moving into
// the finished list, with the value 1. On an *active* quest it is undefined, `Number(undefined
// || 0)` is 0, and every progress signal downstream (the planner's step index, the report's
// `objectives +0`, the neural net's progress input) was reading a constant.
//
// The client measures an active quest a different way, in its own criterion code: the position
// of the live `stepId` inside `dbQuest.stepIds` (`o.dbQuest.stepIds.indexOf(o.stepId)`), and
// `stepId` is written by the QuestStepInfo/QuestStepStarted/step-validated handlers. Quests that
// did finish are counted from `getListOfFinishedQuestsId()`. This suite runs `_questProgress`
// against stub quests and pins the snapshot block to that source.

const fs = require('fs');
const path = require('path');

let pass = 0, fail = 0;
function check(name, ok, detail) {
    if (ok) { console.log('  [PASS] ' + name); pass++; }
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const src = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'mobile-disguise.js'), 'utf8');
const H_START = 'var _questProgress = function (q) {';
const H_END = 'var qs = { active: {},';
const start = src.indexOf(H_START);
const afterHelper = src.indexOf(H_END, start);
check('the progress helper is still where the snapshot block expects it',
    start >= 0, H_START);
check('the helper ends before the quest block starts', afterHelper > start, H_END);
if (start < 0 || afterHelper <= start) {
    console.log('\n' + pass + ' passed, ' + fail + ' failed');
    process.exit(1);
}

const questProgress = new Function(src.slice(start, afterHelper) + '\nreturn _questProgress;')();

const p = questProgress({stepId: 4587, dbQuest: {stepIds: [4535, 4539, 4587, 4974]}});
check('a step in the middle of the quest is position 2, not the first step',
    p.index === 2 && p.step_id === 4587, JSON.stringify(p));

check('the very first step is index 0',
    questProgress({stepId: 4535, dbQuest: {stepIds: [4535, 4539]}}).index === 0);

check('an unknown step id does not invent a position',
    questProgress({stepId: 9999, dbQuest: {stepIds: [4535, 4539]}}).index === 0,
    JSON.stringify(questProgress({stepId: 9999, dbQuest: {stepIds: [4535, 4539]}})));

const strIds = questProgress({stepId: '4539', dbQuest: {stepIds: [4535, 4539, 4587]}});
check('a string step id still matches numeric step ids',
    strIds.index === 1 && strIds.step_id === 4539, JSON.stringify(strIds));

check('a quest with no dbQuest still reports its live step id',
    questProgress({stepId: 4587}).index === 0
    && questProgress({stepId: 4587}).step_id === 4587,
    JSON.stringify(questProgress({stepId: 4587})));

check('a stepIds table that is not an array is not walked',
    questProgress({stepId: 4587, dbQuest: {stepIds: 'nope'}}).index === 0);

check('no quest object does not throw',
    questProgress(null).index === 0 && questProgress(undefined).step_id === 0);

check('a step id of 0 is treated as no step',
    questProgress({stepId: 0, dbQuest: {stepIds: [0, 4539]}}).index === 0);

// The dead field. It is a real name in the bundle (so a bundle-membership guard cannot catch
// this) and it is non-zero on the *finished* list, which is exactly why reading it on an active
// quest looked plausible: a finished-shape object on the active list must still be measured by
// its step position.
const poisoned = questProgress({stepId: 4587, finishedCount: 5,
    dbQuest: {stepIds: [4535, 4539, 4587]}});
check('finishedCount is not consulted for an active quest',
    poisoned.index === 2, JSON.stringify(poisoned));

// -- the snapshot block that consumes it ---------------------------------------------------
const BLOCK_END = 'snap.objectives_completed = qs.completed;';
const blockStart = afterHelper;
const blockEnd = src.indexOf(BLOCK_END, blockStart);
check('the quest block still ends where the counter is emitted', blockEnd > blockStart, BLOCK_END);
const block = src.slice(blockStart, blockEnd + BLOCK_END.length);

check('the block takes the step position from the helper',
    block.indexOf('var prog = _questProgress(q);') >= 0);
check('the per-quest value the planner indexes with is that position',
    block.indexOf('qs.active[qid] = prog.index;') >= 0);
check('the session counter is the sum of real positions',
    block.indexOf('qs.completed += prog.index;') >= 0);
check('the current objective carries the same position it was planned from',
    block.indexOf('{ quest_id: qid, step: prog.index,') >= 0);
check('the raw step id is emitted, so a wrong derivation is visible in the log',
    block.indexOf('snap.quest_steps = qs.steps;') >= 0);
check('finished quests are counted from the client list that can grow',
    block.indexOf('snap.quests_finished = qs.finished;') >= 0
    && block.indexOf('getListOfFinishedQuestsId') >= 0);
check('the count is the length of that list, not a constant',
    block.indexOf('qs.finished = fq.length;') >= 0);
check('a client without the getter is asked for it before it is called',
    block.indexOf("typeof sq.getListOfFinishedQuestsId === 'function'") >= 0);
check('the dead field is gone from the whole progress block',
    block.indexOf('finishedCount') < 0, 'finishedCount came back');

// -- bundle membership ---------------------------------------------------------------------
const bundlePath = path.join(__dirname, '..', 'desktop', 'client', 'www', 'build', 'script.js');
if (fs.existsSync(bundlePath)) {
    const bundle = fs.readFileSync(bundlePath, 'utf8');
    ['stepId', 'stepIds', 'dbQuest', 'dbObjectives', 'objectiveStatus', 'objectiveId',
        'getListOfActiveQuestsId', 'getActiveQuestById', 'getListOfFinishedQuestsId',
        'createFinishedQuestsMap'].forEach(function (name) {
        check('the client has ' + name, bundle.indexOf(name) >= 0);
    });
    // Positive proof of the premise. `setFinishedCountById` has exactly one definition and one
    // caller, and that caller is the QuestValidatedMessage handler, which writes 1 *after*
    // `removeActiveQuestById` -- so the property exists only on the finished list, is a
    // whole-quest flag rather than a step count, and is undefined on every active quest.
    const setDefs = bundle.split('setFinishedCountById=function').length - 1;
    const setAll = bundle.split('setFinishedCountById').length - 1;
    check('the dead field has one definition and exactly one caller',
        setDefs === 1 && setAll === 2, setDefs + ' definitions, ' + setAll + ' occurrences');
    const validate = bundle.indexOf('QuestValidatedMessage');
    const writeSite = bundle.indexOf('setFinishedCountById(t.questId,1)');
    check('that caller runs when a quest is completed, not while it is active',
        writeSite > 0 && validate > 0
        && bundle.lastIndexOf('removeActiveQuestById', writeSite) > writeSite - 400
        && bundle.slice(writeSite - 60, writeSite + 200).indexOf('addFinishedQuest') >= 0,
        'writeSite=' + writeSite + ' validate=' + validate);
    check('the finished list is what the completion counter is read from',
        bundle.indexOf('getListOfFinishedQuestsId=function') > 0);
} else {
    check('the served bundle is present to check names against', false, bundlePath);
}

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
