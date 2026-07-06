import { useAppStore } from "../store";
import ConfigForm from "./ConfigForm";
import { EndPortEditor, StartPortEditor } from "./PortEditor";

// S4/M4: the inspector edits — per-type config forms, Start/End port
// editors (FR-1006), node delete. The store autosaves every change.
export default function Inspector() {
  const { detail, selectedNode, deleteNode } = useAppStore();
  const node = detail?.flow.nodes.find((n) => n.id === selectedNode) ?? null;
  const diagnostics =
    detail?.diagnostics.filter((d) => d.node === selectedNode) ?? [];

  return (
    <aside
      data-testid="inspector"
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
        <>
          <h3 style={{ margin: "0 0 0.5rem", fontSize: 14 }}>
            {detail?.flow.flow.name ?? "—"}
          </h3>
          <p style={{ color: "#666" }}>
            {detail?.flow.flow.description ??
              "Select a node to inspect its config."}
          </p>
        </>
      ) : (
        <>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
            <h3 style={{ margin: "0 0 0.25rem", fontSize: 14, flex: 1 }}>
              {node.id}
            </h3>
            {node.type !== "start" && node.type !== "end" && (
              <button
                data-testid="delete-node"
                onClick={() => deleteNode(node.id)}
                title="delete node (edges go with it)"
                style={{
                  fontSize: 11,
                  color: "#c62828",
                  cursor: "pointer",
                  fontFamily: "inherit",
                  border: "1px solid #e0b4b4",
                  borderRadius: 3,
                  background: "#fff",
                }}
              >
                delete
              </button>
            )}
          </div>
          <p style={{ margin: "0 0 0.75rem", color: "#888" }}>{node.type}</p>
          {diagnostics.length > 0 && (
            <ul style={{ paddingLeft: "1.1rem", color: "#c62828" }}>
              {diagnostics.map((d, i) => (
                <li key={i} style={{ marginBottom: 4 }}>
                  <strong>{d.code}</strong> {d.message}
                </li>
              ))}
            </ul>
          )}
          {node.type === "start" ? (
            <StartPortEditor nodeId={node.id} />
          ) : node.type === "end" ? (
            <EndPortEditor nodeId={node.id} />
          ) : (
            <ConfigForm
              nodeId={node.id}
              nodeType={node.type}
              config={(node.config ?? {}) as Record<string, unknown>}
            />
          )}
          <details style={{ marginTop: "0.75rem" }}>
            <summary style={{ fontSize: 11, color: "#888", cursor: "pointer" }}>
              raw config
            </summary>
            <pre
              data-testid="node-config"
              style={{
                background: "#f6f6f6",
                padding: "0.6rem",
                borderRadius: 4,
                fontSize: 12,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}
            >
              {JSON.stringify(node.config ?? {}, null, 2)}
            </pre>
          </details>
        </>
      )}
    </aside>
  );
}
