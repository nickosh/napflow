import {
  Background,
  BackgroundVariant,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  applyEdgeChanges,
  applyNodeChanges,
  useNodesInitialized,
  useReactFlow,
  useUpdateNodeInternals,
  type Connection,
  type Edge,
  type EdgeChange,
  type NodeChange,
} from "@xyflow/react";
import { useCallback, useEffect, useRef, useState } from "react";

import "@xyflow/react/dist/style.css";
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "./theme.css";

import CodeEditor from "./components/CodeEditor";
import CommandPalette from "./components/CommandPalette";
import ConnectHint from "./components/ConnectHint";
import ConsoleButton from "./components/ConsoleButton";
import FlowNode from "./components/FlowNode";
import NodePalette from "./components/NodePalette";
import RunControls from "./components/RunControls";
import RunEdge from "./components/RunEdge";
import RunInspector from "./components/RunInspector";
import RunPanel from "./components/RunPanel";
import TopLeftBar from "./components/TopLeftBar";
import TopRightBar from "./components/TopRightBar";
import TrashZone from "./components/TrashZone";
import { PALETTE_DRAG_TYPE } from "./forms";
import {
  drillTarget,
  reconcileGraphNodes,
  toGraph,
  type CanvasNode,
} from "./graph";
import { identityFromPath } from "./identity";
import { persistenceRegistry } from "./persistence";
import { ETAG_POLL_MS, useAppStore } from "./store";
import { tidyPositions } from "./tidy";
import { useChrome } from "./uiChrome";

const nodeTypes = { napflow: FlowNode };
const edgeTypes = { napflow: RunEdge };

function overTrashZone(event: MouseEvent | TouchEvent | React.MouseEvent): boolean {
  const zone = document.querySelector('[data-testid="trash-zone"]');
  if (zone === null) return false;
  const point =
    "clientX" in event
      ? { x: event.clientX, y: event.clientY }
      : event.touches.length > 0
        ? { x: event.touches[0].clientX, y: event.touches[0].clientY }
        : null;
  if (point === null) return false;
  const rect = zone.getBoundingClientRect();
  return (
    point.x >= rect.left &&
    point.x <= rect.right &&
    point.y >= rect.top &&
    point.y <= rect.bottom
  );
}

function Canvas() {
  const {
    detail,
    detailError,
    runFramePath,
    runFrameDetail,
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
  const canvasDetail =
    runFramePath.length > 0 ? runFrameDetail : detail;
  const { fitView, screenToFlowPosition } = useReactFlow();
  const updateNodeInternals = useUpdateNodeInternals();
  const nodesInitialized = useNodesInitialized();
  const minimapOn = useChrome((s) => s.minimapOn);
  const tidyTick = useChrome((s) => s.tidyTick);
  const { setFlowsOpen, closePicker, openPickerAt, setDragging, setOverTrash } =
    useChrome();

  // xyflow holds interactive state (drag positions, selection); the
  // store's model stays authoritative — graphVersion bumps rebuild
  // from it after structural edits or external reloads
  const [nodes, setNodes] = useState<CanvasNode[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [fitAfterAddCount, setFitAfterAddCount] = useState<number | null>(null);
  const [tidying, setTidying] = useState(false);
  const renderedIdentity = useRef<string | null>(null);
  const internalsRefreshFrame = useRef<number | null>(null);
  const lastTidyTick = useRef(0);
  const tidySettle = useRef<number | null>(null);
  const identity = canvasDetail?.identity ?? null;
  const allNodesMeasured = nodes.every(
    (node) =>
      node.measured?.width !== undefined && node.measured.height !== undefined,
  );
  useEffect(() => {
    if (canvasDetail !== null) {
      const graph = toGraph(canvasDetail);
      const previousIdentity = renderedIdentity.current;
      setNodes((current) =>
        reconcileGraphNodes(
          current,
          graph.nodes,
          previousIdentity,
          canvasDetail.identity,
        ),
      );
      setEdges(graph.edges);
      renderedIdentity.current = canvasDetail.identity;

      // Same-id nodes can also change their port/config-driven size. Preserve
      // their last dimensions for the rebuild hand-off, then force a fresh
      // measurement after React commits the rebuilt node contents.
      if (internalsRefreshFrame.current !== null) {
        cancelAnimationFrame(internalsRefreshFrame.current);
      }
      const nodeIds = graph.nodes.map((node) => node.id);
      internalsRefreshFrame.current = requestAnimationFrame(() => {
        updateNodeInternals(nodeIds);
        internalsRefreshFrame.current = null;
      });
    } else {
      setNodes([]);
      setEdges([]);
      renderedIdentity.current = null;
    }
    // rebuild on flow switch or explicit invalidation only — NOT on
    // every autosaved detail replacement (drag positions would snap)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [identity, graphVersion, runFramePath.length, updateNodeInternals]);

  useEffect(
    () => () => {
      if (internalsRefreshFrame.current !== null) {
        cancelAnimationFrame(internalsRefreshFrame.current);
      }
      if (tidySettle.current !== null) {
        clearTimeout(tidySettle.current);
      }
    },
    [],
  );

  // F1 tidy wand: layered auto-layout over the CURRENT graph, animated
  // via a transient transform transition, persisted like any drag
  useEffect(() => {
    if (tidyTick === lastTidyTick.current) return;
    lastTidyTick.current = tidyTick;
    if (inRunMode || nodes.length === 0) return;
    const placed = tidyPositions(
      nodes,
      edges
        .filter((e) => !e.id.startsWith("ghost:"))
        .map((e) => ({ source: e.source, target: e.target })),
    );
    setTidying(true);
    setNodes((current) =>
      current.map((node) =>
        placed[node.id] ? { ...node, position: placed[node.id] } : node,
      ),
    );
    for (const [id, pos] of Object.entries(placed)) {
      moveNode(id, Math.round(pos.x), Math.round(pos.y));
    }
    if (tidySettle.current !== null) clearTimeout(tidySettle.current);
    tidySettle.current = window.setTimeout(() => {
      setTidying(false);
      void fitView({ padding: 0.15 });
      tidySettle.current = null;
    }, 450);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tidyTick]);

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

  const addVisibleNode = useCallback(
    (type: string) => {
      // Keep the store's collision-free below-graph placement, then refit
      // once xyflow has rendered and measured the new node.
      setFitAfterAddCount((canvasDetail?.flow.nodes.length ?? nodes.length) + 1);
      addNode(type);
    },
    [addNode, canvasDetail?.flow.nodes.length, nodes.length],
  );

  useEffect(() => {
    if (
      fitAfterAddCount === null ||
      nodes.length < fitAfterAddCount ||
      !allNodesMeasured ||
      !nodesInitialized
    ) {
      return;
    }
    void fitView({ padding: 0.15 });
    setFitAfterAddCount(null);
  }, [
    allNodesMeasured,
    fitAfterAddCount,
    fitView,
    nodes.length,
    nodesInitialized,
  ]);

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

  if (runFramePath.length === 0 && detailError !== null) {
    // broken flow: no canvas to draw — the E-codes ARE the view
    return (
      <div
        style={{
          position: "absolute",
          inset: 0,
          padding: "70px 1.5rem 1rem",
          overflowY: "auto",
        }}
        data-testid="canvas"
      >
        <p data-testid="detail-error" style={{ color: "var(--err)" }}>
          {detailError.message}
        </p>
        <ul data-testid="detail-error-diagnostics" style={{ fontSize: 13 }}>
          {detailError.diagnostics.map((d, i) => (
            <li key={i} style={{ marginBottom: 4 }}>
              <strong>{d.code}</strong> {d.message}{" "}
              <em style={{ color: "var(--muted)" }}>({d.hint})</em>
            </li>
          ))}
        </ul>
      </div>
    );
  }
  if (canvasDetail === null) {
    return <div style={{ position: "absolute", inset: 0 }} data-testid="canvas" />;
  }
  return (
    <div
      className={tidying ? "nf-tidying" : undefined}
      style={{ position: "absolute", inset: 0 }}
      data-testid="canvas"
      onDoubleClick={(event) => {
        // double-clicking empty canvas opens the add-node picker there
        // (node double-click stays drill-in, FR-1007)
        if (
          !inRunMode &&
          (event.target as HTMLElement).classList?.contains("react-flow__pane")
        ) {
          openPickerAt(event.clientX, event.clientY);
        }
      }}
    >
      <ReactFlow
        key={`${canvasDetail.identity}:${runFramePath.at(-1)?.frame ?? "root"}`}
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
        onNodeDragStart={(_event, node) => {
          setInteracting(true);
          setDragging(node.id);
        }}
        onNodeDrag={(event, _node) => {
          setOverTrash(overTrashZone(event));
        }}
        onNodeDragStop={(event, node) => {
          setInteracting(false);
          setDragging(null);
          if (overTrashZone(event)) {
            deleteNode(node.id);
          } else {
            moveNode(node.id, Math.round(node.position.x), Math.round(node.position.y));
          }
        }}
        onNodeClick={(_event, node) => selectNode(node.id)}
        onNodeDoubleClick={(_event, node) => {
          // drill-in (FR-1007, D09): pure navigation into the
          // referenced flow; browser back returns (popstate)
          if (!inRunMode) {
            const target = drillTarget(node.data);
            if (target !== null) void openFlow(target);
          }
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
        onPaneClick={() => {
          selectNode(null);
          setFlowsOpen(false);
          closePicker();
        }}
        onDragOver={inRunMode ? undefined : onDragOver}
        onDrop={inRunMode ? undefined : onDrop}
        deleteKeyCode={inRunMode ? null : ["Backspace", "Delete"]}
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={24}
          size={1.5}
          color="var(--grid-dot)"
        />
        {minimapOn && <MiniMap pannable zoomable />}
        <ConnectHint />
      </ReactFlow>
      {!inRunMode && <NodePalette onAdd={addVisibleNode} />}
    </div>
  );
}

export default function App() {
  const { detail, load, popFlow, pollEtags } = useAppStore();
  const inRunMode = useAppStore((s) => s.runView !== null);
  const theme = useChrome((s) => s.theme);
  const codeOpen = useChrome((s) => s.codeOpen);
  const {
    setCodeOpen,
    setCmdkOpen,
    setFlowsOpen,
    closePicker,
  } = useChrome();

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

  // F1: keyboard chrome — ⌘K palette, Escape unwinds overlays
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setCmdkOpen(!useChrome.getState().cmdkOpen);
      } else if (event.key === "Escape") {
        const chrome = useChrome.getState();
        if (chrome.cmdkOpen) setCmdkOpen(false);
        else if (chrome.runPopoverOpen) chrome.setRunPopoverOpen(false);
        else if (chrome.pickerAt !== null) closePicker();
        else if (chrome.flowsOpen) setFlowsOpen(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [closePicker, setCmdkOpen, setFlowsOpen]);

  return (
    <ReactFlowProvider>
      <div className="nf-root" data-th={theme}>
        <div className="nf-canvas-wrap">
          <Canvas />
          <TopLeftBar />
          <TopRightBar />
          <RunControls />
          <ConsoleButton />
          <TrashZone />
          {/* run mode: the edit forms give way to the run inspector —
              selected node's run data (M5.5); node clicks also filter
              the event stream */}
          {inRunMode && <RunInspector />}
        </div>
        <RunPanel />
        <CommandPalette />
        {codeOpen && detail && (
          <CodeEditor
            identity={detail.identity}
            onClose={() => setCodeOpen(false)}
          />
        )}
      </div>
    </ReactFlowProvider>
  );
}
