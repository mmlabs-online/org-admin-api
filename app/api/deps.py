from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import PageParams, page_params
from app.db.session import get_session

# Function scope: the commit runs before the response (and its `Set-Cookie`) is sent, so a
# failed commit gives a 500 without the cookie (S2, ADR-0004 §4). Declare `get_session` only here.
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
PageDep = Annotated[PageParams, Depends(page_params)]
