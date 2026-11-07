/* Dofus Touch CDN relay shim.
 *
 * When the local Go proxy relay is reachable (window.__DOFUS_CDN_RELAY set by
 * the host app, e.g. { enabled: true, port: 8880 }), this shim rewrites CDN
 * asset URLs  https://<host>.cdn.ankama(.com|-games.com)/(assets|ui)/...  to
 *   http://127.0.0.1:<port>/__cdn/<host>/<path>
 * so the proxy fetches them server-side with hotlink-safe headers
 * (Referer/Origin of the official web client) and caches them on disk.
 *
 * Fail-open by design:
 *  - inert when the relay config is absent/disabled or the probe fails;
 *  - XHRs retry the original CDN URL once if the relay errors or returns 4xx,
 *    which lets the C# WebResourceRequested layer (browser cookies) win;
 *  - URLs already carrying the relay prefix (e.g. built from the rewritten
 *    Config.assetsUrl) are unwrapped back to the CDN origin for that retry,
 *    so relay failures on gated assets still fall through to the intercept;
 *  - <img>/<audio> retry the original URL once on error.
 *
 * Idempotent: safe to load both as a page script and via host script
 * injection.
 */
(function () {
  "use strict";
  if (window.__DOFUS_CDN_RELAY_SHIM_INSTALLED) return;
  window.__DOFUS_CDN_RELAY_SHIM_INSTALLED = true;

  var cfg = window.__DOFUS_CDN_RELAY;
  if (!cfg || !cfg.enabled || !cfg.port) return;

  var relayPrefix = "http://127.0.0.1:" + cfg.port + "/__cdn/";
  var cdnRe = /^https?:\/\/([a-z0-9.-]*\.cdn\.ankama(?:-games)?\.com)\/(?:assets|ui)\//i;
  var active = false;
  var rewriteCount = 0;
  var retryCount = 0;
  var jsonLog = 0;
  var fetchRetryCount = 0;

  // Map negative cache: some map ids answer 403 (S3 AccessDenied) on every
  // path (relay and direct), so both the native-retry fallback and the
  // neighbour prewarm burn connection slots on losers. Remember failures for
  // MAP_FAILURE_TTL and skip ahead; any 200 on the id clears the entry.
  var mapFailures = {};
  var MAP_FAILURE_TTL = 10 * 60 * 1000;
  // Current-map priority: after a map JSON lands, hold neighbour prewarm for
  // a short quiet window so the settling map (and its tiles) own the pipes.
  var currentMapQuietUntil = 0;
  var MAP_QUIET_MS = 2500;

  // Machine-readable stats for the debug overlay (see debug-overlay.js).
  var stats = window.__DOFUS_CDN_STATS = window.__DOFUS_CDN_STATS || {
    installedAt: Date.now(),
    active: false,
    assetsUrl: "",
    rewrites: 0,
    relayRetries: 0,
    fetchRetries: 0,
    srcRetries: 0,
    prewarmMapsProcessed: 0,
    prewarmTilesFired: 0,
    currentMap: null,
    currentMapAt: 0,
    neighbourFetches: 0,
    neighbourBudgetLeft: 0,
    neighbourQueued: 0,
    mapFailures: 0,
    lastMaps: []
  };

  // 14,096 of the 16,141 page->host messages in one hour-long session were CDN_RELAY_DIAG, and
  // each one cost an IPC post, a host log line and a pass through the message parser. Two
  // consecutive identical lines carry no new information, so repeats inside a short window are
  // dropped -- but never a line that looks like a problem, and never silently: the count of what
  // was suppressed is posted as its own line, because a quiet log must not read as a healthy one.
  var diagLastText = null, diagLastAt = 0, diagDropped = 0, diagWindowMs = 5000;
  var diagImportant = /(error|fail|refus|denied|timeout|blocked|fatal|unauthor|forbidden|\b4[0-9][0-9]\b|\b5[0-9][0-9]\b|READY)/i;
  function diagSend(text) {
    window.chrome.webview.postMessage("{\"type\":\"CDN_RELAY_DIAG\",\"text\":\""
      + String(text).replace(/["\\]/g, "'") + "\"}");
  }
  function diag(text) {
    try {
      if (!window.chrome || !window.chrome.webview || !window.chrome.webview.postMessage) return;
      var now = Date.now(), t = String(text);
      if (t === diagLastText && !diagImportant.test(t) && now - diagLastAt < diagWindowMs) {
        diagDropped++;
        diagLastAt = now;          // sliding window: a steady spam stays counted, not flushed
        return;
      }
      if (diagDropped) { diagSend("diag throttle: " + diagDropped + " identical repeats suppressed"); diagDropped = 0; }
      diagLastText = t; diagLastAt = now;
      diagSend(t);
    } catch (e) { }
  }
  // Test seam and live counter: the host and the in-page diagnostics can read what the throttle
  // actually suppressed, so "the log got quieter" is a measured claim rather than an assumption.
  window.__cdnRelayProbe = {
    diag: diag,
    dropped: function () { return diagDropped; },
    reset: function () { diagLastText = null; diagLastAt = 0; diagDropped = 0; },
    // The learned version, and the escalation it drives -- readable by the host, the compat
    // layer and the test, so nobody has to infer it from request logs.
    escalate: function (url) { return escalate(url); },
    note: function (url) { return noteVersion(url); },
    versions: function () { return versionSeen; },
    resetVersions: function () { versionSeen = {}; },
    // the negative cache, so a test can prove a superseded version voids it -- and that learning
    // the SAME version does not, which is the arm that would pass on a careless implementation.
    markFail: function (id, status) { markMapFailure(id, status); },
    failedMaps: function () { return Object.keys(mapFailures); }
  };
  // Seed from the stylesheet as soon as the DOM exists: its 2,000+ ui paths carry the version
  // the build was made against, and background images never pass the hooks that would teach us.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", learnFromStylesheets, { once: true });
  } else {
    learnFromStylesheets();
  }
  diag("shim installed (port " + cfg.port + ")");

  // ---- the asset version is LEARNED, never assumed --------------------------
  // Why: the client is told its asset version by the login server, and during a rollout that
  // value lags the CDN. That is the whole 2026-11-04 mess: requests for
  // /assets/3.3.10_<token>/maps/<id>.json came back 403 (S3 has no such key any more) while
  // /assets/3.3.11_<token>/maps/<id>.json answers 200 -- and the old behaviour was to
  // negative-cache the map id for ten minutes and give up. Nobody may write "3.3.11" into this
  // file: the next push would break it again. So we watch the versions that actually appear in
  // live traffic, keep the newest per asset kind, and on a denial retry the same path at the
  // newer version. One shared, learned variable -- `window.__DOFUS_CDN_STATS.assetVersions`.
  //
  // Compare numerically: as strings "3.3.9" sorts ABOVE "3.3.10", so a string max would pin the
  // oldest version forever. That trap is why this is a tuple compare and not a Math.max on text.
  var VER_RE = /\/assets\/(?:(ui)\/)?(\d+)\.(\d+)\.(\d+)_([^/?]+)/;
  var versionSeen = {};

  function parseVersion(url) {
    var m = VER_RE.exec(String(url || ""));
    if (!m) return null;
    return { kind: m[1] ? "ui" : "assets", major: +m[2], minor: +m[3], patch: +m[4],
             token: m[5], raw: m[2] + "." + m[3] + "." + m[4] + "_" + m[5] };
  }

  function versionCmp(a, b) {
    return (a.major - b.major) || (a.minor - b.minor) || (a.patch - b.patch);
  }

  function noteVersion(url) {
    var v = parseVersion(url);
    if (!v) return null;
    var best = versionSeen[v.kind];
    if (!best || versionCmp(v, best) > 0) {
      versionSeen[v.kind] = v;
      stats.assetVersions = stats.assetVersions || {};
      stats.assetVersions[v.kind] = v.major + "." + v.minor + "." + v.patch;
      stats.assetVersionTokens = stats.assetVersionTokens || {};
      stats.assetVersionTokens[v.kind] = v.token;
      diag("learned " + v.kind + " version " + stats.assetVersions[v.kind]);
      if (v.kind === "assets" && best) {
        // A 403 was a verdict about a version we have now superseded, and the negative cache
        // lasts ten minutes -- long enough to keep a perfectly good map from loading once the
        // rollout caught up. Superseding the version voids those verdicts.
        var voided = 0;
        for (var k in mapFailures) {
          if (Object.prototype.hasOwnProperty.call(mapFailures, k)) { delete mapFailures[k]; voided++; }
        }
        if (voided) diag("cleared " + voided + " negative-cache map id(s): assets went "
          + best.major + "." + best.minor + "." + best.patch + " -> "
          + v.major + "." + v.minor + "." + v.patch);
      }
    }
    return v;
  }

  // Rewrite a denied URL at the newest version we have seen for that same asset kind.
  // Returns null when there is nothing newer to try -- which is what keeps this from
  // looping: escalation only ever moves forward, and a current version escalates to nothing.
  function escalate(url) {
    var v = parseVersion(url);
    if (!v) return null;
    var best = versionSeen[v.kind];
    if (!best || best.raw === v.raw || versionCmp(best, v) <= 0) return null;
    return String(url).split(v.raw).join(best.raw);
  }

  // Seed the table from our own stylesheet: the updater regenerates it from the vendor build,
  // so its 2,000+ ui paths carry the version the *build* expects, and background-image URLs
  // never cross the XHR/img hooks that would otherwise teach us.
  function learnFromStylesheets() {
    try {
      var links = document.querySelectorAll("link[rel=stylesheet], link[href*='styles']");
      for (var i = 0; i < links.length; i++) {
        (function (href) {
          try {
            var p = window.fetch ? window.fetch(href, { cache: "force-cache" }) : null;
            if (p && p.then) p.then(function (r) { return r.text(); }).then(function (css) {
              var seen = 0;
              String(css).replace(/\/assets\/(?:ui\/)?\d+\.\d+\.\d+_[^/"'\s)]+/g, function (m) {
                if (seen++ < 4) noteVersion("https://seed.local" + m);
                return m;
              });
              if (seen) diag("seeded " + seen + " version reference(s) from stylesheet");
            }, function () { });
          } catch (e) { }
        })(links[i].href);
      }
    } catch (e) { }
  }

  function rewrite(url) {
    if (!active || typeof url !== "string" || url.length === 0) return url;
    if (!cdnRe.test(url)) return url;
    noteVersion(url);
    var i = url.indexOf("://");
    rewriteCount++;
    stats.rewrites = rewriteCount;
    if (rewriteCount <= 2 || rewriteCount % 100 === 0) diag("rewrite #" + rewriteCount + " " + url.slice(0, 120));
    if (jsonLog < 40 && /\.json(\?|$)|\/maps\//i.test(url)) {
      jsonLog++;
      diag("jsonrewrite #" + jsonLog + " " + url.slice(0, 130));
    }
    return relayPrefix + url.slice(i + 3);
  }

  function unwrapRelayUrl(url) {
    if (typeof url !== "string" || url.length === 0) return null;
    if (url.lastIndexOf(relayPrefix, 0) !== 0) return null;
    return "https://" + url.slice(relayPrefix.length);
  }

  function rewriteConfig(c) {
    if (!c || typeof c !== "object") return;
    if (c.assetsUrl) c.assetsUrl = rewrite(c.assetsUrl);
    if (c.uiUrl) c.uiUrl = rewrite(c.uiUrl);
    if (c.dataUrl) c.dataUrl = rewrite(c.dataUrl);
  }

  function activate() {
    active = true;
    rewriteConfig(window.Config);
    stats.active = true;
    stats.assetsUrl = (window.Config && window.Config.assetsUrl) || "";
    diag("activated (assetsUrl=" + ((window.Config && window.Config.assetsUrl) || "n/a") + ")");
  }

  // ---- Config hook: rewrite assetsUrl/uiUrl/dataUrl as soon as it is set ----
  try {
    var current = window.Config;
    Object.defineProperty(window, "Config", {
      configurable: true,
      enumerable: true,
      get: function () { return current; },
      set: function (v) { rewriteConfig(v); current = v; }
    });
    rewriteConfig(current);
  } catch (e) { /* keep original assignment path */ }

  // ---- XMLHttpRequest: rewrite + one retry on the original CDN URL ----
  var XHR = window.XMLHttpRequest;
  if (XHR && XHR.prototype && XHR.prototype.open) {
    var xhrOpen = XHR.prototype.open;
    XHR.prototype.open = function (method, url) {
      try {
        if (active && !this.__dtRelayBypass) {
          var unwrapped = unwrapRelayUrl(url);
          if (unwrapped) {
            // Already relay-prefixed (built from rewritten Config.assetsUrl).
            // Keep the relay URL now, but remember the CDN URL so a 4xx can
            // fall back to the native/intercept path.
            this.__dtRelayOrig = unwrapped;
            this.__dtRelayMethod = method;
          } else {
            var rw = rewrite(url);
            if (rw !== url) {
              this.__dtRelayOrig = url;
              this.__dtRelayMethod = method;
              arguments[1] = rw;
            }
          }
        }
        this.__dtRelayBypass = false;
        this.__dtMapID = extractMapID(url);
      } catch (e) { }
      return xhrOpen.apply(this, arguments);
    };

    // Track headers applied between open() and send() so the fallback retry
    // can faithfully replay the request (open() resets responseType and
    // nukes previously set headers; losing responseType would hand the game
    // a string where it expects an ArrayBuffer).
    var xhrSetHeader = XHR.prototype.setRequestHeader;
    XHR.prototype.setRequestHeader = function (name, value) {
      try {
        if (!this.__dtRelayHeaders) this.__dtRelayHeaders = [];
        this.__dtRelayHeaders.push([name, value]);
      } catch (e) { }
      return xhrSetHeader.apply(this, arguments);
    };

    var escalationCount = 0;

  // One faithful replay of an XHR at a different URL. open() resets responseType and drops
  // headers, and losing responseType is how the game gets handed a string where it expects an
  // ArrayBuffer -- the TypeError still recurring in tonight's log.
  function replayXhr(xhr, url, reason) {
    try {
      var responseType = xhr.responseType;
      var withCredentials = xhr.withCredentials;
      var timeout = xhr.timeout;
      var headers = xhr.__dtRelayHeaders;
      xhr.__dtRelayBypass = true;   // do not re-rewrite: this attempt goes straight to the CDN
      xhr.open(xhr.__dtRelayMethod || "GET", url, true);
      try { xhr.responseType = responseType; } catch (e) { }
      xhr.withCredentials = withCredentials;
      if (timeout) xhr.timeout = timeout;
      if (headers) for (var i = 0; i < headers.length; i++) {
        try { xhr.setRequestHeader(headers[i][0], headers[i][1]); } catch (e) { }
      }
      xhr.__dtRelayHeaders = [];
      xhr.send();
      return true;
    } catch (e) {
      diag("replay failed (" + reason + "): " + e);
      return false;
    }
  }

  var xhrSend = XHR.prototype.send;
    XHR.prototype.send = function () {
      var xhr = this;
      if (xhr.__dtMapID && !xhr.__dtPrewarmBound) {
        xhr.__dtPrewarmBound = true;
        var mid = xhr.__dtMapID;
        xhr.addEventListener("loadend", function () { prewarmFromXHR(xhr, mid); });
      }
      var orig = xhr.__dtRelayOrig;
      xhr.__dtRelayOrig = null;
      if (orig && !xhr.__dtRelayRetried) {
        xhr.addEventListener("loadend", function () {
          if (xhr.__dtRelayRetried) return;
          var s = xhr.status;
          // Escalate on any non-answer for a map: 403/404 (denied at a stale version) and 0
          // (never answered at all -- the status tonight's client reported for a map that the CDN
          // serves with 200). One attempt only, then the loader sees whatever it saw before.
          var up = (!xhr.__dtEscalated && xhr.__dtMapID
                    && (s === 0 || s === 403 || s === 404)) ? escalate(orig) : null;
          if (up) {
            xhr.__dtEscalated = true;
            xhr.__dtRelayRetried = true;   // this is the last attempt the loader will see
            escalationCount++;
            stats.escalations = escalationCount;
            diag("version-escalate map " + xhr.__dtMapID + " after status " + s + " -> " + up.slice(-96));
            replayXhr(xhr, up, "escalate");
            return;
          }
          if (xhr.__dtMapID && (s === 403 || s === 404)) {
            // Deterministic per-id CDN denial at the newest version we know: the native retry hits
            // the same S3 answer, so skip it and negative-cache the map id instead.
            markMapFailure(xhr.__dtMapID, s);
            return;
          }
          if (s === 0 || s >= 400) {
            xhr.__dtRelayRetried = true;
            retryCount++;
            stats.relayRetries = retryCount;
            if (retryCount <= 25 || retryCount % 50 === 0) diag("retry #" + retryCount + " (relay gave " + s + ") " + String(orig).slice(0, 130));
            replayXhr(xhr, orig, "relay " + s);
          }
        });
      }
      return xhrSend.apply(this, arguments);
    };
  }

  // ---- fetch: string/Request input rewrite, one native retry on 4xx/5xx ----
  if (window.fetch) {
    var origFetch = window.fetch;
    window.fetch = function (input, init) {
      var nativeFallback = null;
      var mapID = active ? extractMapID(typeof input === "string" ? input : (input && input.url) || "") : null;
      try {
        if (active) {
          if (typeof input === "string") {
            var unwrapped = unwrapRelayUrl(input);
            if (unwrapped) {
              nativeFallback = unwrapped;
            } else {
              input = rewrite(input);
            }
          } else if (typeof Request !== "undefined" && input instanceof Request) {
            var unwrappedReq = unwrapRelayUrl(input.url);
            if (unwrappedReq) {
              nativeFallback = unwrappedReq;
            } else {
              var rw = rewrite(input.url);
              if (rw !== input.url) input = new Request(rw, input);
            }
          }
        }
      } catch (e) { }
      var p = origFetch.call(this, input, init);
      if (nativeFallback && p && typeof p.then === "function") {
        p = p.then(function (res) {
          if (res && res.status >= 400) {
            if (mapID && (res.status === 403 || res.status === 404)) {
              // Deterministic per-id denial (S3 AccessDenied): the native
              // path returns the same status. Skip the retry; the mapID
              // branch below records the failure.
              return res;
            }
            fetchRetryCount++;
            stats.fetchRetries = fetchRetryCount;
            if (fetchRetryCount <= 25) diag("fetchretry #" + fetchRetryCount + " (relay gave " + res.status + ") " + nativeFallback.slice(0, 130));
            return origFetch.call(window, nativeFallback, init);
          }
          return res;
        });
      }
      if (mapID && p && typeof p.then === "function") {
        (function (mid) {
          p = p.then(function (res) {
            try {
              if (res && res.status === 200 && res.clone) {
                res.clone().text().then(function (t) { prewarmMapTiles(mid, t); }, function () { });
              } else if (res && (res.status === 403 || res.status === 404)) {
                markMapFailure(mid, res.status);
              }
            } catch (e) { }
            return res;
          });
        })(mapID);
      }
      return p;
    };
  }

  var srcRetryCount = 0;

  // ---- src setters: rewrite + one retry on the original CDN URL ----
  function patchSrcSetter(proto) {
    if (!proto) return;
    var d = Object.getOwnPropertyDescriptor(proto, "src");
    if (!d || !d.set) return;
    Object.defineProperty(proto, "src", {
      configurable: true,
      enumerable: d.enumerable,
      get: d.get,
      set: function (v) {
        var rw = active ? rewrite(v) : v;
        var nativeOrig = null;
        if (active && rw === v) nativeOrig = unwrapRelayUrl(v);
        if ((rw !== v || nativeOrig) && !this.__dtRelayRetryBound) {
          this.__dtRelayRetryBound = true;
          var el = this, orig = nativeOrig || v;
          el.addEventListener("error", function () {
            srcRetryCount++;
              stats.srcRetries = srcRetryCount;
            // Same ladder as the XHR path: a stylesheet or atlas image denied at a stale version
            // exists one version up, and asking there is cheaper than shipping a page with holes.
            var up = escalate(orig);
            if (up && !el.__dtSrcEscalated) {
              el.__dtSrcEscalated = true;
              escalationCount++;
              stats.escalations = escalationCount;
              diag("version-escalate src after error -> " + up.slice(-96));
              el.addEventListener("error", function () {
                try { d.set.call(el, orig); } catch (e) { }
              }, { once: true });
              try { d.set.call(el, up); } catch (e) { }
              return;
            }
            if (srcRetryCount <= 25) diag("srcretry #" + srcRetryCount + " " + String(orig).slice(0, 130));
            try { d.set.call(el, orig); } catch (e) { }
          }, { once: true });
        }
        d.set.call(this, rw);
      }
    });
  }
  patchSrcSetter(window.HTMLImageElement && HTMLImageElement.prototype);
  patchSrcSetter(window.HTMLMediaElement && HTMLMediaElement.prototype);

  // ---- setAttribute("src", ...) path (asset loaders often use it) ----
  if (window.Element && Element.prototype && Element.prototype.setAttribute) {
    var setAttr = Element.prototype.setAttribute;
    Element.prototype.setAttribute = function (name, value) {
      if (active && name === "src") {
        try {
          var rw = rewrite(value);
          var nativeOrig = null;
          if (rw === value) nativeOrig = unwrapRelayUrl(value);
          if (rw !== value || nativeOrig) {
            var el = this, orig = nativeOrig || value;
            el.addEventListener("error", function () {
              srcRetryCount++;
              stats.srcRetries = srcRetryCount;
              if (srcRetryCount <= 25) diag("srcretry #" + srcRetryCount + " " + String(orig).slice(0, 130));
              try { setAttr.call(el, "src", orig); } catch (e) { }
            }, { once: true });
            value = rw;
          }
        } catch (e) { }
      }
      return setAttr.call(this, name, value);
    };
  }

  // ---- Map tile prewarm ----
  // The game's atlas loader requests map tiles gradually as it decodes them,
  // so a cold map takes many seconds even though the files are tiny. When a
  // map JSON arrives, pre-issue every tile it references as an Image load:
  // Chromium fetches the whole set concurrently and the loader's own later
  // requests coalesce with / hit these. Goes through the same rewrite rules,
  // so it works with or without the relay.
  var prewarmMaps = {};
  var prewarmTiles = {};
  var prewarmEls = [];
  var prewarmLog = 0;
  var prewarmTotal = 0;
  var prewarmRequested = {};
  var prewarmNeighbourBudget = 60;
  var prewarmMaxNeighbourDepth = 2;
  stats.neighbourBudgetLeft = prewarmNeighbourBudget;

  function extractMapID(url) {
    try {
      var m = /\/maps\/(\d+)\.json/i.exec(String(url || ""));
      return m ? m[1] : null;
    } catch (e) { return null; }
  }

  function mapRecentlyFailed(id) {
    var t = mapFailures[id];
    return !!t && (Date.now() - t) < MAP_FAILURE_TTL;
  }

  function markMapFailure(id, status) {
    if (!id) return;
    var t = mapFailures[id];
    if (t && (Date.now() - t) < 5000) return; // already marked moments ago
    if (!t) stats.mapFailures = (stats.mapFailures || 0) + 1;
    mapFailures[id] = Date.now();
    diag("mapfail " + id + " status=" + status + " (negative cache " + (MAP_FAILURE_TTL / 60000) + " min)");
  }

  function clearMapFailure(id) {
    if (id && mapFailures[id]) delete mapFailures[id];
  }

  function prewarmMapTiles(id, text) {
    if (!active || !id || !text) return;
    clearMapFailure(id);
    if (prewarmMaps[id]) return;
    prewarmMaps[id] = true;
    // Hold neighbour prewarm briefly: the map that just landed owns the
    // connection pool until it has settled (current-map priority).
    currentMapQuietUntil = Date.now() + MAP_QUIET_MS;
    var data = null;
    try { data = JSON.parse(text); } catch (e) { return; }
    var base = (window.Config && window.Config.assetsUrl) || "";
    // Config.assetsUrl carries no trailing slash; join exactly one.
    if (base) base = base.replace(/\/+$/, "") + "/";
    var positions = data && data.atlasLayout && data.atlasLayout.graphicsPositions;
    if (!base || !positions) return;
    var fired = 0;
    for (var g in positions) {
      if (!Object.prototype.hasOwnProperty.call(positions, g)) continue;
      if (prewarmTiles[g]) continue;
      prewarmTiles[g] = true;
      var ent = positions[g];
      var jpg = !!(ent && ent.jpg);
      try {
        var im = new Image();
        im.decoding = "async";
        im.src = base + "gfx/world/" + (jpg ? "jpg/" + g + ".jpg" : "png/" + g + ".png");
        prewarmEls.push(im);
        if (prewarmEls.length > 3000) prewarmEls.shift();
        fired++;
      } catch (e) { }
    }
    try {
      var bg = new Image();
      bg.src = base + "backgrounds/" + id + ".jpg";
      prewarmEls.push(bg);
    } catch (e) { }
    prewarmTotal += fired;
    stats.prewarmMapsProcessed++;
    stats.prewarmTilesFired = prewarmTotal;
    stats.currentMap = id;
    stats.currentMapAt = Date.now();
    stats.lastMaps.push({ id: id, tiles: fired, ts: Date.now() });
    if (stats.lastMaps.length > 20) stats.lastMaps.shift();
    if (prewarmLog < 40) {
      prewarmLog++;
      diag("prewarm map " + id + " tiles=" + fired + " total=" + prewarmTotal);
    }
    warmNeighbourMaps(data, base, id);
  }

  // Predictive warming: issue JSON fetches for the maps behind this map's
  // exits so their tiles are already hot when the player walks through. The
  // fetch hook above re-enters prewarmMapTiles for each fetched map; depth is
  // tracked per map id (maps the game loads itself reset to depth 0) and the
  // number of neighbour fetches is budget-capped per session.
  //
  // Fetches are queued and drip-fed by pumpPrewarmQueue instead of fired as
  // one burst: earlier storms of neighbour requests (many doomed 403s, each
  // taking seconds) saturated the pool and delayed the current map, which is
  // exactly what we must not do.
  function warmNeighbourMaps(data, base, id) {
    if (!data || !base || prewarmNeighbourBudget <= 0) return;
    var depth = prewarmRequested[id] || 0;
    if (depth >= prewarmMaxNeighbourDepth) return;
    var dirs = ["topNeighbourId", "bottomNeighbourId", "leftNeighbourId", "rightNeighbourId"];
    for (var i = 0; i < dirs.length; i++) {
      var nid = data[dirs[i]];
      if (typeof nid !== "number" || nid <= 0) continue;
      if (prewarmMaps[nid] || prewarmRequested[nid]) continue;
      if (mapRecentlyFailed(nid)) continue;
      prewarmRequested[nid] = depth + 1;
      prewarmQueue.push({ id: nid, depth: depth + 1, url: base + "maps/" + nid + ".json" });
      stats.neighbourQueued = (stats.neighbourQueued || 0) + 1;
    }
    pumpPrewarmQueue();
  }

  var prewarmQueue = [];
  var prewarmPumpTimer = null;

  function pumpPrewarmQueue() {
    if (prewarmPumpTimer) return;
    prewarmPumpTimer = setTimeout(function () {
      prewarmPumpTimer = null;
      if (prewarmQueue.length === 0) return;
      if (prewarmNeighbourBudget <= 0) { prewarmQueue.length = 0; return; }
      if (Date.now() < currentMapQuietUntil) { pumpPrewarmQueue(); return; }
      var item = prewarmQueue.shift();
      if (!item) return;
      if (prewarmMaps[item.id] || mapRecentlyFailed(item.id)) { pumpPrewarmQueue(); return; }
      prewarmNeighbourBudget--;
      stats.neighbourFetches++;
      stats.neighbourBudgetLeft = prewarmNeighbourBudget;
      diag("prewarm neighbour " + item.id + " depth=" + item.depth + " budget=" + prewarmNeighbourBudget);
      try {
        var p = window.fetch(item.url);
        if (p && p.then) p.then(function () { }, function () { });
      } catch (e) { }
      pumpPrewarmQueue();
    }, 400);
  }

  function prewarmFromXHR(xhr, id) {
    try {
      if (!xhr) return;
      if (xhr.status === 403 || xhr.status === 404) { markMapFailure(id, xhr.status); return; }
      if (xhr.status !== 200) return;
      var txt = null;
      if (xhr.responseType === "" || xhr.responseType === "text") {
        txt = xhr.responseText;
      } else if (xhr.responseType === "json") {
        txt = JSON.stringify(xhr.response);
      } else if (xhr.responseType === "arraybuffer" && xhr.response) {
        txt = new TextDecoder().decode(new Uint8Array(xhr.response));
      }
      if (txt) prewarmMapTiles(id, txt);
    } catch (e) { }
  }

  // ---- probe the local relay; activate only when it answers ----
  try {
    var probe = new XMLHttpRequest();
    probe.open("GET", "http://127.0.0.1:" + cfg.port + "/__cdn/__probe", true);
    probe.timeout = 1500;
    probe.onload = function () {
      diag("probe status " + probe.status);
      if (probe.status === 204 || probe.status === 200) activate();
    };
    probe.onerror = function () { diag("probe network error (relay unreachable / blocked)"); };
    probe.ontimeout = function () { diag("probe timed out"); };
    probe.send();
  } catch (e) { /* stay inactive */ }

  diag("shim ready (rewrites=" + rewriteCount + " retries=" + retryCount + ")");
})();
