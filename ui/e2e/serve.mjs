// Playwright webServer command: scaffold a FRESH workspace (real
// `napf init`, so e2e always exercises the first-touch surface) and
// serve it with the real server + the BUILT bundle. Cross-platform —
// no shell-isms (NFR-02 applies to tooling too).
import { spawn, spawnSync } from "node:child_process";
import {
  closeSync,
  cpSync,
  mkdirSync,
  mkdtempSync,
  openSync,
  writeFileSync,
  writeSync,
} from "node:fs";
import { createConnection } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { createWorkspaceCleanup } from "./workspace-cleanup.mjs";

const repo = resolve(import.meta.dirname, "..", "..");
const port = process.env.NAPF_E2E_PORT ?? "46273";

const workspace = mkdtempSync(join(tmpdir(), "napf-e2e-"));
let server = null;
let finishing = false;
const cleanupWorkspace = createWorkspaceCleanup(workspace);
// Covers normal child exit, Playwright SIGTERM, spawn/init failure, and an
// exception while generating the opt-in 110MiB fixture.
process.once("exit", cleanupWorkspace);

function portIsOpen() {
  return new Promise((resolveOpen) => {
    const socket = createConnection({ host: "127.0.0.1", port: Number(port) });
    let resolved = false;
    function finish(open) {
      if (resolved) return;
      resolved = true;
      socket.destroy();
      resolveOpen(open);
    }
    socket.once("connect", () => finish(true));
    socket.once("error", () => finish(false));
    socket.setTimeout(250, () => finish(false));
  });
}

async function finishAfterServerStops(code) {
  if (finishing) return;
  finishing = true;
  // `uv run` can release its ChildProcess just before the ASGI descendant
  // drops the listening socket. Keep the harness alive for that short tail;
  // it also lets Windows release the workspace cwd before removal.
  for (let attempt = 0; attempt < 100 && (await portIsOpen()); attempt += 1) {
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 50));
  }
  cleanupWorkspace();
  process.exit(code);
}

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    // Install this before init/fixture generation: the perf fixture is
    // 110MiB, and interruption during startup must not leak its workspace.
    if (server !== null) {
      // The child runs with the workspace as cwd; stop it before removal so
      // this works on Windows as well as POSIX. Its exit handler cleans up.
      if (!server.kill(signal)) {
        void finishAfterServerStops(0);
      }
    } else {
      cleanupWorkspace();
      process.exit(0);
    }
  });
}

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
// flows/parent + flows/child — the subflow e2e's pair (S4/M6, FR-1007):
// drill-in, "used in N places", and the clone action. subflow.spec OWNS
// both (the clone test repoints parent.sub) — no other spec may touch
// them (the flows/smoke race lesson, 2026-07-08).
const PARENT_FLOW = `schema: "napflow/v1"
flow: {name: "parent"}
nodes:
  - {id: "start", type: "start", config: {ports: []}}
  - {id: "sub", type: "flow", config: {flow: "flows/child"}}
  - {id: "end", type: "end", config: {ports: [{name: "done", required: false}]}}
edges:
  - {from: "start.out", to: "sub.val"}
  - {from: "sub.done", to: "end.done"}
`;
const CHILD_FLOW = `schema: "napflow/v1"
flow: {name: "child"}
nodes:
  - {id: "start", type: "start", config: {ports: [{name: "val", default: 1}]}}
  - {id: "end", type: "end", config: {ports: [{name: "done", required: false}]}}
edges:
  - {from: "start.val", to: "end.done"}
`;
// flows/ghostcase — a cross-node template reference (two's label reads
// one's output) renders as a ghost-wire; distinct from the real wires
const GHOSTCASE_FLOW = `schema: "napflow/v1"
flow: {name: "ghostcase"}
nodes:
  - {id: "start", type: "start", config: {ports: []}}
  - {id: "one", type: "log", config: {label: "one"}}
  - id: "two"
    type: "log"
    config: {label: "saw {{ nodes.one.out }}"}
  - {id: "end", type: "end", config: {ports: [{name: "done", required: false}]}}
edges:
  - {from: "start.out", to: "one.in"}
  - {from: "start.out", to: "two.in"}
  - {from: "two.out", to: "end.done"}
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
  ["parent", PARENT_FLOW],
  ["child", CHILD_FLOW],
  ["ghostcase", GHOSTCASE_FLOW],
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

// Opt-in M0 browser replay baselines. Ordinary Playwright never creates
// these files; playwright.perf.config.ts sets NAPF_E2E_PERF=1. Generate
// incrementally so the fixture itself does not need another 100MB buffer.
if (process.env.NAPF_E2E_PERF === "1") {
  const PERF_FLOW = `schema: "napflow/v1"
flow: {name: "perf"}
nodes:
  - {id: "start", type: "start", config: {ports: []}}
  - {id: "end", type: "end", config: {ports: [{name: "done", required: false}]}}
edges:
  - {from: "start.out", to: "end.done"}
`;
  mkdirSync(join(workspace, "flows", "perf"));
  writeFileSync(join(workspace, "flows", "perf", "flow.yaml"), PERF_FLOW, "utf-8");
  const perfRuns = join(workspace, ".napflow", "runs", "flows", "perf");
  mkdirSync(perfRuns, { recursive: true });

  function writePerfHistory(runId, targetBytes) {
    const fd = openSync(join(perfRuns, `${runId}.jsonl`), "w");
    let seq = 1;
    let written = 0;
    function line(record) {
      const encoded = `${JSON.stringify(record)}\n`;
      writeSync(fd, encoded, undefined, "utf-8");
      written += Buffer.byteLength(encoded);
    }
    line({
      event: "run_started",
      run_id: runId,
      ts: "1970-01-01T00:00:00.000Z",
      seq,
      format: "napflow-run/1",
      features: [],
      flow: "flows/perf",
      env_name: null,
      inputs: {},
      engine_version: "0.1.0",
    });
    const preview = "y".repeat(400);
    while (written < targetBytes - 1_000) {
      seq += 1;
      line({
        event: "message_emitted",
        run_id: runId,
        frame: "f-0",
        node: "start",
        ts: "1970-01-01T00:00:00.001Z",
        seq,
        from_port: "start.out",
        to_node: "end",
        to_port: "done",
        msg_id: `m-${seq}`,
        value_preview: preview,
      });
    }
    seq += 1;
    line({
      event: "run_finished",
      run_id: runId,
      ts: "1970-01-01T00:00:01.000Z",
      seq,
      state: "passed",
      duration_ms: 1000,
      asserts: { passed: 0, failed: 0 },
      unhandled_errors: [],
      end_outputs: {},
      nodes_never_fired: [],
    });
    closeSync(fd);
  }

  writePerfHistory("19700102-000000-100000", 10 * 1024 * 1024);
  writePerfHistory("19700103-000000-100000", 100 * 1024 * 1024);
}

server = spawn(
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
server.on("error", (error) => {
  console.error(error);
  process.exit(1);
});
server.on("exit", (code) => {
  void finishAfterServerStops(code ?? 0);
});
