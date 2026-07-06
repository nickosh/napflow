import { useAppStore } from "../store";

// FR-1006 edit half: Start ports as a key-value list (name/type/
// default), End ports as name + required flag. Port rows edit the
// node's config.ports in place; the store autosaves.

const PORT_TYPES = ["any", "string", "number", "boolean", "object", "list"];

const cellInput: React.CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  fontSize: 12,
  fontFamily: "ui-monospace, monospace",
  padding: "2px 5px",
  border: "1px solid #ccc",
  borderRadius: 3,
};

type StartPort = { name: string; type?: string; default?: unknown };
type EndPort = { name: string; required?: boolean };

function usePorts(nodeId: string): {
  ports: Record<string, unknown>[];
  setPorts: (ports: Record<string, unknown>[]) => void;
} {
  const detail = useAppStore((s) => s.detail);
  const updateNodeConfig = useAppStore((s) => s.updateNodeConfig);
  const node = detail?.flow.nodes.find((n) => n.id === nodeId);
  const config = (node?.config ?? {}) as Record<string, unknown>;
  const ports = (config.ports ?? []) as Record<string, unknown>[];
  return {
    ports,
    setPorts: (next) => updateNodeConfig(nodeId, { ...config, ports: next }),
  };
}

export function StartPortEditor({ nodeId }: { nodeId: string }) {
  const { ports, setPorts } = usePorts(nodeId);
  const list = ports as StartPort[];

  const update = (index: number, patch: Partial<StartPort>) => {
    setPorts(
      list.map((p, i) => {
        if (i !== index) return { ...p };
        const next = { ...p, ...patch } as Record<string, unknown>;
        // absent default = required input; empty string in the default
        // cell means "no default", not a "" default
        if ("default" in patch && patch.default === undefined) {
          delete next.default;
        }
        if (patch.type === "any") delete next.type; // model default
        return next;
      }),
    );
  };

  return (
    <div data-testid="start-ports">
      <p style={{ fontSize: 11, color: "#666", margin: "8px 0 4px" }}>
        flow inputs (bind via <code>napf run -i key=value</code>)
      </p>
      {list.map((port, index) => (
        <div key={index} style={{ display: "flex", gap: 4, marginBottom: 4 }}>
          <input
            data-testid={`start-port-name-${index}`}
            style={{ ...cellInput, flex: 2 }}
            value={port.name}
            placeholder="name"
            onChange={(e) => update(index, { name: e.target.value })}
          />
          <select
            data-testid={`start-port-type-${index}`}
            style={{ ...cellInput, flex: 1.4 }}
            value={port.type ?? "any"}
            onChange={(e) => update(index, { type: e.target.value })}
          >
            {PORT_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
          <input
            data-testid={`start-port-default-${index}`}
            style={{ ...cellInput, flex: 2 }}
            value={port.default === undefined ? "" : String(port.default)}
            placeholder="(required)"
            title="default value; empty = required at bind"
            onChange={(e) =>
              update(index, {
                default: e.target.value === "" ? undefined : e.target.value,
              })
            }
          />
          <button
            data-testid={`start-port-remove-${index}`}
            onClick={() => setPorts(list.filter((_, i) => i !== index))}
            style={{ cursor: "pointer", fontFamily: "inherit" }}
          >
            ×
          </button>
        </div>
      ))}
      <button
        data-testid="start-port-add"
        onClick={() =>
          setPorts([...list, { name: `input${list.length + 1}` }])
        }
        style={{ fontSize: 12, cursor: "pointer", fontFamily: "inherit" }}
      >
        + input
      </button>
    </div>
  );
}

export function EndPortEditor({ nodeId }: { nodeId: string }) {
  const { ports, setPorts } = usePorts(nodeId);
  const list = ports as EndPort[];

  return (
    <div data-testid="end-ports">
      <p style={{ fontSize: 11, color: "#666", margin: "8px 0 4px" }}>
        flow outputs (required + unreached ⇒ run failed, D18)
      </p>
      {list.map((port, index) => (
        <div
          key={index}
          style={{ display: "flex", gap: 6, marginBottom: 4, alignItems: "center" }}
        >
          <input
            data-testid={`end-port-name-${index}`}
            style={{ ...cellInput, flex: 2 }}
            value={port.name}
            placeholder="name"
            onChange={(e) =>
              setPorts(
                list.map((p, i) =>
                  i === index ? { ...p, name: e.target.value } : { ...p },
                ),
              )
            }
          />
          <label style={{ fontSize: 11, whiteSpace: "nowrap" }}>
            <input
              data-testid={`end-port-required-${index}`}
              type="checkbox"
              checked={port.required !== false}
              onChange={(e) =>
                setPorts(
                  list.map((p, i) => {
                    if (i !== index) return { ...p };
                    const next: Record<string, unknown> = { ...p };
                    // true is the model default — leave it unset
                    if (e.target.checked) delete next.required;
                    else next.required = false;
                    return next as EndPort;
                  }),
                )
              }
            />{" "}
            required
          </label>
          <button
            data-testid={`end-port-remove-${index}`}
            onClick={() => setPorts(list.filter((_, i) => i !== index))}
            style={{ cursor: "pointer", fontFamily: "inherit" }}
          >
            ×
          </button>
        </div>
      ))}
      <button
        data-testid="end-port-add"
        onClick={() =>
          setPorts([...list, { name: `out${list.length + 1}` }])
        }
        style={{ fontSize: 12, cursor: "pointer", fontFamily: "inherit" }}
      >
        + output
      </button>
    </div>
  );
}
