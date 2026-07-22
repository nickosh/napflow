import { create } from "zustand";

// F1 chrome state: view preferences (persisted per browser) plus the
// ephemeral overlay coordination the floating chrome shares — kept out
// of the canvas/persistence/run-replay slices in store/ on purpose.

export type Theme = "dark" | "light";

export type RunInputPort = {
  name: string;
  type?: string;
  default?: unknown;
};

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

function showRunInputValue(value: unknown): string {
  if (value === undefined) return "";
  return typeof value === "string" ? value : JSON.stringify(value);
}

function normalizeRunInputPorts(ports: RunInputPort[]): RunInputPort[] {
  return ports
    .filter((port) => port.name !== "")
    .map((port) => ({ ...port }));
}

function sameRunInputValue(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((value, index) => sameRunInputValue(value, right[index]))
    );
  }
  if (
    typeof left === "object" &&
    left !== null &&
    typeof right === "object" &&
    right !== null
  ) {
    const leftRecord = left as Record<string, unknown>;
    const rightRecord = right as Record<string, unknown>;
    const leftKeys = Object.keys(leftRecord).sort();
    const rightKeys = Object.keys(rightRecord).sort();
    return (
      leftKeys.length === rightKeys.length &&
      leftKeys.every(
        (key, index) =>
          key === rightKeys[index] &&
          sameRunInputValue(leftRecord[key], rightRecord[key]),
      )
    );
  }
  return false;
}

function sameRunInputPorts(
  left: RunInputPort[],
  right: RunInputPort[],
): boolean {
  return (
    left.length === right.length &&
    left.every((port, index) => {
      const other = right[index];
      const hasDefault = Object.prototype.hasOwnProperty.call(port, "default");
      const otherHasDefault = Object.prototype.hasOwnProperty.call(
        other,
        "default",
      );
      return (
        port.name === other.name &&
        (port.type ?? "any") === (other.type ?? "any") &&
        hasDefault === otherHasDefault &&
        (!hasDefault || sameRunInputValue(port.default, other.default))
      );
    })
  );
}

function closedRunPopover() {
  return {
    runPopoverOpen: false,
    runPopoverFlow: null,
    runInputPorts: [] as RunInputPort[],
    runInputCells: {} as Record<string, string>,
    runInputEdited: new Set<string>(),
    runInputInvalid: new Set<string>(),
  };
}

type ChromeState = {
  theme: Theme;
  minimapOn: boolean;
  flowsOpen: boolean;
  cmdkOpen: boolean;
  /** null = closed; otherwise the screen position the picker opened at */
  pickerAt: { x: number; y: number } | null;
  /** Shared run-input owner for the bottom pill and the ⌘K palette. */
  runPopoverOpen: boolean;
  runPopoverFlow: string | null;
  runInputPorts: RunInputPort[];
  runInputCells: Record<string, string>;
  runInputEdited: Set<string>;
  runInputInvalid: Set<string>;
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
  openRunPopover: (flow: string, ports: RunInputPort[]) => void;
  closeRunPopover: () => void;
  editRunInput: (name: string, text: string) => void;
  setRunInputInvalid: (invalid: ReadonlySet<string>) => void;
  syncRunPopoverFlow: (flow: string | null, ports: RunInputPort[]) => void;
  /** Compatibility close path used by the global Escape handler. */
  setRunPopoverOpen: (open: false) => void;
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
  ...closedRunPopover(),
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
  openRunPopover: (runPopoverFlow, ports) => {
    const runInputPorts = normalizeRunInputPorts(ports);
    set({
      runPopoverOpen: true,
      runPopoverFlow,
      runInputPorts,
      runInputCells: Object.fromEntries(
        runInputPorts.map((port) => [
          port.name,
          showRunInputValue(port.default),
        ]),
      ),
      runInputEdited: new Set(),
      runInputInvalid: new Set(),
    });
  },
  closeRunPopover: () => set(closedRunPopover()),
  editRunInput: (name, text) =>
    set((state) => {
      const runInputInvalid = new Set(state.runInputInvalid);
      runInputInvalid.delete(name);
      return {
        runInputCells: { ...state.runInputCells, [name]: text },
        runInputEdited: new Set(state.runInputEdited).add(name),
        runInputInvalid,
      };
    }),
  setRunInputInvalid: (invalid) =>
    set({ runInputInvalid: new Set(invalid) }),
  syncRunPopoverFlow: (flow, ports) =>
    set((state) => {
      if (!state.runPopoverOpen) return state;
      const currentPorts = normalizeRunInputPorts(ports);
      return state.runPopoverFlow !== flow ||
        !sameRunInputPorts(state.runInputPorts, currentPorts)
        ? closedRunPopover()
        : state;
    }),
  setRunPopoverOpen: () => set(closedRunPopover()),
  setDragging: (draggingNode) =>
    set(draggingNode === null ? { draggingNode, overTrash: false } : { draggingNode }),
  setOverTrash: (overTrash) => set({ overTrash }),
  setCodeOpen: (codeOpen) => set({ codeOpen }),
  requestTidy: () => set((s) => ({ tidyTick: s.tidyTick + 1 })),
}));
