// Thin typed wrappers over the server REST surface (WM "Server
// surface"). The UI never constructs run semantics — core owns them.

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
};

export type FlowModel = {
  flow: { name: string; description?: string | null };
  nodes: FlowModelNode[];
  edges: { from: string; to: string }[];
  layout?: Record<string, [number, number]> | null;
};

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
  return getJson<FlowDetail>(`/api/flows/${identity}`);
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
  return putJson<SavedFlow>(`/api/flows/${identity}`, {
    flow,
    base_etag: baseEtag,
    force,
  });
}

export function fetchCode(identity: string): Promise<CodeFile> {
  return getJson<CodeFile>(`/api/code/${identity}`);
}

export function putCode(
  identity: string,
  code: string,
  baseEtag: string | null,
  force = false,
): Promise<SavedCode> {
  return putJson<SavedCode>(`/api/code/${identity}`, {
    code,
    base_etag: baseEtag,
    force,
  });
}

export function fetchEtags(identity: string): Promise<Etags> {
  return getJson<Etags>(`/api/etags/${identity}`);
}
