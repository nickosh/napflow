import { useEffect, useState } from "react";

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

/** Parse the default cell's text per the port's declared type
 * (M4 leftover: the cell wrote strings only). Returns {ok:false} when
 * the text doesn't fit the type — the cell stays local and turns red.
 * Also the parser behind the run-inputs popover (S4/M5). */
export function parseDefault(
  text: string,
  type: string,
): { ok: true; value: unknown } | { ok: false } {
  switch (type) {
    case "string":
      return { ok: true, value: text };
    case "number": {
      const n = Number(text.trim());
      return text.trim() !== "" && Number.isFinite(n)
        ? { ok: true, value: n }
        : { ok: false };
    }
    case "boolean":
      return text === "true" || text === "false"
        ? { ok: true, value: text === "true" }
        : { ok: false };
    case "object":
    case "list": {
      try {
        const value: unknown = JSON.parse(text);
        const isList = Array.isArray(value);
        const fits = type === "list" ? isList : typeof value === "object" && value !== null && !isList;
        return fits ? { ok: true, value } : { ok: false };
      } catch {
        return { ok: false };
      }
    }
    default: {
      // any: JSON when it parses ({"a":1}, 42, true), else the raw text
      try {
        return { ok: true, value: JSON.parse(text) };
      } catch {
        return { ok: true, value: text };
      }
    }
  }
}

function showDefault(value: unknown): string {
  if (value === undefined) return "";
  return typeof value === "string" ? value : JSON.stringify(value);
}

/** Type-aware default cell: local text state, committed on blur; a
 * value that doesn't parse for the declared type stays local (red
 * border) — the model never sees it. Empty = no default (required). */
function DefaultCell({
  port,
  index,
  onCommit,
}: {
  port: StartPort;
  index: number;
  onCommit: (value: unknown | undefined) => void;
}) {
  const type = port.type ?? "any";
  const shown = showDefault(port.default);
  const [text, setText] = useState(shown);
  const [bad, setBad] = useState(false);
  useEffect(() => {
    setText(shown);
    setBad(false);
    // re-sync when the model value OR the declared type changes (a
    // type switch re-validates the same text on next blur)
  }, [shown, type]);

  return (
    <input
      data-testid={`start-port-default-${index}`}
      style={{
        ...cellInput,
        flex: 2,
        borderColor: bad ? "#c62828" : "#ccc",
      }}
      value={text}
      placeholder="(required)"
      title={`default value (${type}); empty = required at bind`}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => {
        if (text === "") {
          setBad(false);
          onCommit(undefined); // absent default = required input
          return;
        }
        const parsed = parseDefault(text, type);
        if (parsed.ok) {
          setBad(false);
          onCommit(parsed.value);
        } else {
          setBad(true); // stays local until it fits the type
        }
      }}
    />
  );
}

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
          <DefaultCell
            port={port}
            index={index}
            onCommit={(value) => update(index, { default: value })}
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
