from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import SessionDep
from app.core.rate_limit import rate_limit
from app.modules.auth.deps import (
    ClientIpDep,
    get_password_hasher,
    get_password_policy,
    get_turnstile_verifier,
)
from app.modules.auth.hooks import SignupHook, get_signup_hook
from app.modules.auth.passwords import PasswordHasher, PasswordPolicy
from app.modules.auth.schemas import SignupRequest, SignupResponse
from app.modules.auth.service import SignupService
from app.modules.auth.turnstile import TurnstileVerifier

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/signup",
    response_model=SignupResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[rate_limit("signup")],
)
async def signup(
    session: SessionDep,
    body: SignupRequest,
    remote_ip: ClientIpDep,
    policy: Annotated[PasswordPolicy, Depends(get_password_policy)],
    hasher: Annotated[PasswordHasher, Depends(get_password_hasher)],
    verifier: Annotated[TurnstileVerifier, Depends(get_turnstile_verifier)],
    hook: Annotated[SignupHook, Depends(get_signup_hook)],
) -> SignupResponse:
    """Public by design (R3 class 3): per-IP rate limit + Turnstile."""
    user = await SignupService(
        session, policy=policy, hasher=hasher, verifier=verifier, hook=hook
    ).signup(body, remote_ip)
    return SignupResponse(
        id=user.id,
        email=user.email,
        email_verified=user.email_verified_at is not None,
        created_at=user.created_at,
    )
