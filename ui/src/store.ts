import { create } from "zustand";

import {
  ApiError,
  ConflictError,
  abortRun as apiAbortRun,
  startRun as apiStartRun,
  fetchEtags,
  fetchFlowDetail,
  fetchFlows,
  fetchRunEvents,
  fetchWorkspace,
  listRuns,
  openRunSocket,
  putFlow,
  type Diagnostic,
  type FlowDetail,
  type FlowModel,
  type FlowModelNode,
  type FlowSummary,
  type RunListEntry,
  type WorkspaceInfo,
} from "./api";
import { defaultConfig } from "./forms";
import { freshNodeId } from "./graph";
import {
  applyRecord,
  emptyRunView,
  finalizeIncomplete,
  reduceRun,
  type RunRecord,
  type RunTrafficSelection,
  type RunView,
} from "./runview";

export type DetailError = {
  message: string;
  diagnostics: Diagnostic[];
};

export type SaveState = "clean" | "dirty" | "saving" | "conflict" | "error";

const AUTOSAVE_MS = 1000; // owner fork (2026-07-06): debounced autosave
const ETAG_POLL_MS = 2000; // FR-1004 v1 shape: poll, reload/prompt on drift

type AppState = {
  workspace: WorkspaceInfo | null;
  flows: FlowSummary[];
  error: string | null;
  selectedFlow: string | null;
  detail: FlowDetail | null;
  detailError: DetailError | null;
  selectedNode: string | null;
  // editing (S4/M4)
  saveState: SaveState;
  saveError: string | null;
  saveDiagnostics: Diagnostic[]; // validation errors from a rejected PUT
  graphVersion: number; // bump = Canvas rebuilds xyflow state from detail
  interacting: boolean; // a drag is live — hold off external reloads
  load: () => Promise<void>;
  openFlow: (identity: string, opts?: { push?: boolean }) => Promise<void>;
  selectNode: (id: string | null) => void;
  // canvas edit actions — every one mutates detail.flow then autosaves
  moveNode: (id: string, x: number, y: number) => void;
  connectEdge: (from: string, to: string) => void;
  deleteEdges: (edges: { from: string; to: string }[]) => void;
  deleteNode: (id: string) => void;
  addNode: (type: string, position?: [number, number]) => void;
  updateNodeConfig: (id: string, config: Record<string, unknown>) => void;
  setInteracting: (interacting: boolean) => void;
  resolveConflict: (how: "reload" | "overwrite") => Promise<void>;
  pollEtags: () => Promise<void>;
  // run on canvas (S4/M5, FR-1005) — runView !== null is RUN MODE:
  // the canvas locks editing and animates off the event stream
  runView: RunView | null;
  runId: string | null;
  runLive: boolean; // a WebSocket is attached (live run, not replay)
  runPanelTab: "events" | "history" | null; // null = panel closed
  runHistory: RunListEntry[] | null;
  runEnv: string | null; // selected env profile for the next run
  runNotice: string | null; // start/abort failures, shown by controls
  // M5.5: selected wire/port whose crossed messages the panel lists —
  // mutually exclusive with selectedNode (one filter at a time)
  runSelection: RunTrafficSelection | null;
  selectRunTraffic: (sel: RunTrafficSelection | null) => void;
  setRunEnv: (env: string | null) => void;
  startRun: (inputs: Record<string, unknown>) => Promise<void>;
  abortRun: () => Promise<void>;
  exitRun: () => void;
  openRunPanel: (tab: "events" | "history") => void;
  openHistoryRun: (runId: string) => Promise<void>;
};

function identityFromPath(pathname: string): string | null {
  const identity = pathname.replace(/^\/+|\/+$/g, "");
  return identity.length > 0 ? identity : null;
}

function splitRef(ref: string): [string, string] {
  const dot = ref.indexOf(".");
  return [ref.slice(0, dot), ref.slice(dot + 1)];
}

let saveTimer: ReturnType<typeof setTimeout> | null = null;

// ---- live run socket (module-level: one run watched at a time) ------
// Records are batched per animation-ish tick: fast flows emit hundreds
// of events/s and a set() per record would thrash React.
let runSocket: WebSocket | null = null;
let pendingRecords: RunRecord[] = [];
let flushTimer: ReturnType<typeof setTimeout> | null = null;
const FLUSH_MS = 16;

function closeRunSocket() {
  if (runSocket !== null) {
    runSocket.onmessage = null;
    runSocket.onclose = null;
    runSocket.onerror = null;
    runSocket.close();
    runSocket = null;
  }
  pendingRecords = [];
  if (flushTimer !== null) {
    clearTimeout(flushTimer);
    flushTimer = null;
  }
}

export const useAppStore = create<AppState>((set, get) => {
  /** Replace detail.flow (immutably at the top level — React re-renders
   * off object identity), mark dirty, kick the autosave timer. */
  function edit(
    mutate: (flow: FlowModel) => FlowModel,
    opts?: { rebuild?: boolean },
  ) {
    const { detail, runView } = get();
    if (detail === null) return;
    if (runView !== null) return; // run mode locks editing (owner fork)
    const flow = mutate(detail.flow);
    set({
      detail: { ...detail, flow },
      saveState: "dirty",
      saveError: null,
      saveDiagnostics: [],
      ...(opts?.rebuild ? { graphVersion: get().graphVersion + 1 } : {}),
    });
    if (saveTimer !== null) clearTimeout(saveTimer);
    saveTimer = setTimeout(() => void save(), AUTOSAVE_MS);
  }

  async function save(force = false) {
    const { detail, saveState } = get();
    if (detail === null) return;
    if (saveState === "saving") {
      // a save is in flight — re-arm; the fresh model goes next round
      saveTimer = setTimeout(() => void save(), AUTOSAVE_MS);
      return;
    }
    const identity = detail.identity;
    set({ saveState: "saving" });
    try {
      const saved = await putFlow(identity, detail.flow, detail.etag, force);
      const current = get().detail;
      if (current === null || current.identity !== identity) return;
      const dirtiedMeanwhile = current.flow !== detail.flow;
      set({
        detail: {
          ...current,
          etag: saved.etag,
          diagnostics: saved.diagnostics,
        },
        saveState: dirtiedMeanwhile ? "dirty" : "clean",
      });
      if (dirtiedMeanwhile) {
        if (saveTimer !== null) clearTimeout(saveTimer);
        saveTimer = setTimeout(() => void save(), AUTOSAVE_MS);
      } else {
        // quiet refetch: port surfaces (D11) and python fn lists are
        // server-derived — new nodes get their handles here
        await refreshDetail(identity);
      }
    } catch (e) {
      const current = get().detail;
      if (current === null || current.identity !== identity) return;
      if (e instanceof ConflictError) {
        set({ saveState: "conflict" });
      } else if (e instanceof ApiError) {
        set({
          saveState: "error",
          saveError: e.message,
          saveDiagnostics: e.diagnostics,
        });
      } else {
        set({
          saveState: "error",
          saveError: e instanceof Error ? e.message : String(e),
        });
      }
    }
  }

  /** Re-pull detail from the server without disturbing local edits:
   * applied only while clean and not mid-drag. */
  async function refreshDetail(identity: string) {
    try {
      const fresh = await fetchFlowDetail(identity);
      const s = get();
      if (
        s.selectedFlow === identity &&
        s.detail?.identity === identity &&
        s.saveState === "clean" &&
        !s.interacting
      ) {
        set({ detail: fresh, graphVersion: s.graphVersion + 1 });
      }
    } catch {
      // transient — the poll or next save will surface real trouble
    }
  }

  function flushRecords() {
    if (flushTimer !== null) {
      clearTimeout(flushTimer);
      flushTimer = null;
    }
    const view = get().runView;
    if (view === null || pendingRecords.length === 0) {
      pendingRecords = [];
      return;
    }
    for (const record of pendingRecords) applyRecord(view, record);
    pendingRecords = [];
    // per-node/edge entries were replaced immutably inside — the outer
    // clone re-renders subscribers of the view itself
    set({ runView: { ...view } });
  }

  function watchRun(runId: string) {
    closeRunSocket();
    let sawFinished = false;
    const socket = openRunSocket(runId);
    runSocket = socket;
    socket.onmessage = (msg) => {
      try {
        const record = JSON.parse(msg.data as string) as RunRecord;
        if (record.event === "run_finished") sawFinished = true;
        pendingRecords.push(record);
        if (flushTimer === null) {
          flushTimer = setTimeout(flushRecords, FLUSH_MS);
        }
      } catch {
        // garbled frame — the JSONL on disk stays the durable record
      }
    };
    socket.onclose = () => {
      if (runSocket !== socket) return; // superseded or intentional
      runSocket = null;
      flushRecords();
      const view = get().runView;
      if (view !== null && !sawFinished) {
        // the socket died mid-run (server gone) — settle the overlay
        finalizeIncomplete(view);
        set({ runView: { ...view }, runLive: false });
      } else {
        set({ runLive: false });
      }
    };
    socket.onerror = () => socket.close();
  }

  async function refreshHistory() {
    const identity = get().selectedFlow;
    if (identity === null) return;
    try {
      const runs = await listRuns(identity);
      if (get().selectedFlow === identity) set({ runHistory: runs });
    } catch {
      if (get().selectedFlow === identity) set({ runHistory: [] });
    }
  }

  return {
    workspace: null,
    flows: [],
    error: null,
    selectedFlow: null,
    detail: null,
    detailError: null,
    selectedNode: null,
    saveState: "clean",
    saveError: null,
    saveDiagnostics: [],
    graphVersion: 0,
    interacting: false,
    runView: null,
    runId: null,
    runLive: false,
    runPanelTab: null,
    runHistory: null,
    runEnv: null,
    runNotice: null,
    runSelection: null,

    load: async () => {
      try {
        const [workspace, flows] = await Promise.all([
          fetchWorkspace(),
          fetchFlows(),
        ]);
        set({ workspace, flows, error: null, runEnv: workspace.env_default });
        // deep link wins (SPA fallback serves index for /flows/...);
        // otherwise the manifest's `main:` flow opens by default (WM)
        const fromPath = identityFromPath(window.location.pathname);
        const initial =
          fromPath ??
          (flows.some((f) => f.identity === workspace.main)
            ? workspace.main
            : (flows[0]?.identity ?? null));
        if (initial !== null) {
          await get().openFlow(initial, { push: fromPath === null });
        }
      } catch (e) {
        set({ error: e instanceof Error ? e.message : String(e) });
      }
    },

    openFlow: async (identity, opts) => {
      if (saveTimer !== null) clearTimeout(saveTimer);
      closeRunSocket(); // a run view belongs to its flow
      set({
        selectedFlow: identity,
        selectedNode: null,
        saveState: "clean",
        saveError: null,
        saveDiagnostics: [],
        runView: null,
        runId: null,
        runLive: false,
        runPanelTab: null,
        runHistory: null,
        runNotice: null,
        runSelection: null,
      });
      if (opts?.push !== false && window.location.pathname !== `/${identity}`) {
        window.history.pushState(null, "", `/${identity}`);
      }
      try {
        const detail = await fetchFlowDetail(identity);
        // a slow response for a flow the user already navigated away
        // from must not clobber the current one
        if (get().selectedFlow === identity) {
          set({
            detail,
            detailError: null,
            graphVersion: get().graphVersion + 1,
          });
        }
      } catch (e) {
        if (get().selectedFlow !== identity) return;
        const detailError: DetailError =
          e instanceof ApiError
            ? { message: e.message, diagnostics: e.diagnostics }
            : {
                message: e instanceof Error ? e.message : String(e),
                diagnostics: [],
              };
        set({ detail: null, detailError });
      }
    },

    selectNode: (id) => set({ selectedNode: id, runSelection: null }),

    selectRunTraffic: (sel) => {
      if (get().runView === null) return; // a run-mode surface only
      set({ runSelection: sel, selectedNode: null });
    },

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

    setInteracting: (interacting) => set({ interacting }),

    resolveConflict: async (how) => {
      const { detail } = get();
      if (detail === null) return;
      if (how === "overwrite") {
        await save(true); // last-write-wins (FR-1004 ceiling)
      } else {
        set({ saveState: "clean" });
        await get().openFlow(detail.identity, { push: false });
      }
    },

    pollEtags: async () => {
      const s = get();
      if (s.detail === null || s.saveState !== "clean" || s.interacting) {
        return; // dirty edits conflict via the PUT's 409, not the poll
      }
      const identity = s.detail.identity;
      try {
        const etags = await fetchEtags(identity);
        const current = get().detail;
        if (
          current !== null &&
          current.identity === identity &&
          get().saveState === "clean" &&
          (etags.etag !== current.etag ||
            etags.code_etag !== current.code_etag)
        ) {
          // external edit while we're clean ⇒ live-reload (autosave
          // preference: frictionless beats prompts when nothing is lost)
          await refreshDetail(identity);
        }
      } catch {
        // flow may have been deleted externally — the next open will 404
      }
    },

    setRunEnv: (env) => set({ runEnv: env }),

    startRun: async (inputs) => {
      const { detail, runLive } = get();
      if (detail === null || runLive) return;
      // the run gate reads the FILE — flush any pending autosave first
      if (saveTimer !== null) {
        clearTimeout(saveTimer);
        saveTimer = null;
      }
      if (get().saveState === "dirty") await save();
      if (get().saveState !== "clean") {
        set({ runNotice: "flow not saved yet — resolve the save first" });
        return;
      }
      set({ runNotice: null });
      try {
        const started = await apiStartRun(
          detail.identity,
          get().runEnv,
          inputs,
        );
        set({
          runView: emptyRunView(),
          runId: started.run_id,
          runLive: true,
          runPanelTab: "events",
          selectedNode: null,
          runSelection: null,
        });
        watchRun(started.run_id);
      } catch (e) {
        // gate failures carry check diagnostics — surface the first
        const message =
          e instanceof ApiError && e.diagnostics.length > 0
            ? `${e.message} — ${e.diagnostics[0].code}: ${e.diagnostics[0].message}`
            : e instanceof Error
              ? e.message
              : String(e);
        set({ runNotice: message });
      }
    },

    abortRun: async () => {
      const { runId, runLive } = get();
      if (runId === null || !runLive) return;
      try {
        await apiAbortRun(runId);
        // state flips via the stream's run_finished (aborted)
      } catch {
        // finished in the meantime — the stream already said so
      }
    },

    exitRun: () => {
      closeRunSocket(); // the run keeps going server-side; Abort stops it
      set({
        runView: null,
        runId: null,
        runLive: false,
        runPanelTab: null,
        runNotice: null,
        runSelection: null,
      });
    },

    openRunPanel: (tab) => {
      set({ runPanelTab: tab });
      if (tab === "history") void refreshHistory();
    },

    openHistoryRun: async (runId) => {
      const { detail } = get();
      if (detail === null) return;
      closeRunSocket();
      try {
        const events = await fetchRunEvents(runId, detail.identity);
        set({
          runView: reduceRun(events as RunRecord[]),
          runId,
          runLive: false,
          runPanelTab: "events",
          runNotice: null,
          selectedNode: null,
          runSelection: null,
        });
      } catch (e) {
        set({ runNotice: e instanceof Error ? e.message : String(e) });
      }
    },
  };
});

export { AUTOSAVE_MS, ETAG_POLL_MS };

// browser back/forward re-selects the flow from the path
window.addEventListener("popstate", () => {
  const identity = identityFromPath(window.location.pathname);
  if (identity !== null) {
    void useAppStore.getState().openFlow(identity, { push: false });
  }
});
