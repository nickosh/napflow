export type SavePhase = "clean" | "dirty" | "saving" | "conflict" | "error";

export type SaveState = {
  phase: SavePhase;
  revision: number;
  persistedRevision: number;
  pending: boolean;
  error: unknown | null;
};

export type SaveContext<T> = {
  value: T;
  revision: number;
  latest: boolean;
};

type SaveCoordinatorOptions<T, R> = {
  debounceMs: number;
  initialEtag?: string | null;
  save: (value: T, baseEtag: string | null, force: boolean) => Promise<R>;
  etag: (result: R) => string | null;
  classifyError?: (error: unknown) => "conflict" | "error";
  onSaved?: (result: R, context: SaveContext<T>) => void;
};

type StateListener = (state: SaveState) => void;

/**
 * One revisioned, serialized save stream for one file.
 *
 * Edits coalesce while debounced. Once a request starts, later edits become the
 * next revision and use the ETag returned by the request ahead of them. No two
 * calls to `save` from this coordinator overlap.
 */
export class SaveCoordinator<T, R> {
  private readonly listeners = new Set<StateListener>();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private pumpPromise: Promise<void> | null = null;
  private latestValue: T | null = null;
  private revision = 0;
  private persistedRevision = 0;
  private generation = 0;
  private etag: string | null;
  private phase: SavePhase = "clean";
  private error: unknown | null = null;
  private forceNext = false;

  constructor(private readonly options: SaveCoordinatorOptions<T, R>) {
    this.etag = options.initialEtag ?? null;
  }

  get state(): SaveState {
    return {
      phase: this.phase,
      revision: this.revision,
      persistedRevision: this.persistedRevision,
      pending: this.hasPending(),
      error: this.error,
    };
  }

  hasPending(): boolean {
    return (
      this.phase !== "clean" ||
      this.revision > this.persistedRevision ||
      this.timer !== null ||
      this.pumpPromise !== null
    );
  }

  subscribe(listener: StateListener, emitCurrent = true): () => void {
    this.listeners.add(listener);
    if (emitCurrent) listener(this.state);
    return () => this.listeners.delete(listener);
  }

  /** Replace the current resource/base revision after a fresh read. */
  reset(etag: string | null): void {
    this.clearTimer();
    this.generation += 1;
    this.latestValue = null;
    this.revision = 0;
    this.persistedRevision = 0;
    this.etag = etag;
    this.phase = "clean";
    this.error = null;
    this.forceNext = false;
    this.emit();
  }

  /** Explicit user choice to throw away a blocked local revision. */
  discard(): void {
    this.clearTimer();
    this.generation += 1;
    this.latestValue = null;
    this.persistedRevision = this.revision;
    this.phase = "clean";
    this.error = null;
    this.forceNext = false;
    this.emit();
  }

  edit(value: T): void {
    this.latestValue = value;
    this.revision += 1;
    this.error = null;
    this.phase = this.pumpPromise === null ? "dirty" : "saving";
    this.emit();

    if (this.pumpPromise === null) this.schedule();
  }

  /** Save immediately and wait until every revision queued behind it settles. */
  async flush(): Promise<boolean> {
    this.clearTimer();
    while (true) {
      if (this.phase === "conflict" || this.phase === "error") return false;
      if (this.revision > this.persistedRevision) {
        await this.ensurePump();
        continue;
      }
      if (this.pumpPromise !== null) {
        await this.pumpPromise;
        continue;
      }
      return this.phase === "clean" && !this.hasPending();
    }
  }

  /** Last-write-wins conflict action, using the latest local revision. */
  async overwrite(): Promise<boolean> {
    this.clearTimer();
    if (this.revision <= this.persistedRevision || this.latestValue === null) {
      return true;
    }
    this.error = null;
    this.phase = "dirty";
    this.forceNext = true;
    this.emit();
    await this.ensurePump();
    const settled = this.state;
    return settled.phase === "clean" && !settled.pending;
  }

  private schedule(): void {
    this.clearTimer();
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.flush();
    }, this.options.debounceMs);
  }

  private clearTimer(): void {
    if (this.timer === null) return;
    clearTimeout(this.timer);
    this.timer = null;
  }

  private ensurePump(): Promise<void> {
    if (this.pumpPromise !== null) return this.pumpPromise;
    const running = this.runPump();
    this.pumpPromise = running;
    void running.finally(() => {
      if (this.pumpPromise === running) this.pumpPromise = null;
      this.emit();
      // reset/discard can invalidate an in-flight generation. If a new edit
      // arrived while that old request was settling, start its generation now
      // without overlapping the old request.
      if (
        this.pumpPromise === null &&
        this.revision > this.persistedRevision &&
        this.latestValue !== null &&
        this.phase !== "conflict" &&
        this.phase !== "error"
      ) {
        void this.ensurePump();
      }
    });
    return running;
  }

  private async runPump(): Promise<void> {
    const generation = this.generation;
    while (
      generation === this.generation &&
      this.revision > this.persistedRevision &&
      this.latestValue !== null
    ) {
      const value = this.latestValue;
      const revision = this.revision;
      const force = this.forceNext;
      this.forceNext = false;
      this.phase = "saving";
      this.error = null;
      this.emit();

      let result: R;
      try {
        result = await this.options.save(value, this.etag, force);
      } catch (error) {
        if (generation !== this.generation) return;
        this.error = error;
        this.phase = this.options.classifyError?.(error) ?? "error";
        this.emit();
        return;
      }
      if (generation !== this.generation) return;

      this.etag = this.options.etag(result);
      this.persistedRevision = revision;
      const latest = this.revision === revision;
      this.options.onSaved?.(result, { value, revision, latest });
      this.phase = latest ? "clean" : "dirty";
      this.emit();
    }
  }

  private emit(): void {
    const state = this.state;
    for (const listener of this.listeners) listener(state);
  }
}

type PendingListener = (pending: boolean) => void;

/** The app-wide lifecycle boundary for currently mounted file editors. */
export class PersistenceRegistry {
  private readonly coordinators = new Set<SaveCoordinator<unknown, unknown>>();
  private readonly unsubscribers = new Map<
    SaveCoordinator<unknown, unknown>,
    () => void
  >();
  private readonly listeners = new Set<PendingListener>();

  register<T, R>(coordinator: SaveCoordinator<T, R>): () => void {
    const item = coordinator as SaveCoordinator<unknown, unknown>;
    this.coordinators.add(item);
    this.unsubscribers.set(item, item.subscribe(() => this.emit(), false));
    this.emit();
    return () => {
      this.unsubscribers.get(item)?.();
      this.unsubscribers.delete(item);
      this.coordinators.delete(item);
      this.emit();
    };
  }

  get pending(): boolean {
    return [...this.coordinators].some((coordinator) => coordinator.hasPending());
  }

  async flushAll(): Promise<boolean> {
    while (true) {
      const results = await Promise.all(
        [...this.coordinators].map((coordinator) => coordinator.flush()),
      );
      if (!results.every(Boolean)) return false;
      // A coordinator that was initially clean can become dirty while a
      // different coordinator's request is in flight. Re-snapshot until the
      // whole registry is quiescent in the same turn.
      if (!this.pending) return true;
    }
  }

  subscribe(listener: PendingListener): () => void {
    this.listeners.add(listener);
    listener(this.pending);
    return () => this.listeners.delete(listener);
  }

  private emit(): void {
    const pending = this.pending;
    for (const listener of this.listeners) listener(pending);
  }
}

export const persistenceRegistry = new PersistenceRegistry();
