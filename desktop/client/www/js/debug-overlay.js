/* Dofus Touch debug overlay.
 *
 * Press F2 to toggle. Reads the machine-readable stats published by
 * cdn-relay-shim.js (window.__DOFUS_CDN_STATS) and samples FPS + JS heap.
 * Tooling only: attaches no game hooks, sends nothing anywhere, and stays
 * hidden until toggled.
 */
(function () {
  "use strict";
  if (window.__DOFUS_DEBUG_OVERLAY_INSTALLED) return;
  window.__DOFUS_DEBUG_OVERLAY_INSTALLED = true;

  var panel = null;
  var visible = false;
  var fps = 0;
  var frames = 0;
  var fpsFrom = performance.now();

  (function sampleFps() {
    frames++;
    var now = performance.now();
    if (now - fpsFrom >= 500) {
      fps = Math.round((frames * 1000) / (now - fpsFrom));
      frames = 0;
      fpsFrom = now;
    }
    requestAnimationFrame(sampleFps);
  })();

  function fmt(v) {
    return (v === null || v === undefined) ? "-" : String(v);
  }

  function ensurePanel() {
    if (panel) return;
    panel = document.createElement("div");
    panel.id = "dofusDebugOverlay";
    panel.style.cssText = [
      "position:fixed",
      "top:8px",
      "left:8px",
      "z-index:2147483647",
      "pointer-events:none",
      "font:11px/1.5 Consolas,monospace",
      "color:#cdd6f4",
      "background:rgba(10,12,18,0.82)",
      "border:1px solid #2a3350",
      "border-radius:6px",
      "padding:6px 9px",
      "white-space:pre",
      "display:none"
    ].join(";");
    var root = document.body || document.documentElement;
    root.appendChild(panel);
    setInterval(render, 500);
  }

  function render() {
    if (!panel) return;
    var s = window.__DOFUS_CDN_STATS;
    var relay = window.__DOFUS_CDN_RELAY;
    var lines = [];

    lines.push("DOFUS debug        F2 hide        fps " + fps);

    if (window.performance && performance.memory) {
      lines.push("heap " + Math.round(performance.memory.usedJSHeapSize / 1048576) + " MB / " +
        Math.round(performance.memory.jsHeapSizeLimit / 1048576) + " MB");
    }

    if (!s) {
      lines.push("cdn shim: inactive (no __DOFUS_CDN_STATS)");
      panel.textContent = lines.join("\n");
      return;
    }

    var mapAge = s.currentMapAt ? Math.round((Date.now() - s.currentMapAt) / 1000) + "s" : "-";
    lines.push("relay " + (s.active ? "active" : "inactive") +
      (relay && relay.port ? " :" + relay.port : "") +
      "        map " + fmt(s.currentMap) + " (" + mapAge + " ago)");
    lines.push("prewarm: maps " + s.prewarmMapsProcessed +
      "  tiles " + s.prewarmTilesFired +
      "  neighbour fetches " + s.neighbourFetches +
      " (budget " + s.neighbourBudgetLeft + ")");
    lines.push("rewrites " + s.rewrites +
      "  retries: relay " + s.relayRetries +
      " fetch " + s.fetchRetries +
      " src " + s.srcRetries);

    var recent = (s.lastMaps || []).slice(-5).reverse();
    if (recent.length > 0) {
      lines.push("recent maps: " + recent.map(function (m) {
        return m.id + "(" + m.tiles + ")";
      }).join("  "));
    }

    panel.textContent = lines.join("\n");
  }

  function show() {
    ensurePanel();
    visible = true;
    panel.style.display = "block";
    render();
  }

  function hide() {
    visible = false;
    if (panel) panel.style.display = "none";
  }

  function toggle() {
    if (visible) hide();
    else show();
  }

  window.addEventListener("keydown", function (e) {
    if (e.key === "F2") {
      e.preventDefault();
      toggle();
    }
  }, true);

  window.__DOFUS_OVERLAY = {
    show: show,
    hide: hide,
    toggle: toggle,
    isVisible: function () { return visible; }
  };
})();
