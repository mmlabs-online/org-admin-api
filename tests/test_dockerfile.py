"""The image's uvicorn never trusts X-Forwarded-* (ADM-011, ADR-0002 §2)."""

from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"


def test_uvicorn_cmd_disables_proxy_headers() -> None:
    cmd = [line for line in DOCKERFILE.read_text().splitlines() if line.startswith("CMD ")]
    assert len(cmd) == 1
    assert '"--no-proxy-headers"' in cmd[0]
