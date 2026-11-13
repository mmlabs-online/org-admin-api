"""Operator CLI. Never imported by `app.main`; not reachable over HTTP.

- `python -m app.cli grant-super-admin --email <email>` (USR-02f): creates no users, takes no
  password, never marks an email verified (D16).
- `python -m app.cli register-app --key <key> --name <name> --origin <origin> ...` (USR-07):
  creates or updates a consuming app and its `<key>:member` role through `AppService.register`.
- `python -m app.cli cleanup [--dry-run]` (USR-04d): deletes email send-log rows older than 48 h
  and used or expired email tokens older than 7 d, in batches; prints counts only.

Output never contains the full email, a submitted value, a traceback or the DSN (R11): every
failure prints one fixed line, and the CLI uses its own engine with `hide_parameters=True`.
"""

import argparse
import asyncio
import logging
import sys
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import TYPE_CHECKING, NoReturn

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.config import Settings
    from app.modules.rbac.schemas import AppRegister
    from app.modules.rbac.service import RegisterResult

    SessionScope = Callable[[], AbstractAsyncContextManager[AsyncSession]]

USAGE = "usage: python -m app.cli grant-super-admin --email <email>"
MSG_UNKNOWN_EMAIL = "error: no account with this email; sign up first"
MSG_DISABLED = "error: the account is disabled; nothing was granted"
MSG_NOT_SEEDED = "error: admin app or admin:super-admin role missing; run migrations"
MSG_CONFIG = "error: settings could not be loaded; see docs/deploy.md (Required runtime config)"
MSG_DB_ECHO = "error: refusing to run with DB_ECHO enabled"
MSG_UNEXPECTED = "error: unexpected failure; the grant was not confirmed"
CHECK_LINE = (
    "check: created_at must match your own sign-up; if not, revoke this grant (docs/deploy.md)"
)

REGISTER_USAGE = (
    "usage: python -m app.cli register-app --key <key> --name <name> --origin <origin>"
    " [--origin <origin> ...] [--join-policy open|approval|closed] [--org-mode none|required]"
    " [--permission <key>:<resource>:<action> ...] [--dry-run]"
)
MSG_ADMIN_APP = "error: the admin app cannot be registered or changed from the CLI"
MSG_BAD_KEY = "error: --key must be 2-32 characters: a-z first, then a-z, 0-9 or -"
MSG_BAD_NAME = "error: --name must be 1-255 characters of printable text"
MSG_BAD_ORIGIN = (
    "error: each --origin must be https://mmlabs.online or a subdomain"
    " (not api.mmlabs.online, default port), or loopback; at most 20"
)
MSG_LOOPBACK = "error: loopback origins are not allowed in staging/production"
MSG_BAD_PERMISSION = (
    "error: each --permission must be <key>:<resource>:<action> with this app's key (no <key>:*)"
)
MSG_ADMIN_PERMISSION = (
    "error: a --permission is an is_admin permission; the CLI never grants those to the member role"
)
MSG_ORIGIN_TAKEN = "error: an origin is already registered to another app; nothing was saved"
MSG_ORG_MODE = (
    "error: org_mode of an existing app cannot be changed from the CLI; nothing was saved"
)
MSG_APP_DISABLED = "error: the app is disabled; nothing was changed"
MSG_CONFLICT = "error: the app was changed concurrently; nothing was saved, run again"
MSG_REGISTER_UNEXPECTED = "error: unexpected failure; the registration was not confirmed"
MSG_DRY_RUN = "dry-run: rolled back, nothing was saved"
MSG_CORS_NOTE = "note: the API applies origin changes within 60 s"

CLEANUP_USAGE = "usage: python -m app.cli cleanup [--dry-run]"
MSG_CLEANUP_FAILED = "error: cleanup failed; batches already committed stay deleted, run again"
MSG_CLEANUP_DRY_RUN = "dry-run: nothing was deleted"

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_UNKNOWN_EMAIL = 2
EXIT_DISABLED = 3
EXIT_USAGE = 64


class _UsageError(Exception):
    pass


class _Fail(Exception):
    def __init__(self, message: str) -> None:
        self.message = message


class _Refused(Exception):
    def __init__(self, message: str, code: int) -> None:
        self.message = message
        self.code = code


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse's message echoes unrecognized arguments (possibly the email): drop it.
        raise _UsageError


def _parse_email(argv: Sequence[str]) -> str:
    from pydantic import EmailStr, TypeAdapter, ValidationError

    parser = _Parser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    grant = commands.add_parser("grant-super-admin")
    grant.add_argument("--email", required=True)
    args = parser.parse_args(argv)
    try:
        # Same normalization as sign-up (app/modules/auth/service.py).
        return str(TypeAdapter(EmailStr).validate_python(args.email)).strip().lower()
    except ValidationError:
        raise _UsageError from None


def _load_settings(settings: "Settings | None") -> "Settings":
    from app.core.config import get_settings

    if settings is None:
        try:
            settings = get_settings()
        except Exception:
            raise _Fail(MSG_CONFIG) from None
    if settings.db_echo:
        raise _Fail(MSG_DB_ECHO)
    return settings


@asynccontextmanager
async def _cli_session_scope(settings: "Settings") -> AsyncIterator["AsyncSession"]:
    """CLI-only engine: never `app.db.session` (API engine, `echo=DB_ECHO`, parameters shown)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(
        str(settings.database_url), hide_parameters=True, echo=False, poolclass=NullPool
    )
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with factory() as session, session.begin():
            yield session
    finally:
        await engine.dispose()


@asynccontextmanager
async def _cli_cleanup_scope(settings: "Settings") -> AsyncIterator["AsyncSession"]:
    """Like `_cli_session_scope`, but without `session.begin()`: cleanup commits per batch."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(
        str(settings.database_url), hide_parameters=True, echo=False, poolclass=NullPool
    )
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


async def amain(
    argv: Sequence[str],
    *,
    settings: "Settings | None" = None,
    session_scope: "SessionScope | None" = None,
) -> int:
    if argv[:1] == ["register-app"]:
        return await _register_app(argv[1:], settings=settings, session_scope=session_scope)
    if argv[:1] == ["cleanup"]:
        return await _cleanup(argv[1:], settings=settings, session_scope=session_scope)
    try:
        email = _parse_email(argv)

        from app.core.text import mask_email
        from app.modules.rbac.service import AssignmentService, BootstrapOutcome

        settings = _load_settings(settings)
        scope = session_scope or (lambda: _cli_session_scope(settings))
        async with scope() as session:
            result = await AssignmentService(session).bootstrap_super_admin(email)

        if result.outcome == BootstrapOutcome.UNKNOWN_EMAIL:
            print(MSG_UNKNOWN_EMAIL, file=sys.stderr)
            return EXIT_UNKNOWN_EMAIL
        if result.outcome == BootstrapOutcome.USER_DISABLED:
            print(MSG_DISABLED, file=sys.stderr)
            return EXIT_DISABLED
        if result.outcome == BootstrapOutcome.NOT_SEEDED:
            raise _Fail(MSG_NOT_SEEDED)

        if result.email is None or result.created_at is None:  # set for GRANTED / ALREADY_HELD
            raise _Fail(MSG_UNEXPECTED)
        head = "granted" if result.outcome == BootstrapOutcome.GRANTED else "already held"
        print(f"{head} admin:super-admin")
        print(f"user_id: {result.user_id}")
        print(f"email: {mask_email(result.email)}")
        print(f"created_at: {result.created_at.isoformat()}")
        print(f"email_verified: {'yes' if result.email_verified else 'no'}")
        print(CHECK_LINE)
        return EXIT_OK
    except _UsageError:
        print(USAGE, file=sys.stderr)
        return EXIT_USAGE
    except _Fail as fail:
        print(fail.message, file=sys.stderr)
        return EXIT_FAILURE
    except Exception:
        # Never str(exc): DB errors carry parameters and DETAIL lines (the email) and the DSN.
        print(MSG_UNEXPECTED, file=sys.stderr)
        return EXIT_FAILURE


class _Rollback(Exception):
    def __init__(self, result: "RegisterResult") -> None:
        self.result = result


# Service refusal reason → (fixed message, exit code). Never str(exc).
_REASONS = {
    "admin_app": (MSG_ADMIN_APP, EXIT_USAGE),
    "loopback_not_allowed": (MSG_LOOPBACK, EXIT_USAGE),
    "prefix_mismatch": (MSG_BAD_PERMISSION, EXIT_USAGE),
    "wildcard_not_allowed": (MSG_BAD_PERMISSION, EXIT_USAGE),
    "is_admin_permission": (MSG_ADMIN_PERMISSION, EXIT_FAILURE),
    "origin_taken": (MSG_ORIGIN_TAKEN, EXIT_FAILURE),
    "org_mode_change": (MSG_ORG_MODE, EXIT_FAILURE),
    "app_disabled": (MSG_APP_DISABLED, EXIT_DISABLED),
}
_FIELD_MESSAGES = {
    "key": MSG_BAD_KEY,
    "name": MSG_BAD_NAME,
    "origins": MSG_BAD_ORIGIN,
    "permissions": MSG_BAD_PERMISSION,
}


def _parse_register(argv: Sequence[str]) -> tuple["AppRegister", bool]:
    from pydantic import ValidationError

    from app.modules.rbac.schemas import AppRegister

    parser = _Parser(prog="python -m app.cli register-app", allow_abbrev=False, add_help=False)
    parser.add_argument("--key", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--origin", action="append", required=True)
    parser.add_argument("--join-policy", choices=["open", "approval", "closed"], default=None)
    parser.add_argument("--org-mode", choices=["none", "required"], default=None)
    parser.add_argument("--permission", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.key == "admin":
        raise _Refused(MSG_ADMIN_APP, EXIT_USAGE)
    try:
        data = AppRegister.model_validate(
            {
                "key": args.key,
                "name": args.name,
                "origins": args.origin,
                "join_policy": args.join_policy,
                "org_mode": args.org_mode,
                "permissions": args.permission,
            }
        )
    except ValidationError as exc:
        # Never print the pydantic error: it carries `input_value`.
        errors = exc.errors()
        field = str(errors[0]["loc"][0]) if errors and errors[0]["loc"] else ""
        message = _FIELD_MESSAGES.get(field)
        if message is None:
            raise _UsageError from None
        raise _Refused(message, EXIT_USAGE) from None
    return data, bool(args.dry_run)


def _print_result(result: "RegisterResult") -> None:
    member = (
        "member role: missing"
        if result.member_role is None
        else f"member role: {result.member_role} = "
        + (", ".join(result.member_permissions) or "(none)")
    )
    lines = [
        f"{result.outcome.value} app {result.key}",
        f"name: {result.name}",
        f"join_policy: {result.join_policy}",
        f"org_mode: {result.org_mode}",
        f"origins: {' '.join(result.origins)}",
        member,
        *(f"~ {field}: {old} -> {new}" for field, old, new in result.changes),
        *(f"+ origin {o}" for o in result.origins_added),
        *(f"- origin {o}" for o in result.origins_removed),
        *(f"+ permission {p}" for p in result.permissions_added),
        *(f"+ member {p}" for p in result.member_added),
        *(f"- member {p}" for p in result.member_removed),
    ]
    if result.origins_added or result.origins_removed:
        lines.append(MSG_CORS_NOTE)
    print("\n".join(lines))


async def _register_app(
    argv: Sequence[str],
    *,
    settings: "Settings | None",
    session_scope: "SessionScope | None",
) -> int:
    try:
        data, dry_run = _parse_register(argv)

        from app.core.errors import AppError, ConflictError
        from app.modules.rbac.service import AppService

        settings = _load_settings(settings)
        app_env = settings.app_env
        scope = session_scope or (lambda: _cli_session_scope(settings))
        try:
            async with scope() as session:
                result = await AppService(session).register(data, app_env=app_env)
                if dry_run:
                    raise _Rollback(result)
        except _Rollback as rollback:
            _print_result(rollback.result)
            print(MSG_DRY_RUN)
            return EXIT_OK
        except AppError as exc:
            details = exc.details if isinstance(exc.details, list) else []
            reason = details[0].get("reason") if details and isinstance(details[0], dict) else None
            if reason in _REASONS:
                raise _Refused(*_REASONS[reason]) from None
            if isinstance(exc, ConflictError):
                raise _Refused(MSG_CONFLICT, EXIT_FAILURE) from None
            raise _Refused(MSG_REGISTER_UNEXPECTED, EXIT_FAILURE) from None

        _print_result(result)  # only after the scope committed
        return EXIT_OK
    except _UsageError:
        print(REGISTER_USAGE, file=sys.stderr)
        return EXIT_USAGE
    except _Refused as refused:
        print(refused.message, file=sys.stderr)
        return refused.code
    except _Fail as fail:
        print(fail.message, file=sys.stderr)
        return EXIT_FAILURE
    except Exception:
        # Never str(exc): DB errors carry parameters (origins, names) and the DSN.
        print(MSG_REGISTER_UNEXPECTED, file=sys.stderr)
        return EXIT_FAILURE


async def _cleanup(
    argv: Sequence[str],
    *,
    settings: "Settings | None",
    session_scope: "SessionScope | None",
) -> int:
    try:
        parser = _Parser(prog="python -m app.cli cleanup", allow_abbrev=False, add_help=False)
        parser.add_argument("--dry-run", action="store_true")
        dry_run = bool(parser.parse_args(argv).dry_run)

        from app.modules.auth.retention import EmailRetentionService

        settings = _load_settings(settings)
        scope = session_scope or (lambda: _cli_cleanup_scope(settings))
        async with scope() as session:
            counts = await EmailRetentionService(session).run(dry_run=dry_run)

        verb = "would delete" if dry_run else "deleted"
        for count in counts:
            print(f"{count.table}: {verb} {count.rows} ({count.retention})")
        if dry_run:
            print(MSG_CLEANUP_DRY_RUN)
        return EXIT_OK
    except _UsageError:
        print(CLEANUP_USAGE, file=sys.stderr)
        return EXIT_USAGE
    except _Fail as fail:
        print(fail.message, file=sys.stderr)
        return EXIT_FAILURE
    except Exception:
        # Never str(exc): DB errors carry the DSN and DETAIL lines.
        print(MSG_CLEANUP_FAILED, file=sys.stderr)
        return EXIT_FAILURE


def main() -> NoReturn:
    sys.tracebacklimit = 0  # backstop for BaseExceptions such as KeyboardInterrupt
    for name in ("sqlalchemy", "asyncpg"):
        logger = logging.getLogger(name)
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
    sys.exit(asyncio.run(amain(sys.argv[1:])))


if __name__ == "__main__":
    main()
