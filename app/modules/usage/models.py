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
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPkMixin

METRIC_KEY_RE = r"^[a-z][a-z0-9-]{1,47}$"
UNIT_RE = r"^[a-z][a-z0-9-]{0,31}$"
MAX_LIMIT = 2**53 - 1  # JS-safe integer
IDEMPOTENCY_KEY_RE = r"^[!-~]{1,128}$"  # printable ASCII without space (ADM-042)


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


class UsageEventState(StrEnum):
    CONSUMED = "consumed"
    SETTLED = "settled"
    REFUNDED = "refunded"


class UsageCounter(UUIDPkMixin, TimestampMixin, Base):
    """Per-period total per (user, metric); kept indefinitely (OQ69). The period kind is part of
    the key, so a metric whose period changes starts a new bucket (ADR-0024 §4)."""

    __tablename__ = "usage_counters"
    __table_args__ = (
        ForeignKeyConstraint(
            ["metric_id", "app_id"],
            ["usage_metrics.id", "usage_metrics.app_id"],
            ondelete="RESTRICT",
            name="fk_usage_counters_metric_id_app_id_usage_metrics",
        ),
        UniqueConstraint(
            "user_id",
            "metric_id",
            "period",
            "period_start",
            name="uq_usage_counters_user_id_metric_id_period_period_start",
        ),
        CheckConstraint("period IN ('day', 'week', 'month')", name="period_valid"),
        CheckConstraint(f"used BETWEEN 0 AND {MAX_LIMIT}", name="used_range"),
    )

    app_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE", name="fk_usage_counters_user_id_users"),
        nullable=False,
    )
    metric_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    period: Mapped[str] = mapped_column(String(10), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))


class UsageEvent(UUIDPkMixin, Base):
    """Ledger row and idempotency record of one consume (ADR-0022 §5). Purged after 90 days
    (ADM-045); refused consumes are never stored."""

    __tablename__ = "usage_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["metric_id", "app_id"],
            ["usage_metrics.id", "usage_metrics.app_id"],
            ondelete="RESTRICT",
            name="fk_usage_events_metric_id_app_id_usage_metrics",
        ),
        UniqueConstraint(
            "app_id", "idempotency_key", name="uq_usage_events_app_id_idempotency_key"
        ),
        CheckConstraint(f"idempotency_key ~ '{IDEMPOTENCY_KEY_RE}'", name="idempotency_key_format"),
        CheckConstraint("period IN ('day', 'week', 'month')", name="period_valid"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "settled_amount IS NULL OR settled_amount > 0", name="settled_amount_positive"
        ),
        CheckConstraint("state IN ('consumed', 'settled', 'refunded')", name="state_valid"),
        CheckConstraint(
            "(state = 'consumed' AND settled_at IS NULL AND settled_amount IS NULL "
            "AND refunded_at IS NULL) "
            "OR (state = 'settled' AND settled_at IS NOT NULL AND settled_amount IS NOT NULL "
            "AND refunded_at IS NULL) "
            "OR (state = 'refunded' AND refunded_at IS NOT NULL "
            "AND (settled_at IS NULL) = (settled_amount IS NULL))",
            name="state_consistent",
        ),
        Index("ix_usage_events_created_at", "created_at"),
        Index("ix_usage_events_user_id_metric_id_created_at", "user_id", "metric_id", "created_at"),
    )

    app_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE", name="fk_usage_events_user_id_users"),
        nullable=False,
    )
    metric_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    period: Mapped[str] = mapped_column(String(10), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    settled_amount: Mapped[int | None] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        server_default=text("'consumed'"),
        default=UsageEventState.CONSUMED.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
