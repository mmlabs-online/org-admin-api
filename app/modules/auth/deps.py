from typing import Annotated

from fastapi import Depends, Request

from app.core.client_ip import client_ip
from app.core.config import Settings
from app.modules.auth.passwords import PasswordHasher, PasswordPolicy
from app.modules.auth.turnstile import TurnstileVerifier


def get_password_hasher(request: Request) -> PasswordHasher:
    hasher: PasswordHasher = request.app.state.password_hasher
    return hasher


def get_password_policy(request: Request) -> PasswordPolicy:
    settings: Settings = request.app.state.settings
    return PasswordPolicy(min_length=settings.password_min_length)


def get_turnstile_verifier(request: Request) -> TurnstileVerifier:
    verifier: TurnstileVerifier = request.app.state.turnstile_verifier
    return verifier


def get_client_ip(request: Request) -> str:
    settings: Settings = request.app.state.settings
    return client_ip(request, settings.trusted_proxy_cidrs)


ClientIpDep = Annotated[str, Depends(get_client_ip)]
