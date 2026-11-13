from typing import Annotated

from fastapi import BackgroundTasks, Depends, Header, Request

from app.api.deps import SessionDep
from app.core.client_ip import client_ip
from app.core.config import Settings
from app.modules.auth.cookies import clear_device_cookie_header, read_device_cookies
from app.modules.auth.hooks import SignupHook, VerificationEmailHook
from app.modules.auth.passwords import PasswordHasher, PasswordPolicy
from app.modules.auth.service import AppContextService
from app.modules.auth.throttle import EmailHasher
from app.modules.auth.turnstile import TurnstileVerifier
from app.modules.email.dispatcher import EmailDispatcher
from app.modules.email.sender import OutgoingEmail
from app.modules.rbac.models import App
from app.modules.rbac.origins import normalize_origin
from app.modules.sessions.errors import SessionInvalidError
from app.modules.sessions.service import DeviceSessionService, ResolvedDevice


def get_password_hasher(request: Request) -> PasswordHasher:
    hasher: PasswordHasher = request.app.state.password_hasher
    return hasher


def get_password_policy(request: Request) -> PasswordPolicy:
    settings: Settings = request.app.state.settings
    return PasswordPolicy(min_length=settings.password_min_length)


def get_turnstile_verifier(request: Request) -> TurnstileVerifier:
    verifier: TurnstileVerifier = request.app.state.turnstile_verifier
    return verifier


def get_email_hasher(request: Request) -> EmailHasher:
    hasher: EmailHasher = request.app.state.email_hasher
    return hasher


def get_client_ip(request: Request) -> str:
    settings: Settings = request.app.state.settings
    return client_ip(request, settings.trusted_proxy_cidrs)


ClientIpDep = Annotated[str, Depends(get_client_ip)]


def get_device_sessions(request: Request, session: SessionDep) -> DeviceSessionService:
    settings: Settings = request.app.state.settings
    return DeviceSessionService(
        session,
        idle_days=settings.device_session_idle_days,
        max_days=settings.device_session_max_days,
    )


DeviceSessionsDep = Annotated[DeviceSessionService, Depends(get_device_sessions)]


async def get_app_context(
    request: Request,
    session: SessionDep,
    x_mm_app: Annotated[str | None, Header(alias="X-MM-App")] = None,
    origin: Annotated[str | None, Header()] = None,
) -> App:
    """`X-MM-App` of an active app and an `Origin` registered for it (D12), else 403."""
    settings: Settings = request.app.state.settings
    return await AppContextService(session, app_env=settings.app_env).check(x_mm_app, origin)


AppContextDep = Annotated[App, Depends(get_app_context)]


def get_email_dispatcher(request: Request) -> EmailDispatcher:
    dispatcher: EmailDispatcher = request.app.state.email_dispatcher
    return dispatcher


EmailDispatcherDep = Annotated[EmailDispatcher, Depends(get_email_dispatcher)]


def get_link_origin(_app: AppContextDep, origin: Annotated[str, Header()]) -> str:
    """`AppContextDep` already proved `Origin` is registered for the `X-MM-App` app."""
    return normalize_origin(origin)


def get_signup_hook(
    request: Request,
    background_tasks: BackgroundTasks,
    dispatcher: EmailDispatcherDep,
    link_origin: Annotated[str, Depends(get_link_origin)],
) -> SignupHook:
    """Background tasks run only after a successful response, i.e. after the commit."""
    settings: Settings = request.app.state.settings

    def schedule(email: OutgoingEmail) -> None:
        background_tasks.add_task(dispatcher.enqueue, email)

    return VerificationEmailHook(
        link_origin=link_origin,
        email_hasher=get_email_hasher(request),
        daily_cap=settings.email_daily_cap,
        schedule=schedule,
    )


async def get_device(request: Request, devices: DeviceSessionsDep) -> ResolvedDevice:
    """Resolve `__Host-mm_device` (may rotate); a 401 clears the cookie when one was sent."""
    cookies = read_device_cookies(request)
    try:
        return await devices.resolve(cookies)
    except SessionInvalidError as exc:
        if cookies:
            settings: Settings = request.app.state.settings
            exc.headers = {"Set-Cookie": clear_device_cookie_header(settings)}
        raise


DeviceDep = Annotated[ResolvedDevice, Depends(get_device)]
