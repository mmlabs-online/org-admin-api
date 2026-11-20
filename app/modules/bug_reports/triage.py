"""Pure triage rules for staff bug-report updates (ADM-029). No I/O.

Audit entries carry ids and status only, never note or description text (not even lengths).
"""

from dataclasses import dataclass
from typing import Any

from app.modules.audit.actions import AuditAction
from app.modules.bug_reports.models import BugReportStatus


@dataclass(frozen=True)
class NoteChange:
    cleared: bool


@dataclass(frozen=True)
class Decision:
    values: dict[str, Any]  # columns for the UPDATE, without handled_by / handled_at
    status_change: tuple[str, str] | None
    note_change: NoteChange | None

    @property
    def is_noop(self) -> bool:
        return self.status_change is None and self.note_change is None


def decide(
    current_status: str,
    current_note: str | None,
    fields: dict[str, Any],
) -> Decision:
    """`fields` holds only what the request set: `status` (str) and/or `resolution_note`
    (cleaned str, or None to clear)."""
    values: dict[str, Any] = {}
    status_change: tuple[str, str] | None = None
    note_change: NoteChange | None = None
    new_status = fields.get("status")
    if new_status is not None and new_status != current_status:
        values["status"] = new_status
        status_change = (current_status, new_status)
    if "resolution_note" in fields and fields["resolution_note"] != current_note:
        new_note = fields["resolution_note"]
        values["resolution_note"] = new_note
        note_change = NoteChange(cleared=new_note is None)
    return Decision(values, status_change, note_change)


def audit_entries(decision: Decision) -> list[tuple[AuditAction, dict[str, Any]]]:
    entries: list[tuple[AuditAction, dict[str, Any]]] = []
    if decision.status_change is not None:
        old, new = decision.status_change
        action = (
            AuditAction.BUG_REPORT_MARKED_DONE
            if new == BugReportStatus.DONE.value
            else AuditAction.BUG_REPORT_REOPENED
        )
        entries.append((action, {"status": {"from": old, "to": new}}))
    if decision.note_change is not None:
        entries.append(
            (
                AuditAction.BUG_REPORT_NOTE_UPDATED,
                {"resolution_note": {"changed": True, "cleared": decision.note_change.cleared}},
            )
        )
    return entries
