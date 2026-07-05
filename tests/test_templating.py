"""Templating render half (S2/M1): native-value rule (D25, TR-10 core),
bare-expression evaluation, recursive config rendering, post-eval type
coercion, evaluation errors, env layering (FR-104).

The request-body round-trip and field-schema application in node runners
(the rest of TR-10) land with the request node (S2/M4).
"""

import pytest

from napflow.core.templating import (
    Renderer,
    TemplateEvaluationError,
    TypeCoercionError,
    coerce_value,
    stringify_native,
)
from napflow.core.workspace import layer_env


@pytest.fixture
def renderer() -> Renderer:
    return Renderer()


# --------------------------------------------------------------------------
# Native-value rule (D25): single expression keeps its type


@pytest.mark.parametrize(
    "value",
    [
        {"a": 1, "nested": {"b": [1, 2]}},
        [1, "two", None],
        42,
        3.5,
        True,
        False,
        None,
        "plain string",
    ],
    ids=["dict", "list", "int", "float", "true", "false", "null", "str"],
)
def test_single_expression_preserves_type(renderer, value):
    result = renderer.render("{{ x }}", {"x": value})
    assert result == value
    assert type(result) is type(value)


def test_surrounding_whitespace_tolerated(renderer):
    payload = {"id": 7}
    assert renderer.render("  {{ x }} \n", {"x": payload}) == payload


def test_expression_result_is_native_not_repr(renderer):
    # The D25 motivating case: a dict must cross as a dict, never as
    # a Python-repr string.
    body = renderer.render(
        "{{ nodes.login.response.body }}",
        {"nodes": {"login": {"response": {"body": {"token": "t"}}}}},
    )
    assert body == {"token": "t"}


def test_mixed_content_renders_to_string(renderer):
    assert renderer.render("id={{ x }}", {"x": 5}) == "id=5"
    assert renderer.render("{{ a }}{{ b }}", {"a": 1, "b": 2}) == "12"
    assert renderer.render("{{ a }} {{ b }}", {"a": 1, "b": 2}) == "1 2"


def test_tag_bearing_template_always_string_renders(renderer):
    # Structural detection: control structures are not "exactly one
    # expression", even when they yield a single value (pin, EN §6).
    result = renderer.render("{% if true %}{{ x }}{% endif %}", {"x": {"a": 1}})
    assert isinstance(result, str)


def test_plain_and_empty_strings_pass_through(renderer):
    assert renderer.render("hello", {}) == "hello"
    assert renderer.render("", {}) == ""


# --------------------------------------------------------------------------
# Bare expressions (`expr:`) — always native


def test_evaluate_bare_expression_native(renderer):
    assert renderer.evaluate("payload.id", {"payload": {"id": 7}}) == 7
    assert renderer.evaluate("items | length", {"items": [1, 2]}) == 2
    assert renderer.evaluate("a > 1 and b", {"a": 2, "b": True}) is True


# --------------------------------------------------------------------------
# Evaluation failures → TemplateEvaluationError (FR-603 routing lands M3)


def test_undefined_variable_native_path(renderer):
    with pytest.raises(TemplateEvaluationError, match="missing"):
        renderer.render("{{ missing }}", {})


def test_undefined_attribute_native_path(renderer):
    with pytest.raises(TemplateEvaluationError, match="nope"):
        renderer.render("{{ payload.nope }}", {"payload": {}})


def test_undefined_variable_string_path(renderer):
    with pytest.raises(TemplateEvaluationError, match="missing"):
        renderer.render("x={{ missing }}", {})


def test_undefined_in_bare_expression(renderer):
    with pytest.raises(TemplateEvaluationError, match="missing"):
        renderer.evaluate("missing", {})
    with pytest.raises(TemplateEvaluationError, match="nope"):
        renderer.evaluate("payload.nope", {"payload": {}})


def test_sandbox_violation_is_evaluation_error(renderer):
    with pytest.raises(TemplateEvaluationError):
        renderer.render("{{ x.__class__ }}", {"x": ""})


def test_exception_inside_expression(renderer):
    with pytest.raises(TemplateEvaluationError, match="ZeroDivisionError"):
        renderer.render("{{ 1 // 0 }}", {})


def test_runtime_syntax_error_is_evaluation_error(renderer):
    # Config strings are E009-gated, but the engine must not crash on
    # a source that slipped through (e.g. built dynamically).
    with pytest.raises(TemplateEvaluationError):
        renderer.render("{{ ", {})


def test_error_carries_source(renderer):
    with pytest.raises(TemplateEvaluationError) as e:
        renderer.render("{{ missing }}", {})
    assert e.value.source == "{{ missing }}"


# --------------------------------------------------------------------------
# Recursive config rendering


def test_render_config_recurses_values_only(renderer):
    context = {"env": {"BASE": "https://api.test", "TOKEN": "abc"}}
    config = {
        "url": "{{ env.BASE }}/jobs",
        "headers": {"Authorization": "Bearer {{ env.TOKEN }}"},
        "query": ["{{ env.TOKEN }}", 3],
        "method": "GET",
        "attempts": 2,
        "flag": None,
    }
    rendered = renderer.render_config(config, context)
    assert rendered == {
        "url": "https://api.test/jobs",
        "headers": {"Authorization": "Bearer abc"},
        "query": ["abc", 3],
        "method": "GET",
        "attempts": 2,
        "flag": None,
    }


def test_render_config_native_inside_structure(renderer):
    # A single-expression string nested in a dict still goes native.
    rendered = renderer.render_config({"body": "{{ payload }}"}, {"payload": {"a": 1}})
    assert rendered == {"body": {"a": 1}}


# --------------------------------------------------------------------------
# Post-evaluation type application (D25 second half; consumers land M3/M4)


def test_stringify_native_pins():
    assert stringify_native("s") == "s"
    assert stringify_native(3) == "3"
    assert stringify_native(3.5) == "3.5"
    assert stringify_native(True) == "true"
    assert stringify_native(None) == "null"
    assert stringify_native({"a": 1}) == '{"a": 1}'  # JSON, never repr
    assert stringify_native([1, 2]) == "[1, 2]"


def test_coerce_number():
    assert coerce_value(3, "number") == 3
    assert coerce_value("3", "number") == 3
    assert coerce_value("3.5", "number") == 3.5
    with pytest.raises(TypeCoercionError):
        coerce_value("abc", "number")
    with pytest.raises(TypeCoercionError):
        coerce_value(True, "number")  # bool is not a number


def test_coerce_boolean():
    assert coerce_value(True, "boolean") is True
    assert coerce_value("true", "boolean") is True
    assert coerce_value("FALSE", "boolean") is False
    with pytest.raises(TypeCoercionError):
        coerce_value("yes", "boolean")
    with pytest.raises(TypeCoercionError):
        coerce_value(1, "boolean")


def test_coerce_string_stringifies():
    assert coerce_value({"a": 1}, "string") == '{"a": 1}'
    assert coerce_value(3, "string") == "3"


def test_coerce_object_rejects_scalars_and_lists():
    assert coerce_value({"a": 1}, "object") == {"a": 1}
    with pytest.raises(TypeCoercionError):
        coerce_value("scalar", "object")  # TR-10: object-typed rejects scalar
    with pytest.raises(TypeCoercionError):
        coerce_value([1], "object")


def test_coerce_list():
    assert coerce_value([1], "list") == [1]
    with pytest.raises(TypeCoercionError):
        coerce_value({"a": 1}, "list")


def test_coerce_any_passes_everything():
    for value in ({"a": 1}, [1], "s", 3, True, None):
        assert coerce_value(value, "any") == value


# --------------------------------------------------------------------------
# Env layering (FR-104, WM §3)


def test_layer_env_process_wins():
    layered = layer_env(
        {"BASE_URL": "https://dev", "API_TOKEN": "from-file"},
        {"API_TOKEN": "from-ci"},
    )
    assert layered["BASE_URL"] == "https://dev"
    assert layered["API_TOKEN"] == "from-ci"


def test_layer_env_process_only_key_visible():
    # The CI-override story: a key the profile never mentions.
    assert layer_env({}, {"EXTRA": "x"})["EXTRA"] == "x"


def test_layer_env_defaults_to_os_environ(monkeypatch):
    monkeypatch.setenv("NAPFLOW_TEST_LAYER", "os")
    assert layer_env({})["NAPFLOW_TEST_LAYER"] == "os"
