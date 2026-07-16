// Pure mapping: flow-detail payload → xyflow graph. No React, no
// fetch — this is the canvas↔model logic Vitest covers.

import type { Edge, Node } from "@xyflow/react";

import type { Diagnostic, FlowDetail, FlowModel } from "./api";

export type PortHandle = {
  name: string;
  type: string;
  required: boolean;
};

export type CanvasNodeData = {
  nodeId: string;
  nodeType: string;
  config: Record<string, unknown> | null;
  inputs: PortHandle[];
  outputs: PortHandle[];
  errors: number;
  warnings: number;
  // ghost-wire anchors (M6): rendered ONLY when a ghost edge needs
  // them — a stray invisible handle would double up xyflow's
  // .react-flow__handle-* classes on every node
  ghostSource: boolean;
  ghostTarget: boolean;
  [key: string]: unknown; // xyflow's Node data must be a Record
};

export type CanvasNode = Node<CanvasNodeData>;

/**
 * Rebuild the model-derived graph without throwing away dimensions xyflow has
 * already measured for nodes that still exist in the same flow.
 *
 * Controlled-node replacements that omit `measured` make xyflow hide the node
 * until its observer measures it again. Structural edits rebuild every node,
 * so retaining dimensions by stable node id keeps unchanged siblings visible
 * during that hand-off. Dimensions must never leak across flow identities, and
 * a genuinely new id must still go through xyflow's normal measurement path.
 */
export function reconcileGraphNodes(
  current: CanvasNode[],
  next: CanvasNode[],
  currentIdentity: string | null,
  nextIdentity: string,
): CanvasNode[] {
  if (currentIdentity !== nextIdentity) return next;

  const measuredById = new Map(
    current
      .filter((node) => node.measured !== undefined)
      .map((node) => [node.id, node.measured] as const),
  );
  return next.map((node) => {
    const measured = measuredById.get(node.id);
    return measured === undefined
      ? node
      : { ...node, measured: { ...measured } };
  });
}

// F1 card sizes: 240px cards (320 for wide types) need real column air
const COLUMN_X = 380;
const ROW_Y = 170;
const MARGIN = 40;

function splitRef(ref: string): [string, string] {
  const dot = ref.indexOf(".");
  return [ref.slice(0, dot), ref.slice(dot + 1)];
}

/** Column-per-depth fallback for nodes the layout: block doesn't
 * place — BFS from the nodes nothing points at. Cycles are legal
 * (guarded), so visited-tracking, not topological order. */
function fallbackPositions(
  detail: FlowDetail,
): Record<string, { x: number; y: number }> {
  const incoming = new Set(detail.flow.edges.map((e) => splitRef(e.to)[0]));
  const outgoing = new Map<string, string[]>();
  for (const edge of detail.flow.edges) {
    const [from] = splitRef(edge.from);
    const [to] = splitRef(edge.to);
    outgoing.set(from, [...(outgoing.get(from) ?? []), to]);
  }
  const depth = new Map<string, number>();
  const queue: string[] = detail.flow.nodes
    .filter((n) => !incoming.has(n.id))
    .map((n) => n.id);
  for (const id of queue) depth.set(id, 0);
  while (queue.length > 0) {
    const id = queue.shift()!;
    for (const next of outgoing.get(id) ?? []) {
      if (!depth.has(next)) {
        depth.set(next, (depth.get(id) ?? 0) + 1);
        queue.push(next);
      }
    }
  }
  const perColumn = new Map<number, number>();
  const positions: Record<string, { x: number; y: number }> = {};
  for (const node of detail.flow.nodes) {
    const column = depth.get(node.id) ?? 0;
    const row = perColumn.get(column) ?? 0;
    perColumn.set(column, row + 1);
    positions[node.id] = {
      x: MARGIN + column * COLUMN_X,
      y: MARGIN + row * ROW_Y,
    };
  }
  return positions;
}

function handlesFor(
  detail: FlowDetail,
  nodeId: string,
): { inputs: PortHandle[]; outputs: PortHandle[] } {
  const surface = detail.ports[nodeId] ?? null;
  const inputs = new Map<string, PortHandle>();
  const outputs = new Map<string, PortHandle>();
  if (surface !== null) {
    for (const [name, type] of Object.entries(surface.inputs)) {
      inputs.set(name, {
        name,
        type,
        required: surface.required_inputs.includes(name),
      });
    }
    for (const [name, type] of Object.entries(surface.outputs)) {
      outputs.set(name, { name, type, required: false });
    }
  }
  // wired ports the surface doesn't declare still need handles:
  // merge inputs grow by wiring (in1..inN, growable), and a broken
  // reference (surface null) must not orphan existing edges
  for (const edge of detail.flow.edges) {
    const [fromNode, fromPort] = splitRef(edge.from);
    const [toNode, toPort] = splitRef(edge.to);
    if (fromNode === nodeId && !outputs.has(fromPort)) {
      outputs.set(fromPort, { name: fromPort, type: "any", required: false });
    }
    if (toNode === nodeId && !inputs.has(toPort)) {
      inputs.set(toPort, { name: toPort, type: "any", required: false });
    }
  }
  return { inputs: [...inputs.values()], outputs: [...outputs.values()] };
}

/** W102 semantics for the live connect hint (D11 soft types): two
 * KNOWN types that differ look wrong — hint, never block. `any` on
 * either end is compatible with everything. */
export function typeMismatch(
  a: string | undefined,
  b: string | undefined,
): boolean {
  const ta = a ?? "any";
  const tb = b ?? "any";
  return ta !== "any" && tb !== "any" && ta !== tb;
}

// invisible node-level handles ghost-wires attach to (FlowNode renders
// them; template references name nodes, not ports)
export const GHOST_SOURCE_HANDLE = "__ghost_out";
export const GHOST_TARGET_HANDLE = "__ghost_in";

/** The flow a flow/loop node references, when it is statically known —
 * drill-in and clone targets. Templated references resolve at run time
 * only (same rule as E007's static-DAG scan), so they return null. */
export function drillTarget(data: {
  nodeType: string;
  config: Record<string, unknown> | null;
}): string | null {
  const key =
    data.nodeType === "flow"
      ? "flow"
      : data.nodeType === "loop"
        ? "body"
        : null;
  if (key === null) return null;
  const target = data.config?.[key];
  return typeof target === "string" && target !== "" && !target.includes("{{")
    ? target
    : null;
}

/** A unique node id for the palette: type, type2, type3, ... (E011). */
export function freshNodeId(flow: FlowModel, type: string): string {
  const taken = new Set(flow.nodes.map((n) => n.id));
  if (!taken.has(type)) return type;
  for (let i = 2; ; i++) {
    if (!taken.has(`${type}${i}`)) return `${type}${i}`;
  }
}

export function toGraph(detail: FlowDetail): {
  nodes: CanvasNode[];
  edges: Edge[];
} {
  const fallback = fallbackPositions(detail);
  const byNode = new Map<string, Diagnostic[]>();
  for (const diag of detail.diagnostics) {
    if (diag.node) {
      byNode.set(diag.node, [...(byNode.get(diag.node) ?? []), diag]);
    }
  }

  // ghost-wire pairs (FR-1007): cross-node template references, drawn
  // from the referenced node to the reader. The model can drift ahead
  // of template_refs between autosave refetches, so refs naming a
  // locally-deleted node are skipped.
  const nodeIds = new Set(detail.flow.nodes.map((n) => n.id));
  const ghostPairs: { source: string; target: string }[] = [];
  for (const [reader, refs] of Object.entries(detail.template_refs ?? {})) {
    if (!nodeIds.has(reader)) continue;
    for (const source of refs) {
      if (source === reader || !nodeIds.has(source)) continue;
      ghostPairs.push({ source, target: reader });
    }
  }

  const nodes: CanvasNode[] = detail.flow.nodes.map((node) => {
    const placed = detail.flow.layout?.[node.id];
    const diags = byNode.get(node.id) ?? [];
    const { inputs, outputs } = handlesFor(detail, node.id);
    return {
      id: node.id,
      type: "napflow",
      position: placed ? { x: placed[0], y: placed[1] } : fallback[node.id],
      data: {
        nodeId: node.id,
        nodeType: node.type,
        config: node.config ?? null,
        inputs,
        outputs,
        errors: diags.filter((d) => d.severity === "error").length,
        warnings: diags.filter((d) => d.severity === "warning").length,
        ghostSource: ghostPairs.some((g) => g.source === node.id),
        ghostTarget: ghostPairs.some((g) => g.target === node.id),
      },
    };
  });

  const edges: Edge[] = detail.flow.edges.map((edge) => {
    const [source, sourceHandle] = splitRef(edge.from);
    const [target, targetHandle] = splitRef(edge.to);
    return {
      // the model refs ARE the identity (edges match by (from,to) in
      // the merge) — deletion maps back through this id
      id: `${edge.from}→${edge.to}`,
      type: "napflow", // RunEdge: wire color + run animation live there
      source,
      sourceHandle,
      target,
      targetHandle,
      data: { from: edge.from, to: edge.to },
      style: { strokeWidth: 2 },
    };
  });

  // view-only ghost edges — no `data`, so the deletion mapping and
  // run-mode wire clicks never see them
  const ghosts: Edge[] = ghostPairs.map(({ source, target }) => ({
    id: `ghost:${source}→${target}`,
    source,
    sourceHandle: GHOST_SOURCE_HANDLE,
    target,
    targetHandle: GHOST_TARGET_HANDLE,
    selectable: false,
    deletable: false,
    focusable: false,
    className: "napf-ghost-edge",
    style: {
      stroke: "#9575cd",
      strokeWidth: 1.2,
      strokeDasharray: "6 4",
      opacity: 0.55,
    },
  }));

  return { nodes, edges: [...edges, ...ghosts] };
}
