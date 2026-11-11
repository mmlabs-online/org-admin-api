from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.rate_limit import InMemoryRateLimiter
from app.modules.auth.passwords import PasswordHasher
from app.modules.auth.turnstile import CloudflareTurnstileVerifier
from app.modules.health.router import router as health_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs" if settings.app_env != "production" else None,
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
    return app


app = create_app()
