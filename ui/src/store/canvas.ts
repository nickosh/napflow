import type { FlowModelNode } from "../api";
import { defaultConfig } from "../forms";
import { freshNodeId } from "../graph";
import type {
  CanvasSlice,
  EditFlow,
  StoreGet,
  StoreSet,
} from "./types";

function splitRef(ref: string): [string, string] {
  const dot = ref.indexOf(".");
  return [ref.slice(0, dot), ref.slice(dot + 1)];
}

export function createCanvasSlice(
  set: StoreSet,
  get: StoreGet,
  edit: EditFlow,
): CanvasSlice {
  return {
    detail: null,
    selectedNode: null,
    graphVersion: 0,
    interacting: false,

    selectNode: (id) => set({ selectedNode: id, runSelection: null }),

    moveNode: (id, x, y) =>
      edit((flow) => ({
        ...flow,
        layout: { ...(flow.layout ?? {}), [id]: [x, y] },
      })),

    connectEdge: (from, to) =>
      edit(
        (flow) => ({
          ...flow,
          // E004 single edge per input, auto-replace on connect
          // (owner fork 2026-07-06): the new wire wins
          edges: [...flow.edges.filter((e) => e.to !== to), { from, to }],
        }),
        { rebuild: true },
      ),

    deleteEdges: (gone) =>
      edit(
        (flow) => ({
          ...flow,
          edges: flow.edges.filter(
            (e) => !gone.some((g) => g.from === e.from && g.to === e.to),
          ),
        }),
        { rebuild: true },
      ),

    deleteNode: (id) => {
      const { selectedNode } = get();
      edit(
        (flow) => {
          const layout = { ...(flow.layout ?? {}) };
          delete layout[id];
          return {
            ...flow,
            nodes: flow.nodes.filter((n) => n.id !== id),
            // edges die with their node — dangling refs are E003
            edges: flow.edges.filter(
              (e) => splitRef(e.from)[0] !== id && splitRef(e.to)[0] !== id,
            ),
            layout,
          };
        },
        { rebuild: true },
      );
      if (selectedNode === id) set({ selectedNode: null });
    },

    addNode: (type, position) => {
      edit(
        (flow) => {
          const id = freshNodeId(flow, type);
          const node: FlowModelNode = { id, type, config: defaultConfig(type) };
          let at = position;
          if (at === undefined) {
            const placed = Object.values(flow.layout ?? {});
            // click-to-add: drop below the current graph, not on top
            const y =
              placed.length > 0
                ? Math.max(...placed.map(([, py]) => py)) + 130
                : 40;
            at = [40, y];
          }
          return {
            ...flow,
            nodes: [...flow.nodes, node],
            layout: { ...(flow.layout ?? {}), [id]: at },
          };
        },
        { rebuild: true },
      );
    },

    updateNodeConfig: (id, config) =>
      edit(
        (flow) => ({
          ...flow,
          nodes: flow.nodes.map((n) => (n.id === id ? { ...n, config } : n)),
        }),
        { rebuild: true },
      ),

    updateNodeMaxSeconds: (id, maxSeconds) =>
      edit((flow) => ({
        ...flow,
        nodes: flow.nodes.map((node) => {
          if (node.id !== id) return node;
          const next = { ...node };
          if (maxSeconds === undefined) delete next.max_seconds;
          else next.max_seconds = maxSeconds;
          return next;
        }),
      })),

    setInteracting: (interacting) => set({ interacting }),
  };
}
