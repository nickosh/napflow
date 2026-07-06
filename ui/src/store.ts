import { create } from "zustand";

import {
  ApiError,
  fetchFlowDetail,
  fetchFlows,
  fetchWorkspace,
  type Diagnostic,
  type FlowDetail,
  type FlowSummary,
  type WorkspaceInfo,
} from "./api";

export type DetailError = {
  message: string;
  diagnostics: Diagnostic[];
};

type AppState = {
  workspace: WorkspaceInfo | null;
  flows: FlowSummary[];
  error: string | null;
  selectedFlow: string | null;
  detail: FlowDetail | null;
  detailError: DetailError | null;
  selectedNode: string | null;
  load: () => Promise<void>;
  openFlow: (identity: string, opts?: { push?: boolean }) => Promise<void>;
  selectNode: (id: string | null) => void;
};

function identityFromPath(pathname: string): string | null {
  const identity = pathname.replace(/^\/+|\/+$/g, "");
  return identity.length > 0 ? identity : null;
}

export const useAppStore = create<AppState>((set, get) => ({
  workspace: null,
  flows: [],
  error: null,
  selectedFlow: null,
  detail: null,
  detailError: null,
  selectedNode: null,

  load: async () => {
    try {
      const [workspace, flows] = await Promise.all([
        fetchWorkspace(),
        fetchFlows(),
      ]);
      set({ workspace, flows, error: null });
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
    set({ selectedFlow: identity, selectedNode: null });
    if (opts?.push !== false && window.location.pathname !== `/${identity}`) {
      window.history.pushState(null, "", `/${identity}`);
    }
    try {
      const detail = await fetchFlowDetail(identity);
      // a slow response for a flow the user already navigated away
      // from must not clobber the current one
      if (get().selectedFlow === identity) {
        set({ detail, detailError: null });
      }
    } catch (e) {
      if (get().selectedFlow !== identity) return;
      const detailError: DetailError =
        e instanceof ApiError
          ? { message: e.message, diagnostics: e.diagnostics }
          : { message: e instanceof Error ? e.message : String(e), diagnostics: [] };
      set({ detail: null, detailError });
    }
  },

  selectNode: (id) => set({ selectedNode: id }),
}));

// browser back/forward re-selects the flow from the path
window.addEventListener("popstate", () => {
  const identity = identityFromPath(window.location.pathname);
  if (identity !== null) {
    void useAppStore.getState().openFlow(identity, { push: false });
  }
});
