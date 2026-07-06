import { useAppStore } from "../store";

// Read-only in S4/M3 — editing (config forms + Monaco) lands at M4.
export default function Inspector() {
  const { detail, selectedNode } = useAppStore();
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
          <h3 style={{ margin: "0 0 0.25rem", fontSize: 14 }}>{node.id}</h3>
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
        </>
      )}
    </aside>
  );
}
