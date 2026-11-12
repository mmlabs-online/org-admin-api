import base64
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.jwt_keys import build_key_set, thumbprint
from tests.auth_factories import ORIGIN, make_login_app
from tests.conftest import TEST_JWT_SIGNING_KEY, MakeClient

JWKS = "/.well-known/jwks.json"


async def test_jwks_public_keys_only(client: AsyncClient) -> None:
    resp = await client.get(JWKS)
    assert resp.status_code == 200
    keys = resp.json()["keys"]
    assert len(keys) == 1
    (key,) = keys
    assert set(key) == {"kty", "crv", "x", "y", "kid", "alg", "use"}
    assert "d" not in key
    assert (key["kty"], key["crv"], key["alg"], key["use"]) == ("EC", "P-256", "ES256", "sig")
    assert key["kid"] == build_key_set(TEST_JWT_SIGNING_KEY, []).signing_kid


async def test_jwks_headers(client: AsyncClient) -> None:
    resp = await client.get(JWKS)
    assert resp.headers["content-type"] == "application/jwk-set+json"
    assert resp.headers["cache-control"] == "public, max-age=300"
    etag = resp.headers["etag"]
    assert etag.startswith('"') and etag.endswith('"')  # strong
    assert resp.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in resp.headers


async def test_jwks_304_on_etag(client: AsyncClient) -> None:
    etag = (await client.get(JWKS)).headers["etag"]
    for value in (etag, f"W/{etag}", f'"other", {etag}'):
        resp = await client.get(JWKS, headers={"If-None-Match": value})
        assert resp.status_code == 304
        assert resp.content == b""
        assert resp.headers["etag"] == etag
        assert resp.headers["cache-control"] == "public, max-age=300"
    miss = await client.get(JWKS, headers={"If-None-Match": '"other"'})
    assert miss.status_code == 200


async def test_jwks_lists_extra_keys(make_client: MakeClient) -> None:
    extra = ec.generate_private_key(ec.SECP256R1())
    pem = extra.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    async with make_client(jwt_public_keys_extra=[base64.b64encode(pem).decode()]) as client:
        keys = (await client.get(JWKS)).json()["keys"]
    assert [k["kid"] for k in keys] == [
        build_key_set(TEST_JWT_SIGNING_KEY, []).signing_kid,
        thumbprint(extra.public_key()),
    ]
    assert all("d" not in k for k in keys)


async def test_jwks_wildcard_without_credentials_for_registered_origin(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await make_login_app(db_session)
    resp = await client.get(JWKS, headers={"Origin": ORIGIN})
    assert resp.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in resp.headers
    assert "origin" not in resp.headers.get("vary", "").lower()


async def test_jwks_rate_limited_429(make_client: MakeClient) -> None:
    async with make_client(jwks_rate_limit=1) as client:
        assert (await client.get(JWKS)).status_code == 200
        resp = await client.get(JWKS)
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "rate_limited"


async def test_jwks_body_stable_across_requests(client: AsyncClient) -> None:
    a = await client.get(JWKS)
    b = await client.get(JWKS)
    assert a.content == b.content
    assert a.headers["etag"] == b.headers["etag"]
    assert json.loads(a.content) == a.json()
