// Graph-shaping logic for the entity graph: domain collapse/expand, `composes`-based component
// nesting, and feeding the result through dagre. Split out of GraphIsland.jsx to keep that a
// thin render layer.
import { useCallback, useEffect, useMemo, useState } from "react";
import { MarkerType, useEdgesState, useNodesState } from "reactflow";
import { layoutGraph } from "./layout.js";

export function groupNodeId(domain) {
  return "__group_" + domain;
}

export function useGraphLayout(rawNodes, rawEdges, dark) {
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

  // `composes` = meronymy/part-whole (extract/schema.py's RelationKind). Nest components under
  // their parent by default (e.g. "Lattice Plugin Host" folds into "Lattice"), same expand/
  // collapse interaction as domain groups, scoped to one parent instead.
  const componentsOf = useMemo(() => {
    const map = new Map();
    for (const e of rawEdges) {
      if (e.relation !== "composes") continue;
      if (!map.has(e.source)) map.set(e.source, []);
      map.get(e.source).push(e.target);
    }
    return map;
  }, [rawEdges]);
  const parentOfComponent = useMemo(() => {
    const map = new Map();
    for (const [parent, children] of componentsOf) {
      for (const child of children) map.set(child, parent);
    }
    return map;
  }, [componentsOf]);

  const [expandedParents, setExpandedParents] = useState(() => new Set());
  const toggleComponents = useCallback((parentId) => {
    setExpandedParents((prev) => {
      const next = new Set(prev);
      if (next.has(parentId)) next.delete(parentId);
      else next.add(parentId);
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
      const parent = parentOfComponent.get(n.id);
      if (parent && !expandedParents.has(parent) && !collapsed.has(domainOf.get(parent))) {
        continue; // folded into its parent's component badge below
      }
      const components = componentsOf.get(n.id);
      visibleNodes.push({
        id: n.id,
        type: "entity",
        data: {
          label: n.label,
          kind: n.kind,
          componentCount: components ? components.length : 0,
          componentsExpanded: expandedParents.has(n.id),
          onToggleComponents: () => toggleComponents(n.id),
        },
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
      if (collapsed.has(domain)) return groupNodeId(domain);
      const parent = parentOfComponent.get(id);
      if (parent && !expandedParents.has(parent) && !collapsed.has(domainOf.get(parent))) {
        return parent;
      }
      return id;
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
    // Re-layout resets manual drag, deliberately: collapse/expand changes what the diagram means.
  }, [
    rawNodes,
    rawEdges,
    collapsed,
    dark,
    componentsOf,
    parentOfComponent,
    expandedParents,
    toggleComponents,
  ]);

  return { nodes, edges, onNodesChange, onEdgesChange, domains, collapsed, toggleDomain };
}
