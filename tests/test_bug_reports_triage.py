"""ADM-029: pure triage rules and audit payloads (no I/O)."""

from app.modules.audit.actions import AuditAction
from app.modules.bug_reports.triage import audit_entries, decide


def test_open_to_done() -> None:
    d = decide("open", None, {"status": "done"})
    assert d.values == {"status": "done"}
    assert d.status_change == ("open", "done")
    assert audit_entries(d) == [
        (AuditAction.BUG_REPORT_MARKED_DONE, {"status": {"from": "open", "to": "done"}})
    ]


def test_done_to_open() -> None:
    d = decide("done", "kept", {"status": "open"})
    assert d.values == {"status": "open"}
    assert [a for a, _ in audit_entries(d)] == [AuditAction.BUG_REPORT_REOPENED]


def test_same_status_noop() -> None:
    d = decide("done", None, {"status": "done"})
    assert d.is_noop
    assert d.values == {}
    assert audit_entries(d) == []


def test_note_set_change_clear() -> None:
    assert decide("open", None, {"resolution_note": "a"}).note_change is not None
    assert decide("open", "a", {"resolution_note": "b"}).values == {"resolution_note": "b"}
    cleared = decide("open", "a", {"resolution_note": None})
    assert cleared.values == {"resolution_note": None}
    assert audit_entries(cleared) == [
        (
            AuditAction.BUG_REPORT_NOTE_UPDATED,
            {"resolution_note": {"changed": True, "cleared": True}},
        )
    ]


def test_note_same_noop() -> None:
    assert decide("open", "a", {"resolution_note": "a"}).is_noop


def test_null_on_null_noop() -> None:
    assert decide("open", None, {"resolution_note": None}).is_noop


def test_status_and_note_two_entries_status_first() -> None:
    d = decide("open", None, {"status": "done", "resolution_note": "fixed"})
    assert [a for a, _ in audit_entries(d)] == [
        AuditAction.BUG_REPORT_MARKED_DONE,
        AuditAction.BUG_REPORT_NOTE_UPDATED,
    ]


def test_entries_never_contain_text() -> None:
    d = decide("open", "old SECRET", {"status": "done", "resolution_note": "new SECRET"})
    assert "SECRET" not in repr(audit_entries(d))
