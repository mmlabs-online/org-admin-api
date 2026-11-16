"""Dynamic CORS from registered app origins (ADR-0006 identity, ADR-0011 admin).

Pure ASGI, one class with a `CORSPolicy`. Scope: paths under `path_prefix` (identity:
`/api/v1/auth/`, credentialed) or the requests a `matcher` selects (admin: Bearer routes,
non-credentialed); everything else passes through untouched (`/.well-known/jwks.json` sets its
own `ACAO: *`). The `Origin` is matched raw and exactly against `OriginRegistry` and echoed as
received. Preflights are answered here and never routed. CORS only decides whether a page may
read a response; the per-request `Origin` + `X-MM-App` check (CSRF) stays in the auth service.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

_PREFLIGHT_VARY = "Origin, Access-Control-Request-Method, Access-Control-Request-Headers"


@dataclass(frozen=True, slots=True)
class CORSPolicy:
    credentials: bool
    allow_methods: frozenset[str]
    allow_headers: frozenset[str]  # lowercase, for matching
    allow_headers_value: str  # as emitted
    expose_headers: str
    max_age: int


IDENTITY_POLICY = CORSPolicy(
    credentials=True,
    allow_methods=frozenset({"GET", "POST"}),
    allow_headers=frozenset({"content-type", "x-mm-app"}),
    allow_headers_value="Content-Type, X-MM-App",
    expose_headers="Retry-After",
    max_age=600,
)
ADMIN_POLICY = CORSPolicy(
    credentials=False,
    allow_methods=frozenset({"GET", "POST", "PATCH", "PUT", "DELETE"}),
    allow_headers=frozenset({"authorization", "content-type"}),
    allow_headers_value="Authorization, Content-Type",
    expose_headers="Retry-After, ETag, X-Request-ID",
    max_age=600,
)

# (scope, method) -> in scope; `method` is `Access-Control-Request-Method` on a preflight.
RouteMatcher = Callable[[Scope, str], bool]


class OriginRegistry(Protocol):
    async def is_allowed(self, origin: str) -> bool: ...


class DynamicCORSMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        registry: OriginRegistry,
        policy: CORSPolicy = IDENTITY_POLICY,
        path_prefix: str | None = None,
        matcher: RouteMatcher | None = None,
    ) -> None:
        if (path_prefix is None) == (matcher is None):
            raise ValueError("exactly one of path_prefix and matcher is required")
        self.app = app
        self.registry = registry
        self.policy = policy
        self.path_prefix = path_prefix
        self.matcher = matcher

    def _in_scope(self, scope: Scope, headers: Headers, preflight: bool) -> bool:
        if self.matcher is None:
            return bool(scope["path"].startswith(str(self.path_prefix)))
        method = headers["access-control-request-method"] if preflight else scope["method"]
        return self.matcher(scope, method)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        preflight = scope["method"] == "OPTIONS" and "access-control-request-method" in headers
        if not self._in_scope(scope, headers, preflight):
            await self.app(scope, receive, send)
            return

        values = headers.getlist("origin")
        origin = values[0] if len(values) == 1 and values[0] != "null" else None
        allowed = origin is not None and await self.registry.is_allowed(origin)
        if origin is not None and not allowed:
            logger.debug("cors_origin_rejected origin=%r", origin[:64])

        if preflight:
            await self._preflight(headers, origin if allowed else None, send)
            return

        policy = self.policy

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                out = MutableHeaders(scope=message)
                vary = ",".join(out.getlist("vary")).split(",")
                if "origin" not in {v.strip().lower() for v in vary}:
                    out.add_vary_header("Origin")
                if allowed and origin is not None:
                    out["Access-Control-Allow-Origin"] = origin
                    if policy.credentials:
                        out["Access-Control-Allow-Credentials"] = "true"
                    out["Access-Control-Expose-Headers"] = policy.expose_headers
                if "cache-control" not in out:
                    out["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_wrapper)

    async def _preflight(self, headers: Headers, origin: str | None, send: Send) -> None:
        policy = self.policy
        out = MutableHeaders()
        out["Vary"] = _PREFLIGHT_VARY
        requested = [
            h.strip().lower()
            for h in headers.get("access-control-request-headers", "").split(",")
            if h.strip()
        ]
        if (
            origin is not None
            and headers["access-control-request-method"] in policy.allow_methods
            and all(h in policy.allow_headers for h in requested)
        ):
            out["Access-Control-Allow-Origin"] = origin
            if policy.credentials:
                out["Access-Control-Allow-Credentials"] = "true"
            out["Access-Control-Allow-Methods"] = ", ".join(sorted(policy.allow_methods))
            out["Access-Control-Allow-Headers"] = policy.allow_headers_value
            out["Access-Control-Max-Age"] = str(policy.max_age)
        await send({"type": "http.response.start", "status": 204, "headers": out.raw})
        await send({"type": "http.response.body", "body": b""})
