"""Append-only audit log (ADM-004, ADR-0009).

No FKs: actor, app and org are snapshots that outlive deletes. No `TimestampMixin`: a row is
never updated. CHECK texts must match the migration exactly: Alembic does not compare CHECKs.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, String, Text, Uuid, func, text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPkMixin
from app.modules.rbac.models import APP_KEY_RE

REQUEST_ID_RE = r"^[A-Za-z0-9._-]{1,64}$"


class AuditLog(UUIDPkMixin, Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint("actor_kind IN ('user', 'system', 'anonymous')", name="actor_kind_valid"),
        CheckConstraint(
            "((actor_kind = 'user') = (actor_user_id IS NOT NULL))"
            " AND (actor_kind <> 'system' OR actor_label IS NOT NULL)",
            name="actor_consistent",
        ),
        CheckConstraint(
            "actor_label IS NULL OR length(actor_label) <= 64", name="actor_label_length"
        ),
        CheckConstraint(
            r"action ~ '^[a-z_]+(\.[a-z_]+)+$' AND length(action) <= 64", name="action_format"
        ),
        CheckConstraint("(target_type IS NULL) = (target_id IS NULL)", name="target_pair"),
        CheckConstraint(f"app_key IS NULL OR app_key ~ '{APP_KEY_RE}'", name="app_key_format"),
        CheckConstraint(
            "changes IS NULL OR jsonb_typeof(changes) = 'object'", name="changes_object"
        ),
        CheckConstraint(
            f"request_id IS NULL OR request_id ~ '{REQUEST_ID_RE}'", name="request_id_format"
        ),
        CheckConstraint("outcome IN ('success', 'failure', 'denied')", name="outcome_valid"),
        # Ascending: a backward scan serves `ORDER BY occurred_at DESC, id DESC`.
        Index("ix_audit_log_occurred_at_id", "occurred_at", "id"),
        Index(
            "ix_audit_log_actor_user_id",
            "actor_user_id",
            "occurred_at",
            "id",
            postgresql_where=text("actor_user_id IS NOT NULL"),
        ),
        Index("ix_audit_log_action", "action", "occurred_at", "id"),
        Index(
            "ix_audit_log_target",
            "target_type",
            "target_id",
            "occurred_at",
            "id",
            postgresql_where=text("target_id IS NOT NULL"),
        ),
        Index(
            "ix_audit_log_app_key",
            "app_key",
            "occurred_at",
            "id",
            postgresql_where=text("app_key IS NOT NULL"),
        ),
        Index(
            "ix_audit_log_outcome",
            "outcome",
            "occurred_at",
            "id",
            postgresql_where=text("outcome IN ('failure', 'denied')"),
        ),
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    actor_kind: Mapped[str] = mapped_column(Text, nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    actor_label: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'success'"))
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    app_key: Mapped[str | None] = mapped_column(Text)
    org_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    changes: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(512))
    request_id: Mapped[str | None] = mapped_column(Text)
