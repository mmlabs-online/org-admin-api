from fastapi import APIRouter

from app.modules.auth.router import router as auth_router
from app.modules.organizations.router import router as organizations_router
from app.modules.rbac.catalog_router import router as catalog_router

api_router = APIRouter()
api_router.include_router(organizations_router)
api_router.include_router(auth_router)
# Public (D17), always mounted; the staff RBAC router is gated in `create_app`.
api_router.include_router(catalog_router)
