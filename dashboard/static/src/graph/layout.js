// Auto-layout for the entity graph: React Flow (unlike Cytoscape's bundled "cose") has no layout
// engine of its own, so positions have to be computed before every render. dagre is a directed
// hierarchical layout — a reasonable fit for a system map where relationships mostly read as
// "A calls/writes to/reads from B".
import dagre from "@dagrejs/dagre";

const NODE_SIZE = { entity: [170, 44], group: [190, 54] };

export function layoutGraph(nodes, edges) {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", nodesep: 24, ranksep: 90, marginx: 20, marginy: 20 });
  g.setDefaultEdgeLabel(() => ({}));

  for (const node of nodes) {
    const [width, height] = NODE_SIZE[node.type] || NODE_SIZE.entity;
    g.setNode(node.id, { width, height });
  }
  // Self-loops and dangling references (an endpoint that got filtered out) would make dagre
  // throw — collapse-driven edge rewriting already dedupes same-node pairs upstream, but this
  // guard keeps layout robust even if that invariant ever slips.
  for (const edge of edges) {
    if (edge.source !== edge.target && g.hasNode(edge.source) && g.hasNode(edge.target)) {
      g.setEdge(edge.source, edge.target);
    }
  }

  dagre.layout(g);

  return nodes.map((node) => {
    const pos = g.node(node.id);
    const [width, height] = NODE_SIZE[node.type] || NODE_SIZE.entity;
    return {
      ...node,
      // dagre positions are node-center; React Flow positions are top-left.
      position: pos ? { x: pos.x - width / 2, y: pos.y - height / 2 } : { x: 0, y: 0 },
    };
  });
}
