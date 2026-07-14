import { useEffect, useState } from "react";

import {
  CONFIG_FORMS,
  parseTemplatableBoolean,
  parseTemplatableNumber,
  type FieldDescriptor,
} from "../forms";
import { useAppStore } from "../store";
import { CasesEditor, ChecksEditor } from "./StructuredRows";

const inputStyle: React.CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  fontSize: 12,
  fontFamily: "ui-monospace, monospace",
  padding: "3px 6px",
  border: "1px solid #ccc",
  borderRadius: 3,
};

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: 11,
  color: "#666",
  margin: "8px 0 2px",
};

/** One JSON-edited field: local text state so half-typed JSON doesn't
 * thrash the model; committed on blur only when it parses. */
function JsonField({
  value,
  onCommit,
  placeholder,
  testId,
}: {
  value: unknown;
  onCommit: (parsed: unknown) => void;
  placeholder?: string;
  testId: string;
}) {
  const serialized =
    value === undefined ? "" : JSON.stringify(value, null, value !== null && typeof value === "object" ? 1 : 0);
  const [text, setText] = useState(serialized);
  const [bad, setBad] = useState(false);
  useEffect(() => {
    setText(serialized);
    setBad(false);
  }, [serialized]);

  return (
    <textarea
      data-testid={testId}
      value={text}
      placeholder={placeholder}
      rows={Math.min(6, Math.max(1, text.split("\n").length))}
      style={{ ...inputStyle, borderColor: bad ? "#c62828" : "#ccc" }}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => {
        if (text.trim() === "") {
          setBad(false);
          onCommit(undefined); // unset the key — keeps the YAML minimal
          return;
        }
        try {
          onCommit(JSON.parse(text));
          setBad(false);
        } catch {
          setBad(true); // stays local until it parses
        }
      }}
    />
  );
}

function Field({
  field,
  value,
  functions,
  onChange,
}: {
  field: FieldDescriptor;
  value: unknown;
  functions: string[] | null;
  onChange: (value: unknown) => void;
}) {
  const testId = `config-${field.key}`;
  switch (field.kind) {
    case "string":
      return (
        <input
          data-testid={testId}
          style={inputStyle}
          value={(value as string) ?? ""}
          placeholder={field.placeholder}
          onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)}
        />
      );
    case "text":
      return (
        <textarea
          data-testid={testId}
          style={inputStyle}
          rows={4}
          value={(value as string) ?? ""}
          onChange={(e) => onChange(e.target.value)}
        />
      );
    case "number":
      return (
        <input
          data-testid={testId}
          style={inputStyle}
          type="number"
          value={value === undefined || value === null ? "" : String(value)}
          onChange={(e) =>
            onChange(e.target.value === "" ? undefined : Number(e.target.value))
          }
        />
      );
    case "boolean":
      return (
        <select
          data-testid={testId}
          style={inputStyle}
          value={value === undefined || value === null ? "" : String(value)}
          onChange={(e) =>
            onChange(e.target.value === "" ? undefined : e.target.value === "true")
          }
        >
          <option value="">(default)</option>
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      );
    case "templatable-number":
      return (
        <input
          data-testid={testId}
          style={inputStyle}
          inputMode="decimal"
          value={value === undefined || value === null ? "" : String(value)}
          placeholder={field.placeholder}
          title="number or Jinja template"
          onChange={(e) => onChange(parseTemplatableNumber(e.target.value))}
        />
      );
    case "templatable-boolean":
      return (
        <>
          <input
            data-testid={testId}
            style={inputStyle}
            list={`${testId}-values`}
            value={value === undefined || value === null ? "" : String(value)}
            placeholder={field.placeholder}
            title="true, false, or Jinja template"
            onChange={(e) => onChange(parseTemplatableBoolean(e.target.value))}
          />
          <datalist id={`${testId}-values`}>
            <option value="true" />
            <option value="false" />
          </datalist>
        </>
      );
    case "select":
      return (
        <select
          data-testid={testId}
          style={inputStyle}
          value={(value as string) ?? ""}
          onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)}
        >
          {(field.options ?? []).map((option) => (
            <option key={option} value={option}>
              {option === "" ? "(default)" : option}
            </option>
          ))}
          {!field.options?.includes("") && value === undefined && (
            <option value="">(unset)</option>
          )}
        </select>
      );
    case "function":
      // AST-derived dropdown (EC14); free text still allowed — the fn
      // may not exist YET (write flow.yaml first, code second)
      return (
        <>
          <input
            data-testid={testId}
            style={inputStyle}
            list="nodes-py-functions"
            value={(value as string) ?? ""}
            onChange={(e) => onChange(e.target.value)}
          />
          <datalist id="nodes-py-functions">
            {(functions ?? []).map((fn) => (
              <option key={fn} value={fn} />
            ))}
          </datalist>
        </>
      );
    case "json":
      return (
        <JsonField
          testId={testId}
          value={value}
          placeholder={field.placeholder}
          onCommit={onChange}
        />
      );
    case "checks":
      return <ChecksEditor checks={value} onChange={onChange} />;
    case "cases":
      return <CasesEditor cases={value} onChange={onChange} />;
  }
}

export default function ConfigForm({
  nodeId,
  nodeType,
  config,
}: {
  nodeId: string;
  nodeType: string;
  config: Record<string, unknown>;
}) {
  const updateNodeConfig = useAppStore((s) => s.updateNodeConfig);
  const functions = useAppStore((s) => s.detail?.functions ?? null);
  const fields = CONFIG_FORMS[nodeType];
  if (!fields) return null;

  const setField = (key: string, value: unknown) => {
    const next = { ...config };
    if (value === undefined) {
      delete next[key]; // absent key = model default (exclude_unset)
    } else {
      next[key] = value;
    }
    updateNodeConfig(nodeId, next);
  };

  return (
    <div data-testid="config-form">
      {fields.map((field) => {
        const body = (
          <Field
            field={field}
            value={config[field.key]}
            functions={functions}
            onChange={(value) => setField(field.key, value)}
          />
        );
        const caption = (
          <>
            {field.label}
            {field.help && <em style={{ marginLeft: 6 }}>({field.help})</em>}
          </>
        );
        // row editors hold many controls — a <label> would misdirect
        // clicks to whichever control happens to come first
        return field.kind === "checks" || field.kind === "cases" ? (
          <div key={field.key}>
            <span style={labelStyle}>{caption}</span>
            {body}
          </div>
        ) : (
          <label key={field.key} style={labelStyle}>
            {caption}
            {body}
          </label>
        );
      })}
    </div>
  );
}
