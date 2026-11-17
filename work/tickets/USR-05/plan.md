Status: READY

# Plan: USR-05: Apps self-register their permission manifest. Slice 1 = USR-05a: app credentials (staff issue / list / revoke) + ADR-0016

This ticket is too big for one implement/review cycle (see `## Split proposal`). This plan covers
**slice 1 (USR-05a)** only. Slice 2 (USR-05b, the manifest `PUT`) has its design fixed below, so its
later plan is mechanical and every owner decision is collected in one place now.

## Spec summary
- Spec §5.3: "Apps are registered; each app declares namespaced permissions (`<app>:<resource>:<action>`)
  and its own roles; users get roles per app ... Every admin `/api/v1` endpoint declares the `admin:*`
  permission it needs, except self-scoped reads of the caller's own authorization".
- Spec §5.3, Membership: "a default role `<app>:member` holding all its permissions except those
  flagged `is_admin` (for `org_mode = required` apps also except org-level permissions,
  `permissions.scope = 'org'`; ADR-0015 ...). Policy and role contents are data."
- ADR-0001 §6: "**Apps declare their catalog** (permissions + default roles) through an API call or a
  manifest at deploy time. This service stores and assigns them; it does not interpret them."
  ADR-0001 "Later, additive": "client credentials for service-to-service calls, full OIDC provider".
- `identity-architecture.md` §4: "`PUT /api/v1/apps/{key}/manifest` | app credential | app pushes its
  permissions + default role | USR-05".
- D9: "Default role `<app>:member` = all app permissions except `is_admin` ones ... Policy and role
  content are data." ADR-0003 Consequences: "A new permission reaches `<app>:member` only after an
  explicit rebuild (USR-02b app creation, USR-05 manifest) or a staff edit."
- ADR-0015 §2: "Creating a non-reserved `scope = app` permission on an `org_mode = required` app also
  needs `admin:*` ...; `register-app` (system actor) is exempt." §3: the build rule "covers
  `build_default_role`, app creation, `register-app` and the USR-05 manifest."
- Ticket AC1 (this slice): "App credential issued/rotated by staff; stored hashed; only accepted for
  its own app". The "only accepted for its own app" part needs the route that accepts credentials,
  so it is verified in slice 2. Slice 1 fixes the binding: one credential belongs to exactly one
  app (`app_id` NOT NULL).
- Ticket note 2026-11-17 (ADR-0015): the manifest declares `scope` per permission (422 when missing
  on `org_mode = required` apps), goes through `_build_for_app`, and "pushing the manifest twice
  never adds a `scope = org` permission to `<app>:member`". This is slice 2; the design below
  honours it.

**Spec conflict to flag (spec wins on scope, so not blocking):** spec §5.3 does not mention app
credentials or a pushed manifest. Only ADR-0001 §6 and identity-architecture §4 do. This is an
extension ("each app declares ... permissions"), not a contradiction. A one-sentence spec addition
is the product-manager's job and goes on the owner list.

## Acceptance criteria → verification
Slice 1 (USR-05a):

| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | Staff issue an app credential. It is shown once, in the format `mmak_<16 hex>_<43 urlsafe>`. Only the SHA-256 of the secret is stored. | `tests/test_app_credentials_api.py::test_issue_returns_secret_once_and_stores_hash`. Checks: 201; `Cache-Control: no-store`; the DB row's `secret_hash == sha256(secret)`; the raw value is in no column; the list endpoint never returns it. |
| 2 | Issuing needs `admin:*` on top of the route permission `admin:apps:write`. A refusal is audited as `denied`. | `test_issue_requires_admin_wildcard`: `admin:apps:write` alone → 403 `admin_wildcard_required`, one `app_credential.issued` row with `outcome=denied`, no credential row. |
| 3 | Rotation: at most 2 active credentials per app. Issuing a 3rd → 409 `credential_limit`. After one is revoked, a new one can be issued. | `test_issue_third_active_is_409_then_rotation_works` |
| 4 | Issuing is refused for the `admin` app (409 `admin_app`), for a disabled app (409 `app_disabled`) and for an unknown app (404). | `test_issue_admin_app_409`, `test_issue_disabled_app_409`, `test_issue_unknown_app_404` |
| 5 | List returns metadata only (`Page[T]`): no secret, no hash. Revoked credentials stay listed with `active=false`. | `test_list_metadata_only`, `test_list_unknown_app_404` |
| 6 | Revoke (DELETE) → 204. It sets `revoked_at` (database clock) and writes one audit row. Revoking again → 204 with no new row. A credential that is unknown or belongs to another app → 404. | `test_revoke_sets_revoked_at_and_audits`, `test_revoke_twice_is_noop`, `test_revoke_unknown_or_foreign_credential_404` |
| 7 | Every mutating action writes an audit row in the same transaction (R4). No secret or hash appears in any audit row (R11). | `tests/test_audit_coverage.py` (AUDITED gains 2 routes, count 16 → 18, `test_no_secret_or_email_in_any_row` covers them); `test_audit_rows_never_contain_credential` |
| 8 | Each new route is in exactly one auth class (R3): Bearer + one `require_permission`. Without a token → 401. | `tests/test_route_auth_classes.py`: the 3 new `BEARER` entries; the runtime 401 sweep picks them up |
| 9 | Hand-written migration with a working downgrade; `alembic check` is clean (R2) | `tests/test_app_credentials_migration.py` (upgrade, CHECKs, unique, cascade, downgrade); `make check` (includes `alembic check` in CI) |
| 10 | ADR-0016 + CLAUDE.md R3/R9 wording + D25 exist, marked pending owner approval (approve by merging) | Files present. The CLAUDE.md wording matches ADR-0016 §1 word for word. |
| 11 | `make check` is green | `make check` |

Slice 2 (USR-05b) criteria, **not** verified in this slice: AC1 "only accepted for its own app", AC2,
AC3, AC4, AC5 (`docs/auth-integration.md`), AC6 (all manifest tests incl. wrong-app → 403). See the
split proposal.

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/app_credentials/__init__.py` | new, empty | new domain module (CLAUDE.md layout, one folder per domain) |
| `app/modules/app_credentials/models.py` | `AppCredential` ORM model (see Data model). `__repr__` without the hash. | AC1, R2 |
| `app/modules/app_credentials/codec.py` | Pure functions, no ORM. `PREFIX = "mmak_"`. `new_credential() -> (key_id, raw)`: `key_id = secrets.token_hex(8)`, `secret = secrets.token_urlsafe(32)`, `raw = f"mmak_{key_id}_{secret}"`. `parse_credential(raw) -> (key_id, secret) \| None`: strict regex `^mmak_([0-9a-f]{16})_([A-Za-z0-9_-]{43})$`, length capped before matching. `secret_hash(secret) -> bytes`: SHA-256, the same rule as `device_token_hash`. | AC1. Slice 2 reuses `parse_credential` |
| `app/modules/app_credentials/schemas.py` | `AppCredentialRead` (`id, app, key_id, created_by, created_at, last_used_at, revoked_at, active`) and `AppCredentialIssued` (`AppCredentialRead` fields + `credential: str`) | AC1, AC5 |
| `app/modules/app_credentials/repository.py` | `add`, `count_active(app_id)`, `page(app_id, limit, offset)` (order `created_at DESC, id`), `get(app_id, credential_id)`, `revoke(credential_id) -> datetime \| None` (conditional `UPDATE ... SET revoked_at = now() WHERE revoked_at IS NULL RETURNING revoked_at`) | R1 (SQLAlchemy only here) |
| `app/modules/app_credentials/service.py` | `AppCredentialService.issue / list / revoke`. Takes the app lock through `RbacRepository.lock_app`, uses `require_super` and `actor_user_id` from `rbac/actor.py`, and audits via `AuditLogger`. Raises `AppError` subclasses only. | AC1–AC7, R1, R4 |
| `app/modules/app_credentials/router.py` | 3 Bearer routes under prefix `/apps`, `dependencies=[Depends(current_admin)]`, each with `**require_permission(...)`. POST sets `Cache-Control: no-store`. | R3 class 1, R6, R9 |
| `migrations/versions/20261117_f3a8d51c9e27_usr05a_app_credentials.py` | Create `app_credentials` (down_revision `d6f03e8b2a15`). Hand-reviewed; `downgrade()` drops the table. | R2 |
| `app/db/models.py` | import and export `AppCredential` | R2 |
| `app/main.py` | `app.include_router(app_credentials_router, prefix=settings.api_prefix)` next to `rbac_router` | routes mounted |
| `app/modules/audit/actions.py` | `APP_CREDENTIAL_ISSUED = "app_credential.issued"`, `APP_CREDENTIAL_REVOKED = "app_credential.revoked"`, `AuditTarget.APP_CREDENTIAL = "app_credential"` | R4 |
| `docs/adr/0016-app-credential-auth-class-and-manifest.md` | New ADR, `Status: Proposed — pending owner (approve by merging)`. Contents are listed under Approach step 7. | R3 has three classes; the 4th class needs an owner-approved ADR |
| `CLAUDE.md` | R3: add class (4) "app credential". R9: add the manifest exception. Exact text is under Approach step 8. **Owner-approval edit**: the reviewer checks it matches ADR-0016 §1 and changes nothing else. | Orchestrator instruction; R3/R9 must name the class before slice 2 adds the route |
| `docs/identity-architecture.md` | §4: the manifest row's auth becomes "app credential (ADR-0016)"; add a row for the staff credential endpoints. Decision log: add **D25** (Proposed, pending owner: R3 class 4 + R9 exception, ADR-0016). | Keeps the architecture doc the single API map |
| `docs/deploy.md` | New section "App credentials (USR-05a)": issue, store, rotate, revoke-on-leak, list. Uses the existing admin curl subshell (token only via `-H @"$hdr"`). The issued value goes straight into a 0600 file, never to stdout or argv. | Ticket "docs support"; staff runbook |
| `tests/app_credential_factories.py` | `make_credential(session, app, *, revoked=False, created_by=None) -> tuple[AppCredential, str]` (row, raw value) | Shared by slice 1 and slice 2 tests |
| `tests/test_app_credentials_codec.py` | Unit tests for `codec.py` | AC1 |
| `tests/test_app_credentials_api.py` | API tests (list under Tests to write) | AC1–AC7, R8 |
| `tests/test_app_credentials_migration.py` | Schema and migration tests, in the shape of `tests/test_invitations_migration.py` | R2 |
| `tests/test_route_auth_classes.py` | Add 3 `BEARER` entries. `_fill`: add `"credential_id"` to the uuid names. | R3, R8 |
| `tests/test_audit_coverage.py` | `AUDITED` += POST/DELETE credential routes, each with a success scenario (`_cred_issue`, `_cred_revoke`) and a failure scenario (unknown app → 404, unknown credential → 404). `len(AUDITED) == 18`. | R4 coverage gate |

No other file is touched. In particular: no `rbac/service.py` change, no new env key, no rate-limit
scope (those are slice 2).

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `test_app_credentials_codec.py › test_new_credential_format_and_entropy` | `mmak_` + 16 hex + `_` + 43 urlsafe chars; two calls differ |
| `› test_parse_round_trip` | `parse_credential(new_credential()[1])` returns the same key_id |
| `› test_parse_rejects_malformed` (parametrized) | missing prefix, uppercase hex, short or long key_id, secret length ≠ 43, illegal chars, trailing whitespace or newline, a JWT-shaped string, an empty string, a 10 kB string → `None` |
| `› test_secret_hash_is_sha256_32_bytes` | deterministic, 32 bytes |
| `test_app_credentials_api.py › test_issue_returns_secret_once_and_stores_hash` | AC1: 201 body (`credential`, `key_id`, `active: true`, `created_by` = actor); `Cache-Control: no-store`; DB `secret_hash == sha256(secret)`; raw value not in any column |
| `› test_issue_requires_admin_wildcard` | AC2: 403 `admin_wildcard_required`, one `denied` row, no credential row |
| `› test_issue_missing_route_permission_403` | `admin:apps:read` only → 403 `permission_denied`, `details.required == "admin:apps:write"` |
| `› test_issue_unknown_app_404` / `› test_issue_admin_app_409` / `› test_issue_disabled_app_409` | AC4: codes and `details[0].reason` (`admin_app`, `app_disabled`) |
| `› test_issue_third_active_is_409_then_rotation_works` | AC3: 409 `credential_limit`; revoke one, then issue → 201 |
| `› test_list_metadata_only` | AC5: `Page` shape; no `credential`, `secret_hash` or `hash` key anywhere in the JSON; revoked row has `active: false`; order newest first |
| `› test_list_unknown_app_404` | 404 |
| `› test_revoke_sets_revoked_at_and_audits` | AC6: 204; `revoked_at` set; one `app_credential.revoked` row with target `app_credential` / id |
| `› test_revoke_twice_is_noop` | AC6: second call 204, `revoked_at` unchanged, no new row |
| `› test_revoke_unknown_or_foreign_credential_404` | AC6: random id → 404; another app's credential id on this app's path → 404 |
| `› test_revoke_needs_only_apps_write` | `admin:apps:write` without `admin:*` → 204 (revoking only tightens) |
| `› test_audit_rows_never_contain_credential` | AC7/R11: the raw value, the secret part and the hash hex appear in no `audit_log` column |
| `› test_app_delete_cascades_credentials` | deleting the app removes its credentials (FK cascade) |
| `test_app_credentials_migration.py › test_table_and_constraints` | CHECK `key_id` format, CHECK hash length 32, unique `key_id`, FK cascade on app, `created_by` SET NULL on user delete, partial index exists |
| `› test_downgrade_drops_table` | downgrade to `d6f03e8b2a15` and upgrade again |
| `test_route_auth_classes.py` (existing tests, new entries) | the 3 routes are classified Bearer with the right permission, appear in OpenAPI with `bearerAuth`, and return 401 without a token or with a cookie only |
| `test_audit_coverage.py` (existing tests, new entries) | one success row (user actor) and no row on failure for POST/DELETE |

## Approach
1. **Model and migration (R2).** `app_credentials` as in Data model. Import the model in
   `app/db/models.py`. The CHECK texts in the model must match the migration exactly (the house
   rule: Alembic does not compare CHECKs). Run `alembic check`.
2. **Codec.** Pure functions in `codec.py`. `key_id` is hex, so it can never contain the audit
   forbidden substrings (`hash|token|password|cookie|email|secret`) or `@`. Keep that property: the
   key_id is the only identifier written to logs and audit rows.
3. **Service (R1, R4).** Every mutator locks the app first: `RbacRepository(session).lock_app(key)`,
   the same lock as every RBAC writer. That serialises the 2-active limit.
   - `issue(key, *, actor, ctx)`, in this order:
     1. lock; unknown → `NotFoundError`.
     2. `require_super(actor)`. On `DENIED_ERRORS`, `audit.record_denied(APP_CREDENTIAL_ISSUED, app_key=key)` and re-raise.
     3. `admin` → `ConflictError(details=[{field: "key", reason: "admin_app"}])`.
     4. `status == disabled` → 409 `app_disabled`.
     5. `count_active >= 2` → 409 `credential_limit` (`field: "key"`).
     6. Insert with `created_by = actor_user_id(actor)`.
     7. Audit `APP_CREDENTIAL_ISSUED`, target `(APP_CREDENTIAL, id)`, `app_key`, changes `{"key_id": {"from": null, "to": key_id}}`.
     8. Return a plain dataclass (row values + raw) so the router never touches the ORM.
   - `list(key, limit, offset)`: unknown app → 404. Read-only.
   - `revoke(key, credential_id, *, actor, ctx)`: lock; unknown app or credential (`app_id` must
     match) → 404. Already revoked → return (no write, no row). Otherwise the conditional UPDATE,
     then audit `APP_CREDENTIAL_REVOKED` with `{"revoked_at": {"from": null, "to": ts}}`. No
     `admin:*`: revoking only removes access, and `admin:apps:write` can already delete the whole
     app.
   - Never log the raw value. Log lines, if any, carry `key_id` only (R5, R11).
4. **Router (R3 class 1, R6, R9).**
   - `POST /apps/{key}/credentials`: 201; `**require_permission("admin:apps:write")` plus a 409
     response description, as `_with_409` does; `response.headers["Cache-Control"] = "no-store"`.
   - `GET /apps/{key}/credentials`: `Page[AppCredentialRead]`, `admin:apps:read`, `PageDep`.
   - `DELETE /apps/{key}/credentials/{credential_id}`: 204, `admin:apps:write`.
   - No body on POST. The CORS admin scope applies automatically (the routes have `current_admin`).
5. **Mount** the router in `create_app` with the API prefix.
6. **Tests** as listed. Update `BEARER`, `_fill` and `AUDITED`.
7. **ADR-0016** (`docs/adr/0016-app-credential-auth-class-and-manifest.md`), `Status: Proposed —
   pending owner (approve by merging USR-05a)`. Sections:
   - §1 **Auth class 4, "app credential"**. Allowed on exactly one route,
     `PUT /api/v1/apps/{key}/manifest` (added in USR-05b), and listed by exact (method, path) in
     `APP_CREDENTIAL` in `tests/test_route_auth_classes.py`. Never accepted on any other route; an
     admin JWT is never accepted on that route. No CORS (server-to-server; the route is outside
     `/api/v1/auth/` and has no `current_admin`). Rate-limited per IP before authentication.
   - §2 **Type:** opaque per-app API key, not OAuth client credentials. Client credentials would add
     a token endpoint, a second JWT audience and machine clients, which moves toward the R10 / ADR-0001
     "later" OIDC provider, for one deploy-time call.
   - §3 **Format and storage:**
     - format `mmak_<key_id>_<secret>`: `key_id` is 16 hex (public, used for lookup), the secret is
       256 bits;
     - only SHA-256(secret) is stored, compared with `hmac.compare_digest`;
     - no pepper: the house pattern for 256-bit secrets (`device_token_hash`), and no new env
       secret;
     - at most 2 active per app (rotation overlap); no expiry in v1;
     - revocation is immediate (every request reads the DB); disabling the app blocks it;
       deleting the app cascades.
   - §4 **Transport:** `Authorization: Bearer mmak_…` only, never query, body or cookie. One 401
     answer (`unauthorized`, `WWW-Authenticate: Bearer realm="app"`, no details) for missing,
     malformed, unknown, wrong secret or revoked. A valid credential for another app → 403
     `app_credential_mismatch`, audited `denied`. App disabled → 403 `app_disabled`.
   - §5 **Issuance:** Bearer `admin:apps:write` + `admin:*` (a long-lived external secret that
     writes the catalog). Revoke: `admin:apps:write`. Both audited. Refused for `admin` and for
     disabled apps.
   - §6 **Manifest actor and write policy** (applies in USR-05b), restated from the split proposal:
     - `SystemActor(f"app:{key}/{key_id}", privileged=False)`, `actor_kind = system`, so no
       audit-schema migration;
     - create-only carve-out for new `is_admin` permissions (inert);
     - no `is_admin` or `scope` flips;
     - new non-reserved `scope = app` permissions on org apps are refused (ADR-0015 §2 unchanged);
     - extra roles never hold `is_admin` permissions and never touch system, default or owner
       roles;
     - never touches app fields;
     - member rebuild as in the split proposal.
   - §7 **R9 exception:** `PUT /api/v1/apps/{key}/manifest`, a singular noun and a single document
     (like D18/D23).
   - Rejected alternatives: client credentials; `X-App-Key` header (proxies and APM redact
     `Authorization`, not custom headers); HMAC pepper; Argon2 (DoS amplifier for a high-entropy
     secret); a new `actor_kind = 'app'` (needs a CHECK rewrite migration; the label already
     identifies the app and key).
   - Consequences / residual risk: a leaked key can add non-admin permissions that reach every
     member, add inert admin permissions, and add permissions to manifest-managed extra roles. It
     cannot flip flags, touch other apps or `admin`, grant roles, or delete anything. Mitigations:
     `admin:*` to issue, at most 2 keys, `last_used_at`, full audit, immediate revoke.
8. **CLAUDE.md** (owner-approval edit, nothing else changes).
   - R3: before "No refresh tokens.", insert: "(4) **app credential** (D25, ADR-0016) — only
     `PUT /api/v1/apps/{key}/manifest`: `Authorization: Bearer mmak_<key_id>_<secret>`, an opaque
     per-app key issued by staff (`admin:*`), stored only as a SHA-256 hash, accepted only for its
     own app (another app → 403) and on no other route; no CORS; per-IP rate-limited."
   - R9: append to the exception list: "`PUT /api/v1/apps/{key}/manifest` (singular `manifest`, a
     single document; D25)."
   - If the implementer's tooling refuses to edit `CLAUDE.md`, put the exact text in ADR-0016 under
     "Proposed CLAUDE.md wording" and record it in `impl-log.md`. The owner applies it on merge.
9. **identity-architecture.md**: the §4 row and D25, as in the file table.
10. **deploy.md** runbook "App credentials (USR-05a)":
    - Issue: inside the ADM-002 curl subshell,
      `curl -fsS -X POST -H @"$hdr" "$api/apps/<key>/credentials" | python3 -c '…print(json.load(sys.stdin)["credential"])' > "$cred"`,
      where `$cred` is a mktemp 0600 file. Then copy it into a GitHub **Environment** secret of the
      deployment repo (environment limited to `main`, required reviewer) and delete the file.
    - Rotation: issue the second key, update the secret, deploy once, revoke the old key (`GET` to
      find its id by `key_id`).
    - Leak: revoke at once (`DELETE`), then issue a new one.
    - Never paste it in argv, logs or an issue. `key_id` is safe to share.
    - Rules: at most 2 active; `admin:*` to issue.
11. `make check` green (R8).

Rules: R1 (router → service → repository; the service raises `AppError` only); R2 (step 1); R3
(class 1 for all three routes; class 4 only documented here); R4 (one row per mutation in the same
transaction; denied rows through `record_denied`); R5/R11 (hash only, `no-store`, no secret in
logs, audit or errors); R6 (201/204/404/409/403/401, envelope); R7 (manifest work stays in slice
2); R8 (tests above); R9 (`/apps/{key}/credentials` plural noun under the D18 key exception,
`Page[T]`, UUID ids, `timestamptz`); R10 (not an OIDC provider, no client-credentials grant).

## Specialist input used
- **Credential type = opaque API key** (all three consults agree): adopted.
- **Transport `Authorization: Bearer mmak_…`** (identity, security): adopted. **`X-App-Key` header**
  (api-platform): rejected. Its concern was that the Bearer machinery conflates the two, but
  `is_bearer_dependant` / `BearerRouteMatcher` key on `current_admin`, not on the header, so no
  conflation happens. Meanwhile proxies and APM redact `Authorization` and not custom headers.
- **Format** `mmak_<key_id>_<secret>` (identity, security) over `mmk_<id>.<secret>` (api-platform):
  adopted, two-of-three. `key_id` is hex (my choice): it is safe in audit rows and logs (no
  forbidden substrings) and simpler to validate.
- **SHA-256, not Argon2** (all): adopted. **HMAC pepper `APP_CREDENTIAL_HMAC_KEY`** (security,
  optional): rejected, because it adds an env secret with little gain for a 256-bit random secret.
  It matches `device_token_hash`.
- **Max 2 active for rotation** (all): adopted. **`expires_at`** (security, optional): deferred to
  follow-ups.
- **Issuance permission:** api-platform proposed `admin:apps:write` only. Identity and security
  proposed `admin:*`. Adopted `admin:*` (orchestrator: fail closed). Revoke stays on
  `admin:apps:write` (my choice: it only tightens).
- **Refuse `admin` app (409 `admin_app`) and disabled app (409 `app_disabled`) at issuance**
  (identity): adopted.
- **Audit actor:** identity proposed `SystemActor` with a label and no migration. Security preferred
  `actor_kind = 'app'` with a migration. Adopted the `SystemActor` label (orchestrator: no
  `actor_kind` migration unless necessary; the CHECK already requires a label for system actors;
  the label is at most 53 chars against the 64 limit).
- **Manifest write policy** (slice 2): identity's "no flips, 409 mismatch" and security's
  "never apply flips" both adopted, as 409 so the deploy fails loudly. Api-platform's "allow
  `is_admin` true→false, audited": rejected (escalation path). The member rebuild follows identity
  and security: register parity, rebuild only when a permission was created. Api-platform's
  rebuild on every push: rejected (undoes staff tightening on every deploy). Member override
  intersected with the eligible set (identity): adopted. Extra roles additive-only and never
  holding `is_admin` (identity + security): adopted. New `scope = app` on org apps:
  identity Q1 (allow), security Q1 option (c) (create as `org` and report), and the orchestrator's
  fail-closed default. Adopted **refuse with 409 `requires_staff`**. Option (c) was rejected
  because the next push would then hit `scope_mismatch` and break idempotency.
- **ETag** (all three): no catalog code change. The AC is interpreted as "changes when the catalog
  content changes". Adopted for slice 2.
- **Route-class wiring** (api-platform B, security R8): adopted for slice 2 (`APP_CREDENTIAL`
  allow-list, OpenAPI scheme `appCredential`, CORS pin test, runtime sweep).
- **`mmlabs-auth` `push_manifest` helper** (api-platform F): a follow-up (separate repo, USR-08/D21),
  per the orchestrator's R7 instruction.
- **Body cap 413** (api-platform, security): rejected for now. Pydantic list caps bound the work;
  a global body-size limit is a follow-up.
- **Audit `auth_failed` rows for a known key_id** (security R7): rejected. Admin auth logs 401s and
  does not audit them; slice 2 logs `app_credential_denied reason=… key_id=…`. Only the wrong-app
  403 is audited (`denied`).

## Assumptions
- Slice 1 is to be followed by a USR-05b ticket created by the orchestrator, with the design in the
  split proposal.
- `created_by` is `ON DELETE SET NULL` (users can be deleted; the audit row keeps the actor).
- The list endpoint includes revoked credentials (history; `active` flag). No filter parameter in v1.
- Revoking an already-revoked credential is a 204 no-op, not a 404 (idempotent DELETE, like other
  "already in the target state" no-ops here).
- Issuing has no request body (no label field). A `label` column is a follow-up if the owner wants
  one.
- Concurrency of two simultaneous issues is covered by the `lock_app` design (ADR-0003 §10); there
  is no dedicated race test.
- Credentials of a disabled app stay stored. Slice 2 refuses them with 403 while the app is
  disabled.
- Date in the migration filename: 20261117.

## Needs owner's decision (non-blocking; defaults applied, approve by merging)
1. **New R3 auth class 4 "app credential" + R9 exception** for `PUT /api/v1/apps/{key}/manifest`:
   ADR-0016, CLAUDE.md R3/R9 wording, D25. *Default:* as written. The reviewer must treat these
   edits as owner-pending, not as approved.
2. **Issuing a credential needs `admin:*`**; revoke needs only `admin:apps:write`.
3. **Spec §5.3 gap:** add one sentence ("apps push their permission manifest at deploy time with a
   per-app credential, ADR-0016") via product-manager / `/idea`. Not done here (planner and
   implementer don't edit the spec).
4. (Slice 2) **AC4 interpretation:** the catalog `ETag` changes when catalog-visible content
   changes (a role added, or a permission added to or removed from a role). A push that only adds
   an `is_admin` or `scope = org` permission, or only changes a description, leaves it unchanged by
   design (ADR-0003 §11: the catalog carries no `is_admin` or descriptions).
5. (Slice 2) **AC3 interpretation:** without an override, `<app>:member` is rebuilt through
   `_build_for_app` **only when the push created at least one permission** (`register-app`
   parity). That rebuild is the full D9 "all non-`is_admin`" set (on org apps also `scope = app`),
   so it **re-adds non-admin permissions staff removed from member by hand**. The response lists
   those keys (`member.added`) and the audit row records them. *Alternative:* an additive-only
   rebuild (only the newly created keys are added), which preserves tightening but deviates from
   the AC wording.
6. (Slice 2) **Member override** (`"member": {"permissions": [...]}`): applied on every push where
   member ≠ target, so the app owns member content once it opts in. It can only narrow: the target
   is intersected with the eligible set, and an ineligible key → 422.
7. (Slice 2) **New non-reserved `scope = app` permissions on `org_mode = required` apps are refused**
   (409 `requires_staff`; staff create them with `admin:*`, after which the manifest matches).
   ADR-0015 §2 is unchanged. *Alternative:* allow it, like `register-app`, which needs an ADR-0015
   amendment.
8. (Slice 2) **The manifest may create new `is_admin` permissions** (create-only, inert: never in
   member, never in manifest roles, granting needs `admin:*`). It never flips `is_admin` or `scope`
   (409 `is_admin_mismatch` / `scope_mismatch`).
9. (Slice 2) **Extra roles:** created if absent; on existing roles, permissions are only added and
   the `name` is never changed (only set at creation); they never hold `is_admin` permissions;
   system, default and org-owner roles are refused (409 `role_not_manifest_managed`).
10. (Slice 2) A disabled app → 403 `app_disabled`; a wrong-app credential → 403
    `app_credential_mismatch`, audited `denied`.
11. **No credential expiry and no HMAC pepper in v1** (follow-ups if wanted).

## Blocking questions
None.

## Split proposal
Too big for one cycle: about 1,000 lines of production code and docs plus about 750 lines of tests
in total, and it carries two risks that should be reviewed separately (a new auth class with a
standing secret, and catalog-writing sync logic).

- **Slice 1 = USR-05a (this plan):** the `app_credentials` table, staff issue/list/revoke (Bearer),
  ADR-0016, CLAUDE.md R3/R9, D25, the deploy.md runbook. The owner approves the new auth class by
  merging it **before** any route accepts credentials.
- **Slice 2 = USR-05b (next ticket, depends on USR-05a merged):** `PUT /api/v1/apps/{key}/manifest`.
  Design fixed here; its plan should follow it.
  - **Files.**
    - Create:
      - `app/modules/app_credentials/deps.py`: `current_app_credential`, an `HTTPBearer(scheme_name="appCredential", bearerFormat="mmak", auto_error=False)` dependency calling `AppCredentialService.authenticate(raw, path_key, ctx)`;
      - `app/modules/rbac/manifest.py`: `ManifestService.sync`;
      - `app/modules/rbac/manifest_router.py`: tag `app-manifest`, no `current_admin`;
      - `docs/auth-integration.md`;
      - `tests/test_app_manifest_api.py`;
      - `tests/test_app_credential_auth.py`.
    - Modify:
      - `app/modules/app_credentials/{service,repository,codec}.py`: `authenticate`, `touch_last_used`;
      - `app/modules/rbac/schemas.py`: `ManifestPut`, `ManifestResult`;
      - `app/modules/rbac/service.py`: an `only: set[str] | None` kwarg on `_build_for_app` (intersect with the eligible set); an `admin_create_allowed: bool = False` kwarg on `PermissionService._create` (skips `require_super` only for `is_admin` on non-`admin` apps; the org-app `scope = app` guard stays);
      - `app/modules/rbac/repository.py`: `roles_holding(app_id, perm_ids)`;
      - `app/core/rate_limit.py` + `app/core/config.py` + `.env.example`: scope `app_manifest`, `APP_MANIFEST_RATE_LIMIT` = 30 / `APP_MANIFEST_RATE_WINDOW_SECONDS` = 60;
      - `app/modules/audit/actions.py`: `APP_MANIFEST_SYNCED = "app.manifest_synced"`;
      - `app/main.py`: mount the manifest router;
      - `tests/test_route_auth_classes.py`;
      - `tests/test_audit_coverage.py`: classify the route;
      - `docs/consumers.md` (link), `docs/deploy.md` (rate-limit keys), `docs/identity-architecture.md` §7 (link).
  - **Auth** (ADR-0016 §4).
    - Order: rate limit → parse → lookup by `key_id` → `compare_digest` → not revoked → app `active`
      (else 403 `app_disabled`) → path `{key}` == credential's app (else 403
      `app_credential_mismatch`, `record_failure` with `outcome=denied` and actor label
      `app:<key>/<key_id>`). Resolve the credential first, so the response never reveals whether the
      path app exists.
    - Bad credential or invalid schema → 401/403 before 422.
    - `last_used_at = now()` on every authenticated push.
  - **Request** (`extra="forbid"`):
    `{"permissions": [{"key", "description"?, "is_admin"?: false, "scope"?}] (≤200),
    "roles"?: [{"key", "name", "permissions": [key] (≤200)}] (≤50),
    "member"?: {"permissions": [key]}}`.
    The display-text validators (`Description`, `RoleName`) and `PERMISSION_KEY_RE` / `ROLE_KEY_RE`
    are reused.
  - **422 before any write** (`details[{field, reason, key?}]`):
    - `prefix_mismatch` (exact first segment, as in `register`);
    - `wildcard_not_allowed`;
    - `duplicate_key`;
    - `reserved_scope` (`_violates_reserved`);
    - `scope_required` (org app, non-reserved key with no scope);
    - `org_mode_required` (`scope = org` on a non-org app);
    - `undeclared_permission` (a role or member references a key not in this manifest);
    - `admin_permission_in_role`;
    - `ineligible_member_permission` (`is_admin`, or `scope = org` on org apps);
    - `reserved_role` (`<app>:member` in `roles`).
  - **409 under `lock_app`, before any write:**
    - `is_admin_mismatch` / `scope_mismatch` (stored ≠ declared);
    - `requires_staff` (a new non-reserved `scope = app` permission on an org app);
    - `role_not_manifest_managed` (`is_system`, default or owner role);
    - `member_is_org_owner` (from `_build_for_app`).
  - **Apply** (one transaction, lock first):
    1. Create new permissions via `PermissionService._create(..., actor=manifest_actor, admin_create_allowed=True)`.
    2. Update changed descriptions.
    3. Create absent extra roles; add the missing declared permissions to existing ones through
       `_set_role_permissions` with target = current ∪ declared (never remove).
    4. Member: with an override, `_build_for_app(app, set_default=False, only=declared)` when member ≠
       target. Without one, `_build_for_app(app, set_default=False)` only if step 1 created
       something.
    5. Never touch `apps` fields or `default_role_id`.
  - **Response 200** (sorted, deterministic):
    `{"app", "changed", "permissions": {"added", "description_updated", "not_in_manifest": [{"key", "held_by_roles"}]}, "roles": {"added", "updated"}, "member": {"role_key", "rebuilt", "added", "removed"}}`.
    Undeclared permissions are never deleted and never stripped from roles.
  - **Audit:** one `app.manifest_synced` row only when `changed` (key lists only). Idempotent:
    an identical re-push → `changed: false`, no catalog write, no row, ETag unchanged.
  - **Route classes:**
    - `APP_CREDENTIAL = {("PUT", "/api/v1/apps/{key}/manifest")}` with an exactness test.
    - `test_every_route_is_classified` and `test_bearer_route_permission_map` subtract it.
    - The stale-entry test and the "no bearer guard" test include it.
    - OpenAPI: security `[{"appCredential": []}]`, 401/403 documented, no `bearerAuth`, no `x-permission`.
    - `BearerRouteMatcher` is False for it, and a preflight or `PUT` with a registered `Origin`
      gets no `Access-Control-Allow-Origin`.
    - Runtime sweep: no credential → 401 with `realm="app"`.
    - An admin JWT on the manifest → 401; a credential on a Bearer route → 401.
  - **Tests (AC6 + ticket note):**
    - namespace 422 for permissions, roles and member;
    - idempotency (second push `changed: false`, no row);
    - `is_admin` excluded from member;
    - org app: two pushes never put a `scope = org` permission in member, and `scope_required`;
    - override narrows, never widens;
    - removed permissions reported and still held;
    - ETag changes on a member-reaching addition and on a new role; unchanged on re-push (304),
      on an `is_admin`-only addition and on a description-only change;
    - wrong-app credential → 403 (+ denied row);
    - revoked or unknown → 401; disabled app → 403; flips → 409; `requires_staff` → 409;
    - rate limit → 429.
  - **Docs:** `docs/auth-integration.md` covers:
    - the manifest format, with an org-app example;
    - the rules table;
    - a deploy-step `curl` reading the key from env (never argv), a push from the deploy job only
      (never on `pull_request` / `pull_request_target`), and failing the deploy on non-200;
    - pushing before rolling out the new app version;
    - the member-rebuild warning and the override;
    - `not_in_manifest` cleanup by staff;
    - rotation;
    - the ETag.
- **Follow-ups (not in either slice):**
  - an `mmlabs-auth` `push_manifest` helper (USR-08 repo);
  - credential `expires_at` and `label`;
  - a global request body-size limit;
  - a per-credential rate-limit bucket;
  - the deployment-repo workflow step that pushes the manifest;
  - the `actor_kind` filter value `app` if the owner wants machine rows separated.

## Data model / migration
Table **`app_credentials`** (revision `f3a8d51c9e27`, down_revision `d6f03e8b2a15`):

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | uuid | no | `UUIDPkMixin` | PK `pk_app_credentials` |
| `app_id` | uuid | no | | FK `apps.id` `ON DELETE CASCADE` |
| `key_id` | text | no | | `uq_app_credentials_key_id`; CHECK `ck_app_credentials_key_id_format`: `key_id ~ '^[0-9a-f]{16}$'` |
| `secret_hash` | bytea | no | | CHECK `ck_app_credentials_secret_hash_length`: `octet_length(secret_hash) = 32` |
| `created_by` | uuid | yes | | FK `users.id` `ON DELETE SET NULL` |
| `created_at` | timestamptz | no | `now()` | |
| `last_used_at` | timestamptz | yes | | written by USR-05b |
| `revoked_at` | timestamptz | yes | | NULL = active |

- Index `ix_app_credentials_app_id_active` on (`app_id`) `WHERE revoked_at IS NULL` (the active
  count and list). Index `ix_app_credentials_app_id` on (`app_id`, `created_at`) for the paged
  list (or one composite; the implementer picks one and records it).
- Constraint names follow the project's naming convention (`app/db/base.py`). The model's
  `CheckConstraint` texts match the migration exactly.
- No change to `apps`, `permissions`, `roles` or `audit_log`. No backfill.
- `downgrade()`: drop the indexes and table `app_credentials` (destroys issued credentials; apps
  then need a new one after re-upgrade, which is acceptable before USR-05b is in use). Expand-only,
  so the previous image runs on the new schema (deploy.md migration rules).

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| POST | `/api/v1/apps/{key}/credentials` | no body | `AppCredentialIssued` `{id, app, key_id, created_by, created_at, last_used_at: null, revoked_at: null, active: true, credential: "mmak_…"}`; header `Cache-Control: no-store` | 201; 401; 403 `permission_denied` / `admin_wildcard_required`; 404; 409 `admin_app` / `app_disabled` / `credential_limit` (in `details[0].reason`) | `admin:apps:write` + service `admin:*` |
| GET | `/api/v1/apps/{key}/credentials?limit&offset` | | `Page[AppCredentialRead]` (no `credential`, no hash), newest first | 200; 401; 403; 404; 422 (paging) | `admin:apps:read` |
| DELETE | `/api/v1/apps/{key}/credentials/{credential_id}` | | empty | 204 (also when already revoked); 401; 403; 404 (unknown app or credential, or a credential of another app); 422 (bad uuid) | `admin:apps:write` |

Slice 2 adds `PUT /api/v1/apps/{key}/manifest` (auth class 4), as in the split proposal.
