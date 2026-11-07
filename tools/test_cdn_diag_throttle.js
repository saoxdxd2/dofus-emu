/* Drives the REAL cdn-relay-shim.js in a sandbox and asserts what its diag throttle does.

Why a test file and not a reasoning exercise: the throttle was written to cut 87% of the page->host
message volume (14,096 of 16,141 in one session). A claim about dropped volume is worth nothing
unless the same code can be shown (a) suppressing a repeat, (b) never suppressing a line that looks
like a failure, and (c) reporting the suppression instead of going silent. Each arm is run against
the shipped file, not a reimplementation.

  node tools/test_cdn_diag_throttle.js
*/
const fs = require("fs");
const vm = require("vm");
const path = require("path");

const SHIM = path.resolve(__dirname, "../desktop/client/www/js/cdn-relay-shim.js");
const src = fs.readFileSync(SHIM, "utf8");

const posted = [];
const noop = () => {};
const element = { style: {}, appendChild: noop, addEventListener: noop,
                  setAttribute: noop, removeChild: noop, querySelector: () => null,
                  children: [], dataset: {} };
const win = {
  chrome: { webview: { postMessage: (m) => posted.push(m) } },
  location: { href: "https://dofus.local/standalone.html", origin: "https://dofus.local" },
  document: { createElement: () => element, body: element, head: element,
              addEventListener: noop, querySelector: () => null,
              getElementsById: () => [], getElementById: () => null },
  navigator: { userAgent: "Mozilla/5.0 (Linux; Android 10; SM-A515F)", onLine: true },
  localStorage: { getItem: () => null, setItem: noop, removeItem: noop },
  setTimeout, clearTimeout, setInterval: () => 0, clearInterval: noop,
  console: { log: noop, warn: noop, error: noop },
  performance: { now: () => Date.now() },
  addEventListener: noop, removeEventListener: noop, fetch: () => Promise.resolve({}),
  XMLHttpRequest: function () { this.open = noop; this.send = noop;
                                this.addEventListener = noop; this.setRequestHeader = noop; },
  Image: function () { this.addEventListener = noop; },
  URL, encodeURIComponent, decodeURIComponent, JSON, Math, Date, Object, Array, String, Number,
  Promise, RegExp, Error, Boolean, parseInt, parseFloat, escape
};
// The shim opens by reading its own installed-guard off `window`, and pages read `window` the
// same way. Without the self-reference the harness fails for the wrong reason.
win.window = win; win.self = win; win.top = win; win.parent = win; win.globalThis = win;
win.document.defaultView = win; win.document.documentElement = element;
win.screen = { width: 1080, height: 2220 };
win.HTMLElement = function () {}; win.Event = function () {}; win.CustomEvent = function () {};
// The shim is deliberately inert without the host's relay config -- that is its fail-open design,
// and the test has to satisfy it to reach the throttle at all.
win.__DOFUS_CDN_RELAY = { enabled: true, port: 8880 };
const sandbox = new Proxy(win, {
  has: () => true,
  get: (target, key) => (key in target ? target[key]
    : (key === Symbol.unscopables ? undefined : undefined))
});

vm.createContext(sandbox);
try {
  vm.runInContext(src, sandbox, { filename: "cdn-relay-shim.js", timeout: 4000 });
} catch (e) {
  console.log("VERDICT: FAIL -- the shipped shim could not be loaded in the sandbox: "
    + e.message + "\n  (so this test cannot see the throttle; fix the harness, do not assume)");
  process.exit(1);
}

const probe = sandbox.window.__cdnRelayProbe;
if (!probe || typeof probe.diag !== "function") {
  console.log('VERDICT: FAIL -- window.__cdnRelayProbe missing; the shim did not reach the throttle');
  process.exit(1);
}

function run() {
  const arms = [];
  posted.length = 0;
  probe.reset();

  // arm 1: a benign line repeated -- only the first may reach the host.
  // (This arm was written with "probe network error check ok" first and failed correctly: that
  // text contains "error", so the throttle treats it as a failure and never suppresses it. The
  // fixture was wrong, not the code -- benign means benign in the words it actually uses.)
  for (let i = 0; i < 6; i++) probe.diag("prewarm queued 3 neighbours for map 145490432");
  arms.push({ arm: "repeat suppressed", posts: posted.length, pass: posted.length === 1 });

  // arm 2: a line that looks like a failure must never wait behind the throttle.
  // This arm initially reported 3 posts, and the third was honest: the previous arm's suppressed
  // repeats flush into the next post, which is arm 3's whole point. So it resets first and tests
  // exactly one property.
  posted.length = 0;
  probe.reset();
  probe.diag("relay gave HTTP 503 for ui/spinner.png");
  probe.diag("relay gave HTTP 503 for ui/spinner.png");
  arms.push({ arm: "failure never throttled", posts: posted.length, pass: posted.length === 2 });

  // arm 3: suppression is announced, not hidden -- the next distinct line carries the count
  posted.length = 0;
  probe.reset();
  probe.diag("warm map tile 145490432");
  for (let i = 0; i < 5; i++) probe.diag("warm map tile 145490432");
  probe.diag("shim installed (port 8880)");
  const announced = posted.some(m => /identical repeats suppressed/.test(String(m)));
  arms.push({ arm: "suppressing is reported", posts: posted.length, pass: announced });

  // arm 4: the throttle must not eat genuinely different lines
  posted.length = 0;
  probe.reset();
  probe.diag("asset a"); probe.diag("asset b"); probe.diag("asset c");
  arms.push({ arm: "distinct lines pass", posts: posted.length, pass: posted.length === 3 });

  // ---- the learned asset version ladder ------------------------------------------------
  // The claim under test: the version is never written into this repo, it is learned from live
  // traffic, and a path denied at a stale version is retried one version up.
  const V = "https://dofustouch.cdn.ankama.com/assets/";
  const push = (armName, ok, extra) =>
    arms.push(Object.assign({ arm: armName, posts: null, pass: !!ok }, extra || {}));

  probe.resetVersions();
  probe.note(V + "3.3.10_9K0.T2bChypxsZ0fDLj7ehuwbXQ6oyhe/maps/145490432.json");
  probe.note(V + "3.3.11_1h-,mO*yuB0Adnu4P-_1Fa9c2gqbLXKo/maps/145490432.json");
  const learned = probe.versions().assets || {};
  push("newest assets version is learned (token chars and all)",
       learned.patch === 11 && learned.token.indexOf(",") > 0 && learned.token.indexOf("*") > 0,
       { detail: learned });

  probe.resetVersions();
  probe.note(V + "3.3.10_A/x.json");
  probe.note(V + "3.3.9_B/x.json");
  push("3.3.9 never outranks 3.3.10 (string compare would)",
       (probe.versions().assets || {}).patch === 10, { detail: probe.versions().assets });

  probe.resetVersions();
  probe.note(V + "3.3.11_B/x.json");
  const escalated = probe.escalate(V + "3.3.10_A/maps/145490432.json");
  push("denied map path escalates to the learned version, same path",
       escalated === V + "3.3.11_B/maps/145490432.json", { detail: escalated });
  push("already-newest escalates to nothing (no loop)",
       probe.escalate(V + "3.3.11_B/maps/1.json") === null);

  probe.resetVersions();
  probe.note(V + "3.3.11_B/x.json");
  push("ui does not inherit the assets version",
       probe.escalate(V + "ui/1.10.7_A/ui/spinner_sprite.png") === null);
  probe.note(V + "ui/1.10.8_R,abc/ui/other.png");
  push("ui escalates inside its own kind",
       probe.escalate(V + "ui/1.10.7_A/ui/spinner_sprite.png")
         === V + "ui/1.10.8_R,abc/ui/spinner_sprite.png");
  push("a non-asset url escalates to nothing",
       probe.escalate("https://static.ankama.com/ankama/cms/images/logo.jpg") === null);

  // A denial is a verdict about a version. Learning the same version must keep it; a newer one
  // must void it, or the ten-minute negative cache silently blocks a map that now loads fine.
  probe.resetVersions();
  probe.note(V + "3.3.10_A/maps/1.json");
  probe.markFail("145490432", 403);
  push("a recorded denial is held", probe.failedMaps().indexOf("145490432") >= 0);
  probe.note(V + "3.3.10_A/maps/2.json");
  push("same version keeps the denial", probe.failedMaps().indexOf("145490432") >= 0);
  probe.note(V + "3.3.11_B/maps/2.json");
  push("a superseded version voids the denial", probe.failedMaps().length === 0,
       { detail: probe.failedMaps() });

  const failed = arms.filter(a => !a.pass);
  console.log(JSON.stringify({ arms, verdict: failed.length ? "FAIL" : "PASS" }, null, 1));
  return failed.length ? 1 : 0;
}
process.exitCode = run();
