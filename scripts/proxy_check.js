// proxy_check.js - deep proxy verifier for the DofusFarm launch pool.
//
// Usage:
//   node scripts/proxy_check.js <input.txt> <output.txt> [paths] [timeoutMs]
//
// paths: comma-separated request paths tested THROUGH each proxy tunnel
//        (default "/build/primus.js,/config.json"). An entry is written to
//        <output.txt> only if EVERY path returns HTTP 200.
//
// The check performed per proxy:
//   TCP connect -> (SOCKS5 handshake | HTTP CONNECT) -> TLS -> GET path
// This mirrors what the game itself needs from dt-proxy-production-login.
// Only entries that pass are safe for proxies.txt - everything else causes
// the in-game "Server busy" fatal reload.

const fs = require('fs');
const path = require('path');
const { requestThrough } = require('./proxy_tunnel');

const TARGET_HOST = 'dt-proxy-production-login.ankama-games.com';
const TARGET_PORT = 443;
const UA = 'Mozilla/5.0 (Linux; Android 10; SM-A515F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.137 Mobile Safari/537.36';

const INPUT = process.argv[2] || 'proxies.txt';
const OUTPUT = process.argv[3] || 'proxies_ok.txt';
const PATHS = (process.argv[4] || '/build/primus.js,/config.json').split(',').map(s => s.trim()).filter(Boolean);
const TIMEOUT_MS = parseInt(process.argv[5] || '8000', 10);
const CONCURRENCY = 50;
const TUNNEL = { timeoutMs: TIMEOUT_MS, targetHost: TARGET_HOST, targetPort: TARGET_PORT, userAgent: UA };

function parseEntry(line) {
  let s = line.trim();
  if (!s || s.startsWith('#')) return null;
  let scheme = 'http';
  const i = s.indexOf('://');
  if (i > 0) { scheme = s.substring(0, i).toLowerCase(); s = s.substring(i + 3); }
  let userinfo = null, rest = s;
  const at = s.lastIndexOf('@');
  if (at >= 0) { userinfo = s.substring(0, at); rest = s.substring(at + 1); }
  const idx = rest.lastIndexOf(':');
  if (idx <= 0) return null;
  const host = rest.substring(0, idx);
  const port = parseInt(rest.substring(idx + 1), 10);
  if (!host || !port) return null;
  return { raw: line.trim(), scheme, host, port, userinfo };
}

// Tests all PATHS via separate tunnels; passes only when every path is 200.
function checkEntry(entry, cb) {
  const perPath = [];
  let i = 0;
  const next = () => {
    if (i >= PATHS.length) {
      entry.results = perPath;
      entry.ok = perPath.every(r => !r.err);
      cb(entry);
      return;
    }
    const p = PATHS[i++];
    requestThrough(entry, p, TUNNEL, (err) => { perPath.push({ path: p, err: err || null }); next(); });
  };
  next();
}

const lines = fs.readFileSync(INPUT, 'utf8').split(/\r?\n/);
const entries = [];
for (const l of lines) { const e = parseEntry(l); if (e) entries.push(e); }
console.log('Checking ' + entries.length + ' entries, paths: ' + PATHS.join(', ') + ' (timeout ' + TIMEOUT_MS + 'ms, ' + CONCURRENCY + ' concurrent)');

let idx = 0, doneCount = 0;
const good = [];
function worker() {
  if (idx >= entries.length) return;
  const e = entries[idx++];
  checkEntry(e, (res) => {
    doneCount++;
    if (res.ok) { good.push(res); console.log('  OK   ' + res.raw); }
    if (doneCount % 100 === 0) console.log('  ...' + doneCount + '/' + entries.length + ' (ok so far: ' + good.length + ')');
    worker();
  });
}
for (let i = 0; i < CONCURRENCY; i++) worker();

const waitFinish = setInterval(() => {
  if (doneCount >= entries.length) {
    clearInterval(waitFinish);
    console.log('\n=== ' + good.length + ' of ' + entries.length + ' pass all paths ===');
    fs.writeFileSync(OUTPUT, good.map(r => r.raw).join('\n') + (good.length ? '\n' : ''));
    console.log('Wrote ' + good.length + ' entries to ' + OUTPUT);
  }
}, 200);
