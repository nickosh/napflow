import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  abortRun: vi.fn(),
  fetchEtags: vi.fn(),
  fetchFlows: vi.fn(),
  fetchRunEventPage: vi.fn(),
  fetchRunFramePage: vi.fn(),
  fetchFlowDetail: vi.fn(),
  fetchWorkspace: vi.fn(),
  openRunSocket: vi.fn(),
  putFlow: vi.fn(),
  startRun: vi.fn(),
}));

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    abortRun: apiMocks.abortRun,
    fetchEtags: apiMocks.fetchEtags,
    fetchFlows: apiMocks.fetchFlows,
    fetchRunEventPage: apiMocks.fetchRunEventPage,
    fetchRunFramePage: apiMocks.fetchRunFramePage,
    fetchFlowDetail: apiMocks.fetchFlowDetail,
    fetchWorkspace: apiMocks.fetchWorkspace,
    openRunSocket: apiMocks.openRunSocket,
    putFlow: apiMocks.putFlow,
    startRun: apiMocks.startRun,
  };
});

import {
  REPLAY_API_FORMAT,
  type Diagnostic,
  type FlowDetail,
  type RunEventPage,
  type RunFramePage,
  type WorkspaceInfo,
} from "./api";
import {
  type NodeRunState,
  type RunFrameSummary,
} from "./runview";
import { persistenceRegistry } from "./persistence";
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

function workspace(overrides: Partial<WorkspaceInfo> = {}): WorkspaceInfo {
  return {
    name: "test workspace",
    description: null,
    root: "/workspace",
    flows_root: "flows",
    environments_root: ".",
    data_root: "data",
    main: FLOW,
    env_profiles: [],
    env_profile_warnings: [],
    env_default: null,
    version: "0.2.0",
    ...overrides,
  };
}

function fakeSocket(): WebSocket {
  return {
    close: vi.fn(),
    onmessage: null,
    onclose: null,
    onerror: null,
  } as unknown as WebSocket;
}

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
    workspace: null,
    workspaceNotice: null,
    flows: [],
    error: null,
    selectedFlow: FLOW,
    detail: detail(),
    detailError: null,
    runHistory: [{ run_id: RUN_ID, state: "passed" }],
    runView: null,
    runId: null,
    runLive: false,
    runSource: null,
    runEnv: null,
    runNotice: null,
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
  apiMocks.fetchEtags.mockResolvedValue({
    identity: FLOW,
    etag: "etag",
    code_etag: null,
  });
  apiMocks.fetchFlows.mockResolvedValue([{ identity: FLOW, valid: true }]);
  apiMocks.fetchWorkspace.mockResolvedValue(workspace());
  apiMocks.openRunSocket.mockReturnValue(fakeSocket());
  apiMocks.putFlow.mockImplementation(async (identity: string) => ({
    identity,
    etag: "etag-saved",
    diagnostics: [],
  }));
  apiMocks.startRun.mockResolvedValue({
    run_id: RUN_ID,
    flow: FLOW,
    state: "running",
    log: `.napflow/runs/${FLOW}/${RUN_ID}.jsonl`,
    warnings: [],
    notes: [],
  });
  apiMocks.abortRun.mockResolvedValue({ run_id: RUN_ID, state: "aborting" });
});

afterEach(() => {
  useAppStore.getState().exitRun();
  vi.unstubAllGlobals();
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

describe("canvas persistence history boundary", () => {
  it("retains the accepted flow root across save refetch before the next edit", async () => {
    const opened = detail("flows/history");
    opened.flow = {
      flow: { name: "history" },
      nodes: [
        { id: "start", type: "start", config: { ports: [] } },
        { id: "end", type: "end", config: { ports: [] } },
      ],
      edges: [],
      layout: { start: [0, 0], end: [200, 0] },
    };
    apiMocks.fetchFlowDetail.mockResolvedValueOnce(opened);
    await useAppStore.getState().openFlow(opened.identity, { push: false });

    useAppStore.getState().moveNode("start", 20, 10);
    const firstEditedRoot = useAppStore.getState().detail!.flow;
    const sharedNodes = firstEditedRoot.nodes;
    apiMocks.fetchFlowDetail.mockResolvedValueOnce({
      ...opened,
      flow: structuredClone(firstEditedRoot),
      etag: "etag-saved",
      functions: ["server_derived"],
    });

    await persistenceRegistry.flushAll();
    await vi.waitFor(() => {
      expect(useAppStore.getState().detail?.functions).toEqual([
        "server_derived",
      ]);
    });
    expect(useAppStore.getState().detail?.flow).toBe(firstEditedRoot);

    apiMocks.fetchEtags.mockResolvedValueOnce({
      identity: opened.identity,
      etag: "etag-saved",
      code_etag: "code-after",
    });
    apiMocks.fetchFlowDetail.mockResolvedValueOnce({
      ...useAppStore.getState().detail!,
      flow: structuredClone(firstEditedRoot),
      code_etag: "code-after",
      functions: ["code_changed"],
    });
    await useAppStore.getState().pollEtags();
    expect(useAppStore.getState().detail?.flow).toBe(firstEditedRoot);
    expect(useAppStore.getState().detail?.functions).toEqual(["code_changed"]);
    expect(useAppStore.getState().canUndo).toBe(true);

    useAppStore.getState().moveNode("end", 220, 10);
    expect(useAppStore.getState().detail?.flow.nodes).toBe(sharedNodes);
    useAppStore.getState().undo();
    expect(useAppStore.getState().detail?.flow).toBe(firstEditedRoot);

    apiMocks.fetchFlowDetail.mockResolvedValue(detail(FLOW));
    await useAppStore.getState().openFlow(FLOW, { push: false });
  });
});

describe("run transport", () => {
  it("surfaces workspace dotenv discovery warnings after initial flow load", async () => {
    vi.stubGlobal("window", {
      location: { pathname: `/flow/${FLOW}` },
      history: {
        state: null,
        replaceState: vi.fn(),
        pushState: vi.fn(),
      },
    });
    apiMocks.fetchWorkspace.mockResolvedValueOnce(
      workspace({
        env_profile_warnings: [
          {
            name: "broken.env",
            path: "/workspace/broken.env",
            message: "expected KEY=VALUE",
          },
        ],
      }),
    );
    apiMocks.fetchFlowDetail.mockResolvedValueOnce(detail());

    await useAppStore.getState().load();

    expect(useAppStore.getState().workspaceNotice).toBe(
      'warning: env profile "broken.env" skipped at /workspace/broken.env: expected KEY=VALUE',
    );
    expect(useAppStore.getState().runNotice).toBeNull();
  });

  it("surfaces successful run preparation warnings and operator notes", async () => {
    const warning: Diagnostic = {
      severity: "warning",
      code: "W103",
      message: "error output is unconnected",
      hint: "connect it",
      file: "flows/large/flow.yaml",
      line: 4,
      column: 3,
      node: "request",
    };
    apiMocks.startRun.mockResolvedValueOnce({
      run_id: RUN_ID,
      flow: FLOW,
      state: "running",
      log: `.napflow/runs/${FLOW}/${RUN_ID}.jsonl`,
      warnings: [warning],
      notes: ['warning: env profile "broken.env" was skipped'],
    });

    await useAppStore.getState().startRun({});

    expect(useAppStore.getState().runNotice).toBe(
      'warning: W103: error output is unconnected · warning: env profile "broken.env" was skipped',
    );
  });

  it("surfaces a failed abort response instead of treating it as a race", async () => {
    useAppStore.setState({
      runId: RUN_ID,
      runLive: true,
      runNotice: null,
    });
    apiMocks.abortRun.mockRejectedValueOnce(new Error("no run 'gone'"));

    await useAppStore.getState().abortRun();

    expect(apiMocks.abortRun).toHaveBeenCalledWith(RUN_ID);
    expect(useAppStore.getState().runNotice).toBe("no run 'gone'");
  });
});
