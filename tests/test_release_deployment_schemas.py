"""ReleaseDeploymentReport and the pure deploy-report rules (ADM-036, OQ38 / OQ39 / OQ40 / OQ45)."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from app.modules.releases.rules import (
    Deployment,
    ReportKind,
    decide_report,
    deployed_at_window_error,
)
from app.modules.releases.write_schemas import ReleaseDeploymentReport

URL = "https://github.com/mmlabs-online/deployment/actions/runs/123"
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def _report(**over: Any) -> dict[str, Any]:
    return {"status": "deployed", "deploy_run_url": URL, "deployed_at": NOW.isoformat(), **over}


def _locs(data: dict[str, Any]) -> set[tuple[Any, ...]]:
    with pytest.raises(ValidationError) as exc:
        ReleaseDeploymentReport.model_validate_json(json.dumps(data))
    return {e["loc"] for e in exc.value.errors()}


def test_valid_deployed_and_failed() -> None:
    ok = ReleaseDeploymentReport.model_validate_json(
        json.dumps(_report(deployed_at="2026-11-20T19:00:00+07:00"))
    )
    assert (
        ok.deployed_at == NOW
        and ok.deployed_at is not None
        and ok.deployed_at.utcoffset() == (timedelta(0))
    )
    failed = ReleaseDeploymentReport.model_validate({"status": "failed", "deploy_run_url": URL})
    assert failed.deployed_at is None


@pytest.mark.parametrize("status", ["pending", "DEPLOYED", ""])
def test_status_values(status: str) -> None:
    assert ("status",) in _locs(_report(status=status))
    assert ("status",) in _locs({"deploy_run_url": URL})


def test_deployed_at_required_with_deployed() -> None:
    assert ("deployed_at",) in _locs({"status": "deployed", "deploy_run_url": URL})
    assert ("deployed_at",) in _locs(_report(deployed_at=None))


def test_deployed_at_refused_with_failed() -> None:
    assert ("deployed_at",) in _locs(_report(status="failed"))


@pytest.mark.parametrize("value", ["2026-11-20T12:00:00", 1790000000, "yesterday"])
def test_deployed_at_strict(value: Any) -> None:
    assert ("deployed_at",) in _locs(_report(deployed_at=value))


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/other/deployment/actions/runs/1",
        "https://github.com/mmlabs-online/other/actions/runs/1",
        "http://github.com/mmlabs-online/deployment/actions/runs/1",
        "https://github.com/mmlabs-online/deployment/actions/runs/0",
        "https://github.com/mmlabs-online/deployment/actions/runs/" + "1" * 21,
        URL + "/attempts/0",
        URL + "/attempts/1000",
        URL + "/",
        URL + "?x=1",
        URL + "#x",
        URL + "\n",
        " " + URL,
        URL + "/attempts/" + "1" * 300,
    ],
)
def test_run_url_allow_list_refused(url: str) -> None:
    assert ("deploy_run_url",) in _locs(_report(deploy_run_url=url))


@pytest.mark.parametrize("url", [URL, URL + "/attempts/2"])
def test_run_url_allow_list_ok(url: str) -> None:
    assert ReleaseDeploymentReport.model_validate_json(json.dumps(_report(deploy_run_url=url)))


def test_run_url_required_for_both_statuses() -> None:
    assert ("deploy_run_url",) in _locs({"status": "failed"})
    assert ("deploy_run_url",) in _locs({"status": "deployed", "deployed_at": NOW.isoformat()})


@pytest.mark.parametrize("extra", ["created_via", "git_sha", "images"])
def test_extra_field_forbidden(extra: str) -> None:
    assert (extra,) in _locs(_report(**{extra: "x"}))


def test_errors_never_echo_input() -> None:
    secret = "https://evil.example/SECRET-VALUE"
    with pytest.raises(ValidationError) as exc:
        ReleaseDeploymentReport.model_validate({"status": "failed", "deploy_run_url": secret})
    shown = exc.value.errors(include_url=False, include_input=False, include_context=False)
    assert "SECRET-VALUE" not in str(shown)


# --- pure rules -------------------------------------------------------------------

U1, U2 = URL, "https://github.com/mmlabs-online/deployment/actions/runs/124"
T1, T2 = NOW, NOW + timedelta(hours=1)


def _d(status: str, at: datetime | None = None, url: str | None = U1) -> Deployment:
    return Deployment(status, at, url)


def test_none_stored_is_create() -> None:
    assert decide_report(None, _d("deployed", T1)).kind is ReportKind.CREATE
    assert decide_report(None, _d("failed")).kind is ReportKind.CREATE


def test_pending_to_deployed_fields() -> None:
    d = decide_report(_d("pending", None, None), _d("deployed", T1))
    assert (d.kind, d.fields) == (ReportKind.UPDATE, ("status", "deployed_at", "deploy_run_url"))


def test_pending_to_failed_fields() -> None:
    d = decide_report(_d("pending", None, None), _d("failed"))
    assert (d.kind, d.fields) == (ReportKind.UPDATE, ("status", "deploy_run_url"))


def test_failed_to_deployed() -> None:
    d = decide_report(_d("failed"), _d("deployed", T1))
    assert (d.kind, d.fields) == (ReportKind.UPDATE, ("status", "deployed_at", "deploy_run_url"))


def test_failed_to_failed_new_url_update_url_only() -> None:
    d = decide_report(_d("failed"), _d("failed", None, U2))
    assert (d.kind, d.fields) == (ReportKind.UPDATE, ("deploy_run_url",))


def test_failed_to_failed_same_url_noop() -> None:
    assert decide_report(_d("failed"), _d("failed")).kind is ReportKind.NOOP


def test_deployed_same_url_noop_even_if_deployed_at_differs() -> None:
    assert decide_report(_d("deployed", T1), _d("deployed", T2)).kind is ReportKind.NOOP


def test_deployed_new_url_later_or_equal_deployed_at_update() -> None:
    later = decide_report(_d("deployed", T1), _d("deployed", T2, U2))
    assert (later.kind, later.fields) == (ReportKind.UPDATE, ("deployed_at", "deploy_run_url"))
    equal = decide_report(_d("deployed", T1), _d("deployed", T1, U2))
    assert (equal.kind, equal.fields) == (ReportKind.UPDATE, ("deploy_run_url",))


def test_deployed_new_url_older_deployed_at_stale_noop() -> None:
    assert decide_report(_d("deployed", T2), _d("deployed", T1, U2)).kind is ReportKind.NOOP


def test_deployed_to_failed_conflict() -> None:
    assert decide_report(_d("deployed", T1), _d("failed", None, U2)).kind is ReportKind.CONFLICT


def test_window_boundaries() -> None:
    assert deployed_at_window_error(NOW + timedelta(minutes=5), NOW) is None
    assert (
        deployed_at_window_error(NOW + timedelta(minutes=5, microseconds=1), NOW)
        == "too_far_in_future"
    )
    assert deployed_at_window_error(NOW - timedelta(days=30), NOW) is None
    assert deployed_at_window_error(NOW - timedelta(days=30, microseconds=1), NOW) == "too_old"
