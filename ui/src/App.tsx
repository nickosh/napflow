import { Background, Controls, ReactFlow } from "@xyflow/react";
import { useEffect, useMemo } from "react";

import "@xyflow/react/dist/style.css";

import DiagnosticsPanel from "./components/DiagnosticsPanel";
import FlowList from "./components/FlowList";
import FlowNode from "./components/FlowNode";
import Inspector from "./components/Inspector";
import { toGraph } from "./graph";
import { useAppStore } from "./store";

const nodeTypes = { napflow: FlowNode };

function Canvas() {
  const { detail, detailError, selectNode } = useAppStore();

  const graph = useMemo(
    () => (detail !== null ? toGraph(detail) : null),
    [detail],
  );

  if (detailError !== null) {
    // broken flow: no canvas to draw — the E-codes ARE the view
    return (
      <div style={{ flex: 1, padding: "1rem", overflowY: "auto" }}>
        <p data-testid="detail-error" style={{ color: "#c62828" }}>
          {detailError.message}
        </p>
        <ul data-testid="detail-error-diagnostics" style={{ fontSize: 13 }}>
          {detailError.diagnostics.map((d, i) => (
            <li key={i}>
              <strong>{d.code}</strong> {d.message}{" "}
              <em style={{ color: "#888" }}>({d.hint})</em>
            </li>
          ))}
        </ul>
      </div>
    );
  }
  if (detail === null || graph === null) {
    return <div style={{ flex: 1 }} data-testid="canvas" />;
  }
  return (
    <div style={{ flex: 1, minWidth: 0 }} data-testid="canvas">
      <ReactFlow
        key={detail.identity} // remount per flow: fresh fitView
        nodes={graph.nodes}
        edges={graph.edges}
        nodeTypes={nodeTypes}
        fitView
        nodesDraggable={false} // read-only until S4/M4
        nodesConnectable={false}
        onNodeClick={(_event, node) => selectNode(node.id)}
        onPaneClick={() => selectNode(null)}
      >
        <Background />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}

export default function App() {
  const { workspace, error, detail, load } = useAppStore();
  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100vh",
        fontFamily: "system-ui, sans-serif",
      }}
    >
      <header
        style={{
          padding: "0.5rem 1rem",
          borderBottom: "1px solid #ddd",
          display: "flex",
          gap: "0.75rem",
          alignItems: "baseline",
        }}
      >
        <strong>napflow</strong>
        <span data-testid="workspace-name">{workspace?.name ?? "…"}</span>
        {workspace && (
          <span style={{ color: "#888", fontSize: "0.85rem" }}>
            v{workspace.version}
          </span>
        )}
        {error && (
          <span data-testid="load-error" style={{ color: "#c00" }}>
            {error}
          </span>
        )}
      </header>
      <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
        <FlowList />
        <Canvas />
        <Inspector />
      </div>
      <DiagnosticsPanel diagnostics={detail?.diagnostics ?? []} />
    </div>
  );
}
