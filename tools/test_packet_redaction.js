// The map-transition packet rings are persisted (autopilot jsonl, trajectories,
// training data) and they recorded every scalar packet argument. The login
// handshake's AuthenticationTicketMessage carries the live session ticket, so
// that value was written to disk verbatim. The snapshot now drops
// credential-shaped argument names at the source.
//
// This executes the real helper from scripts/mobile-disguise.js, so a helper
// that exists but is not wired into the ring, or one that redacts the wrong
// names, fails here instead of passing a grep.

const fs = require('fs');
const path = require('path');

let pass = 0, fail = 0;
function check(name, ok, detail) {
    if (ok) { console.log('  [PASS] ' + name); pass++; }
    else { console.error('  [FAIL] ' + name + (detail ? ': ' + detail : '')); fail++; }
}

const pagePath = path.join(__dirname, '..', 'scripts', 'mobile-disguise.js');
const page = fs.readFileSync(pagePath, 'utf8');

const regexAt = page.indexOf('var _agentSecretPacketArg =');
const regexEnd = page.indexOf(';', regexAt);
const regexSrc = regexAt >= 0 && regexEnd > regexAt
    ? page.slice(regexAt, regexEnd + 1) : null;
check('the page declares the secret argument list', !!regexSrc, 'not found');

const fnAt = page.indexOf('var _agentPacketArgs = function (a) {');
let fnSrc = null;
if (fnAt >= 0) {
    let depth = 0;
    for (let i = fnAt; i < page.length; i++) {
        const c = page[i];
        if (c === '{') depth++;
        else if (c === '}') {
            depth--;
            if (depth === 0) { fnSrc = page.slice(fnAt, i + 2).replace(/;\s*$/, '') + ';'; break; }
        }
    }
}
check('the page declares the argument filter', !!fnSrc, 'not found');

let packetArgs = null;
if (regexSrc && fnSrc) {
    try {
        packetArgs = new Function(regexSrc + '\n' + fnSrc + '\nreturn _agentPacketArgs;')();
    } catch (e) {
        check('the argument filter loads', false, String(e));
    }
}
check('the argument filter loads', typeof packetArgs === 'function');

if (typeof packetArgs === 'function') {
    const ticket = packetArgs({ ticket: 'a'.repeat(32), lang: 'fr', serverId: 5 });
    check('the session ticket is never stored',
          !('ticket' in ticket) && ticket.lang === 'fr' && ticket.serverId === 5, ticket);
    const creds = packetArgs({
        password: 'x', pass: 'y', secret: 'z', token: 't',
        salt: 's', apikey: 'a', api_key: 'b', key: 'c'
    });
    check('every credential-shaped argument name is dropped',
          Object.keys(creds).length === 0, creds);
    const movement = packetArgs({ keyMovements: [1, 2, 3], valid: true, spellId: 0 });
    check('movement and cast fields survive',
          movement.keyMovements === 'array[3]' && movement.valid === true
          && movement.spellId === 0, movement);
    const map = packetArgs({ mapId: 145489920, quiet: false });
    check('a refused change still names its map',
          map.mapId === 145489920 && map.quiet === false, map);
    const long = packetArgs({ text: 'x'.repeat(80) });
    check('a long string is still bounded', long.text.length === 40, long.text && long.text.length);
    const many = {};
    for (let i = 0; i < 12; i++) many['k' + i] = i;
    check('at most eight fields are kept', Object.keys(packetArgs(many)).length === 8);
    check('a missing payload is an empty object',
          JSON.stringify(packetArgs(null)) === '{}' && JSON.stringify(packetArgs(undefined)) === '{}');
}

check('the outgoing ring stores only the filtered arguments',
      /args:\s*_agentPacketArgs\(arguments\[1\]\)/.test(page),
      'the wrapper does not use the filter');
const wrapperAt = page.indexOf('var origSend = cm.sendMessage;');
check('the filter is applied before the row is pushed',
      wrapperAt >= 0 && page.indexOf('_agentPacketArgs(arguments[1])', wrapperAt) > wrapperAt);

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
