import { Background, Controls, ReactFlow, type Node } from "@xyflow/react";
import { useEffect } from "react";

import "@xyflow/react/dist/style.css";

import { useAppStore } from "./store";

// S4/M2 walking skeleton: one canvas node per discovered flow, fed by
// the real API — proves static serving + REST + xyflow render in one
// screen. The real canvas (nodes/edges of ONE flow) replaces this at
// S4/M3.
export default function App() {
  const { workspace, flows, error, load } = useAppStore();
  useEffect(() => {
    void load();
  }, [load]);

  const nodes: Node[] = flows.map((flow, index) => ({
    id: flow.identity,
    position: { x: 40, y: 40 + index * 80 },
    data: { label: flow.valid ? flow.identity : `${flow.identity} (invalid)` },
  }));

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
      <div style={{ flex: 1 }} data-testid="canvas">
        <ReactFlow nodes={nodes} edges={[]} fitView>
          <Background />
          <Controls />
        </ReactFlow>
      </div>
    </div>
  );
}
