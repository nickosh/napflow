import { describe, expect, it } from "vitest";

import {
  LOG_RING,
  RUN_FRAME_WINDOW,
  RUN_RECORD_WINDOW,
  applyRecord,
  appendFrameSummaries,
  emptyRunView,
  finalizeFrameReplay,
  finalizeIncomplete,
  foldRunEventPage,
  matchesTraffic,
  reduceRun,
  settleRootReplay,
  summarize,
  type RunRecord,
  type RunFrameSummary,
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

  it("appends log values to a ring, newest last (M5.5)", () => {
    const view = emptyRunView();
    applyRecord(view, root("log", "logger", { value: { a: 1 }, level: "info" }));
    applyRecord(view, root("log", "logger", { value: "second", level: "info" }));
    expect(view.nodes.logger.log).toEqual({ ring: [{ a: 1 }, "second"], count: 2 });
  });

  it("caps the log ring at LOG_RING, keeping the newest", () => {
    const view = emptyRunView();
    for (let i = 1; i <= LOG_RING + 5; i++) {
      applyRecord(view, root("log", "logger", { value: i }));
    }
    const log = view.nodes.logger.log!;
    expect(log.count).toBe(LOG_RING + 5);
    expect(log.ring).toHaveLength(LOG_RING);
    expect(log.ring[0]).toBe(6); // oldest 5 rolled off
    expect(log.ring[log.ring.length - 1]).toBe(LOG_RING + 5);
  });

  it("paints port traffic on BOTH ends of an emission (M5.5)", () => {
    const view = emptyRunView();
    applyRecord(view, root("node_fired", "a", { firing_no: 1 }));
    applyRecord(
      view,
      root("message_emitted", "a", {
        from_port: "a.out",
        to_node: "b",
        to_port: "in",
        msg_id: "m-1",
        value: { hello: 1 },
      }),
    );
    expect(view.nodes.a.ports["out:out"]).toMatchObject({
      count: 1,
      lastValue: { hello: 1 },
    });
    expect(view.nodes.b.ports["in:in"]).toMatchObject({
      count: 1,
      lastValue: { hello: 1 },
    });
    // arrival is not a firing: no flash (lastSeq untouched), no outcome
    expect(view.nodes.b.lastSeq).toBe(-1);
    expect(view.nodes.b.outcome).toBe("none");

    applyRecord(
      view,
      root("message_emitted", "a", {
        from_port: "a.out",
        to_node: "b",
        to_port: "in",
        msg_id: "m-2",
        value: "next",
      }),
    );
    expect(view.nodes.a.ports["out:out"]).toMatchObject({
      count: 2,
      lastValue: "next",
    });
  });

  it("prefers a complete null value over a legacy preview", () => {
    const view = emptyRunView();
    applyRecord(
      view,
      root("message_emitted", "a", {
        from_port: "a.out",
        to_node: "b",
        to_port: "in",
        msg_id: "m-1",
        value: null,
        value_preview: "stale preview",
      }),
    );

    expect(view.nodes.a.ports["out:out"].lastValue).toBeNull();
    expect(view.nodes.b.ports["in:in"].lastValue).toBeNull();
  });

  it("summarizes the request exchange for the inspector (M5.5)", () => {
    const view = emptyRunView();
    applyRecord(view, root("request_started", "req", {
      method: "GET",
      url: "http://x",
      attempt: 1,
      request: { method: "GET", url: "http://x", headers: {}, body: null, size_bytes: 0 },
    }));
    expect(view.nodes.req.request).toMatchObject({ method: "GET", url: "http://x", status: null });
    applyRecord(view, root("request_finished", "req", { status: 200, size_bytes: 12, timing: { total_ms: 34.2 }, attempt: 1, retries_total: 3 }));
    expect(view.nodes.req.request).toMatchObject({
      method: "GET",
      url: "http://x",
      status: 200,
      sizeBytes: 12,
      totalMs: 34.2,
    });
  });

  it("keeps the retry error visible on the request summary", () => {
    const view = emptyRunView();
    applyRecord(view, root("request_started", "req", {
      method: "GET",
      url: "http://x",
      attempt: 1,
      request: { method: "GET", url: "http://x", headers: {}, body: null, size_bytes: 0 },
    }));
    applyRecord(view, root("request_failed", "req", { error_kind: "timeout", message: "t/o", attempt: 1, will_retry: true }));
    expect(view.nodes.req.request).toMatchObject({
      url: "http://x",
      error: "timeout: t/o",
    });
    expect(view.nodes.req.active).toBe(true); // still retrying
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

  it("folds 100k events while retaining only the browser record window", () => {
    const view = emptyRunView();
    for (let index = 1; index <= 100_000; index += 1) {
      applyRecord(view, {
        event: "message_emitted",
        seq: index,
        frame: "f-0/f-1",
      });
    }

    expect(view.recordCount).toBe(100_000);
    expect(view.records).toHaveLength(RUN_RECORD_WINDOW);
    expect(view.records[0].seq).toBe(100_000 - RUN_RECORD_WINDOW + 1);
    expect(view.records.at(-1)?.seq).toBe(100_000);
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
  it("falls back to message previews in featureless legacy history", () => {
    const view = reduceRun([
      rec("run_started", {
        format: "napflow-run/1",
        features: [],
        flow: "flows/x",
        env_name: null,
      }),
      root("message_emitted", "a", {
        from_port: "a.out",
        to_node: "b",
        to_port: "in",
        msg_id: "m-1",
        value_preview: { legacy: true },
      }),
      rec("run_finished", {
        state: "passed",
        duration_ms: 1,
        asserts: { passed: 0, failed: 0 },
        unhandled_errors: [],
        end_outputs: {},
        nodes_never_fired: [],
      }),
    ]);

    expect(view.nodes.a.ports["out:out"].lastValue).toEqual({ legacy: true });
    expect(view.nodes.b.ports["in:in"].lastValue).toEqual({ legacy: true });
  });

  it("tolerates a dangling request_started (EC20) as incomplete", () => {
    const view = reduceRun([
      rec("run_started", { flow: "flows/x", env_name: null }),
      root("node_fired", "req", { firing_no: 1 }),
      root("request_started", "req", {
        method: "GET",
        url: "http://x",
        attempt: 1,
        request: { method: "GET", url: "http://x", headers: {}, body: null, size_bytes: 0 },
      }),
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

describe("paged replay", () => {
  const summary = {
    state: "passed" as const,
    duration_ms: 17,
    asserts: { passed: 3, failed: 0 },
    unhandled_error_count: 0,
    nodes_never_fired_count: 0,
  };
  const viewSummary = (
    recordCount: number,
    nodes: ReturnType<typeof emptyRunView>["nodes"] = {},
  ) => ({
    scope_frame: "f-0",
    record_count: recordCount,
    nodes,
    edges: {},
    asserts: { passed: 3, failed: 0 },
    started_ts: "2026-07-13T00:00:00.000Z",
  });

  it("folds one page but hydrates the complete >500-event node projection", () => {
    const events = Array.from({ length: 500 }, (_, index) => ({
      event: index === 0 ? "run_started" : "node_fired",
      seq: index + 1,
      ...(index > 0 ? { frame: "f-0", node: "work", firing_no: index } : {}),
    }));
    const view = emptyRunView("f-0");
    const page = {
      root_frame: "f-0",
      history_state: "complete" as const,
      run_summary: summary,
      view_summary: viewSummary(1_102, {
        work: {
          firings: 1_100,
          active: false,
          outcome: "ok",
          guard: null,
          lastSeq: 1_101,
          log: null,
          ports: {},
          request: null,
        },
      }),
      after_seq: 0,
      next_after_seq: 500,
      has_more: true,
      events,
    };

    foldRunEventPage(view, page, 0, "f-0");
    settleRootReplay(view, page);

    expect(view.records).toHaveLength(500);
    expect(view.recordCount).toBe(1_102);
    expect(view.nodes.work.firings).toBe(1_100);
    expect(view.state).toBe("passed");
    expect(view.durationMs).toBe(17);
    expect(view.asserts).toEqual({ passed: 3, failed: 0 });
  });

  it("settles only a known incomplete root and leaves running/indeterminate distinct", () => {
    const settle = (
      history_state: "running" | "incomplete" | "indeterminate",
    ) => {
      const view = emptyRunView();
      settleRootReplay(view, {
        root_frame: "f-0",
        history_state,
        run_summary: null,
        view_summary: viewSummary(0),
        after_seq: 0,
        next_after_seq: 0,
        has_more: false,
        events: [],
      });
      return view.state;
    };

    expect(settle("incomplete")).toBe("incomplete");
    expect(settle("running")).toBe("running");
    expect(settle("indeterminate")).toBe("indeterminate");
  });

  it("rejects a duplicate/non-advancing cursor before mutating the view", () => {
    const view = emptyRunView();
    expect(() =>
      foldRunEventPage(view, {
        root_frame: "f-0",
        history_state: "complete",
        run_summary: summary,
        view_summary: viewSummary(0),
        after_seq: 0,
        next_after_seq: 0,
        has_more: true,
        events: [],
      }, 0, "f-0"),
    ).toThrow("did not advance");
    expect(view.recordCount).toBe(0);
  });
});

describe("frame drilldown", () => {
  function frame(index: number, parent = "f-0"): RunFrameSummary {
    return {
      event: "frame_finished",
      seq: index + 1,
      frame: `${parent}/f-${index}`,
      parent_frame: parent,
      parent_node: "items",
      flow: "flows/item",
      kind: "loop",
      loop_index: index,
      duration_ms: index,
      state: "passed",
      asserts: { passed: 0, failed: 0 },
      unhandled_error_count: 0,
      end_output_names: [],
    };
  }

  it("bounds the active direct-child summary window and keys identity by frame", () => {
    const window = { frames: [], frameCount: 0 } as {
      frames: RunFrameSummary[];
      frameCount: number;
    };
    const summaries = Array.from(
      { length: RUN_FRAME_WINDOW + 7 },
      (_, index) => frame(index),
    );
    appendFrameSummaries(window, summaries);
    expect(window.frameCount).toBe(RUN_FRAME_WINDOW + 7);
    expect(window.frames).toHaveLength(RUN_FRAME_WINDOW);
    expect(window.frames[0].frame).toBe("f-0/f-7");
    expect(new Set(window.frames.map((summary) => summary.frame)).size).toBe(
      RUN_FRAME_WINDOW,
    );
  });

  it("folds child nodes in a separate scope and settles from frame_finished", () => {
    const summary = frame(3);
    const child = emptyRunView(summary.frame);
    applyRecord(child, {
      event: "node_fired",
      seq: 2,
      frame: summary.frame,
      node: "inner",
      firing_no: 1,
    });
    applyRecord(child, {
      event: "node_fired",
      seq: 3,
      frame: "f-0",
      node: "root_only",
      firing_no: 1,
    });
    finalizeFrameReplay(child, summary);

    expect(child.nodes.inner).toMatchObject({ firings: 1, active: false });
    expect(child.nodes.root_only).toBeUndefined();
    expect(child.state).toBe("passed");
    expect(child.state).not.toBe("incomplete");
  });
});

describe("matchesTraffic (wire/port → crossed messages, M5.5)", () => {
  it("filters by edge and by either port end", () => {
    const emit = root("message_emitted", "a", {
      from_port: "a.out",
      to_node: "b",
      to_port: "in",
      msg_id: "m-1",
      value: 42,
    });
    expect(matchesTraffic(emit, { kind: "edge", from: "a.out", to: "b.in" })).toBe(true);
    expect(matchesTraffic(emit, { kind: "edge", from: "a.out", to: "c.in" })).toBe(false);
    expect(matchesTraffic(emit, { kind: "port", node: "a", port: "out", side: "output" })).toBe(true);
    expect(matchesTraffic(emit, { kind: "port", node: "b", port: "in", side: "input" })).toBe(true);
    expect(matchesTraffic(emit, { kind: "port", node: "b", port: "in", side: "output" })).toBe(false);
  });

  it("ignores non-emissions and child-frame traffic (scope pin)", () => {
    const sel = { kind: "edge", from: "a.out", to: "b.in" } as const;
    expect(matchesTraffic(root("node_fired", "a", { firing_no: 1 }), sel)).toBe(false);
    expect(
      matchesTraffic(
        rec("message_emitted", { frame: "f-0/f-1", node: "a", from_port: "a.out", to_node: "b", to_port: "in" }),
        sel,
      ),
    ).toBe(false);
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
    expect(
      summarize(
        rec("frame_finished", {
          frame: "f-0/f-3",
          kind: "loop",
          flow: "flows/item",
          loop_index: 2,
          state: "passed",
          duration_ms: 8.6,
        }),
      ),
    ).toBe("loop flows/item #2 · passed · 9ms");
  });
});
