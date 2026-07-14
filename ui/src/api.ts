// Thin typed wrappers over the server REST surface (WM "Server
// surface"). The UI never constructs run semantics — core owns them.

import { apiIdentityPath } from "./identity";
import type {
  ReplayHistoryState,
  RunEventPageLike,
  RunFramePageLike,
  RunFrameSummary,
  RunRecord,
  RunReplaySummary,
} from "./runview";

export type WorkspaceInfo = {
  name: string | null;
  description: string | null;
  root: string;
  flows_root: string;
  main: string;
  env_profiles: string[];
  env_default: string | null;
  version: string;
};

export type FlowPort = {
  name: string;
  type?: string;
  required: boolean;
  default?: unknown;
};

export type FlowSummary = {
  identity: string;
  valid: boolean;
  name?: string;
  inputs?: FlowPort[];
  outputs?: { name: string; required: boolean }[];
};

export type Diagnostic = {
  severity: "error" | "warning";
  code: string;
  message: string;
  hint: string;
  file: string;
  line: number | null;
  column: number | null;
  node: string | null;
};

export type PortSurfacePayload = {
  inputs: Record<string, string>;
  outputs: Record<string, string>;
  required_inputs: string[];
  growable: boolean;
} | null;

export type FlowModelNode = {
  id: string;
  type: string;
  config?: Record<string, unknown> | null;
  max_seconds?: number | null;
};

export type FlowModel = {
  flow: { name: string; description?: string | null };
  nodes: FlowModelNode[];
  edges: { from: string; to: string }[];
  layout?: Record<string, [number, number]> | null;
};

/** One "used in N places" entry (D09): a flow whose listed nodes
 * reference this one. */
export type UsedBy = { identity: string; nodes: string[] };

export type FlowDetail = {
  identity: string;
  flow: FlowModel;
  diagnostics: Diagnostic[];
  // write-path tokens (S4/M4): content hashes for optimistic concurrency
  etag: string | null;
  code_etag: string | null;
  // nodes.py function names (AST-derived, EC14); null = missing/broken file
  functions: string[] | null;
  ports: Record<string, PortSurfacePayload>;
  // subflow UX (S4/M6, FR-1007): ghost-wire refs per node + D09 usage
  template_refs: Record<string, string[]>;
  used_by: UsedBy[];
};

export type SavedFlow = {
  identity: string;
  etag: string;
  diagnostics: Diagnostic[];
};

export type SyntaxError_ = {
  message: string;
  line: number | null;
  column: number | null;
};

export type CodeFile = {
  identity: string;
  exists: boolean;
  code: string;
  etag: string | null;
  syntax_error: SyntaxError_ | null;
  functions: string[] | null;
};

export type SavedCode = {
  identity: string;
  etag: string;
  syntax_error: SyntaxError_ | null;
  functions: string[] | null;
};

export type Etags = {
  identity: string;
  etag: string | null;
  code_etag: string | null;
};

/** 409 from a PUT: someone else wrote the file. Carries the current
 * etag so the client can reload or force (last-write-wins). */
export class ConflictError extends Error {
  constructor(readonly etag: string | null) {
    super("file changed on disk");
  }
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly diagnostics: Diagnostic[] = [],
  ) {
    super(message);
  }
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    let message = `${path}: HTTP ${response.status}`;
    let diagnostics: Diagnostic[] = [];
    try {
      const body = (await response.json()) as {
        message?: string;
        diagnostics?: Diagnostic[];
      };
      if (body.message) message = body.message;
      diagnostics = body.diagnostics ?? [];
    } catch {
      // non-JSON error body — keep the generic message
    }
    throw new ApiError(message, response.status, diagnostics);
  }
  return (await response.json()) as T;
}

export function fetchWorkspace(): Promise<WorkspaceInfo> {
  return getJson<WorkspaceInfo>("/api/workspace");
}

export async function fetchFlows(): Promise<FlowSummary[]> {
  const payload = await getJson<{ flows: FlowSummary[] }>("/api/flows");
  return payload.flows;
}

export function fetchFlowDetail(identity: string): Promise<FlowDetail> {
  return getJson<FlowDetail>(apiIdentityPath("/api/flows", identity));
}

async function putJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (response.status === 409) {
    const payload = (await response.json()) as { etag: string | null };
    throw new ConflictError(payload.etag);
  }
  if (!response.ok) {
    let message = `${path}: HTTP ${response.status}`;
    let diagnostics: Diagnostic[] = [];
    try {
      const payload = (await response.json()) as {
        message?: string;
        diagnostics?: Diagnostic[];
      };
      if (payload.message) message = payload.message;
      diagnostics = payload.diagnostics ?? [];
    } catch {
      // non-JSON error body — keep the generic message
    }
    throw new ApiError(message, response.status, diagnostics);
  }
  return (await response.json()) as T;
}

export function putFlow(
  identity: string,
  flow: FlowModel,
  baseEtag: string | null,
  force = false,
): Promise<SavedFlow> {
  return putJson<SavedFlow>(apiIdentityPath("/api/flows", identity), {
    flow,
    base_etag: baseEtag,
    force,
  });
}

export function fetchCode(identity: string): Promise<CodeFile> {
  return getJson<CodeFile>(apiIdentityPath("/api/code", identity));
}

export function putCode(
  identity: string,
  code: string,
  baseEtag: string | null,
  force = false,
): Promise<SavedCode> {
  return putJson<SavedCode>(apiIdentityPath("/api/code", identity), {
    code,
    base_etag: baseEtag,
    force,
  });
}

export function fetchEtags(identity: string): Promise<Etags> {
  return getJson<Etags>(apiIdentityPath("/api/etags", identity));
}

// ---- runs (S4/M5, FR-1005) ------------------------------------------

export type StartedRun = {
  run_id: string;
  flow: string;
  state: string;
  log: string;
  warnings: Diagnostic[];
  notes: string[];
};

export type RunListEntry = {
  run_id: string;
  state: string; // passed|failed|error|aborted|running|incomplete
};

export async function startRun(
  flow: string,
  env: string | null,
  inputs: Record<string, unknown>,
): Promise<StartedRun> {
  const response = await fetch("/api/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ flow, ...(env !== null ? { env } : {}), inputs }),
  });
  if (!response.ok) {
    let message = `run failed to start: HTTP ${response.status}`;
    let diagnostics: Diagnostic[] = [];
    try {
      const body = (await response.json()) as {
        message?: string;
        diagnostics?: Diagnostic[];
      };
      if (body.message) message = body.message;
      diagnostics = body.diagnostics ?? [];
    } catch {
      // non-JSON error body — keep the generic message
    }
    throw new ApiError(message, response.status, diagnostics);
  }
  return (await response.json()) as StartedRun;
}

/** D09's "Clone to new flow…": fork the source flow's folder to a new
 * identity under flows.root. 409 = dest already exists. */
export async function cloneFlow(
  source: string,
  dest: string,
): Promise<{ identity: string }> {
  const response = await fetch("/api/flows/clone", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source, dest }),
  });
  if (!response.ok) {
    let message = `clone failed: HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { message?: string };
      if (body.message) message = body.message;
    } catch {
      // non-JSON error body — keep the generic message
    }
    throw new ApiError(message, response.status, []);
  }
  return (await response.json()) as { identity: string };
}

export async function listRuns(flow: string): Promise<RunListEntry[]> {
  const payload = await getJson<{ runs: RunListEntry[] }>(
    `/api/runs?flow=${encodeURIComponent(flow)}`,
  );
  return payload.runs;
}

export const REPLAY_API_FORMAT = "napflow-replay/1" as const;
export const RUN_REPLAY_PAGE_LIMIT = 500;
export const FRAME_REPLAY_PAGE_LIMIT = 200;

type ReplayEnvelope = {
  api_format: typeof REPLAY_API_FORMAT;
  run_id: string;
  run_format: string | null;
  features: string[];
  root_frame: string;
  history_state: ReplayHistoryState;
  run_summary: RunReplaySummary | null;
};

export type RunEventPage = ReplayEnvelope &
  RunEventPageLike & {
    frame: string | null;
  };

export type RunFramePage = ReplayEnvelope &
  RunFramePageLike & {
    parent_frame: string;
    after_seq: number;
    frames: RunFrameSummary[];
  };

export type RunEventDetail = ReplayEnvelope & {
  event: RunRecord;
};

/** A version marker only protects replay if consumers reject unknown and
 * missing formats before interpreting any fields. */
export function requireReplayV1<T>(payload: T): T & ReplayEnvelope {
  const format =
    typeof payload === "object" && payload !== null
      ? (payload as { api_format?: unknown }).api_format
      : undefined;
  if (format !== REPLAY_API_FORMAT) {
    const shown = format === undefined ? "missing" : JSON.stringify(format);
    throw new Error(
      `unsupported replay API format: ${shown}; expected ${REPLAY_API_FORMAT}`,
    );
  }
  return payload as T & ReplayEnvelope;
}

function runReplayPath(
  runId: string,
  flow: string,
  suffix: string,
  params: Record<string, string | number | undefined>,
): string {
  const query = new URLSearchParams({ flow });
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) query.set(key, String(value));
  }
  return `/api/runs/${encodeURIComponent(runId)}/${suffix}?${query.toString()}`;
}

/** Replay = paged reads of the JSONL (D13). Canonical blob descriptors stay
 * unresolved here; the row-detail endpoint is the explicit lazy boundary. */
export async function fetchRunEventPage(
  runId: string,
  flow: string,
  options: { afterSeq: number; limit?: number; frame?: string },
): Promise<RunEventPage> {
  const payload = await getJson<unknown>(
    runReplayPath(runId, flow, "events", {
      after_seq: options.afterSeq,
      limit: options.limit ?? RUN_REPLAY_PAGE_LIMIT,
      frame: options.frame,
    }),
  );
  return requireReplayV1(payload) as RunEventPage;
}

export async function fetchRunFramePage(
  runId: string,
  flow: string,
  options: { parentFrame: string; afterSeq: number; limit?: number },
): Promise<RunFramePage> {
  const payload = await getJson<unknown>(
    runReplayPath(runId, flow, "frames", {
      parent_frame: options.parentFrame,
      after_seq: options.afterSeq,
      limit: options.limit ?? FRAME_REPLAY_PAGE_LIMIT,
    }),
  );
  return requireReplayV1(payload) as RunFramePage;
}

/** Resolve schema-declared content (including hash-verified blobs) for one
 * explicitly expanded event. Replay pages never call this eagerly. */
export async function fetchRunEventDetail(
  runId: string,
  flow: string,
  seq: number,
): Promise<RunEventDetail> {
  const payload = await getJson<unknown>(
    runReplayPath(
      runId,
      flow,
      `events/${encodeURIComponent(String(seq))}`,
      {},
    ),
  );
  return requireReplayV1(payload) as RunEventDetail;
}

export type AbortRunResult = { run_id: string; state: string };

export async function abortRun(runId: string): Promise<AbortRunResult> {
  const response = await fetch(
    `/api/runs/${encodeURIComponent(runId)}/abort`,
    { method: "POST" },
  );
  if (!response.ok) {
    let message = `abort failed: HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { message?: string };
      if (body.message) message = body.message;
    } catch {
      // non-JSON error body — keep the status-bearing message
    }
    throw new ApiError(message, response.status, []);
  }
  return (await response.json()) as AbortRunResult;
}

/** Live tail: text frames are the JSONL lines verbatim (durable disk prefix,
 * then a bounded live queue; close 4410 asks the client to reconnect/resync). */
export function openRunSocket(runId: string): WebSocket {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return new WebSocket(
    `${scheme}://${window.location.host}/ws/runs/${encodeURIComponent(runId)}`,
  );
}
