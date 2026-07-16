import { FlowArrow, TreeStructure, WarningCircle } from "@phosphor-icons/react";

import { useAppStore } from "../store";
import { useChrome } from "../uiChrome";

/** F1: the persistent sidebar became a floating flows menu — a
 * tree-icon toggle in the top-left cluster opening the workspace's
 * flow list; the canvas gets the full width. */
export default function FlowList() {
  const { flows, selectedFlow, openFlow } = useAppStore();
  const flowsOpen = useChrome((s) => s.flowsOpen);
  const setFlowsOpen = useChrome((s) => s.setFlowsOpen);
  return (
    <div style={{ position: "relative" }}>
      <button
        data-testid="flows-toggle"
        className="nf-chip nf-chip-icon"
        title="Flows"
        aria-expanded={flowsOpen}
        onClick={() => setFlowsOpen(!flowsOpen)}
        style={{ color: "var(--accent)" }}
      >
        <TreeStructure size={17} />
      </button>
      {flowsOpen && (
        <nav
          data-testid="flow-list"
          className="nf-card"
          style={{
            position: "absolute",
            left: 0,
            top: 42,
            width: 250,
            maxHeight: "60vh",
            overflowY: "auto",
            padding: 6,
            zIndex: 20,
          }}
        >
          <div className="nf-kicker">Flows in workspace</div>
          {flows.map((flow) => {
            const active = flow.identity === selectedFlow;
            return (
              <button
                key={flow.identity}
                data-testid="flow-item"
                className={`nf-row${active ? " nf-active" : ""}`}
                onClick={() => {
                  setFlowsOpen(false);
                  void openFlow(flow.identity);
                }}
              >
                <FlowArrow size={14} color="var(--accent)" />
                <span
                  style={{
                    flex: 1,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    color: flow.valid ? undefined : "var(--err)",
                  }}
                >
                  {flow.identity}
                </span>
                {!flow.valid && (
                  <WarningCircle size={14} weight="fill" color="var(--err)" />
                )}
              </button>
            );
          })}
          {flows.length === 0 && (
            <p style={{ padding: "0 8px", color: "var(--muted)", fontSize: 12 }}>
              no flows discovered
            </p>
          )}
        </nav>
      )}
    </div>
  );
}
