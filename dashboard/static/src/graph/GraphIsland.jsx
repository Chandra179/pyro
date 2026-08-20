// The interactive entity graph itself. Pan/zoom/drag are native React Flow behavior; the one
// feature it doesn't give us for free is expand/collapse, so that's implemented here: nodes are
// grouped by their `domain` tag (config/config.yaml's fixed taxonomy, already stamped on every
// entity by extraction — see api/graph_view.py), and any domain can be collapsed down to a single
// summary node. Edges crossing a collapsed domain's boundary are rewritten onto that summary node
// and deduped, rather than just hidden, so the graph stays legible instead of showing dangling
// half-edges.
import { useCallback, useEffect, useMemo, useState } from "react";
import ReactFlow, {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  useEdgesState,
  useNodesState,
} from "reactflow";
import { layoutGraph } from "./layout.js";

const KIND_COLOR = {
  datastore: "#0ea5e9",
  queue: "#f59e0b",
  external_system: "#a855f7",
};
const DEFAULT_COLOR = "#6366f1";
const KIND_RADIUS = { datastore: "20px", queue: "4px" };

function EntityNode({ data }) {
  return (
    <div
      style={{
        background: KIND_COLOR[data.kind] || DEFAULT_COLOR,
        color: "#fff",
        borderRadius: KIND_RADIUS[data.kind] || "6px",
        padding: "6px 10px",
        fontSize: 11,
        minWidth: 120,
        textAlign: "center",
        boxShadow: "0 1px 2px rgba(0,0,0,0.25)",
      }}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      {data.label}
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  );
}

// A collapsed domain's stand-in node. Clicking it expands that domain back out — the
// per-domain pill row above the canvas is the other way to toggle, this is just the more
// discoverable one once a domain is already collapsed.
function GroupNode({ data }) {
  return (
    <div
      onClick={data.onExpand}
      title="Click to expand"
      style={{
        border: "2px dashed " + (data.dark ? "#475569" : "#94a3b8"),
        borderRadius: 10,
        padding: "8px 14px",
        fontSize: 11,
        fontWeight: 600,
        color: data.dark ? "#cbd5e1" : "#475569",
        background: data.dark ? "rgba(100,116,139,0.15)" : "rgba(100,116,139,0.08)",
        cursor: "pointer",
        textAlign: "center",
        minWidth: 140,
      }}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      {data.label} ({data.count})
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  );
}

const NODE_TYPES = { entity: EntityNode, group: GroupNode };

function groupNodeId(domain) {
  return "__group_" + domain;
}

export default function GraphIsland({ nodes: rawNodes, edges: rawEdges, dark }) {
  const domains = useMemo(() => {
    const set = new Set(rawNodes.map((n) => n.domain || "Other"));
    return Array.from(set).sort();
  }, [rawNodes]);

  const [collapsed, setCollapsed] = useState(() => new Set());
  const toggleDomain = useCallback((domain) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(domain)) next.delete(domain);
      else next.add(domain);
      return next;
    });
  }, []);

  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);

  useEffect(() => {
    const domainOf = new Map(rawNodes.map((n) => [n.id, n.domain || "Other"]));
    const groupCount = new Map();

    const visibleNodes = [];
    for (const n of rawNodes) {
      const domain = domainOf.get(n.id);
      if (collapsed.has(domain)) {
        groupCount.set(domain, (groupCount.get(domain) || 0) + 1);
        continue;
      }
      visibleNodes.push({
        id: n.id,
        type: "entity",
        data: { label: n.label, kind: n.kind },
        position: { x: 0, y: 0 },
      });
    }
    for (const domain of collapsed) {
      visibleNodes.push({
        id: groupNodeId(domain),
        type: "group",
        data: {
          label: domain,
          count: groupCount.get(domain) || 0,
          dark,
          onExpand: () => toggleDomain(domain),
        },
        position: { x: 0, y: 0 },
      });
    }

    const endpointOf = (id) => {
      const domain = domainOf.get(id);
      return collapsed.has(domain) ? groupNodeId(domain) : id;
    };

    // Multiple raw edges can collapse onto the same (source, target) pair once both ends are
    // routed through their group node — merge those into one edge instead of drawing duplicates.
    const merged = new Map();
    for (const e of rawEdges) {
      const source = endpointOf(e.source);
      const target = endpointOf(e.target);
      if (source === target) continue; // now-internal to one (collapsed) domain — drop it
      const key = source + "->" + target;
      const existing = merged.get(key);
      if (existing) existing.count += 1;
      else merged.set(key, { source, target, label: e.label, count: 1 });
    }

    const visibleEdges = Array.from(merged.entries()).map(([key, v]) => ({
      id: key,
      source: v.source,
      target: v.target,
      label: v.count > 1 ? `${v.label} (+${v.count - 1} more)` : v.label,
      markerEnd: { type: MarkerType.ArrowClosed, color: dark ? "#64748b" : "#94a3b8" },
      style: { stroke: dark ? "#64748b" : "#94a3b8" },
      labelStyle: { fill: dark ? "#cbd5e1" : "#475569", fontSize: 9 },
      labelBgStyle: { fill: dark ? "#0f172a" : "#ffffff" },
    }));

    setNodes(layoutGraph(visibleNodes, visibleEdges));
    setEdges(visibleEdges);
    // Re-layout (which resets any manual drag) is intentional here, not just an artifact: a
    // collapse/expand genuinely changes what the diagram means, so re-running dagre against the
    // new node set is more correct than trying to preserve stale positions from a different graph.
  }, [rawNodes, rawEdges, collapsed, dark]);

  return (
    <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column" }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, padding: "0 0 8px", flexShrink: 0 }}>
        {domains.map((domain) => {
          const isCollapsed = collapsed.has(domain);
          return (
            <button
              key={domain}
              type="button"
              onClick={() => toggleDomain(domain)}
              style={{
                fontSize: 11,
                padding: "3px 8px",
                borderRadius: 999,
                border: "1px solid " + (dark ? "#334155" : "#e2e8f0"),
                background: isCollapsed ? "transparent" : dark ? "#1e293b" : "#f1f5f9",
                color: dark ? "#cbd5e1" : "#475569",
                cursor: "pointer",
              }}
            >
              {isCollapsed ? "+" : "−"} {domain}
            </button>
          );
        })}
      </div>
      <div style={{ width: "100%", flexGrow: 1, minHeight: 0 }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          nodeTypes={NODE_TYPES}
          fitView
          minZoom={0.1}
          maxZoom={3}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={16} color={dark ? "#1e293b" : "#e2e8f0"} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </div>
  );
}
