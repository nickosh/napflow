import { create } from "zustand";

import {
  fetchFlows,
  fetchWorkspace,
  type FlowSummary,
  type WorkspaceInfo,
} from "./api";

type AppState = {
  workspace: WorkspaceInfo | null;
  flows: FlowSummary[];
  error: string | null;
  load: () => Promise<void>;
};

export const useAppStore = create<AppState>((set) => ({
  workspace: null,
  flows: [],
  error: null,
  load: async () => {
    try {
      const [workspace, flows] = await Promise.all([
        fetchWorkspace(),
        fetchFlows(),
      ]);
      set({ workspace, flows, error: null });
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
    }
  },
}));
