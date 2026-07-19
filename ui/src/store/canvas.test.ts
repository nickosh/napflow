import { describe, expect, it } from "vitest";

import type { FlowDetail, FlowModel } from "../api";
import { createCanvasSlice } from "./canvas";
import { DocumentHistory, jsonValueEqual } from "./history";
import type {
  AppState,
  EditFlow,
  StoreGet,
  StoreSet,
} from "./types";

function model(): FlowModel {
  return {
    flow: { name: "history" },
    nodes: [
      { id: "a", type: "log", config: { label: "" } },
      { id: "b", type: "log", config: { label: "b" } },
      { id: "end", type: "end", config: { ports: [{ name: "done" }] } },
    ],
    edges: [
      { from: "a.out", to: "b.in" },
      { from: "b.out", to: "end.done" },
    ],
    layout: { a: [0, 0], b: [100, 0], end: [200, 0] },
  };
}

function detail(flow: FlowModel): FlowDetail {
  return {
    identity: "flows/history",
    flow,
    diagnostics: [],
    etag: "etag-1",
    code_etag: "code-1",
    functions: null,
    ports: {},
    template_refs: {},
    used_by: [],
  };
}

function harness() {
  const history = new DocumentHistory<FlowModel>({ equal: jsonValueEqual });
  let state: AppState;
  const set = ((patch: unknown) => {
    const resolved =
      typeof patch === "function"
        ? (patch as (current: AppState) => Partial<AppState>)(state)
        : patch;
    state = { ...state, ...(resolved as Partial<AppState>) };
  }) as StoreSet;
  const get = (() => state) as StoreGet;
  const edit: EditFlow = (mutate, opts) => {
    if (state.detail === null || state.runView !== null) return;
    const current = state.detail;
    const flow = mutate(current.flow);
    if (history.same(current.flow, flow)) return;
    if (opts?.recordHistory !== false) {
      history.record(current.flow, flow, opts?.historyGroup);
    }
    set({
      detail: { ...current, flow },
      ...history.status,
      ...(opts?.rebuild
        ? { graphVersion: state.graphVersion + 1 }
        : {}),
    });
  };
  const canvas = createCanvasSlice(set, get, edit, history);
  state = {
    ...canvas,
    detail: detail(model()),
    runView: null,
    runSelection: null,
  } as AppState;
  return {
    get: () => state,
    set: (patch: Partial<AppState>) => set(patch),
  };
}

describe("canvas document history", () => {
  it("treats a mixed multi-delete as one step and restores the graph", () => {
    const store = harness();
    store.set({ selectedNode: "a" });

    store.get().deleteElements(["a", "b"], [
      { from: "b.out", to: "end.done" },
    ]);
    expect(store.get().detail?.flow.nodes.map((node) => node.id)).toEqual([
      "end",
    ]);
    expect(store.get().detail?.flow.edges).toEqual([]);
    expect(store.get().selectedNode).toBeNull();

    store.get().undo();
    expect(store.get().detail?.flow.nodes.map((node) => node.id)).toEqual([
      "a",
      "b",
      "end",
    ]);
    expect(store.get().detail?.flow.edges).toHaveLength(2);
    expect(store.get().canUndo).toBe(false);
    expect(store.get().canRedo).toBe(true);
  });

  it("batches tidy positions and ignores a no-op move", () => {
    const store = harness();

    store.get().moveNodes({ a: [10, 20], b: [30, 40] });
    expect(store.get().detail?.flow.layout).toMatchObject({
      a: [10, 20],
      b: [30, 40],
    });
    store.get().undo();
    expect(store.get().detail?.flow.layout).toMatchObject({
      a: [0, 0],
      b: [100, 0],
    });

    store.get().moveNode("a", 0, 0);
    expect(store.get().canUndo).toBe(false);
  });

  it("coalesces one config focus session and preserves session metadata", () => {
    const store = harness();
    const config = (label: string) => ({ label });

    store.get().updateNodeConfig("a", config("h"), "label:typing");
    store.get().updateNodeConfig("a", config("hello"), "label:typing");
    store.set({
      detail: { ...store.get().detail!, etag: "etag-after-save" },
    });
    store.get().undo();

    expect(store.get().detail?.flow.nodes[0].config).toEqual({ label: "" });
    expect(store.get().detail?.etag).toBe("etag-after-save");
    expect(store.get().canUndo).toBe(false);

    store.get().redo();
    store.get().endHistoryGroup();
    store.get().updateNodeConfig("a", config("hello!"), "label:typing");
    store.get().undo();
    expect(store.get().detail?.flow.nodes[0].config).toEqual({
      label: "hello",
    });
  });

  it("rebuilds on restore, clears dangling selection, and locks run mode", () => {
    const store = harness();
    store.get().deleteNode("a");
    const afterDeleteVersion = store.get().graphVersion;
    store.get().undo();
    expect(store.get().graphVersion).toBe(afterDeleteVersion + 1);

    store.set({ selectedNode: "a" });
    store.get().redo();
    expect(store.get().selectedNode).toBeNull();

    const deleted = store.get().detail?.flow;
    store.set({ runView: {} as AppState["runView"] });
    store.get().undo();
    expect(store.get().detail?.flow).toBe(deleted);
  });
});
