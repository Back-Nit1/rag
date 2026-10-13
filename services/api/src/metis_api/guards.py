"""Request-edge guards that run before any route code.

* ``HostGuardMiddleware`` — a loopback server with no real credential (fixture
  identity, the local dev API) must not answer a browser that was DNS-rebound
  to 127.0.0.1, so unknown ``Host`` values are refused. Skipped in cloud mode,
  where API Gateway owns the hostname and the JWT authorizer owns identity.
* ``BodyLimitMiddleware`` — bounds bytes *as they arrive*, so an oversized body
  is refused before it is buffered, not after.
* ``clean_correlation_id`` — a client-supplied id is echoed into logs, queue
  messages and response headers; only a short safe token is accepted.
"""

from __future__ import annotations

import os
import re
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any
from urllib.parse import urlsplit

from metis_contracts import ErrorCode, MetisError, new_id, settings

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]

#: Every JSON route. The largest legitimate body is a 100k-character note.
MAX_JSON_BYTES = 2 * 1024 * 1024
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "testserver"}
_CORRELATION_ID = re.compile(r"[A-Za-z0-9_.:-]{1,64}")


def clean_correlation_id(value: str | None) -> str:
    return value if value and _CORRELATION_ID.fullmatch(value) else new_id("run")


def allowed_hosts() -> set[str]:
    extra = {h.strip().lower() for h in os.environ.get("METIS_ALLOWED_HOSTS", "").split(",") if h.strip()}
    return _LOOPBACK_HOSTS | extra


class HostGuardMiddleware:
    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not settings().is_cloud:
            raw = dict(scope["headers"]).get(b"host", b"").decode("latin-1")
            try:
                host = (urlsplit("//" + raw).hostname or "").lower()
            except ValueError:
                host = ""
            if host not in allowed_hosts():
                body = b'{"code":"forbidden","message":"unexpected Host header"}'
                await send(
                    {
                        "type": "http.response.start",
                        "status": 403,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


class BodyLimitMiddleware:
    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        upload = scope["path"].startswith("/v1/local-uploads/")
        limit = settings().limits.max_upload_bytes if upload else MAX_JSON_BYTES
        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > limit:
            body = (
                b'{"code":"upload_too_large","message":"the request body is over '
                + str(limit).encode()
                + b' bytes"}'
            )
            await send(
                {
                    "type": "http.response.start",
                    "status": 413,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        seen = 0

        async def counted() -> MutableMapping[str, Any]:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    # Chunked bodies have no declared length: stop reading at the limit
                    # (the framework reports it as a malformed body).
                    raise MetisError(ErrorCode.UPLOAD_TOO_LARGE, f"the request body is over {limit} bytes")
            return message

        await self.app(scope, counted, send)
