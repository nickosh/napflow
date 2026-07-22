import { BaseEdge, getBezierPath, type EdgeProps } from "@xyflow/react";

import { useAppStore } from "../store";

/** The one edge type on the canvas. Outside run mode it is a plain
 * bezier wire (data wires blue, error-routing wires red — F1 handoff).
 * In run mode, wires that carried a message brighten and grow animated
 * flow dashes plus a travelling dot per message_emitted; untouched
 * wires dim so the taken path stands out — this also makes history
 * replays read as "where traffic went". */
export default function RunEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  sourceHandleId,
  style,
  markerEnd,
  selected,
}: EdgeProps) {
  // undefined = not in run mode; null = run mode, wire never fired
  const pulse = useAppStore((s) => {
    if (s.runView === null) return undefined;
    const activeView =
      s.runFramePath.length > 0 ? s.runFrameView : s.runView;
    return activeView?.edges[id] ?? null;
  });
  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  // errors are data (D18/D21): wires off error-routing ports read red
  const isErr = sourceHandleId === "error" || sourceHandleId === "failed";
  const baseStroke = isErr ? "var(--wire-err)" : "var(--wire)";
  const bright = isErr ? "var(--wire-err-bright)" : "var(--wire-bright)";

  // selected in run mode = its crossed messages are listed (M5.5)
  const edgeStyle =
    pulse === undefined
      ? { ...style, stroke: baseStroke, opacity: 0.9 }
      : pulse === null
        ? { ...style, stroke: baseStroke, opacity: selected ? 0.6 : 0.3 }
        : {
            ...style,
            stroke: bright,
            strokeWidth: selected ? 3.5 : 2.5,
          };

  return (
    <>
      <BaseEdge id={id} path={path} style={edgeStyle} markerEnd={markerEnd} />
      {pulse != null && (
        <>
          <path
            className="napf-edge-lit"
            d={path}
            fill="none"
            stroke={bright}
            strokeWidth={2.5}
            strokeDasharray="6 12"
            strokeLinecap="round"
            style={{ opacity: 0.9 }}
          />
          <circle
            key={pulse.count} // remount restarts the travel per message
            className="napf-edge-dot"
            r={4}
            style={{ offsetPath: `path("${path}")` }}
            data-testid="edge-dot"
          />
          {pulse.count > 1 && (
            <text
              className="napf-edge-count"
              x={labelX}
              y={labelY}
              data-testid={`edge-count-${id}`}
            >
              ×{pulse.count}
            </text>
          )}
        </>
      )}
    </>
  );
}
