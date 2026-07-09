// Pure fold: run event records (the JSONL lines verbatim, D13) →
// canvas overlay state. No React, no fetch — Vitest covers this.
//
// Scope pin (S4/M5): the overlay animates the CURRENT canvas from the
// ROOT frame's perspective. Container nodes (flow/loop) pulse from
// their own node_fired until their outputs emit back in the root
// frame; child-frame events still appear in the event stream (labeled
// with their frame path) but don't paint inner nodes — that belongs
// to M6's drill-in.

export type RunRecord = {
  event: string;
  run_id?: string;
  seq?: number;
  ts?: string;
  frame?: string;
  node?: string;
  [key: string]: unknown;
};

export const ROOT_FRAME = "f-0";

/** log-node history kept per node (M5.5) — the loop-debugging view */
export const LOG_RING = 50;

export type NodeOutcome = "none" | "ok" | "failed" | "error" | "skipped";

/** What crossed a port (M5.5): message_emitted names both ends, so
 * every emission paints the source's output AND the target's input.
 * `lastValue` is the event's value_preview — the NATIVE value up to
 * 512 chars of compact JSON, truncated marker beyond (engine spec §7). */
export type PortTraffic = {
  count: number;
  lastValue: unknown;
  lastTs: string | null;
};

/** Last request exchange on a node (run-mode inspector summary). */
export type RequestSummary = {
  method: string | null;
  url: string | null;
  status: number | null;
  sizeBytes: number | null;
  totalMs: number | null;
  attempt: number;
  error: string | null;
};

export type NodeRunState = {
  firings: number;
  /** fired (or mid-request) with no output emitted yet — pulse this */
  active: boolean;
  outcome: NodeOutcome;
  guard: "exhausted" | "expired" | null;
  /** seq of the last event touching the node — restarts flash CSS */
  lastSeq: number;
  /** log ring, newest LAST, capped at LOG_RING; count = total seen */
  log: { ring: unknown[]; count: number } | null;
  /** per-port traffic, keyed `in:<port>` / `out:<port>` */
  ports: Record<string, PortTraffic>;
  request: RequestSummary | null;
};

/** Keyed by the canvas edge id (`from.port→to.node.port` — message_
 * emitted's from_port is already the full `node.port` ref). */
export type EdgePulse = { count: number; lastSeq: number };

export type RunViewState =
  | "running"
  | "passed"
  | "failed"
  | "error"
  | "aborted"
  | "incomplete";

export type RunView = {
  state: RunViewState;
  records: RunRecord[];
  nodes: Record<string, NodeRunState>;
  edges: Record<string, EdgePulse>;
  asserts: { passed: number; failed: number };
  durationMs: number | null;
  errorReason: string | null;
  startedTs: string | null;
};

export function emptyRunView(): RunView {
  return {
    state: "running",
    records: [],
    nodes: {},
    edges: {},
    asserts: { passed: 0, failed: 0 },
    durationMs: null,
    errorReason: null,
    startedTs: null,
  };
}

// safe to share: `ports` is only ever REPLACED (withTraffic), never
// mutated, so every fresh node aliasing the same {} is fine
const FRESH_NODE: Omit<NodeRunState, "lastSeq"> = {
  firings: 0,
  active: false,
  outcome: "none",
  guard: null,
  log: null,
  ports: {},
  request: null,
};

function withTraffic(
  ports: Record<string, PortTraffic>,
  key: string,
  value: unknown,
  ts: string | null,
): Record<string, PortTraffic> {
  const prev = ports[key];
  return {
    ...ports,
    [key]: { count: (prev?.count ?? 0) + 1, lastValue: value, lastTs: ts },
  };
}

/** Node states are replaced, never mutated — React subscribers compare
 * by identity, so only the touched node re-renders per event. */
function touch(
  view: RunView,
  node: string,
  seq: number,
  patch: Partial<NodeRunState>,
): void {
  const prev = view.nodes[node] ?? { ...FRESH_NODE, lastSeq: -1 };
  view.nodes[node] = { ...prev, ...patch, lastSeq: seq };
}

/** Fold one record in. MUTATES `view` top-level containers (the store
 * clones the outer object per flush); per-node/per-edge entries are
 * replaced immutably (see `touch`). */
export function applyRecord(view: RunView, record: RunRecord): void {
  view.records.push(record);
  const seq = record.seq ?? view.records.length;
  const event = record.event;

  if (event === "run_started") {
    view.state = "running";
    view.startedTs = record.ts ?? null;
    return;
  }
  if (event === "run_finished") {
    const state = record.state as RunViewState;
    view.state = state;
    view.durationMs = (record.duration_ms as number) ?? null;
    const asserts = record.asserts as { passed?: number; failed?: number };
    view.asserts = {
      passed: asserts?.passed ?? view.asserts.passed,
      failed: asserts?.failed ?? view.asserts.failed,
    };
    view.errorReason = (record.error_reason as string) ?? null;
    for (const id of (record.nodes_never_fired as string[]) ?? []) {
      touch(view, id, seq, { outcome: "skipped" });
    }
    // whatever was still pulsing is over now (abort/error/EC20)
    for (const [id, state_] of Object.entries(view.nodes)) {
      if (state_.active) view.nodes[id] = { ...state_, active: false };
    }
    return;
  }

  // node/edge overlay state is root-frame only (scope pin above);
  // child-frame records still landed in view.records for the stream
  if (record.frame !== ROOT_FRAME) return;
  const node = record.node;

  switch (event) {
    case "node_fired":
      if (node) {
        const firings = (view.nodes[node]?.firings ?? 0) + 1;
        touch(view, node, seq, { firings, active: true });
      }
      break;
    case "request_started":
      if (node) {
        touch(view, node, seq, {
          active: true,
          request: {
            method: (record.method as string) ?? null,
            url: (record.url as string) ?? null,
            status: null,
            sizeBytes: null,
            totalMs: null,
            attempt: (record.attempt as number) ?? 1,
            error: null,
          },
        });
      }
      break;
    case "request_finished":
      // non-2xx is DATA (EC13) — any completed exchange is "ok"; the
      // flow's own asserts/conditions judge the status code
      if (node) {
        const prev = view.nodes[node]?.request;
        const timing = record.timing as Record<string, number> | undefined;
        touch(view, node, seq, {
          outcome: "ok",
          request: {
            method: prev?.method ?? null,
            url: prev?.url ?? null,
            status: (record.status as number) ?? null,
            sizeBytes: (record.size_bytes as number) ?? null,
            totalMs: timing?.total_ms ?? null,
            attempt: (record.attempt as number) ?? prev?.attempt ?? 1,
            error: null,
          },
        });
      }
      break;
    case "request_failed":
      if (node) {
        const prev = view.nodes[node]?.request;
        const request: RequestSummary = {
          method: prev?.method ?? null,
          url: prev?.url ?? null,
          status: null,
          sizeBytes: null,
          totalMs: null,
          attempt: (record.attempt as number) ?? prev?.attempt ?? 1,
          error: `${record.error_kind}: ${record.message}`,
        };
        // a retrying request stays active — but its error is worth
        // showing in the inspector while the next attempt runs
        if (record.will_retry === true) {
          touch(view, node, seq, { request });
        } else {
          touch(view, node, seq, { outcome: "error", request });
        }
      }
      break;
    case "message_emitted": {
      const key = `${record.from_port}→${record.to_node}.${record.to_port}`;
      const prev = view.edges[key];
      view.edges[key] = { count: (prev?.count ?? 0) + 1, lastSeq: seq };
      const ts = record.ts ?? null;
      if (node) {
        const prevNode = view.nodes[node];
        const fromRef = String(record.from_port ?? "");
        const port = fromRef.slice(fromRef.indexOf(".") + 1);
        // `error` is THE reserved error-port name (E012); an emission
        // there means this firing errored, whatever came before
        const outcome: NodeOutcome =
          port === "error"
            ? "error"
            : prevNode?.outcome === "failed" || prevNode?.outcome === "error"
              ? prevNode.outcome
              : "ok";
        touch(view, node, seq, {
          active: false,
          outcome,
          ports: withTraffic(
            prevNode?.ports ?? {},
            `out:${port}`,
            record.value_preview,
            ts,
          ),
        });
      }
      // the receiving end: paint the input port WITHOUT bumping
      // lastSeq — arrival is not a firing, so no flash (M5.5 pin)
      const toNode = record.to_node as string | undefined;
      const toPort = record.to_port as string | undefined;
      if (toNode && toPort) {
        const prevTo = view.nodes[toNode] ?? { ...FRESH_NODE, lastSeq: -1 };
        view.nodes[toNode] = {
          ...prevTo,
          ports: withTraffic(
            prevTo.ports,
            `in:${toPort}`,
            record.value_preview,
            ts,
          ),
        };
      }
      break;
    }
    case "assert_result": {
      const passed = record.passed === true;
      view.asserts = {
        passed: view.asserts.passed + (passed ? 1 : 0),
        failed: view.asserts.failed + (passed ? 0 : 1),
      };
      if (node) {
        const prev = view.nodes[node]?.outcome;
        touch(view, node, seq, {
          outcome: !passed || prev === "failed" ? "failed" : "ok",
        });
      }
      break;
    }
    case "python_error":
      if (node) touch(view, node, seq, { outcome: "error", active: false });
      break;
    case "log":
      if (node) {
        const prev = view.nodes[node]?.log;
        const ring = [...(prev?.ring ?? []), record.value].slice(-LOG_RING);
        touch(view, node, seq, {
          log: { ring, count: (prev?.count ?? 0) + 1 },
        });
      }
      break;
    case "guard_tripped":
      if (node) {
        touch(view, node, seq, {
          guard: record.port as "exhausted" | "expired",
        });
      }
      break;
    default:
      // budget_warning / capture_warning / future events: stream-only
      break;
  }
}

/** Replay a finished (or truncated) JSONL. A missing run_finished tail
 * means the run died mid-write — abort or crash. EC20: dangling
 * request_started (and any still-active node) is tolerated, shown
 * settled, and the run reads `incomplete`. */
export function reduceRun(records: RunRecord[]): RunView {
  const view = emptyRunView();
  for (const record of records) applyRecord(view, record);
  if (!records.some((r) => r.event === "run_finished")) {
    finalizeIncomplete(view);
  }
  return view;
}

/** The stream ended without run_finished (dead WS / truncated file). */
export function finalizeIncomplete(view: RunView): void {
  view.state = "incomplete";
  for (const [id, state] of Object.entries(view.nodes)) {
    if (state.active) view.nodes[id] = { ...state, active: false };
  }
}

/** One-line event summary for the stream list; the expanded row shows
 * the full record (headers, bodies, timing — full wire detail, D13). */
export function summarize(record: RunRecord): string {
  switch (record.event) {
    case "run_started":
      return `env=${record.env_name ?? "—"}`;
    case "node_fired":
      return `firing #${record.firing_no}`;
    case "request_started":
      return `${record.method} ${record.url} (attempt ${record.attempt})`;
    case "request_finished": {
      const timing = record.timing as Record<string, number> | undefined;
      const total = timing?.total_ms;
      return `HTTP ${record.status} · ${record.size_bytes}B${
        typeof total === "number" ? ` · ${Math.round(total)}ms` : ""
      }`;
    }
    case "request_failed":
      return `${record.error_kind}: ${record.message}${
        record.will_retry === true ? " (will retry)" : ""
      }`;
    case "message_emitted":
      return `${record.from_port} → ${record.to_node}.${record.to_port}`;
    case "assert_result":
      return `${record.passed === true ? "✓" : "✗"} ${record.check}`;
    case "python_error":
      return `${record.function}: ${record.error_type}: ${record.message}`;
    case "log":
      return `${record.label ?? record.node}: ${preview(record.value)}`;
    case "guard_tripped":
      return `${record.kind} ${record.port}`;
    case "budget_warning":
      return `${record.remaining} messages left in budget`;
    case "capture_warning":
      return `${record.remaining_mb}MB body capture left`;
    case "run_finished":
      return `${record.state} in ${Math.round((record.duration_ms as number) ?? 0)}ms`;
    default:
      return "";
  }
}

export function preview(value: unknown, max = 80): string {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  if (text === undefined) return "";
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

// ---- crossed-messages selection (M5.5) ------------------------------
// Clicking a wire — or a port handle, which E004 makes the same thing
// on the input side — lists the messages that traversed it: the
// wire-level twin of the node-click event filter.

export type RunTrafficSelection =
  | { kind: "edge"; from: string; to: string }
  | { kind: "port"; node: string; port: string; side: "input" | "output" };

/** Does this record belong to the selected wire/port's message list?
 * Root-frame only — same scope pin as the canvas overlay. */
export function matchesTraffic(
  record: RunRecord,
  sel: RunTrafficSelection,
): boolean {
  if (record.event !== "message_emitted" || record.frame !== ROOT_FRAME) {
    return false;
  }
  if (sel.kind === "edge") {
    return (
      record.from_port === sel.from &&
      `${record.to_node}.${record.to_port}` === sel.to
    );
  }
  return sel.side === "output"
    ? record.from_port === `${sel.node}.${sel.port}`
    : record.to_node === sel.node && record.to_port === sel.port;
}

export function trafficLabel(sel: RunTrafficSelection): string {
  return sel.kind === "edge"
    ? `${sel.from} → ${sel.to}`
    : `${sel.node}.${sel.port}`;
}
