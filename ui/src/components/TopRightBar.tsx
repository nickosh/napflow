import {
  CornersOut,
  FileCode,
  MagnifyingGlass,
  MapTrifold,
  Minus,
  Moon,
  Plus,
  Sun,
} from "@phosphor-icons/react";
import { useReactFlow, useViewport } from "@xyflow/react";

import { useAppStore } from "../store";
import { useChrome } from "../uiChrome";

function ZoomCluster() {
  const { zoomIn, zoomOut, fitView } = useReactFlow();
  const { zoom } = useViewport();
  return (
    <div className="nf-chip" style={{ padding: 0, gap: 0 }}>
      <button
        className="nf-iconbtn"
        title="Zoom out"
        style={{ padding: "0 9px", height: "100%" }}
        onClick={() => void zoomOut()}
      >
        <Minus size={12} />
      </button>
      <span
        style={{
          fontSize: 11,
          color: "var(--muted)",
          minWidth: 36,
          textAlign: "center",
        }}
      >
        {Math.round(zoom * 100)}%
      </span>
      <button
        className="nf-iconbtn"
        title="Zoom in"
        style={{ padding: "0 9px", height: "100%" }}
        onClick={() => void zoomIn()}
      >
        <Plus size={12} />
      </button>
      <button
        className="nf-iconbtn"
        title="Fit View"
        aria-label="Fit View"
        style={{ padding: "0 9px", height: "100%" }}
        onClick={() => void fitView({ padding: 0.15 })}
      >
        <CornersOut size={13} />
      </button>
    </div>
  );
}

/** F1 top-right cluster: ⌘K, zoom, minimap + theme toggles, and the
 * nodes.py editor (kept reachable in run mode — the header used to
 * carry it). */
export default function TopRightBar() {
  const detail = useAppStore((s) => s.detail);
  const {
    minimapOn,
    toggleMinimap,
    theme,
    toggleTheme,
    setCmdkOpen,
    setCodeOpen,
  } = useChrome();

  return (
    <div
      style={{
        position: "absolute",
        right: 14,
        top: 14,
        display: "flex",
        gap: 8,
        alignItems: "center",
        zIndex: 20,
      }}
    >
      <button
        className="nf-chip"
        title="Jump to flow or action"
        onClick={() => setCmdkOpen(true)}
      >
        <MagnifyingGlass size={14} />
        ⌘K
      </button>
      <ZoomCluster />
      <button
        className="nf-chip nf-chip-icon"
        title="Minimap"
        aria-pressed={minimapOn}
        onClick={toggleMinimap}
        style={{ color: minimapOn ? "var(--accent)" : "var(--muted)" }}
      >
        <MapTrifold size={15} />
      </button>
      {detail && (
        <button
          data-testid="open-code"
          className="nf-chip"
          title="Edit this flow's nodes.py"
          onClick={() => setCodeOpen(true)}
        >
          <FileCode size={15} />
          nodes.py
        </button>
      )}
      <button
        className="nf-chip nf-chip-icon"
        title="Theme"
        onClick={toggleTheme}
      >
        {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
      </button>
    </div>
  );
}
