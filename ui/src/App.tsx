import {
  Background,
  Controls,
  ReactFlow,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type Edge,
  type EdgeChange,
  type NodeChange,
} from "@xyflow/react";
import { useCallback, useEffect, useState } from "react";

import "@xyflow/react/dist/style.css";

import CodeEditor from "./components/CodeEditor";
import DiagnosticsPanel from "./components/DiagnosticsPanel";
import FlowList from "./components/FlowList";
import FlowNode from "./components/FlowNode";
import Inspector from "./components/Inspector";
import NodePalette from "./components/NodePalette";
import SaveStatus from "./components/SaveStatus";
import { toGraph, type CanvasNode } from "./graph";
import { ETAG_POLL_MS, useAppStore } from "./store";

const nodeTypes = { napflow: FlowNode };

function Canvas() {
  const {
    detail,
    detailError,
    graphVersion,
    selectNode,
    moveNode,
    connectEdge,
    deleteEdges,
    deleteNode,
    setInteracting,
  } = useAppStore();

  // xyflow holds interactive state (drag positions, selection); the
  // store's model stays authoritative — graphVersion bumps rebuild
  // from it after structural edits or external reloads
  const [nodes, setNodes] = useState<CanvasNode[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const identity = detail?.identity ?? null;
  useEffect(() => {
    if (detail !== null) {
      const graph = toGraph(detail);
      setNodes(graph.nodes);
      setEdges(graph.edges);
    }
    // rebuild on flow switch or explicit invalidation only — NOT on
    // every autosaved detail replacement (drag positions would snap)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [identity, graphVersion]);

  const onNodesChange = useCallback(
    (changes: NodeChange<CanvasNode>[]) => {
      setNodes((current) => applyNodeChanges(changes, current));
      for (const change of changes) {
        if (change.type === "remove") deleteNode(change.id);
      }
    },
    [deleteNode],
  );

  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      setEdges((current) => applyEdgeChanges(changes, current));
      const gone = changes
        .filter((c) => c.type === "remove")
        .map((c) => {
          const [from, to] = c.id.split("→");
          return { from, to };
        })
        .filter((e) => e.from && e.to);
      if (gone.length > 0) deleteEdges(gone);
    },
    [deleteEdges],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      if (
        connection.source &&
        connection.sourceHandle &&
        connection.target &&
        connection.targetHandle
      ) {
        connectEdge(
          `${connection.source}.${connection.sourceHandle}`,
          `${connection.target}.${connection.targetHandle}`,
        );
      }
    },
    [connectEdge],
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
  if (detail === null) {
    return <div style={{ flex: 1 }} data-testid="canvas" />;
  }
  return (
    <div style={{ flex: 1, minWidth: 0, position: "relative" }} data-testid="canvas">
      <ReactFlow
        key={detail.identity} // remount per flow: fresh fitView
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeDragStart={() => setInteracting(true)}
        onNodeDragStop={(_event, node) => {
          setInteracting(false);
          moveNode(node.id, Math.round(node.position.x), Math.round(node.position.y));
        }}
        onNodeClick={(_event, node) => selectNode(node.id)}
        onPaneClick={() => selectNode(null)}
        deleteKeyCode={["Backspace", "Delete"]}
      >
        <Background />
        <Controls />
      </ReactFlow>
      <NodePalette />
    </div>
  );
}

export default function App() {
  const { workspace, error, detail, load, pollEtags } = useAppStore();
  const [codeOpen, setCodeOpen] = useState(false);

  useEffect(() => {
    void load();
  }, [load]);

  // FR-1004 v1: poll etags; external edits live-reload while clean
  useEffect(() => {
    const timer = setInterval(() => void pollEtags(), ETAG_POLL_MS);
    return () => clearInterval(timer);
  }, [pollEtags]);

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
        <span style={{ flex: 1 }} />
        {detail && (
          <button
            data-testid="open-code"
            onClick={() => setCodeOpen(true)}
            style={{
              fontSize: 12,
              padding: "2px 10px",
              cursor: "pointer",
              fontFamily: "inherit",
            }}
          >
            nodes.py
          </button>
        )}
        <SaveStatus />
      </header>
      <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
        <FlowList />
        <Canvas />
        <Inspector />
      </div>
      <DiagnosticsPanel diagnostics={detail?.diagnostics ?? []} />
      {codeOpen && detail && (
        <CodeEditor
          identity={detail.identity}
          onClose={() => setCodeOpen(false)}
        />
      )}
    </div>
  );
}
