// proxy_pool.js - always-on proxy pool manager for the DofusFarm launchers.
//
//   node scripts\proxy_pool.js                 run daemon (recommended: leave a window open)
//   node scripts\proxy_pool.js --once          single pass, then exit
//   node scripts\proxy_pool.js --target 16     pool size (default: 2 x detected instances, min 8)
//   node scripts\proxy_pool.js --batch 200     raw candidates deep-checked per cycle (default 150)
//   node scripts\proxy_pool.js --cycle 10      minutes between cycles (default 10)
//
// What it does every cycle:
//   1. Re-verifies the approved pool (deep check: CONNECT/ SOCKS5 -> TLS -> GET
//      /build/primus.js and /config.json on dt-proxy-production-login; needs 200 on all).
//   2. Failed entries go to quarantine; after 2 consecutive failures they are dropped.
//   3. Scrapes public lists when the raw queue is low or stale (>30 min).
//   4. Deep-checks raw candidates in batches; pass -> approved, fail 2x -> dead forever.
//   5. Rewrites proxies.txt (atomic) with only approved entries - the launcher
//      hot-reads it with a 10s cache, so no launcher restart is needed.
//
// Files (next to the launcher, in the repo root):
//   proxies.txt             -> live pool (owned by this script once running)
//   proxies_raw.txt         -> accumulated raw candidates queue
//   proxy_pool_state.json   -> approved / quarantine / dead bookkeeping
//   proxy_pool.log          -> cycle log

const net = require('net');
const tls = require('tls');
const https = require('https');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const F_POOL = path.join(ROOT, 'proxies.txt');
const F_RAW = path.join(ROOT, 'proxies_raw.txt');
const F_STATE = path.join(ROOT, 'proxy_pool_state.json');
const F_LOG = path.join(ROOT, 'proxy_pool.log');
const F_BACKUP = path.join(ROOT, 'proxies_backup_premanager.txt');

const TARGET_HOST = 'dt-proxy-production-login.ankama-games.com';
const TARGET_PORT = 443;
const TARGET_PATHS = ['/build/primus.js', '/config.json'];
const UA = 'Mozilla/5.0 (Linux; Android 10; SM-A515F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.137 Mobile Safari/537.36';

const SOURCES = [
  { url: 'https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt', scheme: 'http' },
  { url: 'https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt', scheme: 'socks5' },
  { url: 'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt', scheme: 'http' },
  { url: 'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt', scheme: 'socks5' },
  { url: 'https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt', scheme: 'socks5' },
  { url: 'https://raw.githubusercontent.com/zloi-user/hideip.me/main/http.txt', scheme: 'http' },
  { url: 'https://raw.githubusercontent.com/zloi-user/hideip.me/main/socks5.txt', scheme: 'socks5' },
];

const ARGV = process.argv.slice(2);
function argNum(name, def) {
  const i = ARGV.indexOf('--' + name);
  return i >= 0 && ARGV[i + 1] ? parseFloat(ARGV[i + 1]) : def;
}
const ONCE = ARGV.includes('--once');
const NO_SCRAPE = ARGV.includes('--no-scrape');
const CONCURRENCY = argNum('concurrency', 50);
const TIMEOUT_MS = argNum('timeout', 8000);
// A dead proxy almost never completes the TCP handshake, so the connect stage does
// not need the full budget. With 50 workers and an 8 s ceiling the pool managed about
// three candidates a second, which is eight hours for a queue of 86,000. Screening the
// connect at 1.5 s and only then paying for TLS + HTTP is the difference between a
// night and an afternoon.
const CONNECT_TIMEOUT_MS = argNum('connect-timeout', 1500);
// Ceiling for proxies_raw.txt. At ~3 checks a second a queue of 150,000 is a day of
// work; anything older than that is dead anyway.
const MAX_RAW = argNum('max-raw', 150000);
const BATCH = argNum('batch', 150);
const CYCLE_MIN = argNum('cycle', 10);
const TARGET = argNum('target', detectTarget());

function detectTarget() {
  try {
    const avd = path.join(process.env.USERPROFILE || '', '.android', 'avd');
    const n = fs.readdirSync(avd).filter(d => /^dofus-\d+\.avd$/i.test(d)).length;
    return Math.max(8, 2 * n);
  } catch (e) { return 16; }
}

function log(msg) {
  const line = new Date().toISOString().replace('T', ' ').substring(0, 19) + ' ' + msg;
  console.log(line);
  try { fs.appendFileSync(F_LOG, line + '\r\n'); } catch (e) {}
}

function loadJson(file, def) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch (e) { return def; }
}

function parseEntry(line, schemeHint) {
  let s = String(line).trim();
  if (!s || s.startsWith('#')) return null;
  let scheme = schemeHint || 'http';
  const i = s.indexOf('://');
  if (i > 0) { scheme = s.substring(0, i).toLowerCase(); s = s.substring(i + 3); }
  let userinfo = null, rest = s;
  const at = s.lastIndexOf('@');
  if (at >= 0) { userinfo = s.substring(0, at); rest = s.substring(at + 1); }
  const idx = rest.lastIndexOf(':');
  if (idx <= 0) return null;
  const host = rest.substring(0, idx).trim();
  const port = parseInt(rest.substring(idx + 1), 10);
  if (!host || !port || port < 1 || port > 65535) return null;
  if (!/^[a-zA-Z0-9.\-]+$/.test(host)) return null;
  return { raw: (schemeHint ? scheme : scheme) + '://' + (userinfo ? userinfo + '@' : '') + host + ':' + port, scheme, host, port, userinfo };
}

// ---- deep check (same gate as scripts/proxy_check.js) ----

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
      if (!m) return fail('no-http');
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
          if (d.length >= 2 && d[0] === 0x05 && d[1] === 0x00) { sock.removeAllListeners('data'); return tlsFetch(); }
          const hostBuf = Buffer.from(TARGET_HOST, 'ascii');
          sock.write(Buffer.concat([Buffer.from([0x05, 0x01, 0x00, 0x03, hostBuf.length]), hostBuf,
            Buffer.from([(TARGET_PORT >> 8) & 0xff, TARGET_PORT & 0xff])]));
          stage = 3;
        } else if (stage === 3) {
          if (d.length >= 2 && d[1] === 0x00) { sock.removeAllListeners('data'); tlsFetch(); }
          else fail('socks connect ' + (d[1] || '?'));
        }
      } catch (e) { fail('socks err'); }
    });
    return;
  }

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
    else fail('http connect ' + (m ? m[1] : 'bad'));
  });
}

function checkEntry(entry) {
  return new Promise((resolve) => {
    const perPath = [];
    let i = 0;
    const next = () => {
      if (i >= TARGET_PATHS.length) {
        entry.ok = perPath.every(r => !r.err);
        entry.fail = perPath.find(r => r.err);
        resolve(entry);
        return;
      }
      const p = TARGET_PATHS[i++];
      requestThrough(entry, p, (err) => { perPath.push({ path: p, err: err || null }); next(); });
    };
    next();
  });
}

// Cheap question first: does anything answer a TCP handshake at all?
function tcpReachable(entry) {
  return new Promise((resolve) => {
    const sock = net.connect({ host: entry.host, port: entry.port });
    let settled = false;
    const done = (ok, why) => {
      if (settled) return;
      settled = true;
      try { sock.destroy(); } catch (e) {}
      resolve({ entry, ok, why });
    };
    const timer = setTimeout(() => done(false, 'connect-timeout'), CONNECT_TIMEOUT_MS);
    sock.on('connect', () => { clearTimeout(timer); done(true, null); });
    sock.on('error', (e) => { clearTimeout(timer); done(false, e.code || e.message); });
  });
}

async function runPool(items, worker, limit) {
  const queue = items.slice();
  const out = [];
  const workers = [];
  for (let w = 0; w < limit; w++) {
    workers.push((async () => {
      while (queue.length) out.push(await worker(queue.shift()));
    })());
  }
  await Promise.all(workers);
  return out;
}

async function verifyMany(entries) {
  // Stage 1: TCP only, at a short timeout. Stage 2: the real deep check on survivors.
  const screened = await runPool(entries, tcpReachable, CONCURRENCY);
  const alive = screened.filter(r => r.ok).map(r => r.entry);
  const dead = screened.filter(r => !r.ok);
  log('    pre-screen: ' + alive.length + ' of ' + entries.length
      + ' answered a TCP connect (' + dead.length + ' dropped without a TLS attempt)');
  const results = dead.map(r => ({ entry: r.entry, ok: false, why: r.why }));
  const deep = await runPool(alive, checkEntry, CONCURRENCY);
  return results.concat(deep);
}

// ---- scrape ----

function fetchUrl(url, depth) {
  depth = depth || 0;
  return new Promise((resolve) => {
    const req = https.get(url, { headers: { 'User-Agent': UA, 'Accept': 'text/plain,*/*' }, timeout: 20000 }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location && depth < 3) {
        res.resume();
        resolve(fetchUrl(res.headers.location, depth + 1));
        return;
      }
      if (res.statusCode !== 200) { res.resume(); resolve(null); return; }
      let data = '';
      res.setEncoding('utf8');
      res.on('data', (d) => { data += d; if (data.length > 20 * 1024 * 1024) req.destroy(); });
      res.on('end', () => resolve(data));
    });
    req.on('timeout', () => { req.destroy(); resolve(null); });
    req.on('error', () => resolve(null));
  });
}

async function scrape(state, haveSet, rawQueue) {
  let added = 0;
  for (const src of SOURCES) {
    const body = await fetchUrl(src.url);
    if (!body) { log('  scrape: ' + src.url + ' -> failed'); continue; }
    let n = 0;
    for (const line of body.split(/\r?\n/)) {
      const e = parseEntry(line, src.scheme);
      if (!e || haveSet.has(e.raw)) continue;
      haveSet.add(e.raw);
      rawQueue.push(e.raw);
      n++;
    }
    added += n;
    log('  scrape: ' + src.url + ' -> +' + n);
  }
  log('scrape total new candidates: ' + added);
  return added;
}

// ---- main ----

const now = () => Date.now();

async function main() {
  if (fs.existsSync(F_POOL) && !fs.existsSync(F_BACKUP)) {
    try {
      const cur = fs.readFileSync(F_POOL, 'utf8');
      if (cur.indexOf('proxy_pool.js') === -1) {
        fs.copyFileSync(F_POOL, F_BACKUP);
        log('backed up existing proxies.txt -> ' + path.basename(F_BACKUP));
      }
    } catch (e) {}
  }

  const state = loadJson(F_STATE, null) || {
    approved: [], quarantine: {}, dead: [], rawFails: {}, lastScrape: 0, seeded: false
  };
  state.dead = state.dead || [];
  state.rawFails = state.rawFails || {};
  state.quarantine = state.quarantine || {};

  let rawQueue = [];
  try { rawQueue = fs.readFileSync(F_RAW, 'utf8').split(/\r?\n/).map(s => s.trim()).filter(Boolean); } catch (e) {}

  const deadSet = new Set(state.dead);
  const haveSet = new Set([...state.approved, ...Object.keys(state.quarantine), ...rawQueue, ...state.dead]);

  if (!state.seeded) {
    state.seeded = true;
    let n = 0;
    if (fs.existsSync(F_POOL)) {
      for (const line of fs.readFileSync(F_POOL, 'utf8').split(/\r?\n/)) {
        const e = parseEntry(line, null);
        if (e && !haveSet.has(e.raw)) { haveSet.add(e.raw); rawQueue.push(e.raw); n++; }
      }
    }
    if (fs.existsSync(F_RAW)) {
      for (const line of fs.readFileSync(F_RAW, 'utf8').split(/\r?\n/)) {
        const e = parseEntry(line, null);
        if (e && !haveSet.has(e.raw)) { haveSet.add(e.raw); rawQueue.push(e.raw); n++; }
      }
    }
    log('seeded raw queue from existing files: ' + n);
  }

  log('=== proxy_pool start: target=' + TARGET + ' batch=' + BATCH + ' cycle=' + CYCLE_MIN + 'min' + (ONCE ? ' (once)' : '') + ' ===');
  log('state: approved=' + state.approved.length + ' quarantine=' + Object.keys(state.quarantine).length + ' dead=' + state.dead.length + ' rawQueue=' + rawQueue.length);

  const persist = () => {
    // Bound the queue. Without a cap it only grows, the oldest entries are never reached,
    // and the file becomes a museum of dead proxies. Dropping from the head is right
    // because the pool consumes from the tail: what survives here is the newest.
    if (rawQueue.length > MAX_RAW) {
      const dropped = rawQueue.length - MAX_RAW;
      rawQueue = rawQueue.slice(dropped);
      log('  queue over ' + MAX_RAW + '; dropped the ' + dropped + ' oldest candidates');
    }
    fs.writeFileSync(F_RAW, rawQueue.join('\n') + (rawQueue.length ? '\n' : ''));
    state.dead = [...deadSet].slice(-50000);
    fs.writeFileSync(F_STATE, JSON.stringify(state, null, 1));
  };

  const writePool = () => {
    const header =
      '# proxies.txt - managed by proxy_pool.js (do not edit while the manager runs)\n' +
      '# updated: ' + new Date().toISOString() + ' | live entries: ' + state.approved.length + '\n';
    const tmp = F_POOL + '.tmp';
    fs.writeFileSync(tmp, header + state.approved.join('\n') + (state.approved.length ? '\n' : ''));
    fs.renameSync(tmp, F_POOL);
  };

  async function cycle() {
    const t0 = now();
    log('--- cycle start (approved=' + state.approved.length + ') ---');

    if (state.approved.length) {
      const keep = [];
      const re = await verifyMany(state.approved.map(p => parseEntry(p, null)).filter(Boolean));
      for (const e of re) {
        if (e.ok) { keep.push(e.raw); }
        else {
          state.quarantine[e.raw] = { fails: 1, retryAt: now() + 5 * 60 * 1000, lastFail: e.fail && e.fail.err };
          log('  quarantine (1st fail): ' + e.raw + ' [' + (e.fail && e.fail.err) + ']');
        }
      }
      state.approved = keep;
    }

    for (const p of Object.keys(state.quarantine)) {
      const q = state.quarantine[p];
      if (q.retryAt > now()) continue;
      const e = parseEntry(p, null);
      if (!e) { delete state.quarantine[p]; continue; }
      const r = await checkEntry(e);
      if (r.ok) {
        delete state.quarantine[p];
        state.approved.push(r.raw);
        log('  recovered from quarantine: ' + r.raw);
      } else {
        delete state.quarantine[p];
        deadSet.add(p);
        log('  DEAD (2nd fail): ' + p);
      }
    }

    if (state.approved.length < TARGET) {
      if (!NO_SCRAPE && (now() - state.lastScrape > 30 * 60 * 1000) && rawQueue.length < BATCH * 3) {
        state.lastScrape = now();
        await scrape(state, haveSet, rawQueue);
      }
      const batch = [];
      // Newest first.
      //
      // This used to `shift()` — the oldest entry — while the harvester appended to the
      // tail. With a queue of 85,000 that meant a proxy fetched this morning would not be
      // tested for days, and the pool spent every cycle on entries that had been rotting
      // in the file the longest. Public proxies die within hours, so age is the single
      // strongest signal available and it was being used backwards.
      while (batch.length < BATCH && rawQueue.length) {
        const raw = rawQueue.pop();
        if (deadSet.has(raw) || state.approved.indexOf(raw) !== -1 || state.quarantine[raw]) continue;
        const e = parseEntry(raw, null);
        if (e) batch.push(e);
      }
      if (batch.length) {
        log('  deep-checking ' + batch.length + ' raw candidates...');
        const res = await verifyMany(batch);
        for (const e of res) {
          if (e.ok) { state.approved.push(e.raw); log('  OK: ' + e.raw); }
          else {
            const f = (state.rawFails[e.raw] || 0) + 1;
            if (f >= 2) { delete state.rawFails[e.raw]; deadSet.add(e.raw); }
            else { state.rawFails[e.raw] = f; rawQueue.push(e.raw); }
          }
        }
      } else {
        log('  raw queue empty - will refill from scrape next cycle');
      }
    }

    writePool();
    persist();
    const secs = ((now() - t0) / 1000).toFixed(1);
    log('--- cycle done in ' + secs + 's | live=' + state.approved.length + '/' + TARGET +
      ' | rawQueue=' + rawQueue.length + ' | quarantine=' + Object.keys(state.quarantine).length + ' | dead=' + deadSet.size + ' ---');
  }

  await cycle();
  if (ONCE) { log('once mode: exit'); return; }
  const again = async () => {
    await new Promise(r => setTimeout(r, CYCLE_MIN * 60 * 1000));
    try { await cycle(); } catch (e) { log('cycle error: ' + e.message); }
    again();
  };
  again();
}

main().catch(e => { log('fatal: ' + (e && e.stack || e)); process.exit(1); });
