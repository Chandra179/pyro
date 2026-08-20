// Entry point bundled (with React, React Flow, and dagre) into static/js/graph-island.bundle.js
// by `npm run build:js` — see dashboard/package.json. Exposes window.PyroGraph.renderIn so
// static/js/app.js's lazy-load-on-first-appearance logic (unchanged from the old Cytoscape
// integration) can drive it without needing to know anything about React underneath.
import { createElement } from "react";
import { createRoot } from "react-dom/client";
import GraphIsland from "./GraphIsland.jsx";

function isDarkMode() {
  return document.documentElement.classList.contains("dark");
}

function mountNode(node) {
  let elements;
  try {
    elements = JSON.parse(node.getAttribute("data-elements") || "{}");
  } catch (err) {
    console.error("React Flow elements JSON parse failed:", err);
    return;
  }
  createRoot(node).render(
    createElement(GraphIsland, {
      nodes: elements.nodes || [],
      edges: elements.edges || [],
      dark: isDarkMode(),
    }),
  );
  node.setAttribute("data-processed", "true");
}

function renderIn(root) {
  if (!root || !root.querySelectorAll) return;
  for (const node of root.querySelectorAll(".react-flow-graph:not([data-processed])")) {
    mountNode(node);
  }
}

window.PyroGraph = { renderIn };
