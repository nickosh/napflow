import { create } from "zustand";

// F1 chrome state: view preferences (persisted per browser) plus the
// ephemeral overlay coordination the floating chrome shares — kept out
// of store.ts on purpose (that file is the document/run state and has
// its own split pending, F1 Slice 1).

export type Theme = "dark" | "light";

const THEME_KEY = "napflow.theme";
const MINIMAP_KEY = "napflow.minimap";

function readTheme(): Theme {
  try {
    return localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

function readMinimap(): boolean {
  try {
    return localStorage.getItem(MINIMAP_KEY) !== "off";
  } catch {
    return true;
  }
}

type ChromeState = {
  theme: Theme;
  minimapOn: boolean;
  flowsOpen: boolean;
  cmdkOpen: boolean;
  /** null = closed; otherwise the screen position the picker opened at */
  pickerAt: { x: number; y: number } | null;
  /** the run-inputs popover, openable from the run pill and the ⌘K palette */
  runPopoverOpen: boolean;
  /** node id being dragged (the trash zone shows while set) */
  draggingNode: string | null;
  overTrash: boolean;
  /** the nodes.py editor modal */
  codeOpen: boolean;
  /** bumped by the tidy wand / ⌘K action; the canvas effect runs it */
  tidyTick: number;
  toggleTheme: () => void;
  toggleMinimap: () => void;
  setFlowsOpen: (open: boolean) => void;
  setCmdkOpen: (open: boolean) => void;
  openPickerAt: (x: number, y: number) => void;
  closePicker: () => void;
  setRunPopoverOpen: (open: boolean) => void;
  setDragging: (node: string | null) => void;
  setOverTrash: (over: boolean) => void;
  setCodeOpen: (open: boolean) => void;
  requestTidy: () => void;
};

export const useChrome = create<ChromeState>()((set) => ({
  theme: readTheme(),
  minimapOn: readMinimap(),
  flowsOpen: false,
  cmdkOpen: false,
  pickerAt: null,
  runPopoverOpen: false,
  draggingNode: null,
  overTrash: false,
  codeOpen: false,
  tidyTick: 0,
  toggleTheme: () =>
    set((s) => {
      const theme: Theme = s.theme === "dark" ? "light" : "dark";
      try {
        localStorage.setItem(THEME_KEY, theme);
      } catch {
        // preference only — a blocked storage never breaks the app
      }
      return { theme };
    }),
  toggleMinimap: () =>
    set((s) => {
      const minimapOn = !s.minimapOn;
      try {
        localStorage.setItem(MINIMAP_KEY, minimapOn ? "on" : "off");
      } catch {
        // preference only
      }
      return { minimapOn };
    }),
  setFlowsOpen: (flowsOpen) => set({ flowsOpen }),
  setCmdkOpen: (cmdkOpen) => set({ cmdkOpen }),
  openPickerAt: (x, y) =>
    set({
      pickerAt: {
        x: Math.min(Math.max(10, x), window.innerWidth - 300),
        y: Math.min(Math.max(10, y), window.innerHeight - 440),
      },
      flowsOpen: false,
    }),
  closePicker: () => set({ pickerAt: null }),
  setRunPopoverOpen: (runPopoverOpen) => set({ runPopoverOpen }),
  setDragging: (draggingNode) =>
    set(draggingNode === null ? { draggingNode, overTrash: false } : { draggingNode }),
  setOverTrash: (overTrash) => set({ overTrash }),
  setCodeOpen: (codeOpen) => set({ codeOpen }),
  requestTidy: () => set((s) => ({ tidyTick: s.tidyTick + 1 })),
}));
