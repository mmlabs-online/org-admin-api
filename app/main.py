import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.cors import ADMIN_POLICY, DynamicCORSMiddleware
from app.core.errors import register_error_handlers
from app.core.jwt_keys import build_key_set
from app.core.rate_limit import InMemoryRateLimiter
from app.core.request_id import RequestIdMiddleware
from app.db.session import SessionLocal
from app.modules.admin_auth.route_classes import BearerRouteMatcher
from app.modules.audit.router import router as audit_router
from app.modules.auth.passwords import PasswordHasher
from app.modules.auth.throttle import EmailHasher
from app.modules.auth.turnstile import CloudflareTurnstileVerifier
from app.modules.email.dispatcher import EmailDispatcher
from app.modules.email.sender import GmailSmtpSender, build_email_sender
from app.modules.health.router import router as health_router
from app.modules.rbac.assignments_router import router as assignments_router
from app.modules.rbac.models import ADMIN_APP_KEY
from app.modules.rbac.origin_registry import AppOriginRegistry
from app.modules.rbac.router import router as rbac_router
from app.modules.tokens.jwks_router import router as jwks_router
from app.modules.users.router import router as users_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    # Read at shutdown (tests may swap it): drains queued mail for up to 5 s (USR-04d).
    dispatcher: EmailDispatcher = app.state.email_dispatcher
    await dispatcher.aclose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    docs = settings.docs_enabled  # off in production unless API_DOCS_ENABLED=true (ADM-002b)
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs" if docs else None,
        openapi_url="/openapi.json" if docs else None,
        redoc_url=None,
        lifespan=_lifespan,
    )
    # Credentialed CORS for `/api/v1/auth/*` from active apps' origins (ADR-0006), and
    # non-credentialed CORS for the Bearer admin routes from `apps[admin].origins` only
    # (ADR-0011). The two scopes are disjoint (no Bearer route under `/api/v1/auth/`).
    origin_registry = AppOriginRegistry(session_factory=SessionLocal, app_env=settings.app_env)
    app.state.origin_registry = origin_registry
    app.add_middleware(
        DynamicCORSMiddleware,
        registry=origin_registry,
        path_prefix=f"{settings.api_prefix}/auth/",
    )
    app.add_middleware(
        DynamicCORSMiddleware,
        registry=origin_registry.for_app(ADMIN_APP_KEY),
        policy=ADMIN_POLICY,
        matcher=BearerRouteMatcher(app),
    )
    # Added last = outermost: every response, CORS preflights included, carries the id.
    app.add_middleware(RequestIdMiddleware)
    app.state.settings = settings
    app.state.rate_limiter = InMemoryRateLimiter()
    app.state.turnstile_verifier = CloudflareTurnstileVerifier(settings)
    app.state.password_hasher = PasswordHasher(settings)
    # Settings require the key in staging/production; local/test fall back to a random one.
    hmac_key = settings.identity_hmac_key
    app.state.email_hasher = EmailHasher(
        hmac_key.get_secret_value().encode() if hmac_key else secrets.token_bytes(32)
    )
    # Sends after the response, in-process (ADR-0007); SMTP keys are required in production.
    email_sender = build_email_sender(settings)
    app.state.email_dispatcher = EmailDispatcher(email_sender)
    logger.info(
        "email_sender=%s", "gmail" if isinstance(email_sender, GmailSmtpSender) else "disabled"
    )
    # Settings require the key in staging/production; local/test fall back to an ephemeral one.
    signing_key = settings.jwt_signing_key
    key_set = build_key_set(
        signing_key.get_secret_value() if signing_key else None, settings.jwt_public_keys_extra
    )
    app.state.key_set = key_set
    if signing_key is None:
        logger.warning("jwt_signing_key_ephemeral kid=%s", key_set.signing_kid)
    else:
        logger.info(
            "jwt_keys_loaded signing_kid=%s extra_kids=%s",
            key_set.signing_kid,
            list(key_set.extra_kids),
        )
    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(jwks_router)
    app.include_router(api_router, prefix=settings.api_prefix)
    app.include_router(users_router, prefix=settings.api_prefix)
    app.include_router(rbac_router, prefix=settings.api_prefix)
    app.include_router(assignments_router, prefix=settings.api_prefix)
    app.include_router(audit_router, prefix=settings.api_prefix)
    return app


app = create_app()
