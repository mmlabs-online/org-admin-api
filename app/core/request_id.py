"""Request id (ADM-004, ADM-002d): a new UUID4 per request, stored and echoed.

Always generated, never taken from the client: an inbound `X-Request-ID` is ignored from any
peer, so the stored id is unique and no client can choose it. The echoed id equals the stored
id. Pure ASGI, outermost user middleware, so CORS preflights and error envelopes carry it.
"""

import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

HEADER = "x-request-id"


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = request_id

        async def _send(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).append(HEADER, request_id)
            await send(message)

        await self.app(scope, receive, _send)
