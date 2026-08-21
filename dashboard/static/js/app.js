/*
 * Dashboard behavior: off-canvas sidebar, dark-mode toggle, react flow graph mounting, modal backdrop.
 *
 * This used to be ~120 lines of inline <script> in base.html, which meant it was re-sent on every
 * page load, never cached, and invisible to any linter or formatter. The one piece that has to
 * stay inline is the theme bootstrap in base.html's <head> — it must run before first paint to
 * avoid a flash of the wrong theme, which a deferred external file cannot do.
 *
 * Loaded with `defer`, so the DOM is parsed before any of this runs.
 */

// --- off-canvas sidebar (below md/768px) ----------------------------------------------------
// Toggled by the header hamburger and closed by tapping the backdrop or navigating (nav links do
// a full page load, so no explicit close-on-navigate handler is needed). Global because
// base.html's backdrop uses an inline onclick.
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
// The graph view's diagram comes back from the server as a `<div class="react-flow-graph"
// data-elements="...">` block (see api/render.py) — this scans for that class and mounts a
// pan/zoom/drag/expand-collapse React Flow graph (dashboard/static/src/graph/GraphIsland.jsx,
// bundled with React + React Flow + dagre into graph-island.bundle.js by `npm run build:js`).
// Colors are picked once at mount time based on the current theme (isDarkMode lives in
// static/src/graph/main.jsx, read there rather than passed in from here) rather than kept live —
// same limitation the old Cytoscape/Mermaid integrations had: a diagram already on screen doesn't
// repaint itself if dark mode is toggled after the fact, only a fresh render (page load / htmx
// swap) picks up the new theme.

// graph-island.bundle.js is ~380KB — loaded on demand, the first time a `.react-flow-graph` node
// actually shows up in the DOM, instead of unconditionally in every page's <head> (most pages
// never render a diagram, and the Data page's *default* tab is Extraction, not Graph). A
// <script> tag gated on the current page/tab doesn't work for this: the Data page's tab switch
// is a partial htmx swap (partials/data_shell.html's hx-select) that never re-processes a
// fetched response's <head>, so a tag that only appears in the Graph tab's own render would
// never load if the viewer arrived on the Extraction tab and clicked over — this has to be
// driven off DOM content, not off routing.
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
  // Not evt.detail.target: for an outerHTML swap that value is the old node htmx just replaced
  // (already removed from the document — detail.target.isConnected is false by the time this
  // listener runs), not the new one. document.body is always live, and the :not([data-processed])
  // filter already limits this to nodes that actually need drawing, so re-scanning the whole
  // page on every swap is cheap and correct regardless of which nested element the swap
  // actually touched.
  renderReactFlowIn(document.body);
});

// --- modal ----------------------------------------------------------------------------------
// Click on the <dialog> backdrop (not its content) closes it — native <dialog> only auto-closes
// on Escape by default.
document.addEventListener("click", function (evt) {
  var dialog = document.getElementById("preview-modal");
  if (dialog && evt.target === dialog) dialog.close();
});
