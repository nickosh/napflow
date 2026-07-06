import { Handle, Position, type NodeProps } from "@xyflow/react";

import { portColor } from "../colors";
import type { CanvasNode, PortHandle } from "../graph";

const handleStyle = (type: string) => ({
  background: portColor(type),
  width: 9,
  height: 9,
  border: "1.5px solid #fff",
});

function PortRow({
  port,
  side,
}: {
  port: PortHandle;
  side: "input" | "output";
}) {
  const isInput = side === "input";
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
        style={handleStyle(port.type)}
        isConnectable={false}
      />
      <span title={`${port.name}: ${port.type}`}>
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

export default function FlowNode({ data, selected }: NodeProps<CanvasNode>) {
  return (
    <div
      data-testid={`node-${data.nodeId}`}
      style={{
        background: "#fff",
        border: selected ? "2px solid #1565c0" : "1px solid #bbb",
        borderRadius: 6,
        minWidth: 130,
        boxShadow: "0 1px 3px rgba(0,0,0,0.12)",
        fontFamily: "system-ui, sans-serif",
      }}
    >
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
      </div>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <div style={{ padding: "3px 0", flex: 1 }}>
          {data.inputs.map((port) => (
            <PortRow key={port.name} port={port} side="input" />
          ))}
        </div>
        <div style={{ padding: "3px 0", flex: 1 }}>
          {data.outputs.map((port) => (
            <PortRow key={port.name} port={port} side="output" />
          ))}
        </div>
      </div>
    </div>
  );
}
