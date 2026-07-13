import { Handle, Position, type NodeProps } from "@xyflow/react";

import { portColor } from "../colors";
import {
  GHOST_SOURCE_HANDLE,
  GHOST_TARGET_HANDLE,
  type CanvasNode,
  type PortHandle,
} from "../graph";
import { preview, type NodeRunState, type PortTraffic } from "../runview";
import { useAppStore } from "../store";

// run-mode outcome colors (assert green/red, error routing red,
// EC13: a completed request is "ok" whatever the status code)
const OUTCOME_COLOR: Record<string, string> = {
  ok: "#2e7d32",
  failed: "#c62828",
  error: "#b71c1c",
};

const handleStyle = (type: string) => ({
  background: portColor(type),
  width: 9,
  height: 9,
  border: "1.5px solid #fff",
});

function PortRow({
  nodeId,
  port,
  side,
  traffic,
  onSelect,
}: {
  nodeId: string;
  port: PortHandle;
  side: "input" | "output";
  /** undefined = not in run mode; null = run mode, nothing crossed */
  traffic: PortTraffic | null | undefined;
  onSelect?: () => void;
}) {
  const isInput = side === "input";
  // M5.5 port traffic painting: a handle that carried data glows and
  // its tooltip shows the last complete value that crossed (with the
  // legacy preview fallback already folded into PortTraffic)
  const title =
    traffic != null
      ? `${port.name} — last: ${preview(traffic.lastValue, 200)}${
          traffic.count > 1 ? ` (×${traffic.count})` : ""
        }`
      : `${port.name}: ${port.type}`;
  // the click target is the label + handle ONLY — the row's empty
  // middle stays a node click (node clicks filter the event stream)
  const select = onSelect
    ? (e: React.MouseEvent) => {
        e.stopPropagation();
        onSelect();
      }
    : undefined;
  return (
    <div
      style={{
        position: "relative",
        display: "flex",
        justifyContent: isInput ? "flex-start" : "flex-end",
        padding: isInput ? "1px 8px 1px 10px" : "1px 10px 1px 8px",
        fontSize: 11,
        color: "#444",
      }}
    >
      <Handle
        id={port.name}
        type={isInput ? "target" : "source"}
        position={isInput ? Position.Left : Position.Right}
        className={traffic != null ? "napf-port-carried" : undefined}
        style={handleStyle(port.type)}
        onClick={select}
      />
      <span
        data-testid={`port-${nodeId}-${side}-${port.name}`}
        data-carried={traffic != null ? "true" : undefined}
        title={title}
        onClick={select}
        style={{ cursor: select ? "pointer" : undefined }}
      >
        {port.name}
        {port.required && isInput && (
          <span style={{ color: "#c62828" }}> *</span>
        )}
      </span>
    </div>
  );
}

function Badge({
  count,
  color,
  testId,
}: {
  count: number;
  color: string;
  testId: string;
}) {
  if (count === 0) return null;
  return (
    <span
      data-testid={testId}
      style={{
        background: color,
        color: "#fff",
        borderRadius: 8,
        fontSize: 10,
        padding: "0 6px",
        marginLeft: 4,
      }}
    >
      {count}
    </span>
  );
}

/** Border/effects for the run overlay; edit-mode look when `run` is
 * undefined (not in run mode) and idle when null (untouched so far). */
function runStyle(
  run: NodeRunState | null | undefined,
  selected: boolean | undefined,
): React.CSSProperties {
  if (run == null) {
    return { border: selected ? "2px solid #1565c0" : "1px solid #bbb" };
  }
  if (run.outcome === "skipped") {
    return { border: "1px dashed #bbb", opacity: 0.45 };
  }
  const color = run.active ? "#1565c0" : OUTCOME_COLOR[run.outcome];
  return { border: color ? `2px solid ${color}` : "1px solid #bbb" };
}

export default function FlowNode({ data, selected }: NodeProps<CanvasNode>) {
  // undefined = not in run mode; null = run mode, node untouched yet.
  // Entries are replaced immutably per event, so only touched nodes
  // re-render during a live stream.
  const run = useAppStore((s) => {
    if (s.runView === null) return undefined;
    const activeView =
      s.runFramePath.length > 0 ? s.runFrameView : s.runView;
    return activeView?.nodes[data.nodeId] ?? null;
  });
  const inRunMode = run !== undefined;
  const selectRunTraffic = useAppStore((s) => s.selectRunTraffic);
  const lastLog =
    run?.log == null ? undefined : run.log.ring[run.log.ring.length - 1];
  return (
    <div
      data-testid={`node-${data.nodeId}`}
      data-run-status={
        run == null ? undefined : run.active ? "active" : run.outcome
      }
      className={run?.active ? "napf-node-active" : undefined}
      style={{
        position: "relative",
        background: "#fff",
        borderRadius: 6,
        minWidth: 130,
        boxShadow: "0 1px 3px rgba(0,0,0,0.12)",
        fontFamily: "system-ui, sans-serif",
        ...runStyle(run, selected),
      }}
    >
      {run != null && run.lastSeq >= 0 && (
        // one-shot flash per event touching this node — remount replays
        <div key={run.lastSeq} className="napf-node-flash" />
      )}
      {/* node-level anchors for ghost-wires (FR-1007) — invisible,
          never connectable, and rendered only when a ghost edge needs
          them (template references name nodes, not ports) */}
      {data.ghostSource && (
        <Handle
          id={GHOST_SOURCE_HANDLE}
          type="source"
          position={Position.Right}
          isConnectable={false}
          style={{ opacity: 0, pointerEvents: "none", top: "50%" }}
        />
      )}
      {data.ghostTarget && (
        <Handle
          id={GHOST_TARGET_HANDLE}
          type="target"
          position={Position.Left}
          isConnectable={false}
          style={{ opacity: 0, pointerEvents: "none", top: "50%" }}
        />
      )}
      <div
        style={{
          padding: "4px 10px",
          borderBottom: "1px solid #eee",
          display: "flex",
          alignItems: "baseline",
          gap: 6,
        }}
      >
        <strong style={{ fontSize: 12 }}>{data.nodeId}</strong>
        <span style={{ fontSize: 10, color: "#888" }}>{data.nodeType}</span>
        <Badge count={data.errors} color="#c62828" testId="node-errors" />
        <Badge count={data.warnings} color="#ef6c00" testId="node-warnings" />
        {run != null && run.firings > 1 && (
          <span
            data-testid="node-firings"
            style={{ fontSize: 10, color: "#1565c0", fontWeight: 600 }}
          >
            ×{run.firings}
          </span>
        )}
        {run?.guard && (
          <span
            data-testid="node-guard"
            style={{
              background: "#ef6c00",
              color: "#fff",
              borderRadius: 8,
              fontSize: 10,
              padding: "0 6px",
            }}
          >
            {run.guard}
          </span>
        )}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <div style={{ padding: "3px 0", flex: 1 }}>
          {data.inputs.map((port) => (
            <PortRow
              key={port.name}
              nodeId={data.nodeId}
              port={port}
              side="input"
              traffic={
                inRunMode ? (run?.ports[`in:${port.name}`] ?? null) : undefined
              }
              onSelect={
                inRunMode
                  ? () =>
                      selectRunTraffic({
                        kind: "port",
                        node: data.nodeId,
                        port: port.name,
                        side: "input",
                      })
                  : undefined
              }
            />
          ))}
        </div>
        <div style={{ padding: "3px 0", flex: 1 }}>
          {data.outputs.map((port) => (
            <PortRow
              key={port.name}
              nodeId={data.nodeId}
              port={port}
              side="output"
              traffic={
                inRunMode ? (run?.ports[`out:${port.name}`] ?? null) : undefined
              }
              onSelect={
                inRunMode
                  ? () =>
                      selectRunTraffic({
                        kind: "port",
                        node: data.nodeId,
                        port: port.name,
                        side: "output",
                      })
                  : undefined
              }
            />
          ))}
        </div>
      </div>
      {run?.log && (
        // log nodes come alive during a run: newest logged value (the
        // full ring shows in the run inspector on click)
        <div
          className="napf-node-log"
          data-testid="node-log-value"
          title={preview(lastLog, 500)}
        >
          {run.log.count > 1 ? `[${run.log.count}] ` : ""}
          {preview(lastLog, 42)}
        </div>
      )}
    </div>
  );
}
