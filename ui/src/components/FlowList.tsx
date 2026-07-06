import { useAppStore } from "../store";

export default function FlowList() {
  const { flows, selectedFlow, openFlow } = useAppStore();
  return (
    <nav
      data-testid="flow-list"
      style={{
        width: 220,
        borderRight: "1px solid #ddd",
        overflowY: "auto",
        padding: "0.5rem 0",
        flexShrink: 0,
      }}
    >
      {flows.map((flow) => {
        const active = flow.identity === selectedFlow;
        return (
          <button
            key={flow.identity}
            data-testid="flow-item"
            onClick={() => openFlow(flow.identity)}
            style={{
              display: "block",
              width: "100%",
              textAlign: "left",
              padding: "0.4rem 1rem",
              border: "none",
              cursor: "pointer",
              fontSize: 13,
              fontFamily: "inherit",
              background: active ? "#e3f2fd" : "transparent",
              color: flow.valid ? "#222" : "#c62828",
            }}
          >
            {flow.identity}
            {!flow.valid && " ⚠"}
          </button>
        );
      })}
      {flows.length === 0 && (
        <p style={{ padding: "0 1rem", color: "#888", fontSize: 13 }}>
          no flows discovered
        </p>
      )}
    </nav>
  );
}
