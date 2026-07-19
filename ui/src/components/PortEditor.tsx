import { useEffect, useState } from "react";

import { useAppStore } from "../store";

// FR-1006 edit half: Start ports as a key-value list (name/type/
// default), End ports as name + required flag. Port rows edit the
// node's config.ports in place; the store autosaves.

const PORT_TYPES = ["any", "string", "number", "boolean", "object", "list"];

type StartPort = { name: string; type?: string; default?: unknown };
type EndPort = { name: string; required?: boolean };

function isTemplateSource(text: string): boolean {
  return text.includes("{{") || text.includes("{%");
}

/** Parse the default cell's text per the port's declared type
 * (M4 leftover: the cell wrote strings only). Returns {ok:false} when
 * the text doesn't fit the type — the cell stays local and turns red.
 * Also the parser behind the run-inputs popover (S4/M5). */
export function parseDefault(
  text: string,
  type: string,
  allowTemplate = true,
): { ok: true; value: unknown } | { ok: false } {
  // Typed Start defaults are evaluated before post-render coercion (D25).
  // Keep template source as a string even for number/bool/object/list ports;
  // plain non-template text must still fit the declared native type.
  if (
    allowTemplate &&
    type !== "string" &&
    type !== "any" &&
    isTemplateSource(text)
  ) {
    return { ok: true, value: text };
  }
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

/** Type-aware default cell: local text state, committed on blur; a value that
 * doesn't parse for the declared type stays local (red border). The explicit
 * checkbox separates an absent default (required input) from the valid native
 * empty-string default accepted by string/any ports. */
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
  const hasModelDefault = Object.prototype.hasOwnProperty.call(port, "default");
  const [text, setText] = useState(shown);
  const [bad, setBad] = useState(false);
  const [enabled, setEnabled] = useState(hasModelDefault);
  useEffect(() => {
    setText(shown);
    setBad(false);
    setEnabled(hasModelDefault);
    // re-sync when the model value OR the declared type changes (a
    // type switch re-validates the same text on next blur)
  }, [shown, type, hasModelDefault]);

  return (
    <span style={{ display: "flex", flex: 2, gap: 3, alignItems: "center" }}>
      <input
        data-testid={`start-port-default-${index}`}
        className={`nf-input nodrag${bad ? " nf-bad" : ""}`}
        style={{ flex: 1 }}
        value={text}
        placeholder={enabled ? "(empty value)" : "(required)"}
        title={`default value (${type}); use the checkbox to distinguish empty from required`}
        onChange={(e) => {
          setText(e.target.value);
          if (e.target.value !== "") setEnabled(true);
        }}
        onBlur={() => {
          if (!enabled) return;
          const parsed = parseDefault(text, type);
          if (parsed.ok) {
            setBad(false);
            onCommit(parsed.value);
          } else {
            setBad(true); // stays local until it fits the type
          }
        }}
      />
      <label
        title="default is present; unchecked means this input is required"
        className="nodrag"
        style={{ fontSize: 10, whiteSpace: "nowrap", color: "var(--muted)" }}
      >
        <input
          data-testid={`start-port-default-enabled-${index}`}
          type="checkbox"
          checked={enabled}
          onChange={(event) => {
            const checked = event.target.checked;
            setEnabled(checked);
            setBad(false);
            if (!checked) {
              setText("");
              onCommit(undefined);
            } else if (text === "" && (type === "string" || type === "any")) {
              onCommit("");
            }
          }}
        />
        default
      </label>
    </span>
  );
}

function usePorts(nodeId: string): {
  ports: Record<string, unknown>[];
  setPorts: (
    ports: Record<string, unknown>[],
    historyGroup?: string,
  ) => void;
} {
  const detail = useAppStore((s) => s.detail);
  const updateNodeConfig = useAppStore((s) => s.updateNodeConfig);
  const node = detail?.flow.nodes.find((n) => n.id === nodeId);
  const config = (node?.config ?? {}) as Record<string, unknown>;
  const ports = (config.ports ?? []) as Record<string, unknown>[];
  return {
    ports,
    setPorts: (next, historyGroup) =>
      updateNodeConfig(
        nodeId,
        { ...config, ports: next },
        historyGroup === undefined ? undefined : `ports:${historyGroup}`,
      ),
  };
}

export function StartPortEditor({ nodeId }: { nodeId: string }) {
  const { ports, setPorts } = usePorts(nodeId);
  const list = ports as StartPort[];

  const update = (
    index: number,
    patch: Partial<StartPort>,
    historyGroup?: string,
  ) => {
    setPorts(
      list.map((p, i) => {
        if (i !== index) return { ...p };
        const next = { ...p, ...patch } as Record<string, unknown>;
        // Only an undefined patch removes the default (required input); the
        // explicit checkbox lets a native empty-string default remain "".
        if ("default" in patch && patch.default === undefined) {
          delete next.default;
        }
        if (patch.type === "any") delete next.type; // model default
        return next;
      }),
      historyGroup,
    );
  };

  return (
    <div data-testid="start-ports">
      <p style={{ fontSize: 11, color: "var(--muted)", margin: "0 0 4px" }}>
        flow inputs (bind via <code>napf run -i key=value</code>)
      </p>
      {list.map((port, index) => (
        <div key={index} style={{ display: "flex", gap: 4, marginBottom: 4 }}>
          <input
            data-testid={`start-port-name-${index}`}
            className="nf-input nodrag" style={{ flex: 2 }}
            value={port.name}
            placeholder="name"
            onChange={(e) =>
              update(
                index,
                { name: e.target.value },
                `row:${index}:name`,
              )
            }
          />
          <select
            data-testid={`start-port-type-${index}`}
            className="nf-select nodrag" style={{ flex: 1.4 }}
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
            className="nf-btn nodrag"
            style={{ padding: "2px 7px" }}
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
        className="nf-btn nodrag"
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
      <p style={{ fontSize: 11, color: "var(--muted)", margin: "0 0 4px" }}>
        flow outputs (required + unreached ⇒ run failed, D18)
      </p>
      {list.map((port, index) => (
        <div
          key={index}
          style={{ display: "flex", gap: 6, marginBottom: 4, alignItems: "center" }}
        >
          <input
            data-testid={`end-port-name-${index}`}
            className="nf-input nodrag" style={{ flex: 2 }}
            value={port.name}
            placeholder="name"
            onChange={(e) =>
              setPorts(
                list.map((p, i) =>
                  i === index ? { ...p, name: e.target.value } : { ...p },
                ),
                `row:${index}:name`,
              )
            }
          />
          <label className="nodrag" style={{ fontSize: 11, whiteSpace: "nowrap" }}>
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
            className="nf-btn nodrag"
            style={{ padding: "2px 7px" }}
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
        className="nf-btn nodrag"
      >
        + output
      </button>
    </div>
  );
}
