import { useEffect, useState } from "react";

import { useAppStore } from "../store";

const inputStyle: React.CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  fontSize: 12,
  fontFamily: "ui-monospace, monospace",
  padding: "3px 6px",
  border: "1px solid #ccc",
  borderRadius: 3,
};

/** NodeBase policy shared by every node type. It deliberately sits outside
 * `config`: the YAML key is a sibling of id/type/config (D24). */
export default function NodeSafetyForm({ nodeId }: { nodeId: string }) {
  const node = useAppStore((state) =>
    state.detail?.flow.nodes.find((candidate) => candidate.id === nodeId),
  );
  const updateNodeMaxSeconds = useAppStore(
    (state) => state.updateNodeMaxSeconds,
  );
  const shown =
    node?.max_seconds === undefined || node.max_seconds === null
      ? ""
      : String(node.max_seconds);
  const [text, setText] = useState(shown);
  const [bad, setBad] = useState(false);

  useEffect(() => {
    setText(shown);
    setBad(false);
  }, [shown, nodeId]);

  return (
    <label
      style={{ display: "block", fontSize: 11, color: "#666", margin: "8px 0 2px" }}
    >
      max_seconds <em>(optional per-firing ceiling)</em>
      <input
        data-testid="node-max-seconds"
        style={{ ...inputStyle, borderColor: bad ? "#c62828" : "#ccc" }}
        type="number"
        min="0"
        step="any"
        value={text}
        placeholder="(engine default)"
        onChange={(event) => setText(event.target.value)}
        onBlur={() => {
          if (text.trim() === "") {
            setBad(false);
            updateNodeMaxSeconds(nodeId, undefined);
            return;
          }
          const value = Number(text);
          if (Number.isFinite(value) && value > 0) {
            setBad(false);
            updateNodeMaxSeconds(nodeId, value);
          } else {
            setBad(true);
          }
        }}
      />
    </label>
  );
}
