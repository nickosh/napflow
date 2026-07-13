import { describe, expect, it } from "vitest";

import coverage from "./form-coverage.json";
import {
  CONFIG_FORMS,
  DEDICATED_FORM_COVERAGE,
  NODE_FIELD_COVERAGE,
  NODE_TYPES,
  parseTemplatableBoolean,
  parseTemplatableNumber,
} from "./forms";

describe("flow schema to visual form coverage", () => {
  it("maps every node config field to a form kind or dedicated port editor", () => {
    const catalog = ["start", "end", ...NODE_TYPES].sort();
    expect(Object.keys(coverage.configs).sort()).toEqual(catalog);

    for (const [nodeType, expected] of Object.entries(coverage.configs)) {
      if (nodeType === "start" || nodeType === "end") {
        expect(CONFIG_FORMS[nodeType]).toBeUndefined();
        expect(expected).toEqual({
          ports: nodeType === "start" ? "start-ports" : "end-ports",
        });
        continue;
      }
      const actual = Object.fromEntries(
        CONFIG_FORMS[nodeType].map((field) => [field.key, field.kind]),
      );
      expect(actual).toEqual(expected);
    }
  });

  it("pins universal and nested fields to their dedicated editor paths", () => {
    expect(NODE_FIELD_COVERAGE).toEqual(coverage.node);
    expect(DEDICATED_FORM_COVERAGE).toEqual(coverage.nested);
  });
});

describe("templatable typed form values", () => {
  it("keeps native numbers native and template source as text", () => {
    expect(parseTemplatableNumber("2.5")).toBe(2.5);
    expect(parseTemplatableNumber(" 3 ")).toBe(3);
    expect(parseTemplatableNumber("{{ env.TIMEOUT }}")).toBe(
      "{{ env.TIMEOUT }}",
    );
    expect(parseTemplatableNumber("")).toBeUndefined();
  });

  it("keeps native booleans native and template source as text", () => {
    expect(parseTemplatableBoolean("true")).toBe(true);
    expect(parseTemplatableBoolean("false")).toBe(false);
    expect(parseTemplatableBoolean("{{ env.VERIFY_TLS }}")).toBe(
      "{{ env.VERIFY_TLS }}",
    );
    expect(parseTemplatableBoolean("")).toBeUndefined();
  });
});
