import { useAppStore } from "../store";

// Debounced-autosave surface (owner fork): a passive indicator, plus
// the ONE prompt the model allows — etag conflict, last-write-wins or
// reload (FR-1004 ceiling). F1: rendered inside the breadcrumb chip.
export default function SaveStatus() {
  const { saveState, saveError, saveDiagnostics, resolveConflict } =
    useAppStore();

  if (saveState === "conflict") {
    return (
      <span
        data-testid="save-conflict"
        style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 6 }}
      >
        <span style={{ color: "var(--err)" }}>file changed on disk</span>
        <button
          data-testid="conflict-reload"
          className="nf-btn"
          style={{ padding: "1px 8px" }}
          onClick={() => void resolveConflict("reload")}
        >
          reload
        </button>
        <button
          data-testid="conflict-overwrite"
          className="nf-btn"
          style={{ padding: "1px 8px" }}
          onClick={() => void resolveConflict("overwrite")}
        >
          overwrite
        </button>
      </span>
    );
  }
  if (saveState === "error") {
    const detail = saveDiagnostics[0]?.message ?? saveError ?? "save failed";
    return (
      <span
        data-testid="save-error"
        title={detail}
        style={{
          fontSize: 12,
          color: "var(--err)",
          maxWidth: 260,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        save failed: {detail}
      </span>
    );
  }
  const label =
    saveState === "clean" ? "saved" : saveState === "saving" ? "saving…" : "…";
  return (
    <span
      data-testid="save-status"
      data-state={saveState}
      style={{ fontSize: 11, color: "var(--muted)" }}
    >
      {label}
    </span>
  );
}
