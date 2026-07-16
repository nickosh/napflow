import { useEffect, useState } from "react";

// Structured row editors for assert.checks and switch.cases (M4
// leftover — these edited as raw-JSON textareas). Same pattern as
// PortEditor: rows edit the config list in place, the store autosaves.
// Both lists are min_length=1 in the model, so the last row can't be
// removed.

const rowStyle: React.CSSProperties = {
  display: "flex",
  gap: 4,
  marginBottom: 4,
  alignItems: "center",
};

/** Display a value the way ValueCell will parse it back: plain words
 * raw, everything JSON-ambiguous (numbers, booleans, quoted strings,
 * containers) as JSON. */
function showValue(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") {
    try {
      JSON.parse(value);
      return JSON.stringify(value); // "true"/"200" need their quotes
    } catch {
      return value;
    }
  }
  return JSON.stringify(value);
}

function parseValue(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text; // bare words are strings — nobody quotes "ready"
  }
}

/** Free-typed value cell: local text, committed on blur via
 * JSON-parse-with-string-fallback (never rejects). */
function ValueCell({
  value,
  onCommit,
  testId,
  placeholder,
  flex = 2,
}: {
  value: unknown;
  onCommit: (value: unknown) => void;
  testId: string;
  placeholder?: string;
  flex?: number;
}) {
  const shown = showValue(value);
  const [text, setText] = useState(shown);
  useEffect(() => setText(shown), [shown]);
  return (
    <input
      data-testid={testId}
      className="nf-input nodrag" style={{ flex }}
      value={text}
      placeholder={placeholder}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => onCommit(parseValue(text))}
    />
  );
}

function RemoveButton({
  onClick,
  disabled,
  testId,
}: {
  onClick: () => void;
  disabled: boolean;
  testId: string;
}) {
  return (
    <button
      data-testid={testId}
      onClick={onClick}
      disabled={disabled}
      title={disabled ? "at least one row is required" : "remove"}
      className="nf-btn nodrag"
      style={{ cursor: disabled ? "default" : "pointer", padding: "2px 7px" }}
    >
      ×
    </button>
  );
}



// ---------------------------------------------------------------- checks

type Check = Record<string, unknown> & { kind?: string };

const CHECK_KINDS = ["status", "expr", "response_time"] as const;
const EXPR_OPS = [
  "present",
  "equals",
  "not_equals",
  "contains",
  "matches",
  "gt",
  "lt",
] as const;

function freshCheck(kind: string): Check {
  switch (kind) {
    case "status":
      return { kind, equals: 200 };
    case "response_time":
      return { kind, under_ms: 1000 };
    default:
      return { kind: "expr", expr: "", op: "present" };
  }
}

/** TemplatableInt/Number: numeric text commits as a number, anything
 * else ({{ env.EXPECTED }}) stays a string for the renderer. */
function parseNumeric(text: string): number | string {
  const trimmed = text.trim();
  return trimmed !== "" && Number.isFinite(Number(trimmed))
    ? Number(trimmed)
    : text;
}

export function ChecksEditor({
  checks,
  onChange,
}: {
  checks: unknown;
  onChange: (checks: Check[]) => void;
}) {
  const list = (Array.isArray(checks) ? checks : []) as Check[];

  const update = (index: number, patch: Check) =>
    onChange(list.map((c, i) => (i === index ? patch : { ...c })));

  return (
    <div data-testid="checks-editor">
      {list.map((check, index) => (
        <div key={index} style={rowStyle}>
          <select
            data-testid={`check-kind-${index}`}
            className="nf-select nodrag" style={{ flex: 1.4 }}
            value={(check.kind as string) ?? "expr"}
            onChange={(e) => update(index, freshCheck(e.target.value))}
          >
            {CHECK_KINDS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
          {check.kind === "status" && (
            <input
              data-testid={`check-equals-${index}`}
              className="nf-input nodrag" style={{ flex: 2 }}
              value={String(check.equals ?? "")}
              placeholder="200"
              onChange={(e) =>
                update(index, { ...check, equals: parseNumeric(e.target.value) })
              }
            />
          )}
          {check.kind === "response_time" && (
            <input
              data-testid={`check-under-ms-${index}`}
              className="nf-input nodrag" style={{ flex: 2 }}
              value={String(check.under_ms ?? "")}
              placeholder="1000"
              onChange={(e) =>
                update(index, {
                  ...check,
                  under_ms: parseNumeric(e.target.value),
                })
              }
            />
          )}
          {(check.kind ?? "expr") === "expr" && (
            <>
              <input
                data-testid={`check-expr-${index}`}
                className="nf-input nodrag" style={{ flex: 3 }}
                value={(check.expr as string) ?? ""}
                placeholder="trigger.value.status"
                onChange={(e) =>
                  update(index, { ...check, expr: e.target.value })
                }
              />
              <select
                data-testid={`check-op-${index}`}
                className="nf-select nodrag" style={{ flex: 1.6 }}
                value={(check.op as string) ?? "present"}
                onChange={(e) => {
                  const op = e.target.value;
                  const next: Check = { ...check, op };
                  // op 'present' takes no value; every other op needs one
                  if (op === "present") delete next.value;
                  else if (!("value" in next)) next.value = "";
                  update(index, next);
                }}
              >
                {EXPR_OPS.map((op) => (
                  <option key={op} value={op}>
                    {op}
                  </option>
                ))}
              </select>
              {check.op !== undefined && check.op !== "present" && (
                <ValueCell
                  testId={`check-value-${index}`}
                  value={check.value}
                  placeholder="value"
                  onCommit={(value) => update(index, { ...check, value })}
                />
              )}
            </>
          )}
          <RemoveButton
            testId={`check-remove-${index}`}
            disabled={list.length <= 1}
            onClick={() => onChange(list.filter((_, i) => i !== index))}
          />
        </div>
      ))}
      <button
        data-testid="check-add"
        onClick={() => onChange([...list, freshCheck("expr")])}
        className="nf-btn nodrag"
      >
        + check
      </button>
    </div>
  );
}

// ----------------------------------------------------------------- cases

type Case = { name?: string; equals?: unknown };

export function CasesEditor({
  cases,
  onChange,
}: {
  cases: unknown;
  onChange: (cases: Case[]) => void;
}) {
  const list = (Array.isArray(cases) ? cases : []) as Case[];

  return (
    <div data-testid="cases-editor">
      <p style={{ fontSize: 11, color: "var(--muted)", margin: "2px 0 4px", textTransform: "none", letterSpacing: 0 }}>
        output port ← taken when expr equals
      </p>
      {list.map((case_, index) => (
        <div key={index} style={rowStyle}>
          <input
            data-testid={`case-name-${index}`}
            className="nf-input nodrag" style={{ flex: 2 }}
            value={case_.name ?? ""}
            placeholder="port name"
            onChange={(e) =>
              onChange(
                list.map((c, i) =>
                  i === index ? { ...c, name: e.target.value } : { ...c },
                ),
              )
            }
          />
          <ValueCell
            testId={`case-equals-${index}`}
            value={case_.equals}
            placeholder="value"
            onCommit={(equals) =>
              onChange(
                list.map((c, i) => (i === index ? { ...c, equals } : { ...c })),
              )
            }
          />
          <RemoveButton
            testId={`case-remove-${index}`}
            disabled={list.length <= 1}
            onClick={() => onChange(list.filter((_, i) => i !== index))}
          />
        </div>
      ))}
      <button
        data-testid="case-add"
        onClick={() =>
          onChange([...list, { name: `case${list.length + 1}`, equals: "" }])
        }
        className="nf-btn nodrag"
      >
        + case
      </button>
    </div>
  );
}
