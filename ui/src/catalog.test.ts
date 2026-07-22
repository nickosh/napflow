import { describe, expect, it } from "vitest";

import { NODE_META, PICKER_TABS, nodeMeta } from "./catalog";
import { CONFIG_FORMS, NODE_TYPES } from "./forms";

// Structural catalog guards only. Consumer tests separately prove that the
// picker, cards, and forms read this data; bounded execution/boundary semantics
// intentionally remain explicit and are not claims about a future plugin API.
describe("node catalog registry coverage", () => {
  it("gives every catalog type an explicit registry entry (fallback is for future/unknown types only)", () => {
    const catalog = ["start", "end", ...NODE_TYPES].sort();
    expect(Object.keys(NODE_META).sort()).toEqual(catalog);
  });

  it("keeps generic presentation metadata well formed", () => {
    for (const [type, meta] of Object.entries(NODE_META)) {
      expect(meta.description, type).not.toBe("");
      expect(PICKER_TABS).toContain(meta.category);
      expect(meta.width, type).toBeGreaterThan(0);
    }
  });

  it("points quick-config keys at real form descriptors", () => {
    for (const type of NODE_TYPES) {
      const keys = new Set(
        (CONFIG_FORMS[type] ?? []).map((field) => field.key),
      );
      for (const quick of NODE_META[type].quick) {
        expect(keys.has(quick), `${type}.${quick}`).toBe(true);
      }
    }
    // start/end edit through the dedicated port editors, never quick rows
    expect(NODE_META.start.quick).toEqual([]);
    expect(NODE_META.end.quick).toEqual([]);
  });

  it("falls back for unknown types without throwing (future blocks land here)", () => {
    const meta = nodeMeta("not-a-type");
    expect(meta.width).toBeGreaterThan(0);
    expect(meta.quick).toEqual([]);
  });
});
