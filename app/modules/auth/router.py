from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, Security, status
from fastapi.security import APIKeyCookie

from app.api.deps import SessionDep
from app.core.config import Settings
from app.core.rate_limit import rate_limit
from app.modules.auth.cookies import (
    DEVICE_COOKIE,
    clear_device_cookie,
    read_device_cookies,
    set_device_cookie,
)
from app.modules.auth.deps import (
    AppContextDep,
    ClientIpDep,
    DeviceDep,
    DeviceSessionsDep,
    get_email_hasher,
    get_password_hasher,
    get_password_policy,
    get_turnstile_verifier,
)
from app.modules.auth.hooks import SignupHook, get_signup_hook
from app.modules.auth.passwords import PasswordHasher, PasswordPolicy
from app.modules.auth.schemas import (
    LoginRequest,
    LoginResponse,
    LoginUser,
    LogoutRequest,
    SignupRequest,
    SignupResponse,
)
from app.modules.auth.service import LoginService, LogoutService, SignupService
from app.modules.auth.throttle import EmailHasher
from app.modules.auth.turnstile import TurnstileVerifier
from app.modules.tokens.deps import KeySetDep
from app.modules.tokens.schemas import TokenRequest, TokenResponse
from app.modules.tokens.service import TokenService

router = APIRouter(prefix="/auth", tags=["auth"])

# OpenAPI only: the dependencies read the raw `Cookie` header themselves (duplicates matter).
_device_cookie_scheme = APIKeyCookie(
    name=DEVICE_COOKIE, scheme_name="DeviceCookie", auto_error=False
)
DeviceCookieDoc = Annotated[str | None, Security(_device_cookie_scheme)]

_ERR: dict[str, Any] = {"description": "Error envelope"}


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


@router.post(
    "/login",
    response_model=LoginResponse,
    dependencies=[rate_limit("login")],
    responses={401: _ERR, 403: _ERR, 429: _ERR},
)
async def login(
    request: Request,
    response: Response,
    session: SessionDep,
    _app: AppContextDep,
    _cookie: DeviceCookieDoc,
    body: LoginRequest,
    remote_ip: ClientIpDep,
    devices: DeviceSessionsDep,
    hasher: Annotated[PasswordHasher, Depends(get_password_hasher)],
    verifier: Annotated[TurnstileVerifier, Depends(get_turnstile_verifier)],
    email_hasher: Annotated[EmailHasher, Depends(get_email_hasher)],
) -> LoginResponse:
    """Public (R3 class 3: per-IP limit, Turnstile after 3 failures) + Origin/`X-MM-App` (A1)."""
    result = await LoginService(
        session, hasher=hasher, verifier=verifier, email_hasher=email_hasher, devices=devices
    ).login(
        body,
        cookies=read_device_cookies(request),
        remote_ip=remote_ip,
        user_agent=request.headers.get("user-agent"),
    )
    settings: Settings = request.app.state.settings
    if result.new_cookie is not None:
        set_device_cookie(response, result.new_cookie, settings)
    response.headers["Cache-Control"] = "no-store"
    return LoginResponse(
        account_id=result.account_id,
        user=LoginUser(
            id=result.user.id,
            email=result.user.email,
            email_verified=result.user.email_verified_at is not None,
        ),
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={401: _ERR, 403: _ERR},
)
async def logout(
    request: Request,
    response: Response,
    _app: AppContextDep,
    _cookie: DeviceCookieDoc,
    body: LogoutRequest,
    device: DeviceDep,
    devices: DeviceSessionsDep,
) -> None:
    """Cookie class (R3 class 2): `__Host-mm_device` + Origin + `X-MM-App`."""
    await LogoutService(devices).logout(body, device)
    settings: Settings = request.app.state.settings
    if body.all:
        clear_device_cookie(response, settings)
    elif device.new_cookie is not None:
        set_device_cookie(response, device.new_cookie, settings)
    response.headers["Cache-Control"] = "no-store"


@router.post(
    "/token",
    response_model=TokenResponse,
    dependencies=[rate_limit("token")],
    responses={401: _ERR, 403: _ERR, 422: _ERR, 429: _ERR},
)
async def token(
    request: Request,
    response: Response,
    session: SessionDep,
    app: AppContextDep,
    _cookie: DeviceCookieDoc,
    body: TokenRequest,
    device: DeviceDep,
    key_set: KeySetDep,
) -> TokenResponse:
    """Cookie class (R3 class 2): `__Host-mm_device` + Origin + `X-MM-App`; per-app JWT (D7)."""
    settings: Settings = request.app.state.settings
    issued = await TokenService(
        session,
        key_set=key_set,
        issuer=settings.jwt_issuer,
        ttl_seconds=settings.access_token_ttl_seconds,
    ).issue(app, device, body)
    if device.new_cookie is not None:
        set_device_cookie(response, device.new_cookie, settings)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return TokenResponse(
        access_token=issued.access_token, expires_in=issued.expires_in, audience=app.key
    )
