// Pure fold: run event records (the JSONL lines verbatim, D13) →
// canvas overlay state. No React, no fetch — Vitest covers this.
//
// Scope pin (S4/M5): the overlay animates the CURRENT canvas from the
// ROOT frame's perspective. Container nodes (flow/loop) pulse from
// their own node_fired until their outputs emit back in the root
// frame; child-frame events still appear in the event stream (labeled
// with their frame path) but don't paint inner nodes — v0.2 M5 owns
// replay-time frame drill-in.

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
export const RUN_RECORD_WINDOW = 2_000;
export const RUN_FRAME_WINDOW = 200;

/** log-node history kept per node (M5.5) — the loop-debugging view */
export const LOG_RING = 50;

export type NodeOutcome = "none" | "ok" | "failed" | "error" | "skipped";

/** What crossed a port (M5.5): message_emitted names both ends, so
 * every emission paints the source's output AND the target's input.
 * Canonical `value` may be a lazy blob descriptor; featureless legacy
 * histories fall back to their lossy `value_preview`. */
export type PortTraffic = {
  count: number;
  lastValue: unknown;
  lastTs: string | null;
  /** Canonical message_emitted event locator. Null means the latest value
   * came from a legacy/local record without a durable sequence. */
  lastSeq: number | null;
};

/** Read the complete M4 message value without breaking v0.1/featureless
 * histories. Presence, rather than nullishness, matters: null is a valid
 * complete message value and must not fall back to a legacy preview. */
export function messageValue(record: RunRecord): unknown {
  return Object.hasOwn(record, "value") ? record.value : record.value_preview;
}

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
  | "incomplete"
  | "indeterminate";

export type RunView = {
  /** Exact frame whose node/edge aggregate this view folds. The root
   * canvas keeps its own view while frame drilldown uses a separate one. */
  scopeFrame: string;
  state: RunViewState;
  records: RunRecord[];
  recordCount: number;
  nodes: Record<string, NodeRunState>;
  edges: Record<string, EdgePulse>;
  asserts: { passed: number; failed: number };
  durationMs: number | null;
  errorReason: string | null;
  startedTs: string | null;
};

export function emptyRunView(scopeFrame = ROOT_FRAME): RunView {
  return {
    scopeFrame,
    state: "running",
    records: [],
    recordCount: 0,
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
  eventSeq: number | null,
): Record<string, PortTraffic> {
  const prev = ports[key];
  return {
    ...ports,
    [key]: {
      count: (prev?.count ?? 0) + 1,
      lastValue: value,
      lastTs: ts,
      lastSeq: eventSeq,
    },
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
  view.recordCount += 1;
  view.records.push(record);
  if (view.records.length > RUN_RECORD_WINDOW) {
    view.records.splice(0, view.records.length - RUN_RECORD_WINDOW);
  }
  const seq = record.seq ?? view.recordCount;
  // `seq` above may be a synthetic animation key for featureless/local
  // records. Only a real, exactly representable canonical sequence may be
  // used to address GET /events/{seq} for a lazy port-value read.
  const eventSeq =
    typeof record.seq === "number" &&
    Number.isSafeInteger(record.seq) &&
    record.seq > 0
      ? record.seq
      : null;
  const event = record.event;

  if (event === "run_started" && view.scopeFrame === ROOT_FRAME) {
    view.state = "running";
    view.startedTs = record.ts ?? null;
    return;
  }
  if (event === "run_finished" && view.scopeFrame === ROOT_FRAME) {
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
  if (record.frame !== view.scopeFrame) return;
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
            messageValue(record),
            ts,
            eventSeq,
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
            messageValue(record),
            ts,
            eventSeq,
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

// ---- paged replay + frame drilldown (v0.2/M5) ----------------------

export type ReplayHistoryState =
  | "running"
  | "complete"
  | "incomplete"
  | "indeterminate";

/** Scalar terminal fact projected from the durable run_finished record.
 * Content-heavy End outputs and error bodies remain lazy event detail. */
export type RunReplaySummary = {
  state: "passed" | "failed" | "error" | "aborted";
  duration_ms: number;
  asserts: { passed: number; failed: number };
  unhandled_error_count: number;
  nodes_never_fired_count: number;
  error_reason?: string;
};

/** Full-history, frame-scoped projection carried beside every bounded event
 * page. It contains no canonical records or content-heavy run outputs. */
export type RunViewSummary = {
  scope_frame: string;
  record_count: number;
  nodes: Record<string, NodeRunState>;
  edges: Record<string, EdgePulse>;
  asserts: { passed: number; failed: number };
  started_ts: string | null;
};

/** Minimum page shape consumed by the pure folder. The REST wrapper owns
 * version gating; keeping this structural lets Vitest exercise paging with
 * small in-memory pages and keeps fetch out of the reducer. */
export type RunEventPageLike = {
  root_frame: string;
  history_state: ReplayHistoryState;
  run_summary: RunReplaySummary | null;
  view_summary: RunViewSummary;
  after_seq: number;
  next_after_seq: number;
  has_more: boolean;
  events: RunRecord[];
};

function validateEventPage(
  page: RunEventPageLike,
  expectedAfterSeq: number,
  expectedRootFrame: string,
): void {
  if (page.after_seq !== expectedAfterSeq) {
    throw new Error(
      `replay page cursor mismatch: requested after_seq=${expectedAfterSeq}, received ${page.after_seq}`,
    );
  }
  if (page.root_frame !== expectedRootFrame) {
    throw new Error("replay root frame changed between pages");
  }
  if (
    !Number.isInteger(page.view_summary.record_count) ||
    page.view_summary.record_count < page.events.length
  ) {
    throw new Error("replay view summary has an invalid record count");
  }
  let previousSeq = expectedAfterSeq;
  for (const record of page.events) {
    if (
      !Number.isInteger(record.seq) ||
      (record.seq as number) <= previousSeq
    ) {
      throw new Error(
        `replay event sequence did not advance after ${previousSeq}`,
      );
    }
    previousSeq = record.seq as number;
  }
  const expectedNext = page.events.length > 0 ? previousSeq : expectedAfterSeq;
  if (page.next_after_seq !== expectedNext) {
    throw new Error(
      `replay next cursor mismatch: expected ${expectedNext}, received ${page.next_after_seq}`,
    );
  }
  if (page.has_more && page.next_after_seq <= expectedAfterSeq) {
    throw new Error(
      `replay page did not advance: after_seq=${expectedAfterSeq}, next_after_seq=${page.next_after_seq}`,
    );
  }
}

/** Fold exactly one bounded response into the reduced graph aggregate.
 * Validation happens before mutation, so a bad cursor cannot half-apply a
 * page or double-count records. The caller explicitly requests continuation. */
export function foldRunEventPage(
  view: RunView,
  page: RunEventPageLike,
  expectedAfterSeq: number,
  expectedRootFrame: string,
): void {
  validateEventPage(page, expectedAfterSeq, expectedRootFrame);
  if (page.view_summary.scope_frame !== view.scopeFrame) {
    throw new Error("replay view summary scope changed");
  }
  view.records = page.events.slice(-RUN_RECORD_WINDOW);
  view.recordCount = page.view_summary.record_count;
  view.nodes = { ...page.view_summary.nodes };
  view.edges = { ...page.view_summary.edges };
  view.asserts = { ...page.view_summary.asserts };
  view.startedTs = page.view_summary.started_ts;
}

/** Settle a root replay from bounded envelope metadata. A complete history
 * does not need to fetch the tail merely to rediscover run_finished. */
export function settleRootReplay(
  view: RunView,
  page: RunEventPageLike,
): void {
  if (page.history_state === "running") {
    view.state = "running";
    return;
  }
  if (page.history_state === "indeterminate") {
    view.state = "indeterminate";
    for (const [id, state] of Object.entries(view.nodes)) {
      if (state.active) view.nodes[id] = { ...state, active: false };
    }
    return;
  }
  if (page.history_state === "incomplete") {
    finalizeIncomplete(view);
    return;
  }
  const summary = page.run_summary;
  if (summary === null) {
    throw new Error("complete replay is missing its durable run summary");
  }
  view.state = summary.state;
  view.durationMs = summary.duration_ms;
  view.asserts = { ...summary.asserts };
  view.errorReason = summary.error_reason ?? null;
  for (const [id, state] of Object.entries(view.nodes)) {
    if (state.active) view.nodes[id] = { ...state, active: false };
  }
}

/** Bounded direct-child projection of canonical frame_finished. Heavy error
 * detail and End values stay in the lazy canonical event at this `seq`. */
export type RunFrameSummary = RunRecord & {
  event: "frame_finished";
  seq: number;
  frame: string;
  parent_frame: string;
  parent_node: string;
  flow: string;
  kind: "flow" | "loop";
  loop_index?: number | null;
  duration_ms: number;
  state: "passed" | "failed" | "error" | "aborted";
  asserts: { passed: number; failed: number };
  unhandled_error_count: number;
  end_output_names: string[];
};

export type FrameSummaryWindow = {
  frames: RunFrameSummary[];
  frameCount: number;
};

export type RunFramePageLike = {
  next_after_seq: number;
  has_more: boolean;
  frames: RunFrameSummary[];
};

export function emptyFrameSummaryWindow(): FrameSummaryWindow {
  return { frames: [], frameCount: 0 };
}

/** Append one response page and discard summaries older than the active
 * browser window. This deliberately does not build or cache a frame tree. */
export function appendFrameSummaries(
  window: FrameSummaryWindow,
  frames: RunFrameSummary[],
): void {
  window.frameCount += frames.length;
  window.frames.push(...frames);
  if (window.frames.length > RUN_FRAME_WINDOW) {
    window.frames.splice(0, window.frames.length - RUN_FRAME_WINDOW);
  }
}

/** A child event page has no run_finished. Its parent summary is the
 * authoritative completion fact, so settle the detail view from that record. */
export function finalizeFrameReplay(
  view: RunView,
  summary: RunFrameSummary,
): void {
  view.state = summary.state;
  view.durationMs = summary.duration_ms;
  view.asserts = { ...summary.asserts };
  for (const [id, state] of Object.entries(view.nodes)) {
    if (state.active) view.nodes[id] = { ...state, active: false };
  }
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
    case "frame_finished": {
      const index =
        typeof record.loop_index === "number" ? ` #${record.loop_index}` : "";
      return `${record.kind} ${record.flow}${index} · ${record.state} · ${Math.round(
        (record.duration_ms as number) ?? 0,
      )}ms`;
    }
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
  scopeFrame = ROOT_FRAME,
): boolean {
  if (record.event !== "message_emitted" || record.frame !== scopeFrame) {
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
