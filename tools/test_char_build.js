// Structural checks for the level-up housekeeping tool (char.build).
//
// A character leveled to 10 with 35 stat points and 8 spell points unspent: every
// fight ran at base stats and the bot never noticed, because nothing in the tool
// vocabulary owned the "+" button. These checks pin the four pieces that make the
// gap closed rather than merely handled once by hand: the handler, the element
// table it derives the stat from, the snapshot field that reveals the need, and
// the automated call site gated on the bot actually driving.

const fs = require('fs');
const path = require('path');

let pass = 0, fail = 0;
function check(name, ok, detail) {
    if (ok) { console.log('  [PASS] ' + name); pass++; }
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const root = path.join(__dirname, '..');
const src = fs.readFileSync(path.join(root, 'scripts', 'mobile-disguise.js'), 'utf8');
const caps = JSON.parse(fs.readFileSync(
    path.join(root, 'dofus-network', 'data', 'capabilities.json'), 'utf8'));

const start = src.indexOf("'char.build': function(args) {");
check('the char.build handler exists', start >= 0);
const block = start >= 0 ? src.slice(start, start + 9000) : '';

check('stat points are spent through the client message',
    block.indexOf("StatsUpgradeRequestMessage") >= 0, 'no StatsUpgradeRequestMessage');
check('spell ranks are requested through the client message',
    block.indexOf("SpellChangeRequestMessage") >= 0, 'no SpellChangeRequestMessage');
check('the element comes from the damage effect ids, not a class table',
    (function () {
        const ids = ['96', '97', '98', '99', '100'];
        return ids.every(function (id) {
            return new RegExp(id + ":\\s*'(water|earth|air|fire|neutral)'").test(block);
        });
    })(), 'missing one of 96-100');
check('each element maps to the stat id the + button sends',
    /water:\s*13/.test(block) && /earth:\s*10/.test(block)
    && /air:\s*14/.test(block) && /fire:\s*15/.test(block),
    'stat ids changed');
check('only castable/usable spells are preferred as the build target',
    block.indexOf('canCastThisSpell') >= 0 && block.indexOf('getSpellStatus') >= 0,
    'no usability bias');

check('the snapshot exposes unspent points',
    /snap\.points\s*=\s*\{[\s\S]{0,200}stats:/.test(src),
    'state.snapshot has no points block');

check('autoBuild is defined and refuses mid-fight or when idle',
    (function () {
        const at = src.indexOf('autoBuild: function() {');
        if (at < 0) return false;
        const body = src.slice(at, at + 1600);
        return body.indexOf('!this.enabled') >= 0
            && body.indexOf('this.state.inFight') >= 0
            && body.indexOf("_agentHandlers['char.build']") >= 0;
    })());

check('a refused spell rank is not re-asked at the same character level',
    (function () {
        const at = src.indexOf('autoBuild: function() {');
        if (at < 0) return false;
        const body = src.slice(at, at + 1600);
        return body.indexOf('_spellAsk') >= 0
            && body.indexOf('ask.level === level') >= 0
            && body.indexOf('spellAllowed') >= 0;
    })());

check('the keep-alive timer is the call site',
    (function () {
        const at = src.indexOf('self._keepAliveTimer = setInterval');
        if (at < 0) return false;
        return src.slice(at, at + 1600).indexOf('self.autoBuild()') >= 0;
    })());

check('char.build stays out of the fixed 27-tool vocabulary',
    caps.capabilities.filter(function (c) { return c.id === 'char.build'; }).length === 0
    && caps.capabilities.length === 28, String(caps.capabilities.length));
check('char.build is reachable through a directive alias',
    /'CHAR_BUILD': 'char\.build'/.test(src));

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
