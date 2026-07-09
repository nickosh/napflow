import { useState } from "react";

import { ApiError, cloneFlow } from "../api";
import { useAppStore } from "../store";

/** Inspector block for flow/loop nodes (S4/M6, FR-1007): open the
 * referenced flow (drill-in is pure navigation, D09) and "Clone to new
 * flow…" — fork the target's folder and repoint THIS node at the clone
 * (the D09 escape hatch from shared-reference semantics; the other
 * users of the original keep it untouched). */
export default function SubflowActions({
  nodeId,
  configKey,
  target,
  config,
}: {
  nodeId: string;
  configKey: "flow" | "body";
  target: string;
  config: Record<string, unknown>;
}) {
  const { openFlow, updateNodeConfig, refreshFlows } = useAppStore();
  const [dest, setDest] = useState<string | null>(null); // null = closed
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function clone() {
    if (dest === null || dest === "" || busy) return;
    setBusy(true);
    setError(null);
    try {
      const created = await cloneFlow(target, dest);
      // repoint this node at its private fork — autosave persists it
      updateNodeConfig(nodeId, { ...config, [configKey]: created.identity });
      setDest(null);
      void refreshFlows();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ margin: "0.5rem 0 0.75rem", fontSize: 12 }}>
      <button
        data-testid="drill-in"
        onClick={() => void openFlow(target)}
        title="open the referenced flow (double-clicking the node works too)"
        style={{
          cursor: "pointer",
          fontFamily: "inherit",
          fontSize: 12,
          padding: "2px 8px",
        }}
      >
        open {target} →
      </button>
      {dest === null ? (
        <button
          data-testid="clone-flow"
          onClick={() => {
            setError(null);
            setDest(`${target}_copy`);
          }}
          title="fork the referenced flow's folder and point this node at the copy"
          style={{
            cursor: "pointer",
            fontFamily: "inherit",
            fontSize: 12,
            padding: "2px 8px",
            marginLeft: 6,
          }}
        >
          Clone to new flow…
        </button>
      ) : (
        <div style={{ marginTop: 6, display: "flex", gap: 4 }}>
          <input
            data-testid="clone-dest"
            value={dest}
            onChange={(e) => setDest(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void clone();
              if (e.key === "Escape") setDest(null);
            }}
            autoFocus
            style={{ flex: 1, fontSize: 12, fontFamily: "inherit" }}
          />
          <button
            data-testid="clone-confirm"
            onClick={() => void clone()}
            disabled={busy}
            style={{ cursor: "pointer", fontFamily: "inherit", fontSize: 12 }}
          >
            clone
          </button>
          <button
            onClick={() => setDest(null)}
            style={{ cursor: "pointer", fontFamily: "inherit", fontSize: 12 }}
          >
            ×
          </button>
        </div>
      )}
      {error !== null && (
        <p data-testid="clone-error" style={{ color: "#c62828", margin: "4px 0 0" }}>
          {error}
        </p>
      )}
    </div>
  );
}
