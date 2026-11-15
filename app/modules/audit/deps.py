"""The only place that reads the request for audit rows (R1): routers pass `ctx=` on."""

from typing import Annotated

from fastapi import Depends, Request

from app.core.client_ip import client_ip
from app.core.config import Settings
from app.modules.audit.context import AuditContext


def audit_context(request: Request) -> AuditContext:
    """IP as the rate limiter resolves it; the logger drops invalid IPs and cuts the UA."""
    settings: Settings = request.app.state.settings
    return AuditContext(
        ip=client_ip(request, settings.trusted_proxy_cidrs),
        user_agent=request.headers.get("user-agent"),
        request_id=getattr(request.state, "request_id", None),
    )


AuditContextDep = Annotated[AuditContext, Depends(audit_context)]
