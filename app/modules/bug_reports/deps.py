from typing import Annotated

from fastapi import Depends

from app.modules.auth.deps import AppContextDep
from app.modules.auth.errors import OriginNotAllowedError
from app.modules.rbac.models import ADMIN_APP_KEY, App


async def get_report_app_context(app: AppContextDep) -> App:
    """The `admin` app cannot be a report target: same 403 as an unregistered app (no oracle)."""
    if app.key == ADMIN_APP_KEY:
        raise OriginNotAllowedError("Origin is not allowed")
    return app


ReportAppContextDep = Annotated[App, Depends(get_report_app_context)]
