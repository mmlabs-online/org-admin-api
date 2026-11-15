"""Request id (ADM-004): keep a safe inbound `X-Request-ID`, else a new UUID4; echo it always.

Pure ASGI, outermost user middleware, so CORS preflights and error envelopes carry it. The
inbound value is never logged.
"""

import re
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

HEADER = "x-request-id"
_SAFE = re.compile(r"[A-Za-z0-9._-]{1,64}")


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        inbound = next((v for k, v in scope["headers"] if k == HEADER.encode()), b"")
        value = inbound.decode("latin-1")
        request_id = value if _SAFE.fullmatch(value) else str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = request_id

        async def _send(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).append(HEADER, request_id)
            await send(message)

        await self.app(scope, receive, _send)
