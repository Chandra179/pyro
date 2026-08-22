// Auto-layout for the entity graph: React Flow has no layout engine of its own. dagre is a
// directed hierarchical layout, a reasonable fit for "A calls/writes to/reads from B" edges.
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
  // Self-loops / dangling endpoints would make dagre throw.
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
      // dagre is node-center; React Flow is top-left.
      position: pos ? { x: pos.x - width / 2, y: pos.y - height / 2 } : { x: 0, y: 0 },
    };
  });
}
