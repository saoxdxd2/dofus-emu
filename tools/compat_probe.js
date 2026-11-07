/* Prove a vendor bundle can carry the compat hooks BEFORE the client is asked to run it.

This is the gate the old pipeline never had. `clientbundle.cs` patches a downloaded bundle by
string replacement and used to adopt it whenever one of 23 rules matched, so a build that lost a
dozen anchors shipped half-patched and black-screened (3.3.11, 2026-11-04 -- and that build took
23/23 rules and still broke, because matching is necessary and not sufficient).

So the shim at `desktop/client/www/js/compat-shim.js` binds by *name*, and this tool answers the one
question that matters about a new build: **is every name the hooks need still there?**

  node tools/compat_probe.js                       # verify the build the relay last saw
  node tools/compat_probe.js --build 3.3.11_xxx --bundle path/to/script.js
  node tools/compat_probe.js --write               # record the result in the shim's table
  node tools/compat_probe.js --selftest            # prove the check can actually fail

The contract list is read out of the shim itself, so a hook added there is checked here
automatically -- the two files cannot drift apart, which is how the old anchors rotted.
*/
const fs = require("fs");
const path = require("path");

const REPO = path.resolve(__dirname, "..");
const SHIM = path.join(REPO, "desktop/client/www/js/compat-shim.js");
const PRISTINE = path.join(REPO, "desktop/client/www/build/script.js.bak");
const REQUESTS = path.join(REPO, "asset_cache/requests.log");
const BEGIN = "/* BEGIN AUTO-RESOLVED TABLE";
const END = "/* END AUTO-RESOLVED TABLE */";

function contractsFromShim(src) {
  const out = [];
  const re = /id:\s*"([^"]+)"[\s\S]{0,400}?required:\s*\[([^\]]*)\]/g;
  let m;
  while ((m = re.exec(src))) {
    const names = m[2].split(",").map(s => s.trim().replace(/^["']|["']$/g, "")).filter(Boolean);
    out.push({ id: m[1], required: names });
  }
  return out;
}

function findEvidence(text, dotted) {
  // Property names survive minification because the code reads them by name; local variables do
  // not. So the check is "appears as a property or an export", never "appears somewhere".
  const leaf = dotted.includes(".") ? dotted.split(".").pop() : dotted;
  const patterns = dotted.includes(".")
    ? [new RegExp("[.\\[]['\"]?" + leaf + "['\"]?\\s*[=:,\\]]"),
       new RegExp("\\b" + leaf + "\\s*[=:]\\s*function\\b"),
       new RegExp("\\b" + leaf + "\\s*[:=]\\s*[0-9{\"']")]
    : [new RegExp("\\bnew\\s+" + leaf + "\\b"),        // a global constructor the game instantiates
       new RegExp("\\b" + leaf + "\\.prototype\\b"),   // a global whose prototype we wrap
       new RegExp("\\bwindow\\." + leaf + "\\b")];     // a global read off window
  // Bare "does the word appear anywhere" was tried first and rejected: it also matches inside
  // strings and comments, so it passes builds that cannot work. A usage form can be absent.
  for (const re of patterns) {
    const at = text.search(re);
    if (at >= 0) return { found: true, at, sample: text.slice(Math.max(0, at - 40), at + 44) };
  }
  return { found: false };
}

function liveStamp() {
  try {
    const tail = fs.readFileSync(REQUESTS, "utf8").split("\n").slice(-4000).join("\n");
    const hits = [...tail.matchAll(/\/assets\/(\d+\.\d+\.\d+_[^/]+)\//g)].map(x => x[1]);
    return hits.length ? hits[hits.length - 1] : null;
  } catch (e) { return null; }
}

function tableFromShim(src) {
  const b = src.indexOf(BEGIN), e = src.indexOf(END);
  if (b < 0 || e < 0) throw new Error("shim has no AUTO-RESOLVED table markers");
  const body = src.slice(src.indexOf("{", b), e).trim();
  try { return JSON.parse(body.replace(/\/\/[^\n]*/g, "")
    .replace(/([{,]\s*)([A-Za-z_][\w]*)\s*:/g, '$1"$2":').replace(/,\s*}/g, "}")); }
  catch (err) { return { builds: {}, defaultMode: "pristine" }; }
}

function run(argv) {
  const arg = (name, dflt) => {
    const i = argv.indexOf("--" + name);
    return i >= 0 && argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[i + 1] : dflt;
  };
  const flag = name => argv.includes("--" + name);
  const shimSrc = fs.readFileSync(SHIM, "utf8");
  const contracts = contractsFromShim(shimSrc);
  if (!contracts.length) {
    console.log("VERDICT: FAIL -- no contracts parsed out of the shim; the probe would check nothing");
    return 1;
  }
  const bundlePath = flag("bundle") ? arg("bundle") : PRISTINE;
  const stamp = arg("build") || liveStamp();
  if (!fs.existsSync(bundlePath)) { console.log("VERDICT: FAIL -- no bundle at " + bundlePath); return 1; }
  const text = fs.readFileSync(bundlePath, "utf8");

  const results = [];
  for (const c of contracts) {
    const names = c.required.map(n => {
      const ev = findEvidence(text, n);
      return { name: n, ok: ev.found, sample: ev.found ? ev.sample.replace(/\s+/g, " ").slice(0, 70) : null };
    });
    results.push({ id: c.id, ok: names.every(n => n.ok), names });
  }
  const passed = results.filter(r => r.ok).length;
  const report = {
    bundle: path.relative(REPO, bundlePath),
    // bytes, not the character count: the bundle carries non-ASCII, so text.length was 35 KB short
    // of the file size and the table would have disagreed with `ls`.
    bytes: Buffer.byteLength(text), chars: text.length,
    build: stamp || "unknown", contracts: results,
    summary: passed + "/" + results.length + " resolvable",
    verdict: passed === results.length ? "HOOKABLE" : "PRISTINE-ONLY"
  };
  console.log(JSON.stringify(report, null, 1));

  if (flag("selftest")) {
    // Each contract, mutated one at a time: rename the exact name the probe looks for and require
    // the probe now says the build is NOT hookable. A single positive control proves nothing -- an
    // old pipeline check passed on a bundle with twelve dead anchors.
    const arms = [];
    for (const r of results) {
      const target = r.names[0];
      const leaf = target.name.split(".").pop();
      // Rename the symbol EVERYWHERE (not just the dotted form): this is the failure the probe
      // exists to catch -- the vendor renamed the thing our hook binds to. A mutation that leaves
      // bare `disconnect:` behind would be answered by the probe's own fallback pattern, and the
      // arm would be testing nothing.
      const occurrences = (text.match(new RegExp("\\b" + leaf + "\\b", "g")) || []).length;
      const mutated = text.replace(new RegExp("\\b" + leaf + "\\b", "g"), "zzRenamed" + leaf);
      arms.push({ contract: r.id, name: target.name, occurrences: occurrences,
                  detected: occurrences > 0 && !findEvidence(mutated, target.name).found });
    }
    const ok = passed === results.length && arms.every(a => a.detected);
    console.log(JSON.stringify({ selftest: { positive_control: passed === results.length,
      mutation_arms: arms, verdict: ok ? "PASS" : "FAIL" } }, null, 1));
    return ok ? 0 : 1;
  }

  if (flag("write")) {
    const table = tableFromShim(shimSrc);
    table.builds = table.builds || {};
    if (!stamp) console.log("not written: no build stamp (pass --build or let the relay log answer)");
    else {
      table.builds[stamp] = {
        mode: passed === results.length ? "hooked" : "pristine",
        verifiedAt: new Date().toISOString().slice(0, 19),
        bundleChars: text.length,
        bundleBytes: Buffer.byteLength(text, "utf8"),
        resolved: results.filter(r => r.ok).map(r => r.id),
        unresolved: results.filter(r => !r.ok).map(r => r.id)
      };
      const body = JSON.stringify(table, null, 2).replace(/\n\s*/g, "\n    ");
      const b = shimSrc.indexOf(BEGIN), e = shimSrc.indexOf(END);
      const next = shimSrc.slice(0, shimSrc.indexOf("{", b) + 1) + body.slice(body.indexOf("{") + 1)
        + ";" + String.fromCharCode(10) + "  " + shimSrc.slice(e);
      fs.writeFileSync(SHIM, next, "utf8");
      console.log("wrote table entry for " + stamp + " mode=" + table.builds[stamp].mode);
    }
  }
  return passed === results.length ? 0 : 1;
}

if (require.main === module) process.exitCode = run(process.argv.slice(2));
module.exports = { run, contractsFromShim, findEvidence };
