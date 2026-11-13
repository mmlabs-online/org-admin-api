# USR-07: Register an app in production from the CLI (first consumer: ricotdin)

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P1 |
| Areas | api, auth, security |
| Attempts | 0 |
| Created | 2026-11-12 |
| PR | — |

## Goal
An operator can register a consuming app (key, name, origins, join policy, org mode, default member role) on the production server without the flag-gated RBAC admin API, so that ricotdin can start using mmlabs Identity before ADM-002 ships.

## Spec reference
§5.3, §5.8; ADR-0001, ADR-0003; D8–D10, D17, D19; owner 2026-11-12 ("ricotdin starts using user and auth from this API").

## Acceptance criteria
- [ ] `python -m app.cli register-app --key <key> --name <name> --origin <origin> [--origin …] [--join-policy open|approval|closed] [--org-mode none|required]` (+ `make` target): creates the app with its default role `<key>:member`, or updates name/origins/policy of an existing app idempotently; the `admin` app is refused; origins validated by the D19 rules (`normalize_origin`); takes `lock_app` like every RBAC writer
- [ ] Reuses the existing RBAC service layer (no duplicate business rules); same output and error discipline as `grant-super-admin` (fixed messages, exit codes, own engine with `hide_parameters`, no tracebacks)
- [ ] Optional permission list (`--permission <app>:<res>:<act>` repeatable) so the member role is non-empty; `is_admin` permissions excluded from the member role (D9)
- [ ] Runbook entry in `docs/deploy.md` (run via the `migrate` service), with ricotdin as the worked example
- [ ] Tests: create, idempotent re-run, origin rejected, `admin` refused, permission → member role, exit codes, no leaks

## Dependencies
USR-02, USR-02f (CLI pattern).

## Notes / answers from owner
- 2026-11-12: owner wants ricotdin (FastAPI + React SPA; JWT via PyJWT) to start using mmlabs users and auth. ricotdin: `join_policy=open`. Domain/origins and `org_mode` still to confirm (ricotdin has no orgs mentioned → default `none`).
- Integration path for ricotdin, in order: USR-03c (token + JWKS) → USR-03e (CORS) → USR-04 (email verification; tokens need a verified email, D16) → USR-07 (register ricotdin) → USR-03b (join + client libs).
- 2026-11-12 (owner): ricotdin origins `https://ricotdin.mmlabs.online` and `https://ricotdin-staging.mmlabs.online`; `join_policy=open`; no existing ricotdin users are migrated (fresh start).
- 2026-11-12 (owner): **separate app keys** — `ricotdin` with origin `https://ricotdin.mmlabs.online`, and `ricotdin-staging` with origin `https://ricotdin-staging.mmlabs.online` (own audience; staging tokens are not valid on the prod backend). Both `join_policy=open`, `org_mode=none`. Register both with the CLI.
