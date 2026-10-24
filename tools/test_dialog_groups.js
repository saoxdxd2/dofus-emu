// Tests for the dialog and monster-group readers in scripts/mobile-disguise.js.
//
// Both are observation contracts: the policy can only understand a dialog if it
// sees the question and the reply texts, and it can only decide whether to fight
// if it sees the group's real id, cell, members and levels. These cases pin the
// client shapes the bundle actually uses, including the ones that made earlier
// readers return nothing.

const fs = require('fs');
const path = require('path');

const scriptPath = path.join(__dirname, '..', 'scripts', 'mobile-disguise.js');
const src = fs.readFileSync(scriptPath, 'utf8').replace(/\r\n/g, '\n');
const start = src.indexOf('var _agentPlainText');
const end = src.indexOf('var _agentTutorial', start);
if (start < 0 || end < 0) {
    console.error('cannot extract the dialog/group helpers from ' + scriptPath);
    process.exit(2);
}
const body = src.slice(start, end);
const makeHelpers = new Function('window',
    body + '\nreturn { plain: _agentPlainText, normalize: _agentNormalizeText,'
        + ' info: _agentDialogInfo, dialog: _agentDialogState,'
        + ' replyIndex: _agentReplyIndexForText, groups: _listMonsterGroups };');

function failure(name, got, want) {
    return name + ': got ' + JSON.stringify(got) + ', wanted ' + JSON.stringify(want);
}

const failures = [];
function check(name, got, want) {
    const ok = JSON.stringify(got) === JSON.stringify(want);
    console.log((ok ? 'ok   ' : 'FAIL ') + name);
    if (!ok) failures.push(failure(name, got, want));
}
function checkTrue(name, got) {
    console.log((got ? 'ok   ' : 'FAIL ') + name);
    if (!got) failures.push(name);
}

// 1. Text normalisation: the client formats dialog strings as HTML.
{
    const window = { gui: {} };
    const h = makeHelpers(window);
    check('strips markup and decodes entities',
        h.plain('<b>Bonjour</b>&nbsp;!<br>Ça va&nbsp;?'),
        'Bonjour ! Ça va ?');
    check('decodes numeric entities', h.plain('prix&#160;: 10'), 'prix : 10');
    check('normalises case and accents',
        h.normalize('  Oui,  je VEUX bien ! '), 'oui, je veux bien !');
}

// 2. A closed dialog is a small, explicit observation.
{
    const window = {
        gui: {
            playerData: { isInDialog: false },
            npcDialogUi: { currentMsg: 'stale', replies: ['stale'] },
            npcDialogHandler: { replyIds: [1] }
        }
    };
    const h = makeHelpers(window);
    check('closed dialog', h.dialog(), { open: false });
}

// 3. An open dialog: question text from the view, ids from the handler.
{
    const ui = {
        currentMsg: '<b>Que veux-tu&nbsp;?</b>',
        replies: ['Oui, je veux bien !', 'Non, merci.'],
        npcName: 'Mémé'
    };
    const handler = { replyIds: [42, 43], npcData: { id: 3888, nameId: 'Mémé' } };
    const window = {
        gui: {
            playerData: { isInDialog: true },
            npcDialogUi: ui,
            npcDialogHandler: handler
        }
    };
    const h = makeHelpers(window);
    check('open dialog state', h.dialog(), {
        open: true,
        npc_id: 3888,
        name_id: 'Mémé',
        message: 'Que veux-tu ?',
        replies: [
            { index: 0, text: 'Oui, je veux bien !', reply_id: 42 },
            { index: 1, text: 'Non, merci.', reply_id: 43 }
        ],
        reply_count: 2,
        ids_ready: true,
        text_ready: true
    });
    check('reply text resolves to its index', h.replyIndex('oui, je veux bien !'), {
        hits: [0], count: 2
    });
    check('a substring is not a match', h.replyIndex('Non'), { hits: [], count: 2 });
    check('an unknown reply has no match', h.replyIndex('Peut-être'), { hits: [], count: 2 });
}

// 4. The reply list can arrive before the ids (or vice versa). The observation
// must say which half is ready instead of pretending the other exists.
{
    const window = {
        gui: {
            playerData: { isInDialog: true },
            npcDialogUi: { currentMsg: 'Question', replies: ['Oui', 'Non'] },
            npcDialogHandler: { replyIds: null, npcData: { id: 7, nameId: 'Npc' } }
        }
    };
    const h = makeHelpers(window);
    const st = h.dialog();
    checkTrue('text ready while ids are not', st.text_ready === true && st.ids_ready === false);
    check('ids stay null', st.replies.map(r => r.reply_id), [null, null]);
}

// 5. Some builds keep replyIds on the view; accept that too.
{
    const window = {
        gui: {
            playerData: { isInDialog: true },
            npcDialogUi: { currentMsg: 'Q', replies: ['A'], replyIds: [99] },
            npcDialogHandler: null
        }
    };
    const h = makeHelpers(window);
    check('view-held ids', h.dialog().replies, [
        { index: 0, text: 'A', reply_id: 99 }
    ]);
}

// 6. The reply list is capped: an observation is not a transcript.
{
    const replies = [];
    const ids = [];
    for (let i = 0; i < 30; i++) { replies.push('r' + i); ids.push(i + 1); }
    const window = {
        gui: {
            playerData: { isInDialog: true },
            npcDialogUi: { currentMsg: 'Q', replies },
            npcDialogHandler: { replyIds: ids, npcData: { id: 1, nameId: 'N' } }
        }
    };
    const h = makeHelpers(window);
    const st = h.dialog();
    check('replies capped at 12', st.replies.length, 12);
    check('but the count stays honest', st.reply_count, 30);
}

// 7. Monster groups: the bundle's real shape is one main creature plus
// underlings, each with its own staticInfos carrying level and boss flags.
{
    const group = {
        actorId: 9001,
        cellId: 321,
        data: {
            type: 'GameRolePlayGroupMonsterInformations',
            scaleLevel: 4,
            staticInfos: {
                mainCreatureLightInfos: {
                    creatureGenericId: 447,
                    staticInfos: { level: 12, isBoss: true }
                },
                underlings: [
                    { creatureGenericId: 448, staticInfos: { level: 8 } },
                    { creatureGenericId: 448, staticInfos: { level: 7 } }
                ]
            }
        }
    };
    const npc = {
        actorId: 5, cellId: 10,
        data: { type: 'GameRolePlayNpcInformations', npcId: 3888 }
    };
    const window = {
        actorManager: { actors: { 9001: group, 5: npc } }
    };
    const h = makeHelpers(window);
    check('one group, not the npc', h.groups().length, 1);
    const g = h.groups()[0];
    check('group id from actorId', g.group_id, 9001);
    check('group cell', g.cell, 321);
    check('member ids', g.monster_ids, [447, 448, 448]);
    check('member levels', g.members.map(m => m.level), [12, 8, 7]);
    check('total level', g.total_level, 27);
    check('max level', g.max_level, 12);
    check('boss flag', g.is_boss, true);
    check('scale level', g.scale_level, 4);
}

// 8. A missing level must stay unknown, not become zero.
{
    const group = {
        actorId: 9002,
        cellId: 100,
        data: {
            type: 'GameRolePlayGroupMonsterInformations',
            staticInfos: {
                mainCreatureLightInfos: {
                    creatureGenericId: 10,
                    staticInfos: { level: 5 }
                },
                underlings: [
                    { creatureGenericId: 11, staticInfos: {} }
                ]
            }
        }
    };
    const window = { actorManager: { actors: { 9002: group } } };
    const h = makeHelpers(window);
    const g = h.groups()[0];
    check('partial levels are not summed', g.total_level, null);
    check('known level count', g.levels_known, 1);
    check('unknown level is null', g.members[1].level, null);
}

// 9. Legacy shape: a flat `monsters` list on staticInfos.
{
    const group = {
        actorId: 9003,
        cellId: 55,
        data: {
            type: 'GameRolePlayGroupMonsterInformations',
            staticInfos: { monsters: [{ creatureGenericId: 3, level: 2 }] }
        }
    };
    const window = { actorManager: { actors: { 9003: group } } };
    const h = makeHelpers(window);
    const g = h.groups()[0];
    check('legacy monster list', g.monster_ids, [3]);
    check('legacy level', g.total_level, 2);
}

// 10. Empty actor manager is an empty list, never a throw.
{
    const h = makeHelpers({ actorManager: null, isoEngine: null });
    check('no actors', h.groups(), []);
}

if (failures.length) {
    console.error('\n' + failures.join('\n'));
    process.exit(1);
}
console.log('\nall dialog and group cases passed');
