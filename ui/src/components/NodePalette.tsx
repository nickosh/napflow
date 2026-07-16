import { useState } from "react";
import {
  DotsSixVertical,
  MagnifyingGlass,
  X,
} from "@phosphor-icons/react";

import { PICKER_TABS, nodeMeta } from "../catalog";
import { NODE_TYPES, PALETTE_DRAG_TYPE } from "../forms";
import { useChrome } from "../uiChrome";

/** F1 add-block picker: floating card with search, category tabs and
 * hover descriptions over the REAL node catalog. Click adds below the
 * graph and refits (keyboard-friendly); dragging an entry onto the
 * canvas adds at the drop position (HTML5 dnd, unchanged contract). */
export default function NodePalette({
  onAdd,
}: {
  onAdd: (type: string) => void;
}) {
  const pickerAt = useChrome((s) => s.pickerAt);
  const closePicker = useChrome((s) => s.closePicker);
  const [tab, setTab] = useState<(typeof PICKER_TABS)[number]>("All");
  const [q, setQ] = useState("");
  const [tip, setTip] = useState<{ type: string; y: number } | null>(null);

  if (pickerAt === null) return null;

  const query = q.toLowerCase();
  const rows = NODE_TYPES.filter((type) => {
    const meta = nodeMeta(type);
    return (
      (tab === "All" || meta.category === tab) &&
      (query === "" || type.toLowerCase().includes(query))
    );
  });

  return (
    <div
      data-testid="node-palette"
      className="nf-card"
      style={{
        position: "fixed",
        left: pickerAt.x,
        top: pickerAt.y,
        width: 284,
        zIndex: 30,
        overflow: "hidden",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "9px 12px",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <DotsSixVertical size={14} color="var(--muted)" />
        <span style={{ fontWeight: 500, fontSize: 12.5, flex: 1 }}>
          Add node
        </span>
        <button className="nf-iconbtn" onClick={closePicker} title="Close">
          <X size={13} />
        </button>
      </div>
      <div style={{ padding: "9px 10px 0" }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 7,
            background: "var(--surface2)",
            border: "1px solid var(--border)",
            borderRadius: "var(--rsm)",
            padding: "5px 9px",
          }}
        >
          <MagnifyingGlass size={13} color="var(--muted)" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search nodes…"
            autoFocus
            style={{
              all: "unset",
              flex: 1,
              fontSize: 12,
              color: "var(--text)",
            }}
          />
        </div>
      </div>
      <div style={{ display: "flex", gap: 2, padding: "9px 10px 7px" }}>
        {PICKER_TABS.map((t) => (
          <button
            key={t}
            className={`nf-tab${tab === t ? " nf-active" : ""}`}
            style={{ padding: "4px 9px", fontSize: 11 }}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </div>
      <div style={{ maxHeight: 300, overflowY: "auto", padding: "0 6px 8px" }}>
        {rows.map((type) => {
          const meta = nodeMeta(type);
          const RowIcon = meta.icon;
          return (
            <button
              key={type}
              data-testid={`palette-${type}`}
              className="nf-row"
              draggable
              onDragStart={(e) => {
                e.dataTransfer.setData(PALETTE_DRAG_TYPE, type);
                e.dataTransfer.effectAllowed = "copy";
                setTip(null);
              }}
              onDragEnd={closePicker}
              onClick={() => {
                onAdd(type);
                closePicker();
              }}
              onMouseEnter={(e) =>
                setTip({ type, y: e.currentTarget.getBoundingClientRect().top })
              }
              onMouseLeave={() => setTip(null)}
              style={{ cursor: "grab" }}
            >
              <RowIcon size={15} color="var(--accent)" />
              <span style={{ flex: 1 }}>{type}</span>
              <DotsSixVertical size={12} color="var(--muted)" opacity={0.6} />
            </button>
          );
        })}
        {rows.length === 0 && (
          <p style={{ padding: "4px 8px", color: "var(--muted)", fontSize: 12 }}>
            nothing matches
          </p>
        )}
      </div>
      {tip !== null && (
        <div
          style={{
            position: "fixed",
            left: Math.min(pickerAt.x + 292, window.innerWidth - 225),
            top: tip.y,
            width: 210,
            background: "var(--surface2)",
            border: "1px solid var(--border)",
            borderRadius: "var(--rsm)",
            boxShadow: "var(--shadow)",
            padding: "9px 11px",
            fontSize: 11.5,
            color: "var(--muted)",
            zIndex: 40,
            pointerEvents: "none",
            lineHeight: 1.5,
          }}
        >
          {nodeMeta(tip.type).description}
        </div>
      )}
    </div>
  );
}
