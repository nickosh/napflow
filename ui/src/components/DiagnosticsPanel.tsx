import { Info, WarningCircle } from "@phosphor-icons/react";

import type { Diagnostic } from "../api";
import { useAppStore } from "../store";

function Row({ diag }: { diag: Diagnostic }) {
  const selectNode = useAppStore((s) => s.selectNode);
  const isError = diag.severity === "error";
  return (
    <li
      className={diag.node ? "nf-row" : undefined}
      style={{
        display: "flex",
        alignItems: "baseline",
        gap: 8,
        padding: "4px 8px",
        cursor: diag.node ? "pointer" : "default",
        listStyle: "none",
      }}
      onClick={() => diag.node && selectNode(diag.node)}
    >
      {isError ? (
        <WarningCircle
          size={14}
          weight="fill"
          color="var(--err)"
          style={{ alignSelf: "center", flexShrink: 0 }}
        />
      ) : (
        <Info
          size={14}
          color="var(--warn)"
          style={{ alignSelf: "center", flexShrink: 0 }}
        />
      )}
      <strong style={{ color: isError ? "var(--err)" : "var(--warn)" }}>
        {diag.code}
      </strong>
      {diag.node && (
        <span style={{ color: "var(--accent)", fontWeight: 500 }}>
          {diag.node}
        </span>
      )}
      <span>{diag.message}</span>
      <em style={{ color: "var(--muted)" }}>({diag.hint})</em>
    </li>
  );
}

// FR-1006 check half: E/W diagnostics surfaced with the canvas — the
// same CheckDiagnostic records `napf check` prints, node-linked.
// F1: rendered as the console's Diagnostics tab.
export default function DiagnosticsPanel({
  diagnostics,
}: {
  diagnostics: Diagnostic[];
}) {
  return (
    <div
      data-testid="diagnostics"
      style={{ flex: 1, overflowY: "auto", padding: "6px 10px" }}
    >
      {diagnostics.length === 0 ? (
        <p style={{ color: "var(--muted)", margin: "0.5rem 0.5rem" }}>
          no diagnostics — the flow checks clean
        </p>
      ) : (
        <ul style={{ margin: 0, padding: 0 }}>
          {diagnostics.map((diag, index) => (
            <Row key={index} diag={diag} />
          ))}
        </ul>
      )}
    </div>
  );
}
