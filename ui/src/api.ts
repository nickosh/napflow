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
  ports: Record<string, PortSurfacePayload>;
};

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
