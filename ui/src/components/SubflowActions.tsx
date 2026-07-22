import { useState } from "react";
import { ArrowSquareOut, CopySimple } from "@phosphor-icons/react";

import { ApiError, cloneFlow } from "../api";
import { useAppStore } from "../store";

/** Card-editor block for flow/loop nodes (S4/M6, FR-1007): open the
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
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <button
          data-testid="drill-in"
          className="nf-btn nf-btn-accent nodrag"
          onClick={() => void openFlow(target)}
          title="open the referenced flow (double-clicking the node works too)"
        >
          <ArrowSquareOut size={13} />
          open {target}
        </button>
        {dest === null && (
          <button
            data-testid="clone-flow"
            className="nf-btn nodrag"
            onClick={() => {
              setError(null);
              setDest(`${target}_copy`);
            }}
            title="fork the referenced flow's folder and point this node at the copy"
          >
            <CopySimple size={13} />
            Clone to new flow…
          </button>
        )}
      </div>
      {dest !== null && (
        <div style={{ display: "flex", gap: 4 }}>
          <input
            data-testid="clone-dest"
            className="nf-input nodrag"
            value={dest}
            onChange={(e) => setDest(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void clone();
              if (e.key === "Escape") setDest(null);
            }}
            autoFocus
            style={{ flex: 1 }}
          />
          <button
            data-testid="clone-confirm"
            className="nf-btn nf-btn-accent nodrag"
            onClick={() => void clone()}
            disabled={busy}
          >
            clone
          </button>
          <button className="nf-btn nodrag" onClick={() => setDest(null)}>
            ×
          </button>
        </div>
      )}
      {error !== null && (
        <p
          data-testid="clone-error"
          style={{ color: "var(--err)", margin: 0, fontSize: 12 }}
        >
          {error}
        </p>
      )}
    </div>
  );
}
