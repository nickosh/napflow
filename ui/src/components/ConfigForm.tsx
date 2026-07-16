import { useEffect, useState } from "react";

import {
  CONFIG_FORMS,
  parseTemplatableBoolean,
  parseTemplatableNumber,
  type FieldDescriptor,
} from "../forms";
import { useAppStore } from "../store";
import { CasesEditor, ChecksEditor } from "./StructuredRows";

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
      className={`nf-input nodrag${bad ? " nf-bad" : ""}`}
      value={text}
      placeholder={placeholder}
      rows={Math.min(6, Math.max(1, text.split("\n").length))}
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
  quick,
}: {
  field: FieldDescriptor;
  value: unknown;
  functions: string[] | null;
  onChange: (value: unknown) => void;
  quick?: boolean;
}) {
  const testId = `config-${field.key}`;
  // quick variant: compact unlabeled card inputs (the label rides the
  // title/placeholder); narrow keys stay narrow, the rest stretch
  const narrow = field.key === "method" || field.key === "level";
  const quickStyle: React.CSSProperties | undefined = quick
    ? narrow
      ? { width: 64, flex: "0 0 auto" }
      : { flex: "1 1 60px", width: "auto" }
    : undefined;
  const cls = "nf-input nodrag";
  switch (field.kind) {
    case "string":
      return (
        <input
          data-testid={testId}
          className={cls}
          style={quickStyle}
          value={(value as string) ?? ""}
          placeholder={quick ? field.label : field.placeholder}
          title={quick ? field.label : undefined}
          onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)}
        />
      );
    case "text":
      return (
        <textarea
          data-testid={testId}
          className={cls}
          style={quickStyle}
          rows={4}
          value={(value as string) ?? ""}
          onChange={(e) => onChange(e.target.value)}
        />
      );
    case "number":
      return (
        <input
          data-testid={testId}
          className={cls}
          style={quickStyle}
          type="number"
          placeholder={quick ? field.label : undefined}
          title={quick ? field.label : undefined}
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
          className="nf-select nodrag"
          style={quickStyle}
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
          className={cls}
          style={quickStyle}
          inputMode="decimal"
          value={value === undefined || value === null ? "" : String(value)}
          placeholder={quick ? field.label : field.placeholder}
          title="number or Jinja template"
          onChange={(e) => onChange(parseTemplatableNumber(e.target.value))}
        />
      );
    case "templatable-boolean":
      return (
        <>
          <input
            data-testid={testId}
            className={cls}
            style={quickStyle}
            list={`${testId}-values`}
            value={value === undefined || value === null ? "" : String(value)}
            placeholder={quick ? field.label : field.placeholder}
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
          className="nf-select nodrag"
          style={quickStyle}
          title={quick ? field.label : undefined}
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
            className={cls}
            style={quickStyle}
            list="nodes-py-functions"
            placeholder={quick ? field.label : undefined}
            title={quick ? field.label : undefined}
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

/** Per-type config editor. F1 splits it across the node card: `only`
 * renders the always-visible quick inputs, `exclude` the expanded rest
 * — the same descriptors and testids either way. */
export default function ConfigForm({
  nodeId,
  nodeType,
  config,
  only,
  exclude,
  quick = false,
}: {
  nodeId: string;
  nodeType: string;
  config: Record<string, unknown>;
  only?: readonly string[];
  exclude?: readonly string[];
  quick?: boolean;
}) {
  const updateNodeConfig = useAppStore((s) => s.updateNodeConfig);
  const functions = useAppStore((s) => s.detail?.functions ?? null);
  let fields = CONFIG_FORMS[nodeType];
  if (!fields) return null;
  if (only !== undefined) fields = fields.filter((f) => only.includes(f.key));
  if (exclude !== undefined) {
    fields = fields.filter((f) => !exclude.includes(f.key));
  }
  if (fields.length === 0) return null;

  const setField = (key: string, value: unknown) => {
    const next = { ...config };
    if (value === undefined) {
      delete next[key]; // absent key = model default (exclude_unset)
    } else {
      next[key] = value;
    }
    updateNodeConfig(nodeId, next);
  };

  if (quick) {
    return (
      <>
        {fields.map((field) => (
          <Field
            key={field.key}
            field={field}
            value={config[field.key]}
            functions={functions}
            onChange={(value) => setField(field.key, value)}
            quick
          />
        ))}
      </>
    );
  }

  return (
    <div data-testid="config-form" style={{ display: "flex", flexDirection: "column", gap: 9 }}>
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
          <span>
            {field.label}
            {field.help && (
              <em style={{ marginLeft: 6, textTransform: "none" }}>
                ({field.help})
              </em>
            )}
          </span>
        );
        // row editors hold many controls — a <label> would misdirect
        // clicks to whichever control happens to come first
        return field.kind === "checks" || field.kind === "cases" ? (
          <div key={field.key} className="nf-label">
            {caption}
            {body}
          </div>
        ) : (
          <label key={field.key} className="nf-label">
            {caption}
            {body}
          </label>
        );
      })}
    </div>
  );
}
