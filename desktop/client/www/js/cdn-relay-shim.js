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

  function rewrite(url) {
    if (!active || typeof url !== "string" || url.length === 0) return url;
    if (!cdnRe.test(url)) return url;
    var i = url.indexOf("://");
    return relayPrefix + url.slice(i + 3);
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
          var rw = rewrite(url);
          if (rw !== url) {
            this.__dtRelayOrig = url;
            this.__dtRelayMethod = method;
            arguments[1] = rw;
          }
        }
        this.__dtRelayBypass = false;
      } catch (e) { }
      return xhrOpen.apply(this, arguments);
    };

    var xhrSend = XHR.prototype.send;
    XHR.prototype.send = function () {
      var xhr = this;
      var orig = xhr.__dtRelayOrig;
      xhr.__dtRelayOrig = null;
      if (orig && !xhr.__dtRelayRetried) {
        xhr.addEventListener("loadend", function () {
          if (xhr.__dtRelayRetried) return;
          var s = xhr.status;
          if (s === 0 || s >= 400) {
            xhr.__dtRelayRetried = true;
            try {
              xhr.__dtRelayBypass = true; // do not re-rewrite the original URL
              xhr.open(xhr.__dtRelayMethod || "GET", orig, true);
              xhr.send();
            } catch (e) { }
          }
        });
      }
      return xhrSend.apply(this, arguments);
    };
  }

  // ---- fetch: string/Request input rewrite ----
  if (window.fetch) {
    var origFetch = window.fetch;
    window.fetch = function (input) {
      try {
        if (active) {
          if (typeof input === "string") {
            input = rewrite(input);
          } else if (typeof Request !== "undefined" && input instanceof Request) {
            var rw = rewrite(input.url);
            if (rw !== input.url) input = new Request(rw, input);
          }
        }
      } catch (e) { }
      return origFetch.apply(this, arguments);
    };
  }

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
        if (rw !== v && !this.__dtRelayRetryBound) {
          this.__dtRelayRetryBound = true;
          var el = this, orig = v;
          el.addEventListener("error", function () {
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
          if (rw !== value) {
            var el = this, orig = value;
            el.addEventListener("error", function () {
              try { setAttr.call(el, "src", orig); } catch (e) { }
            }, { once: true });
            value = rw;
          }
        } catch (e) { }
      }
      return setAttr.call(this, name, value);
    };
  }

  // ---- probe the local relay; activate only when it answers ----
  try {
    var probe = new XMLHttpRequest();
    probe.open("GET", "http://127.0.0.1:" + cfg.port + "/__cdn/__probe", true);
    probe.timeout = 1500;
    probe.onload = function () {
      if (probe.status === 204 || probe.status === 200) activate();
    };
    probe.onerror = probe.ontimeout = function () { /* stay inactive */ };
    probe.send();
  } catch (e) { /* stay inactive */ }
})();
