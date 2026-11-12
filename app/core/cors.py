"""Credentialed CORS for the identity endpoints, from registered app origins (ADR-0006, D12).

Pure ASGI. Scope: paths under `path_prefix` (`/api/v1/auth/`) only; everything else passes
through untouched (`/.well-known/jwks.json` sets its own `ACAO: *`). The `Origin` is matched
raw and exactly against `OriginRegistry` and echoed as received. Preflights are answered here
and never routed. CORS only decides whether a page may read a response; the per-request
`Origin` + `X-MM-App` check (CSRF) stays in the auth service.
"""

import logging
from typing import Protocol

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

ALLOW_METHODS = frozenset({"GET", "POST"})
ALLOW_HEADERS = frozenset({"content-type", "x-mm-app"})
MAX_AGE_SECONDS = 600
EXPOSE_HEADERS = "Retry-After"
_PREFLIGHT_VARY = "Origin, Access-Control-Request-Method, Access-Control-Request-Headers"


class OriginRegistry(Protocol):
    async def is_allowed(self, origin: str) -> bool: ...


class DynamicCORSMiddleware:
    def __init__(self, app: ASGIApp, *, registry: OriginRegistry, path_prefix: str) -> None:
        self.app = app
        self.registry = registry
        self.path_prefix = path_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(self.path_prefix):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        values = headers.getlist("origin")
        origin = values[0] if len(values) == 1 and values[0] != "null" else None
        allowed = origin is not None and await self.registry.is_allowed(origin)
        if origin is not None and not allowed:
            logger.debug("cors_origin_rejected origin=%r", origin[:64])

        if scope["method"] == "OPTIONS" and "access-control-request-method" in headers:
            await self._preflight(headers, origin if allowed else None, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                out = MutableHeaders(scope=message)
                vary = ",".join(out.getlist("vary")).split(",")
                if "origin" not in {v.strip().lower() for v in vary}:
                    out.add_vary_header("Origin")
                if allowed and origin is not None:
                    out["Access-Control-Allow-Origin"] = origin
                    out["Access-Control-Allow-Credentials"] = "true"
                    out["Access-Control-Expose-Headers"] = EXPOSE_HEADERS
                if "cache-control" not in out:
                    out["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_wrapper)

    @staticmethod
    async def _preflight(headers: Headers, origin: str | None, send: Send) -> None:
        out = MutableHeaders()
        out["Vary"] = _PREFLIGHT_VARY
        requested = [
            h.strip().lower()
            for h in headers.get("access-control-request-headers", "").split(",")
            if h.strip()
        ]
        if (
            origin is not None
            and headers["access-control-request-method"] in ALLOW_METHODS
            and all(h in ALLOW_HEADERS for h in requested)
        ):
            out["Access-Control-Allow-Origin"] = origin
            out["Access-Control-Allow-Credentials"] = "true"
            out["Access-Control-Allow-Methods"] = ", ".join(sorted(ALLOW_METHODS))
            out["Access-Control-Allow-Headers"] = "Content-Type, X-MM-App"
            out["Access-Control-Max-Age"] = str(MAX_AGE_SECONDS)
        await send({"type": "http.response.start", "status": 204, "headers": out.raw})
        await send({"type": "http.response.body", "body": b""})
