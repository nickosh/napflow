import { CaretRight } from "@phosphor-icons/react";

import { flowPath } from "../identity";
import { useAppStore } from "../store";
import FlowList from "./FlowList";
import SaveStatus from "./SaveStatus";

/** F1 top-left cluster: flows menu, workspace › flow breadcrumb with
 * the autosave indicator, D09's "used in N places" links, and load
 * errors — the old header row condensed into floating chrome. */
export default function TopLeftBar() {
  const { workspace, error, detail, selectedFlow, openFlow } = useAppStore();
  const usedBy = detail?.used_by ?? [];
  const places = usedBy.reduce((n, u) => n + u.nodes.length, 0);

  return (
    <div
      style={{
        position: "absolute",
        left: 14,
        top: 14,
        display: "flex",
        gap: 8,
        alignItems: "flex-start",
        zIndex: 20,
      }}
    >
      <FlowList />
      <div className="nf-chip">
        <span data-testid="workspace-name" style={{ color: "var(--muted)" }}>
          {workspace?.name ?? "…"}
        </span>
        {selectedFlow !== null && (
          <>
            <CaretRight size={10} color="var(--muted)" />
            <span style={{ fontWeight: 500 }}>{selectedFlow}</span>
          </>
        )}
        <SaveStatus />
      </div>
      {places > 0 && (
        <div className="nf-chip" data-testid="used-by" style={{ gap: 5 }}>
          <span style={{ color: "var(--muted)" }}>
            used in {places} place{places === 1 ? "" : "s"}:
          </span>
          {usedBy.map((u) => (
            <span key={u.identity} style={{ whiteSpace: "nowrap" }}>
              <a
                data-testid={`used-by-${u.identity}`}
                href={flowPath(u.identity)}
                style={{ color: "var(--accent)" }}
                onClick={(e) => {
                  e.preventDefault();
                  void openFlow(u.identity);
                }}
              >
                {u.identity}
              </a>{" "}
              <span style={{ color: "var(--muted)" }}>
                ({u.nodes.join(", ")})
              </span>
            </span>
          ))}
        </div>
      )}
      {error && (
        <div
          className="nf-chip"
          data-testid="load-error"
          style={{ color: "var(--err)", maxWidth: 360 }}
        >
          <span
            style={{
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {error}
          </span>
        </div>
      )}
    </div>
  );
}
