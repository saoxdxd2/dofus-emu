// Guard: scripts/mobile-disguise.js hardcodes CSS selectors and data attributes
// that do not exist in the served client bundle (desktop/client/www/build/script.js).
// Every Ankama client update renames classes and these selectors break silently.
//
// Run: node tools/test_js_selector_hardcodes.js   (exit 0 = green)

const fs = require('fs');
const path = require('path');

const adapterPath = path.join(__dirname, '..', 'scripts', 'mobile-disguise.js');
const bundlePath = path.join(__dirname, '..', 'desktop', 'client', 'www', 'build', 'script.js');

const adapter = fs.readFileSync(adapterPath, 'utf8').replace(/\r\n/g, '\n');
const bundle = fs.existsSync(bundlePath)
    ? fs.readFileSync(bundlePath, 'utf8').replace(/\r\n/g, '\n')
    : '';

const failures = [];
function check(name, ok, detail) {
    console.log((ok ? 'ok   ' : 'FAIL ') + name);
    if (!ok) failures.push(name + (detail ? ': ' + detail : ''));
}

// Selectors the adapter relies on, extracted from the source.
const selectorLiterals = [
    '.pass-turn', '.btn-turn', '[data-action="pass"]', '.timeline-tur',
    '.join-fight', '.btn-join', '.sword-fight', '[data-action="join"]',
    '[data-spell-id="', '.spell-',
    '.hotbar-slot', '.spell-slot', '.slot-spell',
    '.fighter-enemy', '.target-cell', '[data-cell-',
    '[class*="menuIcon"]', '.addSignButton',
];
let foundCount = 0;
for (const sel of selectorLiterals) {
    if (adapter.includes(sel)) {
        foundCount++;
        // Check whether the selector (or its stem) appears in the served bundle.
        const stem = sel.replace(/\[.*$/,'').replace(/\.$/,'').slice(1); // strip leading dot / bracket
        const inBundle = stem && bundle.includes(stem);
        check(`selector ${sel} exists in served bundle`, inBundle,
            'hardcoded selector not found in bundle — breaks on client update');
    }
}
check('hardcoded selectors found in adapter', foundCount > 0,
    'no hardcoded selectors detected (unexpected)');

// The adapter falls back to these classes when the real ones are missing.
check('pass button selector hardcoded', /\.pass-turn|'\[data-action="pass"'\]/.test(adapter));
check('join button selector hardcoded', /\.join-fight|'\[data-action="join"'\]/.test(adapter));
check('spell slot selector hardcoded', /\.spell-slot|\.hotbar-slot/.test(adapter));
check('enemy/target selector hardcoded', /\.fighter-enemy|\.target-cell/.test(adapter));

if (failures.length) {
    console.error('\n' + failures.join('\n'));
    process.exit(1);
}
console.log('\nno JS selector hardcodes detected (unexpected — review needed)');