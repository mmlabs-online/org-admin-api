"""`GET /.well-known/jwks.json` — R3 class 3 (public), root mount (D13), per-IP limit `jwks`.

Uncredentialed: `Access-Control-Allow-Origin: *`; the credentialed CORS middleware
(`app.core.cors`) only covers `/api/v1/auth/`.
"""

from fastapi import APIRouter, Request, Response

from app.core.etag import if_none_match
from app.core.rate_limit import rate_limit
from app.modules.tokens.deps import KeySetDep
from app.modules.tokens.schemas import JwkSet

router = APIRouter(tags=["identity"])


@router.get(
    "/.well-known/jwks.json",
    response_model=JwkSet,
    dependencies=[rate_limit("jwks")],
    responses={
        200: {"content": {"application/jwk-set+json": {}}},
        304: {"description": "Not modified"},
        429: {"description": "Rate limited"},
    },
    summary="Access-token signing public keys (ADR-0005)",
)
async def get_jwks(request: Request, key_set: KeySetDep) -> Response:
    headers = {
        "ETag": key_set.jwks_etag,
        "Cache-Control": "public, max-age=300",
        "Access-Control-Allow-Origin": "*",
    }
    if if_none_match(request.headers.getlist("if-none-match"), key_set.jwks_etag):
        return Response(status_code=304, headers=headers)
    return Response(
        content=key_set.jwks_body, media_type="application/jwk-set+json", headers=headers
    )
