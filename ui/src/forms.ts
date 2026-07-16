// Hand-rolled per-type config form descriptors (owner fork 2026-07-06:
// explicit descriptors, not JSON-Schema-driven). One entry per node
// type in the v1 catalog (flow-schema spec). start/end are NOT here:
// their port lists get dedicated editors (FR-1006).

export type FieldKind =
  | "string" // one-line text
  | "text" // multiline text
  | "number"
  | "boolean"
  | "templatable-number" // native number or a Jinja template string
  | "templatable-boolean" // native bool or a Jinja template string
  | "select"
  | "json" // any JSON value, edited as text
  | "function" // python function name — options come from detail.functions
  | "checks" // assert.checks row editor (StructuredRows)
  | "cases"; // switch.cases row editor (StructuredRows)

export type FieldDescriptor = {
  key: string;
  label: string;
  kind: FieldKind;
  options?: string[]; // select
  placeholder?: string;
  help?: string;
};

/** Parse a text control backed by TemplatableNumber. Numeric text stays
 * native in YAML; every other non-empty value stays a string for Jinja. */
export function parseTemplatableNumber(
  text: string,
): number | string | undefined {
  if (text === "") return undefined;
  const trimmed = text.trim();
  return trimmed !== "" && Number.isFinite(Number(trimmed))
    ? Number(trimmed)
    : text;
}

/** Parse a text control backed by TemplatableBool. The literal choices stay
 * native in YAML while templates remain editable as strings. */
export function parseTemplatableBoolean(
  text: string,
): boolean | string | undefined {
  if (text === "") return undefined;
  if (text === "true") return true;
  if (text === "false") return false;
  return text;
}

export const CONFIG_FORMS: Record<string, FieldDescriptor[]> = {
  request: [
    { key: "method", label: "method", kind: "string", placeholder: "GET" },
    { key: "url", label: "url", kind: "string", placeholder: "{{ env.BASE_URL }}/path" },
    { key: "headers", label: "headers", kind: "json", placeholder: '{"X-Token": "{{ env.TOKEN }}"}' },
    { key: "query", label: "query", kind: "json", placeholder: '{"page": 1}' },
    { key: "body", label: "body", kind: "json" },
    {
      key: "timeout_s",
      label: "timeout_s",
      kind: "templatable-number",
      placeholder: "30 or {{ env.REQUEST_TIMEOUT }}",
    },
    {
      key: "verify_tls",
      label: "verify_tls",
      kind: "templatable-boolean",
      placeholder: "true, false, or {{ env.VERIFY_TLS }}",
    },
    { key: "retry", label: "retry", kind: "json", placeholder: '{"max_attempts": 3}' },
    { key: "http_version", label: "http_version", kind: "select", options: ["", "1.1", "2", "3"] },
  ],
  python: [
    { key: "function", label: "function", kind: "function" },
    { key: "outputs", label: "outputs", kind: "json", placeholder: '["summary"]' },
  ],
  assert: [
    { key: "checks", label: "checks", kind: "checks" },
    { key: "mode", label: "mode", kind: "select", options: ["report_all", "fail_fast"] },
  ],
  condition: [{ key: "expr", label: "expr", kind: "string" }],
  switch: [
    { key: "expr", label: "expr", kind: "string" },
    { key: "cases", label: "cases", kind: "cases" },
  ],
  loop: [
    { key: "over", label: "over", kind: "string", placeholder: "trigger.value.items" },
    { key: "body", label: "body", kind: "string", placeholder: "flows/enroll_user" },
    { key: "mode", label: "mode", kind: "select", options: ["sequential", "parallel"] },
    { key: "max_concurrency", label: "max_concurrency", kind: "number" },
    { key: "on_error", label: "on_error", kind: "select", options: ["stop", "continue"] },
    { key: "fresh_session", label: "fresh_session", kind: "boolean" },
  ],
  flow: [{ key: "flow", label: "flow", kind: "string", placeholder: "flows/login" }],
  set: [
    { key: "name", label: "name", kind: "string" },
    { key: "value", label: "value", kind: "json" },
  ],
  get: [{ key: "name", label: "name", kind: "string" }],
  merge: [
    { key: "mode", label: "mode", kind: "select", options: ["any", "all", "collect"] },
    { key: "count", label: "count", kind: "number", help: "collect mode only" },
  ],
  counter: [{ key: "count", label: "count", kind: "number" }],
  timeout: [{ key: "seconds", label: "seconds", kind: "number" }],
  delay: [
    {
      key: "seconds",
      label: "seconds",
      kind: "templatable-number",
      placeholder: "1 or {{ inputs.wait_s }}",
    },
  ],
  log: [
    { key: "label", label: "label", kind: "string" },
    {
      key: "level",
      label: "level",
      kind: "select",
      options: ["debug", "info", "warn", "error"],
    },
  ],
  fixture: [
    { key: "file", label: "file", kind: "string", placeholder: "users.json" },
    { key: "format", label: "format", kind: "select", options: ["", "json", "csv"] },
  ],
  note: [{ key: "text", label: "text", kind: "text" }],
};

/** Cross-language schema coverage markers. `form-coverage.json` is checked
 * against Pydantic by pytest and against these implemented UI paths by
 * Vitest, so adding a schema field cannot silently omit its editor policy. */
export const NODE_FIELD_COVERAGE = {
  id: "canvas",
  type: "palette",
  config: "type-form",
  max_seconds: "node-number",
} as const;

export const DEDICATED_FORM_COVERAGE = {
  StartPort: {
    name: "start-ports",
    type: "start-ports",
    default: "typed-template",
  },
  EndPort: { name: "end-ports", required: "end-ports" },
  RetryConfig: { max_attempts: "request-retry-json" },
  StatusCheck: { kind: "checks", equals: "templatable-number" },
  ExprCheck: {
    kind: "checks",
    expr: "checks",
    op: "checks",
    value: "checks",
  },
  ResponseTimeCheck: {
    kind: "checks",
    under_ms: "templatable-number",
  },
  SwitchCase: { name: "cases", equals: "cases" },
} as const;

/** dataTransfer key for palette→canvas drags (drag-from-palette). */
export const PALETTE_DRAG_TYPE = "application/x-napflow-node-type";

/** Node types the palette offers (spec node catalog order). */
export const NODE_TYPES = [
  "request",
  "python",
  "assert",
  "condition",
  "switch",
  "loop",
  "flow",
  "set",
  "get",
  "merge",
  "counter",
  "timeout",
  "delay",
  "log",
  "fixture",
  "note",
] as const;

/** A config that passes model validation for a fresh node — required
 * fields present with editable stubs, optional ones left unset so the
 * saved YAML stays minimal (exclude_unset). */
export function defaultConfig(type: string): Record<string, unknown> {
  switch (type) {
    case "start":
    case "end":
      return { ports: [] };
    case "request":
      return { url: "" };
    case "python":
      return { function: "", outputs: [] };
    case "assert":
      return { checks: [{ kind: "expr", expr: "trigger.value", op: "present" }] };
    case "condition":
      return { expr: "" };
    case "switch":
      return { expr: "", cases: [{ name: "case1", equals: "" }] };
    case "loop":
      return { over: "", body: "" };
    case "flow":
      return { flow: "" };
    case "set":
      return { name: "value", value: "" };
    case "get":
      return { name: "value" };
    case "merge":
      return { mode: "any" };
    case "counter":
      return { count: 3 };
    case "timeout":
      return { seconds: 60 };
    case "delay":
      return { seconds: 1 };
    case "fixture":
      return { file: "" };
    case "note":
      return { text: "" };
    default:
      return {}; // log — every field optional
  }
}
