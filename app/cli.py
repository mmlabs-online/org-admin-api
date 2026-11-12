"""Operator CLI (USR-02f): `python -m app.cli grant-super-admin --email <email>`.

Never imported by `app.main`; not reachable over HTTP. Creates no users, takes no password, never
marks an email verified (D16). Output never contains the full email, a traceback or the DSN (R11):
every failure prints one fixed line, and the CLI uses its own engine with `hide_parameters=True`.
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


async def amain(
    argv: Sequence[str],
    *,
    settings: "Settings | None" = None,
    session_scope: "SessionScope | None" = None,
) -> int:
    try:
        email = _parse_email(argv)

        from app.core.config import get_settings
        from app.core.text import mask_email
        from app.modules.rbac.service import AssignmentService, BootstrapOutcome

        if settings is None:
            try:
                settings = get_settings()
            except Exception:
                raise _Fail(MSG_CONFIG) from None
        if settings.db_echo:
            raise _Fail(MSG_DB_ECHO)

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


def main() -> NoReturn:
    sys.tracebacklimit = 0  # backstop for BaseExceptions such as KeyboardInterrupt
    for name in ("sqlalchemy", "asyncpg"):
        logger = logging.getLogger(name)
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
    sys.exit(asyncio.run(amain(sys.argv[1:])))


if __name__ == "__main__":
    main()
