import { describe, expect, it } from "vitest";

import {
  applyRecord,
  emptyRunView,
  finalizeIncomplete,
  reduceRun,
  summarize,
  type RunRecord,
} from "./runview";

let seq = 0;
function rec(event: string, fields: Record<string, unknown> = {}): RunRecord {
  return { event, seq: ++seq, ts: "2026-07-08T12:00:00.000Z", ...fields };
}
function root(event: string, node: string, fields: Record<string, unknown> = {}) {
  return rec(event, { frame: "f-0", node, ...fields });
}

describe("applyRecord", () => {
  it("tracks firing counts and the active pulse window", () => {
    const view = emptyRunView();
    applyRecord(view, rec("run_started", { flow: "flows/smoke", env_name: null }));
    applyRecord(view, root("node_fired", "req", { firing_no: 1 }));
    expect(view.state).toBe("running");
    expect(view.nodes.req).toMatchObject({ firings: 1, active: true });

    // emitting an output ends the firing: active clears, outcome ok
    applyRecord(
      view,
      root("message_emitted", "req", {
        from_port: "req.response",
        to_node: "check",
        to_port: "input",
        msg_id: "m-1",
      }),
    );
    expect(view.nodes.req).toMatchObject({ active: false, outcome: "ok" });
    expect(view.edges["req.response→check.input"]).toMatchObject({ count: 1 });

    // re-fire (cycle): count grows, pulse restarts
    applyRecord(view, root("node_fired", "req", { firing_no: 2 }));
    expect(view.nodes.req).toMatchObject({ firings: 2, active: true });
  });

  it("replaces node state objects instead of mutating (React identity)", () => {
    const view = emptyRunView();
    applyRecord(view, root("node_fired", "a", { firing_no: 1 }));
    const before = view.nodes.a;
    applyRecord(
      view,
      root("message_emitted", "a", {
        from_port: "a.out",
        to_node: "b",
        to_port: "in",
      }),
    );
    expect(view.nodes.a).not.toBe(before);
    expect(before.active).toBe(true); // old snapshot untouched
  });

  it("colors asserts per result and tallies live", () => {
    const view = emptyRunView();
    applyRecord(view, root("assert_result", "check", { check: "status", passed: true }));
    expect(view.nodes.check.outcome).toBe("ok");
    applyRecord(view, root("assert_result", "check", { check: "body", passed: false }));
    expect(view.nodes.check.outcome).toBe("failed");
    // a later pass never un-fails the node
    applyRecord(view, root("assert_result", "check", { check: "again", passed: true }));
    expect(view.nodes.check.outcome).toBe("failed");
    expect(view.asserts).toEqual({ passed: 2, failed: 1 });
  });

  it("marks error-port emissions and python errors as errored", () => {
    const view = emptyRunView();
    applyRecord(view, root("python_error", "fn", { function: "f", error_type: "ValueError", message: "boom", traceback: "…" }));
    expect(view.nodes.fn.outcome).toBe("error");
    applyRecord(
      view,
      root("message_emitted", "req", {
        from_port: "req.error",
        to_node: "handler",
        to_port: "trigger",
      }),
    );
    expect(view.nodes.req.outcome).toBe("error");
  });

  it("keeps a retrying request active; final failure errors it", () => {
    const view = emptyRunView();
    applyRecord(view, root("node_fired", "req", { firing_no: 1 }));
    applyRecord(view, root("request_failed", "req", { error_kind: "timeout", message: "t/o", attempt: 1, will_retry: true }));
    expect(view.nodes.req).toMatchObject({ active: true, outcome: "none" });
    applyRecord(view, root("request_failed", "req", { error_kind: "timeout", message: "t/o", attempt: 2, will_retry: false }));
    expect(view.nodes.req.outcome).toBe("error");
  });

  it("treats a completed non-2xx exchange as ok (EC13: data, not error)", () => {
    const view = emptyRunView();
    applyRecord(view, root("request_finished", "req", { status: 503, size_bytes: 12, timing: {}, attempt: 1, retries_total: 3 }));
    expect(view.nodes.req.outcome).toBe("ok");
  });

  it("feeds log nodes their latest value, counting entries", () => {
    const view = emptyRunView();
    applyRecord(view, root("log", "logger", { value: { a: 1 }, level: "info" }));
    applyRecord(view, root("log", "logger", { value: "second", level: "info" }));
    expect(view.nodes.logger.log).toEqual({ value: "second", count: 2 });
  });

  it("records guard trips without treating them as errors (D19)", () => {
    const view = emptyRunView();
    applyRecord(view, root("guard_tripped", "retries", { kind: "counter", port: "exhausted" }));
    expect(view.nodes.retries.guard).toBe("exhausted");
    expect(view.nodes.retries.outcome).toBe("none");
  });

  it("ignores child-frame events for node state but keeps the record", () => {
    const view = emptyRunView();
    applyRecord(view, rec("node_fired", { frame: "f-0/f-1", node: "inner", firing_no: 1 }));
    expect(view.nodes.inner).toBeUndefined();
    expect(view.records).toHaveLength(1);
  });

  it("finalizes: state, tallies, skipped nodes, actives cleared", () => {
    const view = emptyRunView();
    applyRecord(view, root("node_fired", "req", { firing_no: 1 }));
    applyRecord(
      view,
      rec("run_finished", {
        state: "failed",
        duration_ms: 41.5,
        asserts: { passed: 2, failed: 1 },
        unhandled_errors: [],
        end_outputs: {},
        nodes_never_fired: ["orphan"],
      }),
    );
    expect(view.state).toBe("failed");
    expect(view.durationMs).toBe(41.5);
    expect(view.asserts).toEqual({ passed: 2, failed: 1 });
    expect(view.nodes.orphan.outcome).toBe("skipped");
    expect(view.nodes.req.active).toBe(false);
  });
});

describe("reduceRun (history replay)", () => {
  it("tolerates a dangling request_started (EC20) as incomplete", () => {
    const view = reduceRun([
      rec("run_started", { flow: "flows/x", env_name: null }),
      root("node_fired", "req", { firing_no: 1 }),
      root("request_started", "req", { method: "GET", url: "http://x", headers: {}, body_preview: null, attempt: 1 }),
      // aborted mid-request: the JSONL just stops (EC20)
    ]);
    expect(view.state).toBe("incomplete");
    expect(view.nodes.req.active).toBe(false); // settled, not pulsing
    expect(view.nodes.req.firings).toBe(1);
  });

  it("replays a finished run to its final overlay", () => {
    const view = reduceRun([
      rec("run_started", { flow: "flows/x", env_name: "dev" }),
      root("node_fired", "check", { firing_no: 1 }),
      root("assert_result", "check", { check: "status == 200", passed: true }),
      rec("run_finished", { state: "passed", duration_ms: 7, asserts: { passed: 1, failed: 0 }, unhandled_errors: [], end_outputs: {}, nodes_never_fired: [] }),
    ]);
    expect(view.state).toBe("passed");
    expect(view.nodes.check.outcome).toBe("ok");
  });

  it("finalizeIncomplete settles a live view whose socket died", () => {
    const view = emptyRunView();
    applyRecord(view, root("node_fired", "req", { firing_no: 1 }));
    finalizeIncomplete(view);
    expect(view.state).toBe("incomplete");
    expect(view.nodes.req.active).toBe(false);
  });
});

describe("summarize", () => {
  it("gives one-liners for the wire-detail rows", () => {
    expect(
      summarize(root("request_finished", "req", { status: 200, size_bytes: 512, timing: { total_ms: 12.4 } })),
    ).toBe("HTTP 200 · 512B · 12ms");
    expect(
      summarize(root("assert_result", "check", { check: "status == 200", passed: false })),
    ).toBe("✗ status == 200");
    expect(
      summarize(root("log", "logger", { label: "users", value: [1, 2, 3] })),
    ).toBe("users: [1,2,3]");
  });
});
