import { useEffect, useRef, useState } from "react";

import { fetchRunEventDetail } from "../api";
import {
  messageValue,
  preview,
  type PortTraffic,
  type RunView,
} from "../runview";
import { useAppStore } from "../store";
import DataModal, { type DataModalContent } from "./DataModal";

// S4/M5.5: the run inspector returns during run mode — the selected
// node's RUN data (firing count, request summary, per-port last
// values, log history) instead of the edit forms run mode locks away.
// F1: a floating card on the right; port rows open the data-peek
// modal with the full last value that crossed.

const OUTCOME_COLOR: Record<string, string> = {
  ok: "var(--ok)",
  failed: "var(--err)",
  error: "var(--err-bright)",
  skipped: "var(--muted)",
};

type Peek = ({ title: string } & DataModalContent) | null;

function showFull(value: unknown): string {
  if (value === undefined) return "(no value recorded)";
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return String(value);
  }
}

function PortRows({
  label,
  entries,
  nodeId,
  onPeek,
}: {
  label: string;
  entries: [string, PortTraffic][];
  nodeId: string;
  onPeek: (title: string, traffic: PortTraffic) => void;
}) {
  if (entries.length === 0) return null;
  return (
    <>
      <h4 className="nf-kicker" style={{ padding: "8px 0 2px" }}>
        {label}
      </h4>
      <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
        {entries.map(([key, traffic]) => {
          const portName = key.slice(key.indexOf(":") + 1);
          return (
            <li key={key}>
              <button
                data-testid="run-inspector-port"
                data-port-key={key}
                className="nf-row"
                title="click for the full payload"
                onClick={() => onPeek(`${nodeId} · ${portName}`, traffic)}
                style={{ padding: "3px 6px", gap: 6 }}
              >
                <code style={{ fontSize: 11 }}>{portName}</code>
                {traffic.count > 1 && (
                  <span style={{ color: "var(--accent)", fontSize: 10 }}>
                    ×{traffic.count}
                  </span>
                )}
                <span
                  style={{
                    flex: 1,
                    fontFamily: "var(--mono)",
                    fontSize: 11,
                    color: "var(--muted)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    textAlign: "right",
                  }}
                >
                  {preview(traffic.lastValue, 60)}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </>
  );
}

function RunSummary({
  view,
  flowName,
}: {
  view: RunView;
  flowName: string | null;
}) {
  return (
    <>
      <h3 style={{ margin: "0 0 0.5rem", fontSize: 14, fontWeight: 500 }}>
        {flowName ?? "—"} — run
      </h3>
      <p style={{ color: "var(--muted)", margin: "0 0 0.5rem" }}>
        {view.state}
        {view.durationMs !== null && ` in ${Math.round(view.durationMs)}ms`} ·
        asserts {view.asserts.passed}✓ {view.asserts.failed}✗
      </p>
      <p style={{ color: "var(--muted)" }}>
        Click a node for its run data; click a wire or port for the
        messages that crossed it.
      </p>
    </>
  );
}

export default function RunInspector() {
  const {
    detail,
    selectedNode,
    runView: rootRunView,
    runFramePath,
    runFrameDetail,
    runFrameView,
    runId,
    selectedFlow,
  } = useAppStore();
  const [peek, setPeek] = useState<Peek>(null);
  const requestGeneration = useRef(0);
  const inFrameDetail = runFramePath.length > 0;
  const activeDetail = inFrameDetail ? runFrameDetail : detail;
  const runView = inFrameDetail ? runFrameView : rootRunView;
  // per-node subscription keeps identity semantics with FlowNode
  const run = useAppStore((s) => {
    if (s.selectedNode === null) return null;
    const activeView =
      s.runFramePath.length > 0 ? s.runFrameView : s.runView;
    return activeView?.nodes[s.selectedNode] ?? null;
  });

  // Changing run, frame, or selected node retires any in-flight read and
  // releases a previously resolved large value. Closing the inspector on
  // unmount invalidates it as well.
  useEffect(() => {
    requestGeneration.current += 1;
    setPeek(null);
    return () => {
      requestGeneration.current += 1;
    };
  }, [runId, selectedFlow, runView?.scopeFrame, selectedNode]);

  const closePeek = () => {
    requestGeneration.current += 1;
    setPeek(null);
  };

  const openPeek = (title: string, traffic: PortTraffic) => {
    const generation = ++requestGeneration.current;
    const seq =
      typeof traffic.lastSeq === "number" &&
      Number.isSafeInteger(traffic.lastSeq) &&
      traffic.lastSeq > 0
        ? traffic.lastSeq
        : null;
    if (seq === null) {
      // Featureless/local records have no canonical address. Their already
      // folded value is the only available fallback.
      setPeek({ title, status: "value", json: showFull(traffic.lastValue) });
      return;
    }
    if (runId === null || selectedFlow === null) {
      setPeek({
        title,
        status: "error",
        message: "full value unavailable: run context is missing",
      });
      return;
    }

    setPeek({ title, status: "loading" });
    void fetchRunEventDetail(runId, selectedFlow, seq)
      .then((payload) => {
        if (
          payload.event.event !== "message_emitted" ||
          payload.event.seq !== seq
        ) {
          throw new Error("event locator did not resolve to its message");
        }
        if (generation === requestGeneration.current) {
          setPeek({
            title,
            status: "value",
            json: showFull(messageValue(payload.event)),
          });
        }
      })
      .catch((caught: unknown) => {
        if (generation === requestGeneration.current) {
          setPeek({
            title,
            status: "error",
            message: `full value unavailable: ${
              caught instanceof Error ? caught.message : String(caught)
            }`,
          });
        }
      });
  };

  if (runView === null) return null; // App only mounts this in run mode
  const node =
    activeDetail?.flow.nodes.find((n) => n.id === selectedNode) ?? null;
  const ports = Object.entries(run?.ports ?? {});

  return (
    <aside
      data-testid="run-inspector"
      className="nf-card"
      style={{
        position: "absolute",
        right: 14,
        top: 60,
        width: 300,
        maxHeight: "calc(100% - 130px)",
        padding: "0.75rem 1rem",
        overflowY: "auto",
        fontSize: 13,
        zIndex: 20,
        boxSizing: "border-box",
      }}
    >
      {node === null ? (
        <RunSummary
          view={runView}
          flowName={activeDetail?.flow.flow.name ?? null}
        />
      ) : (
        <>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
            <h3 style={{ margin: "0 0 0.25rem", fontSize: 14, fontWeight: 500 }}>
              {node.id}
            </h3>
            <span style={{ color: "var(--muted)", fontSize: 11 }}>
              {node.type}
            </span>
          </div>
          {run === null ? (
            <p
              data-testid="run-inspector-untouched"
              style={{ color: "var(--muted)" }}
            >
              no events for this node in this run
            </p>
          ) : (
            <>
              <p data-testid="run-inspector-status" style={{ margin: "0 0 0.5rem" }}>
                fired ×{run.firings} ·{" "}
                <span
                  style={{
                    color: run.active
                      ? "var(--accent)"
                      : (OUTCOME_COLOR[run.outcome] ?? "var(--muted)"),
                    fontWeight: 600,
                  }}
                >
                  {run.active ? "running" : run.outcome}
                </span>
                {run.guard && ` · guard ${run.guard}`}
              </p>
              {run.request && (
                <div
                  data-testid="run-inspector-request"
                  style={{
                    background: "var(--surface2)",
                    border: "1px solid var(--border)",
                    borderRadius: "var(--rsm)",
                    padding: "0.4rem 0.6rem",
                    fontFamily: "var(--mono)",
                    fontSize: 11,
                    wordBreak: "break-all",
                  }}
                >
                  <div>
                    {run.request.method} {run.request.url}
                  </div>
                  {run.request.status !== null && (
                    <div style={{ color: "var(--muted)" }}>
                      HTTP {run.request.status} · {run.request.sizeBytes}B
                      {run.request.totalMs !== null &&
                        ` · ${Math.round(run.request.totalMs)}ms`}
                    </div>
                  )}
                  {run.request.error !== null && (
                    <div style={{ color: "var(--err-bright)" }}>
                      {run.request.error} (attempt {run.request.attempt})
                    </div>
                  )}
                </div>
              )}
              <PortRows
                label="inputs"
                entries={ports.filter(([k]) => k.startsWith("in:"))}
                nodeId={node.id}
                onPeek={openPeek}
              />
              <PortRows
                label="outputs"
                entries={ports.filter(([k]) => k.startsWith("out:"))}
                nodeId={node.id}
                onPeek={openPeek}
              />
              {run.log && (
                <>
                  <h4 className="nf-kicker" style={{ padding: "8px 0 2px" }}>
                    log · {run.log.count}{" "}
                    {run.log.count > run.log.ring.length &&
                      `(last ${run.log.ring.length} kept)`}
                  </h4>
                  <ol
                    data-testid="run-inspector-log"
                    style={{
                      margin: 0,
                      paddingLeft: "1.4rem",
                      fontFamily: "var(--mono)",
                      fontSize: 11,
                    }}
                  >
                    {/* newest first — the tail is what you're chasing */}
                    {[...run.log.ring].reverse().map((value, i) => (
                      <li key={i} title={preview(value, 500)}>
                        {preview(value, 60)}
                      </li>
                    ))}
                  </ol>
                </>
              )}
            </>
          )}
        </>
      )}
      {peek !== null && (
        <DataModal
          title={peek.title}
          content={peek}
          color="var(--accent)"
          onClose={closePeek}
        />
      )}
    </aside>
  );
}
