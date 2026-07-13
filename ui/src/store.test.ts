import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  fetchRunEventPage: vi.fn(),
  fetchRunFramePage: vi.fn(),
  fetchFlowDetail: vi.fn(),
}));

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    fetchRunEventPage: apiMocks.fetchRunEventPage,
    fetchRunFramePage: apiMocks.fetchRunFramePage,
    fetchFlowDetail: apiMocks.fetchFlowDetail,
  };
});

import {
  REPLAY_API_FORMAT,
  type FlowDetail,
  type RunEventPage,
  type RunFramePage,
} from "./api";
import {
  type NodeRunState,
  type RunFrameSummary,
} from "./runview";
import { useAppStore } from "./store";

const RUN_ID = "20260713-000000-abcdef";
const FLOW = "flows/large";
const SUMMARY = {
  state: "passed" as const,
  duration_ms: 42,
  asserts: { passed: 2, failed: 0 },
  unhandled_error_count: 0,
  nodes_never_fired_count: 0,
};

function detail(identity = FLOW): FlowDetail {
  return {
    identity,
    flow: {
      flow: { name: identity },
      nodes: [],
      edges: [],
    },
    diagnostics: [],
    etag: "etag",
    code_etag: null,
    functions: null,
    ports: {},
    template_refs: {},
    used_by: [],
  };
}

function projectedNode(firings: number, lastSeq: number): NodeRunState {
  return {
    firings,
    active: false,
    outcome: "ok",
    guard: null,
    lastSeq,
    log: null,
    ports: {},
    request: null,
  };
}

function rootEvents(first: number, last: number) {
  return Array.from({ length: last - first + 1 }, (_, offset) => {
    const seq = first + offset;
    return seq === 1
      ? { event: "run_started", seq }
      : {
          event: "node_fired",
          seq,
          frame: "f-0",
          node: "work",
          firing_no: seq - 1,
        };
  });
}

function eventPage(afterSeq: number, frame = "f-0"): RunEventPage {
  const first = afterSeq + 1;
  const last = afterSeq + 500;
  return {
    api_format: REPLAY_API_FORMAT,
    run_id: RUN_ID,
    run_format: "napflow-run/1",
    features: ["content-blobs/1"],
    root_frame: "f-0",
    history_state: "complete",
    run_summary: SUMMARY,
    view_summary: {
      scope_frame: frame,
      record_count: 1_101,
      nodes: { work: projectedNode(1_099, 1_100) },
      edges: {},
      asserts: { passed: 2, failed: 0 },
      started_ts: "2026-07-13T00:00:00.000Z",
    },
    frame,
    after_seq: afterSeq,
    next_after_seq: last,
    has_more: last < 1_101,
    events: rootEvents(first, last),
  };
}

const CHILD: RunFrameSummary = {
  event: "frame_finished",
  seq: 1_100,
  frame: "f-0/f-1",
  parent_frame: "f-0",
  parent_node: "items",
  flow: "flows/item",
  kind: "loop",
  loop_index: 0,
  duration_ms: 10,
  state: "passed",
  asserts: { passed: 1, failed: 0 },
  unhandled_error_count: 0,
  end_output_names: ["result"],
};

function framePage(parentFrame: string): RunFramePage {
  return {
    api_format: REPLAY_API_FORMAT,
    run_id: RUN_ID,
    run_format: "napflow-run/1",
    features: ["content-blobs/1"],
    root_frame: "f-0",
    history_state: "complete",
    run_summary: SUMMARY,
    parent_frame: parentFrame,
    after_seq: 0,
    next_after_seq: parentFrame === "f-0" ? CHILD.seq : 0,
    has_more: false,
    frames: parentFrame === "f-0" ? [CHILD] : [],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  useAppStore.setState({
    selectedFlow: FLOW,
    detail: detail(),
    detailError: null,
    runHistory: [{ run_id: RUN_ID, state: "passed" }],
    runView: null,
    runId: null,
    runLive: false,
    runSource: null,
    runFramePath: [],
    runFrameDetail: null,
    runFrameView: null,
    runEventPage: null,
    runEventPageLoading: false,
  });
  apiMocks.fetchRunEventPage.mockImplementation(
    async (_runId: string, _flow: string, options: { afterSeq: number; frame: string }) =>
      eventPage(options.afterSeq, options.frame),
  );
  apiMocks.fetchRunFramePage.mockImplementation(
    async (
      _runId: string,
      _flow: string,
      options: { parentFrame: string },
    ) => framePage(options.parentFrame),
  );
  apiMocks.fetchFlowDetail.mockResolvedValue(detail("flows/item"));
});

describe("bounded history store", () => {
  it("opens one event/frame page, hydrates the full projection, and advances explicitly", async () => {
    await useAppStore.getState().openHistoryRun(RUN_ID);

    expect(apiMocks.fetchRunEventPage).toHaveBeenCalledTimes(1);
    expect(apiMocks.fetchRunFramePage).toHaveBeenCalledTimes(1);
    expect(apiMocks.fetchRunEventPage.mock.invocationCallOrder[0]).toBeLessThan(
      apiMocks.fetchRunFramePage.mock.invocationCallOrder[0],
    );
    expect(apiMocks.fetchRunEventPage.mock.calls[0][2]).toMatchObject({
      afterSeq: 0,
      frame: "f-0",
    });
    let state = useAppStore.getState();
    expect(state.runEventPage).toHaveLength(500);
    expect(state.runView?.records).toHaveLength(500);
    expect(state.runView?.recordCount).toBe(1_101);
    expect(state.runView?.nodes.work.firings).toBe(1_099);
    expect(state.runView?.state).toBe("passed");
    expect(state.runFrameChildren.map((frame) => frame.frame)).toEqual([
      CHILD.frame,
    ]);

    await state.pageRunEvents("next");
    expect(apiMocks.fetchRunEventPage).toHaveBeenCalledTimes(2);
    expect(apiMocks.fetchRunEventPage.mock.calls[1][2]).toMatchObject({
      afterSeq: 500,
      frame: "f-0",
    });
    state = useAppStore.getState();
    expect(state.runEventPage?.[0].seq).toBe(501);
    expect(state.runEventPage).toHaveLength(500);
    expect(state.runView?.recordCount).toBe(1_101);
    expect(state.runView?.nodes.work.firings).toBe(1_099);

    await state.pageRunEvents("first");
    expect(apiMocks.fetchRunEventPage).toHaveBeenCalledTimes(3);
    expect(apiMocks.fetchRunEventPage.mock.calls[2][2]).toMatchObject({
      afterSeq: 0,
      frame: "f-0",
    });
    state = useAppStore.getState();
    expect(state.runEventPage?.[0].seq).toBe(1);
    expect(state.runView?.recordCount).toBe(1_101);
    expect(state.runView?.nodes.work.firings).toBe(1_099);
  });

  it("loads one child event/frame page and its run-only flow detail", async () => {
    await useAppStore.getState().openHistoryRun(RUN_ID);
    await useAppStore.getState().openRunFrame(CHILD);

    expect(apiMocks.fetchRunEventPage).toHaveBeenCalledTimes(2);
    expect(apiMocks.fetchRunFramePage).toHaveBeenCalledTimes(2);
    expect(apiMocks.fetchRunEventPage.mock.calls[1][2]).toMatchObject({
      afterSeq: 0,
      frame: CHILD.frame,
    });
    expect(apiMocks.fetchFlowDetail).toHaveBeenCalledWith("flows/item");
    const state = useAppStore.getState();
    expect(state.runFramePath.map((frame) => frame.frame)).toEqual([
      CHILD.frame,
    ]);
    expect(state.runFrameDetail?.identity).toBe("flows/item");
    expect(state.runFrameView?.records).toHaveLength(500);
    expect(state.runFrameView?.recordCount).toBe(1_101);
    expect(state.runFrameView?.state).toBe("passed");
  });

  it("keeps durable frame events usable when current flow source is gone", async () => {
    apiMocks.fetchFlowDetail.mockRejectedValueOnce(new Error("flow not found"));
    await useAppStore.getState().openHistoryRun(RUN_ID);
    await useAppStore.getState().openRunFrame(CHILD);

    const state = useAppStore.getState();
    expect(state.runFrameDetail).toBeNull();
    expect(state.runFrameView?.records).toHaveLength(500);
    expect(state.runFrameView?.state).toBe("passed");
    expect(state.runFrameChildren).toEqual([]);
    expect(state.runFrameError).toContain("durable frame events remain inspectable");
    expect(state.runFrameError).toContain("flow not found");
  });
});
