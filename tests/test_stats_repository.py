"""StatsRepository sign-up buckets (fixed clock) and the audit count's index use (ADM-015)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.repository import AuditRepository
from app.modules.stats.repository import StatsRepository
from tests.auth_factories import make_login_user

NOW = datetime(2026, 11, 15, tzinfo=UTC)
TZ = "Asia/Ho_Chi_Minh"


async def _user_at(db: AsyncSession, created_at: datetime, *, status: str = "active") -> None:
    user = await make_login_user(db, status=status)
    await db.execute(
        text("UPDATE users SET created_at = :c WHERE id = :id"), {"c": created_at, "id": user.id}
    )


async def _by_month(db: AsyncSession) -> dict[str, int]:
    rows = await StatsRepository(db).sign_ups_by_month(NOW, 12, TZ)
    return dict(rows)


async def test_sign_ups_month_boundary_in_hcm(db_session: AsyncSession) -> None:
    before = await _by_month(db_session)
    await _user_at(db_session, datetime(2026, 10, 31, 16, 59, 59, tzinfo=UTC))
    await _user_at(db_session, datetime(2026, 10, 31, 17, 0, 0, tzinfo=UTC))
    after = await _by_month(db_session)
    assert after["2026-10"] - before["2026-10"] == 1
    assert after["2026-11"] - before["2026-11"] == 1


async def test_sign_ups_window_and_zero_fill(db_session: AsyncSession) -> None:
    before = await _by_month(db_session)
    await _user_at(db_session, datetime(2025, 11, 30, 16, 59, 59, tzinfo=UTC))
    await _user_at(db_session, datetime(2025, 11, 30, 17, 0, 0, tzinfo=UTC), status="disabled")
    rows = await StatsRepository(db_session).sign_ups_by_month(NOW, 12, TZ)
    months = [m for m, _ in rows]
    assert months[0] == "2025-12"
    assert months[-1] == "2026-11"
    assert len(months) == 12
    after = dict(rows)
    assert set(after) == set(before)
    assert sum(after.values()) - sum(before.values()) == 1
    assert after["2025-12"] - before["2025-12"] == 1


async def test_login_count_uses_action_index(db_session: AsyncSession) -> None:
    await db_session.execute(text("SET LOCAL enable_seqscan = off"))
    # Text mirror of `AuditRepository.count_actions_since`.
    plan = (
        await db_session.execute(
            text(
                "EXPLAIN SELECT action, count(*) FROM audit_log "
                "WHERE action IN ('auth.login_succeeded', 'auth.login_failed') "
                "AND occurred_at >= now() - interval '24 hours' GROUP BY action"
            )
        )
    ).scalars()
    text_plan = "\n".join(plan)
    assert "ix_audit_log_action" in text_plan
    assert "Seq Scan on audit_log" not in text_plan
    # The method itself runs.
    since = datetime.now(UTC) - timedelta(hours=24)
    assert isinstance(await AuditRepository(db_session).count_actions_since(["x"], since), dict)
