import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, delete, func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.models import App
from app.modules.releases.models import AppRelease, ReleaseStatus
from app.modules.releases.schemas import ReleaseSort


@dataclass(frozen=True)
class ListFilter:
    app_key: str | None = None
    environment: str | None = None
    status: str | None = None
    created_from: datetime | None = None  # inclusive
    created_to: datetime | None = None  # exclusive


@dataclass(frozen=True)
class ReleaseView:
    """Plain values for the router. `images` and `changes` are empty in lists."""

    id: uuid.UUID
    app_key: str
    environment: str
    version: str
    status: str
    git_sha: str | None
    deployment_pr: str | None
    has_migrations: bool | None
    deploy_run_url: str | None
    created_via: str
    changes_count: int
    created_at: datetime
    updated_at: datetime
    deployed_at: datetime | None
    images: list[dict[str, Any]] = field(default_factory=list)
    changes: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class DeletedRelease:
    app_id: uuid.UUID
    environment: str
    version: str
    status: str
    changes_count: int


def _columns() -> list[Any]:
    """Everything but the bodies (`images`, `changes`), which lists never select."""
    return [
        AppRelease.id,
        App.key,
        AppRelease.environment,
        AppRelease.version,
        AppRelease.status,
        AppRelease.git_sha,
        AppRelease.deployment_pr,
        AppRelease.has_migrations,
        AppRelease.deploy_run_url,
        AppRelease.created_via,
        func.jsonb_array_length(AppRelease.changes).label("changes_count"),
        AppRelease.created_at,
        AppRelease.updated_at,
        AppRelease.deployed_at,
    ]


def _where(f: ListFilter) -> list[ColumnElement[bool]]:
    conds: list[ColumnElement[bool]] = []
    if f.app_key is not None:
        conds.append(App.key == f.app_key)
    if f.environment is not None:
        conds.append(AppRelease.environment == f.environment)
    if f.status is not None:
        conds.append(AppRelease.status == f.status)
    if f.created_from is not None:
        conds.append(AppRelease.created_at >= f.created_from)
    if f.created_to is not None:
        conds.append(AppRelease.created_at < f.created_to)
    return conds


def _view(row: Any) -> ReleaseView:
    """`row` has the 14 summary columns, optionally followed by `images` and `changes`."""
    return ReleaseView(*row)


class AppReleaseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, release: AppRelease) -> AppRelease:
        self.session.add(release)
        await self.session.flush()
        await self.session.refresh(release, ["created_at", "updated_at"])
        return release

    async def insert_if_absent(
        self,
        *,
        app_id: uuid.UUID,
        environment: str,
        version: str,
        created_via: str,
        **fields: Any,
    ) -> AppRelease | None:
        """`INSERT ... ON CONFLICT DO NOTHING` on the natural key: None when it already exists.

        A concurrent first write waits for the other transaction and then gets None, never an
        error. No `DO UPDATE`: what to do with an existing record depends on its status.
        """
        return await self.session.scalar(
            pg_insert(AppRelease)
            .values(
                app_id=app_id,
                environment=environment,
                version=version,
                created_via=created_via,
                status=ReleaseStatus.PENDING.value,
                **fields,
            )
            .on_conflict_do_nothing(constraint="uq_app_releases_app_id_environment_version")
            .returning(AppRelease)
            .execution_options(populate_existing=True)
        )

    async def get_for_update(
        self, app_id: uuid.UUID, environment: str, version: str
    ) -> AppRelease | None:
        return await self.session.scalar(
            select(AppRelease)
            .where(
                AppRelease.app_id == app_id,
                AppRelease.environment == environment,
                AppRelease.version == version,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def page(
        self, f: ListFilter, sort: ReleaseSort, *, limit: int, offset: int
    ) -> tuple[list[ReleaseView], int]:
        where = _where(f)
        order: list[Any]
        if sort is ReleaseSort.DEPLOYED_AT:
            order = [
                AppRelease.deployed_at.desc().nulls_last(),
                AppRelease.created_at.desc(),
                AppRelease.id.desc(),
            ]
        else:
            order = [AppRelease.created_at.desc(), AppRelease.id.desc()]
        rows = await self.session.execute(
            select(*_columns())
            .join(App, App.id == AppRelease.app_id)
            .where(*where)
            .order_by(*order)
            .limit(limit)
            .offset(offset)
        )
        total = await self.session.scalar(
            select(func.count())
            .select_from(AppRelease)
            .join(App, App.id == AppRelease.app_id)
            .where(*where)
        )
        return [_view(r) for r in rows.all()], int(total or 0)

    async def get_view(self, release_id: uuid.UUID) -> ReleaseView | None:
        row = (
            await self.session.execute(
                select(*_columns(), AppRelease.images, AppRelease.changes)
                .join(App, App.id == AppRelease.app_id)
                .where(AppRelease.id == release_id)
            )
        ).first()
        return None if row is None else _view(row)

    async def live_page(
        self,
        app_key: str | None,
        environment: str | None,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[ReleaseView], int]:
        """Per (app, environment): the deployed record with the latest `deployed_at`."""
        conds: list[ColumnElement[bool]] = [AppRelease.status == ReleaseStatus.DEPLOYED.value]
        if app_key is not None:
            conds.append(App.key == app_key)
        if environment is not None:
            conds.append(AppRelease.environment == environment)
        latest = (
            select(AppRelease.id)
            .join(App, App.id == AppRelease.app_id)
            .where(*conds)
            .ext(distinct_on(AppRelease.app_id, AppRelease.environment))
            .order_by(
                AppRelease.app_id,
                AppRelease.environment,
                AppRelease.deployed_at.desc(),
                AppRelease.id.desc(),
            )
            .subquery()
        )
        rows = await self.session.execute(
            select(*_columns())
            .join(App, App.id == AppRelease.app_id)
            .where(AppRelease.id.in_(select(latest.c.id)))
            .order_by(App.key, AppRelease.environment)
            .limit(limit)
            .offset(offset)
        )
        total = await self.session.scalar(select(func.count()).select_from(latest))
        return [_view(r) for r in rows.all()], int(total or 0)

    async def delete_returning(self, release_id: uuid.UUID) -> DeletedRelease | None:
        row = (
            await self.session.execute(
                delete(AppRelease)
                .where(AppRelease.id == release_id)
                .returning(
                    AppRelease.app_id,
                    AppRelease.environment,
                    AppRelease.version,
                    AppRelease.status,
                    func.jsonb_array_length(AppRelease.changes),
                )
                .execution_options(synchronize_session=False)
            )
        ).first()
        return None if row is None else DeletedRelease(*row)

    async def app_key(self, app_id: uuid.UUID) -> str:
        key = await self.session.scalar(select(App.key).where(App.id == app_id))
        assert key is not None  # noqa: S101 (FK RESTRICT)
        return key
