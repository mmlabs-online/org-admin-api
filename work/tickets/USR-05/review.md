# Review: USR-05 (slice USR-05a: app credentials)

## Attempt 1 — 2026-11-17
Verdict: PASS

Scope judged: slice USR-05a only (plan.md "Acceptance criteria → verification", slice 1). The
manifest route, `authenticate`, "only accepted for its own app" at runtime and
`docs/auth-integration.md` belong to USR-05b by the plan's split.

### Checks (run by reviewer)
- `service postgresql start; env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`, exit 0:
  ruff "All checks passed!"; "272 files already formatted"; mypy "Success: no issues found in 123
  source files"; pytest "2042 passed, 1 warning in 160.36s". The flake did not occur. The numbers
  match impl-log.md.
- `alembic upgrade head && alembic check` (same env, the CI sequence): "No new upgrade operations
  detected."

### Acceptance criteria
- [x] 1. Issue: shown once in the `mmak_<16 hex>_<43>` format, only SHA-256 stored, `no-store`, not
  in the list. Evidence: `tests/test_app_credentials_api.py::test_issue_returns_secret_once_and_stores_hash`;
  `codec.py` regex `^mmak_([0-9a-f]{16})_([A-Za-z0-9_-]{43})\Z`, length capped at 128;
  `router.py` sets `Cache-Control: no-store`; codec tests in `tests/test_app_credentials_codec.py`.
- [x] 2. Issuing needs `admin:*`; a refusal is audited `denied` and no row is written. Evidence:
  `test_issue_requires_admin_wildcard` (403 `admin_wildcard_required`, one `denied` row, no
  credential); `test_issue_missing_route_permission_403`; `service.py` `issue` (`require_super` +
  `record_denied`).
- [x] 3. At most 2 active (409 `credential_limit`), rotation after a revoke. Evidence:
  `test_issue_third_active_is_409_then_rotation_works`. Serialised by `lock_app` (`FOR NO KEY UPDATE`).
- [x] 4. `admin` app 409, disabled app 409, unknown app 404. Evidence: `test_issue_admin_app_409`,
  `test_issue_disabled_app_409`, `test_issue_unknown_app_404`.
- [x] 5. List: `Page[T]`, metadata only, revoked rows listed with `active=false`. Evidence:
  `test_list_metadata_only`, `test_list_unknown_app_404`; `AppCredentialRead` has no secret or hash field.
- [x] 6. Revoke: 204, DB clock `revoked_at`, one audit row; second call is a 204 no-op; an unknown
  or foreign credential gives 404. Evidence: `test_revoke_sets_revoked_at_and_audits`,
  `test_revoke_twice_is_noop`, `test_revoke_unknown_or_foreign_credential_404`,
  `test_revoke_needs_only_apps_write`; `repository.py` `revoke` uses a conditional UPDATE with `func.now()`.
- [x] 7. Audit in the same transaction; no secret or hash in any audit row. Evidence:
  `tests/test_audit_coverage.py` (AUDITED 16 → 18, success and failure scenarios for POST and
  DELETE, the existing secret/email sweep); `test_audit_rows_never_contain_credential`.
- [x] 8. Every route is Bearer + one `require_permission`, 401 without a token. Evidence:
  `tests/test_route_auth_classes.py` has 3 `BEARER` entries and `credential_id` in `_fill`. These
  are picked up by the classification, OpenAPI, CORS-matcher and 401 sweeps (all green).
- [x] 9. Hand-written migration with a working downgrade; `alembic check` is clean. Evidence:
  `migrations/versions/20261117_f3a8d51c9e27_usr05a_app_credentials.py` (down_revision
  `d6f03e8b2a15`; the CHECK texts match `models.py`); `tests/test_app_credentials_migration.py`
  (`test_table_and_constraints`, `test_downgrade_drops_table`); the reviewer's `alembic check` run.
- [x] 10. ADR-0016, the CLAUDE.md R3/R9 wording and D25 are present and marked pending owner.
  Evidence: `docs/adr/0016-…md` ("Status: Proposed — pending owner"); the CLAUDE.md diff changes
  only the R3 and R9 rows and matches plan step 8 word for word; `docs/identity-architecture.md`
  has the §4 rows and D25 "Proposed, pending owner". These remain **owner-pending** edits. Merging
  approves them.
- [x] 11. `make check` is green (above).

### Issues
None.

### Rules R1–R11
No violations found.
- R1: the router calls only the service, and the service returns plain dataclasses (`CredentialView`
  / `IssuedCredential`), raises only `NotFoundError`/`ConflictError`/denied errors, and keeps
  SQLAlchemy in `repository.py` and `RbacRepository`.
- R2: the migration has a downgrade, the model is exported in `app/db/models.py`, and `alembic check`
  is clean.
- R3: class 1 on all 3 routes. Class 4 is only documented, with no route that accepts credentials yet.
- R4: one audit row per mutation, plus a denied row for the `admin:*` refusal.
- R5/R11: SHA-256 only, `no-store`, `__repr__` without the hash, and only `key_id` (hex) in the
  audit. No new env key.
- R6/R9: 201/204/404/409, error envelope, `Page[T]`, UUID ids, timestamptz, and the D18 `{key}`
  path exception.
- R7: every changed code or doc file is in the plan's file table. The `work/` files are workflow
  bookkeeping (backlog, the USR-05b ticket, consult notes).

### Nits (do not block)
- `test_list_metadata_only`: both credentials are inserted in one transaction, so `created_at` is
  equal, and the `>=` assertion does not really prove the newest-first order (the tie falls back to `id`).
- `test_issue_returns_secret_once_and_stores_hash`: the "raw value is in no column" check only
  inspects `key_id`, `created_by` and `created_at`. It is weak but harmless, because the schema has
  no free-text column.
- No explicit test for `DELETE …/credentials/<not-a-uuid>` → 422 (it is FastAPI's default path validation).
- `service.py` `issue` re-parses its own output to get the secret part. `new_credential` could return
  the secret directly. This is cosmetic.
- Plan AC10 says the CLAUDE.md text matches "ADR-0016 §1 word for word". The plan defines the two
  texts differently (CLAUDE.md is a summary of §1, §3 and §5), so it was checked against plan step 8
  and found consistent with the ADR.

### Follow-ups (out of scope)
- USR-05b: manifest route, `current_app_credential`/`authenticate`, `APP_CREDENTIAL` route class,
  rate-limit scope, `docs/auth-integration.md`.
- Spec §5.3 one-sentence addition (product-manager), credential `expires_at`/`label`, and a
  per-credential rate-limit bucket (from the plan).
