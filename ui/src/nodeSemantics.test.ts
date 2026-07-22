import { describe, expect, it } from "vitest";

import type { FlowModel } from "./api";
import {
  canAddNodeType,
  isFrameStartSource,
  missingBoundaryTypes,
} from "./nodeSemantics";

function flow(types: string[], edges: FlowModel["edges"] = []): FlowModel {
  return {
    flow: { name: "semantics" },
    nodes: types.map((type, index) => ({ id: `${type}${index}`, type })),
    edges,
  };
}

describe("boundary authoring semantics", () => {
  it.each([
    { types: ["start", "end"], missing: [] },
    { types: ["end"], missing: ["start"] },
    { types: ["start"], missing: ["end"] },
    { types: [], missing: ["start", "end"] },
  ])("offers only missing boundaries for $types", ({ types, missing }) => {
    expect(missingBoundaryTypes(flow(types))).toEqual(missing);
  });

  it("rejects duplicate boundary additions without restricting ordinary nodes", () => {
    const current = flow(["start", "end", "log"]);
    expect(canAddNodeType(current, "start")).toBe(false);
    expect(canAddNodeType(current, "end")).toBe(false);
    expect(canAddNodeType(current, "log")).toBe(true);
  });
});

describe("frame-start source semantics", () => {
  it("always marks Start and marks a fixture only while trigger is unconnected", () => {
    const start = { id: "entry", type: "start" };
    const fixture = { id: "users", type: "fixture" };
    expect(isFrameStartSource(flow([]), start)).toBe(true);
    expect(isFrameStartSource(flow([]), fixture)).toBe(true);
    expect(
      isFrameStartSource(
        flow([], [{ from: "entry.out", to: "users.trigger" }]),
        fixture,
      ),
    ).toBe(false);
  });
});
