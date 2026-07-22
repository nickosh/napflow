import type { FlowModel, FlowModelNode } from "./api";

export const BOUNDARY_NODE_TYPES = ["start", "end"] as const;

export type BoundaryNodeType = (typeof BOUNDARY_NODE_TYPES)[number];

export function isBoundaryNodeType(type: string): type is BoundaryNodeType {
  return BOUNDARY_NODE_TYPES.some((boundary) => boundary === type);
}

/** Boundary types the picker may offer for this exact document revision. */
export function missingBoundaryTypes(
  flow: Pick<FlowModel, "nodes">,
): BoundaryNodeType[] {
  const present = new Set(flow.nodes.map((node) => node.type));
  return BOUNDARY_NODE_TYPES.filter((type) => !present.has(type));
}

/** Store-level defense for stale picker gestures and direct action calls. */
export function canAddNodeType(
  flow: Pick<FlowModel, "nodes">,
  type: string,
): boolean {
  return !isBoundaryNodeType(type) || !flow.nodes.some((node) => node.type === type);
}

/** Authoring-time rule-6 cue; deliberately independent of live run state. */
export function isFrameStartSource(
  flow: Pick<FlowModel, "edges">,
  node: Pick<FlowModelNode, "id" | "type">,
): boolean {
  if (node.type === "start") return true;
  return (
    node.type === "fixture" &&
    !flow.edges.some((edge) => edge.to === `${node.id}.trigger`)
  );
}
