import json
from urllib.parse import parse_qs

import httpx

from app.core.config import Settings
from app.modules.auth.turnstile import SITEVERIFY_URL, CloudflareTurnstileVerifier


def _verifier(
    handler: httpx.MockTransport | None = None, **overrides: object
) -> CloudflareTurnstileVerifier:
    settings = Settings(**{"turnstile_secret_key": "real-secret", **overrides})  # type: ignore[arg-type]
    return CloudflareTurnstileVerifier(settings, transport=handler)


def _json(body: object, status_code: int = 200) -> httpx.MockTransport:
    return httpx.MockTransport(lambda _: httpx.Response(status_code, json=body))


async def test_success() -> None:
    assert await _verifier(_json({"success": True, "hostname": "a.io"})).verify("t", "1.2.3.4")


async def test_failure_response() -> None:
    body = {"success": False, "error-codes": ["invalid-input-response"]}
    assert not await _verifier(_json(body)).verify("t", "1.2.3.4")


async def test_hostname_not_allowed() -> None:
    verifier = _verifier(
        _json({"success": True, "hostname": "evil.io"}), turnstile_allowed_hostnames=["a.io"]
    )
    assert not await verifier.verify("t", "1.2.3.4")
    verifier = _verifier(
        _json({"success": True, "hostname": "a.io"}), turnstile_allowed_hostnames=["a.io"]
    )
    assert await verifier.verify("t", "1.2.3.4")


async def test_network_error_fails_closed() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout", request=request)

    assert not await _verifier(httpx.MockTransport(boom)).verify("t", "1.2.3.4")


async def test_5xx_fails_closed() -> None:
    assert not await _verifier(_json({"success": True}, status_code=502)).verify("t", "1.2.3.4")
    non_json = httpx.MockTransport(lambda _: httpx.Response(200, text="<html>"))
    assert not await _verifier(non_json).verify("t", "1.2.3.4")


async def test_missing_secret_fails_closed_without_network_call() -> None:
    calls: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"success": True})

    verifier = CloudflareTurnstileVerifier(Settings(), transport=httpx.MockTransport(record))
    assert not await verifier.verify("t", "1.2.3.4")
    assert calls == []


async def test_cloudflare_test_secret_rejected_in_production() -> None:
    calls: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"success": True})

    test_secret = "1x0000000000000000000000000000000AA"
    prod = _verifier(
        httpx.MockTransport(record),
        turnstile_secret_key=test_secret,
        app_env="production",
        trusted_proxy_cidrs=["172.18.0.0/16"],
        argon2_memory_kib=19456,
        argon2_time_cost=2,
    )
    assert not await prod.verify("t", "1.2.3.4")
    assert calls == []
    local = _verifier(httpx.MockTransport(record), turnstile_secret_key=test_secret)
    assert await local.verify("t", "1.2.3.4")


async def test_sends_secret_response_remoteip() -> None:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=json.dumps({"success": True}))

    assert await _verifier(httpx.MockTransport(record)).verify("tok", "9.9.9.9")
    (request,) = seen
    assert str(request.url) == SITEVERIFY_URL
    assert request.method == "POST"
    form = parse_qs(request.content.decode())
    assert form == {"secret": ["real-secret"], "response": ["tok"], "remoteip": ["9.9.9.9"]}
