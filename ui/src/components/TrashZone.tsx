import { Trash } from "@phosphor-icons/react";

import { useChrome } from "../uiChrome";

/** F1: drop a dragged node here to delete it (Delete/Backspace still
 * work). The Canvas drag handlers own the hit-testing against this
 * element's rect and the actual deletion. */
export default function TrashZone() {
  const draggingNode = useChrome((s) => s.draggingNode);
  const overTrash = useChrome((s) => s.overTrash);
  if (draggingNode === null) return null;
  return (
    <div
      data-testid="trash-zone"
      style={{
        position: "absolute",
        left: "50%",
        transform: "translateX(-50%)",
        bottom: 72,
        width: 250,
        height: 58,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 9,
        border: `1.5px dashed ${overTrash ? "var(--err-bright)" : "var(--border)"}`,
        borderRadius: "var(--radius)",
        background: overTrash ? "rgba(255, 107, 107, 0.12)" : "var(--surface)",
        color: overTrash ? "var(--err-bright)" : "var(--muted)",
        fontSize: 12.5,
        zIndex: 25,
        transition: "background 0.15s, border-color 0.15s",
        pointerEvents: "none",
      }}
    >
      <Trash size={17} />
      Drop here to delete
    </div>
  );
}
