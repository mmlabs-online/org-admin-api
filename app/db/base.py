import uuid
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, InstrumentedAttribute, Mapped, mapped_column

# Deterministic constraint names so Alembic autogenerate produces stable migrations.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def bumped_updated_at(column: InstrumentedAttribute[datetime]) -> ColumnElement[datetime]:
    """A strictly increasing `updated_at` for a conditional write (ADM-024).

    `now()` is constant inside a transaction and could repeat the old value (an ABA hazard for
    the ETag), so take the later of the wall clock and the old value plus one microsecond.
    """
    return func.greatest(func.clock_timestamp(), column + timedelta(microseconds=1))


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPkMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
