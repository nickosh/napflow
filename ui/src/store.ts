import { create } from "zustand";

import type { FlowModel } from "./api";
import { createCanvasSlice } from "./store/canvas";
import { DocumentHistory, jsonValueEqual } from "./store/history";
import { createPersistenceSlice } from "./store/persistence";
import { createRunReplaySlice } from "./store/runReplay";
import type { AppState } from "./store/types";

export const useAppStore = create<AppState>()((set, get) => {
  const runReplay = createRunReplaySlice(set, get);
  const documentHistory = new DocumentHistory<FlowModel>({
    equal: jsonValueEqual,
  });
  const persistence = createPersistenceSlice(
    set,
    get,
    runReplay.resetForFlow,
    documentHistory,
  );

  return {
    ...persistence.slice,
    ...createCanvasSlice(
      set,
      get,
      persistence.editFlow,
      documentHistory,
    ),
    ...runReplay.slice,
  };
});

export { AUTOSAVE_MS, ETAG_POLL_MS } from "./store/persistence";
export type { DetailError, SaveState } from "./store/types";
