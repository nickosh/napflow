// Playwright webServer command: scaffold a FRESH workspace (real
// `napf init`, so e2e always exercises the first-touch surface) and
// serve it with the real server + the BUILT bundle. Cross-platform —
// no shell-isms (NFR-02 applies to tooling too).
import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const repo = resolve(import.meta.dirname, "..", "..");
const port = process.env.NAPF_E2E_PORT ?? "46273";

const workspace = mkdtempSync(join(tmpdir(), "napf-e2e-"));
const init = spawnSync(
  "uv",
  ["run", "--project", repo, "napf", "init", workspace],
  { stdio: "inherit" },
);
if (init.status !== 0) {
  process.exit(init.status ?? 1);
}

// two extra flows the diagnostics e2e needs (FR-1006 check half):
// flows/warn — warning-only: W103, the request's error port is
// unwired (never runs; the URL is a placeholder)
const WARN_FLOW = `schema: "napflow/v1"
flow: {name: "warn"}
nodes:
  - {id: "start", type: "start", config: {ports: []}}
  - id: "req"
    type: "request"
    config: {method: "GET", url: "http://127.0.0.1:1/unused"}
  - {id: "end", type: "end", config: {ports: [{name: "done"}]}}
edges:
  - {from: "start.out", to: "req.trigger"}
  - {from: "req.response", to: "end.done"}
`;
// flows/broken — E004: two edges into one input; loads, fails check
const BROKEN_FLOW = `schema: "napflow/v1"
flow: {name: "broken"}
nodes:
  - {id: "start", type: "start", config: {ports: []}}
  - id: "extra"
    type: "log"
    config: {}
  - {id: "end", type: "end", config: {ports: [{name: "out"}]}}
edges:
  - {from: "start.out", to: "end.out"}
  - {from: "extra.out", to: "end.out"}
`;
for (const [name, content] of [
  ["warn", WARN_FLOW],
  ["broken", BROKEN_FLOW],
]) {
  mkdirSync(join(workspace, "flows", name));
  writeFileSync(join(workspace, "flows", name, "flow.yaml"), content, "utf-8");
}

const server = spawn(
  "uv",
  [
    "run",
    "--project",
    repo,
    "napf",
    "ui",
    "--no-browser",
    "--port",
    port,
  ],
  { cwd: workspace, stdio: "inherit" },
);
server.on("exit", (code) => process.exit(code ?? 0));
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => server.kill(signal));
}
