import type { Icon } from "@phosphor-icons/react";
import {
  ArrowSquareDown,
  ArrowSquareUp,
  Alarm,
  CheckSquare,
  Code,
  FileArrowDown,
  FlagCheckered,
  Globe,
  GitBranch,
  GitMerge,
  Hourglass,
  ListNumbers,
  Note,
  Play,
  Repeat,
  Shuffle,
  Stack,
  TerminalWindow,
} from "@phosphor-icons/react";

// F1 catalog metadata: icons, picker categories, and one-line
// descriptions for the REAL node catalog (flow-schema spec). The
// handoff prototype shipped a fictional catalog (schedule/webhook
// triggers are deferred by decision) — only its presentation applies.

export type NodeMeta = {
  icon: Icon;
  category: "I/O" | "Data" | "Logic" | "Flow";
  description: string;
  /** config keys shown as always-visible quick inputs on the card */
  quick: string[];
  /** per-type card width (owner call: blocks size to their content);
   * the in-card editor grows the card to at least EDITOR_WIDTH */
  width: number;
};

export const PICKER_TABS = ["All", "I/O", "Data", "Logic", "Flow"] as const;

export const NODE_META: Record<string, NodeMeta> = {
  request: {
    icon: Globe,
    category: "I/O",
    description:
      "Performs an HTTP request. Response on response, failures on error.",
    quick: ["method", "url"],
    width: 320,
  },
  python: {
    icon: Code,
    category: "Data",
    description:
      "Runs a function from this flow's nodes.py with the declared inputs.",
    quick: ["function"],
    width: 240,
  },
  assert: {
    icon: CheckSquare,
    category: "Logic",
    description:
      "Checks conditions against incoming data; failures fail the run (CI exit codes).",
    quick: [],
    width: 230,
  },
  condition: {
    icon: GitBranch,
    category: "Logic",
    description: "Routes each message to true or false by expression.",
    quick: ["expr"],
    width: 220,
  },
  switch: {
    icon: Shuffle,
    category: "Logic",
    description: "Routes by expression value across named cases.",
    quick: ["expr"],
    width: 230,
  },
  loop: {
    icon: Repeat,
    category: "Flow",
    description: "Runs another flow once per item of a list.",
    quick: ["over", "body"],
    width: 260,
  },
  flow: {
    icon: Stack,
    category: "Flow",
    description: "Runs another flow from the workspace as a single node.",
    quick: ["flow"],
    width: 250,
  },
  set: {
    icon: ArrowSquareDown,
    category: "Data",
    description: "Stores a value in this frame's flow variables.",
    quick: ["name"],
    width: 210,
  },
  get: {
    icon: ArrowSquareUp,
    category: "Data",
    description: "Reads a flow variable.",
    quick: ["name"],
    width: 200,
  },
  merge: {
    icon: GitMerge,
    category: "Data",
    description: "Joins paths into one stream: any, all, or collect N.",
    quick: ["mode"],
    width: 220,
  },
  counter: {
    icon: ListNumbers,
    category: "Logic",
    description: "Cycle guard: passes N times, then routes to exhausted.",
    quick: ["count"],
    width: 200,
  },
  timeout: {
    icon: Alarm,
    category: "Logic",
    description: "Cycle guard: passes until its time budget expires.",
    quick: ["seconds"],
    width: 200,
  },
  delay: {
    icon: Hourglass,
    category: "Flow",
    description: "Waits a fixed time before passing data on.",
    quick: ["seconds"],
    width: 200,
  },
  log: {
    icon: TerminalWindow,
    category: "I/O",
    description: "Writes a value to the run log at the chosen level.",
    quick: ["level", "label"],
    width: 300,
  },
  fixture: {
    icon: FileArrowDown,
    category: "I/O",
    description: "Loads inline or file data (relative to the data root).",
    quick: ["file"],
    width: 230,
  },
  note: {
    icon: Note,
    category: "Flow",
    description: "A canvas note — documentation only, never fires.",
    quick: [],
    width: 260,
  },
  // not in the palette (every flow has them) but they render as cards
  start: {
    icon: Play,
    category: "Flow",
    description: "Flow inputs — bind via napf run -i key=value.",
    quick: [],
    width: 200,
  },
  end: {
    icon: FlagCheckered,
    category: "Flow",
    description: "Flow outputs — required + unreached fails the run (D18).",
    quick: [],
    width: 200,
  },
};

const FALLBACK: NodeMeta = {
  icon: Stack,
  category: "Flow",
  description: "",
  quick: [],
  width: 240,
};

/** minimum card width once the in-card editor is expanded */
export const EDITOR_WIDTH = 320;

export function nodeMeta(type: string): NodeMeta {
  return NODE_META[type] ?? FALLBACK;
}
