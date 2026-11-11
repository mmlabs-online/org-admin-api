from fastapi import APIRouter

from app.modules.auth.router import router as auth_router
from app.modules.organizations.router import router as organizations_router

api_router = APIRouter()
api_router.include_router(organizations_router)
api_router.include_router(auth_router)
