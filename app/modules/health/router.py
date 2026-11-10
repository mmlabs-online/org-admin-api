from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import SessionDep
from app.core.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def liveness() -> dict[str, str]:
    return {"status": "ok", "version": get_settings().app_version}


@router.get("/readyz")
async def readiness(session: SessionDep) -> dict[str, str]:
    await session.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}
