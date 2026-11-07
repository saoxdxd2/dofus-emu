// harvest_proxies.js - keep the raw candidate queue growing in real time.
//
// scripts/proxy_pool.js already scrapes, but only when its queue is low or stale. With
// 75,000 candidates sitting unchecked it never refreshes, so the pool keeps re-testing a
// frozen snapshot of a list that rots by the hour. This does the fetching on its own
// schedule and appends what is new; the pool stays responsible for testing and for
// rewriting proxies.txt.
//
//   node scripts\harvest_proxies.js              harvest every 5 minutes, forever
//   node scripts\harvest_proxies.js --once       one pass, then exit
//   node scripts\harvest_proxies.js --interval 60   every minute
//
// Files (repo root):
//   proxies_raw.txt        the queue the pool consumes; this only ever appends to it
//   proxy_harvest.log      one line per pass: sources ok/failed, new entries
//
// It never tests anything and never touches proxies.txt. A harvester that also judged
// candidates would be a second opinion about what "working" means.

const https = require('https');
const http = require('http');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const F_RAW = path.join(ROOT, 'proxies_raw.txt');
const F_LOG = path.join(ROOT, 'proxy_harvest.log');

const args = process.argv.slice(2);
const ONCE = args.includes('--once');
const INTERVAL_S = (() => {
  const i = args.indexOf('--interval');
  const v = i >= 0 ? parseInt(args[i + 1], 10) : NaN;
  return Number.isFinite(v) && v > 0 ? v : 300;
})();

// Plain-text lists, and the two APIs that return one proxy per line. Every source is
// https so a network that filters by URL still has to allow the fetch.
const SOURCES = [
  { scheme: 'http',   url: 'https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt' },
  { scheme: 'socks5', url: 'https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt' },
  { scheme: 'http',   url: 'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt' },
  { scheme: 'socks5', url: 'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt' },
  { scheme: 'socks5', url: 'https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt' },
  { scheme: 'http',   url: 'https://raw.githubusercontent.com/zloi-user/hideip.me/main/http.txt' },
  { scheme: 'socks5', url: 'https://raw.githubusercontent.com/zloi-user/hideip.me/main/socks5.txt' },
  { scheme: 'http',   url: 'https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt' },
  { scheme: 'socks5', url: 'https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/socks5.txt' },
  { scheme: 'http',   url: 'https://raw.githubusercontent.com/mmpx12/proxy-list/master/http.txt' },
  { scheme: 'socks5', url: 'https://raw.githubusercontent.com/mmpx12/proxy-list/master/socks5.txt' },
  { scheme: 'http',   url: 'https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-http.txt' },
  { scheme: 'socks5', url: 'https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-socks5.txt' },
  { scheme: 'http',   url: 'https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all' },
  { scheme: 'socks5', url: 'https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks5&timeout=10000&country=all&ssl=all&anonymity=all' },
  { scheme: 'http',   url: 'https://www.proxy-list.download/api/v1/get?type=http' },
  { scheme: 'socks5', url: 'https://www.proxy-list.download/api/v1/get?type=socks5' },
];

const TIMEOUT_MS = 15000;

function log(line) {
  const stamped = new Date().toISOString().replace('T', ' ').slice(0, 19) + ' ' + line;
  try { fs.appendFileSync(F_LOG, stamped + '\r\n'); } catch (e) {}
  console.log(stamped);
}

function fetchText(url) {
  return new Promise(resolve => {
    const mod = url.startsWith('https') ? https : http;
    const req = mod.get(url, { headers: { 'User-Agent': 'Mozilla/5.0', 'Accept': 'text/plain,*/*' } }, res => {
      if (res.statusCode !== 200) {
        res.resume();
        resolve(null);
        return;
      }
      let body = '';
      res.setEncoding('utf8');
      res.on('data', chunk => { if (body.length < 8e6) body += chunk; });
      res.on('end', () => resolve(body));
      res.on('error', () => resolve(null));
    });
    req.setTimeout(TIMEOUT_MS, () => { req.destroy(); resolve(null); });
    req.on('error', () => resolve(null));
  });
}

// Accepts `1.2.3.4:80`, `scheme://1.2.3.4:80`, and the `ip,port,country,...` CSV that a
// couple of the APIs return. Returns null for anything that is not a usable endpoint.
function parseLine(raw, defaultScheme) {
  const line = String(raw).trim();
  if (!line || line.startsWith('#')) return null;

  let m = /^(https?|socks5|socks4):\/\/([^\s:/]+):(\d{2,5})\/?$/.exec(line);
  if (m) return normalise(m[1], m[2], m[3]);

  m = /^([\d.]+):(\d{2,5})$/.exec(line);
  if (m) return normalise(defaultScheme, m[1], m[2]);

  // "1.2.3.4,80,US,elite" style
  m = /^([\d.]+)\s*,\s*(\d{2,5})\b/.exec(line);
  if (m) return normalise(defaultScheme, m[1], m[2]);

  return null;
}

function normalise(scheme, host, port) {
  const p = parseInt(port, 10);
  if (!(p > 0 && p < 65536)) return null;
  if (!/^\d{1,3}(\.\d{1,3}){3}$/.test(host)) return null;
  const s = scheme === 'socks4' ? 'socks5' : (scheme === 'https' ? 'http' : scheme);
  return s + '://' + host + ':' + p;
}

function loadKnown() {
  const known = new Set();
  try {
    for (const line of fs.readFileSync(F_RAW, 'utf8').split(/\r?\n/)) {
      const v = line.trim();
      if (v) known.add(v);
    }
  } catch (e) {}
  return known;
}

async function pass() {
  const started = Date.now();
  const known = loadKnown();
  const before = known.size;
  let okSources = 0;
  let failed = 0;
  let seen = 0;
  const fresh = [];

  const bodies = await Promise.all(SOURCES.map(async s => ({ s, body: await fetchText(s.url) })));
  for (const { s, body } of bodies) {
    if (body == null) { failed++; continue; }
    okSources++;
    for (const line of body.split(/\r?\n/)) {
      const entry = parseLine(line, s.scheme);
      if (!entry) continue;
      if (known.has(entry)) { seen++; continue; }
      known.add(entry);
      fresh.push(entry);
    }
  }

  if (fresh.length) {
    // Shuffle before appending.
    //
    // The pool consumes from the tail, so whatever is written last is tested first. Sources
    // return their list in blocks, which means one fetch could fill an entire batch with a
    // single list's addresses — usually one country, one hosting provider, one failure mode.
    // Mixing costs nothing and samples across all of them.
    for (let i = fresh.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      const tmp = fresh[i]; fresh[i] = fresh[j]; fresh[j] = tmp;
    }
    try {
      fs.appendFileSync(F_RAW, fresh.join('\n') + '\n');
    } catch (e) {
      log('FAIL could not append to proxies_raw.txt: ' + e.message);
      return;
    }
  }

  // "new" is the number that matters: a pass that adds nothing is a pass where every source
  // returned what we already had, which is the normal state between their update cycles.
  log('pass: sources ' + okSources + ' ok / ' + failed + ' failed, +' + fresh.length
      + ' new, ' + seen + ' already known (queue ' + before + ' -> ' + known.size + ') in '
      + ((Date.now() - started) / 1000).toFixed(1) + 's');
}

(async () => {
  log('=== harvest start: ' + SOURCES.length + ' sources, every ' + INTERVAL_S + 's ===');
  await pass();
  if (ONCE) { log('once mode: exit'); return; }
  for (;;) {
    await new Promise(r => setTimeout(r, INTERVAL_S * 1000));
    await pass();
  }
})().catch(e => { log('FATAL ' + (e && e.stack || e)); process.exit(1); });
