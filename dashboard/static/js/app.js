/*
 * Dashboard behavior: off-canvas sidebar, dark-mode toggle, mermaid rendering, modal backdrop.
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

// --- mermaid --------------------------------------------------------------------------------
// The graph view's diagram comes back from the server as a `<pre class="mermaid">` block (see
// api/render.py) — mermaid.js scans for that class and swaps in an inline SVG. startOnLoad is off
// because a diagram can arrive either in the initial server render or later via an htmx swap, so
// rendering is driven explicitly from both places below instead.
function currentMermaidTheme() {
  return document.documentElement.classList.contains("dark") ? "dark" : "default";
}

mermaid.initialize({ startOnLoad: false, theme: currentMermaidTheme() });

var mermaidRenderSeq = 0;

// mermaid.render()'s internal text-measurement step depends on layout having actually settled
// (getBBox/getComputedTextLength on its offscreen sandbox). Called right at DOMContentLoaded it
// works — the browser has already painted. Called synchronously from inside an htmx:afterSwap
// handler, before the browser has had a chance to paint the swap it just made, the returned
// promise was observed to hang indefinitely (mermaid.js has open upstream issues describing
// exactly this "works on load, hangs on dynamic insert" pattern). Yielding a frame first gives
// the browser that paint.
function nextFrame() {
  return new Promise(function (resolve) {
    requestAnimationFrame(function () {
      requestAnimationFrame(resolve);
    });
  });
}

async function renderMermaidNode(node) {
  var source = node.textContent;
  var id = "mermaid-graph-" + ++mermaidRenderSeq;
  try {
    await nextFrame();
    var result = await mermaid.render(id, source);
    // The node's container can be replaced wholesale (outerHTML) by an htmx swap (e.g. a
    // Delete-graph click) while we were awaiting layout — only attach if it's still around.
    if (document.body.contains(node)) {
      node.innerHTML = result.svg;
      node.setAttribute("data-processed", "true");
    }
  } catch (err) {
    console.error("Mermaid render failed:", err);
  }
}

async function renderMermaidIn(root) {
  if (!root || !root.querySelectorAll) return;
  var nodes = root.querySelectorAll(".mermaid:not([data-processed])");
  if (!nodes.length) return;
  // Re-initialize with the current theme each time rather than trusting the call above — that one
  // only ran once at page load, so a diagram rendered after a later dark-mode toggle would
  // otherwise use stale (mismatched) colors.
  mermaid.initialize({ startOnLoad: false, theme: currentMermaidTheme() });
  // Sequential, not Promise.all: mermaid.render() draws into a sandbox element it manages
  // internally (not our own DOM node), so two calls in flight at once corrupt each other's
  // sandbox state and throw deep in d3 (`Cannot read properties of null (reading
  // 'getAttribute')`).
  for (var node of nodes) {
    await renderMermaidNode(node);
  }
}

renderMermaidIn(document.body);

document.body.addEventListener("htmx:afterSwap", function () {
  // Not evt.detail.target: for an outerHTML swap that value is the old node htmx just replaced
  // (already removed from the document — detail.target.isConnected is false by the time this
  // listener runs), not the new one. document.body is always live, and the :not([data-processed])
  // filter in renderMermaidIn already limits this to nodes that actually need drawing, so
  // re-scanning the whole page on every swap is cheap and correct regardless of which nested
  // element the swap actually touched.
  renderMermaidIn(document.body);
});

// --- modal ----------------------------------------------------------------------------------
// Click on the <dialog> backdrop (not its content) closes it — native <dialog> only auto-closes
// on Escape by default.
document.addEventListener("click", function (evt) {
  var dialog = document.getElementById("preview-modal");
  if (dialog && evt.target === dialog) dialog.close();
});

// --- merge-run streaming --------------------------------------------------------------------
// Live merge output arrives as server-sent events (api/sse.py) appended into per-call <pre>
// elements. Keep each one pinned to the bottom as it grows, unless the reader has scrolled up to
// look at something — matching how a terminal behaves.
document.body.addEventListener("htmx:sseMessage", function (evt) {
  var target = evt.target;
  if (!target || !target.matches || !target.matches("[data-autoscroll]")) return;
  var slack = target.scrollHeight - target.scrollTop - target.clientHeight;
  if (slack < 40) target.scrollTop = target.scrollHeight;
});

// A finished run stops its own stream: the server sends `stream-close` as its last frame, and
// leaving the EventSource open past that holds a connection open per finished job card.
document.body.addEventListener("htmx:sseClose", function (evt) {
  var container = evt.target;
  if (container && container.removeAttribute) container.removeAttribute("sse-connect");
});
