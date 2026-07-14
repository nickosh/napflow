import { Suspense, lazy, useEffect, useMemo, useState } from "react";

import {
  ConflictError,
  fetchCode,
  putCode,
  type SavedCode,
  type SyntaxError_,
} from "../api";
import {
  persistenceRegistry,
  SaveCoordinator,
  type SavePhase,
} from "../persistence";

// Whole-file nodes.py editor (owner fork 2026-07-06) over PUT
// /api/code/* with etag concurrency. Debounced autosave like the
// canvas; a syntax error is reported inline but SAVES anyway (the
// server is last-write-wins — code is never held hostage).
//
// The editing surface is CodeMirror 6, bundled into the wheel's static
// assets (no CDN) and lazy-loaded so the canvas doesn't pay for it.
const CodePane = lazy(() => import("./CodeMirrorPane"));

const AUTOSAVE_MS = 1000;

export default function CodeEditor({
  identity,
  onClose,
}: {
  identity: string;
  onClose: () => void;
}) {
  const [code, setCode] = useState<string | null>(null);
  const [syntaxError, setSyntaxError] = useState<SyntaxError_ | null>(null);
  const [state, setState] = useState<SavePhase>("clean");
  const [saveError, setSaveError] = useState<string | null>(null);
  const coordinator = useMemo(
    () =>
      new SaveCoordinator<string, SavedCode>({
        debounceMs: AUTOSAVE_MS,
        save: (value, baseEtag, force) =>
          putCode(identity, value, baseEtag, force),
        etag: (saved) => saved.etag,
        classifyError: (error) =>
          error instanceof ConflictError ? "conflict" : "error",
        onSaved: (saved, { latest }) => {
          if (latest) setSyntaxError(saved.syntax_error);
        },
      }),
    [identity],
  );

  useEffect(
    () =>
      coordinator.subscribe(({ phase, error }) => {
        setState(phase);
        setSaveError(
          phase === "error"
            ? error instanceof Error
              ? error.message
              : String(error)
            : null,
        );
      }),
    [coordinator],
  );

  useEffect(() => persistenceRegistry.register(coordinator), [coordinator]);

  useEffect(() => {
    let current = true;
    setCode(null);
    void fetchCode(identity).then((file) => {
      if (!current) return;
      coordinator.reset(file.etag);
      setCode(file.code);
      setSyntaxError(file.syntax_error);
    });
    return () => {
      current = false;
    };
  }, [coordinator, identity]);

  const onEdit = (value: string) => {
    setCode(value);
    coordinator.edit(value);
  };

  const reload = async () => {
    const revision = coordinator.state.revision;
    const file = await fetchCode(identity);
    if (coordinator.state.revision !== revision) return;
    coordinator.reset(file.etag);
    setCode(file.code);
    setSyntaxError(file.syntax_error);
  };

  const close = async () => {
    if (await coordinator.flush()) onClose();
  };

  return (
    <div
      data-testid="code-editor"
      style={{
        position: "fixed",
        inset: "6vh 8vw",
        background: "#fff",
        border: "1px solid #999",
        borderRadius: 6,
        boxShadow: "0 8px 30px rgba(0,0,0,0.3)",
        display: "flex",
        flexDirection: "column",
        zIndex: 20,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: 10,
          padding: "0.5rem 1rem",
          borderBottom: "1px solid #ddd",
        }}
      >
        <strong style={{ fontSize: 13 }}>{identity}/nodes.py</strong>
        {state === "conflict" ? (
          <span data-testid="code-conflict" style={{ fontSize: 12 }}>
            <span style={{ color: "#c62828", marginRight: 8 }}>
              file changed on disk
            </span>
            <button
              data-testid="code-conflict-reload"
              onClick={() => void reload()}
              style={{ marginRight: 4, cursor: "pointer", fontFamily: "inherit" }}
            >
              reload
            </button>
            <button
              data-testid="code-conflict-overwrite"
              onClick={() => void coordinator.overwrite()}
              style={{ cursor: "pointer", fontFamily: "inherit" }}
            >
              overwrite
            </button>
          </span>
        ) : state === "error" ? (
          <span data-testid="code-save-error" style={{ fontSize: 12, color: "#c62828" }}>
            save failed: {saveError ?? "unknown error"}
          </span>
        ) : (
          <span
            data-testid="code-save-status"
            data-state={state}
            style={{ fontSize: 12, color: "#888" }}
          >
            {state === "clean" ? "saved" : state === "saving" ? "saving…" : "…"}
          </span>
        )}
        {syntaxError && (
          <span data-testid="code-syntax-error" style={{ fontSize: 12, color: "#c62828" }}>
            line {syntaxError.line}: {syntaxError.message}
          </span>
        )}
        <span style={{ flex: 1 }} />
        <button
          data-testid="code-close"
          onClick={() => void close()}
          style={{ cursor: "pointer", fontFamily: "inherit" }}
        >
          close
        </button>
      </div>
      {code === null ? (
        <p style={{ padding: "1rem", color: "#888", fontSize: 13 }}>loading…</p>
      ) : (
        <div data-testid="code-text" style={{ flex: 1, minHeight: 0 }}>
          <Suspense
            fallback={
              <p style={{ padding: "1rem", color: "#888", fontSize: 13 }}>
                loading…
              </p>
            }
          >
            <CodePane value={code} onChange={onEdit} />
          </Suspense>
        </div>
      )}
    </div>
  );
}
