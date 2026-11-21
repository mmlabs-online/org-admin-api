"""Usage metrics (ADM-039, ADR-0024) and per-user limit overrides (ADM-040).

CHECK texts must match the migration exactly: Alembic does not compare CHECKs.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPkMixin

METRIC_KEY_RE = r"^[a-z][a-z0-9-]{1,47}$"
UNIT_RE = r"^[a-z][a-z0-9-]{0,31}$"
MAX_LIMIT = 2**53 - 1  # JS-safe integer


class UsagePeriod(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class UsageMetricStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class UsageMetric(UUIDPkMixin, TimestampMixin, Base):
    """`key` and `unit` are immutable; a metric is archived, never deleted."""

    __tablename__ = "usage_metrics"
    __table_args__ = (
        UniqueConstraint("app_id", "key", name="uq_usage_metrics_app_id_key"),
        UniqueConstraint("id", "app_id", name="uq_usage_metrics_id_app_id"),
        CheckConstraint(f"key ~ '{METRIC_KEY_RE}'", name="key_format"),
        CheckConstraint("char_length(display_name) BETWEEN 1 AND 100", name="display_name_length"),
        CheckConstraint(f"unit ~ '{UNIT_RE}'", name="unit_format"),
        CheckConstraint(f"default_limit BETWEEN 0 AND {MAX_LIMIT}", name="default_limit_range"),
        CheckConstraint("period IN ('day', 'week', 'month')", name="period_valid"),
        CheckConstraint("status IN ('active', 'archived')", name="status_valid"),
    )

    app_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("apps.id", ondelete="RESTRICT", name="fk_usage_metrics_app_id_apps"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(String(48), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    default_limit: Mapped[int] = mapped_column(BigInteger, nullable=False)
    period: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'week'"), default=UsagePeriod.WEEK.value
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'active'"),
        default=UsageMetricStatus.ACTIVE.value,
    )

    def __repr__(self) -> str:
        return f"<UsageMetric id={self.id} key={self.key}>"


class UsageLimitOverride(UUIDPkMixin, TimestampMixin, Base):
    """One row per (user, metric): replaces the metric default (OQ62); history is in the audit log.

    `created_by` is the first setter and is not changed on replace. No reason column: the reason is
    stored in the audit entry only (ADR-0024 point 5).
    """

    __tablename__ = "usage_limit_overrides"
    __table_args__ = (
        UniqueConstraint("user_id", "metric_id", name="uq_usage_limit_overrides_user_id_metric_id"),
        CheckConstraint(f"limit_value BETWEEN 0 AND {MAX_LIMIT}", name="limit_value_range"),
        Index("ix_usage_limit_overrides_metric_id", "metric_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE", name="fk_usage_limit_overrides_user_id_users"),
        nullable=False,
    )
    metric_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "usage_metrics.id",
            ondelete="RESTRICT",
            name="fk_usage_limit_overrides_metric_id_usage_metrics",
        ),
        nullable=False,
    )
    limit_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "users.id", ondelete="SET NULL", name="fk_usage_limit_overrides_created_by_users"
        ),
        nullable=True,
    )

    def __repr__(self) -> str:
        return f"<UsageLimitOverride id={self.id} user={self.user_id} metric={self.metric_id}>"
