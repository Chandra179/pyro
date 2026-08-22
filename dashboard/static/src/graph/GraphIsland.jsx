// Render layer for the entity graph: pill row + ReactFlow canvas. Expand/collapse logic (domain
// and composes-parent grouping) lives in useGraphLayout.js.
import ReactFlow, { Background, Controls, Handle, Position } from "reactflow";
import { useGraphLayout } from "./useGraphLayout.js";

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
      {data.componentCount ? (
        <span
          onClick={(e) => {
            e.stopPropagation();
            data.onToggleComponents();
          }}
          title={
            data.componentsExpanded
              ? "Collapse components"
              : `${data.componentCount} component${data.componentCount > 1 ? "s" : ""} (composes) — click to expand`
          }
          style={{
            marginLeft: 6,
            fontSize: 9,
            fontWeight: 600,
            padding: "1px 5px",
            borderRadius: 999,
            background: "rgba(255,255,255,0.25)",
            cursor: "pointer",
          }}
        >
          {data.componentsExpanded ? "−" : "+"}
          {data.componentCount}
        </span>
      ) : null}
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  );
}

// A collapsed domain's stand-in node; click to expand (same effect as the pill row).
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

export default function GraphIsland({ nodes: rawNodes, edges: rawEdges, dark }) {
  const { nodes, edges, onNodesChange, onEdgesChange, domains, collapsed, toggleDomain } =
    useGraphLayout(rawNodes, rawEdges, dark);

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
