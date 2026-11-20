"""ADM-028: pure text rules and the create schema."""

import re
import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from app.modules.bug_reports import text
from app.modules.bug_reports.models import BugReport
from app.modules.bug_reports.schemas import BugReportCreate

# The CHECK text of `ck_bug_reports_page_url_reduced`, translated to Python `re`.
_DB_URL = re.compile(r"^https?://[^/?#@\s]+/[^?#\s]*$")


def test_reduce_page_url_strips_userinfo_query_fragment() -> None:
    assert (
        text.reduce_page_url("https://u:p@Example.COM:8443/a/b?q=1#token=x")
        == "https://example.com:8443/a/b"
    )


def test_reduce_page_url_default_port_dropped_and_empty_path() -> None:
    assert text.reduce_page_url("https://h:443") == "https://h/"
    assert text.reduce_page_url("http://h:80/x") == "http://h/x"
    assert text.reduce_page_url("https://h?q=1") == "https://h/"
    assert text.reduce_page_url("HTTPS://[::1]:8443/x") == "https://[::1]:8443/x"


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "data:text/html,x",
        "file:///etc/passwd",
        "/relative",
        "https://",
        "https://h:99999/",
        "https://h/a b",
        "https://h/a\nb",
        "https://h/" + "a" * 2048,
    ],
)
def test_reduce_page_url_rejects(value: str) -> None:
    with pytest.raises(ValueError):
        text.reduce_page_url(value)


def test_reduce_page_url_keeps_unicode_path() -> None:
    out = text.reduce_page_url("https://h.example/đường/dẫn?x=1")
    assert out == "https://h.example/đường/dẫn"
    assert _DB_URL.match(out)


@pytest.mark.parametrize("value", ["", "   ", "x" * 5001, "a\x00b", "a\x07b", "a\x85b"])
def test_description_rejects(value: str) -> None:
    with pytest.raises(ValueError):
        text.clean_description(value)


def test_description_accepts() -> None:
    assert text.clean_description("x" * 5000) == "x" * 5000
    assert text.clean_description("  a\nb\tc\r\n ") == "a\nb\tc"


def test_app_version_and_user_agent_rules() -> None:
    assert text.check_app_version("v" * 64)
    assert text.check_printable("u" * 512, text.USER_AGENT_MAX)
    for bad in ("v" * 65, "", "1.0\n", "1.\x7f", "é"):
        with pytest.raises(ValueError):
            text.check_app_version(bad)
    for bad in ("u" * 513, "", "a\x00", "a\nb"):
        with pytest.raises(ValueError):
            text.check_printable(bad, text.USER_AGENT_MAX)


def test_model_limits_match_text_constants() -> None:
    table: Any = BugReport.__table__
    assert table.c.page_url.type.length == text.PAGE_URL_MAX
    assert table.c.app_version.type.length == text.APP_VERSION_MAX
    assert table.c.user_agent.type.length == text.USER_AGENT_MAX


@pytest.mark.parametrize("field", ["app_key", "app", "user_id", "status", "handled_by"])
def test_create_schema_forbids_extra_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        BugReportCreate.model_validate(
            {"account_id": str(uuid.uuid4()), "description": "x", field: "y"}
        )
