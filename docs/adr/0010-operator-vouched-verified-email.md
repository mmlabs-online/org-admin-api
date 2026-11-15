# ADR-0010: Operator-vouched verified email (CLI `create-account`)

Status: Accepted (USR-09 plan, 2026-11-15)
Date: 2026-11-15

## Context
Bootstrapping a new app's first user is a deadlock. The verification link needs an app origin that
serves `/verify-email`, and TuDo needs `TUDO_OWNER_SUB` before its first release. D2 requires a
verified email before joining an app or getting an app token; D16 and ADR-0007 rule out any interim
"mark verified" path. The owner declined two alternatives: a localhost origin with a localhost
Turnstile hostname, and a TuDo "setup mode" (2026-11-15, "chon B").

## Decision
1. Only `python -m app.cli create-account --email <email> [--join <app_key>]` may set
   `email_verified_at` without a token, and only on the row it inserts in the same transaction.
2. It never touches an existing account, never runs over HTTP, never from the ops workflow and
   never on a non-TTY path.
3. The operator chooses the password at a TTY and types it twice (never argv, env or a workflow
   input). The email is retyped on the TTY to catch typos.
4. The optional `--join` assigns only the app's default non-admin role, under exactly the
   join-endpoint guards (shared helpers `lock_joinable_app` and `join_default_role`). There is no
   `--role`, no `--org` and no repeated `--join`. Granting `admin:super-admin` stays with
   `grant-super-admin`.
5. The trust level is root-equivalent on the DB host, the same as `grant-super-admin`.
6. The command is allowed in every `APP_ENV`.

## Amends
D16, the Context of ADR-0007, and the "never marks an email verified" wording in `docs/deploy.md`
and `app/cli.py`, which is now scoped to `grant-super-admin`.

## Accepted risks
- a) A typo verifies an address the operator does not control. The owner of that address can use
  forgot/reset and take over the account, including TuDo ownership. Mitigated by the retype, the
  runbook check and an immediate login.
- b) There is no durable audit before ADM-004 (log line only, and `--rm` drops the logs). The
  runbook requires a manual record.
- c) The email is visible in `ps`/`docker inspect` while the prompt waits.
- d) Turnstile and the rate limits do not apply.
- e) `create-account` + `grant-super-admin` yields a verified super-admin without email proof,
  accepted at DB-host trust.
- f) The password stays in process memory until exit.

## Revisit
If anyone proposes this for routine onboarding (bulk import, staff HTTP endpoints): both stay out
of scope and need their own decision.
