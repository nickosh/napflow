import { useState } from "react";

import { useAppStore } from "../store";
import { parseDefault } from "./PortEditor";

type StartPort = { name: string; type?: string; default?: unknown };

function showValue(value: unknown): string {
  if (value === undefined) return "";
  return typeof value === "string" ? value : JSON.stringify(value);
}

const buttonStyle: React.CSSProperties = {
  fontSize: 12,
  padding: "2px 10px",
  cursor: "pointer",
  fontFamily: "inherit",
};

/** Header run surface (FR-1005, owner fork "hybrid popover"): env
 * dropdown always visible; Run fires immediately when the flow has no
 * Start ports, otherwise a popover opens prefilled with the declared
 * defaults (same typed parsing as the Start-port editor — `napf run
 * -i` semantics, zero friction on input-less flows). */
export default function RunControls() {
  const {
    workspace,
    detail,
    runEnv,
    runLive,
    runNotice,
    setRunEnv,
    startRun,
    openRunPanel,
  } = useAppStore();
  const [popoverOpen, setPopoverOpen] = useState(false);
  const [cells, setCells] = useState<Record<string, string>>({});
  const [bad, setBad] = useState<Set<string>>(new Set());

  if (detail === null) return null;
  const startPorts = (
    (detail.flow.nodes.find((n) => n.type === "start")?.config?.ports as
      | StartPort[]
      | undefined) ?? []
  ).filter((p) => p.name !== "");

  const launch = (inputs: Record<string, unknown>) => {
    setPopoverOpen(false);
    void startRun(inputs);
  };

  const openPopover = () => {
    const prefill: Record<string, string> = {};
    for (const port of startPorts) prefill[port.name] = showValue(port.default);
    setCells(prefill);
    setBad(new Set());
    setPopoverOpen(true);
  };

  const launchWithInputs = () => {
    const inputs: Record<string, unknown> = {};
    const invalid = new Set<string>();
    for (const port of startPorts) {
      const text = cells[port.name] ?? "";
      if (text === "" && port.default === undefined) continue; // gate decides
      const parsed = parseDefault(text, port.type ?? "any");
      if (parsed.ok) inputs[port.name] = parsed.value;
      else invalid.add(port.name);
    }
    setBad(invalid);
    if (invalid.size === 0) launch(inputs);
  };

  return (
    <span style={{ position: "relative", display: "flex", gap: 6 }}>
      {runNotice && (
        <span
          data-testid="run-notice"
          title={runNotice}
          style={{
            fontSize: 12,
            color: "#c62828",
            maxWidth: 320,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {runNotice}
        </span>
      )}
      {(workspace?.env_profiles.length ?? 0) > 0 && (
        <select
          data-testid="run-env"
          value={runEnv ?? ""}
          onChange={(e) => setRunEnv(e.target.value === "" ? null : e.target.value)}
          style={{ fontSize: 12, fontFamily: "inherit" }}
        >
          <option value="">(no env)</option>
          {workspace?.env_profiles.map((profile) => (
            <option key={profile} value={profile}>
              {profile}
            </option>
          ))}
        </select>
      )}
      <button
        data-testid="run-button"
        disabled={runLive}
        onClick={() =>
          startPorts.length > 0 ? openPopover() : launch({})
        }
        style={{
          ...buttonStyle,
          background: "#1565c0",
          color: "#fff",
          border: "1px solid #0d47a1",
          borderRadius: 3,
          opacity: runLive ? 0.5 : 1,
        }}
      >
        ▶ run
      </button>
      <button
        data-testid="open-history"
        onClick={() => openRunPanel("history")}
        style={buttonStyle}
      >
        runs
      </button>
      {popoverOpen && (
        <div
          data-testid="run-popover"
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            right: 0,
            zIndex: 20,
            background: "#fff",
            border: "1px solid #bbb",
            borderRadius: 6,
            boxShadow: "0 4px 14px rgba(0,0,0,0.18)",
            padding: "0.6rem 0.75rem",
            minWidth: 260,
            fontSize: 12,
          }}
        >
          <div style={{ marginBottom: 6, color: "#555" }}>
            flow inputs (blank = must have a default)
          </div>
          {startPorts.map((port) => (
            <label
              key={port.name}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                marginBottom: 4,
              }}
            >
              <span style={{ minWidth: 90, fontFamily: "ui-monospace, monospace" }}>
                {port.name}
                <span style={{ color: "#888" }}>: {port.type ?? "any"}</span>
              </span>
              <input
                data-testid={`run-input-${port.name}`}
                value={cells[port.name] ?? ""}
                onChange={(e) =>
                  setCells({ ...cells, [port.name]: e.target.value })
                }
                style={{
                  flex: 1,
                  fontSize: 12,
                  fontFamily: "ui-monospace, monospace",
                  padding: "2px 5px",
                  borderRadius: 3,
                  border: bad.has(port.name)
                    ? "1px solid #c62828"
                    : "1px solid #ccc",
                }}
              />
            </label>
          ))}
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 6 }}>
            <button style={buttonStyle} onClick={() => setPopoverOpen(false)}>
              cancel
            </button>
            <button
              data-testid="run-popover-start"
              style={{
                ...buttonStyle,
                background: "#1565c0",
                color: "#fff",
                border: "1px solid #0d47a1",
                borderRadius: 3,
              }}
              onClick={launchWithInputs}
            >
              ▶ run
            </button>
          </div>
        </div>
      )}
    </span>
  );
}
