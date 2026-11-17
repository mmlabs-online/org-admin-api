## Attempt 1 — 2026-11-17

### Files touched
| File | Required because |
|---|---|
| `migrations/versions/20261117_d6f03e8b2a15_usr06c_permission_scope.py` (new) | `permissions.scope`, backfill, scope CHECK, owner≠default CHECK with preflight (AC3, AC6, AC10) |
| `app/modules/rbac/models.py` | `PermissionScope`, `Permission.scope`, both CHECKs |
| `app/modules/rbac/scopes.py` (new) | pure `reserved_scope` / `default_scope` |
| `app/modules/rbac/schemas.py` | `scope` on create/update/read, `AppRegister.org_permissions` |
| `app/modules/rbac/repository.py` | `set_default_role_if_null` owner guard + bool, `list_permissions(scope=)`, `role_has_org_permission` |
| `app/modules/rbac/service.py` | scope-filtered build + member-is-owner refusal, permission scope rules and flip guards, PATCH app / role PUT guards, `register` `org_permissions` |
| `app/modules/audit/diff.py` | `scope` in the permission diff (R4) |
| `app/modules/tokens/service.py` | `org_roles` claim |
| `app/modules/invitations/service.py`, `rules.py` | guard restated by scope, `roles_overlap` removed |
| `app/cli.py` | `--org-permission` and new fixed messages |
| `tests/*` (see below) | plan test list |
| `docs/adr/0015-*.md` (new), `0013`, `0014`, `0005`, `0003`, `docs/identity-architecture.md`, `docs/spec-v1.md`, `docs/consumers.md`, `docs/deploy.md` | plan step 9 |
| `work/tickets/USR-05/ticket.md` | the plan's USR-05 note (one Notes line) |

### Tests added / changed
- `tests/test_auth_token.py`: org claim test extended; `test_org_roles_excludes_app_scope_only_role`, `test_org_roles_role_in_both_scopes_in_both_claims`, `test_org_roles_excludes_other_org_role`; app-scope-only test asserts no `org_roles`.
- `tests/test_rbac_scopes.py` (new), `tests/test_rbac_default_role.py` (5 new; the FK-race fake `list_permissions` gained a `scope` kwarg), `tests/test_rbac_permissions_api.py` (scope tests; the requires-super case is split in two because a denied request rolls back the test session), `tests/test_rbac_apps_api.py`, `tests/test_rbac_roles_api.py`, `tests/test_rbac_schema.py`.
- `tests/test_permission_scope_migration.py` (new): backfill table, none apps, `role_permissions` untouched, downgrade/upgrade, preflight abort.
- `tests/test_invitations_create.py`, `tests/test_invitations_accept.py` (renamed/added guard tests, end-to-end after build), `tests/test_invitations_rules.py` (five overlap tests removed), `tests/test_cli_register_app.py` (7 new).
- Helpers: `tests/rbac_factories.py` (`scope`), `tests/invitation_factories.py` (org-level permissions marked `org`).

### Checks (actual output summary)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: ruff clean, format clean, mypy clean (116 files), `1987 passed`.
- alembic: `upgrade head`, `check` ("No new upgrade operations detected."), `downgrade -1`, `upgrade head`, `check` all clean.

### Out-of-plan files
- None. Not done from the plan: no new `Case` in `tests/test_admin_guards.py` (the org→app `admin:*` rule is covered in `test_rbac_permissions_api.py` instead; no existing test broke).

### Follow-ups (noticed, not done)
- `mmlabs-auth`: `org_roles` accessor, `require_org_permission` mapping only `org_roles`, reject a token with `org` and no `org_roles`, release before calechip relies on it, TS type.
- calechip: migration note; the next build narrows its member role.
- Refuse app-scope grants of roles holding `org` permissions on org apps.
- Retire the S1 guard once every org-mode consumer uses `org_roles`.
- Optionally expose `scope` in the role catalog.
- `mmlabs-online/deployment`: `ops-org-admin-api` input for `--org-permission`.
- Pre-existing: a user holding only org roles gets `not_member` at `/auth/token`.

### Scope self-check
- Could the diff be smaller? No: every file maps to the plan; docs are the minimum the plan lists.
- Diff size: see `git diff --shortstat` of the commits.
