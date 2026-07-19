import { create } from "zustand";

import { createCanvasSlice } from "./store/canvas";
import { createPersistenceSlice } from "./store/persistence";
import { createRunReplaySlice } from "./store/runReplay";
import type { AppState } from "./store/types";

export const useAppStore = create<AppState>()((set, get) => {
  const runReplay = createRunReplaySlice(set, get);
  const persistence = createPersistenceSlice(
    set,
    get,
    runReplay.resetForFlow,
  );

  return {
    ...persistence.slice,
    ...createCanvasSlice(set, get, persistence.editFlow),
    ...runReplay.slice,
  };
});

export { AUTOSAVE_MS, ETAG_POLL_MS } from "./store/persistence";
export type { DetailError, SaveState } from "./store/types";
