import { Panel, useConnection } from "@xyflow/react";

import { portColor } from "../colors";
import type { CanvasNode } from "../graph";
import { typeMismatch } from "../graph";

// Live W102 hint (FR-1002 "W102 hints", the connect-time half): while
// a wire is being dragged, hovering a typed input that mismatches the
// source's output type shows the same message the post-save checker
// would. Soft types (D11) — the connection is never blocked.
export default function ConnectHint() {
  const connection = useConnection<CanvasNode>();
  if (!connection.inProgress || connection.toHandle === null) return null;

  // handles exist for both directions; normalize to output → input
  const [outHandle, outNode, inHandle, inNode] =
    connection.fromHandle.type === "source"
      ? [
          connection.fromHandle,
          connection.fromNode,
          connection.toHandle,
          connection.toNode,
        ]
      : [
          connection.toHandle,
          connection.toNode,
          connection.fromHandle,
          connection.fromNode,
        ];
  if (inHandle.type !== "target" || outHandle.type !== "source") return null;

  const find = (
    node: typeof outNode,
    side: "inputs" | "outputs",
    name: string | null | undefined,
  ) => node?.data[side].find((p) => p.name === name);
  const source = find(outNode, "outputs", outHandle.id);
  const target = find(inNode, "inputs", inHandle.id);
  if (source === undefined || target === undefined) return null;
  if (!typeMismatch(source.type, target.type)) return null;

  return (
    <Panel position="bottom-center">
      <div
        data-testid="connect-hint"
        style={{
          background: "#fff8e1",
          border: "1px solid #ef6c00",
          borderRadius: 4,
          padding: "4px 10px",
          fontSize: 12,
          boxShadow: "0 1px 4px rgba(0,0,0,0.15)",
        }}
      >
        <strong style={{ color: "#ef6c00" }}>W102</strong> port type mismatch:{" "}
        <code style={{ color: portColor(source.type) }}>
          {outNode?.id}.{source.name}
        </code>{" "}
        is {source.type},{" "}
        <code style={{ color: portColor(target.type) }}>
          {inNode?.id}.{target.name}
        </code>{" "}
        expects {target.type} — soft types never block
      </div>
    </Panel>
  );
}
