import {
  ApiError,
  abortRun as apiAbortRun,
  startRun as apiStartRun,
  fetchFlowDetail,
  fetchRunEventPage,
  fetchRunFramePage,
  RUN_REPLAY_PAGE_LIMIT,
  listRuns,
  openRunSocket,
  type Diagnostic,
  type RunFramePage,
} from "../api";
import { persistenceRegistry } from "../persistence";
import {
  applyRecord,
  emptyRunView,
  finalizeIncomplete,
  finalizeFrameReplay,
  foldRunEventPage,
  ROOT_FRAME,
  RUN_FRAME_WINDOW,
  settleRootReplay,
  type RunRecord,
  type RunFrameSummary,
} from "../runview";
import { joinOperatorNotices } from "./notices";
import type {
  RunReplaySlice,
  RunResetPatch,
  StoreGet,
  StoreSet,
} from "./types";

function startedRunNotice(
  warnings: Diagnostic[],
  notes: string[],
): string | null {
  return joinOperatorNotices([
    ...warnings.map(
      (warning) =>
        `${warning.severity}: ${warning.code}: ${warning.message}`,
    ),
    ...notes,
  ]);
}

// ---- live run socket (module-level: one run watched at a time) ------
// Records are batched per animation-ish tick: fast flows emit hundreds
// of events/s and a set() per record would thrash React.
let runSocket: WebSocket | null = null;
let pendingRecords: RunRecord[] = [];
let flushTimer: ReturnType<typeof setTimeout> | null = null;
const FLUSH_MS = 16;
const LIVE_BATCH_LIMIT = 256;
const WS_RESYNC_REQUIRED = 4410;
const WS_SUBSCRIBER_LIMIT = 4411;
const MAX_LIVE_RESYNCS = 3;

class StaleReplay extends Error {}

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

export function createRunReplaySlice(
  set: StoreSet,
  get: StoreGet,
): {
  slice: RunReplaySlice;
  resetForFlow: () => RunResetPatch;
} {
  let replayGeneration = 0;
  let eventPageGeneration = 0;

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

  function watchRun(runId: string, resyncs = 0) {
    closeRunSocket();
    let sawFinished = false;
    const socket = openRunSocket(runId);
    runSocket = socket;
    socket.onmessage = (msg) => {
      try {
        const record = JSON.parse(msg.data as string) as RunRecord;
        if (record.event === "run_finished") sawFinished = true;
        pendingRecords.push(record);
        if (pendingRecords.length >= LIVE_BATCH_LIMIT) {
          flushRecords();
        } else if (flushTimer === null) {
          flushTimer = setTimeout(flushRecords, FLUSH_MS);
        }
      } catch {
        // garbled frame — the JSONL on disk stays the durable record
      }
    };
    socket.onclose = (event) => {
      if (runSocket !== socket) return; // superseded or intentional
      runSocket = null;
      if (event.code === WS_SUBSCRIBER_LIMIT && get().runId === runId) {
        flushRecords();
        set({
          runLive: false,
          runNotice: "too many live viewers — retry after another viewer closes",
        });
        return;
      }
      if (
        event.code === WS_RESYNC_REQUIRED &&
        !sawFinished &&
        get().runId === runId
      ) {
        if (resyncs >= MAX_LIVE_RESYNCS) {
          flushRecords();
          set({
            runLive: false,
            runNotice: "live view fell behind — reopen the run from history",
          });
          return;
        }
        if (flushTimer !== null) {
          clearTimeout(flushTimer);
          flushTimer = null;
        }
        pendingRecords = [];
        // The new socket replays a fresh durable prefix. Reset the reducer so
        // replayed records never double-apply counters from the old attempt.
        set({ runView: emptyRunView(), runLive: true, runSource: "live" });
        watchRun(runId, resyncs + 1);
        return;
      }
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

  function requireCurrentReplay(
    generation: number,
    runId: string,
    identity: string,
  ): void {
    if (!isCurrentReplay(generation, runId, identity)) {
      throw new StaleReplay();
    }
  }

  function isCurrentReplay(
    generation: number,
    runId: string,
    identity: string,
  ): boolean {
    const state = get();
    return !(
      generation !== replayGeneration ||
      state.runId !== runId ||
      state.selectedFlow !== identity ||
      state.runSource !== "history"
    );
  }

  function requireFramePage(
    page: RunFramePage,
    parentFrame: string,
    afterSeq: number,
    rootFrame: string,
  ): void {
    if (page.parent_frame !== parentFrame || page.after_seq !== afterSeq) {
      throw new Error("frame replay page cursor/parent mismatch");
    }
    if (page.root_frame !== rootFrame) {
      throw new Error("replay root frame changed between pages");
    }
    if (page.frames.length > RUN_FRAME_WINDOW) {
      throw new Error(
        `frame page exceeds the ${RUN_FRAME_WINDOW}-summary browser window`,
      );
    }
  }

  async function loadFrameChildrenPage(
    runId: string,
    identity: string,
    parentFrame: string,
    afterSeq: number,
    generation: number,
  ): Promise<void> {
    set({ runFrameLoading: true, runFrameError: null });
    try {
      const page = await fetchRunFramePage(runId, identity, {
        parentFrame,
        afterSeq,
      });
      requireCurrentReplay(generation, runId, identity);
      requireFramePage(page, parentFrame, afterSeq, get().runRootFrame);
      set({
        runFrameChildren: page.frames,
        runFrameChildrenAfterSeq: afterSeq,
        runFrameChildrenNextAfterSeq: page.next_after_seq,
        runFrameChildrenHasMore: page.has_more,
        runFrameLoading: false,
        runFrameError: null,
      });
    } catch (error) {
      if (
        error instanceof StaleReplay ||
        !isCurrentReplay(generation, runId, identity)
      ) {
        return;
      }
      set({
        runFrameChildren: [],
        runFrameChildrenHasMore: false,
        runFrameLoading: false,
        runFrameError: error instanceof Error ? error.message : String(error),
      });
    }
  }

  async function loadFramePath(
    path: RunFrameSummary[],
    generation: number,
  ): Promise<void> {
    const state = get();
    const runId = state.runId;
    const identity = state.selectedFlow;
    const summary = path.at(-1);
    const rootFrame = state.runRootFrame;
    if (
      runId === null ||
      identity === null ||
      summary === undefined ||
      state.runSource !== "history"
    ) {
      return;
    }
    set({
      runFramePath: path,
      runFrameDetail: null,
      runFrameView: emptyRunView(summary.frame),
      runFrameChildren: [],
      runFrameChildrenAfterSeq: 0,
      runFrameChildrenNextAfterSeq: 0,
      runFrameChildrenHasMore: false,
      runFrameLoading: true,
      runFrameError: null,
      runEventPage: null,
      runEventPageLoading: false,
      selectedNode: null,
      runSelection: null,
    });
    try {
      const [eventPage, childPage, frameDetailResult] = await Promise.all([
        fetchRunEventPage(runId, identity, {
          afterSeq: 0,
          frame: summary.frame,
        }),
        fetchRunFramePage(runId, identity, {
          parentFrame: summary.frame,
          afterSeq: 0,
        }),
        fetchFlowDetail(summary.flow)
          .then((frameDetail) => ({ frameDetail, error: null }))
          .catch((error: unknown) => ({
            frameDetail: null,
            error: `current flow source is unavailable; durable frame events remain inspectable: ${
              error instanceof Error ? error.message : String(error)
            }`,
          })),
      ]);
      requireCurrentReplay(generation, runId, identity);
      if (eventPage.frame !== summary.frame) {
        throw new Error("event replay frame changed between request and response");
      }
      if (eventPage.events.length > RUN_REPLAY_PAGE_LIMIT) {
        throw new Error(
          `event page exceeds the ${RUN_REPLAY_PAGE_LIMIT}-record browser window`,
        );
      }
      requireFramePage(childPage, summary.frame, 0, rootFrame);
      const view = emptyRunView(summary.frame);
      foldRunEventPage(view, eventPage, 0, rootFrame);
      finalizeFrameReplay(view, summary);
      set({
        runFrameDetail: frameDetailResult.frameDetail,
        runFrameView: { ...view },
        runFrameChildren: childPage.frames,
        runFrameChildrenAfterSeq: 0,
        runFrameChildrenNextAfterSeq: childPage.next_after_seq,
        runFrameChildrenHasMore: childPage.has_more,
        runFrameLoading: false,
        runFrameError: frameDetailResult.error,
        runEventPage: eventPage.events,
        runEventPageAfterSeq: 0,
        runEventPageNextAfterSeq: eventPage.next_after_seq,
        runEventPageHasMore: eventPage.has_more,
        runEventPageLoading: false,
      });
    } catch (error) {
      if (
        error instanceof StaleReplay ||
        !isCurrentReplay(generation, runId, identity)
      ) {
        return;
      }
      set({
        runFrameLoading: false,
        runFrameError: error instanceof Error ? error.message : String(error),
      });
    }
  }

  function resetForFlow(): RunResetPatch {
    replayGeneration += 1;
    eventPageGeneration += 1;
    closeRunSocket();
    return {
      runView: null,
      runId: null,
      runLive: false,
      runSource: null,
      runPanelTab: null,
      runHistory: null,
      runNotice: null,
      runReplayLoading: false,
      runReplayError: null,
      runRootFrame: ROOT_FRAME,
      runFramePath: [],
      runFrameDetail: null,
      runFrameView: null,
      runFrameChildren: [],
      runFrameChildrenAfterSeq: 0,
      runFrameChildrenNextAfterSeq: 0,
      runFrameChildrenHasMore: false,
      runFrameLoading: false,
      runFrameError: null,
      runEventPage: null,
      runEventPageAfterSeq: 0,
      runEventPageNextAfterSeq: 0,
      runEventPageHasMore: false,
      runEventPageLoading: false,
      runSelection: null,
    };
  }

  const slice: RunReplaySlice = {
    runView: null,
    runId: null,
    runLive: false,
    runSource: null,
    runPanelTab: null,
    runHistory: null,
    runEnv: null,
    runNotice: null,
    runReplayLoading: false,
    runReplayError: null,
    runRootFrame: ROOT_FRAME,
    runFramePath: [],
    runFrameDetail: null,
    runFrameView: null,
    runFrameChildren: [],
    runFrameChildrenAfterSeq: 0,
    runFrameChildrenNextAfterSeq: 0,
    runFrameChildrenHasMore: false,
    runFrameLoading: false,
    runFrameError: null,
    runEventPage: null,
    runEventPageAfterSeq: 0,
    runEventPageNextAfterSeq: 0,
    runEventPageHasMore: false,
    runEventPageLoading: false,
    runSelection: null,

    selectRunTraffic: (sel) => {
      if (get().runView === null) return; // a run-mode surface only
      set({ runSelection: sel, selectedNode: null });
    },

    setRunEnv: (env) => set({ runEnv: env }),

    startRun: async (inputs) => {
      const { detail, runLive } = get();
      if (detail === null || runLive) return;
      // The run gate reads flow.yaml and nodes.py from disk. Flush every
      // mounted editor and block on conflict/error.
      if (!(await persistenceRegistry.flushAll())) {
        set({ runNotice: "flow not saved yet — resolve the save first" });
        return;
      }
      const current = get().detail;
      if (current === null || current.identity !== detail.identity) return;
      set({ runNotice: null });
      try {
        const started = await apiStartRun(
          current.identity,
          get().runEnv,
          inputs,
        );
        replayGeneration += 1;
        eventPageGeneration += 1;
        set({
          runView: emptyRunView(),
          runId: started.run_id,
          runLive: true,
          runNotice: startedRunNotice(started.warnings, started.notes),
          runSource: "live",
          runPanelTab: "events",
          runReplayLoading: false,
          runReplayError: null,
          runRootFrame: ROOT_FRAME,
          runFramePath: [],
          runFrameDetail: null,
          runFrameView: null,
          runFrameChildren: [],
          runFrameChildrenAfterSeq: 0,
          runFrameChildrenNextAfterSeq: 0,
          runFrameChildrenHasMore: false,
          runFrameLoading: false,
          runFrameError: null,
          runEventPage: null,
          runEventPageLoading: false,
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
      set({ runNotice: null });
      try {
        await apiAbortRun(runId);
        // state flips via the stream's run_finished (aborted)
      } catch (error) {
        // A finished-in-the-meantime run is a successful 200 from the
        // idempotent server endpoint. Real HTTP/network failures stay visible.
        set({
          runNotice:
            error instanceof Error ? error.message : String(error),
        });
      }
    },

    exitRun: () => {
      replayGeneration += 1;
      eventPageGeneration += 1;
      closeRunSocket(); // the run keeps going server-side; Abort stops it
      set({
        runView: null,
        runId: null,
        runLive: false,
        runSource: null,
        runPanelTab: null,
        runNotice: null,
        runReplayLoading: false,
        runReplayError: null,
        runFramePath: [],
        runFrameDetail: null,
        runFrameView: null,
        runFrameChildren: [],
        runFrameChildrenHasMore: false,
        runFrameLoading: false,
        runFrameError: null,
        runEventPage: null,
        runEventPageLoading: false,
        selectedNode: null,
        runSelection: null,
      });
    },

    openRunPanel: (tab) => {
      set({ runPanelTab: tab });
      if (tab === "history") void refreshHistory();
    },

    closeRunPanel: () => {
      // collapses the console only — an active run overlay stays (the
      // Edit pill / exit-run leaves run mode)
      set({ runPanelTab: null });
    },

    openHistoryRun: async (runId) => {
      const { detail } = get();
      if (detail === null) return;
      const generation = ++replayGeneration;
      eventPageGeneration += 1;
      closeRunSocket();
      set({
        runView: emptyRunView(ROOT_FRAME),
        runId,
        runLive: false,
        runSource: "history",
        runPanelTab: "events",
        runNotice: null,
        runReplayLoading: true,
        runReplayError: null,
        runRootFrame: ROOT_FRAME,
        runFramePath: [],
        runFrameDetail: null,
        runFrameView: null,
        runFrameChildren: [],
        runFrameChildrenAfterSeq: 0,
        runFrameChildrenNextAfterSeq: 0,
        runFrameChildrenHasMore: false,
        runFrameLoading: false,
        runFrameError: null,
        runEventPage: null,
        runEventPageAfterSeq: 0,
        runEventPageNextAfterSeq: 0,
        runEventPageHasMore: false,
        runEventPageLoading: false,
        selectedNode: null,
        runSelection: null,
      });

      // A history row for a manager-owned live run belongs on the existing
      // bounded WebSocket catch-up path, not a settled REST snapshot.
      const listedState = get().runHistory?.find(
        (entry) => entry.run_id === runId,
      )?.state;
      if (listedState === "running") {
        set({
          runView: emptyRunView(ROOT_FRAME),
          runLive: true,
          runSource: "live",
          runReplayLoading: false,
        });
        watchRun(runId);
        return;
      }

      try {
        // Capture root events first. A later frame-page snapshot may contain
        // more completed children, but can never omit a child already covered
        // by a `complete` event projection.
        const eventPage = await fetchRunEventPage(runId, detail.identity, {
          afterSeq: 0,
          frame: ROOT_FRAME,
        });
        requireCurrentReplay(generation, runId, detail.identity);
        if (eventPage.frame !== eventPage.root_frame) {
          throw new Error("root replay page did not select the root frame");
        }
        if (eventPage.events.length > RUN_REPLAY_PAGE_LIMIT) {
          throw new Error(
            `event page exceeds the ${RUN_REPLAY_PAGE_LIMIT}-record browser window`,
          );
        }
        if (eventPage.history_state === "running") {
          set({
            runView: emptyRunView(eventPage.root_frame),
            runLive: true,
            runSource: "live",
            runReplayLoading: false,
            runRootFrame: eventPage.root_frame,
          });
          watchRun(runId);
          return;
        }
        const framePage = await fetchRunFramePage(runId, detail.identity, {
          parentFrame: eventPage.root_frame,
          afterSeq: 0,
        });
        requireCurrentReplay(generation, runId, detail.identity);
        requireFramePage(framePage, eventPage.root_frame, 0, eventPage.root_frame);
        const view = emptyRunView(eventPage.root_frame);
        foldRunEventPage(view, eventPage, 0, eventPage.root_frame);
        settleRootReplay(view, eventPage);
        set({
          runView: { ...view },
          runLive: false,
          runSource: "history",
          runReplayLoading: false,
          runReplayError:
            eventPage.history_state === "indeterminate"
              ? "run activity is indeterminate; showing the durable prefix without settling it"
              : null,
          runRootFrame: eventPage.root_frame,
          runFrameChildren: framePage.frames,
          runFrameChildrenAfterSeq: 0,
          runFrameChildrenNextAfterSeq: framePage.next_after_seq,
          runFrameChildrenHasMore: framePage.has_more,
          runFrameLoading: false,
          runFrameError: null,
          runEventPage: eventPage.events,
          runEventPageAfterSeq: 0,
          runEventPageNextAfterSeq: eventPage.next_after_seq,
          runEventPageHasMore: eventPage.has_more,
          runEventPageLoading: false,
        });
      } catch (e) {
        if (e instanceof StaleReplay) return;
        try {
          requireCurrentReplay(generation, runId, detail.identity);
        } catch (stale) {
          if (stale instanceof StaleReplay) return;
          throw stale;
        }
        set({
          runReplayLoading: false,
          runReplayError: e instanceof Error ? e.message : String(e),
        });
      }
    },

    openRunFrame: async (summary) => {
      const state = get();
      if (state.runSource !== "history") return;
      const parent = state.runFramePath.at(-1)?.frame ?? state.runRootFrame;
      if (summary.parent_frame !== parent) return;
      const generation = ++replayGeneration;
      eventPageGeneration += 1;
      await loadFramePath([...state.runFramePath, summary], generation);
    },

    backRunFrame: async () => {
      const state = get();
      if (state.runSource !== "history" || state.runFramePath.length === 0) {
        return;
      }
      const nextPath = state.runFramePath.slice(0, -1);
      const generation = ++replayGeneration;
      eventPageGeneration += 1;
      if (nextPath.length === 0) {
        set({
          runFramePath: [],
          runFrameDetail: null,
          runFrameView: null,
          runFrameChildren: [],
          runFrameChildrenAfterSeq: 0,
          runFrameChildrenNextAfterSeq: 0,
          runFrameChildrenHasMore: false,
          runFrameError: null,
          runEventPage: null,
          runEventPageLoading: false,
          selectedNode: null,
          runSelection: null,
        });
        if (state.runId !== null && state.selectedFlow !== null) {
          await loadFrameChildrenPage(
            state.runId,
            state.selectedFlow,
            state.runRootFrame,
            0,
            generation,
          );
        }
        return;
      }
      await loadFramePath(nextPath, generation);
    },

    rootRunFrame: async () => {
      const state = get();
      if (state.runSource !== "history" || state.runFramePath.length === 0) {
        return;
      }
      const generation = ++replayGeneration;
      eventPageGeneration += 1;
      set({
        runFramePath: [],
        runFrameDetail: null,
        runFrameView: null,
        runFrameChildren: [],
        runFrameChildrenAfterSeq: 0,
        runFrameChildrenNextAfterSeq: 0,
        runFrameChildrenHasMore: false,
        runFrameError: null,
        runEventPage: null,
        runEventPageLoading: false,
        selectedNode: null,
        runSelection: null,
      });
      if (state.runId !== null && state.selectedFlow !== null) {
        await loadFrameChildrenPage(
          state.runId,
          state.selectedFlow,
          state.runRootFrame,
          0,
          generation,
        );
      }
    },

    pageRunFrames: async (where) => {
      const state = get();
      if (
        state.runSource !== "history" ||
        state.runId === null ||
        state.selectedFlow === null
      ) {
        return;
      }
      if (where === "next" && !state.runFrameChildrenHasMore) return;
      const parent = state.runFramePath.at(-1)?.frame ?? state.runRootFrame;
      const afterSeq =
        where === "first" ? 0 : state.runFrameChildrenNextAfterSeq;
      const generation = ++replayGeneration;
      await loadFrameChildrenPage(
        state.runId,
        state.selectedFlow,
        parent,
        afterSeq,
        generation,
      );
    },

    pageRunEvents: async (where) => {
      const state = get();
      if (
        state.runSource !== "history" ||
        state.runId === null ||
        state.selectedFlow === null
      ) {
        return;
      }
      if (where === "next" && !state.runEventPageHasMore) return;
      const frame = state.runFramePath.at(-1)?.frame ?? state.runRootFrame;
      const afterSeq = where === "first" ? 0 : state.runEventPageNextAfterSeq;
      const generation = ++eventPageGeneration;
      set({ runEventPageLoading: true, runReplayError: null });
      try {
        const page = await fetchRunEventPage(state.runId, state.selectedFlow, {
          afterSeq,
          frame,
        });
        const current = get();
        const currentFrame =
          current.runFramePath.at(-1)?.frame ?? current.runRootFrame;
        if (
          generation !== eventPageGeneration ||
          current.runId !== state.runId ||
          current.selectedFlow !== state.selectedFlow ||
          current.runSource !== "history" ||
          currentFrame !== frame ||
          (where === "next" &&
            current.runEventPageNextAfterSeq !== afterSeq)
        ) {
          return;
        }
        if (page.frame !== frame) {
          throw new Error("event replay frame changed between request and response");
        }
        if (page.events.length > RUN_REPLAY_PAGE_LIMIT) {
          throw new Error(
            `event page exceeds the ${RUN_REPLAY_PAGE_LIMIT}-record browser window`,
          );
        }
        const activeSummary = current.runFramePath.at(-1) ?? null;
        if (activeSummary === null && page.history_state === "running") {
          set({
            runView: emptyRunView(page.root_frame),
            runLive: true,
            runSource: "live",
            runReplayLoading: false,
            runReplayError: null,
            runRootFrame: page.root_frame,
            runEventPage: null,
            runEventPageLoading: false,
          });
          watchRun(state.runId);
          return;
        }
        const view =
          where === "first"
            ? emptyRunView(frame)
            : activeSummary === null
              ? current.runView
              : current.runFrameView;
        if (view === null) return;
        foldRunEventPage(view, page, afterSeq, current.runRootFrame);
        if (activeSummary === null) {
          settleRootReplay(view, page);
        } else {
          finalizeFrameReplay(view, activeSummary);
        }
        set({
          ...(activeSummary === null
            ? { runView: { ...view } }
            : { runFrameView: { ...view } }),
          runEventPage: page.events,
          runEventPageAfterSeq: afterSeq,
          runEventPageNextAfterSeq: page.next_after_seq,
          runEventPageHasMore: page.has_more,
          runEventPageLoading: false,
          runReplayError:
            activeSummary === null && page.history_state === "indeterminate"
              ? "run activity is indeterminate; showing the durable prefix without settling it"
              : null,
        });
      } catch (error) {
        if (generation !== eventPageGeneration) return;
        set({
          runEventPageLoading: false,
          runReplayError:
            error instanceof Error ? error.message : String(error),
        });
      }
    },
  };

  return { slice, resetForFlow };
}
