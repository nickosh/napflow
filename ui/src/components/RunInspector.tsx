import { preview, type PortTraffic, type RunView } from "../runview";
import { useAppStore } from "../store";

// S4/M5.5: the right panel returns during run mode — the selected
// node's RUN data (firing count, request summary, per-port last
// values, log history) instead of the edit forms run mode locks away.

const OUTCOME_COLOR: Record<string, string> = {
  ok: "#2e7d32",
  failed: "#c62828",
  error: "#b71c1c",
  skipped: "#888",
};

function PortRows({
  label,
  entries,
}: {
  label: string;
  entries: [string, PortTraffic][];
}) {
  if (entries.length === 0) return null;
  return (
    <>
      <h4 style={{ margin: "0.75rem 0 0.25rem", fontSize: 11, color: "#888" }}>
        {label}
      </h4>
      <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
        {entries.map(([key, traffic]) => (
          <li
            key={key}
            data-testid="run-inspector-port"
            style={{ display: "flex", gap: 6, alignItems: "baseline" }}
          >
            <code style={{ fontSize: 11 }}>{key.slice(key.indexOf(":") + 1)}</code>
            {traffic.count > 1 && (
              <span style={{ color: "#1565c0", fontSize: 10 }}>
                ×{traffic.count}
              </span>
            )}
            <span
              title={preview(traffic.lastValue, 500)}
              style={{
                fontFamily: "ui-monospace, monospace",
                fontSize: 11,
                color: "#333",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {preview(traffic.lastValue, 60)}
            </span>
          </li>
        ))}
      </ul>
    </>
  );
}

function RunSummary({ view }: { view: RunView }) {
  const { detail } = useAppStore();
  return (
    <>
      <h3 style={{ margin: "0 0 0.5rem", fontSize: 14 }}>
        {detail?.flow.flow.name ?? "—"} — run
      </h3>
      <p style={{ color: "#666", margin: "0 0 0.5rem" }}>
        {view.state}
        {view.durationMs !== null && ` in ${Math.round(view.durationMs)}ms`} ·
        asserts {view.asserts.passed}✓ {view.asserts.failed}✗
      </p>
      <p style={{ color: "#888" }}>
        Click a node for its run data; click a wire or port for the
        messages that crossed it.
      </p>
    </>
  );
}

export default function RunInspector() {
  const { detail, selectedNode } = useAppStore();
  const runView = useAppStore((s) => s.runView);
  // per-node subscription keeps identity semantics with FlowNode
  const run = useAppStore((s) =>
    s.selectedNode === null
      ? null
      : (s.runView?.nodes[s.selectedNode] ?? null),
  );
  if (runView === null) return null; // App only mounts this in run mode
  const node = detail?.flow.nodes.find((n) => n.id === selectedNode) ?? null;
  const ports = Object.entries(run?.ports ?? {});

  return (
    <aside
      data-testid="run-inspector"
      style={{
        width: 300,
        borderLeft: "1px solid #ddd",
        padding: "0.75rem 1rem",
        overflowY: "auto",
        fontSize: 13,
        flexShrink: 0,
      }}
    >
      {node === null ? (
        <RunSummary view={runView} />
      ) : (
        <>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
            <h3 style={{ margin: "0 0 0.25rem", fontSize: 14 }}>{node.id}</h3>
            <span style={{ color: "#888", fontSize: 11 }}>{node.type}</span>
          </div>
          {run === null ? (
            <p data-testid="run-inspector-untouched" style={{ color: "#888" }}>
              no events for this node in this run
            </p>
          ) : (
            <>
              <p data-testid="run-inspector-status" style={{ margin: "0 0 0.5rem" }}>
                fired ×{run.firings} ·{" "}
                <span
                  style={{
                    color: run.active
                      ? "#1565c0"
                      : (OUTCOME_COLOR[run.outcome] ?? "#666"),
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
                    background: "#f6f6f6",
                    borderRadius: 4,
                    padding: "0.4rem 0.6rem",
                    fontFamily: "ui-monospace, monospace",
                    fontSize: 11,
                    wordBreak: "break-all",
                  }}
                >
                  <div>
                    {run.request.method} {run.request.url}
                  </div>
                  {run.request.status !== null && (
                    <div style={{ color: "#555" }}>
                      HTTP {run.request.status} · {run.request.sizeBytes}B
                      {run.request.totalMs !== null &&
                        ` · ${Math.round(run.request.totalMs)}ms`}
                    </div>
                  )}
                  {run.request.error !== null && (
                    <div style={{ color: "#b71c1c" }}>
                      {run.request.error} (attempt {run.request.attempt})
                    </div>
                  )}
                </div>
              )}
              <PortRows
                label="inputs"
                entries={ports.filter(([k]) => k.startsWith("in:"))}
              />
              <PortRows
                label="outputs"
                entries={ports.filter(([k]) => k.startsWith("out:"))}
              />
              {run.log && (
                <>
                  <h4
                    style={{
                      margin: "0.75rem 0 0.25rem",
                      fontSize: 11,
                      color: "#888",
                    }}
                  >
                    log · {run.log.count}{" "}
                    {run.log.count > run.log.ring.length &&
                      `(last ${run.log.ring.length} kept)`}
                  </h4>
                  <ol
                    data-testid="run-inspector-log"
                    style={{
                      margin: 0,
                      paddingLeft: "1.4rem",
                      fontFamily: "ui-monospace, monospace",
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
    </aside>
  );
}
