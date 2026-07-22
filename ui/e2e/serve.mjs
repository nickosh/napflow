// Playwright webServer command: scaffold a FRESH workspace (real
// `napf init --example`, so e2e exercises the opt-in demo surface) and
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
  ["run", "--project", repo, "napf", "init", "--example", workspace],
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

// Portable URL-transport fixture for M1/FR-1111. Spaces, #, and % are
// legal on all three target filesystems but must be encoded per URL segment.
cpSync(
  join(workspace, "flows", "main"),
  join(workspace, "flows", "encoded name #100%"),
  { recursive: true },
);

// These valid workspace-relative identities would collide with the API and
// static surfaces if canvas deep links used the identity at URL root. They
// are intentionally outside the default discovery root: direct/reference
// identities still need to work through /flow/<identity>.
for (const identity of [
  ["api", "workspace"],
  ["assets", "canvas"],
]) {
  const parent = join(workspace, identity[0]);
  mkdirSync(parent, { recursive: true });
  cpSync(join(workspace, "flows", "main"), join(parent, identity[1]), {
    recursive: true,
  });
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
// flows/boundary — editing.spec's isolated boundary-authoring fixture.
// Start and the untriggered fixture are both frame-start sources initially;
// the source cue test connects/deletes fixture.trigger to prove it is live
// authoring state rather than a run-result decoration.
const BOUNDARY_FLOW = `schema: "napflow/v1"
flow: {name: "boundary"}
nodes:
  - {id: "start", type: "start", config: {ports: []}}
  - {id: "fx", type: "fixture", config: {file: "smoke.json"}}
  - {id: "end", type: "end", config: {ports: [{name: "done", required: false}]}}
edges:
  - {from: "start.out", to: "end.done"}
layout:
  start: [40, 80]
  fx: [360, 80]
  end: [680, 80]
`;
// flows/island — disconnected nodes are legal, with W104 phrased in terms of
// every execution source rather than Start alone.
const ISLAND_FLOW = `schema: "napflow/v1"
flow: {name: "island"}
nodes:
  - {id: "start", type: "start", config: {ports: []}}
  - {id: "stranded", type: "merge", config: {mode: "any"}}
  - {id: "end", type: "end", config: {ports: [{name: "done", required: false}]}}
edges:
  - {from: "start.out", to: "end.done"}
layout:
  start: [40, 80]
  stranded: [360, 260]
  end: [680, 80]
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
// flows/replay-parent + flows/replay-child — run.spec's M5 real-history
// fixture. The nested Python result exceeds the 64 KiB inline threshold,
// so the completed run exercises both durable frame_finished summaries and
// content-blobs/1 lazy detail through the browser (without touching the
// parent/child pair exclusively owned by subflow.spec).
const REPLAY_PARENT_FLOW = `schema: "napflow/v1"
flow: {name: "replay-parent"}
nodes:
  - {id: "start", type: "start", config: {ports: []}}
  - {id: "child", type: "flow", config: {flow: "flows/replay-child"}}
  - {id: "end", type: "end", config: {ports: [{name: "done"}]}}
edges:
  - {from: "start.out", to: "child.seed"}
  - {from: "child.done", to: "end.done"}
`;
const REPLAY_CHILD_FLOW = `schema: "napflow/v1"
flow: {name: "replay-child"}
nodes:
  - id: "start"
    type: "start"
    config: {ports: [{name: "seed"}]}
  - id: "produce"
    type: "python"
    config: {function: "make_large", outputs: ["large"]}
  - {id: "observe", type: "log", config: {label: "lazy child value"}}
  - id: "end"
    type: "end"
    config: {ports: [{name: "done"}, {name: "python_error", required: false}]}
edges:
  - {from: "start.seed", to: "produce.seed"}
  - {from: "produce.large", to: "observe.in"}
  - {from: "observe.out", to: "end.done"}
  - {from: "produce.error", to: "end.python_error"}
`;
const REPLAY_CHILD_NODES = `"""M5 browser replay fixture: one blob-backed child value."""


def make_large(seed):
    return {"large": "M5-LAZY-BLOB:" + ("x" * (72 * 1024)) + ":END"}
`;
for (const [name, content] of [
  ["warn", WARN_FLOW],
  ["broken", BROKEN_FLOW],
  ["unloadable", UNLOADABLE_FLOW],
  ["typed", TYPED_FLOW],
  ["hint", HINT_FLOW],
  ["boundary", BOUNDARY_FLOW],
  ["island", ISLAND_FLOW],
  ["failcase", FAILCASE_FLOW],
  ["parent", PARENT_FLOW],
  ["child", CHILD_FLOW],
  ["ghostcase", GHOSTCASE_FLOW],
  ["slow", SLOW_FLOW],
  ["replay-parent", REPLAY_PARENT_FLOW],
  ["replay-child", REPLAY_CHILD_FLOW],
]) {
  mkdirSync(join(workspace, "flows", name));
  writeFileSync(join(workspace, "flows", name, "flow.yaml"), content, "utf-8");
}
writeFileSync(
  join(workspace, "flows", "replay-child", "nodes.py"),
  REPLAY_CHILD_NODES,
  "utf-8",
);

// editing.spec mutates these models serially, and a Playwright retry starts a
// fresh worker against the SAME server/workspace. Keep source snapshots outside
// flows.root so they are not catalog entries and ordinary sidebar/editor
// actions never target them. The spec reads these baselines and force-restores
// its owned flows (plus smoke's nodes.py) in beforeAll on every worker/retry.
const editingBaseline = join(workspace, "e2e-baselines", "editing");
mkdirSync(editingBaseline, { recursive: true });
for (const name of ["main", "smoke", "hint", "boundary"]) {
  cpSync(join(workspace, "flows", name), join(editingBaseline, name), {
    recursive: true,
  });
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

// A complete, strictly consecutive run whose log and port values are valid
// blob descriptors but whose companion blob directory is intentionally
// absent. Replay projections and frame discovery must remain usable; only an
// explicit row expansion or port-modal open may surface the missing content.
const MISSING_BLOB_RUN_ID = "19700101-000001-b10b00";
const missingBlobDir = join(
  workspace,
  ".napflow",
  "runs",
  "flows",
  "replay-parent",
);
mkdirSync(missingBlobDir, { recursive: true });
const missingBlob = {
  $napflow: {
    kind: "blob",
    hash: `sha256:${"0".repeat(64)}`,
    bytes: 72 * 1024,
    media_type: "text/plain; charset=utf-8",
    codec: "utf-8",
  },
};
const missingBlobRecords = [
  {
    event: "run_started",
    run_id: MISSING_BLOB_RUN_ID,
    frame: "f-0",
    ts: "1970-01-01T00:00:01.000Z",
    seq: 1,
    format: "napflow-run/1",
    features: ["content-blobs/1"],
    flow: "flows/replay-parent",
    env_name: null,
    inputs: {},
    engine_version: "0.0.0",
  },
  {
    event: "node_fired",
    run_id: MISSING_BLOB_RUN_ID,
    frame: "f-0",
    node: "missing_blob",
    ts: "1970-01-01T00:00:01.001Z",
    seq: 2,
    firing_no: 1,
  },
  {
    event: "log",
    run_id: MISSING_BLOB_RUN_ID,
    frame: "f-0",
    node: "missing_blob",
    ts: "1970-01-01T00:00:01.002Z",
    seq: 3,
    label: "missing blob fixture",
    level: "info",
    value: missingBlob,
  },
  {
    event: "message_emitted",
    run_id: MISSING_BLOB_RUN_ID,
    frame: "f-0",
    node: "child",
    ts: "1970-01-01T00:00:01.003Z",
    seq: 4,
    from_port: "child.done",
    to_node: "end",
    to_port: "done",
    msg_id: "m-missing-blob",
    value: missingBlob,
  },
  {
    event: "run_finished",
    run_id: MISSING_BLOB_RUN_ID,
    ts: "1970-01-01T00:00:01.004Z",
    seq: 5,
    state: "passed",
    duration_ms: 3,
    asserts: { passed: 0, failed: 0 },
    unhandled_errors: [],
    end_outputs: {},
    nodes_never_fired: [],
  },
];
writeFileSync(
  join(missingBlobDir, `${MISSING_BLOB_RUN_ID}.jsonl`),
  `${missingBlobRecords.map((record) => JSON.stringify(record)).join("\n")}\n`,
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
