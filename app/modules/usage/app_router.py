"""App backends' usage metering — R3 class 4 (a), `usage:read` / `usage:write` app credentials
(ADM-042, ADR-0022).

Server-to-server only: no CORS, per-IP limit before auth, a failed-auth bucket, then a per-key
limit shared by the four routes. POST bodies are read by hand after auth (see
`releases/body.py`). The idempotency key and emails are never logged (R11).
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response

from app.api.deps import SessionDep
from app.core.rate_limit import enforce_subject_limit, rate_limit
from app.modules.app_credentials.deps import (
    UsageConsumeCredentialDep,
    UsageReadCredentialDep,
    UsageRefundCredentialDep,
    UsageSettleCredentialDep,
)
from app.modules.releases.body import inline_schema, read_json_body
from app.modules.usage.meter_schemas import (
    MetricKey,
    UsageConsume,
    UsageEventResult,
    UsageRefund,
    UsageSettle,
    UserUsageRead,
)
from app.modules.usage.meter_service import UsageMeterService

router = APIRouter(prefix="/apps", tags=["app-usage"])

_COMMON: dict[int | str, dict[str, Any]] = {
    401: {"description": "Missing or invalid app credential"},
    403: {
        "description": "app_credential_mismatch, app_credential_scope, app_disabled or "
        "usage_user_denied (unknown, non-member or disabled user)"
    },
    429: {"description": "Rate limited"},
}
_POST: dict[int | str, dict[str, Any]] = {
    **_COMMON,
    411: {"description": "Content-Length required"},
    413: {"description": "Body too large"},
    415: {"description": "Content-Type must be application/json"},
    422: {"description": "Invalid body (details have no values)"},
}


def _body(model: type[UsageConsume] | type[UsageSettle] | type[UsageRefund]) -> dict[str, Any]:
    return {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": inline_schema(model)}},
        }
    }


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@router.get(
    "/{key}/usage/users/{user_id}",
    response_model=UserUsageRead,
    dependencies=[rate_limit("usage")],
    summary="A user's limits and usage for the app (usage:read or usage:write app credential)",
    description=(
        "Every active metric of the app with the user's limit, used and remaining in the "
        "current period; `metric` narrows it to one. No email or name. Server-to-server only, "
        "no CORS; never cached."
    ),
    responses={
        **_COMMON,
        404: {"description": "usage_metric_not_found (unknown or archived `metric`)"},
        422: {"description": "Invalid user id or metric key"},
    },
)
async def read_usage(
    request: Request,
    response: Response,
    session: SessionDep,
    caller: UsageReadCredentialDep,
    key: str,
    user_id: uuid.UUID,
    metric: Annotated[MetricKey | None, Query()] = None,
) -> UserUsageRead:
    enforce_subject_limit(request, "usage_key", caller.key_id)
    _no_store(response)
    return await UsageMeterService(session, request.app.state.settings).read(
        caller, user_id, metric
    )


@router.post(
    "/{key}/usage/consume",
    response_model=UsageEventResult,
    dependencies=[rate_limit("usage")],
    summary="Consume usage before an action (usage:write app credential)",
    description=(
        "Atomically charges `amount` if it fits the user's limit, else 409 "
        "`usage_limit_exceeded` and nothing is consumed. A retried `idempotency_key` with the "
        "same payload returns the first result (200, `replayed: true`, current limit/used); a "
        "different payload gives 409 `idempotency_key_reused`. Server-to-server only, no CORS."
    ),
    responses={
        **_POST,
        404: {"description": "usage_metric_not_found (unknown or archived metric)"},
        409: {"description": "usage_limit_exceeded or idempotency_key_reused"},
    },
    openapi_extra=_body(UsageConsume),
)
async def consume_usage(
    request: Request,
    response: Response,
    session: SessionDep,
    caller: UsageConsumeCredentialDep,
    key: str,
) -> UsageEventResult:
    enforce_subject_limit(request, "usage_key", caller.key_id)
    settings = request.app.state.settings
    body = await read_json_body(
        request,
        UsageConsume,
        cap=settings.usage_max_bytes,
        timeout_s=settings.usage_read_timeout_seconds,
    )
    _no_store(response)
    return await UsageMeterService(session, settings).consume(caller, body)


@router.post(
    "/{key}/usage/settle",
    response_model=UsageEventResult,
    dependencies=[rate_limit("usage")],
    summary="Settle the actual amount of a consume (usage:write app credential)",
    description=(
        "Replaces the consumed estimate by the actual `amount`, once, within the adjust window. "
        "A settle above the estimate is accepted even past the limit. Charged to the period of "
        "the original consume. Server-to-server only, no CORS."
    ),
    responses={
        **_POST,
        404: {"description": "usage_event_not_found"},
        409: {
            "description": "idempotency_key_reused, usage_event_state_conflict or "
            "usage_adjust_window_closed"
        },
    },
    openapi_extra=_body(UsageSettle),
)
async def settle_usage(
    request: Request,
    response: Response,
    session: SessionDep,
    caller: UsageSettleCredentialDep,
    key: str,
) -> UsageEventResult:
    enforce_subject_limit(request, "usage_key", caller.key_id)
    settings = request.app.state.settings
    body = await read_json_body(
        request,
        UsageSettle,
        cap=settings.usage_max_bytes,
        timeout_s=settings.usage_read_timeout_seconds,
    )
    _no_store(response)
    return await UsageMeterService(session, settings).settle(caller, body)


@router.post(
    "/{key}/usage/refund",
    response_model=UsageEventResult,
    dependencies=[rate_limit("usage")],
    summary="Refund a consume after a failed action (usage:write app credential)",
    description=(
        "Credits the whole current charge of the consume, once, within the adjust window; never "
        "below zero. A repeat returns the refunded result. Server-to-server only, no CORS."
    ),
    responses={
        **_POST,
        404: {"description": "usage_event_not_found"},
        409: {"description": "idempotency_key_reused or usage_adjust_window_closed"},
    },
    openapi_extra=_body(UsageRefund),
)
async def refund_usage(
    request: Request,
    response: Response,
    session: SessionDep,
    caller: UsageRefundCredentialDep,
    key: str,
) -> UsageEventResult:
    enforce_subject_limit(request, "usage_key", caller.key_id)
    settings = request.app.state.settings
    body = await read_json_body(
        request,
        UsageRefund,
        cap=settings.usage_max_bytes,
        timeout_s=settings.usage_read_timeout_seconds,
    )
    _no_store(response)
    return await UsageMeterService(session, settings).refund(caller, body)
