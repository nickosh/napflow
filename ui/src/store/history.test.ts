import { describe, expect, it } from "vitest";

import {
  DOCUMENT_HISTORY_LIMIT,
  DocumentHistory,
  documentHistoryShortcut,
  jsonValueEqual,
} from "./history";

type Document = {
  value: number;
  shared?: { label: string };
};

function history(now: () => number = () => 0) {
  return new DocumentHistory<Document>({ equal: jsonValueEqual, now });
}

describe("DocumentHistory", () => {
  it("maps macOS and Windows/Linux shortcuts without Alt leakage", () => {
    const shortcut = (
      key: string,
      overrides: Partial<{
        ctrlKey: boolean;
        metaKey: boolean;
        shiftKey: boolean;
        altKey: boolean;
      }> = {},
    ) =>
      documentHistoryShortcut({
        key,
        ctrlKey: false,
        metaKey: false,
        shiftKey: false,
        altKey: false,
        ...overrides,
      });

    expect(shortcut("z", { metaKey: true })).toBe("undo");
    expect(shortcut("Z", { metaKey: true, shiftKey: true })).toBe("redo");
    expect(shortcut("z", { ctrlKey: true })).toBe("undo");
    expect(shortcut("z", { ctrlKey: true, shiftKey: true })).toBe("redo");
    expect(shortcut("y", { ctrlKey: true })).toBe("redo");
    expect(shortcut("y", { metaKey: true })).toBeNull();
    expect(shortcut("z", { ctrlKey: true, altKey: true })).toBeNull();
  });

  it("undoes and redoes immutable roots without cloning shared values", () => {
    const temporal = history();
    const shared = { label: "same reference" };
    const first = { value: 1, shared };
    const second = { value: 2, shared };

    expect(temporal.record(first, second)).toBe(true);
    expect(temporal.status).toEqual({ canUndo: true, canRedo: false });

    const undone = temporal.undo(second);
    expect(undone).toBe(first);
    expect(undone?.shared).toBe(shared);
    expect(temporal.status).toEqual({ canUndo: false, canRedo: true });

    expect(temporal.redo(first)).toBe(second);
    expect(temporal.status).toEqual({ canUndo: true, canRedo: false });
  });

  it("caps the oldest history while retaining the newest 100 steps", () => {
    const temporal = history();
    let current: Document = { value: 0 };
    for (let value = 1; value <= DOCUMENT_HISTORY_LIMIT + 1; value += 1) {
      const next = { value };
      temporal.record(current, next);
      current = next;
    }

    for (let count = 0; count < DOCUMENT_HISTORY_LIMIT; count += 1) {
      current = temporal.undo(current)!;
    }
    expect(current.value).toBe(1);
    expect(temporal.undo(current)).toBeNull();
  });

  it("coalesces one keyed typing burst but not another field or idle burst", () => {
    let time = 0;
    const temporal = history(() => time);
    const zero = { value: 0 };
    const one = { value: 1 };
    const two = { value: 2 };
    const three = { value: 3 };
    const four = { value: 4 };

    temporal.record(zero, one, "node:label");
    time += 100;
    temporal.record(one, two, "node:label");
    time += 100;
    temporal.record(two, three, "node:url");
    time += 1_000;
    temporal.record(three, four, "node:url");

    expect(temporal.undo(four)).toBe(three);
    expect(temporal.undo(three)).toBe(two);
    expect(temporal.undo(two)).toBe(zero);
  });

  it("drops a typing group that returns to its baseline", () => {
    let time = 0;
    const temporal = history(() => time);
    const baseline = { value: 0 };
    const typed = { value: 1 };
    const nativeUndo = { value: 0 };

    temporal.record(baseline, typed, "node:label");
    time += 10;
    temporal.record(typed, nativeUndo, "node:label");

    expect(temporal.status).toEqual({ canUndo: false, canRedo: false });
    expect(temporal.undo(nativeUndo)).toBeNull();
  });

  it("invalidates redo on a branch and resets both stacks", () => {
    const temporal = history();
    const zero = { value: 0 };
    const one = { value: 1 };
    const branch = { value: 9 };

    temporal.record(zero, one);
    expect(temporal.undo(one)).toBe(zero);
    temporal.record(zero, branch);
    expect(temporal.status).toEqual({ canUndo: true, canRedo: false });
    expect(temporal.redo(branch)).toBeNull();

    expect(temporal.reset()).toEqual({ canUndo: false, canRedo: false });
  });

  it("ignores structurally equal replacement roots", () => {
    const temporal = history();
    expect(temporal.record({ value: 1 }, { value: 1 })).toBe(false);
    expect(temporal.status).toEqual({ canUndo: false, canRedo: false });
  });
});
