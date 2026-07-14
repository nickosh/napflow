import { describe, expect, it } from "vitest";

import { parseDefault } from "./PortEditor";

describe("typed Start defaults", () => {
  it("parses native values according to the declared port type", () => {
    expect(parseDefault("42", "number")).toEqual({ ok: true, value: 42 });
    expect(parseDefault("false", "boolean")).toEqual({
      ok: true,
      value: false,
    });
    expect(parseDefault('{"page": 1}', "object")).toEqual({
      ok: true,
      value: { page: 1 },
    });
    expect(parseDefault('["a"]', "list")).toEqual({
      ok: true,
      value: ["a"],
    });
  });

  it("preserves templates for post-render coercion on every typed port", () => {
    for (const type of ["number", "boolean", "object", "list"]) {
      expect(parseDefault("{{ env.VALUE }}", type)).toEqual({
        ok: true,
        value: "{{ env.VALUE }}",
      });
    }
  });

  it("still rejects plain values that do not fit the declared type", () => {
    expect(parseDefault("not-a-number", "number")).toEqual({ ok: false });
    expect(parseDefault("yes", "boolean")).toEqual({ ok: false });
    expect(parseDefault("[]", "object")).toEqual({ ok: false });
    expect(parseDefault("{}", "list")).toEqual({ ok: false });
  });

  it("does not treat templates as runtime values in the run-input parser", () => {
    expect(parseDefault("{{ env.VALUE }}", "number", false)).toEqual({
      ok: false,
    });
  });
});
