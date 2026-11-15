"""Bypass confinement of the audit purge (ADM-004c): static checks over `app/` and migrations."""

import ast
import re
from pathlib import Path

from app.main import create_app
from app.modules.audit.purge import MIN_DAYS

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
PURGE_MODULE = APP / "modules" / "audit" / "purge.py"
(PURGE_MIGRATION,) = (ROOT / "migrations" / "versions").glob("*_d7e4b2a91c3f_*.py")
FORBIDDEN = ("disable trigger", "session_replication_role", "alter table audit_log", "set_config(")


def _py(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def test_allow_purge_only_in_purge_module_and_migration() -> None:
    hits = {
        path
        for path in _py(APP) + _py(ROOT / "migrations")
        if "allow_purge" in path.read_text().lower()
    }
    assert hits == {PURGE_MODULE, PURGE_MIGRATION}


def test_no_trigger_bypass_in_app() -> None:
    for path in _py(APP):
        normalized = " ".join(path.read_text().lower().split())
        for needle in FORBIDDEN:
            assert needle not in normalized, f"{needle!r} in {path.relative_to(ROOT)}"


def test_purge_module_imported_only_by_cli() -> None:
    importers = set()
    for path in _py(APP):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = {module} | {f"{module}.{alias.name}" for alias in node.names}
            else:
                continue
            if "app.modules.audit.purge" in names:
                importers.add(path)
    assert importers == {APP / "cli.py"}


def test_no_purge_route() -> None:
    paths = [getattr(route, "path", "") for route in create_app().routes]
    assert paths
    assert not [p for p in paths if "purge" in p.lower()]


def test_min_days_matches_migration_floor() -> None:
    (floor,) = re.findall(r"interval '(\d+) days'", PURGE_MIGRATION.read_text())
    assert int(floor) == MIN_DAYS
