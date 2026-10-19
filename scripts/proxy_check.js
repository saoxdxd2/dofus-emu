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

const net = require('net');
const tls = require('tls');
const fs = require('fs');
const path = require('path');

const TARGET_HOST = 'dt-proxy-production-login.ankama-games.com';
const TARGET_PORT = 443;
const UA = 'Mozilla/5.0 (Linux; Android 10; SM-A515F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.137 Mobile Safari/537.36';

const INPUT = process.argv[2] || 'proxies.txt';
const OUTPUT = process.argv[3] || 'proxies_ok.txt';
const PATHS = (process.argv[4] || '/build/primus.js,/config.json').split(',').map(s => s.trim()).filter(Boolean);
const TIMEOUT_MS = parseInt(process.argv[5] || '8000', 10);
const CONCURRENCY = 50;

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

// Opens one tunnel through the proxy, then TLS + GET of one path.
function requestThrough(entry, reqPath, cb) {
  const sock = net.connect({ host: entry.host, port: entry.port });
  let done = false;
  const fail = (reason) => { if (done) return; done = true; try { sock.destroy(); } catch (e) {} cb(reason); };
  const timer = setTimeout(() => fail('timeout'), TIMEOUT_MS);

  const tlsFetch = () => {
    const t = tls.connect({ socket: sock, servername: TARGET_HOST, rejectUnauthorized: false }, () => {
      t.write('GET ' + reqPath + ' HTTP/1.1\r\nHost: ' + TARGET_HOST +
        '\r\nUser-Agent: ' + UA + '\r\nAccept: */*\r\nConnection: close\r\n\r\n');
    });
    let buf = '';
    t.on('data', (d) => { buf += d.toString('latin1'); if (buf.length > 8192) t.destroy(); });
    t.on('error', (e) => { clearTimeout(timer); fail('tls:' + (e.code || e.message)); });
    t.on('close', () => {
      clearTimeout(timer);
      const m = buf.match(/^HTTP\/1\.[01] (\d+)/);
      if (!m) return fail('no-http-response');
      if (m[1] !== '200') return fail('http ' + m[1]);
      done = true;
      cb(null);
    });
  };

  if (entry.scheme.startsWith('socks')) {
    let stage = 0;
    const wantAuth = !!entry.userinfo;
    sock.on('connect', () => sock.write(wantAuth ? Buffer.from([0x05, 0x02, 0x00, 0x02]) : Buffer.from([0x05, 0x01, 0x00])));
    sock.on('error', (e) => fail(e.code || e.message));
    sock.on('data', (d) => {
      try {
        if (stage === 0) {
          if (d.length < 2 || d[0] !== 0x05) return fail('socks bad-ver');
          if (d[1] === 0x00) { stage = 1; }
          else if (d[1] === 0x02 && wantAuth) {
            const parts = entry.userinfo.split(':');
            const u = Buffer.from(parts[0] || '', 'utf8'), p = Buffer.from(parts[1] || '', 'utf8');
            sock.write(Buffer.concat([Buffer.from([0x01, u.length]), u, Buffer.from([p.length]), p]));
            stage = 2; return;
          } else return fail('socks method ' + d[1]);
        } else if (stage === 2) {
          if (!(d.length >= 2 && d[1] === 0x00)) return fail('socks auth-fail');
          stage = 1;
        }
        if (stage === 1) {
          if (d.length >= 2 && d[1] === 0x00 && d[0] === 0x05) { sock.removeAllListeners('data'); return tlsFetch(); }
          const hostBuf = Buffer.from(TARGET_HOST, 'ascii');
          sock.write(Buffer.concat([Buffer.from([0x05, 0x01, 0x00, 0x03, hostBuf.length]), hostBuf,
            Buffer.from([(TARGET_PORT >> 8) & 0xff, TARGET_PORT & 0xff])]));
          stage = 3;
        } else if (stage === 3) {
          if (d.length >= 2 && d[1] === 0x00) { sock.removeAllListeners('data'); tlsFetch(); }
          else fail('socks connect-reply ' + (d[1] || '?'));
        }
      } catch (e) { fail('socks err ' + e.message); }
    });
    return;
  }

  // HTTP proxy: CONNECT
  let auth = '';
  if (entry.userinfo) auth = 'Proxy-Authorization: Basic ' + Buffer.from(entry.userinfo, 'utf8').toString('base64') + '\r\n';
  sock.on('connect', () => sock.write('CONNECT ' + TARGET_HOST + ':' + TARGET_PORT + ' HTTP/1.1\r\nHost: ' + TARGET_HOST + ':' + TARGET_PORT + '\r\n' + auth + '\r\n'));
  sock.on('error', (e) => fail(e.code || e.message));
  let buf = '';
  sock.on('data', (d) => {
    buf += d.toString('latin1');
    if (buf.indexOf('\r\n\r\n') === -1) { if (buf.length > 4096) fail('http big-reply'); return; }
    const m = buf.match(/^HTTP\/1\.[01] (\d+)/);
    if (m && m[1] === '200') { sock.removeAllListeners('data'); tlsFetch(); }
    else fail('http connect ' + (m ? m[1] : 'bad-reply'));
  });
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
    requestThrough(entry, p, (err) => { perPath.push({ path: p, err: err || null }); next(); });
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
