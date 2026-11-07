/* Drives the REAL compat-shim.js boot canary in a sandbox and asserts what it says to the host.

The updater now acts on this message: "true" clears a patched bundle's pending judgement, "false"
reverts the bundle and puts the build on the never-patch list. So the two directions have to be
provable from the shipped file, and so does the third case that matters most -- a canary that
invents a verdict from nothing would blacklist good patches or bless black screens. Each arm loads
../desktop/client/www/js/compat-shim.js itself; nothing here is a reimplementation.

  node tools/test_boot_canary.js
*/
const fs = require("fs");
const vm = require("vm");
const path = require("path");

const SHIM = path.resolve(__dirname, "../desktop/client/www/js/compat-shim.js");
const src = fs.readFileSync(SHIM, "utf8");
const MAP_URL = "/assets/3.3.11_AbCdEfGh/gfx/maps/10200.json";

let pass = 0, fail = 0;
function check(name, ok, detail) {
  if (ok) { pass++; console.log("  [PASS] " + name); }
  else { fail++; console.error("  [FAIL] " + name + (detail ? " -- " + detail : "")); }
}

// One fresh page: the shim is installed once per window, so arms cannot share a sandbox.
function arm(stamp) {
  const posted = [];
  const timers = [];
  const noop = () => {};
  const element = { style: {}, appendChild: noop, addEventListener: noop, setAttribute: noop,
                    removeChild: noop, querySelector: () => null, children: [], dataset: {},
                    src: "" };
  function Xhr() { this.addEventListener = noop; this.setRequestHeader = noop; this.send = noop; }
  Xhr.prototype = { open: noop, setRequestHeader: noop, send: noop, addEventListener: noop };
  const win = {
    chrome: { webview: { postMessage: (m) => posted.push(m) } },
    location: { href: "https://dofus.local/standalone.html", origin: "https://dofus.local" },
    document: { createElement: () => element, body: element, head: element, addEventListener: noop,
                querySelector: () => null, getElementsById: () => [], getElementById: () => null },
    navigator: { userAgent: "Mozilla/5.0 (Linux; Android 10; SM-A515F)", onLine: true },
    localStorage: { getItem: () => null, setItem: noop, removeItem: noop },
    setTimeout: (fn) => { timers.push({ fn: fn, cleared: false }); return timers.length; },
    setInterval: (fn) => { timers.push({ fn: fn, cleared: false }); return timers.length; },
    clearTimeout: (id) => { if (timers[id - 1]) timers[id - 1].cleared = true; },
    clearInterval: (id) => { if (timers[id - 1]) timers[id - 1].cleared = true; },
    console: { log: noop, warn: noop, error: noop },
    performance: { now: () => 0 },
    addEventListener: noop, removeEventListener: noop, fetch: () => Promise.resolve({}),
    XMLHttpRequest: Xhr,
    Image: function () { this.addEventListener = noop; },
    URL, encodeURIComponent, decodeURIComponent, JSON, Math, Date, Object, Array, String, Number,
    Promise, RegExp, Error, Boolean, parseInt, parseFloat, escape
  };
  win.window = win; win.self = win; win.top = win; win.parent = win; win.globalThis = win;
  win.document.defaultView = win; win.document.documentElement = element;
  win.screen = { width: 1080, height: 2220 };
  win.HTMLElement = function () {}; win.Event = function () {}; win.CustomEvent = function () {};
  if (stamp) win.__DOFUS_BOOT_STAMP = stamp;
  const sandbox = new Proxy(win, { has: () => true, get: (t, k) => (k in t ? t[k] : undefined) });
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: "compat-shim.js", timeout: 4000 });

  // The disconnect the vendor would call, wrapped by the shim's own guard.
  const kills = [];
  win.dofus = { disconnect: (why) => { kills.push(why); return true; } };

  // A map fetch through the shim's observer. `status` is set before loadend fires, which is what
  // the real XHR does; the shim counts it into ok/failed exactly as the page would.
  const observed = { open: 0, listeners: [] };
  const realOpen = Xhr.prototype.open;
  win.__observed = observed;
  function fetchMap(status) {
    const xhr = new win.XMLHttpRequest();
    xhr.addEventListener = (name, cb) => { if (name === "loadend") observed.listeners.push(cb); };
    xhr.open("GET", MAP_URL);
    observed.open++;
    xhr.status = status;
    observed.listeners.forEach((cb) => cb());
    observed.listeners = [];
  }
  return {
    win: win, posted: posted, kills: kills, observed: observed, fetchMap: fetchMap,
    tick: (times) => { for (let i = 0; i < times; i++) timers.forEach((t) => { if (!t.cleared) t.fn(); }); },
    canaries: () => posted.filter((m) => String(m).indexOf("BOOT_CANARY") >= 0)
  };
}

// A world that arrived: facade up, one map back 200. This is the only honest "true".
let a = arm("STAMP_A");
a.fetchMap(200);
a.tick(1);
let msg = a.canaries();
check("a facade plus a loaded map answers ok:true",
      msg.length === 1 && /"ok":true/.test(msg[0]) && /"stamp":"STAMP_A"/.test(msg[0]),
      JSON.stringify(msg));

// The 3.3.11 signature: our own failure path asks for a teardown, the veto budget runs out and
// the session dies. That must answer false, or the reverted bundle never comes back.
a = arm("STAMP_B");
for (let i = 0; i < 3; i++) a.win.dofus.disconnect("ASSET_MISSING");
a.tick(1);
msg = a.canaries();
check("a self-inflicted teardown past the veto budget answers ok:false",
      msg.length === 1 && /"ok":false/.test(msg[0]) && /"stamp":"STAMP_B"/.test(msg[0]),
      JSON.stringify(msg));
check("and it really did spend the budget before answering",
      a.observed.open === 0 && a.kills.length === 1,
      "disconnects passed through the guard: " + a.kills.length);

// Survived hiccups are not a failure: two vetoes are inside the budget, and the world then loads.
a = arm("STAMP_C");
a.win.dofus.disconnect("ASSET_MISSING");
a.win.dofus.disconnect("LOADING_MAP_ERROR boom");
a.fetchMap(200);
a.tick(1);
msg = a.canaries();
check("vetoes inside the budget plus a loaded map still answer ok:true",
      msg.length === 1 && /"ok":true/.test(msg[0]), JSON.stringify(msg));

// No verdict is the safe answer, and it must stay silent: a wrong false would blacklist good
// patches for ever, a wrong true is the lie this whole mechanism replaces.
a = arm("STAMP_D");
a.tick(60);                       // 60 ticks is past the 90 s grace
msg = a.canaries();
check("a client that never renders a map says nothing at all",
      msg.length === 0, JSON.stringify(msg));

a = arm("STAMP_E");
a.fetchMap(0);                   // credentialed-CORS shape: a 200 the browser throws away
a.fetchMap(403);
a.tick(60);
msg = a.canaries();
check("map requests that all failed are not a booted game",
      msg.length === 0, JSON.stringify(msg));

// Without the host's stamp there is no judgement waiting, so nothing may be claimed about it.
a = arm(null);
a.fetchMap(200);
a.tick(10);
msg = a.canaries();
check("no boot stamp means no canary, even on a healthy client",
      msg.length === 0, JSON.stringify(msg));

// One answer per bundle: a second verdict would fight the first over the pending file.
a = arm("STAMP_F");
a.fetchMap(200);
a.tick(5);
a.fetchMap(200);
a.tick(5);
msg = a.canaries();
check("the canary answers once, not once per tick",
      msg.length === 1, JSON.stringify(msg));

console.log("\n" + pass + " passed, " + fail + " failed");
console.log(fail ? "VERDICT: FAIL" : "VERDICT: PASS");
process.exit(fail ? 1 : 0);
