import { useState } from "react";

import { NODE_TYPES, PALETTE_DRAG_TYPE } from "../forms";
import { useAppStore } from "../store";

// Add-node control: a small overlay button opening the v1 catalog.
// Click adds below the graph (keyboard-friendly); dragging an entry
// onto the canvas adds at the drop position.
export default function NodePalette() {
  const addNode = useAppStore((s) => s.addNode);
  const [open, setOpen] = useState(false);

  return (
    <div style={{ position: "absolute", top: 10, left: 10, zIndex: 5 }}>
      <button
        data-testid="add-node"
        onClick={() => setOpen((o) => !o)}
        style={{
          fontSize: 13,
          padding: "3px 10px",
          cursor: "pointer",
          fontFamily: "inherit",
          background: "#fff",
          border: "1px solid #bbb",
          borderRadius: 4,
          boxShadow: "0 1px 3px rgba(0,0,0,0.12)",
        }}
      >
        + node
      </button>
      {open && (
        <div
          data-testid="node-palette"
          style={{
            marginTop: 4,
            background: "#fff",
            border: "1px solid #bbb",
            borderRadius: 4,
            boxShadow: "0 2px 6px rgba(0,0,0,0.15)",
            maxHeight: 320,
            overflowY: "auto",
          }}
        >
          {NODE_TYPES.map((type) => (
            <button
              key={type}
              data-testid={`palette-${type}`}
              draggable
              onDragStart={(e) => {
                e.dataTransfer.setData(PALETTE_DRAG_TYPE, type);
                e.dataTransfer.effectAllowed = "copy";
              }}
              onDragEnd={() => setOpen(false)}
              onClick={() => {
                addNode(type);
                setOpen(false);
              }}
              style={{
                display: "block",
                width: "100%",
                textAlign: "left",
                padding: "4px 14px",
                border: "none",
                background: "transparent",
                cursor: "grab",
                fontSize: 13,
                fontFamily: "inherit",
              }}
            >
              {type}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
