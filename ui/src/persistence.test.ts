import { afterEach, describe, expect, it, vi } from "vitest";

import { PersistenceRegistry, SaveCoordinator } from "./persistence";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((ok, fail) => {
    resolve = ok;
    reject = fail;
  });
  return { promise, resolve, reject };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("SaveCoordinator", () => {
  it("debounces edits and persists only the latest snapshot", async () => {
    vi.useFakeTimers();
    const save = vi.fn(async (value: string) => ({ etag: `e-${value}` }));
    const coordinator = new SaveCoordinator({
      debounceMs: 100,
      initialEtag: "e0",
      save,
      etag: (result) => result.etag,
    });

    coordinator.edit("one");
    coordinator.edit("two");
    await vi.advanceTimersByTimeAsync(100);

    expect(save).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenCalledWith("two", "e0", false);
    expect(coordinator.state.phase).toBe("clean");
  });

  it("queues edits during a save and chains the returned ETag", async () => {
    const first = deferred<{ etag: string }>();
    const calls: Array<{
      value: string;
      baseEtag: string | null;
      force: boolean;
    }> = [];
    let active = 0;
    let maximumActive = 0;
    const coordinator = new SaveCoordinator<string, { etag: string }>({
      debounceMs: 10_000,
      initialEtag: "e0",
      save: async (value, baseEtag, force) => {
        calls.push({ value, baseEtag, force });
        active += 1;
        maximumActive = Math.max(maximumActive, active);
        const result = calls.length === 1 ? await first.promise : { etag: "e2" };
        active -= 1;
        return result;
      },
      etag: (result) => result.etag,
    });

    coordinator.edit("one");
    const flushing = coordinator.flush();
    await Promise.resolve();
    coordinator.edit("two");
    first.resolve({ etag: "e1" });

    expect(await flushing).toBe(true);
    expect(calls).toEqual([
      { value: "one", baseEtag: "e0", force: false },
      { value: "two", baseEtag: "e1", force: false },
    ]);
    expect(maximumActive).toBe(1);
  });

  it("flushes immediately and waits for the in-flight request", async () => {
    vi.useFakeTimers();
    const pending = deferred<{ etag: string }>();
    const save = vi.fn(() => pending.promise);
    const coordinator = new SaveCoordinator({
      debounceMs: 1_000,
      save,
      etag: (result) => result.etag,
    });

    coordinator.edit("now");
    const flushing = coordinator.flush();
    expect(save).toHaveBeenCalledTimes(1);
    expect(coordinator.state.phase).toBe("saving");
    pending.resolve({ etag: "e1" });

    expect(await flushing).toBe(true);
    expect(coordinator.state.phase).toBe("clean");
  });

  it("blocks on conflict and force-saves the latest local revision", async () => {
    class Conflict extends Error {}
    let attempt = 0;
    const calls: Array<{ value: string; force: boolean }> = [];
    const coordinator = new SaveCoordinator<string, { etag: string }>({
      debounceMs: 1_000,
      initialEtag: "old",
      save: async (value, _baseEtag, force) => {
        calls.push({ value, force });
        attempt += 1;
        if (attempt === 1) throw new Conflict();
        return { etag: "new" };
      },
      etag: (result) => result.etag,
      classifyError: (error) => (error instanceof Conflict ? "conflict" : "error"),
    });

    coordinator.edit("first");
    expect(await coordinator.flush()).toBe(false);
    expect(coordinator.state.phase).toBe("conflict");
    coordinator.edit("latest");
    expect(await coordinator.overwrite()).toBe(true);

    expect(calls).toEqual([
      { value: "first", force: false },
      { value: "latest", force: true },
    ]);
  });

  it("ignores a stale response after the resource is reset", async () => {
    const pending = deferred<{ etag: string }>();
    const saved = vi.fn();
    const coordinator = new SaveCoordinator({
      debounceMs: 1_000,
      save: () => pending.promise,
      etag: (result) => result.etag,
      onSaved: saved,
    });

    coordinator.edit("old resource");
    const flushing = coordinator.flush();
    coordinator.reset("fresh-etag");
    pending.resolve({ etag: "stale-etag" });

    expect(await flushing).toBe(true);
    expect(saved).not.toHaveBeenCalled();
    expect(coordinator.state.phase).toBe("clean");
  });

  it("queues a new-generation edit behind a stale in-flight request", async () => {
    const old = deferred<{ etag: string }>();
    const calls: string[] = [];
    const coordinator = new SaveCoordinator<string, { etag: string }>({
      debounceMs: 1_000,
      save: async (value) => {
        calls.push(value);
        return value === "old" ? old.promise : { etag: "fresh-etag" };
      },
      etag: (result) => result.etag,
    });

    coordinator.edit("old");
    const oldFlush = coordinator.flush();
    coordinator.reset("new-base");
    coordinator.edit("new");
    const newFlush = coordinator.flush();
    old.resolve({ etag: "stale-etag" });

    expect(await oldFlush).toBe(true);
    expect(await newFlush).toBe(true);
    expect(calls).toEqual(["old", "new"]);
    expect(coordinator.state.phase).toBe("clean");
  });
});

describe("PersistenceRegistry", () => {
  it("tracks and flushes all registered file coordinators", async () => {
    const registry = new PersistenceRegistry();
    const pendingStates: boolean[] = [];
    registry.subscribe((pending) => pendingStates.push(pending));
    const make = () =>
      new SaveCoordinator<string, { etag: string }>({
        debounceMs: 1_000,
        save: async (value) => ({ etag: value }),
        etag: (result) => result.etag,
      });
    const flow = make();
    const code = make();
    const unregisterFlow = registry.register(flow);
    const unregisterCode = registry.register(code);

    flow.edit("flow");
    code.edit("code");
    expect(registry.pending).toBe(true);
    expect(await registry.flushAll()).toBe(true);
    expect(registry.pending).toBe(false);
    expect(pendingStates).toContain(true);

    unregisterFlow();
    unregisterCode();
  });

  it("re-snapshots when a clean coordinator is edited during another flush", async () => {
    const registry = new PersistenceRegistry();
    const delayed = deferred<{ etag: string }>();
    const flowSave = vi.fn(async (value: string) => ({ etag: value }));
    const flow = new SaveCoordinator<string, { etag: string }>({
      debounceMs: 1_000,
      save: flowSave,
      etag: (result) => result.etag,
    });
    const code = new SaveCoordinator<string, { etag: string }>({
      debounceMs: 1_000,
      save: () => delayed.promise,
      etag: (result) => result.etag,
    });
    registry.register(flow);
    registry.register(code);

    code.edit("slow");
    const flushing = registry.flushAll();
    await Promise.resolve();
    flow.edit("late");
    delayed.resolve({ etag: "code-etag" });

    expect(await flushing).toBe(true);
    expect(flowSave).toHaveBeenCalledWith("late", null, false);
    expect(registry.pending).toBe(false);
  });
});
