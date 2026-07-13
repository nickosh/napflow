import { afterEach, describe, expect, it, vi } from "vitest";

import {
  abortRun,
  fetchRunEventDetail,
  fetchRunEventPage,
  fetchRunFramePage,
  REPLAY_API_FORMAT,
  requireReplayV1,
} from "./api";

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

function envelope(overrides: Record<string, unknown> = {}) {
  return {
    api_format: REPLAY_API_FORMAT,
    run_id: "run-1",
    run_format: "napflow-run/1",
    features: ["content-blobs/1"],
    root_frame: "f-0",
    history_state: "complete",
    run_summary: {
      state: "passed",
      duration_ms: 1,
      asserts: { passed: 0, failed: 0 },
      unhandled_error_count: 0,
      nodes_never_fired_count: 0,
    },
    ...overrides,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("versioned replay API", () => {
  it("rejects missing and unexpected API format markers", () => {
    expect(() => requireReplayV1({ events: [] })).toThrow(
      "unsupported replay API format: missing",
    );
    expect(() =>
      requireReplayV1({ api_format: "napflow-replay/2" }),
    ).toThrow("napflow-replay/2");
  });

  it("requests bounded cursor pages and runtime-gates frame responses", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(
        envelope({
          parent_frame: "f-0",
          after_seq: 10,
          next_after_seq: 20,
          has_more: false,
          frames: [],
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchRunFramePage("run #1", "flows/item list", {
      parentFrame: "f-0/f-2",
      afterSeq: 10,
      limit: 20,
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/runs/run%20%231/frames?flow=flows%2Fitem+list&parent_frame=f-0%2Ff-2&after_seq=10&limit=20",
    );
  });

  it("does not resolve descriptors during paging; detail fetch is explicit", async () => {
    const descriptor = {
      $napflow: {
        kind: "blob",
        hash: `sha256:${"a".repeat(64)}`,
        bytes: 70_000,
        media_type: "text/plain; charset=utf-8",
        codec: "utf-8",
      },
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.includes("/events/7?")) {
        return jsonResponse(
          envelope({ event: { event: "log", seq: 7, value: "resolved" } }),
        );
      }
      return jsonResponse(
        envelope({
          frame: "f-0",
          after_seq: 0,
          next_after_seq: 7,
          has_more: false,
          events: [{ event: "log", seq: 7, value: descriptor }],
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const page = await fetchRunEventPage("run-1", "flows/smoke", {
      afterSeq: 0,
      frame: "f-0",
      limit: 50,
    });
    expect(page.events[0].value).toEqual(descriptor);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const detail = await fetchRunEventDetail("run-1", "flows/smoke", 7);
    expect(detail.event.value).toBe("resolved");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][0]).toBe(
      "/api/runs/run-1/events/7?flow=flows%2Fsmoke",
    );
  });

  it("surfaces missing/corrupt detail errors instead of returning a descriptor", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ message: "content blob is missing or corrupt" }, 422),
      ),
    );

    await expect(
      fetchRunEventDetail("run-1", "flows/smoke", 9),
    ).rejects.toThrow("content blob is missing or corrupt");
  });
});

describe("abort API", () => {
  it("accepts the server's 202 aborting response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ run_id: "run-1", state: "aborting" }, 202),
      ),
    );

    await expect(abortRun("run #1")).resolves.toEqual({
      run_id: "run-1",
      state: "aborting",
    });
    expect(fetch).toHaveBeenCalledWith("/api/runs/run%20%231/abort", {
      method: "POST",
    });
  });

  it("surfaces a non-success response instead of pretending abort worked", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ message: "no run 'gone'" }, 404)),
    );

    await expect(abortRun("gone")).rejects.toMatchObject({
      message: "no run 'gone'",
      status: 404,
    });
  });
});
