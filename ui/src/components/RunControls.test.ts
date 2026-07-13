import { describe, expect, it } from "vitest";

import { collectRunInputs, type StartPort } from "./RunControls";

describe("run input default/override distinction", () => {
  const ports: StartPort[] = [
    { name: "text", type: "string", default: "configured" },
    { name: "count", type: "number", default: "{{ env.COUNT }}" },
    { name: "required", type: "string" },
  ];

  it("omits untouched defaults so the engine evaluates them at BIND", () => {
    expect(
      collectRunInputs(
        ports,
        { text: "configured", count: "{{ env.COUNT }}", required: "" },
        new Set(),
      ),
    ).toEqual({ inputs: {}, invalid: new Set() });
  });

  it("preserves an edited empty string instead of silently reusing default", () => {
    expect(
      collectRunInputs(
        ports,
        { text: "", count: "{{ env.COUNT }}", required: "" },
        new Set(["text"]),
      ),
    ).toEqual({ inputs: { text: "" }, invalid: new Set() });
  });

  it("still rejects an edited blank for a typed numeric input", () => {
    expect(
      collectRunInputs(
        ports,
        { text: "configured", count: "", required: "" },
        new Set(["count"]),
      ),
    ).toEqual({ inputs: {}, invalid: new Set(["count"]) });
  });
});
