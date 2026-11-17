"""Admin Bearer-token helpers (ADM-002): staff users and minted `aud=admin` tokens."""

import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime
from typing import Any

from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.jwt_keys import build_key_set
from app.modules.rbac.models import App, Permission
from app.modules.users.models import User
from tests.auth_factories import make_device, make_login_user
from tests.conftest import TEST_JWT_ISSUER, TEST_JWT_SIGNING_KEY
from tests.rbac_factories import assign, grant, make_role

# The app under test signs with this key (`JWT_SIGNING_KEY`), so tokens minted here verify.
KEY_SET = build_key_set(TEST_JWT_SIGNING_KEY, [])
DROP = object()  # claim/header override: leave the member out
_SIDS: dict[uuid.UUID, uuid.UUID] = {}  # user id -> account-session id (the token's `sid`)


async def admin_session(session: AsyncSession, user: User) -> uuid.UUID:
    """A live device + account session for `user`; `claims_for(user)` uses it as `sid`."""
    _, _, (account,) = await make_device(session, user)
    _SIDS[user.id] = account.id
    return account.id


async def make_staff(
    session: AsyncSession,
    *perm_keys: str,
    verified: bool = True,
    status: str = "active",
    expires_at: datetime | None = None,
) -> User:
    """A user with an app-scope admin-app role holding the (seeded) `perm_keys`; no role if none."""
    user = await make_login_user(session, verified=verified, status=status)
    if perm_keys:
        admin = await session.scalar(select(App).where(App.key == "admin"))
        assert admin is not None
        perms = list(
            await session.scalars(
                select(Permission).where(
                    Permission.app_id == admin.id, Permission.key.in_(perm_keys)
                )
            )
        )
        assert {p.key for p in perms} == set(perm_keys)
        role = await make_role(session, admin, f"admin:test-{uuid.uuid4().hex[:8]}")
        await grant(session, role, *perms)
        await assign(session, user, role, expires_at=expires_at)
    await admin_session(session, user)
    return user


def claims_for(user_or_sub: User | str, **overrides: Any) -> dict[str, Any]:
    sub = str(user_or_sub.id) if isinstance(user_or_sub, User) else user_or_sub
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": TEST_JWT_ISSUER,
        "aud": "admin",
        "sub": sub,
        "email": "staff@example.com",
        "email_verified": True,
        "roles": [],
        "iat": now,
        "exp": now + 900,
        "jti": str(uuid.uuid4()),
    }
    if isinstance(user_or_sub, User) and user_or_sub.id in _SIDS:
        claims["sid"] = str(_SIDS[user_or_sub.id])
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not DROP}


def mint(user_or_sub: User | str, **claim_overrides: Any) -> str:
    """An `aud=admin` token signed with the app's key (`DROP` removes a claim)."""
    return KEY_SET.sign(claims_for(user_or_sub, **claim_overrides))


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def mint_raw(
    headers: dict[str, Any],
    claims: dict[str, Any],
    key: ec.EllipticCurvePrivateKey | bytes | None,
    algorithm: str,
) -> str:
    """A JWS built by hand, for the alg/typ/kid cases PyJWT refuses to produce."""
    header = {"alg": algorithm, **headers}
    header = {k: v for k, v in header.items() if v is not DROP}
    signing_input = ".".join(
        _b64url(json.dumps(part, separators=(",", ":")).encode()) for part in (header, claims)
    ).encode()
    if algorithm == "none":
        signature = b""
    elif algorithm == "HS256":
        assert isinstance(key, bytes)
        signature = hmac.new(key, signing_input, hashlib.sha256).digest()
    else:
        assert isinstance(key, ec.EllipticCurvePrivateKey)
        signature = ECAlgorithm(ECAlgorithm.SHA256).sign(signing_input, key)
    return f"{signing_input.decode()}.{_b64url(signature)}"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
