import { TerminalWindow } from "@phosphor-icons/react";

import { useAppStore } from "../store";

/** F1 bottom-left console toggle. Opens the most relevant tab: the
 * event stream when a run overlay is active, otherwise diagnostics
 * when the checker has findings, otherwise run history. */
export default function ConsoleButton() {
  const runPanelTab = useAppStore((s) => s.runPanelTab);
  const inRunMode = useAppStore((s) => s.runView !== null);
  const diagCount = useAppStore((s) => s.detail?.diagnostics.length ?? 0);
  const { openRunPanel, closeRunPanel } = useAppStore();
  const open = runPanelTab !== null;

  return (
    <div
      style={{
        position: "absolute",
        left: 14,
        bottom: 14,
        zIndex: 20,
      }}
    >
      <button
        data-testid="console-toggle"
        className="nf-chip"
        aria-pressed={open}
        style={{ color: open ? "var(--accent)" : "var(--muted)", height: 30 }}
        onClick={() =>
          open
            ? closeRunPanel()
            : openRunPanel(inRunMode ? "events" : diagCount > 0 ? "diag" : "history")
        }
      >
        <TerminalWindow size={14} />
        Console
        {diagCount > 0 && <span className="nf-badge">{diagCount}</span>}
      </button>
    </div>
  );
}
