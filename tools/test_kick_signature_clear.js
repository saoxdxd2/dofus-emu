// A seeded kick signature had no way to be cleared: the accepted-validation
// handler called __agentClearKick, but that function returned early whenever
// localStorage had no row -- which is exactly a fresh profile -- and it only
// ever deleted the learned row, never the seeded entry. One wrong seed would
// therefore disable an objective in every session, forever.
//
// This runs the real functions from scripts/mobile-disguise.js against a fake
// window, so the correction path is executed rather than grepped.

const fs = require('fs');
const path = require('path');

let pass = 0, fail = 0;
function check(name, ok, detail) {
    if (ok) { console.log('  [PASS] ' + name); pass++; }
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const pagePath = path.join(__dirname, '..', 'scripts', 'mobile-disguise.js');
const page = fs.readFileSync(pagePath, 'utf8');

function functionBody(marker) {
    const at = page.indexOf(marker);
    if (at < 0) return null;
    const open = page.indexOf('{', at);
    if (open < 0) return null;
    let depth = 0;
    for (let i = open; i < page.length; i++) {
        const c = page[i];
        if (c === '{') depth++;
        else if (c === '}') {
            depth--;
            if (depth === 0) return page.slice(at, i + 1);
        }
    }
    return null;
}

function constant(name) {
    const m = page.match(new RegExp('var ' + name + ' = [^;]+;'));
    return m ? m[0] : null;
}

const store = constant('_KICK_STORE');
const ttl = constant('_KICK_TTL_MS');
const knownFn = functionBody('var _knownKickSignatures = function() {');
const clearFn = functionBody('window.__agentClearKick = function(questId, objectiveId) {');
check('the page declares the kick store and its TTL', !!store && !!ttl, String(store));
check('the page declares the known-signature reader', !!knownFn);
check('the page declares the clear action', !!clearFn);

let api = null;
if (store && ttl && knownFn && clearFn) {
    try {
        api = new Function('window', 'console',
            [store, ttl, knownFn, clearFn].join('\n')
            + '\nreturn {known: _knownKickSignatures, clear: window.__agentClearKick};')(
                {__agentKnownKickSignatures: []}, {log: function () {}});
    } catch (e) {
        check('the kick store loads', false, String(e));
    }
}
check('the kick store loads', !!api && typeof api.known === 'function'
      && typeof api.clear === 'function');

function storage(initial) {
    const data = Object.assign({}, initial || {});
    return {
        getItem: function (k) {
            return Object.prototype.hasOwnProperty.call(data, k) ? data[k] : null;
        },
        setItem: function (k, v) { data[k] = String(v); },
        dump: function () { return data; }
    };
}

if (api) {
    function fresh(seeds, rows) {
        const win = {__agentKnownKickSignatures: (seeds || []).slice(),
                     localStorage: storage(rows && {dofus: rows})};
        // Re-run the page code against this window so the closure sees it.
        const f = new Function('window', 'console',
            [store, ttl, knownFn, clearFn].join('\n')
            + '\nreturn {known: _knownKickSignatures, clear: window.__agentClearKick};');
        return {win: win, api: f(win, {log: function () {}})};
    }

    const seeded = fresh(['3841/14070', '4262/13956']);
    check('a seeded signature is active',
          seeded.api.known()['3841/14070'] === true
          && seeded.api.known()['4262/13956'] === true, seeded.api.known());
    check('an accepted validation clears a seeded signature with no localStorage row',
          seeded.api.clear(3841, 14070) === true
          && seeded.api.known()['3841/14070'] !== true, seeded.win.__agentKnownKickSignatures);
    check('the other seeds are untouched',
          seeded.api.known()['4262/13956'] === true
          && seeded.win.__agentKnownKickSignatures.length === 1,
          seeded.win.__agentKnownKickSignatures);

    const learned = fresh([]);
    learned.win.localStorage.setItem('dofus.agent.unsafeClaims.v1',
        JSON.stringify({'4262/13948': {at: Date.now(), via: 'serverDisconnecting',
                                       reason: 'CONNECTION_LOST', count: 1}}));
    check('a learned signature is active',
          learned.api.known()['4262/13948'] === true, learned.api.known());
    check('an accepted validation clears the learned row and persists it',
          learned.api.clear(4262, 13948) === true
          && Object.keys(JSON.parse(
              learned.win.localStorage.getItem('dofus.agent.unsafeClaims.v1'))).length === 0,
          learned.win.localStorage.getItem('dofus.agent.unsafeClaims.v1'));

    const stale = fresh([]);
    stale.win.localStorage.setItem('dofus.agent.unsafeClaims.v1', JSON.stringify({
        '3841/13537': {at: Date.now() - 25 * 60 * 60 * 1000, via: 'x', count: 1},
        '3841/13538': {at: Date.now(), via: 'x', count: 1}
    }));
    check('a signature older than a day is forgotten, a fresh one is not',
          stale.api.known()['3841/13537'] !== true
          && stale.api.known()['3841/13538'] === true, stale.api.known());

    const unknown = fresh(['1/2']);
    check('clearing a signature nobody recorded changes nothing',
          unknown.api.clear(9, 9) === false
          && unknown.win.__agentKnownKickSignatures.length === 1);
    check('a missing pair is refused instead of clearing anything',
          unknown.api.clear(null, 5) === false && unknown.api.clear(5, null) === false);
}

check('the accepted-validation handler calls the clear action',
      /QuestObjectiveValidatedMessage['"]\s*,\s*function\s*\(msg\)\s*\{[\s\S]{0,200}__agentClearKick\(msg\.questId,\s*msg\.objectiveId\)/
          .test(page));

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
