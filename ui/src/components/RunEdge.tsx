import { BaseEdge, getBezierPath, type EdgeProps } from "@xyflow/react";

import { useAppStore } from "../store";

/** The one edge type on the canvas. Outside run mode it is a plain
 * bezier wire (stroke = port color, set in graph.ts). In run mode,
 * wires that carried a message glow + replay a travelling dot per
 * message_emitted; untouched wires dim so the taken path stands out —
 * this also makes history replays read as "where traffic went". */
export default function RunEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style,
  markerEnd,
  selected,
}: EdgeProps) {
  // undefined = not in run mode; null = run mode, wire never fired
  const pulse = useAppStore((s) =>
    s.runView === null ? undefined : (s.runView.edges[id] ?? null),
  );
  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  // selected in run mode = its crossed messages are listed (M5.5)
  const edgeStyle =
    pulse === undefined
      ? style
      : pulse === null
        ? { ...style, opacity: selected ? 0.6 : 0.3 }
        : { ...style, strokeWidth: selected ? 3.5 : 2.5 };

  return (
    <>
      <BaseEdge id={id} path={path} style={edgeStyle} markerEnd={markerEnd} />
      {pulse != null && (
        <>
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
