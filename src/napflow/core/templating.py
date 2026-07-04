"""Sandboxed Jinja2 environment — the ONLY expression/template language.

M4 scope: environment construction + syntax checking (E009). Rendering,
the templating context, and the native-value rule (D25) land with the
engine (S2). The sandbox is accident protection, not a security
boundary (EC35) — flows are code.
"""

from jinja2 import StrictUndefined, TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment


def create_environment() -> SandboxedEnvironment:
    """One environment shape everywhere: sandboxed, StrictUndefined
    (undefined variables are node errors, never empty strings)."""
    return SandboxedEnvironment(
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )


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
