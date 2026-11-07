/* The page's own asset references: every local script must be cache-busted, and busted correctly.

Why: Chromium caches `js/compat-shim.js` forever when the tag has no `?v=`, and
`ClientBundle.RewriteCacheBusters` only rewrites `?v=` that is already there. So a shim added
without a stamp keeps working in a fresh profile and silently runs a stale copy on every machine
that loaded the page once -- which is how an hour of live diagnosis ended up reading a report
string that no longer existed in the file. The class is "the artifact changed, the page kept the
old bytes", and the only honest witness is the stamp matching the file it names.

  node tools/test_page_assets.js
*/
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

let pass = 0, fail = 0;
function check(name, ok, detail) {
    if (ok) { pass++; console.log('  [PASS] ' + name); }
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const www = path.join(__dirname, '..', 'desktop', 'client', 'www');
const htmlPath = path.join(www, 'standalone.html');
const html = fs.readFileSync(htmlPath, 'utf8');

// Every local script and stylesheet the page loads, with whatever query it carries.
const refs = [];
// The path part is greedy up to a `?`, so a query cannot be swallowed by a short match: with
// `[^"]+?` here every reference reported "no query" and the check passed on the wrong evidence.
const re = /(?:src|href)="((?!https?:|data:)[^"?]+)(\?([^"]*))?"/g;
let m;
while ((m = re.exec(html)) !== null) {
    const file = m[1];
    if (!/\.(js|css)$/i.test(file)) continue;
    // Group 3 is the query WITHOUT its leading `?`, so a buster reads `v=<hex>` here. Getting this
    // backwards reports every reference as unburst -- a check that fails on its own bookkeeping
    // rather than on the page is worse than no check.
    refs.push({ file: file, query: m[3] || '' });
}
check('the page references local assets worth checking', refs.length >= 4, refs.length);

const unburst = refs.filter((r) => !/^v=/.test(r.query));
check('every local script and stylesheet carries ?v=', unburst.length === 0,
    unburst.map((r) => r.file));

// One stamp per FILE is the point: a page that pastes the bundle's hash onto every reference keeps
// a cached shim alive after the shim is fixed, which is how three relaunches on 2026-11-04 tested
// code that was not running. So each reference is checked against the file IT names.
function sha10(rel) {
    const abs = path.join(www, rel.replace(/\//g, path.sep));
    if (!fs.existsSync(abs)) return null;
    return crypto.createHash('sha1').update(fs.readFileSync(abs)).digest('hex').slice(0, 10);
}
const wrong = [];
refs.forEach((r) => {
    const want = sha10(r.file);
    if (want === null) return;                       // reported by the existence check below
    const got = r.query.replace(/^v=/, '');
    if (got !== want) wrong.push(r.file + ' says ' + got + ', hashes to ' + want);
});
check('each stamp is the hash of the file it names', wrong.length === 0, wrong);
check('the bundle itself is stamped with its own hash',
    sha10('build/script.js') !== null
    && (refs.filter((r) => r.file === 'build/script.js')[0] || {}).query ===
       'v=' + sha10('build/script.js'),
    sha10('build/script.js'));

// A reference that 404s is a shim that never runs, and nothing downstream says so.
const missing = refs.filter((r) => !fs.existsSync(path.join(www, r.file.replace(/\//g, path.sep))));
check('every referenced asset exists on disk', missing.length === 0, missing.map((r) => r.file));

// The compat layer must load BEFORE the bundle: it traps the assignment of window.dofus, and a
// bundle that ran first would hand the game to the page unhooked.
const order = (html.match(/<script[^>]+src="([^"?]+)/g) || [])
    .map((s) => s.replace(/.*src="/, ''));
check('compat-shim loads before build/script.js',
    order.indexOf('js/compat-shim.js') >= 0 && order.indexOf('build/script.js') >= 0
    && order.indexOf('js/compat-shim.js') < order.indexOf('build/script.js'),
    order.join(' -> '));
check('the agent script is not double-loaded by the page',
    (html.match(/mobile-disguise/g) || []).length === 0
    || /<!-- The agent script is injected by the host/.test(html),
    'the page must not <script src> the agent the host already injects');

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
