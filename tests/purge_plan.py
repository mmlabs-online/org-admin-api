"""Planner helpers for the BUG-001 regression tests (batch limit vs. a rescanned subquery)."""

from sqlalchemy import Executable, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

GUCS = (
    "enable_hashjoin",
    "enable_mergejoin",
    "enable_material",
    "enable_hashagg",
    "enable_sort",
)


async def force_rescanned_semijoin(session: AsyncSession) -> None:
    """Make the planner run `id IN (SELECT ... LIMIT n FOR UPDATE)` as a nested-loop semi join
    that rescans the limited subquery once per outer row (index/seq scans stay enabled)."""
    for guc in GUCS:
        await session.execute(text(f"SET LOCAL {guc} = off"))


async def explain(session: AsyncSession, stmt: Executable) -> str:
    sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    rows = await session.execute(text("EXPLAIN " + sql))
    return "\n".join(r[0] for r in rows)
