import {
  Background,
  Controls,
  ReactFlow,
  ReactFlowProvider,
  applyEdgeChanges,
  applyNodeChanges,
  useReactFlow,
  type Connection,
  type Edge,
  type EdgeChange,
  type NodeChange,
} from "@xyflow/react";
import { useCallback, useEffect, useState } from "react";

import "@xyflow/react/dist/style.css";
import "./run.css";

import CodeEditor from "./components/CodeEditor";
import ConnectHint from "./components/ConnectHint";
import DiagnosticsPanel from "./components/DiagnosticsPanel";
import FlowList from "./components/FlowList";
import FlowNode from "./components/FlowNode";
import Inspector from "./components/Inspector";
import NodePalette from "./components/NodePalette";
import RunControls from "./components/RunControls";
import RunEdge from "./components/RunEdge";
import RunInspector from "./components/RunInspector";
import RunPanel from "./components/RunPanel";
import SaveStatus from "./components/SaveStatus";
import { PALETTE_DRAG_TYPE } from "./forms";
import { drillTarget, toGraph, type CanvasNode } from "./graph";
import { identityFromPath } from "./identity";
import { persistenceRegistry } from "./persistence";
import { ETAG_POLL_MS, useAppStore } from "./store";

const nodeTypes = { napflow: FlowNode };
const edgeTypes = { napflow: RunEdge };

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
    addNode,
    setInteracting,
    selectRunTraffic,
    openFlow,
  } = useAppStore();
  // run mode (S4/M5): the canvas locks editing and animates instead —
  // clicks still select (they filter the event stream)
  const inRunMode = useAppStore((s) => s.runView !== null);
  const { screenToFlowPosition } = useReactFlow();

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

  const onDragOver = useCallback((event: React.DragEvent) => {
    if (event.dataTransfer.types.includes(PALETTE_DRAG_TYPE)) {
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
    }
  }, []);

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      const type = event.dataTransfer.getData(PALETTE_DRAG_TYPE);
      if (type === "") return;
      event.preventDefault();
      const at = screenToFlowPosition({
        x: event.clientX,
        y: event.clientY,
      });
      addNode(type, [Math.round(at.x), Math.round(at.y)]);
    },
    [addNode, screenToFlowPosition],
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
        edgeTypes={edgeTypes}
        fitView
        zoomOnDoubleClick={false} // double-click means drill-in (M6)
        nodesDraggable={!inRunMode}
        nodesConnectable={!inRunMode}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeDragStart={() => setInteracting(true)}
        onNodeDragStop={(_event, node) => {
          setInteracting(false);
          moveNode(node.id, Math.round(node.position.x), Math.round(node.position.y));
        }}
        onNodeClick={(_event, node) => selectNode(node.id)}
        onNodeDoubleClick={(_event, node) => {
          // drill-in (FR-1007, D09): pure navigation into the
          // referenced flow; browser back returns (popstate)
          const target = drillTarget(node.data);
          if (target !== null) void openFlow(target);
        }}
        onEdgeClick={(_event, edge) => {
          // run mode: a wire click lists its crossed messages (M5.5)
          if (inRunMode && edge.data) {
            selectRunTraffic({
              kind: "edge",
              from: String(edge.data.from),
              to: String(edge.data.to),
            });
          }
        }}
        onPaneClick={() => selectNode(null)}
        onDragOver={inRunMode ? undefined : onDragOver}
        onDrop={inRunMode ? undefined : onDrop}
        deleteKeyCode={inRunMode ? null : ["Backspace", "Delete"]}
      >
        <Background />
        <Controls />
        <ConnectHint />
      </ReactFlow>
      {!inRunMode && <NodePalette />}
    </div>
  );
}

export default function App() {
  const { workspace, error, detail, load, popFlow, pollEtags } = useAppStore();
  const inRunMode = useAppStore((s) => s.runView !== null);
  const runPanelOpen = useAppStore((s) => s.runPanelTab !== null);
  const [codeOpen, setCodeOpen] = useState(false);

  useEffect(() => {
    void load();
  }, [load]);

  // Browser back/forward is a same-document navigation, so it must cross the
  // same save barrier as sidebar and drill-in navigation.
  useEffect(() => {
    const onPopState = (event: PopStateEvent) => {
      const identity = identityFromPath(window.location.pathname);
      const index = Number.isInteger(event.state?.napflowIndex)
        ? event.state.napflowIndex
        : null;
      void popFlow(identity, index);
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, [popFlow]);

  // Async PUT/ETag handshakes cannot be made reliable from beforeunload
  // (sendBeacon is POST-only and keepalive bodies are bounded). Prompt while
  // any accepted edit is debounced, saving, conflicted, or errored.
  useEffect(() => {
    let attached = false;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    const unsubscribe = persistenceRegistry.subscribe((pending) => {
      if (pending && !attached) {
        window.addEventListener("beforeunload", beforeUnload);
        attached = true;
      } else if (!pending && attached) {
        window.removeEventListener("beforeunload", beforeUnload);
        attached = false;
      }
    });
    return () => {
      unsubscribe();
      if (attached) window.removeEventListener("beforeunload", beforeUnload);
    };
  }, []);

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
        <RunControls />
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
        <ReactFlowProvider>
          <Canvas />
        </ReactFlowProvider>
        {/* run mode: the edit forms give way to the run inspector —
            selected node's run data (M5.5); node clicks also filter
            the event stream */}
        {inRunMode ? <RunInspector /> : <Inspector />}
      </div>
      {runPanelOpen ? (
        <RunPanel />
      ) : (
        <DiagnosticsPanel diagnostics={detail?.diagnostics ?? []} />
      )}
      {codeOpen && detail && (
        <CodeEditor
          identity={detail.identity}
          onClose={() => setCodeOpen(false)}
        />
      )}
    </div>
  );
}
