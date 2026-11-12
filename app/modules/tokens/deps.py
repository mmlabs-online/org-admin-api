from typing import Annotated

from fastapi import Depends, Request

from app.core.jwt_keys import KeySet


def get_key_set(request: Request) -> KeySet:
    key_set: KeySet = request.app.state.key_set
    return key_set


KeySetDep = Annotated[KeySet, Depends(get_key_set)]
