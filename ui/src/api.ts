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

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`${path}: HTTP ${response.status}`);
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
