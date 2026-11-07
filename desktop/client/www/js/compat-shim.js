/* Client compatibility shim -- the app-compat layer for Ankama's twice-weekly builds.
 *
 * WHY THIS EXISTS. Our patches used to be string replacements inside the vendor bundle
 * (tools/patch-engine.js). Every re-minify moves identifiers and reorders chunks, so a
 * replacement either missed silently or half-applied, and a half-applied loader is a black
 * screen -- 2026-11-04, build 3.3.11, where our own map-resilience patch called
 * window.dofus.disconnect("ASSET_MISSING") and killed the session the vendor loader would have
 * survived. Windows solved this class decades ago and never by editing the vendor binary: it
 * hooks at a named API boundary (shims), resolves implementations through a contract name rather
 * than an address (API sets), and if nothing matches the app just runs (fail open). This file is
 * that layer for the web client.
 *
 * THE RULES THIS LAYER IS HELD TO
 *  1. Bind to names, never to bytes. Property names survive minification because the code reads
 *     them by name (`dofus.disconnect`, `Config.MAP_PATH`); local variables do not. Every hook
 *     declares the names it needs and resolves them at runtime.
 *  2. Fail open. Unresolved contract -> that hook is skipped and counted. An unhooked client is
 *     degraded; a half-patched client is blank. Never be the blank one.
 *  3. Prove it fired. A hook is "bound" at install and "fired" when it intercepts something real.
 *     `bound=N fired=0` over a session means the hook is decorative, which is a finding, not a
 *     success. The old patch pipeline could not say this at all -- it reported only that the
 *     replacement text had been written.
 *  4. Nothing here may throw into the game. Every entry point is wrapped; a shim that breaks the
 *     client is worse than the bug it fixes.
 *
 * Loaded BEFORE build/script.js so the facade trap and the network observer are in place when the
 * vendor bundle runs. Reads its per-build policy from the table between the AUTO markers, which
 * tools/compat_probe.js generates and rewrites -- hand-editing that block is how the old pipeline
 * got its anchors in the first place.
 */
(function () {
  "use strict";

  if (window.__compatShim) return;
  var shim = {
    version: 1,
    contracts: {},        // id -> {bound, fired, skipped, note}
    build: null,
    mode: "unresolved",   // hooked | pristine | unresolved
    errors: []
  };
  window.__compatShim = shim;

  function report(text) {
    try {
      console.warn("[compat] " + text);
      if (window.chrome && window.chrome.webview && window.chrome.webview.postMessage) {
        window.chrome.webview.postMessage('{"type":"COMPAT_DIAG","text":"'
          + String(text).replace(/["\\]/g, "'") + '"}');
      }
    } catch (e) { /* reporting must never be the reason the client breaks */ }
  }

  // ---------------------------------------------------------------- per-build table

  /* BEGIN AUTO-RESOLVED TABLE -- rewritten by tools/compat_probe.js, do not hand-edit */
  var TABLE = {
    "builds": {
    "3.3.11_1h-,mO*yuB0Adnu4P-_1Fa9c2gqbLXKo": {
    "mode": "hooked",
    "verifiedAt": "2026-11-04T11:11:18",
    "bundleChars": 5362738,
    "bundleBytes": 5397933,
    "resolved": [
    "disconnect-guard",
    "map-observer"
    ],
    "unresolved": []
    }
    },
    "defaultMode": "pristine"
    };
  /* END AUTO-RESOLVED TABLE */

  // The build stamp is not told to us -- it appears in the asset URLs the client itself requests,
  // which the relay shim already sees. Reading it from a live URL keeps this working when Ankama
  // pushes a version we have never met.
  function buildFromUrl(url) {
    var m = /\/assets\/([0-9]+\.[0-9]+\.[0-9]+_[^\/]+)\//.exec(String(url || ""));
    return m ? m[1] : null;
  }

  // ---------------------------------------------------------------- contract registry

  // Each contract: names it requires, how to install once resolved, and a probe that can fail.
  // `required` is matched against the live object graph, not the bundle text -- the text check is
  // tools/compat_probe.js's job before the build is ever shipped.
  var CONTRACTS = [
    {
      id: "disconnect-guard",
      // Why: our own failure paths (and any future patch) name reasons the vendor never emits.
      // Tearing the session down for a self-inflicted reason is what turned a recoverable map
      // problem into a black screen on 3.3.11. The guard counts them and lets the client live.
      required: ["dofus.disconnect"],
      install: function (api) {
        var original = api.dofus.disconnect;
        if (typeof original !== "function") return false;
        // Bounded veto. `ASSET_MISSING` is the vendor's own vocabulary as well as our patch's, so
        // refusing it forever could park the client on a half-loaded world instead of failing
        // cleanly. Twice per session: enough to survive a self-inflicted hiccup, short enough that
        // a genuinely missing asset still disconnects the way the vendor intends.
        var VETO_BUDGET = 2;
        api.dofus.disconnect = function (reason) {
          var why = String(reason || "");
          var slot = shim.contracts["disconnect-guard"];
          slot.fired++;
          if (/^(ASSET_MISSING|LOADING_MAP_ERROR)/.test(why) && slot.vetoed < VETO_BUDGET) {
            slot.vetoed++;
            slot.selfKills++;
            report("refusing disconnect reason=" + why.slice(0, 60) + " veto=" + slot.vetoed
              + "/" + VETO_BUDGET + " -- the vendor loader survives this map; killing the session does not");
            return undefined;
          }
          if (/^(ASSET_MISSING|LOADING_MAP_ERROR)/.test(why)) {
            slot.budgetOut++;
            report("disconnect " + why.slice(0, 40) + " passed through: veto budget spent");
          }
          return original.apply(this, arguments);
        };
        return true;
      }
    },
    {
      id: "map-observer",
      // Why: "asset missing" used to be a verdict taken from inside the vendor loader. Observed at
      // the network seam it is a fact with a status code, a latency and a URL -- and the retry
      // policy can be changed without touching the bundle at all.
      required: ["XMLHttpRequest"],
      install: function () {
        if (!window.XMLHttpRequest || !window.XMLHttpRequest.prototype
            || typeof window.XMLHttpRequest.prototype.open !== "function") return false;
        var open = window.XMLHttpRequest.prototype.open;
        window.XMLHttpRequest.prototype.open = function (method, url) {
          var key = String(url || "");
          if (/\/(gfx\/)?maps\/[0-9-]+\.json/.test(key)) {
            shim.contracts["map-observer"].seen++;
            shim.lastMap = key.slice(-48);
            var xhr = this;
            var stamp = buildFromUrl(key);
            if (stamp && !TABLE.builds[stamp] && shim.build !== stamp) {
              shim.build = stamp;
              report("unlisted build " + stamp + " -> mode=" + TABLE.defaultMode
                + "; regenerating the table with tools/compat_probe.js --write");
            }
            xhr.addEventListener("loadend", function () {
              var ok = xhr.status >= 200 && xhr.status < 400;
              var obs = shim.contracts["map-observer"];
              if (ok) obs.ok++; else obs.failed++;
              // One success is worth reporting too, with the URL the answer actually came from:
              // it is the only way to tell "the relay served this" from "the host interceptor served
              // this" without guessing, and the two have different bugs.
              if (ok && obs.ok === 1) {
                var w = "";
                try { w = String(xhr.responseURL || ""); } catch (e2) { w = "<unreadable>"; }
                report("first map ok HTTP " + xhr.status + " cameFrom=" + w.slice(-80)
                  + " len=" + (xhr.responseText || "").length);
              }
              if (!ok) {
                // The fields the host cannot see from its own log: `responseURL` is EMPTY when
                // Chromium blocks a cross-origin response (CORS, mixed content, a failed preflight)
                // and non-empty when the answer arrived and carried a status. And the HOST of the
                // request is printed in full, because the tail alone cannot tell which of the three
                // responders (this shim, the host interceptor, the real CDN) was asked -- a request
                // that reaches none of them and still returns 0 is a different bug from one the
                // relay answered wrongly. Measured 2026-11-04: the map JSON appeared in neither the
                // relay's log nor the interceptor's, with responseURL empty.
                var where = "";
                try { where = String(xhr.responseURL || ""); } catch (e) { where = "<blocked>"; }
                report("map fetch HTTP " + xhr.status + " asked=" + key.slice(0, 96)
                  + " tail=" + key.slice(-30)
                  + " type=" + xhr.responseType
                  + " finalUrl=" + (where ? where.slice(-72) : "<empty: response discarded>")
                  + " len=" + (xhr.responseText || "").length);
              }
            });
          }
          return open.apply(this, arguments);
        };
        return true;
      }
    }
  ];

  function state(id) {
    if (!shim.contracts[id]) {
      shim.contracts[id] = { bound: 0, fired: 0, skipped: 0, seen: 0, ok: 0, failed: 0,
                             selfKills: 0, vetoed: 0, budgetOut: 0 };
    }
    return shim.contracts[id];
  }
  CONTRACTS.forEach(function (c) { state(c.id); });

  function resolve(names) {
    var found = {};
    for (var i = 0; i < names.length; i++) {
      var parts = names[i].split(".");
      var here = window;
      for (var j = 0; j < parts.length && here; j++) here = here[parts[j]];
      if (!here) return null;
      found[names[i]] = here;
    }
    return found;
  }

  function installAll(api) {
    var hooked = 0;
    CONTRACTS.forEach(function (c) {
      var slot = state(c.id);
      try {
        if (c.install(api || resolve(c.required))) {
          slot.bound = 1;
          hooked++;
        } else {
          slot.skipped = 1;
          report("contract " + c.id + " not installed: target shape changed");
        }
      } catch (e) {
        slot.skipped = 1;
        shim.errors.push(c.id + ": " + e);
        report("contract " + c.id + " threw during install (" + e + ") -- skipped, client continues");
      }
    });
    shim.mode = hooked ? "hooked" : "pristine";
    report("mode=" + shim.mode + " build=" + (shim.build || "unknown")
      + " contracts=" + JSON.stringify(shim.contracts));
    return hooked;
  }

  // The facade is created by the bundle (`window.dofus = i(34)`). Trapping the assignment is the
  // loader-resolution trick: we bind the moment the object exists, before any caller touches it,
  // which a post-hoc setTimeout cannot promise. Same technique the relay already uses for Config.
  var realDofus = window.dofus, trapped = false;
  function onFacade(value) {
    if (trapped || !value) return;
    trapped = true;
    installAll({ dofus: value, XMLHttpRequest: window.XMLHttpRequest });
  }
  try {
    Object.defineProperty(window, "dofus", {
      configurable: true,
      enumerable: true,
      get: function () { return realDofus; },
      set: function (value) { realDofus = value; try { onFacade(value); } catch (e) { shim.errors.push("trap:" + e); } }
    });
  } catch (e) {
    // A browser or an earlier script that owns the property differently: fall back to polling. The
    // fallback is worse (a window where the game runs unhooked) and it says so.
    shim.errors.push("trap-unavailable:" + e);
    report("facade trap unavailable (" + e + ") -- falling back to polling");
    var tries = 0;
    var timer = setInterval(function () {
      if (window.dofus) { clearInterval(timer); installAll(resolve(CONTRACTS[0].required) || {}); }
      else if (++tries > 600) { clearInterval(timer); shim.mode = "pristine"; report("facade never appeared; running pristine"); }
    }, 50);
  }

  // Contracts that need no facade install immediately (the network seam). Installed now so no
  // request is missed while the bundle is still loading.
  state("disconnect-guard");
  try {
    var net = CONTRACTS[1];
    if (!state("map-observer").bound) {
      if (net.install()) state("map-observer").bound = 1;
    }
  } catch (e) {
    shim.errors.push("map-observer:" + e);
    report("map observer skipped (" + e + ")");
  }
  if (!shim.build) {
    // The stamp is only knowable once a URL passes; the page itself carries it in the asset query.
    var tag = document.querySelector && document.querySelector("script[src*='script.js']");
    var v = tag && /[?&]v=([^&]+)/.exec(tag.src);
    shim.pageStamp = v ? v[1] : null;
  }

  // ------------------------------------------------------------------ boot canary
  //
  // The updater's real question about a patched bundle is not "did the rules apply" -- 23/23 of
  // them applied to build 3.3.11 and the client still black-screened -- but "did the game come
  // up". Only this file sees both halves: it counts every disconnect the client tries to use on
  // itself, and it counts the map fetches that mean a world actually arrived.
  //
  // Silence is allowed and is the safe answer. No verdict leaves `boot-pending.json` in place, and
  // the next update reports the build as unjudged. A wrong "false" would blacklist a good rule set
  // for ever; a wrong "true" is precisely the lie this whole mechanism replaces.
  var canarySent = false;
  function sendCanary(ok, reason) {
    if (canarySent) return;
    var stamp = window.__DOFUS_BOOT_STAMP;
    if (!stamp) return;      // an untracked bundle has no judgement waiting on an answer
    canarySent = true;
    shim.canary = { ok: ok, reason: reason, stamp: stamp };
    try {
      if (window.chrome && window.chrome.webview && window.chrome.webview.postMessage) {
        window.chrome.webview.postMessage('{"type":"BOOT_CANARY","ok":' + (ok ? "true" : "false")
          + ',"stamp":"' + String(stamp).replace(/["\\]/g, "") + '"'
          + ',"reason":"' + String(reason || "").replace(/["\\]/g, "'").slice(0, 120) + '"}');
      }
    } catch (e) { /* a canary must never become the reason the client stops */ }
    report("boot canary ok=" + ok + " reason=" + (reason || ""));
  }

  // A teardown our own failure paths asked for, after the veto budget ran out: the session died
  // and the screen went black. That is the 3.3.11 signature, observed rather than remembered.
  function killsSeen() {
    var n = 0;
    for (var id in shim.contracts) {
      if (!shim.contracts.hasOwnProperty(id)) continue;
      n += (shim.contracts[id].budgetOut || 0);
    }
    return n;
  }

  // The facade alone is not a booted game -- it is assigned long before anything renders. A map
  // that came back 200 is the first thing that proves the client is actually in a world.
  function worldArrived() {
    var m = shim.contracts["map-observer"];
    return !!(m && m.ok > 0);
  }

  var canaryTries = 0;
  var canaryTimer = setInterval(function () {
    canaryTries++;
    if (canarySent) { clearInterval(canaryTimer); return; }
    if (killsSeen() > 0) {
      sendCanary(false, "self-inflicted disconnect survived the veto budget");
      clearInterval(canaryTimer);
      return;
    }
    if (window.dofus && worldArrived()) {
      sendCanary(true, "facade up and a map arrived");
      clearInterval(canaryTimer);
      return;
    }
    // 45 x 2 s. Past that, on a slow or half-loaded client, this is not evidence about the patch
    // set, so nothing is claimed and the interval is dropped.
    if (canaryTries > 45) {
      clearInterval(canaryTimer);
      report("boot canary: no verdict after 90 s, leaving the build unjudged");
    }
  }, 2000);

  // Read-only surface for the agent and the watchdog: `window.__compatShim`.
  shim.summary = function () {
    return { mode: shim.mode, build: shim.build, pageStamp: shim.pageStamp,
             canary: shim.canary || null,
             contracts: shim.contracts, errors: shim.errors };
  };
})();
