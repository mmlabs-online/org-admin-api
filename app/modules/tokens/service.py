"""Per-app access tokens (D7, ADR-0005): checks in the order of the USR-03c plan, then sign."""

import logging
import re
import time
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import TypeAdapter, ValidationError
from pydantic_core import PydanticCustomError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import RateLimitedError
from app.core.jwt_keys import KeySet
from app.core.rate_limit import RateLimit, RateLimiter
from app.core.text import reject_disallowed_categories
from app.modules.audit.actions import AuditAction, AuditTarget
from app.modules.audit.context import AuditContext, EndUser
from app.modules.audit.service import AuditLogger
from app.modules.entitlements.repository import EntitlementRepository, EntitlementView
from app.modules.entitlements.schemas import PLAN_PATTERN, Limits
from app.modules.membership.service import join_available
from app.modules.rbac.authz import AuthzService
from app.modules.rbac.models import ADMIN_APP_KEY, App, OrgMode
from app.modules.sessions.errors import SessionInvalidError
from app.modules.sessions.repository import SessionRepository
from app.modules.sessions.service import ResolvedDevice
from app.modules.tokens.errors import (
    AudienceMismatchError,
    EmailUnverifiedError,
    NotMemberError,
    OrgAccessDeniedError,
    OrgNotSupportedError,
)
from app.modules.tokens.schemas import TokenRequest
from app.modules.users.models import UserStatus
from app.modules.users.repository import UserRepository

logger = logging.getLogger(__name__)

_LIMITS = TypeAdapter(Limits)
NAME_CLAIM_MAX_LEN = 100  # matches users.display_name String(100)


def _ent_claim(view: EntitlementView, aud: str) -> dict[str, object] | None:
    """The `ent` claim; a row that breaks the ADM-006 contract fails closed to `None`."""
    try:
        if re.fullmatch(PLAN_PATTERN, view.plan) is None:
            raise ValueError("plan")
        _LIMITS.validate_python(view.limits, strict=True)
    except (ValueError, ValidationError):
        logger.warning("ent_out_of_contract entitlement_id=%s aud=%s", view.id, aud)
        return None
    return {"plan": view.plan, "limits": dict(view.limits)}


def _name_claim(display_name: str | None, *, user_id: uuid.UUID, aud: str) -> str | None:
    """The `name` claim (USR-32): NFC text, 1-100 allowed characters, else `None` (fail closed)."""
    if display_name is None:
        return None
    value = unicodedata.normalize("NFC", display_name)
    if value.strip() == "":
        return None
    try:
        if len(value) > NAME_CLAIM_MAX_LEN:
            raise ValueError("length")
        reject_disallowed_categories(value)
    except (ValueError, PydanticCustomError):
        logger.warning("name_out_of_contract user_id=%s aud=%s", user_id, aud)
        return None
    return value


def _denied() -> OrgAccessDeniedError:
    """The one org refusal: unknown, suspended, foreign org and non-holder look the same."""
    return OrgAccessDeniedError("No access to this organization", details={})


@dataclass(frozen=True)
class IssuedToken:
    access_token: str
    expires_in: int


class TokenService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        key_set: KeySet,
        issuer: str,
        ttl_seconds: int,
        super_admin_ttl_seconds: int,
        limiter: RateLimiter,
        super_admin_limit: RateLimit,
        join_reject_cooldown_seconds: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.accounts = SessionRepository(session)
        self.users = UserRepository(session)
        self.authz = AuthzService(session)
        self.entitlements = EntitlementRepository(session)
        self.key_set = key_set
        self.issuer = issuer
        self.ttl = ttl_seconds
        self.super_admin_ttl = super_admin_ttl_seconds
        self.limiter = limiter
        self.super_admin_limit = super_admin_limit
        self.audit = AuditLogger(session)
        self.join_reject_cooldown_seconds = join_reject_cooldown_seconds
        self.session = session
        self.clock = clock

    async def issue(
        self, app: App, device: ResolvedDevice, data: TokenRequest, *, ctx: AuditContext
    ) -> IssuedToken:
        if data.audience != app.key:
            raise AudienceMismatchError("Audience does not match the calling app")
        if data.org_id is not None and app.org_mode == OrgMode.NONE.value:
            raise OrgNotSupportedError(
                "This app does not use organizations",
                details=[{"field": "org_id", "reason": "not_supported"}],
            )
        # No `headers`: the cookie is not cleared, other accounts on the device stay signed in.
        account = await self.accounts.get_active_account(
            device.device_id, data.account_id, created_before=device.accounts_created_before
        )
        if account is None:
            raise SessionInvalidError("Session is invalid")
        user = await self.users.get(account.user_id)
        if user is None or user.status != UserStatus.ACTIVE.value:
            raise SessionInvalidError("Session is invalid")
        if user.email_verified_at is None:
            raise EmailUnverifiedError("Email address is not verified")
        roles = await self.authz.effective_roles(user.id, app.key)
        if not roles:
            available = await join_available(
                self.session,
                app,
                user.id,
                reject_cooldown_seconds=self.join_reject_cooldown_seconds,
            )
            raise NotMemberError("Not a member of this app", details={"join_available": available})

        claims: dict[str, object] = {}
        super_admin_org = False
        if data.org_id is not None:
            # Org-exact: the app-scope role above must not make an unknown, suspended or
            # foreign org look usable. One answer for all three (no existence oracle).
            org_roles = await self.authz.org_role_keys(user.id, app.key, data.org_id)
            if not org_roles:
                # USR-39, ADR-0030 §7: the app's super-admin holder may enter any org of the
                # app. Every refusal is the same error, and the org is looked up last.
                if not await self.authz.app_super_admin_access(user.id, app):
                    raise _denied()
                if not await self.authz.is_org_of_app(data.org_id, app.id):
                    raise _denied()
                retry_after = self.limiter.hit(
                    "super_admin_org_token", str(user.id), self.super_admin_limit
                )
                if retry_after is not None:
                    raise RateLimitedError(retry_after)
                super_admin_org = True  # `roles` stays the app-scope roles from above
            else:
                roles = await self.authz.effective_roles(user.id, app.key, data.org_id)
            claims["org"] = str(data.org_id)
            claims["org_roles"] = org_roles  # org-exact only (ADR-0015); `roles` stays the union
            if super_admin_org:
                claims["org_access"] = "super_admin"
            if app.key != ADMIN_APP_KEY:  # invariant: `ent` never sits next to `sid`
                view = await self.entitlements.get_active_for(data.org_id, app.id)
                claims["ent"] = None if view is None else _ent_claim(view, app.key)

        if app.key == ADMIN_APP_KEY:
            claims["sid"] = str(account.id)  # admin tokens end with this account session (USR-14)
        elif (name := _name_claim(user.display_name, user_id=user.id, aud=app.key)) is not None:
            claims["name"] = name  # app tokens only; absent when unset (USR-32)

        ttl = self.super_admin_ttl if super_admin_org else self.ttl
        now = int(self.clock())
        jti = str(uuid.uuid4())
        token = self.key_set.sign(
            {
                "iss": self.issuer,
                "aud": app.key,
                "sub": str(user.id),
                "email": user.email,
                "email_verified": True,  # unverified users were refused above
                "roles": roles,
                **claims,
                "iat": now,
                "exp": now + ttl,
                "jti": jti,
            }
        )
        if super_admin_org and data.org_id is not None and app.super_admin_role_id is not None:
            # In the request transaction: a failed audit row fails the request (R4).
            await self.audit.record(
                AuditAction.TOKEN_SUPER_ADMIN_ORG_ISSUED,
                actor=EndUser(user.id),
                ctx=ctx,
                target=(AuditTarget.ORGANIZATION, data.org_id),
                app_key=app.key,
                org_id=data.org_id,
                changes={
                    "jti": jti,
                    "account_session_id": str(account.id),
                    "role_id": str(app.super_admin_role_id),
                    "expires_at": datetime.fromtimestamp(now + ttl, UTC).isoformat(),
                },
            )
        logger.info(
            "token_issued user_id=%s account_session_id=%s aud=%s org_id=%s kid=%s jti=%s"
            " super_admin_org=%s",
            user.id,
            account.id,
            app.key,
            data.org_id,
            self.key_set.signing_kid,
            jti,
            super_admin_org,
        )
        return IssuedToken(access_token=token, expires_in=ttl)
