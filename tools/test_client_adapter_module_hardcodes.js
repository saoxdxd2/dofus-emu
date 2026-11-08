// Guard: scripts/mobile-disguise.js must not hardcode webpack module indices
// or bundle-internal method names. These break on every Ankama bundle update.
// (Known_GAPS.md G1: the keep-alive silently died because it named
// `dofus.connectionManager` — module 105 — instead of resolving the tracker by
// shape. module 64 is the tracker id this build; a client update renumbers it.)
//
// Run: node tools/test_client_adapter_module_hardcodes.js   (exit 0 = green)

const fs = require('fs');
const path = require('path');

const scriptPath = path.join(__dirname, '..', 'scripts', 'mobile-disguise.js');
const src = fs.readFileSync(scriptPath, 'utf8').replace(/\r\n/g, '\n');

const failures = [];
function check(name, ok, detail) {
    console.log((ok ? 'ok   ' : 'FAIL ') + name);
    if (!ok) failures.push(name + (detail ? ': ' + detail : ''));
}

// 1) No module-index hardcodes ("module 105", "module 64", etc.).
const moduleIndexHits = src.match(/\bmodule\s+\d+\b/gi);
check('no hardcoded webpack module index in the adapter',
    !moduleIndexHits || moduleIndexHits.length === 0,
    moduleIndexHits ? moduleIndexHits.join(', ') : '');

// 2) No bundle-internal method names baked as literals that the client can rename.
const internalNames = src.match(
    /\b(connectionManager|fightManager\.castSpell|dofus\.disconnect|_checkActivity|recordActivity)\b/g);
check('no bundle-internal method name hardcoded as a literal',
    !internalNames || internalNames.length === 0,
    internalNames ? internalNames.join(', ') : '');

// 3) The build stamp must not be a hand-edited literal that can go stale:
//    it should be derivable, but at minimum flag its presence for review.
const buildStamp = src.match(/var AGENT_BUILD\s*=\s*'([^']+)'/);
check('AGENT_BUILD stamp present and reviewable', buildStamp != null,
    buildStamp ? buildStamp[1] : 'missing');

// 4) The adapter must resolve the activity tracker by shape (setInterval third
//    argument is `this`), not by name — assert the capture pattern exists.
check('tracker is captured by shape via setInterval `this` argument',
    /setInterval\(function\s*\(e\)\s*\{\s*e\._checkActivity\s*\(\)/.test(src) ||
    /setInterval\(function\s*\(e\)[\s\S]{0,120}e\._checkActivity/.test(src),
    'no shape-based setInterval capture found');

// 5) No hardcoded Ankama login host in the adapter.
check('no hardcoded Ankama host in the adapter',
    !/dt-proxy-production-login\.ankama-games\.com|auth\.ankama\.com/.test(src));

if (failures.length) {
    console.error('\n' + failures.join('\n'));
    process.exit(1);
}
console.log('\nall client-adapter hardcode checks passed');