import { describe, expect, it } from "vitest";

import type { FlowDetail } from "./api";
import { PORT_TYPE_COLORS } from "./colors";
import { freshNodeId, toGraph } from "./graph";

function detail(overrides: Partial<FlowDetail> = {}): FlowDetail {
  // shaped like flows/smoke from `napf init` (fixture→python→assert)
  return {
    identity: "flows/smoke",
    flow: {
      flow: { name: "smoke" },
      nodes: [
        { id: "start", type: "start" },
        { id: "users", type: "fixture", config: { file: "f.json" } },
        {
          id: "summarize",
          type: "python",
          config: { function: "summarize", outputs: ["summary"] },
        },
        { id: "end", type: "end", config: { ports: [{ name: "summary" }] } },
      ],
      edges: [
        { from: "users.value", to: "summarize.users" },
        { from: "summarize.summary", to: "end.summary" },
      ],
      layout: { start: [40, 40], users: [40, 160] },
    },
    diagnostics: [],
    etag: "abc123",
    code_etag: "def456",
    functions: ["summarize"],
    ports: {
      start: { inputs: {}, outputs: { out: "object" }, required_inputs: [], growable: false },
      users: { inputs: {}, outputs: { value: "list" }, required_inputs: [], growable: false },
      summarize: {
        inputs: { users: "any" },
        outputs: { summary: "any", error: "object" },
        required_inputs: ["users"],
        growable: false,
      },
      end: { inputs: { summary: "any" }, outputs: {}, required_inputs: ["summary"], growable: false },
    },
    ...overrides,
  };
}

describe("toGraph", () => {
  it("uses layout coordinates when present, fallback columns otherwise", () => {
    const { nodes } = toGraph(detail());
    const byId = new Map(nodes.map((n) => [n.id, n]));
    expect(byId.get("start")!.position).toEqual({ x: 40, y: 40 });
    expect(byId.get("users")!.position).toEqual({ x: 40, y: 160 });
    // summarize/end are not in layout: — BFS columns place them
    // (users is a root → depth 0; summarize depth 1; end depth 2)
    expect(byId.get("summarize")!.position.x).toBeGreaterThan(
      byId.get("end")!.position.x - 1000,
    );
    expect(byId.get("summarize")!.position.x).not.toBe(
      byId.get("end")!.position.x,
    );
  });

  it("builds handles from port surfaces with required markers", () => {
    const { nodes } = toGraph(detail());
    const summarize = nodes.find((n) => n.id === "summarize")!;
    expect(summarize.data.inputs).toEqual([
      { name: "users", type: "any", required: true },
    ]);
    expect(summarize.data.outputs.map((p) => p.name)).toEqual([
      "summary",
      "error",
    ]);
  });

  it("maps edges to source/target handles with type-colored strokes", () => {
    const { edges } = toGraph(detail());
    expect(edges[0]).toMatchObject({
      source: "users",
      sourceHandle: "value",
      target: "summarize",
      targetHandle: "users",
    });
    // users.value is a list output → list color on the wire (D11)
    expect(edges[0].style?.stroke).toBe(PORT_TYPE_COLORS.list);
  });

  it("grows undeclared-but-wired handles (merge inputs, null surfaces)", () => {
    const d = detail({
      flow: {
        flow: { name: "m" },
        nodes: [
          { id: "a", type: "start" },
          { id: "join", type: "merge", config: { mode: "any" } },
          { id: "broken", type: "flow", config: { flow: "flows/nope" } },
        ],
        edges: [
          { from: "a.out", to: "join.in1" },
          { from: "a.out", to: "broken.trigger" },
        ],
        layout: null,
      },
      ports: {
        a: { inputs: {}, outputs: { out: "object" }, required_inputs: [], growable: false },
        join: { inputs: {}, outputs: { out: "any" }, required_inputs: [], growable: true },
        broken: null, // unresolvable reference — surface unknowable
      },
    });
    const { nodes } = toGraph(d);
    const join = nodes.find((n) => n.id === "join")!;
    expect(join.data.inputs.map((p) => p.name)).toEqual(["in1"]);
    const broken = nodes.find((n) => n.id === "broken")!;
    expect(broken.data.inputs).toEqual([
      { name: "trigger", type: "any", required: false },
    ]);
  });

  it("edge ids carry the model refs so deletion maps back", () => {
    const { edges } = toGraph(detail());
    expect(edges[0].id).toBe("users.value→summarize.users");
    expect(edges[0].data).toEqual({
      from: "users.value",
      to: "summarize.users",
    });
  });

  it("attaches diagnostic counts to their nodes", () => {
    const d = detail({
      diagnostics: [
        {
          severity: "warning",
          code: "W104",
          message: "unreachable",
          hint: "wire it",
          file: "flows/smoke/flow.yaml",
          line: 3,
          column: 1,
          node: "summarize",
        },
        {
          severity: "error",
          code: "E005",
          message: "missing input",
          hint: "wire it",
          file: "flows/smoke/flow.yaml",
          line: 9,
          column: 1,
          node: "summarize",
        },
      ],
    });
    const { nodes } = toGraph(d);
    const summarize = nodes.find((n) => n.id === "summarize")!;
    expect(summarize.data.warnings).toBe(1);
    expect(summarize.data.errors).toBe(1);
    expect(nodes.find((n) => n.id === "users")!.data.warnings).toBe(0);
  });
});

describe("freshNodeId", () => {
  it("uses the bare type when free, then numbers from 2 (E011-safe)", () => {
    const flow = detail().flow;
    expect(freshNodeId(flow, "request")).toBe("request");
    expect(freshNodeId(flow, "start")).toBe("start2");
    flow.nodes.push({ id: "start2", type: "start" });
    expect(freshNodeId(flow, "start")).toBe("start3");
  });
});
