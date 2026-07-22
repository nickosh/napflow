import { useEffect, useState } from "react";
import {
  FileCode,
  FlowArrow,
  MagicWand,
  MagnifyingGlass,
  Moon,
  Play,
  TerminalWindow,
} from "@phosphor-icons/react";
import type { Icon } from "@phosphor-icons/react";
import { useShallow } from "zustand/react/shallow";

import { useAppStore } from "../store";
import { useChrome, type RunInputPort } from "../uiChrome";

type Row = {
  icon: Icon;
  name: string;
  kind: "flow" | "action";
  run: () => void;
};

/** F1 ⌘K palette: jump to any flow or fire an editor action. Pure
 * client-side — rows come from the already-loaded workspace list. */
export default function CommandPalette() {
  const cmdkOpen = useChrome((s) => s.cmdkOpen);
  const setCmdkOpen = useChrome((s) => s.setCmdkOpen);
  const {
    toggleTheme,
    setCodeOpen,
    requestTidy,
    openRunPopover,
    closeRunPopover,
  } = useChrome(
    useShallow((state) => ({
      toggleTheme: state.toggleTheme,
      setCodeOpen: state.setCodeOpen,
      requestTidy: state.requestTidy,
      openRunPopover: state.openRunPopover,
      closeRunPopover: state.closeRunPopover,
    })),
  );
  const { flows, detail, openFlow, startRun, openRunPanel, closeRunPanel } =
    useAppStore();
  const runPanelTab = useAppStore((s) => s.runPanelTab);
  const inRunMode = useAppStore((s) => s.runView !== null);
  const [q, setQ] = useState("");

  useEffect(() => {
    if (cmdkOpen) setQ("");
  }, [cmdkOpen]);

  if (!cmdkOpen) return null;
  const close = () => setCmdkOpen(false);

  const startPorts = (
    (detail?.flow.nodes.find((n) => n.type === "start")?.config?.ports as
      | RunInputPort[]
      | undefined) ?? []
  ).filter((p) => p.name !== "");

  const rows: Row[] = [
    ...flows.map((f) => ({
      icon: FlowArrow,
      name: f.identity,
      kind: "flow" as const,
      run: () => void openFlow(f.identity),
    })),
    ...(detail !== null && !inRunMode
      ? [
          {
            icon: Play,
            name: "Run flow",
            kind: "action" as const,
            run: () => {
              if (startPorts.length > 0) {
                openRunPopover(detail.identity, startPorts);
              } else {
                closeRunPopover();
                void startRun({});
              }
            },
          },
          {
            icon: MagicWand,
            name: "Tidy layout",
            kind: "action" as const,
            run: requestTidy,
          },
        ]
      : []),
    {
      icon: TerminalWindow,
      name: "Toggle console",
      kind: "action" as const,
      run: () =>
        runPanelTab !== null ? closeRunPanel() : openRunPanel("history"),
    },
    ...(detail !== null
      ? [
          {
            icon: FileCode,
            name: "Open nodes.py",
            kind: "action" as const,
            run: () => setCodeOpen(true),
          },
        ]
      : []),
    {
      icon: Moon,
      name: "Toggle theme",
      kind: "action" as const,
      run: toggleTheme,
    },
  ].filter((r) => q === "" || r.name.toLowerCase().includes(q.toLowerCase()));

  return (
    <div
      data-testid="cmdk"
      onClick={close}
      style={{
        position: "fixed",
        inset: 0,
        background: "var(--scrim)",
        zIndex: 50,
        display: "flex",
        justifyContent: "center",
        alignItems: "flex-start",
        paddingTop: 110,
      }}
    >
      <div
        className="nf-card"
        onClick={(e) => e.stopPropagation()}
        style={{ width: 480, overflow: "hidden" }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 9,
            padding: "12px 14px",
            borderBottom: "1px solid var(--border)",
          }}
        >
          <MagnifyingGlass size={15} color="var(--muted)" />
          <input
            data-testid="cmdk-input"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Jump to flow or action…"
            autoFocus
            style={{ all: "unset", flex: 1, fontSize: 13, color: "var(--text)" }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && rows.length > 0) {
                close();
                rows[0].run();
              }
            }}
          />
          <span
            style={{
              fontSize: 10,
              color: "var(--muted)",
              border: "1px solid var(--border)",
              borderRadius: 4,
              padding: "2px 5px",
            }}
          >
            esc
          </span>
        </div>
        <div style={{ maxHeight: 300, overflowY: "auto", padding: 6 }}>
          {rows.map((row) => {
            const RowIcon = row.icon;
            return (
              <button
                key={`${row.kind}:${row.name}`}
                data-testid="cmdk-row"
                className="nf-row"
                onClick={() => {
                  close();
                  row.run();
                }}
              >
                <RowIcon size={14} color="var(--accent)" />
                <span style={{ flex: 1, fontSize: 12.5 }}>{row.name}</span>
                <span style={{ fontSize: 10.5, color: "var(--muted)" }}>
                  {row.kind}
                </span>
              </button>
            );
          })}
          {rows.length === 0 && (
            <p style={{ padding: "4px 10px", color: "var(--muted)", fontSize: 12 }}>
              nothing matches
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
