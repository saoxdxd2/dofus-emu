// Guard: mobile-disguise.js must not hardcode the VirtualBox NAT IP 10.0.2.15.
// That address only exists inside VirtualBox NAT; on any other hypervisor or bare
// metal it is a fictitious local IP that gets scrubbed into WebRTC SDP and ICE
// candidates, leaking the wrong local address and breaking ICE gathering.
//
// Run: node tools/test_no_virtualbox_ip.js   (exit 0 = green)

const fs = require('fs');
const path = require('path');

const scriptPath = path.join(__dirname, '..', 'scripts', 'mobile-disguise.js');
const src = fs.readFileSync(scriptPath, 'utf8').replace(/\r\n/g, '\n');

const failures = [];
function check(name, ok, detail) {
    console.log((ok ? 'ok   ' : 'FAIL ') + name);
    if (!ok) failures.push(name + (detail ? ': ' + detail : ''));
}

check('no hardcoded VirtualBox NAT IP 10.0.2.15',
    !/10\.0\.2\.15/.test(src),
    src.match(/10\.0\.2\.15/g) ? 'found ' + src.match(/10\.0\.2\.15/g).length + ' occurrence(s)' : '');

if (failures.length) {
    console.error('\n' + failures.join('\n'));
    process.exit(1);
}
console.log('\nno hardcoded VirtualBox IP detected');