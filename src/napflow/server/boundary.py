"""Local request trust boundary and serialized source writes (D37)."""

import asyncio
import ipaddress
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from blacksheep import Request, Response, WebSocket
from blacksheep.server.responses import json as json_response

WS_REQUEST_ORIGIN = 4403

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True)
class Authority:
    scheme: str
    host: str
    port: int


def request_scheme(request: Request) -> str:
    scheme = request.scheme.lower()
    if scheme == "ws":
        return "http"
    if scheme == "wss":
        return "https"
    return scheme


def parse_authority(value: str, scheme: str) -> Authority | None:
    """Parse a Host authority without accepting URL-shaped surprises."""
    try:
        parsed = urlsplit(f"//{value}")
        port = parsed.port
    except ValueError:
        return None
    if (
        not value
        or value.endswith(":")
        or any(char.isspace() for char in value)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.hostname is None
    ):
        return None
    host = parsed.hostname.lower().removesuffix(".")
    if port is None:
        port = 443 if scheme == "https" else 80
    if not 1 <= port <= 65535:
        return None
    return Authority(scheme=scheme, host=host, port=port)


def is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def request_authority(request: Request) -> Authority | None:
    hosts = request.get_headers(b"host")
    if len(hosts) != 1:
        return None
    try:
        raw_host = hosts[0].decode("ascii")
    except UnicodeDecodeError:
        return None
    authority = parse_authority(raw_host, request_scheme(request))
    if authority is None or not is_loopback_host(authority.host):
        return None
    return authority


def origin_matches(request: Request, authority: Authority) -> bool:
    origins = request.get_headers(b"origin")
    if not origins:
        # Origin is a browser boundary. Programmatic localhost clients such
        # as niquests and websockets remain supported without fabricating it.
        return True
    if len(origins) != 1:
        return False
    try:
        value = origins[0].decode("ascii")
        parsed = urlsplit(value)
        port = parsed.port
    except (UnicodeDecodeError, ValueError):
        return False
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.netloc == ""
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.hostname is None
    ):
        return False
    host = parsed.hostname.lower().removesuffix(".")
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return Authority(parsed.scheme, host, port) == authority


class LocalRequestBoundary:
    """Loopback Host + browser same-origin boundary (D37/FR-1108)."""

    async def __call__(
        self,
        request: Request,
        next_handler: Callable[[Request], Awaitable[Response | None]],
    ) -> Response | None:
        authority = request_authority(request)
        is_websocket = isinstance(request, WebSocket)
        origin_required = is_websocket or request.method.upper() in UNSAFE_METHODS
        if authority is None or (
            origin_required and not origin_matches(request, authority)
        ):
            if is_websocket:
                await request.close(WS_REQUEST_ORIGIN, "request origin rejected")
                return None
            return json_response(
                {
                    "error": "request_origin",
                    "message": "request rejected by local server boundary",
                },
                status=403,
            )
        return await next_handler(request)


class SourceWriteCoordinator:
    """Per-canonical-file serialization for ETag check + replacement."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def lock(self, path: Path) -> AsyncIterator[None]:
        key = os.path.normcase(str(path))
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            yield
