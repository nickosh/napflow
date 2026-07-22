import { Handle, Position, type NodeProps } from "@xyflow/react";
import { useState } from "react";
import { CaretDown, CaretUp, Trash, WarningCircle } from "@phosphor-icons/react";

import { EDITOR_WIDTH, nodeMeta } from "../catalog";
import { portBright, portColor } from "../colors";
import {
  GHOST_SOURCE_HANDLE,
  GHOST_TARGET_HANDLE,
  drillTarget,
  type CanvasNode,
  type PortHandle,
} from "../graph";
import { preview, type NodeRunState, type PortTraffic } from "../runview";
import { useAppStore } from "../store";
import { isBoundaryNodeType } from "../nodeSemantics";
import ConfigForm from "./ConfigForm";
import NodeSafetyForm from "./NodeSafetyForm";
import { EndPortEditor, StartPortEditor } from "./PortEditor";
import SubflowActions from "./SubflowActions";

// run-mode outcome colors (assert green/red, error routing red,
// EC13: a completed request is "ok" whatever the status code)
const OUTCOME_COLOR: Record<string, string> = {
  ok: "var(--ok)",
  failed: "var(--err)",
  error: "var(--err-bright)",
};

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
  const carried = traffic != null;
  const base = portColor(port.type);
  const bright = portBright(port.type);
  // M5.5 port traffic painting: a handle that carried data glows and
  // its tooltip shows the last complete value that crossed (with the
  // legacy preview fallback already folded into PortTraffic)
  const title = carried
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
    <div className={`nf-port-row${isInput ? "" : " nf-out"}`}>
      <Handle
        id={port.name}
        type={isInput ? "target" : "source"}
        position={isInput ? Position.Left : Position.Right}
        className={`nf-handle${carried ? " napf-port-carried" : ""}`}
        style={{
          background: carried ? bright : `${base}59`,
          borderColor: carried ? bright : base,
          color: bright, // napf-port-carried glows via currentColor
        }}
        onClick={select}
      />
      <span
        data-testid={`port-${nodeId}-${side}-${port.name}`}
        data-carried={carried ? "true" : undefined}
        title={title}
        onClick={select}
        style={{
          cursor: select ? "pointer" : undefined,
          color: carried ? bright : undefined,
        }}
      >
        {port.name}
        {port.required && isInput && (
          <span style={{ color: "var(--err)" }}> *</span>
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
    <span className="nf-count-badge" data-testid={testId} style={{ background: color }}>
      {count}
    </span>
  );
}

/** Border/effects for the run overlay; edit-mode look when `run` is
 * undefined (not in run mode) and idle when null (untouched so far). */
function runStyle(run: NodeRunState | null | undefined): React.CSSProperties {
  if (run == null) return {};
  if (run.outcome === "skipped") {
    return { borderStyle: "dashed", opacity: 0.45 };
  }
  const color = run.active ? "var(--accent)" : OUTCOME_COLOR[run.outcome];
  return color ? { borderColor: color } : {};
}

/** Collapsed-card summary for types without quick inputs. */
function metaFor(
  nodeType: string,
  config: Record<string, unknown> | null,
): string | null {
  if (config === null) return null;
  if (nodeType === "assert") {
    const checks = Array.isArray(config.checks) ? config.checks.length : 0;
    return `${checks} check${checks === 1 ? "" : "s"}`;
  }
  if (nodeType === "note") {
    const text = typeof config.text === "string" ? config.text : "";
    return text === "" ? "empty note" : text.slice(0, 200);
  }
  if (nodeType === "start" || nodeType === "end") {
    const ports = Array.isArray(config.ports) ? config.ports.length : 0;
    const word = nodeType === "start" ? "input" : "output";
    return `${ports} ${word}${ports === 1 ? "" : "s"}`;
  }
  return null;
}

/** The in-card editor — the F1 replacement for the right-hand
 * Inspector: full per-type config, safety ceiling, port editors,
 * subflow actions, diagnostics, raw config, delete. Shown while the
 * node is selected (or pinned open via the caret). */
function NodeEditor({ data }: { data: CanvasNode["data"] }) {
  const deleteNode = useAppStore((s) => s.deleteNode);
  // select the stable array, filter in render — a filtering selector
  // would return a fresh reference per snapshot and loop React
  const allDiagnostics = useAppStore((s) => s.detail?.diagnostics);
  const diagnostics = (allDiagnostics ?? []).filter(
    (d) => d.node === data.nodeId,
  );
  const meta = nodeMeta(data.nodeType);
  const config = (data.config ?? {}) as Record<string, unknown>;
  const target = drillTarget(data);

  return (
    <div className="nf-node-editor nodrag nowheel">
      {diagnostics.length > 0 && (
        <ul style={{ margin: 0, paddingLeft: "1.1rem", color: "var(--err)" }}>
          {diagnostics.map((d, i) => (
            <li key={i} style={{ marginBottom: 4 }}>
              <strong>{d.code}</strong> {d.message}
            </li>
          ))}
        </ul>
      )}
      {target !== null && (
        <SubflowActions
          nodeId={data.nodeId}
          configKey={data.nodeType === "flow" ? "flow" : "body"}
          target={target}
          config={config}
        />
      )}
      {data.nodeType === "start" ? (
        <StartPortEditor nodeId={data.nodeId} />
      ) : data.nodeType === "end" ? (
        <EndPortEditor nodeId={data.nodeId} />
      ) : (
        <ConfigForm
          nodeId={data.nodeId}
          nodeType={data.nodeType}
          config={config}
          exclude={meta.quick}
        />
      )}
      <NodeSafetyForm nodeId={data.nodeId} />
      <details>
        <summary
          style={{ fontSize: 11, color: "var(--muted)", cursor: "pointer" }}
        >
          raw config
        </summary>
        <pre
          data-testid="node-config"
          style={{
            background: "var(--surface2)",
            border: "1px solid var(--border)",
            padding: "0.6rem",
            borderRadius: "var(--rsm)",
            fontSize: 11,
            fontFamily: "var(--mono)",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {JSON.stringify(data.config ?? {}, null, 2)}
        </pre>
      </details>
      <button
        data-testid="delete-node"
        className="nf-btn nf-btn-danger nodrag"
        onClick={() => deleteNode(data.nodeId)}
        title="delete node (edges go with it)"
        style={{ alignSelf: "flex-start" }}
      >
        <Trash size={13} />
        delete
      </button>
    </div>
  );
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
  const storeSelected = useAppStore((s) => s.selectedNode === data.nodeId);
  const selectRunTraffic = useAppStore((s) => s.selectRunTraffic);
  const endHistoryGroup = useAppStore((s) => s.endHistoryGroup);
  const [pinned, setPinned] = useState(false);
  const meta = nodeMeta(data.nodeType);
  const NodeIcon = meta.icon;
  // full inline editing (F1 owner call): selection opens the in-card
  // editor — the Inspector's mental model moved into the node
  const expanded = !inRunMode && (storeSelected || pinned);
  const quickKeys = meta.quick;
  const metaLine =
    quickKeys.length > 0 && !inRunMode ? null : metaFor(data.nodeType, data.config);
  const boundaryType = isBoundaryNodeType(data.nodeType)
    ? data.nodeType
    : null;
  const iconColor =
    boundaryType === "start"
      ? "var(--boundary-start)"
      : boundaryType === "end"
        ? "var(--boundary-end)"
        : "var(--accent)";
  const lastLog =
    run?.log == null ? undefined : run.log.ring[run.log.ring.length - 1];
  return (
    <div
      data-testid={`node-${data.nodeId}`}
      data-run-status={
        run == null ? undefined : run.active ? "active" : run.outcome
      }
      data-boundary={boundaryType ?? undefined}
      className={`nf-node${
        selected || storeSelected ? " nf-selected" : ""
      }${
        boundaryType === null
          ? ""
          : ` nf-boundary nf-boundary-${boundaryType}`
      }${run?.active ? " napf-node-active" : ""}`}
      onBlurCapture={endHistoryGroup}
      style={{
        // per-type sizes (owner call); the editor grows the card
        width: expanded ? Math.max(meta.width, EDITOR_WIDTH) : meta.width,
        ...runStyle(run),
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
      <div className="nf-node-head">
        <NodeIcon size={15} color={iconColor} />
        <span className="nf-node-title">{data.nodeId}</span>
        <span className="nf-node-type">{data.nodeType}</span>
        {data.autoStart && (
          <span
            data-testid="node-auto"
            className="nf-auto-source"
            title="Runs automatically once per flow frame"
          >
            AUTO
          </span>
        )}
        <Badge count={data.errors} color="var(--err)" testId="node-errors" />
        <Badge count={data.warnings} color="var(--warn)" testId="node-warnings" />
        {run != null && run.firings > 1 && (
          <span
            data-testid="node-firings"
            style={{ fontSize: 10, color: "var(--accent)", fontWeight: 600 }}
          >
            ×{run.firings}
          </span>
        )}
        {run?.guard && (
          <span
            className="nf-count-badge"
            data-testid="node-guard"
            style={{ background: "var(--warn)" }}
          >
            {run.guard}
          </span>
        )}
        {data.warnings + data.errors > 0 && (
          <WarningCircle
            size={14}
            weight="fill"
            color={data.errors > 0 ? "var(--err)" : "var(--warn)"}
          />
        )}
        {!inRunMode && (
          <button
            className="nf-iconbtn nodrag"
            title={expanded ? "Collapse" : "Expand"}
            aria-expanded={expanded}
            onClick={(e) => {
              e.stopPropagation();
              setPinned(!expanded);
            }}
          >
            {expanded ? <CaretUp size={13} /> : <CaretDown size={13} />}
          </button>
        )}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <div style={{ padding: "4px 0", flex: 1 }}>
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
        <div style={{ padding: "4px 0", flex: 1 }}>
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
      {quickKeys.length > 0 && !inRunMode && (
        <div className="nf-quick">
          <ConfigForm
            nodeId={data.nodeId}
            nodeType={data.nodeType}
            config={(data.config ?? {}) as Record<string, unknown>}
            only={quickKeys}
            quick
          />
        </div>
      )}
      {metaLine !== null && !expanded && (
        <div className="nf-node-meta">{metaLine}</div>
      )}
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
      {expanded && <NodeEditor data={data} />}
    </div>
  );
}
