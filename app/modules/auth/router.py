from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, Security, status
from fastapi.security import APIKeyCookie

from app.api.deps import SessionDep
from app.core.config import Settings
from app.core.rate_limit import rate_limit
from app.modules.audit.recorder import AuditRecorder, LogAuditRecorder
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
    EmailDispatcherDep,
    get_email_hasher,
    get_link_origin,
    get_password_hasher,
    get_password_policy,
    get_signup_hook,
    get_turnstile_verifier,
)
from app.modules.auth.email_verification import EmailVerificationService
from app.modules.auth.hooks import SignupHook
from app.modules.auth.password_reset import PasswordResetService
from app.modules.auth.passwords import PasswordHasher, PasswordPolicy
from app.modules.auth.schemas import (
    AccountList,
    AccountRead,
    ForgotPasswordRequest,
    LoginRequest,
    LoginResponse,
    LoginUser,
    LogoutRequest,
    ResendVerificationRequest,
    ResetPasswordRequest,
    SignupRequest,
    SignupResponse,
    VerifyEmailRequest,
)
from app.modules.auth.service import LoginService, LogoutService, SignupService
from app.modules.auth.throttle import EmailHasher
from app.modules.auth.turnstile import TurnstileVerifier
from app.modules.membership.schemas import JoinRequest, JoinResponse
from app.modules.membership.service import JoinService
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

_audit_recorder = LogAuditRecorder()


def get_audit_recorder() -> AuditRecorder:
    """Placeholder until ADM-004; tests override it."""
    return _audit_recorder


@router.post(
    "/signup",
    response_model=SignupResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[rate_limit("signup")],
    responses={403: _ERR, 409: _ERR, 422: _ERR, 429: _ERR},
)
async def signup(
    session: SessionDep,
    _app: AppContextDep,
    body: SignupRequest,
    remote_ip: ClientIpDep,
    policy: Annotated[PasswordPolicy, Depends(get_password_policy)],
    hasher: Annotated[PasswordHasher, Depends(get_password_hasher)],
    verifier: Annotated[TurnstileVerifier, Depends(get_turnstile_verifier)],
    hook: Annotated[SignupHook, Depends(get_signup_hook)],
) -> SignupResponse:
    """Public by design (R3 class 3): per-IP rate limit + Turnstile, plus the required app
    context: `X-MM-App` of an active app and an `Origin` registered for it (403 otherwise,
    before any account is created).

    The verification link uses the validated `Origin`; the email is queued after the commit.
    """
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


@router.get(
    "/accounts",
    response_model=AccountList,
    dependencies=[rate_limit("accounts")],
    responses={401: _ERR, 403: _ERR, 429: _ERR},
)
async def list_accounts(
    request: Request,
    response: Response,
    _app: AppContextDep,
    _cookie: DeviceCookieDoc,
    device: DeviceDep,
    devices: DeviceSessionsDep,
) -> AccountList:
    """Cookie class (R3 class 2): `__Host-mm_device` + Origin + `X-MM-App`.

    `AppContextDep` comes before `DeviceDep`: a bad app context answers 403 before any device
    lookup or cookie rotation.
    """
    rows = await devices.list_accounts(device)
    settings: Settings = request.app.state.settings
    if device.new_cookie is not None:
        set_device_cookie(response, device.new_cookie, settings)
    response.headers["Cache-Control"] = "no-store"
    return AccountList(
        items=[
            AccountRead(
                account_id=account.id,
                email=user.email,
                email_verified=user.email_verified_at is not None,
                display_name=user.display_name,
            )
            for account, user in rows
        ]
    )


@router.post(
    "/apps/{key}/join",
    response_model=JoinResponse,
    dependencies=[rate_limit("join")],
    responses={401: _ERR, 403: _ERR, 422: _ERR, 429: _ERR},
)
async def join_app(
    request: Request,
    response: Response,
    session: SessionDep,
    key: str,
    app: AppContextDep,
    _cookie: DeviceCookieDoc,
    body: JoinRequest,
    device: DeviceDep,
    recorder: Annotated[AuditRecorder, Depends(get_audit_recorder)],
) -> JoinResponse:
    """Cookie class (R3 class 2): `__Host-mm_device` + Origin + `X-MM-App`; `{key}` must be the
    calling app. "Continue as <account>" is the consent (D8). 200 for a new or an existing
    membership (idempotent)."""
    await JoinService(session, recorder).join(app, device, key, body)
    settings: Settings = request.app.state.settings
    if device.new_cookie is not None:
        set_device_cookie(response, device.new_cookie, settings)
    response.headers["Cache-Control"] = "no-store"
    return JoinResponse(status="member")


@router.post(
    "/verify-email",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    dependencies=[rate_limit("verify_email")],
    responses={422: _ERR, 429: _ERR},
)
async def verify_email(
    request: Request,
    response: Response,
    session: SessionDep,
    body: VerifyEmailRequest,
) -> None:
    """Public (R3 class 3, per-IP limit): the one-time token is the credential."""
    settings: Settings = request.app.state.settings
    await EmailVerificationService(
        session, email_hasher=get_email_hasher(request), daily_cap=settings.email_daily_cap
    ).verify(body.token)
    response.headers["Cache-Control"] = "no-store"


@router.post(
    "/verify-email/resend",
    status_code=status.HTTP_202_ACCEPTED,
    response_class=Response,
    dependencies=[rate_limit("verify_email_resend")],
    responses={401: _ERR, 403: _ERR, 409: _ERR, 422: _ERR, 429: _ERR},
)
async def resend_verification(
    request: Request,
    response: Response,
    session: SessionDep,
    background_tasks: BackgroundTasks,
    _cookie: DeviceCookieDoc,
    _app: AppContextDep,
    body: ResendVerificationRequest,
    link_origin: Annotated[str, Depends(get_link_origin)],
    device: DeviceDep,
    dispatcher: EmailDispatcherDep,
) -> None:
    """Cookie class (R3 class 2): `__Host-mm_device` + Origin + `X-MM-App`.

    The route-level `AppContextDep` comes before `DeviceDep`, so a bad app context answers
    403 before any device lookup or cookie rotation, even if `get_link_origin` changes.
    """
    settings: Settings = request.app.state.settings
    email = await EmailVerificationService(
        session, email_hasher=get_email_hasher(request), daily_cap=settings.email_daily_cap
    ).resend(device, body.account_id, link_origin)
    background_tasks.add_task(dispatcher.enqueue, email)
    if device.new_cookie is not None:
        set_device_cookie(response, device.new_cookie, settings)
    response.headers["Cache-Control"] = "no-store"


@router.post(
    "/password/forgot",
    status_code=status.HTTP_202_ACCEPTED,
    response_class=Response,
    dependencies=[rate_limit("forgot")],
    responses={403: _ERR, 422: _ERR, 429: _ERR},
)
async def forgot_password(
    request: Request,
    response: Response,
    session: SessionDep,
    background_tasks: BackgroundTasks,
    _app: AppContextDep,
    body: ForgotPasswordRequest,
    remote_ip: ClientIpDep,
    link_origin: Annotated[str, Depends(get_link_origin)],
    verifier: Annotated[TurnstileVerifier, Depends(get_turnstile_verifier)],
    policy: Annotated[PasswordPolicy, Depends(get_password_policy)],
    hasher: Annotated[PasswordHasher, Depends(get_password_hasher)],
    dispatcher: EmailDispatcherDep,
) -> None:
    """Public (R3 class 3: per-IP limit + Turnstile) + required app context (owner 2026-11-13):
    `X-MM-App` of an active app and an `Origin` registered for it (403 otherwise).

    Always 202 for any email (no enumeration); per-email limits are silent. The reset link
    uses the validated `Origin`; the email is queued after the commit.
    """
    settings: Settings = request.app.state.settings
    email = await PasswordResetService(
        session,
        email_hasher=get_email_hasher(request),
        daily_cap=settings.email_daily_cap,
        verifier=verifier,
        policy=policy,
        hasher=hasher,
    ).forgot(body, remote_ip, link_origin)
    if email is not None:
        background_tasks.add_task(dispatcher.enqueue, email)
    response.headers["Cache-Control"] = "no-store"


@router.post(
    "/password/reset",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    dependencies=[rate_limit("reset")],
    responses={422: _ERR, 429: _ERR},
)
async def reset_password(
    request: Request,
    response: Response,
    session: SessionDep,
    body: ResetPasswordRequest,
    verifier: Annotated[TurnstileVerifier, Depends(get_turnstile_verifier)],
    policy: Annotated[PasswordPolicy, Depends(get_password_policy)],
    hasher: Annotated[PasswordHasher, Depends(get_password_hasher)],
) -> None:
    """Public (R3 class 3, per-IP limit): the one-time token is the credential. Ends every
    account session of the user; sets no cookie and signs nobody in."""
    settings: Settings = request.app.state.settings
    await PasswordResetService(
        session,
        email_hasher=get_email_hasher(request),
        daily_cap=settings.email_daily_cap,
        verifier=verifier,
        policy=policy,
        hasher=hasher,
    ).reset(body.token, body.new_password.get_secret_value())
    response.headers["Cache-Control"] = "no-store"
