import type { StateCreator } from "zustand";

import type {
  Diagnostic,
  FlowDetail,
  FlowModel,
  FlowSummary,
  RunListEntry,
  WorkspaceInfo,
} from "../api";
import type { SavePhase } from "../persistence";
import type {
  RunFrameSummary,
  RunTrafficSelection,
  RunView,
  RunRecord,
} from "../runview";

export type DetailError = {
  message: string;
  diagnostics: Diagnostic[];
};

export type SaveState = SavePhase;

export type PersistenceSlice = {
  workspace: WorkspaceInfo | null;
  // Persistent dotenv discovery warning.
  workspaceNotice: string | null;
  flows: FlowSummary[];
  error: string | null;
  selectedFlow: string | null;
  detailError: DetailError | null;
  // Editing (S4/M4).
  saveState: SaveState;
  saveError: string | null;
  // Validation errors from a rejected PUT.
  saveDiagnostics: Diagnostic[];
  load: () => Promise<void>;
  refreshFlows: () => Promise<void>;
  openFlow: (identity: string, opts?: { push?: boolean }) => Promise<boolean>;
  popFlow: (
    identity: string | null,
    historyIndex: number | null,
  ) => Promise<void>;
  resolveConflict: (how: "reload" | "overwrite") => Promise<void>;
  pollEtags: () => Promise<void>;
};

export type CanvasSlice = {
  // The open canvas document. Future temporal middleware snapshots flow only;
  // diagnostics, etags, and the surrounding session remain non-historical.
  detail: FlowDetail | null;
  selectedNode: string | null;
  // Bump = Canvas rebuilds xyflow state from detail.
  graphVersion: number;
  // A drag is live — hold off external reloads.
  interacting: boolean;
  selectNode: (id: string | null) => void;
  // Every canvas edit mutates detail.flow then autosaves.
  moveNode: (id: string, x: number, y: number) => void;
  connectEdge: (from: string, to: string) => void;
  deleteEdges: (edges: { from: string; to: string }[]) => void;
  deleteNode: (id: string) => void;
  addNode: (type: string, position?: [number, number]) => void;
  updateNodeConfig: (id: string, config: Record<string, unknown>) => void;
  updateNodeMaxSeconds: (
    id: string,
    maxSeconds: number | undefined,
  ) => void;
  setInteracting: (interacting: boolean) => void;
};

export type RunReplaySlice = {
  // runView !== null is RUN MODE: the canvas locks editing and animates off
  // the event stream.
  runView: RunView | null;
  runId: string | null;
  // A WebSocket is attached (live run, not replay).
  runLive: boolean;
  runSource: "live" | "history" | null;
  // F1: the bottom console also hosts check diagnostics as a tab; null closes it.
  runPanelTab: "events" | "history" | "diag" | null;
  runHistory: RunListEntry[] | null;
  // Selected env profile for the next run.
  runEnv: string | null;
  // Run-prep warnings plus start/abort failures.
  runNotice: string | null;
  runReplayLoading: boolean;
  runReplayError: string | null;
  runRootFrame: string;
  // Historical child frames render separately so their node ids never paint
  // the root canvas. Only the active path/window is retained.
  runFramePath: RunFrameSummary[];
  runFrameDetail: FlowDetail | null;
  runFrameView: RunView | null;
  runFrameChildren: RunFrameSummary[];
  runFrameChildrenAfterSeq: number;
  runFrameChildrenNextAfterSeq: number;
  runFrameChildrenHasMore: boolean;
  runFrameLoading: boolean;
  runFrameError: string | null;
  // The aggregate keeps a tail ring; this optional one-page browser makes
  // older durable events reachable without growing active state.
  runEventPage: RunRecord[] | null;
  runEventPageAfterSeq: number;
  runEventPageNextAfterSeq: number;
  runEventPageHasMore: boolean;
  runEventPageLoading: boolean;
  // Selected wire/port whose crossed messages the panel lists; mutually
  // exclusive with selectedNode.
  runSelection: RunTrafficSelection | null;
  selectRunTraffic: (selection: RunTrafficSelection | null) => void;
  setRunEnv: (env: string | null) => void;
  startRun: (inputs: Record<string, unknown>) => Promise<void>;
  abortRun: () => Promise<void>;
  exitRun: () => void;
  openRunPanel: (tab: "events" | "history" | "diag") => void;
  closeRunPanel: () => void;
  openHistoryRun: (runId: string) => Promise<void>;
  openRunFrame: (summary: RunFrameSummary) => Promise<void>;
  backRunFrame: () => Promise<void>;
  rootRunFrame: () => Promise<void>;
  pageRunFrames: (where: "first" | "next") => Promise<void>;
  pageRunEvents: (where: "first" | "next") => Promise<void>;
};

export type AppState = PersistenceSlice & CanvasSlice & RunReplaySlice;

type AppStateCreator = StateCreator<AppState, [], [], AppState>;

export type StoreSet = Parameters<AppStateCreator>[0];
export type StoreGet = Parameters<AppStateCreator>[1];

export type EditFlow = (
  mutate: (flow: FlowModel) => FlowModel,
  opts?: { rebuild?: boolean },
) => void;

type RunReplayAction =
  | "selectRunTraffic"
  | "setRunEnv"
  | "startRun"
  | "abortRun"
  | "exitRun"
  | "openRunPanel"
  | "closeRunPanel"
  | "openHistoryRun"
  | "openRunFrame"
  | "backRunFrame"
  | "rootRunFrame"
  | "pageRunFrames"
  | "pageRunEvents";

export type RunResetPatch = Omit<
  RunReplaySlice,
  RunReplayAction | "runEnv"
>;
