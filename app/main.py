from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.rate_limit import InMemoryRateLimiter
from app.modules.auth.passwords import PasswordHasher
from app.modules.auth.turnstile import CloudflareTurnstileVerifier
from app.modules.health.router import router as health_router
from app.modules.rbac.router import router as rbac_router
from app.modules.users.router import router as users_router


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
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.state.settings = settings
    app.state.rate_limiter = InMemoryRateLimiter()
    app.state.turnstile_verifier = CloudflareTurnstileVerifier(settings)
    app.state.password_hasher = PasswordHasher(settings)
    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(api_router, prefix=settings.api_prefix)
    if settings.admin_users_api_enabled:  # unauthenticated until ADM-002 (see Settings)
        app.include_router(users_router, prefix=settings.api_prefix)
    if settings.admin_rbac_api_enabled:  # unauthenticated until ADM-002 (see Settings)
        app.include_router(rbac_router, prefix=settings.api_prefix)
    return app


app = create_app()
