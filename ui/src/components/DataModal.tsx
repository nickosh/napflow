import { useState } from "react";
import { ArrowsIn, ArrowsOut, X } from "@phosphor-icons/react";

/** F1 data peek: a port's full last-crossed payload in a modal,
 * expandable to near-fullscreen. Opened from the run inspector's port
 * rows; the console's message rows keep their inline expansion. */
export default function DataModal({
  title,
  json,
  color,
  onClose,
}: {
  title: string;
  json: string;
  color: string;
  onClose: () => void;
}) {
  const [full, setFull] = useState(false);
  return (
    <div
      data-testid="data-modal"
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "var(--scrim)",
        zIndex: 50,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <div
        className="nf-card"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: full ? "92vw" : 480,
          height: full ? "88vh" : 360,
          maxWidth: "92vw",
          maxHeight: "88vh",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          transition: "width 0.25s, height 0.25s",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 9,
            padding: "11px 14px",
            borderBottom: "1px solid var(--border)",
          }}
        >
          <span
            style={{
              width: 10,
              height: 10,
              borderRadius: 2,
              background: color,
              flexShrink: 0,
            }}
          />
          <span style={{ fontWeight: 500, fontSize: 12.5, flex: 1 }}>
            {title}
          </span>
          <button
            className="nf-iconbtn"
            title={full ? "Shrink" : "Expand"}
            onClick={() => setFull(!full)}
          >
            {full ? <ArrowsIn size={14} /> : <ArrowsOut size={14} />}
          </button>
          <button
            data-testid="data-modal-close"
            className="nf-iconbtn"
            title="Close"
            onClick={onClose}
          >
            <X size={14} />
          </button>
        </div>
        <pre
          style={{
            flex: 1,
            overflow: "auto",
            margin: 0,
            padding: "14px 16px",
            fontFamily: "var(--mono)",
            fontSize: 11.5,
            lineHeight: 1.7,
            userSelect: "text",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {json}
        </pre>
      </div>
    </div>
  );
}
