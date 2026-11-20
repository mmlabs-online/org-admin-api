"""Pure validation of the app release write input (spec OQ36 / OQ45, ADR-0021 §7)."""

import json
from typing import Any

import pytest
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.modules.releases.models import ReleaseEnvironment
from app.modules.releases.write_schemas import (
    ReleasePut,
    check_release_path,
    check_staging_sha,
)

SHA = "0123456789abcdef0123456789abcdef01234567"
DIGEST = "sha256:" + "a" * 64
PR = "https://github.com/mmlabs-online/org-admin-api/pull/12"


def body(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"git_sha": SHA, "images": [{"name": "mmlabs/api", "digest": DIGEST}]}
    return {**base, **over}


def parse(data: dict[str, Any]) -> ReleasePut:
    return ReleasePut.model_validate_json(json.dumps(data))


def bad(data: dict[str, Any]) -> list[Any]:
    with pytest.raises(ValidationError) as exc:
        parse(data)
    return exc.value.errors(include_url=False)


def change(**over: Any) -> dict[str, Any]:
    return {"pr_url": PR, "title": "Fix it", **over}


def test_valid_minimal_and_full() -> None:
    minimal = parse(body())
    assert minimal.changes == [] and minimal.has_migrations is None
    full = parse(
        body(
            deployment_pr="https://github.com/mmlabs-online/deployment/pull/7",
            has_migrations=False,
            changes=[change(ticket_id="ADM-034", risk="high")],
        )
    )
    assert full.changes[0].risk is not None and full.changes[0].risk.value == "high"


@pytest.mark.parametrize(
    ("env", "version", "ok"),
    [
        ("production", "v1.2.3", True),
        ("production", "v0.0.0", True),
        ("production", "v01.2.3", False),
        ("production", "v1.2", False),
        ("production", "v1.2.3-rc1", False),
        ("production", "1.2.3", False),
        ("production", "v1.2.3\n", False),
        ("production", "sha-abcdef0", False),
        ("staging", "sha-abcdef0", True),
        ("staging", "sha-ABCDEF0", False),
        ("staging", "sha-abc", False),
        ("staging", "sha-abcdef0\n", False),
        ("staging", "v1.2.3", False),
    ],
)
def test_version_grammar(env: str, version: str, ok: bool) -> None:
    if ok:
        check_release_path(ReleaseEnvironment(env), version)
    else:
        with pytest.raises(RequestValidationError) as exc:
            check_release_path(ReleaseEnvironment(env), version)
        assert exc.value.errors()[0]["loc"] == ("path", "version")


def test_staging_sha_cross_check() -> None:
    put = parse(body())
    check_staging_sha(ReleaseEnvironment.STAGING, "sha-0123456", put)
    check_staging_sha(ReleaseEnvironment.PRODUCTION, "v1.0.0", put)
    with pytest.raises(RequestValidationError) as exc:
        check_staging_sha(ReleaseEnvironment.STAGING, "sha-abcdef0", put)
    assert exc.value.errors()[0]["loc"] == ("body", "git_sha")


@pytest.mark.parametrize("git_sha", ["", SHA.upper(), SHA[:-1], SHA + "0", SHA + "\n", None, 5])
def test_git_sha_format(git_sha: Any) -> None:
    assert bad(body(git_sha=git_sha))


def test_digest_lowercase_64_hex() -> None:
    for digest in ("sha256:" + "A" * 64, "sha256:" + "a" * 63, "sha1:" + "a" * 40, DIGEST + "\n"):
        assert bad(body(images=[{"name": "a/b", "digest": digest}]))


@pytest.mark.parametrize(
    "name", ["a/b:latest", "a/b@sha256:" + "a" * 64, "A/b", "a//b", "/a", "a/", "a b", "a/b\n", ""]
)
def test_image_name_no_tag_or_digest(name: str) -> None:
    assert bad(body(images=[{"name": name, "digest": DIGEST}]))


def test_image_name_accepts_registries() -> None:
    for name in ("mmlabs/api", "ghcr.io/mmlabs-online/api", "localhost:5000/a/b", "api"):
        assert parse(body(images=[{"name": name, "digest": DIGEST}]))


def test_image_name_max_255() -> None:
    assert parse(body(images=[{"name": "a" * 255, "digest": DIGEST}]))
    assert bad(body(images=[{"name": "a" * 256, "digest": DIGEST}]))


def test_images_1_to_10() -> None:
    def imgs(n: int) -> list[dict[str, str]]:
        return [{"name": f"a/n{i}", "digest": DIGEST} for i in range(n)]

    assert bad(body(images=[]))
    assert parse(body(images=imgs(10)))
    assert bad(body(images=imgs(11)))


def test_image_names_unique() -> None:
    assert bad(body(images=[{"name": "a/b", "digest": DIGEST}] * 2))


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/other/x/pull/1",
        "http://github.com/mmlabs-online/x/pull/1",
        "https://github.com/mmlabs-online/./pull/1",
        "https://github.com/mmlabs-online/../pull/1",
        "https://github.com/mmlabs-online/x/pull/0",
        "https://github.com/mmlabs-online/x/pull/1/",
        "https://github.com/mmlabs-online/x/pull/1?a=b",
        "https://github.com/mmlabs-online/x/pull/1\n",
        "https://github.com/mmlabs-online/x/issues/1",
    ],
)
def test_pr_url_allow_list(url: str) -> None:
    assert bad(body(changes=[change(pr_url=url)]))


def test_pr_url_unique_and_changes_max_200() -> None:
    assert bad(body(changes=[change(), change(title="other")]))
    many = [change(pr_url=f"https://github.com/mmlabs-online/x/pull/{i}") for i in range(1, 201)]
    assert parse(body(changes=many))
    extra = [*many, change(pr_url="https://github.com/mmlabs-online/x/pull/999")]
    assert bad(body(changes=extra))


def test_title_strip_and_bounds() -> None:
    def title(raw: str) -> str:
        return parse(body(changes=[change(title=raw)])).changes[0].title

    assert title("  a\x00b\x1fc\x85d  ") == "abcd"
    assert title("x‮y​z") == "xyz"  # bidi override, zero-width space
    assert title("é") == "é"  # NFC
    assert title("a" * 200) == "a" * 200
    assert bad(body(changes=[change(title="\x00​ \n")]))  # nothing left
    assert bad(body(changes=[change(title="a" * 201)]))
    assert bad(body(changes=[change(title="")]))


def test_ticket_id_and_risk_formats() -> None:
    for ok in ("ADM-1", "USR-05a", "A1-123456", "BUG-001"):
        assert parse(body(changes=[change(ticket_id=ok)]))
    for no in ("adm-1", "A-1", "ADM1", "ADM-1234567", "ADM-1\n", "ADM-1A", "ABCDEFGHIJK-1"):
        assert bad(body(changes=[change(ticket_id=no)]))
    assert bad(body(changes=[change(risk="critical")]))
    assert parse(body(changes=[change(ticket_id=None, risk=None)]))


def test_deployment_pr_exact() -> None:
    ok = "https://github.com/mmlabs-online/deployment/pull/7"
    assert parse(body(deployment_pr=ok))
    for no in (ok + "/", ok + "\n", ok.replace("deployment", "other"), ok[:-1] + "0"):
        assert bad(body(deployment_pr=no))


def test_strict_types() -> None:
    assert bad(body(has_migrations="true"))
    assert bad(body(has_migrations=1))
    assert bad(body(changes=[change(title=5)]))


def test_extra_forbid_every_level() -> None:
    assert bad(body(status="deployed"))
    assert bad(body(author="x"))
    assert bad(body(images=[{"name": "a/b", "digest": DIGEST, "x": 1}]))
    assert bad(body(changes=[change(author_email="a@b.c")]))


def test_errors_never_echo_input() -> None:
    secret = "SuperSecretValue123"
    data = body(
        git_sha=secret,
        deployment_pr=secret,
        images=[{"name": secret + "/UPPER", "digest": secret}],
        changes=[change(pr_url=secret, ticket_id=secret, risk=secret)],
        has_migrations=secret,
    )
    errors = bad(data)
    assert len(errors) >= 6
    for error in errors:
        assert secret not in json.dumps({"msg": error["msg"], "type": error["type"]})
        assert secret not in json.dumps(error.get("ctx", {}), default=str)
