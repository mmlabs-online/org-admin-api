"""`escape_cell` and `render_csv` (ADM-022): pure functions."""

import csv
import io
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.modules.audit.csv_export import COLUMNS, escape_cell, render_csv
from app.modules.audit.models import AuditLog


@pytest.mark.parametrize(
    "value",
    [
        "=1+1",
        "+1",
        "-1",
        "@SUM(1)",
        "\tx",
        "\rx",
        "\nx",
        "\uff1d1",
        "\uff0b1",
        "\uff0d1",
        "\uff20x",
        "  =1+1",
    ],
)
def test_escape_cell_triggers(value: str) -> None:
    assert escape_cell(value) == "'" + value


@pytest.mark.parametrize(
    "value",
    [
        str(uuid.uuid4()),
        "2026-11-01T12:00:00+00:00",
        "::1",
        "203.0.113.5",
        "organization.created",
        "",
        '{"a":1}',
    ],
)
def test_escape_cell_leaves_safe_values(value: str) -> None:
    assert escape_cell(value) == value


def _row(**kwargs: Any) -> AuditLog:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "occurred_at": datetime(2026, 11, 1, tzinfo=UTC),
        "actor_kind": "user",
        "actor_user_id": None,
        "actor_label": None,
        "action": "organization.created",
        "outcome": "success",
        "target_type": None,
        "target_id": None,
        "app_key": None,
        "org_id": None,
        "changes": None,
        "ip": None,
        "user_agent": None,
        "request_id": None,
    }
    values.update(kwargs)
    return AuditLog(**values)


def test_render_csv_quoting_and_newlines() -> None:
    nasty = 'a,b "q"\nline2'
    out = render_csv([_row(user_agent=nasty, changes={"b": 1, "a": {"from": None, "to": "x"}})])
    assert out.endswith("\r\n")
    rows = list(csv.reader(io.StringIO(out, newline="")))
    assert tuple(rows[0]) == COLUMNS
    data = dict(zip(COLUMNS, rows[1], strict=True))
    assert data["user_agent"] == nasty
    assert data["actor_user_id"] == ""
    assert data["changes"] == '{"a":{"from":null,"to":"x"},"b":1}'
