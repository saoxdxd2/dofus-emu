/* Dofus Touch wizAssets local-file layer.
 *
 * The official mobile client routes EVERY asset through window.wizAssets:
 * downloadFile(cdnUrl, destPath) downloads the file once to persistent device
 * storage and callbacks with a LOCAL file URI; images and JSONs then load
 * from that local file. The plain web build has no wizAssets, so it
 * re-downloads assets over the network with only the browser cache as a
 * store.
 *
 * This shim replicates the mobile behaviour on top of the local relay:
 *  - window.__DOFUS_CDN_RELAY.cacheBase (https://assets.local/) is a WebView2
 *    virtual host mapped to the relay cache mirror; loading from it is a pure
 *    local file read - no HTTP connection limits, no CDN round-trip.
 *  - GET /__files lists everything already mirrored (fetched once, async).
 *  - GET /__warm ensures a cold asset is downloaded and mirrored via the
 *    relay (which also triggers neighbour-map prefetching for map JSONs).
 *
 * Fail-open: when the relay config is absent or disabled the shim stays
 * inert and the game keeps its built-in web fallback (direct CDN URLs,
 * rewritten to the relay by cdn-relay-shim.js).
 */
(function () {
  "use strict";
  if (window.wizAssets) return;
  var cfg = window.__DOFUS_CDN_RELAY;
  if (!cfg || !cfg.enabled || !cfg.port) return;

  var relayBase = "http://127.0.0.1:" + cfg.port;
  var cacheBase = typeof cfg.cacheBase === "string" ? cfg.cacheBase : "";
  var knownFiles = null; // rel path -> 1; null until the index arrives
  var pending = {};      // rel path -> [{s: success, f: failure}]
  var diagCount = 0;

  window.WizAssetsError = window.WizAssetsError || {
    ARGS_MISSING_ERROR: 1, INVALID_URL_ERROR: 2, CONNECTIVITY_ERROR: 3,
    HTTP_REQUEST_ERROR: 4, HTTP_REQUEST_CONTENT_ERROR: 5,
    DIRECTORY_CREATION_ERROR: 6, FILE_CREATION_ERROR: 7, JSON_CREATION_ERROR: 8,
    INITIALIZATION_ERROR: 9, NOT_FOUND_ERROR: 10, UNREFERENCED_ERROR: 11
  };
  var E = window.WizAssetsError;

  function diag(text) {
    if (diagCount >= 80) return;
    diagCount++;
    try {
      if (window.chrome && window.chrome.webview && window.chrome.webview.postMessage) {
        window.chrome.webview.postMessage("{\"type\":\"CDN_RELAY_DIAG\",\"text\":\"" + String(text).replace(/["\\]/g, "'") + "\"}");
      }
    } catch (e) { }
  }

  // relPathOf maps an accepted asset URL to "<cdn host>/<path>", the key
  // shared with the relay's mirror tree. Accepts relay-wrapped URLs
  // (http://127.0.0.1:<port>/__cdn/<host>/<path>) and raw CDN URLs.
  function relPathOf(url) {
    try {
      var s = String(url || "");
      if (s.length === 0) return null;
      var m = /^http:\/\/127\.0\.0\.1:\d+\/__cdn\/([^\/?#]+)(\/[^?#]*)/.exec(s);
      if (m) return m[1] + m[2];
      m = /^https?:\/\/([a-z0-9.-]*\.cdn\.ankama(?:-games)?\.com)(\/[^?#]*)/i.exec(s);
      if (m) return m[1] + m[2];
      return null;
    } catch (e) { return null; }
  }

  function hydrateIndex() {
    try {
      var x = new XMLHttpRequest();
      x.open("GET", relayBase + "/__files", true);
      x.onload = function () {
        try {
          var list = JSON.parse(x.responseText);
          knownFiles = {};
          for (var i = 0; i < list.length; i++) knownFiles[list[i]] = 1;
          diag("files index " + list.length);
        } catch (e) { diag("files index parse error"); }
      };
      x.onerror = function () { diag("files index fetch error"); };
      x.send();
    } catch (e) { }
  }

  function warm(rel, onDone) {
    try {
      var x = new XMLHttpRequest();
      x.open("GET", relayBase + "/__warm?u=" + encodeURIComponent("https://" + rel), true);
      x.onload = function () {
        if (x.status === 200) onDone(null);
        else onDone(E.HTTP_REQUEST_ERROR);
      };
      x.onerror = function () { onDone(E.CONNECTIVITY_ERROR); };
      x.send();
    } catch (e) { onDone(E.CONNECTIVITY_ERROR); }
  }

  function downloadFile(url, filePath, success, fail) {
    var rel = relPathOf(url);
    if (!rel) {
      if (fail) fail(E.INVALID_URL_ERROR);
      return;
    }
    var local = cacheBase ? cacheBase + rel : url;
    var okCb = function () { if (success) success(local); };
    var errCb = function (code) { if (fail) fail(code); };
    if (knownFiles && knownFiles[rel]) { okCb(); return; }
    var queue = pending[rel];
    if (queue) { queue.push({ s: okCb, f: errCb }); return; }
    queue = pending[rel] = [{ s: okCb, f: errCb }];
    warm(rel, function (errCode) {
      var q = pending[rel] || [];
      delete pending[rel];
      if (errCode) {
        diag("warm FAIL " + errCode + " " + rel.slice(-70));
        for (var i = 0; i < q.length; i++) { if (q[i].f) q[i].f(errCode); }
        return;
      }
      if (knownFiles) knownFiles[rel] = 1;
      for (var j = 0; j < q.length; j++) { if (q[j].s) q[j].s(); }
    });
  }

  function deleteFile(uri, success, fail) {
    var rel = relPathOf(uri) || String(uri || "").replace(/^\//, "");
    if (!rel) { if (fail) fail(E.NOT_FOUND_ERROR); return; }
    try {
      var x = new XMLHttpRequest();
      x.open("GET", relayBase + "/__delete?p=" + encodeURIComponent(rel), true);
      x.onload = function () {
        try {
          var res = JSON.parse(x.responseText);
          if (x.status === 200 && res && res.removed > 0) {
            if (knownFiles) delete knownFiles[rel];
            if (success) success();
          } else if (fail) fail(E.NOT_FOUND_ERROR);
        } catch (e) { if (fail) fail(E.UNREFERENCED_ERROR); }
      };
      x.onerror = function () { if (fail) fail(E.CONNECTIVITY_ERROR); };
      x.send();
    } catch (e) { if (fail) fail(E.UNREFERENCED_ERROR); }
  }

  window.wizAssets = {
    initialize: function (success) {
      hydrateIndex();
      if (success) success();
    },
    downloadFile: downloadFile,
    deleteFile: deleteFile,
    deleteFiles: function (uris, success, fail) {
      if (!uris || !uris.length) { if (success) success(); return; }
      var left = uris.length;
      var tick = function () { if (--left <= 0 && success) success(); };
      for (var i = 0; i < uris.length; i++) {
        deleteFile(uris[i], tick, tick);
      }
    },
    getFileURI: function (uri, success, fail) {
      var rel = relPathOf(uri);
      if (rel && knownFiles && knownFiles[rel] && cacheBase) {
        if (success) success(cacheBase + rel);
      } else if (fail) fail(E.NOT_FOUND_ERROR);
    },
    getFileURIs: function (success) {
      var out = [];
      if (knownFiles && cacheBase) {
        for (var rel in knownFiles) { if (knownFiles[rel]) out.push(cacheBase + rel); }
      }
      if (success) success(out);
    }
  };
  diag("wizAssets shim ready (cacheBase=" + (cacheBase || "none") + ")");
})();
