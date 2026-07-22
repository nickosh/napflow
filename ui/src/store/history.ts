/** Bounded, in-memory history for the currently open flow document.
 *
 * Entries are immutable FlowModel roots. The canvas actions already path-copy
 * only what they change, so retaining those roots gives snapshot semantics
 * with structural sharing and without cloning or serializing the document.
 */

export const DOCUMENT_HISTORY_LIMIT = 100;
export const DOCUMENT_HISTORY_COALESCE_MS = 750;

export type HistoryStatus = {
  canUndo: boolean;
  canRedo: boolean;
};

type HistoryShortcut = Pick<
  KeyboardEvent,
  "key" | "ctrlKey" | "metaKey" | "shiftKey" | "altKey"
>;

export function documentHistoryShortcut(
  event: HistoryShortcut,
): "undo" | "redo" | null {
  if (event.altKey) return null;
  const key = event.key.toLowerCase();
  const primary = event.metaKey || event.ctrlKey;
  if (primary && key === "z") {
    return event.shiftKey ? "redo" : "undo";
  }
  if (event.ctrlKey && !event.metaKey && !event.shiftKey && key === "y") {
    return "redo";
  }
  return null;
}

type HistoryOptions<T> = {
  limit?: number;
  coalesceMs?: number;
  equal?: (left: T, right: T) => boolean;
  now?: () => number;
};

type ActiveGroup = {
  key: string;
  touchedAt: number;
};

export class DocumentHistory<T> {
  private readonly limit: number;
  private readonly coalesceMs: number;
  private readonly equal: (left: T, right: T) => boolean;
  private readonly now: () => number;
  private readonly past: T[] = [];
  private readonly future: T[] = [];
  private activeGroup: ActiveGroup | null = null;

  constructor(options: HistoryOptions<T> = {}) {
    this.limit = options.limit ?? DOCUMENT_HISTORY_LIMIT;
    this.coalesceMs =
      options.coalesceMs ?? DOCUMENT_HISTORY_COALESCE_MS;
    this.equal = options.equal ?? Object.is;
    this.now = options.now ?? Date.now;
  }

  get status(): HistoryStatus {
    return {
      canUndo: this.past.length > 0,
      canRedo: this.future.length > 0,
    };
  }

  same(left: T, right: T): boolean {
    return this.equal(left, right);
  }

  /** Record one accepted document edit. Returns false for a semantic no-op. */
  record(before: T, after: T, groupKey?: string): boolean {
    if (this.equal(before, after)) return false;

    const touchedAt = this.now();
    const elapsed =
      this.activeGroup === null ? -1 : touchedAt - this.activeGroup.touchedAt;
    const coalesces =
      groupKey !== undefined &&
      this.activeGroup?.key === groupKey &&
      elapsed >= 0 &&
      elapsed <= this.coalesceMs;

    this.future.length = 0;
    if (!coalesces) {
      this.past.push(before);
      this.trim(this.past);
    }

    if (
      coalesces &&
      this.past.length > 0 &&
      this.equal(this.past[this.past.length - 1], after)
    ) {
      // Native text undo can return a focused, controlled input to the
      // beginning of its canvas history group. Do not leave an empty step.
      this.past.pop();
      this.activeGroup = null;
    } else {
      this.activeGroup =
        groupKey === undefined ? null : { key: groupKey, touchedAt };
    }
    return true;
  }

  undo(current: T): T | null {
    const previous = this.past.pop();
    if (previous === undefined) return null;
    this.future.push(current);
    this.trim(this.future);
    this.activeGroup = null;
    return previous;
  }

  redo(current: T): T | null {
    const next = this.future.pop();
    if (next === undefined) return null;
    this.past.push(current);
    this.trim(this.past);
    this.activeGroup = null;
    return next;
  }

  endGroup(): void {
    this.activeGroup = null;
  }

  reset(): HistoryStatus {
    this.past.length = 0;
    this.future.length = 0;
    this.activeGroup = null;
    return this.status;
  }

  private trim(stack: T[]): void {
    if (stack.length > this.limit) {
      stack.splice(0, stack.length - this.limit);
    }
  }
}

/** FlowModel values are JSON-compatible, but history never serializes them. */
export function jsonValueEqual(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (
    left === null ||
    right === null ||
    typeof left !== "object" ||
    typeof right !== "object"
  ) {
    return false;
  }
  if (Array.isArray(left) || Array.isArray(right)) {
    if (!Array.isArray(left) || !Array.isArray(right)) return false;
    return (
      left.length === right.length &&
      left.every((value, index) => jsonValueEqual(value, right[index]))
    );
  }

  const leftRecord = left as Record<string, unknown>;
  const rightRecord = right as Record<string, unknown>;
  const leftKeys = Object.keys(leftRecord);
  const rightKeys = Object.keys(rightRecord);
  return (
    leftKeys.length === rightKeys.length &&
    leftKeys.every(
      (key) =>
        Object.prototype.hasOwnProperty.call(rightRecord, key) &&
        jsonValueEqual(leftRecord[key], rightRecord[key]),
    )
  );
}
