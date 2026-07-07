// Pure mapping: flow-detail payload → xyflow graph. No React, no
// fetch — this is the canvas↔model logic Vitest covers.

import type { Edge, Node } from "@xyflow/react";

import type { Diagnostic, FlowDetail, FlowModel } from "./api";
import { portColor } from "./colors";

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
  [key: string]: unknown; // xyflow's Node data must be a Record
};

export type CanvasNode = Node<CanvasNodeData>;

const COLUMN_X = 240;
const ROW_Y = 130;
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
      },
    };
  });

  const outputType = new Map<string, string>();
  for (const node of nodes) {
    for (const port of node.data.outputs) {
      outputType.set(`${node.id}.${port.name}`, port.type);
    }
  }

  const edges: Edge[] = detail.flow.edges.map((edge) => {
    const [source, sourceHandle] = splitRef(edge.from);
    const [target, targetHandle] = splitRef(edge.to);
    return {
      // the model refs ARE the identity (edges match by (from,to) in
      // the merge) — deletion maps back through this id
      id: `${edge.from}→${edge.to}`,
      source,
      sourceHandle,
      target,
      targetHandle,
      data: { from: edge.from, to: edge.to },
      style: { stroke: portColor(outputType.get(edge.from)), strokeWidth: 1.5 },
    };
  });

  return { nodes, edges };
}
