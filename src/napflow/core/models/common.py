"""Shared building blocks for the flow and manifest models.

All models are validated READ-ONLY views over the loaded YAML document
(FR-208): the ruamel CommentedMap is the single write source; these
models are never serialized back to disk. `frozen=True` enforces the
read-only contract; `extra="forbid"` makes unknown keys a validation
error (surfaced as E002 by the checker).
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# Node ids are template/path-safe (E011): `nodes.<id>.<port>` expressions
# and `from: <id>.<port>` edge endpoints must parse unambiguously.
IDENT_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"
PORT_REF_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$"

NodeId = Annotated[str, Field(pattern=IDENT_PATTERN)]
PortName = Annotated[str, Field(pattern=IDENT_PATTERN)]
PortRef = Annotated[str, Field(pattern=PORT_REF_PATTERN)]
EnvVarName = Annotated[str, Field(pattern=IDENT_PATTERN)]

# Soft port types: UI colors ports and warns on mismatch (W102), never blocks.
PortType = Literal["string", "number", "boolean", "object", "list", "any"]

# Any config value may be a Jinja2 template string; the field's schema
# type applies POST-evaluation (native-value rule, D25). Hence every
# non-string config field admits `str` alongside its native type.
TemplatableInt = int | str
TemplatableNumber = int | float | str
TemplatableBool = bool | str

# Header/query values: scalars are stringified post-evaluation (D25).
Scalar = str | int | float | bool


class FrozenModel(BaseModel):
    """Base for every napflow model: immutable, unknown keys rejected."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)
