import uuid
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.modules.rbac.models import ADMIN_APP_KEY, App, AppStatus, JoinPolicy, Role
from app.modules.rbac.repository import RbacRepository
from app.modules.rbac.schemas import AppCreate, AppUpdate


@dataclass(frozen=True)
class DefaultRoleResult:
    role_key: str
    created: bool
    added: list[str]
    removed: list[str]


class RoleCatalogService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = RbacRepository(session)

    async def build_default_role(
        self, app_key: str, actor: uuid.UUID | None = None
    ) -> DefaultRoleResult:
        """Create or update `<app>:member` = every permission of the app with `is_admin = false`.

        Runs only when called (seed, app creation, USR-05 manifest), never automatically on
        permission changes: that would silently widen or undo manual tightening (D9: role
        content is data). `<app>:*` is never included (the CHECK forces it to `is_admin`). Sets
        `apps.default_role_id` only when it is NULL. Idempotent. Refuses the `admin` app (409).
        """
        app = await self.repo.get_app_by_key(app_key)
        if app is None:
            raise NotFoundError("App not found")
        return await self._build_for_app(app)

    async def _build_for_app(self, app: App) -> DefaultRoleResult:
        # R4: audit entry once ADM-004 lands.
        if app.key == ADMIN_APP_KEY:
            raise ConflictError("The admin app has no member role")

        key = f"{app.key}:member"
        role = await self.repo.get_role_by_key(app.id, key)
        created = role is None
        if role is None:
            try:
                role = await self.repo.add_role(
                    Role(app_id=app.id, key=key, name="Member", is_system=True)
                )
            except IntegrityError as exc:
                raise ConflictError("Member role was created concurrently") from exc

        target = {p.id: p.key for p in await self.repo.list_permissions(app.id, is_admin=False)}
        current = await self.repo.role_permission_keys(role.id)
        to_add = target.keys() - current.keys()
        to_remove = current.keys() - target.keys()
        await self.repo.add_role_permissions(role.id, app.id, to_add)
        await self.repo.remove_role_permissions(role.id, to_remove)

        await self.repo.set_default_role_if_null(app.id, role.id)
        await self.repo.session.refresh(app)

        return DefaultRoleResult(
            role_key=key,
            created=created,
            added=sorted(target[p] for p in to_add),
            removed=sorted(current[p] for p in to_remove),
        )


class AppService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = RbacRepository(session)
        self.catalog = RoleCatalogService(session)

    async def list(self, *, limit: int, offset: int) -> tuple[list[App], int]:
        return await self.repo.list_apps(limit=limit, offset=offset)

    async def get(self, key: str) -> App:
        app = await self.repo.get_app_by_key(key)
        if app is None:
            raise NotFoundError("App not found")
        return app

    async def create(self, data: AppCreate, actor: uuid.UUID | None = None) -> App:
        # R4: audit entry in the same transaction once ADM-004 lands.
        if await self.repo.get_app_by_key(data.key) is not None:
            raise ConflictError("App key is already taken")
        try:
            app = await self.repo.add_app(
                App(
                    key=data.key,
                    name=data.name,
                    join_policy=data.join_policy.value,
                    org_mode=data.org_mode.value,
                    origins=data.origins,
                )
            )
        except IntegrityError as exc:  # concurrent insert with the same key
            raise ConflictError("App key is already taken") from exc
        # Same transaction: a failure here rolls the app back with the request.
        await self.catalog._build_for_app(app)
        return app

    async def update(self, key: str, data: AppUpdate, actor: uuid.UUID | None = None) -> App:
        app = await self.get(key)
        fields = data.model_fields_set

        if app.key == ADMIN_APP_KEY and (
            ("join_policy" in fields and data.join_policy != JoinPolicy.CLOSED)
            or ("default_role_id" in fields and data.default_role_id is not None)
            or ("status" in fields and data.status != AppStatus.ACTIVE)
        ):
            raise ConflictError("The admin app must stay active, closed and without a default role")

        if "default_role_id" in fields and data.default_role_id is not None:
            if await self.repo.get_role(app.id, data.default_role_id) is None:
                raise UnprocessableError(
                    "Unknown role for this app",
                    details=[{"field": "default_role_id", "reason": "unknown_role"}],
                )
            if await self.repo.role_has_admin_permission(data.default_role_id):
                raise UnprocessableError(
                    "A default role must not hold admin permissions",
                    details=[{"field": "default_role_id", "reason": "role_has_admin_permissions"}],
                )

        changed = False
        # ADM-002 guard: changing admin origins requires admin:* (applied with the fields below).
        for field in fields:
            value = getattr(data, field)
            if isinstance(value, StrEnum):
                value = value.value
            if getattr(app, field) != value:
                setattr(app, field, value)
                changed = True
        if not changed:
            return app  # no write: updated_at stays unchanged

        # R4: audit entry in the same transaction once ADM-004 lands.
        try:
            await self.repo.save()
        except IntegrityError as exc:  # e.g. the role was deleted concurrently
            raise ConflictError("App was changed concurrently") from exc
        await self.repo.session.refresh(app)
        return app

    async def delete(self, key: str, actor: uuid.UUID | None = None) -> None:
        app = await self.get(key)
        if app.key == ADMIN_APP_KEY:
            raise ConflictError("The admin app cannot be deleted")
        if await self.repo.app_has_assignments(app.id):
            raise ConflictError("App has role assignments; disable it instead")
        # R4: audit entry in the same transaction once ADM-004 lands.
        try:
            await self.repo.delete_app(app)
        except IntegrityError as exc:  # an assignment was added concurrently
            raise ConflictError("App has role assignments; disable it instead") from exc
