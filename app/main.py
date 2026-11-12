import logging
import secrets

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import Receive, Scope, Send

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.jwt_keys import build_key_set
from app.core.rate_limit import InMemoryRateLimiter
from app.modules.auth.passwords import PasswordHasher
from app.modules.auth.throttle import EmailHasher
from app.modules.auth.turnstile import CloudflareTurnstileVerifier
from app.modules.health.router import router as health_router
from app.modules.rbac.assignments_router import router as assignments_router
from app.modules.rbac.router import router as rbac_router
from app.modules.tokens.jwks_router import router as jwks_router
from app.modules.users.router import router as users_router

logger = logging.getLogger(__name__)


class _CredentialedCORS(CORSMiddleware):
    """Static CORS until USR-03e; skips `/.well-known/` (public, uncredentialed, ACAO *)."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith("/.well-known/"):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        # Swagger UI is public in every environment, production included (owner, 2026-11-11;
        # temporary). Gated admin routes are not mounted in production, so they are not listed.
        docs_url="/docs",
        redoc_url=None,
    )
    if settings.cors_origins:
        app.add_middleware(
            _CredentialedCORS,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.state.settings = settings
    app.state.rate_limiter = InMemoryRateLimiter()
    app.state.turnstile_verifier = CloudflareTurnstileVerifier(settings)
    app.state.password_hasher = PasswordHasher(settings)
    # Settings require the key in staging/production; local/test fall back to a random one.
    hmac_key = settings.identity_hmac_key
    app.state.email_hasher = EmailHasher(
        hmac_key.get_secret_value().encode() if hmac_key else secrets.token_bytes(32)
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
    if settings.admin_users_api_enabled:  # unauthenticated until ADM-002 (see Settings)
        app.include_router(users_router, prefix=settings.api_prefix)
    if settings.admin_rbac_api_enabled:  # unauthenticated until ADM-002 (see Settings)
        app.include_router(rbac_router, prefix=settings.api_prefix)
        app.include_router(assignments_router, prefix=settings.api_prefix)
    return app


app = create_app()
