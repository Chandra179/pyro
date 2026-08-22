// Dashboard behavior: off-canvas sidebar, dark-mode toggle, react flow graph mounting, modal
// backdrop. Loaded with `defer`. Theme bootstrap stays inline in base.html's <head> — it must
// run before first paint, which a deferred external file can't do.

// --- off-canvas sidebar (below md/768px) ----------------------------------------------------
// Global because base.html's backdrop uses an inline onclick.
function toggleSidebar(open) {
  var sidebar = document.getElementById("sidebar");
  var backdrop = document.getElementById("sidebar-backdrop");
  var isOpen = open !== undefined ? open : sidebar.classList.contains("-translate-x-full");
  sidebar.classList.toggle("-translate-x-full", !isOpen);
  sidebar.classList.toggle("translate-x-0", isOpen);
  backdrop.classList.toggle("hidden", !isOpen);
}

document.getElementById("sidebar-toggle").addEventListener("click", function () {
  toggleSidebar();
});

// --- dark mode ------------------------------------------------------------------------------
(function () {
  var root = document.documentElement;
  var sun = document.getElementById("icon-sun");
  var moon = document.getElementById("icon-moon");

  function syncIcons() {
    var isDark = root.classList.contains("dark");
    sun.classList.toggle("hidden", !isDark);
    moon.classList.toggle("hidden", isDark);
  }

  syncIcons();
  document.getElementById("theme-toggle").addEventListener("click", function () {
    root.classList.toggle("dark");
    localStorage.setItem("theme", root.classList.contains("dark") ? "dark" : "light");
    syncIcons();
  });
})();

// --- react flow (interactive entity graph) ---------------------------------------------------
// Server sends `<div class="react-flow-graph" data-elements="...">` (api/render.py); this scans
// for it and mounts GraphIsland.jsx, bundled into graph-island.bundle.js by `npm run build:js`.
// Theme is read once at mount (static/src/graph/main.jsx) — an on-screen diagram doesn't repaint
// if dark mode toggles after the fact, only a fresh render does.

// Loaded on demand (~380KB) on first `.react-flow-graph` appearance rather than unconditionally,
// since most pages never render one. Must be DOM-driven, not route-driven: the Data page's tab
// switch is a partial htmx swap that never re-processes <head>, so a tag scoped to the Graph
// tab's own render wouldn't load for a viewer who arrived on Extraction and clicked over.
var reactFlowLoadPromise = null;
function loadReactFlow() {
  if (!reactFlowLoadPromise) {
    reactFlowLoadPromise = new Promise(function (resolve, reject) {
      var css = document.createElement("link");
      css.rel = "stylesheet";
      css.href = "/static/css/react-flow.css";
      document.head.appendChild(css);

      var script = document.createElement("script");
      script.src = "/static/js/graph-island.bundle.js";
      script.onload = resolve;
      script.onerror = function () {
        reactFlowLoadPromise = null; // let a later render retry instead of failing forever
        reject(new Error("failed to load graph-island.bundle.js"));
      };
      document.head.appendChild(script);
    });
  }
  return reactFlowLoadPromise;
}

function renderReactFlowIn(root) {
  if (!root || !root.querySelectorAll) return;
  var nodes = root.querySelectorAll(".react-flow-graph:not([data-processed])");
  if (!nodes.length) return;
  loadReactFlow()
    .then(function () {
      // window.PyroGraph.renderIn is set by graph-island.bundle.js (see main.jsx) — it does its
      // own :not([data-processed]) scan, so it's safe to just hand it the same root.
      window.PyroGraph.renderIn(root);
    })
    .catch(function (err) {
      console.error(err);
    });
}

renderReactFlowIn(document.body);

document.body.addEventListener("htmx:afterSwap", function () {
  // Not evt.detail.target: for an outerHTML swap that's the old, now-detached node.
  renderReactFlowIn(document.body);
});

// --- modal ----------------------------------------------------------------------------------
// Backdrop click closes it — native <dialog> only auto-closes on Escape.
document.addEventListener("click", function (evt) {
  var dialog = document.getElementById("preview-modal");
  if (dialog && evt.target === dialog) dialog.close();
});
