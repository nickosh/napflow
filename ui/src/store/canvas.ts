import type { FlowModel, FlowModelNode } from "../api";
import { defaultConfig } from "../forms";
import { freshNodeId } from "../graph";
import { canAddNodeType } from "../nodeSemantics";
import type { DocumentHistory } from "./history";
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
  documentHistory: DocumentHistory<FlowModel>,
): CanvasSlice {
  const deleteElements: CanvasSlice["deleteElements"] = (nodeIds, gone) => {
    const removedNodes = new Set(nodeIds);
    const removedEdges = new Set(gone.map((edge) => `${edge.from}\0${edge.to}`));
    const { selectedNode } = get();
    edit(
      (flow) => {
        const nodes = flow.nodes.filter((node) => !removedNodes.has(node.id));
        const edges = flow.edges.filter((edge) => {
          const [fromNode] = splitRef(edge.from);
          const [toNode] = splitRef(edge.to);
          return (
            !removedNodes.has(fromNode) &&
            !removedNodes.has(toNode) &&
            !removedEdges.has(`${edge.from}\0${edge.to}`)
          );
        });
        if (
          nodes.length === flow.nodes.length &&
          edges.length === flow.edges.length
        ) {
          return flow;
        }
        const layout = { ...(flow.layout ?? {}) };
        for (const id of removedNodes) delete layout[id];
        return { ...flow, nodes, edges, layout };
      },
      { rebuild: true },
    );
    if (selectedNode !== null && removedNodes.has(selectedNode)) {
      set({ selectedNode: null });
    }
  };

  const restore = (direction: "undo" | "redo") => {
    const state = get();
    if (state.detail === null || state.runView !== null) return;
    const flow =
      direction === "undo"
        ? documentHistory.undo(state.detail.flow)
        : documentHistory.redo(state.detail.flow);
    if (flow === null) return;
    edit(() => flow, { rebuild: true, recordHistory: false });
    if (
      state.selectedNode !== null &&
      !flow.nodes.some((node) => node.id === state.selectedNode)
    ) {
      set({ selectedNode: null });
    }
  };

  return {
    detail: null,
    selectedNode: null,
    graphVersion: 0,
    interacting: false,
    canUndo: false,
    canRedo: false,

    selectNode: (id) => set({ selectedNode: id, runSelection: null }),

    moveNode: (id, x, y) =>
      edit((flow) => {
        const current = flow.layout?.[id];
        if (current?.[0] === x && current[1] === y) return flow;
        return {
          ...flow,
          layout: { ...(flow.layout ?? {}), [id]: [x, y] },
        };
      }),

    moveNodes: (positions) =>
      edit((flow) => {
        const layout = { ...(flow.layout ?? {}) };
        let changed = false;
        for (const [id, position] of Object.entries(positions)) {
          const current = layout[id];
          if (
            current?.[0] === position[0] &&
            current[1] === position[1]
          ) {
            continue;
          }
          layout[id] = position;
          changed = true;
        }
        return changed ? { ...flow, layout } : flow;
      }),

    connectEdge: (from, to) =>
      edit(
        (flow) => {
          const into = flow.edges.filter((edge) => edge.to === to);
          if (into.length === 1 && into[0].from === from) return flow;
          return {
            ...flow,
            // E004 single edge per input, auto-replace on connect
            // (owner fork 2026-07-06): the new wire wins
            edges: [
              ...flow.edges.filter((edge) => edge.to !== to),
              { from, to },
            ],
          };
        },
        { rebuild: true },
      ),

    deleteElements,

    deleteNode: (id) => deleteElements([id], []),

    addNode: (type, position) => {
      edit(
        (flow) => {
          if (!canAddNodeType(flow, type)) return flow;
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

    updateNodeConfig: (id, config, historyGroup) =>
      edit(
        (flow) => ({
          ...flow,
          nodes: flow.nodes.map((n) => (n.id === id ? { ...n, config } : n)),
        }),
        {
          rebuild: true,
          historyGroup:
            historyGroup === undefined
              ? undefined
              : `config:${id}:${historyGroup}`,
        },
      ),

    updateNodeMaxSeconds: (id, maxSeconds) =>
      edit((flow) => ({
        ...flow,
        nodes: flow.nodes.map((node) => {
          if (node.id !== id) return node;
          if (node.max_seconds === maxSeconds) return node;
          const next = { ...node };
          if (maxSeconds === undefined) delete next.max_seconds;
          else next.max_seconds = maxSeconds;
          return next;
        }),
      })),

    undo: () => restore("undo"),
    redo: () => restore("redo"),
    endHistoryGroup: () => documentHistory.endGroup(),
    setInteracting: (interacting) => set({ interacting }),
  };
}
