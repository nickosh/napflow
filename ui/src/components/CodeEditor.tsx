import { Suspense, lazy, useCallback, useEffect, useRef, useState } from "react";

import {
  ConflictError,
  fetchCode,
  putCode,
  type SyntaxError_,
} from "../api";

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
  const [state, setState] = useState<"clean" | "dirty" | "saving" | "conflict">(
    "clean",
  );
  const etag = useRef<string | null>(null);
  const latest = useRef<string>("");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    void fetchCode(identity).then((file) => {
      etag.current = file.etag;
      latest.current = file.code;
      setCode(file.code);
      setSyntaxError(file.syntax_error);
    });
    return () => {
      if (timer.current !== null) clearTimeout(timer.current);
    };
  }, [identity]);

  const save = useCallback(
    async (force = false) => {
      setState("saving");
      try {
        const saved = await putCode(identity, latest.current, etag.current, force);
        etag.current = saved.etag;
        setSyntaxError(saved.syntax_error);
        setState("clean");
      } catch (e) {
        if (e instanceof ConflictError) {
          setState("conflict");
        } else {
          setState("dirty"); // transient — retry on next edit
        }
      }
    },
    [identity],
  );

  const onEdit = (value: string) => {
    latest.current = value;
    setCode(value);
    setState("dirty");
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = setTimeout(() => void save(), AUTOSAVE_MS);
  };

  const reload = async () => {
    const file = await fetchCode(identity);
    etag.current = file.etag;
    latest.current = file.code;
    setCode(file.code);
    setSyntaxError(file.syntax_error);
    setState("clean");
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
              onClick={() => void save(true)}
              style={{ cursor: "pointer", fontFamily: "inherit" }}
            >
              overwrite
            </button>
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
          onClick={onClose}
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
