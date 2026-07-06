import type { Diagnostic } from "../api";
import { useAppStore } from "../store";

function Row({ diag }: { diag: Diagnostic }) {
  const selectNode = useAppStore((s) => s.selectNode);
  const color = diag.severity === "error" ? "#c62828" : "#ef6c00";
  return (
    <li
      style={{ cursor: diag.node ? "pointer" : "default", marginBottom: 2 }}
      onClick={() => diag.node && selectNode(diag.node)}
    >
      <strong style={{ color }}>{diag.code}</strong>
      {diag.node && <span style={{ color: "#888" }}> [{diag.node}]</span>}{" "}
      {diag.message} <em style={{ color: "#888" }}>({diag.hint})</em>
    </li>
  );
}

// FR-1006 check half: E/W diagnostics surfaced with the canvas — the
// same CheckDiagnostic records `napf check` prints, node-linked.
export default function DiagnosticsPanel({
  diagnostics,
}: {
  diagnostics: Diagnostic[];
}) {
  if (diagnostics.length === 0) return null;
  return (
    <section
      data-testid="diagnostics"
      style={{
        borderTop: "1px solid #ddd",
        maxHeight: 140,
        overflowY: "auto",
        padding: "0.4rem 1rem",
        fontSize: 12,
        background: "#fffdf5",
      }}
    >
      <ul style={{ margin: 0, paddingLeft: "1.1rem", listStyle: "none" }}>
        {diagnostics.map((diag, index) => (
          <Row key={index} diag={diag} />
        ))}
      </ul>
    </section>
  );
}
