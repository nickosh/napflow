"""Sandboxed Jinja2 — the ONLY expression/template language (D10).

Two halves, one environment shape:
- syntax checking (E009, used by the checker since M4);
- rendering (S2/M1): the `Renderer` implements the native-value rule
  (D25) plus bare-expression evaluation, and `coerce_value` applies the
  field/port schema type POST-evaluation.

The sandbox is accident protection, not a security boundary (EC35) —
flows are code.

Native-value rule (D25): a config value that is exactly one
`{{ expression }}` (ignoring surrounding whitespace) evaluates to the
expression's native value — dicts, lists, numbers, booleans, null keep
their type; any mixed content renders to a string; bare `expr:` fields
are always native. "Exactly one expression" is structural: the parsed
template must be a single `{{ }}` output holding one expression, so
tag-bearing templates (`{% if %}`, `{% for %}`) always string-render,
and nothing is ever literal_eval'd (auto-parsing rendered strings was
rejected in D25).

Evaluation failures (`TemplateEvaluationError`) are node errors: the
engine routes them to the node's error port; port-less nodes surface
them as unhandled node errors ⇒ run `failed` (FR-603, EC24).
"""

import json
from collections.abc import Mapping
from typing import Any

from jinja2 import StrictUndefined, TemplateSyntaxError, UndefinedError, nodes
from jinja2.nativetypes import NativeCodeGenerator
from jinja2.parser import Parser
from jinja2.runtime import Undefined
from jinja2.sandbox import SandboxedEnvironment

from napflow.core.models.common import PortType

# --------------------------------------------------------------------------
# Environments


def create_environment() -> SandboxedEnvironment:
    """One environment shape everywhere: sandboxed, StrictUndefined
    (undefined variables are node errors, never empty strings)."""
    return SandboxedEnvironment(
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )


def _native_concat(values: Any) -> Any:
    """A lone output keeps its type; anything else joins as text —
    never literal_eval (D25 rejected auto-parsing rendered strings)."""
    items = list(values)
    if len(items) == 1:
        return items[0]
    return "".join(str(v) for v in items)


class _NativeSandboxedEnvironment(SandboxedEnvironment):
    """Sandboxed twin of jinja2's NativeEnvironment: outputs are not
    pre-stringified, so a single-expression template returns the
    expression's value with its type intact. Only ever fed sources that
    passed the structural single-expression check."""

    code_generator_class = NativeCodeGenerator
    concat = staticmethod(_native_concat)  # type: ignore[assignment]


def create_native_environment() -> SandboxedEnvironment:
    return _NativeSandboxedEnvironment(
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )


# --------------------------------------------------------------------------
# Syntax checking (E009) — the checker's half


def template_syntax_error(env: SandboxedEnvironment, source: str) -> str | None:
    """Syntax-check `{{ }}` interpolation content; None when valid."""
    try:
        env.parse(source)
    except TemplateSyntaxError as e:
        return e.message or "template syntax error"
    return None


def expression_syntax_error(env: SandboxedEnvironment, expr: str) -> str | None:
    """Syntax-check a bare Jinja2 expression (`expr:`, `over:`, assert
    `expr` checks); None when valid."""
    try:
        env.compile_expression(expr)
    except TemplateSyntaxError as e:
        return e.message or "expression syntax error"
    return None


def referenced_nodes(
    env: SandboxedEnvironment, source: str, *, expression: bool = False
) -> set[str]:
    """Node ids referenced as `nodes.<id>` / `nodes["<id>"]` — the
    ghost-wire extraction (flow-schema §Templating: cross-node template
    references render as ghost-wires). Same AST parse E009 runs, so no
    regex false-positives on string literals; a syntax error yields the
    empty set (E009 owns reporting it)."""
    tree: nodes.Node
    try:
        if expression:
            # mirror compile_expression's parse — wrapping in `{{ }}`
            # would misparse expressions containing `}}`
            tree = Parser(env, source, state="variable").parse_expression()
        else:
            tree = env.parse(source)
    except TemplateSyntaxError:
        return set()
    refs: set[str] = set()
    for ref in tree.find_all((nodes.Getattr, nodes.Getitem)):
        base = ref.node
        if not (isinstance(base, nodes.Name) and base.name == "nodes"):
            continue
        if isinstance(ref, nodes.Getattr):
            refs.add(ref.attr)
        elif isinstance(ref.arg, nodes.Const) and isinstance(ref.arg.value, str):
            refs.add(ref.arg.value)
    return refs


# --------------------------------------------------------------------------
# Rendering — the engine's half


class TemplateEvaluationError(Exception):
    """Runtime template/expression failure: undefined variable
    (StrictUndefined), sandbox violation, or an exception raised inside
    the expression. Routed by the engine per FR-603/EC24."""

    def __init__(self, source: str, message: str):
        self.source = source
        self.message = message
        super().__init__(f"{message} in {source!r}")


def _error_message(exc: Exception) -> str:
    if isinstance(exc, TemplateSyntaxError):
        return exc.message or "template syntax error"
    if isinstance(exc, UndefinedError):
        return str(exc) or "undefined variable"
    return f"{type(exc).__name__}: {exc}"


def _is_single_expression(env: SandboxedEnvironment, stripped: str) -> bool:
    """Structural D25 check on a pre-stripped source: the whole template
    is one `{{ }}` output holding one expression — no tags, no text."""
    try:
        body = env.parse(stripped).body
    except TemplateSyntaxError:
        return False  # string path re-raises with full context
    if len(body) != 1 or not isinstance(body[0], nodes.Output):
        return False
    parts = body[0].nodes
    return len(parts) == 1 and not isinstance(parts[0], nodes.TemplateData)


class Renderer:
    """Render half of templating; one instance per run.

    Context shape (EN §6, populated by the engine): `env`, `inputs`,
    `run`, `nodes`, `trigger` (full envelope), `item`/`index` in
    loop-body frames.
    """

    def __init__(self) -> None:
        self._string_env = create_environment()
        self._native_env = create_native_environment()

    def render(self, source: str, context: Mapping[str, Any]) -> Any:
        """Render a config string per the native-value rule (D25):
        single-expression sources evaluate natively, everything else
        renders to a string."""
        stripped = source.strip()
        if _is_single_expression(self._native_env, stripped):
            env, template_source = self._native_env, stripped
        else:
            env, template_source = self._string_env, source
        try:
            result = env.from_string(template_source).render(dict(context))
            if isinstance(result, Undefined):
                str(result)  # StrictUndefined raises, naming the variable —
                # native rendering yields the Undefined object without
                # touching it, so force the error here (top-level only;
                # attribute access on Undefined already raises upstream)
            return result
        except TemplateEvaluationError:
            raise
        except Exception as e:
            raise TemplateEvaluationError(source, _error_message(e)) from e

    def evaluate(self, expr: str, context: Mapping[str, Any]) -> Any:
        """Evaluate a bare expression (`expr:`, `over:`, assert `expr`)
        — always native (D25)."""
        try:
            compiled = self._string_env.compile_expression(
                expr, undefined_to_none=False
            )
            result = compiled(dict(context))
            if isinstance(result, Undefined):
                str(result)  # as in render(): force the strict error
            return result
        except TemplateEvaluationError:
            raise
        except Exception as e:
            raise TemplateEvaluationError(expr, _error_message(e)) from e

    def render_config(self, value: Any, context: Mapping[str, Any]) -> Any:
        """Recursively render every string in a config structure — dict
        values and list items; keys are never templated. Non-strings
        pass through untouched."""
        if isinstance(value, str):
            return self.render(value, context)
        if isinstance(value, Mapping):
            return {k: self.render_config(v, context) for k, v in value.items()}
        if isinstance(value, list):
            return [self.render_config(v, context) for v in value]
        return value


# --------------------------------------------------------------------------
# Post-evaluation type application (D25: the schema is the type authority)


class TypeCoercionError(Exception):
    """Rendered value does not fit the declared field/port type — a node
    error, routed exactly like TemplateEvaluationError (EC24)."""

    def __init__(self, value: Any, expected: str):
        self.value = value
        self.expected = expected
        super().__init__(f"expected {expected}, got {type(value).__name__}: {value!r}")


def stringify_native(value: Any) -> str:
    """String-typed fields stringify non-string natives as JSON — never
    Python repr (D25). Scalars: `3` → "3", `true` → "true", null →
    "null"; containers become JSON text."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value)
    except (TypeError, ValueError) as e:
        raise TypeCoercionError(value, "JSON-serializable value") from e


def coerce_value(value: Any, type_: PortType) -> Any:
    """Apply a declared soft type post-evaluation (D25). `number` and
    `boolean` accept the string forms env-file values arrive in (WM §2:
    profile values are literal strings); `object`/`list` reject
    everything else — including scalars into object-typed fields."""
    if type_ == "any":
        return value
    if type_ == "string":
        return stringify_native(value)
    if type_ == "number":
        if isinstance(value, bool):
            raise TypeCoercionError(value, "number")
        if isinstance(value, int | float):
            return value
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                try:
                    return float(value)
                except ValueError:
                    raise TypeCoercionError(value, "number") from None
        raise TypeCoercionError(value, "number")
    if type_ == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower() == "true"
        raise TypeCoercionError(value, "boolean")
    if type_ == "object":
        if isinstance(value, dict):
            return value
        raise TypeCoercionError(value, "object")
    if type_ == "list":
        if isinstance(value, list):
            return value
        raise TypeCoercionError(value, "list")
    raise ValueError(f"unknown port type {type_!r}")  # unreachable via models
