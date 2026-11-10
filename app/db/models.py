"""Import every module's models here so Alembic sees the full metadata."""

from app.db.base import Base
from app.modules.organizations.models import Organization

__all__ = ["Base", "Organization"]
