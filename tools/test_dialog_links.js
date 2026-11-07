// Behavioural + fidelity checks for the dialog LINK read (KNOWN_GAPS G32).
//
// Why this exists: the loop has never accepted a quest. Measured over the trajectory corpus
// (`trajectories/default.jsonl` + its rotation, 9,036 rows that each carry the full
// `quests_active` log) with `dofus-network/tools/quest_advance_shapes.py`, which classifies a
// `quest.advance` row by its WHOLE argument set: **15 start-shaped requests (a quest id and nothing
// else), 0 honoured within the next five steps, and all 15 ids absent from the whole instance's
// active log.** An earlier pass read "carries a quest id" as "is a start" and reported 212; talk,
// reply and close rows carry one too, so that number was the quest.advance traffic, not accepts.
// The active log does move -- 37 times in the corpus -- but only at world entry (`state.snapshot`
// rows adding 4604/4278, 4644/4266, 3841), i.e. quests a person or the tutorial script had already
// taken.
//
// The reason is in the served bundle, and it is not the packet the loop was sending. The dialog
// view parses `/<a\shref=['"](.*?)['"]\s*>(.*?)<\/a>/gi` out of the question text and hands each
// href to `_linkHandler`, which splits on "," and knows exactly three payloads:
// `startquest,<questId>` -> `QuestStartRequestMessage` then close;
// `validateobjective,<questId>,<objectiveId>` -> `QuestObjectiveValidationMessage` then close;
// `map,<worldX>,<worldY>` -> a compass update.
// So an offer is a LINK inside the NPC's text. The page publishes `dialog.message` through
// `_agentPlainText`, which strips every tag -- the offer was being deleted before the policy ever
// saw it, and `quest.advance {quest_id}` was the guess that filled the gap.
//
// This suite pins two things: that the read parses what the CLIENT'S OWN regex parses (the
// regex is extracted from the bundle and its match count is compared per sample, so widening the
// page's pattern past the client's fails here), and that it is a READ -- nothing in this block
// sends a message.

const fs = require('fs');
const path = require('path');

let pass = 0, fail = 0;
function check(name, ok, detail) {
    if (ok) { console.log('  [PASS] ' + name); pass++; }
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const page = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'mobile-disguise.js'), 'utf8');
const bundlePath = path.join(__dirname, '..', 'desktop', 'client', 'www', 'build', 'script.js');
const bundle = fs.existsSync(bundlePath)
    ? fs.readFileSync(bundlePath, 'utf8') : null;

const START = 'var _agentDialogLinkPayloads = function (raw) {';
const END = 'var _agentDialogState = function () {';
const start = page.indexOf(START);
const end = page.indexOf(END, start);
check('the link helpers are still in one block before the dialog state',
    start >= 0 && end > start, START);
if (start < 0 || end <= start) {
    console.log('\n' + pass + ' passed, ' + fail + ' failed');
    process.exit(1);
}
const block = page.slice(start, end);
const api = new Function(block + '\nreturn {_agentDialogLinkPayloads: _agentDialogLinkPayloads,'
    + ' _agentDialogLinks: _agentDialogLinks,'
    + ' _agentStartQuestOffered: _agentStartQuestOffered,'
    + ' _agentLinkHandlerOwner: _agentLinkHandlerOwner};')();

// ---- what the client itself declares ------------------------------------
if (bundle) {
    ['startquest', 'validateobjective', 'QuestStartRequestMessage',
     'QuestObjectiveValidationMessage', '_linkHandler'].forEach(function (name) {
        check('the client bundle still declares ' + name, bundle.indexOf(name) >= 0, name);
    });
    const needle = '/<a\\shref=[\'"](.*?)[\'"]\\s*>(.*?)<\\/a>/gi';
    check('and the anchor pattern this read copies is the client\'s own',
        bundle.indexOf(needle) >= 0, needle);
    // The link grammar the client splits on, verbatim from the same class (the bundle is minified:
    // no space around `=`, so a needle with spaces would silently never match).
    const split = 'function(e){var t=e.split(",")';
    check('the handler splits the href on commas, as this read does',
        bundle.indexOf(split) >= 0, split);
    check('and the three payload names it recognises are the three this read knows',
        bundle.indexOf('h="startquest",f="validateobjective",m="map"') >= 0,
        'the client\'s own link-kind constants');
} else {
    check('client bundle present to read names from', false, bundlePath);
}

// ---- fidelity: the page must match exactly what the client's regex matches ----
const samples = [
    'Talk to me <a href="startquest,3841">about a job</a> please',
    'plain text with no link at all',
    'two links <a href="startquest,42">A</a> and <a href="validateobjective,42,13900">B</a>',
    "single quotes <a href='map,12,-34'>go</a>",
    'an href spaced before the close <a href="startquest,7" >x</a>',
    '<a class="q" href="startquest,9">attribute before href</a>',
    '<a href=startquest,11>unquoted href</a>'
];
const clientRe = /<a\shref=['"](.*?)['"]\s*>(.*?)<\/a>/gi;
samples.forEach(function (s, i) {
    const want = (s.match(clientRe) || []).length;
    const got = api._agentDialogLinkPayloads(s).length;
    check('sample ' + i + ': the read finds as many links as the client\'s own regex ('
        + want + ')', want === got, want + ' vs ' + got + ' in ' + s.slice(0, 48));
});

// ---- behaviour on the three payloads the client knows ----
const start1 = api._agentDialogLinkPayloads('x <a href="startquest,3841">take this</a>');
check('startquest yields kind + numeric quest id, not a string',
    start1.length === 1 && start1[0].kind === 'startquest' && start1[0].quest_id === 3841,
    JSON.stringify(start1));
const val1 = api._agentDialogLinkPayloads('<a href="validateobjective,4260,13900">v</a>');
check('validateobjective carries BOTH ids as numbers',
    val1[0].quest_id === 4260 && val1[0].objective_id === 13900, JSON.stringify(val1));
const map1 = api._agentDialogLinkPayloads('<a href="map,12,-34.9">m</a>');
check('map truncates coordinates the way the client does (~~)',
    map1[0].world_x === 12 && map1[0].world_y === -34, JSON.stringify(map1));
const other = api._agentDialogLinkPayloads('<a href="url,http://x">u</a>');
check('an unknown payload is published as other, never dropped',
    other[0].kind === 'other' && other[0].href.indexOf('url,') === 0, JSON.stringify(other));
check('no link is not an empty quest id',
    api._agentStartQuestOffered(api._agentDialogLinkPayloads('nothing here')) === null);

// ---- attribution and the offer the policy would act on ----
const info = {raw_msg: 'hello <a href="map,1,2">m</a>',
    texts: ['first reply', 'second <a href="startquest,4266">take</a>']};
const links = api._agentDialogLinks(info);
check('message links are attributed to the message, reply links to their index',
    links.length === 2 && links[0].from === 'message'
    && links[1].from === 'reply' && links[1].reply_index === 1, JSON.stringify(links));
check('the quest on offer is the startquest payload, wherever it appears',
    api._agentStartQuestOffered(links) === 4266, String(api._agentStartQuestOffered(links)));
check('a validateobjective link is never mistaken for an offer',
    api._agentStartQuestOffered(api._agentDialogLinkPayloads(
        '<a href="validateobjective,1,2">v</a>')) === null);
check('a zero or broken quest id is not an offer',
    api._agentStartQuestOffered(api._agentDialogLinkPayloads(
        '<a href="startquest,0">z</a>')) === null
    && api._agentStartQuestOffered(api._agentDialogLinkPayloads(
        '<a href="startquest,abc">z</a>')) === null);

// ---- which live object owns the client's handler, detected not assumed ----
check('the view is reported when it owns _linkHandler',
    api._agentLinkHandlerOwner({ui: {_linkHandler: function () {}}, handler: {}})
    === 'gui.npcDialogUi');
check('the handler is reported when only it owns _linkHandler',
    api._agentLinkHandlerOwner({ui: {}, handler: {_linkHandler: function () {}}})
    === 'gui.npcDialogHandler');
check('and null when neither does, rather than a guessed name',
    api._agentLinkHandlerOwner({ui: {}, handler: {}}) === null,
    String(api._agentLinkHandlerOwner({ui: {}, handler: {}})));

// ---- the read stays a read, and the ranking text is unchanged ----
check('the link block sends nothing',
    block.indexOf('sendMessage(') === -1 && block.indexOf('.close(') === -1,
    'a send call appeared in the read');
check('the published message is still the stripped text the ranker reads',
    page.indexOf('message: info.message,') > 0);
check('while the raw string is kept only for the link parse',
    page.indexOf('raw_msg: (ui && ui.currentMsg != null) ? ui.currentMsg : null,') > 0);
check('and the snapshot publishes the three new fields',
    page.indexOf('links: links,') > 0
    && page.indexOf('start_quest: _agentStartQuestOffered(links),') > 0
    && page.indexOf('link_handler: _agentLinkHandlerOwner(info)') > 0);

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
