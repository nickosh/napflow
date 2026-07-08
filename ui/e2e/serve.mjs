// Playwright webServer command: scaffold a FRESH workspace (real
// `napf init`, so e2e always exercises the first-touch surface) and
// serve it with the real server + the BUILT bundle. Cross-platform —
// no shell-isms (NFR-02 applies to tooling too).
import { spawn, spawnSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
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

// flows/passcase — a verbatim copy of the scaffolded flows/smoke
// (flow.yaml + nodes.py; the fixture file is shared read-only) for the
// run e2e's PASSING live run. Spec FILES run in parallel workers
// against this ONE workspace, and editing.spec mutates flows/smoke
// (assert values, nodes.py) — running smoke from run.spec raced that
// and failed on 2-core CI runners (all 3 OS, 2026-07-08): specs that
// RUN a flow must own it exclusively.
cpSync(join(workspace, "flows", "smoke"), join(workspace, "flows", "passcase"), {
  recursive: true,
});

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
// flows/broken — E004: two edges into one input; loads, fails check.
// Since the M4 pin, check E-codes still render an editable canvas —
// only unloadable files get the error view (flows/unloadable below).
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
// flows/unloadable — E002 load failure (unknown node type): no model
// to render, so the detail GET 400s and the UI shows E-codes instead
const UNLOADABLE_FLOW = `schema: "napflow/v1"
flow: {name: "unloadable"}
nodes:
  - {id: "start", type: "no_such_type", config: {}}
edges: []
`;
// flows/typed + flows/hint — the live W102 connect hint needs a TYPED
// input to hover: flow-node inputs inherit the target's start-port
// types (number here; the default keeps the port optional, no E005)
const TYPED_FLOW = `schema: "napflow/v1"
flow: {name: "typed"}
nodes:
  - id: "start"
    type: "start"
    config: {ports: [{name: "count", type: "number", default: 1}]}
  - {id: "end", type: "end", config: {ports: [{name: "done"}]}}
edges:
  - {from: "start.out", to: "end.done"}
`;
const HINT_FLOW = `schema: "napflow/v1"
flow: {name: "hint"}
nodes:
  - {id: "start", type: "start", config: {ports: []}}
  - {id: "sub", type: "flow", config: {flow: "flows/typed"}}
  - {id: "end", type: "end", config: {ports: [{name: "done"}]}}
edges:
  - {from: "start.out", to: "end.done"}
`;
// flows/failcase — the run e2e's workhorse (S4/M5, FR-1005), fully
// offline: FAILS with the default input (100 < 5 is false), PASSES
// when the run popover overrides threshold to 3; the log node feeds
// the live-value display. Checks clean (0 warnings).
const FAILCASE_FLOW = `schema: "napflow/v1"
flow: {name: "failcase"}
nodes:
  - id: "start"
    type: "start"
    config: {ports: [{name: "threshold", type: "number", default: 100}]}
  - {id: "echo", type: "log", config: {label: "threshold"}}
  - id: "verify"
    type: "assert"
    config: {checks: [{kind: "expr", expr: "trigger.value", op: "lt", value: 5}]}
  - id: "end"
    type: "end"
    config: {ports: [{name: "ok", required: false}, {name: "not_ok", required: false}]}
edges:
  - {from: "start.threshold", to: "echo.in"}
  - {from: "echo.out", to: "verify.in"}
  - {from: "verify.passed", to: "end.ok"}
  - {from: "verify.failed", to: "end.not_ok"}
`;
// flows/slow — a 30s delay so the abort e2e has something running to
// abort (delay is exempt from the max_seconds default, D24)
const SLOW_FLOW = `schema: "napflow/v1"
flow: {name: "slow"}
nodes:
  - {id: "start", type: "start", config: {ports: []}}
  - {id: "wait", type: "delay", config: {seconds: 30}}
  - {id: "end", type: "end", config: {ports: [{name: "done", required: false}]}}
edges:
  - {from: "start.out", to: "wait.in"}
  - {from: "wait.out", to: "end.done"}
`;
for (const [name, content] of [
  ["warn", WARN_FLOW],
  ["broken", BROKEN_FLOW],
  ["unloadable", UNLOADABLE_FLOW],
  ["typed", TYPED_FLOW],
  ["hint", HINT_FLOW],
  ["failcase", FAILCASE_FLOW],
  ["slow", SLOW_FLOW],
]) {
  mkdirSync(join(workspace, "flows", name));
  writeFileSync(join(workspace, "flows", name, "flow.yaml"), content, "utf-8");
}

// a truncated JSONL — a run that died mid-request (abort/crash). The
// history browser must list it `incomplete` and replay it without
// choking on the dangling request_started (EC20). The 1970 stem sorts
// it after (below) any real runs the specs start.
const DANGLING_RUN_ID = "19700101-000000-ec20aa";
const danglingDir = join(workspace, ".napflow", "runs", "flows", "failcase");
mkdirSync(danglingDir, { recursive: true });
writeFileSync(
  join(danglingDir, `${DANGLING_RUN_ID}.jsonl`),
  [
    `{"event":"run_started","run_id":"${DANGLING_RUN_ID}","ts":"1970-01-01T00:00:00.000Z","seq":1,"flow":"flows/failcase","env_name":null,"inputs":{},"engine_version":"0.0.0"}`,
    `{"event":"node_fired","run_id":"${DANGLING_RUN_ID}","frame":"f-0","node":"echo","ts":"1970-01-01T00:00:00.001Z","seq":2,"firing_no":1}`,
    `{"event":"request_started","run_id":"${DANGLING_RUN_ID}","frame":"f-0","node":"echo","ts":"1970-01-01T00:00:00.002Z","seq":3,"method":"GET","url":"http://127.0.0.1:1/never","headers":{},"body_preview":null,"attempt":1}`,
    "",
  ].join("\n"),
  "utf-8",
);

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
