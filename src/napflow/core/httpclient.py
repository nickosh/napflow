"""HTTP transport adapter — the ONLY module importing niquests (NFR-09).

Everything engine-visible is napflow-shaped: `WireResponse` in,
`TransportError` out. Swapping the HTTP client stays a contained change;
nothing else in `core/` may import niquests (guarded by a test).

Owns the binary payload envelope (FR-207) in both directions:
`{"__binary__": true, "content_type": ..., "base64": ...}` — response
bodies that are neither JSON nor decodable text arrive as the envelope;
a request body shaped like the envelope is sent as its decoded bytes.

Timing fields are best-effort per EN §7: `total_ms` always (from
`response.elapsed`); dns/connect/tls/ttfb only where niquests' conn_info
exposes the corresponding latency — omitted otherwise, never zero-filled.
"""

import json
from base64 import b64decode, b64encode
from binascii import Error as Base64Error
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import niquests
from niquests import exceptions as _exc

from napflow.core.templating import stringify_native

_TEXT_MIMES = {
    "application/xml",
    "application/xhtml+xml",
    "application/x-www-form-urlencoded",
    "image/svg+xml",
}


@dataclass(frozen=True)
class WireRequest:
    """One niquests-prepared request, copied before transport can mutate it.

    ``headers`` includes session defaults and the effective ``Cookie`` header.
    ``body`` is ``None`` for no body or napflow's binary envelope for the exact
    prepared bytes; ``size_bytes`` always counts those raw bytes.
    """

    method: str
    url: str
    headers: dict[str, str]
    body: Any
    size_bytes: int


class TransportError(Exception):
    """Transport-level failure (EC13): connection/DNS/TLS, or timeout of
    one attempt. Non-2xx responses are NOT errors — they are responses."""

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        request: WireRequest | None = None,
        redirects_total: int = 0,
    ):
        self.kind = kind  # connection | timeout | tls | transport
        self.request = request
        self.redirects_total = redirects_total
        super().__init__(message)


class RequestEncodingError(ValueError):
    """The configured request body cannot be encoded for transport.

    This is user-controlled request data, not an engine failure. The
    engine routes it through the request node's ``error`` port (EC48).
    """


@dataclass(frozen=True)
class WireResponse:
    status: int
    headers: dict[str, str]
    body: Any  # decoded: JSON-native | text | binary envelope | None
    size_bytes: int  # exact response-body bytes received on the wire
    url: str
    http_version: str | None
    elapsed_ms: float
    request: WireRequest
    redirects_total: int
    timing: dict[str, float] = field(default_factory=dict)


class HttpClient:
    """Shared per-run client (EN §1): one `AsyncSession` per negotiated
    `http_version` option (None/1.1/2/3 — session-level flags in
    niquests), created lazily, all closed at FINALIZE."""

    def __init__(self) -> None:
        self._sessions: dict[str | None, niquests.AsyncSession] = {}

    def _session(self, http_version: str | None) -> niquests.AsyncSession:
        if http_version not in self._sessions:
            kwargs: dict[str, Any] = {}
            if http_version == "1.1":
                kwargs = {"disable_http2": True, "disable_http3": True}
            elif http_version == "2":
                kwargs = {"disable_http1": True, "disable_http3": True}
            elif http_version == "3":
                kwargs = {"disable_http1": True, "disable_http2": True}
            try:
                session = niquests.AsyncSession(**kwargs)
            except TypeError:  # older niquests without disable_http1
                kwargs.pop("disable_http1", None)
                session = niquests.AsyncSession(**kwargs)
            self._sessions[http_version] = session
        return self._sessions[http_version]

    async def request(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        query: dict[str, str] | None = None,
        body: Any = None,
        timeout_s: float,
        verify_tls: bool,
        http_version: str | None = None,
        on_prepared: Callable[[WireRequest], None] | None = None,
    ) -> WireResponse:
        send: dict[str, Any] = {}
        headers = dict(headers or {})
        explicit_empty_body = False
        if body is not None:
            kind, payload, content_type = _encode_body(body)
            send[kind] = payload
            explicit_empty_body = kind == "data" and payload == b""
            if content_type and not _has_content_type(headers):
                headers["Content-Type"] = content_type

        initial_request: WireRequest | None = None
        latest_request: WireRequest | None = None
        prepared_count = 0

        def capture_prepared(prepared: niquests.PreparedRequest) -> None:
            nonlocal initial_request, latest_request, prepared_count
            preserve_empty = (
                explicit_empty_body
                and prepared.method is not None
                and prepared.method.upper() == method.upper()
            )
            snapshot = _wire_request_of(
                prepared,
                preserve_explicit_empty=preserve_empty,
            )
            prepared_count += 1
            latest_request = snapshot
            if initial_request is None:
                initial_request = snapshot
                if on_prepared is not None:
                    on_prepared(snapshot)

        try:
            response = await self._session(http_version).request(
                method,
                url,
                headers=headers or None,
                params=query or None,
                timeout=timeout_s,
                verify=verify_tls,
                hooks={"pre_request": capture_prepared},
                **send,
            )
        except _exc.Timeout as e:
            raise _transport_error("timeout", e, latest_request, prepared_count) from e
        except _exc.SSLError as e:
            raise _transport_error("tls", e, latest_request, prepared_count) from e
        except _exc.ConnectionError as e:
            raise _transport_error(
                "connection", e, latest_request, prepared_count
            ) from e
        except _exc.RequestException as e:
            raise _transport_error(
                "transport", e, latest_request, prepared_count
            ) from e

        prepared = getattr(response, "request", None)
        if prepared is None:
            raise TransportError(
                "transport",
                "HTTP response did not expose its prepared request",
                request=latest_request,
                redirects_total=max(prepared_count - 1, 0),
            )
        final_request = _wire_request_of(
            prepared,
            preserve_explicit_empty=(
                explicit_empty_body
                and prepared.method is not None
                and prepared.method.upper() == method.upper()
            ),
        )

        content = response.content or b""
        decoded, size = _decode_body(content, response.headers.get("Content-Type", ""))
        return WireResponse(
            status=response.status_code,
            headers=dict(response.headers),
            body=decoded,
            size_bytes=size,
            url=str(response.url),
            http_version=_http_version_of(response),
            elapsed_ms=_ms(response.elapsed) or 0.0,
            request=final_request,
            redirects_total=len(response.history),
            timing=_timing_of(response),
        )

    async def close(self) -> None:
        for session in self._sessions.values():
            await session.close()
        self._sessions.clear()


# --------------------------------------------------------------------------
# Body codec (FR-207)


def _wire_request_of(
    prepared: niquests.PreparedRequest,
    *,
    preserve_explicit_empty: bool = False,
) -> WireRequest:
    """Copy the prepared method/URL/headers and exact finite body bytes."""
    if prepared.method is None or prepared.url is None:
        raise RequestEncodingError("niquests produced an incomplete prepared request")

    headers = {
        _header_text(name): _header_text(value)
        for name, value in (prepared.headers or {}).items()
    }
    prepared_body = prepared.body
    if prepared_body is None and not preserve_explicit_empty:
        body = None
        size_bytes = 0
    else:
        if prepared_body is None:
            raw = b""
        elif isinstance(prepared_body, str):
            raw = prepared_body.encode("utf-8")
        elif isinstance(prepared_body, bytes | bytearray | memoryview):
            raw = bytes(prepared_body)
        else:
            raise RequestEncodingError(
                "prepared request body is not a finite byte sequence"
            )
        body = {
            "__binary__": True,
            "content_type": _content_type(headers) or "application/octet-stream",
            "base64": b64encode(raw).decode("ascii"),
        }
        size_bytes = len(raw)

    return WireRequest(
        method=str(prepared.method),
        url=str(prepared.url),
        headers=headers,
        body=body,
        size_bytes=size_bytes,
    )


def _transport_error(
    kind: str,
    error: _exc.RequestException,
    latest_request: WireRequest | None,
    prepared_count: int,
) -> TransportError:
    prepared = getattr(error, "request", None)
    request = latest_request
    if request is None and isinstance(prepared, niquests.PreparedRequest):
        request = _wire_request_of(prepared)
    return TransportError(
        kind,
        str(error),
        request=request,
        redirects_total=max(prepared_count - 1, 0),
    )


def _header_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("latin-1")
    return str(value)


def _content_type(headers: dict[str, str]) -> str | None:
    return next(
        (value for name, value in headers.items() if name.lower() == "content-type"),
        None,
    )


def _has_content_type(headers: dict[str, str]) -> bool:
    return any(k.lower() == "content-type" for k in headers)


def _encode_body(body: Any) -> tuple[str, Any, str | None]:
    """→ (niquests kwarg, payload, implied content-type or None)."""
    if isinstance(body, dict) and body.get("__binary__") is True:
        expected = {"__binary__", "content_type", "base64"}
        actual = set(body)
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(
                key if isinstance(key, str) else repr(key) for key in actual - expected
            )
            details = []
            if missing:
                details.append(f"missing {', '.join(missing)}")
            if extra:
                details.append(f"unexpected {', '.join(extra)}")
            raise RequestEncodingError(
                "invalid binary envelope fields (" + "; ".join(details) + ")"
            )
        content_type = body["content_type"]
        encoded = body["base64"]
        if not isinstance(content_type, str) or not content_type.strip():
            raise RequestEncodingError(
                "binary envelope content_type must be a non-empty string"
            )
        if not isinstance(encoded, str):
            raise RequestEncodingError("binary envelope base64 must be a string")
        try:
            decoded = b64decode(encoded, validate=True)
        except (Base64Error, UnicodeEncodeError, ValueError) as exc:
            raise RequestEncodingError(
                "binary envelope base64 is not valid canonical base64"
            ) from exc
        if b64encode(decoded).decode("ascii") != encoded:
            raise RequestEncodingError(
                "binary envelope base64 is not valid canonical base64"
            )
        return ("data", decoded, content_type)
    if isinstance(body, dict | list):
        return ("json", body, None)  # niquests sets application/json
    if isinstance(body, str):
        return ("data", body.encode("utf-8"), None)
    return ("data", stringify_native(body).encode("utf-8"), None)


def _decode_body(content: bytes, content_type: str) -> tuple[Any, int]:
    """→ (decoded body, exact response byte count). JSON parses native;
    text decodes; everything else becomes the binary envelope."""
    if not content:
        return None, 0
    mime, _, params = content_type.partition(";")
    mime = mime.strip().lower()
    charset = "utf-8"
    for param in params.split(";"):
        name, _, value = param.partition("=")
        if name.strip().lower() == "charset" and value.strip():
            charset = value.strip().strip("'\"")
    if mime == "application/json" or mime.endswith("+json"):
        try:
            return json.loads(content), len(content)
        except ValueError:
            pass  # mislabeled JSON — fall through to text
    if mime.startswith("text/") or mime in _TEXT_MIMES or not mime:
        try:
            return content.decode(charset), len(content)
        except (UnicodeDecodeError, LookupError):
            pass  # undecodable — fall through to binary
    encoded = b64encode(content).decode("ascii")
    envelope = {
        "__binary__": True,
        "content_type": content_type or "application/octet-stream",
        "base64": encoded,
    }
    return envelope, len(content)


# --------------------------------------------------------------------------
# Best-effort wire introspection


def _ms(delta: Any) -> float | None:
    if isinstance(delta, timedelta):
        return delta.total_seconds() * 1000
    return None


def _http_version_of(response: Any) -> str | None:
    info = getattr(response, "conn_info", None)
    raw = getattr(info, "http_version", None)
    if raw is None:
        return None
    text = str(getattr(raw, "value", raw))
    for tag, out in (("3", "3"), ("2", "2"), ("1.1", "1.1"), ("1.0", "1.0")):
        if tag in text:
            return out
    return text


def _timing_of(response: Any) -> dict[str, float]:
    timing: dict[str, float] = {}
    total = _ms(getattr(response, "elapsed", None))
    if total is not None:
        timing["total_ms"] = round(total, 3)
    info = getattr(response, "conn_info", None)
    for attr, key in (
        ("resolution_latency", "dns_ms"),
        ("established_latency", "connect_ms"),
        ("tls_handshake_latency", "tls_ms"),
        ("response_latency", "ttfb_ms"),
    ):
        value = _ms(getattr(info, attr, None))
        if value is not None:
            timing[key] = round(value, 3)
    return timing
