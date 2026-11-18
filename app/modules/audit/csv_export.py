"""CSV serialisation of audit rows (ADM-022): pure, no I/O. Cells are escaped against formula
injection (OWASP CSV injection)."""

import csv
import io
import json
from collections.abc import Sequence
from typing import Any

from app.modules.audit.models import AuditLog
from app.modules.audit.schemas import AuditLogRead

COLUMNS = tuple(AuditLogRead.model_fields)
_TRIGGERS = frozenset(
    {"=", "+", "-", "@", "\t", "\r", "\n", "\uff1d", "\uff0b", "\uff0d", "\uff20"}
)


def escape_cell(value: str) -> str:
    """Prefix `'` when a spreadsheet could read the cell as a formula (also after spaces)."""
    if value[:1] in _TRIGGERS or value.lstrip(" ")[:1] in _TRIGGERS:
        return "'" + value
    return value


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return str(value)


def render_csv(rows: Sequence[AuditLog]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    for row in rows:
        data = AuditLogRead.model_validate(row).model_dump(mode="json")
        writer.writerow([escape_cell(_cell(data[c])) for c in COLUMNS])
    return buf.getvalue()
