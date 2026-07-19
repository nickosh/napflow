import {
  ApiError,
  ConflictError,
  fetchEtags,
  fetchFlowDetail,
  fetchFlows,
  fetchWorkspace,
  putFlow,
  type FlowModel,
  type SavedFlow,
  type WorkspaceInfo,
} from "../api";
import { flowPath, identityFromPath } from "../identity";
import {
  persistenceRegistry,
  SaveCoordinator,
} from "../persistence";
import { joinOperatorNotices } from "./notices";
import type {
  EditFlow,
  PersistenceSlice,
  RunResetPatch,
  StoreGet,
  StoreSet,
  DetailError,
} from "./types";

export const AUTOSAVE_MS = 1000; // owner fork (2026-07-06): debounced autosave
export const ETAG_POLL_MS = 2000; // FR-1004 v1 shape: poll, reload/prompt on drift

function workspaceEnvNotice(workspace: WorkspaceInfo): string | null {
  return joinOperatorNotices(
    workspace.env_profile_warnings.map(
      (warning) =>
        `warning: env profile ${JSON.stringify(warning.name)} skipped at ${warning.path}: ${warning.message}`,
    ),
  );
}

type FlowSaveValue = { identity: string; flow: FlowModel };

export function createPersistenceSlice(
  set: StoreSet,
  get: StoreGet,
  resetRunForFlow: () => RunResetPatch,
): { slice: PersistenceSlice; editFlow: EditFlow } {
  let navigationGeneration = 0;
  let detailGeneration = 0;
  let activeHistoryIndex = 0;
  let restoringHistory = false;
  const flowSaves = new SaveCoordinator<FlowSaveValue, SavedFlow>({
    debounceMs: AUTOSAVE_MS,
    save: ({ identity, flow }, baseEtag, force) =>
      putFlow(identity, flow, baseEtag, force),
    etag: (saved) => saved.etag,
    classifyError: (error) =>
      error instanceof ConflictError ? "conflict" : "error",
    onSaved: (saved, { value, latest }) => {
      const current = get().detail;
      if (current === null || current.identity !== value.identity) return;
      set({
        detail: {
          ...current,
          etag: saved.etag,
          diagnostics: saved.diagnostics,
        },
      });
      if (latest) {
        // Port surfaces and Python function lists are server-derived. Refetch
        // only after the latest queued revision has reached disk.
        queueMicrotask(() => void refreshDetail(value.identity));
      }
    },
  });
  persistenceRegistry.register(flowSaves);
  flowSaves.subscribe(({ phase, error }) => {
    if (phase === "error") {
      set({
        saveState: phase,
        saveError: error instanceof Error ? error.message : String(error),
        saveDiagnostics: error instanceof ApiError ? error.diagnostics : [],
      });
    } else {
      set({ saveState: phase, saveError: null, saveDiagnostics: [] });
    }
  }, false);

  /** Replace detail.flow (immutably at the top level — React re-renders
   * off object identity), then queue the newest revision for autosave. */
  const editFlow: EditFlow = (mutate, opts) => {
    const { detail, runView } = get();
    if (detail === null) return;
    if (runView !== null) return; // run mode locks editing (owner fork)
    detailGeneration += 1;
    const flow = mutate(detail.flow);
    set({
      detail: { ...detail, flow },
      saveError: null,
      saveDiagnostics: [],
      ...(opts?.rebuild ? { graphVersion: get().graphVersion + 1 } : {}),
    });
    flowSaves.edit({ identity: detail.identity, flow });
  };

  /** Re-pull detail from the server without disturbing local edits:
   * applied only while clean and not mid-drag. */
  async function refreshDetail(identity: string) {
    const navigation = navigationGeneration;
    const detailVersion = detailGeneration;
    const expectedEtag = get().detail?.etag ?? null;
    try {
      const fresh = await fetchFlowDetail(identity);
      const s = get();
      if (
        navigation === navigationGeneration &&
        detailVersion === detailGeneration &&
        s.selectedFlow === identity &&
        s.detail?.identity === identity &&
        s.detail.etag === expectedEtag &&
        s.saveState === "clean" &&
        !s.interacting
      ) {
        detailGeneration += 1;
        flowSaves.reset(fresh.etag);
        set({ detail: fresh, graphVersion: s.graphVersion + 1 });
      }
    } catch {
      // transient — the poll or next save will surface real trouble
    }
  }

  return {
    editFlow,
    slice: {
      workspace: null,
      workspaceNotice: null,
      flows: [],
      error: null,
      selectedFlow: null,
      detailError: null,
      saveState: "clean",
      saveError: null,
      saveDiagnostics: [],

      load: async () => {
        try {
          const [workspace, flows] = await Promise.all([
            fetchWorkspace(),
            fetchFlows(),
          ]);
          set({
            workspace,
            workspaceNotice: workspaceEnvNotice(workspace),
            flows,
            error: null,
            runEnv: workspace.env_default,
          });
          // deep link wins (SPA fallback serves index for /flow/<identity>);
          // otherwise the manifest's `main:` flow opens by default (WM)
          const fromPath = identityFromPath(window.location.pathname);
          const initial =
            fromPath ??
            (flows.some((f) => f.identity === workspace.main)
              ? workspace.main
              : (flows[0]?.identity ?? null));
          if (initial !== null) {
            const stateIndex = window.history.state?.napflowIndex;
            activeHistoryIndex = Number.isInteger(stateIndex) ? stateIndex : 0;
            window.history.replaceState(
              {
                ...(window.history.state ?? {}),
                napflowIndex: activeHistoryIndex,
              },
              "",
              flowPath(initial),
            );
            await get().openFlow(initial, { push: false });
          }
        } catch (e) {
          set({ error: e instanceof Error ? e.message : String(e) });
        }
      },

      refreshFlows: async () => {
        // sidebar-only refetch (a clone just landed a new folder)
        try {
          set({ flows: await fetchFlows() });
        } catch {
          // transient — the list simply stays as it was
        }
      },

      openFlow: async (identity, opts) => {
        const navigation = ++navigationGeneration;
        if (!(await persistenceRegistry.flushAll())) {
          return false;
        }
        if (navigation !== navigationGeneration) return false;

        const runReset = resetRunForFlow();
        detailGeneration += 1;
        flowSaves.reset(null);
        set({
          selectedFlow: identity,
          detail: null,
          detailError: null,
          selectedNode: null,
          saveError: null,
          saveDiagnostics: [],
          ...runReset,
        });
        if (
          opts?.push !== false &&
          identityFromPath(window.location.pathname) !== identity
        ) {
          activeHistoryIndex += 1;
          window.history.pushState(
            {
              ...(window.history.state ?? {}),
              napflowIndex: activeHistoryIndex,
            },
            "",
            flowPath(identity),
          );
        }
        try {
          const detail = await fetchFlowDetail(identity);
          // A slow response, including one for the same identity opened twice,
          // must not clobber the latest navigation generation.
          if (
            navigation === navigationGeneration &&
            get().selectedFlow === identity
          ) {
            flowSaves.reset(detail.etag);
            set({
              detail,
              detailError: null,
              graphVersion: get().graphVersion + 1,
            });
            return true;
          }
          return false;
        } catch (e) {
          if (
            navigation !== navigationGeneration ||
            get().selectedFlow !== identity
          ) {
            return false;
          }
          const detailError: DetailError =
            e instanceof ApiError
              ? { message: e.message, diagnostics: e.diagnostics }
              : {
                  message: e instanceof Error ? e.message : String(e),
                  diagnostics: [],
                };
          set({ detail: null, detailError });
          // The save barrier accepted this navigation. A missing or temporarily
          // invalid target is still the current history entry and should render
          // its detail error, not be mistaken for a blocked persistence flush.
          return true;
        }
      },

      popFlow: async (identity, historyIndex) => {
        if (restoringHistory) {
          restoringHistory = false;
          return;
        }
        const targetIndex =
          historyIndex !== null && Number.isInteger(historyIndex)
            ? historyIndex
            : activeHistoryIndex;
        const accepted =
          identity !== null &&
          (await get().openFlow(identity, { push: false }));
        if (accepted) {
          activeHistoryIndex = targetIndex;
          return;
        }
        const delta = activeHistoryIndex - targetIndex;
        if (delta !== 0) {
          restoringHistory = true;
          window.history.go(delta);
        }
      },

      resolveConflict: async (how) => {
        const { detail } = get();
        if (detail === null) return;
        if (how === "overwrite") {
          await flowSaves.overwrite(); // last-write-wins (FR-1004 ceiling)
        } else {
          flowSaves.discard();
          await get().openFlow(detail.identity, { push: false });
        }
      },

      pollEtags: async () => {
        const s = get();
        if (s.detail === null || s.saveState !== "clean" || s.interacting) {
          return; // dirty edits conflict via the PUT's 409, not the poll
        }
        const identity = s.detail.identity;
        try {
          const etags = await fetchEtags(identity);
          const current = get().detail;
          if (
            current !== null &&
            current.identity === identity &&
            get().saveState === "clean" &&
            (etags.etag !== current.etag ||
              etags.code_etag !== current.code_etag)
          ) {
            // external edit while we're clean ⇒ live-reload (autosave
            // preference: frictionless beats prompts when nothing is lost)
            await refreshDetail(identity);
          }
        } catch {
          // flow may have been deleted externally — the next open will 404
        }
      },
    },
  };
}
