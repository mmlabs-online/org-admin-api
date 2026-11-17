"""Operator CLI. Never imported by `app.main`; not reachable over HTTP.

- `python -m app.cli grant-super-admin --email <email>` (USR-02f): creates no users, takes no
  password, never marks an email verified (D16).
- `python -m app.cli create-account --email <email> [--join <app_key>]` (USR-09, ADR-0010): the
  only CLI path that creates a user and marks its email verified; TTY-only password, never
  touches an existing account.
- `python -m app.cli register-app --key <key> --name <name> --origin <origin> ...` (USR-07):
  creates or updates a consuming app and its `<key>:member` role through `AppService.register`.
- `python -m app.cli set-admin-origins (--origin <origin> ... | --clear) [--dry-run]` (ADM-002c):
  replaces the origins of the `admin` app through `AppService.set_admin_origins`; nothing else.
- `python -m app.cli cleanup [--dry-run]` (USR-04d): deletes email send-log rows older than 48 h
  and used or expired email tokens older than 7 d, in batches; prints counts only.
- `python -m app.cli audit-purge --older-than-days <90-3650> [--dry-run]` (ADM-004c): deletes
  audit rows older than N days (the trigger refuses 90 days or newer), in batches, each with its
  `audit_log.purged` summary row; prints counts and the cutoff only. Never an HTTP route.
- `python -m app.cli openapi [--output <path>]` (ADM-013): writes the OpenAPI document from code
  defaults only; no database, no secrets, deterministic bytes.

Output never contains the full email, a submitted value, a traceback or the DSN (R11): every
failure prints one fixed line, and the CLI uses its own engine with `hide_parameters=True`.
"""

import argparse
import asyncio
import getpass
import logging
import re
import sys
import warnings
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import TYPE_CHECKING, NoReturn, Protocol

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.config import Settings
    from app.modules.rbac.schemas import AdminOriginsSet, AppRegister
    from app.modules.rbac.service import AdminOriginsResult, RegisterResult

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
    " [--permission <key>:<resource>:<action> ...]"
    " [--org-permission <key>:<resource>:<action> ...] [--dry-run]"
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
MSG_RESERVED_SCOPE = (
    "error: <key>:org:create is always a --permission and <key>:org:invite always an"
    " --org-permission"
)
MSG_SCOPE_CONFLICT = "error: a permission cannot be in both --permission and --org-permission"
MSG_ORG_MODE_REQUIRED = "error: --org-permission needs an app with org_mode required"
MSG_SCOPE_MISMATCH = (
    "error: a permission is already stored with the other scope; change it through the admin API"
)
MSG_MEMBER_IS_OWNER = "error: the member role is the org owner role; nothing was saved"
MSG_ORIGIN_TAKEN = "error: an origin is already registered to another app; nothing was saved"
MSG_ORG_MODE = (
    "error: org_mode of an existing app cannot be changed from the CLI; nothing was saved"
)
MSG_APP_DISABLED = "error: the app is disabled; nothing was changed"
MSG_CONFLICT = "error: the app was changed concurrently; nothing was saved, run again"
MSG_REGISTER_UNEXPECTED = "error: unexpected failure; the registration was not confirmed"
MSG_DRY_RUN = "dry-run: rolled back, nothing was saved"
MSG_CORS_NOTE = "note: the API applies origin changes within 60 s"

SET_ADMIN_ORIGINS_USAGE = (
    "usage: python -m app.cli set-admin-origins"
    " (--origin <origin> [--origin <origin>] | --clear) [--dry-run]"
)
MSG_ADMIN_BAD_ORIGIN = (
    "error: each --origin must be https://<host>.mmlabs.online"
    " (not api.mmlabs.online, default port), or loopback"
)
MSG_ADMIN_APEX = "error: https://mmlabs.online cannot be an admin origin; use a dedicated host"
MSG_ADMIN_TOO_MANY = "error: at most 2 admin origins"
MSG_ADMIN_NOT_SEEDED = "error: admin app missing or not in its seeded state; nothing was changed"
MSG_SET_ORIGINS_UNEXPECTED = "error: unexpected failure; the admin origins change was not confirmed"
MSG_ADMIN_CLOSED = (
    "note: no new admin tokens can be obtained once this applies;"
    " issued tokens last up to their TTL"
)

CLEANUP_USAGE = "usage: python -m app.cli cleanup [--dry-run]"
MSG_CLEANUP_FAILED = "error: cleanup failed; batches already committed stay deleted, run again"
MSG_CLEANUP_DRY_RUN = "dry-run: nothing was deleted"

AUDIT_PURGE_USAGE = "usage: python -m app.cli audit-purge --older-than-days <90-3650> [--dry-run]"
MSG_AUDIT_PURGE_FAILED = (
    "error: audit purge failed; batches already committed stay deleted with their summary rows,"
    " run again"
)

CREATE_USAGE = "usage: python -m app.cli create-account --email <email> [--join <app_key>]"
MSG_NO_TTY = "error: run from an interactive terminal (no -T, ssh -t); nothing was created"
MSG_ABORTED = "error: input aborted; nothing was created"
MSG_EMAIL_MISMATCH = "error: the retyped email does not match; nothing was created"
MSG_PASSWORD_MISMATCH = "error: the passwords do not match; nothing was created"  # noqa: S105
MSG_WEAK_PASSWORD = "error: password refused ({reasons}); nothing was created"  # noqa: S105
MSG_EMAIL_TAKEN = "error: an account with this email already exists; nothing was changed"
MSG_JOIN_ADMIN = "error: --join admin is not allowed; nothing was created"
MSG_JOIN_UNKNOWN = "error: unknown or disabled app; nothing was created"
MSG_JOIN_CLOSED = "error: the app does not accept joins; nothing was created"
MSG_JOIN_APPROVAL = "error: the app requires approval to join; nothing was created"
MSG_JOIN_UNAVAILABLE = "error: the app has no usable default role; nothing was created"
MSG_CREATE_UNEXPECTED = "error: unexpected failure; the account was not confirmed"

OPENAPI_USAGE = "usage: python -m app.cli openapi [--output <path>]"
MSG_OPENAPI_FAILED = "error: could not write the OpenAPI document"

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


def _normalize_email(value: str) -> str:
    from pydantic import EmailStr, TypeAdapter, ValidationError

    try:
        # Same normalization as sign-up (app/modules/auth/service.py).
        return str(TypeAdapter(EmailStr).validate_python(value)).strip().lower()
    except ValidationError:
        raise _UsageError from None


def _parse_email(argv: Sequence[str]) -> str:
    parser = _Parser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    grant = commands.add_parser("grant-super-admin")
    grant.add_argument("--email", required=True)
    args = parser.parse_args(argv)
    return _normalize_email(args.email)


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
    tty: "_Tty | None" = None,
) -> int:
    if argv[:1] == ["openapi"]:
        return _openapi(argv[1:])
    if argv[:1] == ["create-account"]:
        return await _create_account(
            argv[1:], settings=settings, session_scope=session_scope, tty=tty or _SystemTty()
        )
    if argv[:1] == ["register-app"]:
        return await _register_app(argv[1:], settings=settings, session_scope=session_scope)
    if argv[:1] == ["set-admin-origins"]:
        return await _set_admin_origins(argv[1:], settings=settings, session_scope=session_scope)
    if argv[:1] == ["cleanup"]:
        return await _cleanup(argv[1:], settings=settings, session_scope=session_scope)
    if argv[:1] == ["audit-purge"]:
        return await _audit_purge(argv[1:], settings=settings, session_scope=session_scope)
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


class _Tty(Protocol):
    def isatty(self) -> bool: ...
    def read_line(self, prompt: str) -> str: ...
    def read_secret(self, prompt: str) -> str: ...


class _SystemTty:
    def isatty(self) -> bool:
        return sys.stdin.isatty()

    def read_line(self, prompt: str) -> str:
        print(prompt, end="", file=sys.stderr, flush=True)
        line = sys.stdin.readline()
        if not line:  # EOF
            raise _Refused(MSG_ABORTED, EXIT_FAILURE)
        return line.rstrip("\r\n")

    def read_secret(self, prompt: str) -> str:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                return getpass.getpass(prompt)
        except getpass.GetPassWarning:  # echo could not be disabled
            raise _Refused(MSG_NO_TTY, EXIT_FAILURE) from None
        except (EOFError, KeyboardInterrupt):
            raise _Refused(MSG_ABORTED, EXIT_FAILURE) from None


_PASSWORD_REASONS = {"too_short", "too_common", "too_long"}


def _parse_create(argv: Sequence[str]) -> tuple[str, str | None]:
    parser = _Parser(prog="python -m app.cli create-account", allow_abbrev=False, add_help=False)
    parser.add_argument("--email", required=True)
    parser.add_argument("--join", action="append", default=[])
    args = parser.parse_args(argv)
    if len(args.join) > 1:
        raise _UsageError
    return _normalize_email(args.email), (args.join[0] if args.join else None)


async def _create_account(
    argv: Sequence[str],
    *,
    settings: "Settings | None",
    session_scope: "SessionScope | None",
    tty: _Tty,
) -> int:
    try:
        email, join = _parse_create(argv)
        if join == "admin":
            raise _Refused(MSG_JOIN_ADMIN, EXIT_FAILURE)

        from app.core.text import mask_email
        from app.modules.auth.errors import (
            EmailTakenError,
            OriginNotAllowedError,
            WeakPasswordError,
        )
        from app.modules.auth.passwords import PasswordHasher, PasswordPolicy
        from app.modules.auth.provisioning import AccountProvisioningService, vet_and_hash_password
        from app.modules.membership.errors import (
            JoinApprovalUnavailableError,
            JoinClosedError,
            JoinUnavailableError,
        )

        settings = _load_settings(settings)
        if not tty.isatty():
            raise _Refused(MSG_NO_TTY, EXIT_FAILURE)
        try:
            retyped = _normalize_email(tty.read_line("retype email: "))
        except _UsageError:
            raise _Refused(MSG_EMAIL_MISMATCH, EXIT_FAILURE) from None
        if retyped != email:
            raise _Refused(MSG_EMAIL_MISMATCH, EXIT_FAILURE)
        password = tty.read_secret("password: ")
        if password != tty.read_secret("repeat password: "):
            raise _Refused(MSG_PASSWORD_MISMATCH, EXIT_FAILURE)
        try:
            # Hash before the transaction: the app row lock is never held during Argon2.
            password_hash = await vet_and_hash_password(
                password,
                policy=PasswordPolicy(settings.password_min_length),
                hasher=PasswordHasher(settings),
            )
        except WeakPasswordError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            reasons = ", ".join(r for r in details.get("reasons", []) if r in _PASSWORD_REASONS)
            raise _Refused(MSG_WEAK_PASSWORD.format(reasons=reasons), EXIT_FAILURE) from None
        finally:
            del password

        scope = session_scope or (lambda: _cli_session_scope(settings))
        try:
            async with scope() as session:
                result = await AccountProvisioningService(session).create_verified(
                    email, password_hash, join
                )
        except EmailTakenError:
            raise _Refused(MSG_EMAIL_TAKEN, EXIT_FAILURE) from None
        except JoinClosedError:
            raise _Refused(MSG_JOIN_CLOSED, EXIT_FAILURE) from None
        except JoinApprovalUnavailableError:
            raise _Refused(MSG_JOIN_APPROVAL, EXIT_FAILURE) from None
        except JoinUnavailableError:
            raise _Refused(MSG_JOIN_UNAVAILABLE, EXIT_FAILURE) from None
        except OriginNotAllowedError:  # unknown or disabled app
            raise _Refused(MSG_JOIN_UNKNOWN, EXIT_FAILURE) from None

        print("created account")  # only after the scope committed
        print(f"user_id: {result.user_id}")
        print(f"email: {mask_email(result.email)}")
        print(f"joined: {result.joined or '-'}")
        return EXIT_OK
    except _UsageError:
        print(CREATE_USAGE, file=sys.stderr)
        return EXIT_USAGE
    except _Refused as refused:
        print(refused.message, file=sys.stderr)
        return refused.code
    except _Fail as fail:
        print(fail.message, file=sys.stderr)
        return EXIT_FAILURE
    except Exception:
        # Never str(exc): DB errors carry parameters (email, hash) and the DSN.
        print(MSG_CREATE_UNEXPECTED, file=sys.stderr)
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
    "reserved_scope": (MSG_RESERVED_SCOPE, EXIT_USAGE),
    "scope_conflict": (MSG_SCOPE_CONFLICT, EXIT_USAGE),
    "org_mode_required": (MSG_ORG_MODE_REQUIRED, EXIT_USAGE),
    "scope_mismatch": (MSG_SCOPE_MISMATCH, EXIT_FAILURE),
    "member_is_org_owner": (MSG_MEMBER_IS_OWNER, EXIT_FAILURE),
    "origin_taken": (MSG_ORIGIN_TAKEN, EXIT_FAILURE),
    "org_mode_change": (MSG_ORG_MODE, EXIT_FAILURE),
    "app_disabled": (MSG_APP_DISABLED, EXIT_DISABLED),
}
_FIELD_MESSAGES = {
    "key": MSG_BAD_KEY,
    "name": MSG_BAD_NAME,
    "origins": MSG_BAD_ORIGIN,
    "permissions": MSG_BAD_PERMISSION,
    "org_permissions": MSG_BAD_PERMISSION,
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
    parser.add_argument("--org-permission", action="append", default=[])
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
                "org_permissions": args.org_permission,
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
        *(f"+ org permission {p}" for p in result.org_permissions_added),
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


class _AdminOriginsRollback(Exception):
    def __init__(self, result: "AdminOriginsResult") -> None:
        self.result = result


# Separate from `_REASONS`: register-only reasons (e.g. `admin_app`) never apply here.
_ADMIN_ORIGIN_REASONS = {
    "loopback_not_allowed": (MSG_LOOPBACK, EXIT_USAGE),
    "apex_not_allowed": (MSG_ADMIN_APEX, EXIT_USAGE),
    "too_many_origins": (MSG_ADMIN_TOO_MANY, EXIT_USAGE),
    "origin_taken": (MSG_ORIGIN_TAKEN, EXIT_FAILURE),
    "admin_not_seeded": (MSG_ADMIN_NOT_SEEDED, EXIT_FAILURE),
}


def _parse_set_admin_origins(argv: Sequence[str]) -> tuple["AdminOriginsSet", bool]:
    from pydantic import ValidationError

    from app.modules.rbac.schemas import AdminOriginsSet

    parser = _Parser(prog="python -m app.cli set-admin-origins", allow_abbrev=False, add_help=False)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--origin", action="append")
    target.add_argument("--clear", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        data = AdminOriginsSet(origins=args.origin or [])
    except ValidationError:
        # Never print the pydantic error: it carries `input_value`.
        raise _Refused(MSG_ADMIN_BAD_ORIGIN, EXIT_USAGE) from None
    return data, bool(args.dry_run)


def _print_admin_origins(result: "AdminOriginsResult") -> None:
    lines = [
        f"{result.outcome.value} app {result.key}",
        f"join_policy: {result.join_policy}",
        f"status: {result.status}",
        f"origins: {' '.join(result.origins) or '(none)'}",
        *(f"+ origin {o}" for o in result.origins_added),
        *(f"- origin {o}" for o in result.origins_removed),
    ]
    if result.origins_added or result.origins_removed:
        lines.append(MSG_CORS_NOTE)
    if result.outcome.value == "updated" and not result.origins:
        lines.append(MSG_ADMIN_CLOSED)
    print("\n".join(lines))


async def _set_admin_origins(
    argv: Sequence[str],
    *,
    settings: "Settings | None",
    session_scope: "SessionScope | None",
) -> int:
    try:
        data, dry_run = _parse_set_admin_origins(argv)

        from app.core.errors import AppError, ConflictError
        from app.modules.rbac.service import AppService

        settings = _load_settings(settings)
        app_env = settings.app_env
        scope = session_scope or (lambda: _cli_session_scope(settings))
        try:
            async with scope() as session:
                result = await AppService(session).set_admin_origins(data, app_env=app_env)
                if dry_run:
                    raise _AdminOriginsRollback(result)
        except _AdminOriginsRollback as rollback:
            _print_admin_origins(rollback.result)
            print(MSG_DRY_RUN)
            return EXIT_OK
        except AppError as exc:
            details = exc.details if isinstance(exc.details, list) else []
            reason = details[0].get("reason") if details and isinstance(details[0], dict) else None
            if reason in _ADMIN_ORIGIN_REASONS:
                raise _Refused(*_ADMIN_ORIGIN_REASONS[reason]) from None
            if isinstance(exc, ConflictError):
                raise _Refused(MSG_CONFLICT, EXIT_FAILURE) from None
            raise _Refused(MSG_SET_ORIGINS_UNEXPECTED, EXIT_FAILURE) from None

        _print_admin_origins(result)  # only after the scope committed
        return EXIT_OK
    except _UsageError:
        print(SET_ADMIN_ORIGINS_USAGE, file=sys.stderr)
        return EXIT_USAGE
    except _Refused as refused:
        print(refused.message, file=sys.stderr)
        return refused.code
    except _Fail as fail:
        print(fail.message, file=sys.stderr)
        return EXIT_FAILURE
    except Exception:
        # Never str(exc): DB errors carry parameters (origins) and the DSN.
        print(MSG_SET_ORIGINS_UNEXPECTED, file=sys.stderr)
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


async def _audit_purge(
    argv: Sequence[str],
    *,
    settings: "Settings | None",
    session_scope: "SessionScope | None",
) -> int:
    try:
        from app.modules.audit.purge import MAX_DAYS, MIN_DAYS, AuditPurgeService

        parser = _Parser(prog="python -m app.cli audit-purge", allow_abbrev=False, add_help=False)
        parser.add_argument("--older-than-days", required=True)
        parser.add_argument("--dry-run", action="store_true")
        args = parser.parse_args(argv)
        # ASCII digits only: int() would also take non-ASCII digits, signs and spaces.
        if re.fullmatch(r"[0-9]{1,4}", args.older_than_days) is None:
            raise _UsageError
        days = int(args.older_than_days)
        if not MIN_DAYS <= days <= MAX_DAYS:
            raise _UsageError
        dry_run = bool(args.dry_run)

        settings = _load_settings(settings)
        scope = session_scope or (lambda: _cli_cleanup_scope(settings))
        async with scope() as session:
            result = await AuditPurgeService(session).run(days, dry_run=dry_run)

        verb = "would delete" if dry_run else "deleted"
        print(
            f"audit_log: {verb} {result.deleted}"
            f" (older than {result.older_than_days} d, cutoff {result.cutoff.isoformat()})"
        )
        if dry_run:
            print(MSG_CLEANUP_DRY_RUN)
        return EXIT_OK
    except _UsageError:
        print(AUDIT_PURGE_USAGE, file=sys.stderr)
        return EXIT_USAGE
    except _Fail as fail:
        print(fail.message, file=sys.stderr)
        return EXIT_FAILURE
    except Exception:
        # Never str(exc): DB errors carry the DSN and DETAIL lines.
        print(MSG_AUDIT_PURGE_FAILED, file=sys.stderr)
        return EXIT_FAILURE


def _openapi(argv: Sequence[str]) -> int:
    try:
        parser = _Parser(prog="python -m app.cli openapi", allow_abbrev=False, add_help=False)
        parser.add_argument("--output")
        output = parser.parse_args(argv).output

        from pathlib import Path

        from app.core.config import Settings
        from app.core.openapi import render_openapi
        from app.main import create_app

        # Code defaults only: neither `.env` nor the process env may change the document.
        document = render_openapi(create_app(Settings.model_construct(app_env="local")))
        if output is None:
            sys.stdout.buffer.write(document)
            sys.stdout.buffer.flush()
        else:
            Path(output).write_bytes(document)
        return EXIT_OK
    except _UsageError:
        print(OPENAPI_USAGE, file=sys.stderr)
        return EXIT_USAGE
    except Exception:
        print(MSG_OPENAPI_FAILED, file=sys.stderr)
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
