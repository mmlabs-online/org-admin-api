import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.cli import EXIT_FAILURE, EXIT_OK, EXIT_USAGE, OPENAPI_USAGE, amain
from app.core.config import Settings
from app.core.openapi import render_openapi
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent


def _render() -> bytes:
    return render_openapi(create_app(Settings.model_construct(app_env="local")))


def test_dump_deterministic_in_process() -> None:
    first = _render()
    assert first == _render()
    assert first.endswith(b"\n")


async def test_cli_openapi_writes_file(tmp_path: Path) -> None:
    out = tmp_path / "o.json"
    assert await amain(["openapi", "--output", str(out)]) == EXIT_OK
    assert out.read_bytes() == _render()
    json.loads(out.read_text())


async def test_cli_openapi_stdout(capfd: pytest.CaptureFixture[str]) -> None:
    assert await amain(["openapi"]) == EXIT_OK
    assert json.loads(capfd.readouterr().out)["openapi"]


@pytest.mark.parametrize("argv", [["openapi", "--bogus"], ["openapi", "--output"]])
async def test_cli_openapi_usage_exit_64(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert await amain(argv) == EXIT_USAGE
    assert OPENAPI_USAGE in capsys.readouterr().err


async def test_cli_openapi_unwritable_path_exit_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "missing" / "o.json"
    assert await amain(["openapi", "--output", str(target)]) == EXIT_FAILURE
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "Traceback" not in err
    assert "missing" not in err


def test_dump_ignores_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = _render()
    monkeypatch.setenv("APP_NAME", "other")
    monkeypatch.setenv("API_PREFIX", "/x")
    monkeypatch.setenv("API_DOCS_ENABLED", "false")
    assert _render() == expected


def test_dump_contains_all_routers() -> None:
    paths = json.loads(_render())["paths"]
    for path in (
        "/healthz",
        "/.well-known/jwks.json",
        "/api/v1/auth/login",
        "/api/v1/organizations",
        "/api/v1/users",
        "/api/v1/apps/{key}/roles",
        "/api/v1/audit-logs",
    ):
        assert path in paths, path
    assert any("assignments" in p for p in paths)


def test_subprocess_dump_offline_deterministic(tmp_path: Path) -> None:
    env = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ.get("HOME", str(tmp_path)),
        "PYTHONPATH": str(ROOT),
        "DATABASE_URL": "postgresql+asyncpg://u:p@127.0.0.1:1/none",
    }
    outputs = []
    for name in ("a.json", "b.json"):
        out = tmp_path / name
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "app.cli", "openapi", "--output", str(out)],
            env=env,
            cwd=tmp_path,
            capture_output=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        outputs.append(out.read_bytes())
    assert outputs[0] == outputs[1] == _render()
