import { useState } from "react";
import {
  ClockCounterClockwise,
  MagicWand,
  PencilSimple,
  Play,
  Plus,
} from "@phosphor-icons/react";

import { useAppStore } from "../store";
import { useChrome } from "../uiChrome";
import { parseDefault } from "./PortEditor";

export type StartPort = { name: string; type?: string; default?: unknown };

export function collectRunInputs(
  startPorts: StartPort[],
  cells: Record<string, string>,
  edited: ReadonlySet<string>,
): { inputs: Record<string, unknown>; invalid: Set<string> } {
  const inputs: Record<string, unknown> = {};
  const invalid = new Set<string>();
  for (const port of startPorts) {
    const text = cells[port.name] ?? "";
    const wasEdited = edited.has(port.name);
    // Only a truly untouched configured default is omitted, so the engine can
    // evaluate it in BIND context. An edited blank is a real empty-string
    // override for string/any ports, not a request to silently reuse default.
    if (
      port.default !== undefined &&
      text === showValue(port.default)
    ) {
      continue;
    }
    if (!wasEdited && text === "" && port.default === undefined) continue;
    const parsed = parseDefault(text, port.type ?? "any", false);
    if (parsed.ok) inputs[port.name] = parsed.value;
    else invalid.add(port.name);
  }
  return { inputs, invalid };
}

function showValue(value: unknown): string {
  if (value === undefined) return "";
  return typeof value === "string" ? value : JSON.stringify(value);
}

const roundBtn: React.CSSProperties = {
  width: 40,
  height: 40,
  borderRadius: "50%",
  padding: 0,
  justifyContent: "center",
};

/** F1 bottom-center bar (FR-1005, owner fork "hybrid popover" kept):
 * add-block + tidy + the Run pill. Run fires immediately when the flow
 * has no Start ports, otherwise the popover opens prefilled with the
 * declared defaults (same typed parsing as the Start-port editor —
 * `napf run -i` semantics). In run mode the pill flips to Edit. */
export default function RunControls() {
  const {
    workspace,
    workspaceNotice,
    detail,
    runEnv,
    runLive,
    runNotice,
    setRunEnv,
    startRun,
    exitRun,
    openRunPanel,
  } = useAppStore();
  const inRunMode = useAppStore((s) => s.runView !== null);
  const {
    pickerAt,
    openPickerAt,
    closePicker,
    requestTidy,
    runPopoverOpen,
    setRunPopoverOpen,
  } = useChrome();
  const [cells, setCells] = useState<Record<string, string>>({});
  const [bad, setBad] = useState<Set<string>>(new Set());
  const [edited, setEdited] = useState<Set<string>>(new Set());

  if (detail === null) return null;
  const startPorts = (
    (detail.flow.nodes.find((n) => n.type === "start")?.config?.ports as
      | StartPort[]
      | undefined) ?? []
  ).filter((p) => p.name !== "");
  const notice = runNotice ?? workspaceNotice;
  const noticeIsWarning = notice?.startsWith("warning:") ?? false;

  const launch = (inputs: Record<string, unknown>) => {
    setRunPopoverOpen(false);
    void startRun(inputs);
  };

  const openPopover = () => {
    const prefill: Record<string, string> = {};
    for (const port of startPorts) prefill[port.name] = showValue(port.default);
    setCells(prefill);
    setBad(new Set());
    setEdited(new Set());
    setRunPopoverOpen(true);
  };

  const launchWithInputs = () => {
    const { inputs, invalid } = collectRunInputs(startPorts, cells, edited);
    setBad(invalid);
    if (invalid.size === 0) launch(inputs);
  };

  return (
    <div
      style={{
        position: "absolute",
        left: "50%",
        transform: "translateX(-50%)",
        bottom: 14,
        display: "flex",
        gap: 8,
        alignItems: "center",
        zIndex: 20,
      }}
    >
      {notice && (
        <span
          data-testid="run-notice"
          title={notice}
          className="nf-chip"
          style={{
            color: noticeIsWarning ? "var(--warn)" : "var(--err)",
            maxWidth: 320,
          }}
        >
          <span
            style={{
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {notice}
          </span>
        </span>
      )}
      {!inRunMode && (
        <>
          <button
            data-testid="add-node"
            className="nf-btn nf-btn-accent"
            title="Add node"
            style={{ ...roundBtn, background: "var(--surface)", boxShadow: "var(--shadow)" }}
            onClick={() =>
              pickerAt !== null
                ? closePicker()
                : openPickerAt(
                    window.innerWidth / 2 - 142,
                    window.innerHeight / 2 - 260,
                  )
            }
          >
            <Plus size={17} />
          </button>
          <button
            className="nf-btn"
            title="Tidy layout"
            style={{ ...roundBtn, background: "var(--surface)", boxShadow: "var(--shadow)", color: "var(--muted)" }}
            onClick={requestTidy}
          >
            <MagicWand size={16} />
          </button>
          {(workspace?.env_profiles.length ?? 0) > 0 && (
            <select
              data-testid="run-env"
              className="nf-select"
              value={runEnv ?? ""}
              title="env profile for the next run"
              onChange={(e) =>
                setRunEnv(e.target.value === "" ? null : e.target.value)
              }
              style={{ height: 40, borderRadius: 20, background: "var(--surface)", boxShadow: "var(--shadow)" }}
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
            className="nf-btn nf-btn-accent"
            disabled={runLive}
            onClick={() => (startPorts.length > 0 ? openPopover() : launch({}))}
            style={{
              height: 40,
              padding: "0 18px",
              borderRadius: 20,
              fontWeight: 500,
              background: "var(--surface)",
              boxShadow: "var(--shadow)",
            }}
          >
            <Play size={14} />
            Run
          </button>
          <button
            data-testid="open-history"
            className="nf-btn"
            title="Run history"
            style={{ ...roundBtn, background: "var(--surface)", boxShadow: "var(--shadow)", color: "var(--muted)" }}
            onClick={() => openRunPanel("history")}
          >
            <ClockCounterClockwise size={16} />
          </button>
        </>
      )}
      {inRunMode && (
        <button
          data-testid="run-pill-edit"
          className="nf-btn nf-btn-accent"
          title={
            runLive
              ? "back to editing — stop watching (the run keeps going)"
              : "back to editing"
          }
          onClick={exitRun}
          style={{
            height: 40,
            padding: "0 18px",
            borderRadius: 20,
            fontWeight: 500,
            background: "var(--accent-t)",
            boxShadow: "var(--shadow)",
          }}
        >
          <PencilSimple size={14} />
          Edit
        </button>
      )}
      {runPopoverOpen && (
        <div
          data-testid="run-popover"
          className="nf-card"
          style={{
            position: "absolute",
            bottom: "calc(100% + 8px)",
            left: "50%",
            transform: "translateX(-50%)",
            zIndex: 30,
            padding: "0.6rem 0.75rem",
            minWidth: 280,
            fontSize: 12,
          }}
        >
          <div style={{ marginBottom: 6, color: "var(--muted)" }}>
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
              <span style={{ minWidth: 90, fontFamily: "var(--mono)" }}>
                {port.name}
                <span style={{ color: "var(--muted)" }}>
                  : {port.type ?? "any"}
                </span>
              </span>
              <input
                data-testid={`run-input-${port.name}`}
                className={`nf-input${bad.has(port.name) ? " nf-bad" : ""}`}
                value={cells[port.name] ?? ""}
                onChange={(e) => {
                  setCells({ ...cells, [port.name]: e.target.value });
                  setEdited((current) => new Set(current).add(port.name));
                }}
                style={{ flex: 1 }}
              />
            </label>
          ))}
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 6 }}>
            <button className="nf-btn" onClick={() => setRunPopoverOpen(false)}>
              cancel
            </button>
            <button
              data-testid="run-popover-start"
              className="nf-btn nf-btn-accent"
              onClick={launchWithInputs}
            >
              <Play size={12} />
              run
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
