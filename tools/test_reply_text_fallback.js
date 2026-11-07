// A named reply whose page moved on.
//
// Live 2026-10-30, whole corpus: 40 `quest.advance` calls were refused with "no
// dialog reply matches the given text", and 37 of those were answered by a page
// offering exactly one reply -- the plan's text named the page before (a ~355 ms
// old observation, or a chain built from the quest table). One choice cannot be
// the wrong branch of several, so the page now answers it and reports both texts.
// A page offering several choices is still refused: there the live text is the
// only evidence of which is which.

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

const body = block('var _agentReplyPlan = function (args, resolved)');
check('the reply-plan helper is present', !!body);
const plan = body ? vm.runInThisContext('(function(args, resolved)' + body + ')') : null;

function run(args, resolved) {
    return plan ? plan(args, resolved) : { error: 'helper missing' };
}

const ONE = { asked_text: 'Ask what Ek\'s problem is.', live_text: 'Approve and go back and fight.' };
let r = run({ quest_id: 3841, reply: 0, reply_text: "Ask what Ek's problem is." },
            { hits: [], count: 1, texts: ['Approve and go back and fight.'] });
check('a page that moved on and offers one reply is answered, not refused',
      !r.error && r.want === 0 && r.fallback
      && r.fallback.asked_text === ONE.asked_text
      && r.fallback.live_text === ONE.live_text,
      JSON.stringify(r));

r = run({ quest_id: 3841, reply: 0, reply_text: 'Ask what Ek\'s problem is.' },
        { hits: [], count: 3, texts: ['Approve.', 'Apologise.', 'Leave.'] });
check('a page offering several replies is still refused',
      !!r.error && r.want === null && !r.fallback
      && r.data && r.data.reply_count === 3,
      JSON.stringify(r));
check('the refusal keeps the wording the corpus already counts',
      r.error === 'no dialog reply matches the given text', r.error);

r = run({ quest_id: 3841, reply: 0, reply_text: 'Oui.' },
        { hits: [0], count: 2, texts: ['Oui.', 'Non, merci.'] });
check('an exact match is sent by its index, with no substitution',
      !r.error && r.want === 0 && !r.fallback, JSON.stringify(r));

r = run({ quest_id: 3841, reply: 1, reply_text: 'Non, merci.' },
        { hits: [1], count: 2, texts: ['Oui.', 'Non, merci.'] });
check('a matching text and index agree on the second choice',
      !r.error && r.want === 1 && !r.fallback, JSON.stringify(r));

r = run({ quest_id: 3841, reply: 0, reply_text: 'Oui.' },
        { hits: [0, 2], count: 3, texts: ['Oui.', 'Non.', 'oui'] });
check('a text matching two replies is refused, with both candidates',
      !!r.error && /matches 2 replies/.test(r.error)
      && r.data && r.data.candidates.length === 2,
      JSON.stringify(r));

r = run({ quest_id: 3841, reply: 2, reply_text: 'Oui.' },
        { hits: [0], count: 3, texts: ['Oui.', 'Non.', 'Partir.'] });
check('an index that contradicts the text is refused',
      !!r.error && /different choices/.test(r.error)
      && r.data && r.data.reply_text_index === 0,
      JSON.stringify(r));

r = run({ quest_id: 3841, reply: 0 },
        { hits: [], count: 1, texts: ['Only reply.'] });
check('a call naming no text keeps its index and never substitutes',
      !r.error && r.want === 0 && !r.fallback, JSON.stringify(r));

r = run({ quest_id: 3841, reply: 0, reply_text: 'Anything.' },
        { hits: [], count: 0, texts: [] });
check('a page with no replies is refused, not answered blind',
      !!r.error && r.want === null && !r.fallback, JSON.stringify(r));

// The handler must route through the helper and report the substitution.
check('the handler resolves replies through the helper',
      src.indexOf('var plan = _agentReplyPlan(args, resolved);') >= 0
      && src.indexOf("'no dialog reply matches the given text');") < 0,
      'the inline refusal is still present');
check('the reply result reports which text was substituted',
      src.indexOf('text_fallback: textFallback') >= 0,
      'text_fallback is not reported');

console.log(pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
