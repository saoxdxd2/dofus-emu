// The challenge message carries ids; every recorded fight had null names even
// though the client shows them. The bundle fills gui.challengeIndicator.challengesData
// in setChallenge (name/description/dropBonus/xpBonus/points/gfxId/categoryId), so the
// reader has to merge that panel instead of trusting the message alone. These checks
// run the extracted reader against a stub panel.
const fs = require('fs');
const path = require('path');

let fail = 0;
function check(name, ok, detail) {
    const good = !!ok;
    if (good) console.log('  [PASS] ' + name);
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const src = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'mobile-disguise.js'), 'utf8');

function extract(signature) {
    const start = src.indexOf(signature);
    if (start < 0) return null;
    const open = src.indexOf('{', start);
    let depth = 0, end = -1;
    for (let i = open; i < src.length; i++) {
        if (src[i] === '{') depth++;
        else if (src[i] === '}') { depth--; if (depth === 0) { end = i; break; } }
    }
    return end < 0 ? null : src.slice(start, end + 1);
}

const challengeSrc = extract('var _challenges = function()');
check('the challenge reader exists', !!challengeSrc, 'var _challenges not found');
check('the reader merges the client panel',
    /challengeIndicator/.test(src) && /challengesData/.test(src),
    'names live on gui.challengeIndicator.challengesData');
if (!challengeSrc) { console.log('FAILURES: ' + (fail || 1)); process.exit(1); }

const build = win => new Function('window', 'return (function(){' + challengeSrc
    + '; return _challenges; })();')(win);

// --- panel supplies what the message lacked
const win = {
    __agentChallenges: {
        40: { id: 40, name: null, description: null, success: true },
        366: { id: 366, success: false },
        500: { id: 500, name: 'Message Name', points: 9 }
    },
    gui: { challengeIndicator: { challengesData: {
        40: { name: 'Blitz', description: 'Kill in a single turn', points: 5,
              dropBonus: 100, xpBonus: 50, gfxId: 123, categoryId: 2,
              targetFighter: { id: -5 } },
        366: { name: 'Survivor', description: 'Nobody dies', points: 3 },
        500: { name: 'Panel Name', points: 1 }
    } } }
};
const rows = build(win)();
const byId = {};
for (const r of rows) byId[r.id] = r;
check('a panel name fills a missing message name', byId[40].name === 'Blitz',
    JSON.stringify(byId[40]));
check('the message name wins over the panel',
    byId[500].name === 'Message Name', JSON.stringify(byId[500]));
check('the description, points and bonuses come from the panel',
    byId[40].description === 'Kill in a single turn' && byId[40].points === 5
    && byId[40].drop_bonus === 100 && byId[40].xp_bonus === 50,
    JSON.stringify(byId[40]));
check('the challenge target fighter is carried',
    byId[40].target_id === -5, JSON.stringify(byId[40]));
check('the icon and category are carried',
    byId[40].gfx_id === 123 && byId[40].category_id === 2, JSON.stringify(byId[40]));
check('success survives the merge',
    byId[40].success === true && byId[366].success === false, JSON.stringify(byId[366]));
check('a challenge with no panel row is still reported',
    byId[366].name === 'Survivor' && !!byId[366], JSON.stringify(byId[366]));

// --- no panel at all: the reader still works, names honest nulls
const bare = build({ __agentChallenges: { 7: { id: 7, success: true } } })();
check('a missing panel keeps the reader working',
    bare.length === 1 && bare[0].id === 7 && bare[0].name === null,
    JSON.stringify(bare));

// --- no map: empty, not a throw
check('no challenges means an empty list, not an error',
    build({}).length === 0, 'reader must not throw or invent rows');

console.log(fail ? 'FAILURES: ' + fail : 'challenge suite: all checks passed');
process.exit(fail ? 1 : 0);
