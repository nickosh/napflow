import { useAppStore } from "../store";

// Debounced-autosave surface (owner fork): a passive indicator, plus
// the ONE prompt the model allows — etag conflict, last-write-wins or
// reload (FR-1004 ceiling).
export default function SaveStatus() {
  const { saveState, saveError, saveDiagnostics, resolveConflict } =
    useAppStore();

  if (saveState === "conflict") {
    return (
      <span data-testid="save-conflict" style={{ fontSize: 12 }}>
        <span style={{ color: "#c62828", marginRight: 8 }}>
          file changed on disk
        </span>
        <button
          data-testid="conflict-reload"
          onClick={() => void resolveConflict("reload")}
          style={{ marginRight: 4, cursor: "pointer", fontFamily: "inherit" }}
        >
          reload
        </button>
        <button
          data-testid="conflict-overwrite"
          onClick={() => void resolveConflict("overwrite")}
          style={{ cursor: "pointer", fontFamily: "inherit" }}
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
        style={{ fontSize: 12, color: "#c62828" }}
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
      style={{ fontSize: 12, color: "#888" }}
    >
      {label}
    </span>
  );
}
