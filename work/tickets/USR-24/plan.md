Status: READY

# Plan — USR-24: Optimistic concurrency on PUT role permissions

## Scope summary
- Spec §5.3 (`docs/spec-v1.md` L47): "each app declares namespaced permissions (`<app>:<resource>:<action>`) and its own roles"; L49: "Policy and role contents are data." Staff edit role contents through `PUT /api/v1/apps/{key}/roles/{role_id}/permissions` (USR-02d), and admin-pan reads them through `GET /api/v1/apps/{key}/role-definitions/{role_id}` (USR-16).
- Spec §5.1/§5.6 ADM-024 note (L39, L63), the precedent this ticket extends: "GET/PATCH (and POST 201) return a strong ETag derived from updated_at; PATCH accepts optional If-Match (strong comparison, * = any) and answers 412 precondition_failed with the current ETag when stale, changing nothing; without If-Match behaviour is unchanged. 412 joins the error codes (R6)."
- CLAUDE.md R6: "Codes: 201 create, 204 delete, 401, 403, 404, 409, 412 (stale If-Match, ADM-024), 422."
- CLAUDE.md R9: "No breaking changes inside v1." Everything here is additive: an optional request header, a response header, a new body field `updated_at` on `RoleRead`, and 412 only when the client sends `If-Match`.
- Ticket goal: "`RoleDefinitionRead.updated_at` is not bumped by a permission replace (only by rename), so clients cannot use it as a version." The current schema text confirms it (`app/modules/rbac/schemas.py:248-250`: "permission changes do not bump it").
- Ticket AC choices settled at PLAN (consistent with ADM-024; see Blocking questions Q1–Q2 for Ming's confirmation):
  - version = `roles.updated_at` (no integer column, no migration);
  - precondition = `If-Match` header with the ETag (no body `expected_version`);
  - mismatch = 412 `precondition_failed`, `details: null`, current `ETag` header (the ticket's `role_changed` reason belongs to the 409 alternative and is dropped).

## Acceptance criteria → verification
| # | Criterion (ticket) | How the reviewer verifies it |
|---|---|---|
| 1 | Every permission replace bumps a version visible on `RoleDefinitionRead` and `RoleRead`. | `test_rbac_roles_if_match.py › test_put_changing_set_bumps_updated_at` (PUT body `updated_at` and the re-GET role-definition `updated_at` are both strictly greater than before, and equal to each other); `› test_put_noop_does_not_bump`; `› test_manifest_change_bumps_role_version`; `› test_default_role_rebuild_bumps_member_version`; `› test_touch_role_strictly_increasing`. `test_rbac_roles_api.py › test_role_read_has_exact_keys` (updated: `updated_at` in all three `RoleRead` responses). |
| 2 | The PUT accepts an optional precondition. On mismatch: 412, nothing written, no audit row. | `› test_put_stale_if_match_412` (412 envelope `{"error": {"code": "precondition_failed", "message": ..., "details": null}}`, `ETag` header = current tag, permissions unchanged via `_held`, `audit_log` count unchanged, role `updated_at` unchanged); `› test_two_clients_second_gets_412`; `› test_put_weak_if_match_412`; `› test_put_malformed_if_match_412`; `› test_put_if_match_list_containing_current_200`; `› test_put_if_match_star_200`. `test_openapi_contract.py › test_role_put_412_and_if_match_declared`. |
| 3 | Without a precondition the PUT behaves as today (R9). | `› test_put_without_if_match_unchanged` plus every existing PUT test in `tests/test_rbac_roles_api.py` and `tests/test_rbac_authz.py` passing unedited (only `ROLE_READ_KEYS` changes, AC1). Diff reading: with `precondition is None` or `*` no new branch runs. |
| 4 | Compare and write are atomic. | Code reading: the compare reads `role.updated_at` after `lock_app` (`FOR NO KEY UPDATE` on the app row, `repository.py:78-90`), which every writer of `role_permissions` takes first; `get_role` uses `populate_existing=True` so the value is the post-lock row, not an identity-map copy. Test `› test_precondition_read_after_app_lock` (SQL capture: the `FOR NO KEY UPDATE` statement precedes the `SELECT ... FROM roles`, and a stale request issues no `INSERT`/`DELETE`/`UPDATE`). |
| 5 | Tests: matching version succeeds and bumps it; stale → 412, permissions unchanged, no audit row; no precondition still works. | The tests in "Tests to write" and green `make check`. |

## Ming verification after merge
None for the API itself. Optional: in admin-pan (PAN-030), open the same role in two tabs, save in tab 1, then save in tab 2 and see the 412 "re-fetch" path.

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `app/modules/rbac/repository.py` | Add `touch_role(role: Role) -> None`: `update(Role).where(Role.id == role.id).values(updated_at=bumped_updated_at(Role.updated_at)).execution_options(synchronize_session=False)`, then `await self.session.refresh(role, attribute_names=["updated_at"])`. `get_role` (L191-194): add `.execution_options(populate_existing=True)`. | AC1, AC4 (R1: SQL in the repository). | **High** (`app/modules/rbac/**`) |
| `app/modules/rbac/service.py` | `_set_role_permissions` (L187-203): call `await repo.touch_role(role)` after a non-empty diff; docstring notes "bumps `roles.updated_at` (ETag, USR-24); callers must hold `lock_app`". `RoleService._read` (L1077-1085) and `create` (L1177-1179): pass `updated_at=role.updated_at`. `update` (L1201): set `role.updated_at = bumped_updated_at(Role.updated_at)` alongside the rename (see Approach 4). `replace_permissions` (L1258-1348): new `precondition: IfMatch \| None = None` kwarg, the 412 check after `_get` (L1268), and `updated_at=role.updated_at` in the returned `RoleRead` (L1342). | AC1–AC4. | **High** |
| `app/modules/rbac/schemas.py` | `RoleRead` (L221-226): add `updated_at: datetime` with a description. `RoleDefinitionRead.updated_at` (L248-250): new description "Last change to the role row, including its permission set (USR-24). ETag source: `version_etag(updated_at)`." | AC1. The current text becomes false. | **High** |
| `app/modules/rbac/router.py` | Import `Header` and `ETAG_RESPONSE_HEADER, IF_MATCH_DESCRIPTION, parse_if_match, version_etag` from `app.core.etag`. Add a local `_with_etag(kwargs, code, description)` helper (same as `organizations/router.py:45-50`) and `_ROLE_412` text. `get_role_definition` (L282-290): `response: Response`, set `ETag`, declare it on 200. `replace_role_permissions` (L380-403): add `412: _ROLE_412` to `errors`, `ETag` on 200, `response: Response`, `if_match` header param, pass `precondition=parse_if_match(if_match)`, set `ETag` from the returned `updated_at`. | AC2, AC4 (R1: router only parses and sets headers). | **High** |
| `docs/consumers.md` | Add one bullet after the ADM-024 bullet (L95-101), text in Approach 7. | admin-pan is the consumer (PAN-030). | No |
| `docs/spec-v1.md` | Add one dated USR-24 note under §5.3 after L51, text in Approach 7. | Keeps the spec the source of truth for the new contract. | No |
| `tests/test_rbac_roles_if_match.py` | **New.** API and repository tests listed below. | AC1–AC5, R8. | No |
| `tests/test_rbac_roles_api.py` | L19 only: `ROLE_READ_KEYS` gains `"updated_at"`. | AC1 changes the `RoleRead` shape (additive). | No |
| `tests/test_app_manifest_api.py` | One test: a manifest push that adds a permission to an existing role changes that role's `updated_at`; a repeat push of the same manifest does not. | AC1 (the manifest is a writer of `role_permissions`, `manifest.py:219`). | No |
| `tests/test_openapi_contract.py` | Add `test_role_put_412_and_if_match_declared`; add `("/api/v1/apps/{key}/role-definitions/{role_id}", "get", "200")` and the PUT 200 to `test_etag_header_declared` (L430-438). `_EXPECTED_ERRORS` needs no edit: it compares only `{404, 409}` (L361). | AC2, R9 contract. | No |

No other file may be touched without a note in `impl-log.md` (R7). No `app/core/**` or `app/db/**` change under the proposed defaults: `bumped_updated_at`, `version_etag`, `parse_if_match`, `IfMatch`, `PreconditionFailedError` and the CORS `If-Match`/`ETag` allowance all exist from ADM-024. If Ming picks Q1 option (b) (keep a `role_changed` reason), `app/core/errors.py` (**High**) joins the list.

## Data model / migration
None. `roles.updated_at` (timestamptz NOT NULL, from `TimestampMixin`, `app/db/base.py:38-40`) is the version. No column, index or constraint change; R2 does not apply; `uv run alembic check` must stay clean.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/apps/{key}/role-definitions/{role_id}` | — | `RoleDefinitionRead` (unchanged shape; `updated_at` now also moves on permission changes) + header `ETag` (new) | 200, 401, 403, 404 | `admin:apps:read` |
| PUT | `/api/v1/apps/{key}/roles/{role_id}/permissions` | `RolePermissionsReplace` (unchanged); optional header `If-Match` (new) | `RoleRead` + `updated_at` (new field) + header `ETag` (new) | 200, 401, 403, 404, 409, **412 `precondition_failed` (new, `details: null`, current `ETag`)**, 422 | `admin:roles:manage`; conditional `admin:*` (unchanged) |
| POST | `/api/v1/apps/{key}/roles` | unchanged | `RoleRead` + `updated_at` (new field) | unchanged | unchanged |
| PATCH | `/api/v1/apps/{key}/roles/{role_id}` | unchanged | `RoleRead` + `updated_at` (new field) | unchanged | unchanged |

`ETag` format and `If-Match` parsing are exactly ADM-024's (`app/core/etag.py`): `"<microseconds since epoch of updated_at>"`, strong comparison, list or `*`, weak/malformed/over-long fail closed with 412. Lists (`GET …/role-definitions`) carry no `ETag`; items carry `updated_at`, from which the tag is derivable. Auth class 1 (R3) for every route, unchanged.

Order of checks on the PUT (proposed default, Q3): 401/403 (`require_permission` dependency) → 422 body shape (Pydantic) → 404 app/role (after `lock_app`) → **412** → 422 unknown permission / `wildcard_super_admin_only` → 403 conditional `admin:*` (audited `record_denied`) → 409 role rules (default/owner/super-admin/G3) → write.

## Tests to write
All in `tests/test_rbac_roles_if_match.py` unless stated. Reuse `_setup`, `_held`, `_put_url` and `_capture_sql` from `tests/test_rbac_roles_api.py` (import them, or copy the few lines; do not refactor the other file). Audit rows are counted with `select(func.count()).select_from(AuditLog)` filtered on `action == "role.permissions_replaced"` (non-denied rows).

| Test (file › name) | Covers |
|---|---|
| `test_get_role_definition_returns_etag` | GET role-definition: `ETag == version_etag(parse(body["updated_at"]))`. |
| `test_put_changing_set_bumps_updated_at` | AC1. PUT without header changes the set: body `updated_at` > the earlier GET's, the re-GET `updated_at` equals the PUT's, and the PUT `ETag` equals the re-GET `ETag`. Two successive changing PUTs give strictly increasing values. |
| `test_put_noop_does_not_bump` | AC1. Re-PUT of the same set (with the current tag and without a header): 200, same `updated_at` and `ETag`, no audit row added. |
| `test_put_matching_if_match_200` | AC2/AC5. GET tag A, PUT with A: 200, permissions replaced (`_held`), new `ETag` ≠ A, exactly one `role.permissions_replaced` audit row. A second PUT with the new tag: 200. |
| `test_put_stale_if_match_412` | AC2/AC5. Tag A; PUT with A (tag B); PUT a different set with A: 412 envelope, `details` null, response `ETag == B`, `_held` equals the set from the first PUT, audit count unchanged, role `updated_at` unchanged. |
| `test_two_clients_second_gets_412` | The ticket's motivating case: both hold A; client 1 PUTs `{a}` → 200; client 2 PUTs `{b}` with A → 412; role holds `{a}`. |
| `test_put_if_match_list_containing_current_200` | `If-Match: "0", <current>` → 200. |
| `test_put_if_match_star_200` | `If-Match: *` → 200, set replaced. |
| `test_put_weak_if_match_412` | `W/<current>` → 412, nothing changes. |
| `test_put_malformed_if_match_412` | `If-Match: garbage` → 412, nothing changes. |
| `test_put_noop_with_stale_if_match_412` | Same set as stored but stale tag → 412 (precondition before change detection, as ADM-024 Assumption 7). |
| `test_put_without_if_match_unchanged` | AC3. Stale situation but no header → 200, set replaced, audit row written. |
| `test_put_unknown_role_404_beats_412` | Unknown `role_id` (and unknown app) with a stale tag → 404. |
| `test_put_stale_and_unknown_permission_gives_412` | Pins the order: stale tag + unknown permission key → 412 (default Q3). |
| `test_put_stale_and_409_rule_gives_412` | Stale tag + adding an `is_admin` permission to the default role → 412, no 409. |
| `test_put_stale_and_needs_super_gives_412_no_denied_audit` | Non-super caller (`make_client(admin_permissions=["admin:roles:manage"])`) adds an `is_admin` permission with a stale tag → 412 and no denied audit row (default Q3). Same request with the current tag → 403 `admin_wildcard_required` and one denied row (unchanged behaviour). |
| `test_put_if_match_401_unchanged`, `test_put_if_match_403_unchanged` | No Bearer → 401; caller without `admin:roles:manage` → 403, with `If-Match` present. |
| `test_precondition_read_after_app_lock` | AC4. `_capture_sql`: the first `FOR NO KEY UPDATE` statement index is lower than the first `FROM roles` select; a stale request issues no `INSERT`/`DELETE`/`UPDATE`. |
| `test_rename_bumps_updated_at` | PATCH rename after a PUT: `updated_at` strictly greater than the PUT's (monotonic across both writers; Approach 4). A no-op PATCH (`{}` or same name) does not change it. |
| `test_manifest_change_bumps_role_version` (`tests/test_app_manifest_api.py`) | Manifest push that adds a permission to an existing role: that role's `updated_at` increases; an unchanged second push leaves it unchanged. |
| `test_default_role_rebuild_bumps_member_version` | `RoleCatalogService(db_session).build_default_role("stoktok")` after adding a non-admin permission: `<app>:member` `updated_at` increases; an idempotent rerun leaves it unchanged. |
| `test_touch_role_strictly_increasing` | Repository: two `touch_role` calls in one transaction give strictly increasing `updated_at` (works under the constant `now()` of the test transaction). |
| `test_get_role_populates_existing` | Repository: change `roles.updated_at` with a Core `UPDATE`; `get_role` returns the new value even though the `Role` is in the identity map. |
| `test_openapi_contract.py › test_role_put_412_and_if_match_declared` | PUT: `412` references the envelope; `If-Match` header param, not required; `ETag` header on 200. |
| `test_openapi_contract.py › test_etag_header_declared` (extended) | `ETag` on 200 of GET role-definition and PUT permissions. |
| `test_rbac_roles_api.py › test_role_read_has_exact_keys` (updated constant) | `RoleRead` has `updated_at` on POST, PATCH and PUT. |

## Approach
1. **Version source (no migration).** `roles.updated_at`, bumped with ADM-024's `bumped_updated_at` (`GREATEST(clock_timestamp(), updated_at + 1 µs)`, `app/db/base.py:17-23`), never `now()`: `now()` is constant per transaction and can repeat the old value (ABA), and the tests run in one outer transaction.
2. **One hook for every writer of `role_permissions`.** Writers found: `_set_role_permissions` (`service.py:187`) is the only caller of `apply_role_permission_diff` (L200), and it serves the staff PUT (L1330), the default-role builder (L322) and the manifest (`manifest.py:219`). `delete_permission` cascades only when no role holds the permission (guard at `service.py:1042`), so it never changes a role's set; `delete_role` removes the role itself. So `touch_role` goes inside `_set_role_permissions`, only when `to_add or to_remove` (after the diff, outside the `try`). A no-op write never bumps, which keeps `test_put_role_permissions_dedupes_and_is_idempotent` (`writes == []`) and the manifest "push twice is idempotent" tests green.
   - `touch_role` uses Core `UPDATE … execution_options(synchronize_session=False)` then `session.refresh(role, attribute_names=["updated_at"])`. Do **not** assign `role.updated_at` in Python and flush (it would mark the row dirty and fall back to `onupdate=now()` semantics elsewhere).
   - Fresh roles created in the builder or manifest also get bumped; harmless.
3. **`replace_permissions(..., precondition: IfMatch | None = None)`** (R1):
   - a. `app = await _locked_app(...)`; `role = await self._get(app, role_id)` (404). `get_role` now loads with `populate_existing=True`, so `role.updated_at` is the value read after the app lock under READ COMMITTED (database-optimizer finding 3).
   - b. `if precondition is not None and not precondition.any and role.updated_at not in precondition.versions: raise PreconditionFailedError(role.updated_at)`. Nothing has been written; no `record`/`record_denied`; `PreconditionFailedError` is not added to `DENIED_ERRORS`.
   - c. The rest of the method is unchanged. After `_set_role_permissions`, `role.updated_at` is the bumped value (or unchanged on a no-op); return `RoleRead(..., updated_at=role.updated_at)`.
   - Atomicity: every writer of a role's permission set holds `lock_app` for the app (ADR-0003 §9/§10, `repository.py:78-84`), so between (a) and the write no other writer can commit a change to this role. No conditional `UPDATE … WHERE updated_at = :v` is needed (both consults); no lost-race branch.
4. **Rename uses the same clock.** In `RoleService.update`, next to `role.name = data.name` (L1201), set `role.updated_at = bumped_updated_at(Role.updated_at)` (SQLAlchemy accepts a SQL expression as an attribute value; the existing `refresh` at L1206 loads the result). Without it, a rename in a transaction that waited on the lock writes `now()` (its start time), which can be earlier than a prior `clock_timestamp()` bump, so the version would go backwards (database-optimizer finding 6). One line, same file, no behaviour change other than the timestamp value. `If-Match` on PATCH stays out of scope.
5. **Schemas.** `RoleRead.updated_at: datetime = Field(description="Last change to the role row, including its permission set (USR-24).")`. `_read`, `create` and `replace_permissions` fill it. `RoleCatalogEntry` is untouched (the public catalog ETag is a body hash).
6. **Router** (only parse and set headers, R1):
   - `_ROLE_412 = "Stale If-Match (code precondition_failed); nothing changed; carries the current ETag"`.
   - GET role-definition: `**_with_etag(require_permission("admin:apps:read", errors={404: _ROLE_404}), 200, "Successful Response")`, `response.headers["ETag"] = version_etag(read.updated_at)`.
   - PUT: `**_with_etag(require_permission("admin:roles:manage", errors={404: _ROLE_404, 409: ..., 412: _ROLE_412}, requires_super="conditional", super_when=...), 200, "Successful Response")`; param `if_match: Annotated[list[str] | None, Header(alias="If-Match", description=IF_MATCH_DESCRIPTION)] = None`; set `ETag` from the returned `RoleRead.updated_at`. `require_permission` accepts 412 (`admin_auth/deps.py:127-130` only rejects 401/403/non-4xx).
   - Confirm in the OpenAPI test that the 200 `response_model` schema survives the `_with_etag` merge (as ADM-024 did).
7. **Docs.**
   - `docs/spec-v1.md` §5.3, after L51: `> 2026-11-19 (USR-24): roles.updated_at now also moves when a role's permission set changes (staff PUT, manifest, default-role rebuild); GET /api/v1/apps/{key}/role-definitions/{role_id} and PUT …/roles/{role_id}/permissions return a strong ETag derived from it; the PUT accepts optional If-Match with the ADM-024 rules (412 precondition_failed with the current ETag, nothing changed); RoleRead gains updated_at.`
   - `docs/consumers.md`, new bullet after L101: "**Role permissions (USR-24).** `GET …/role-definitions/{role_id}` and `PUT …/roles/{role_id}/permissions` return an `ETag` with the ADM-024 format; send it as `If-Match` on the PUT. A stale tag gives 412 `precondition_failed` with the current `ETag`; re-GET and re-apply. `updated_at` now moves on every permission-set change, including manifest pushes; it does not move when only derived fields change (`is_default`, `privileged`, e.g. after an app's default role or a permission's `is_admin` changes). The PUT response carries the new tag, so edits can be chained without a re-GET."
8. **Run `make check`** (R8) and `uv run alembic check`.

Rules: R1 (SQL in repository, compare in service, headers in router), R3 (auth class and permissions unchanged), R4 (audit unchanged; no row on 412), R6 (existing 412 envelope), R7 (PATCH/DELETE `If-Match` are follow-ups), R8 (tests above), R9 (additive only).

Size: about 60 production lines and 300 test lines. One implement/review cycle.

## Specialist input used
- **api-platform-engineer, adopted:** F1/Rec 2 reuse of ADM-024 helpers; F3 app lock instead of a conditional UPDATE; F4/Rec 1 one bump hook in `_set_role_permissions`, conditional on a non-empty diff, with `bumped_updated_at`; F5/Rec 4 `updated_at` on `RoleRead` and new `RoleDefinitionRead` description; Rec 4 `ETag` on the PUT response; Rec 5 no ETag on lists; Rec 3 order (412 right after 404, before 422/403/409) as the proposed default; Rec 6 docs; Rec 7 test list. OQ1 answered: no other path removes `role_permissions` rows of a surviving role (Approach 2).
- **api-platform-engineer, deferred:** Rec 2 / OQ3 `ETag` header on POST and PATCH roles: not needed by the AC (both responses now carry `updated_at`, from which the tag is derivable); keeps the diff small. Listed as a follow-up together with `If-Match` on PATCH/DELETE (OQ2).
- **database-optimizer, adopted:** F3 atomicity via `lock_app` and the READ COMMITTED caveat (`populate_existing=True` on `get_role`); F4 shared hook; F5 permission delete cannot change a role's set; F6 rename on `bumped_updated_at` (Approach 4, adopted because it costs one line and keeps the version monotonic); 412 with `details: null` (proposed default Q1); the lock-ordering SQL-capture test; repository test for `touch_role`; manifest and default-rebuild tests.
- **database-optimizer, not adopted as default:** OQ1 "403 before 412" (keep denied attempts audited). Presented to Ming as Q3 option (b); the default follows RFC 9110 §13.2.1 and the api-platform-engineer.

## Assumptions
- **Builds on ADM-024 (merged).** See `work/tickets/ADM-024/plan.md`. Reused as is: `app/core/etag.py` (`version_etag`, `parse_if_match`, `IfMatch`, `IF_MATCH_DESCRIPTION`, `ETAG_RESPONSE_HEADER`), `app/core/errors.py` `PreconditionFailedError` (L135-145), `app/db/base.py` `bumped_updated_at`, the CORS `ADMIN_POLICY` allowing `If-Match` and exposing `ETag`, and R6 already listing 412. Same parsing, ETag format, `*` semantics, fail-closed rules and "412 carries the current ETag".
- Version visible on `RoleDefinitionRead` means its existing `updated_at` field (now bumped); no new field there.
- `updated_at` changes meaning on `RoleDefinitionRead` ("last change including permissions"). That is the fix the ticket asks for; no consumer relies on the old meaning (admin-pan requested this ticket).
- The tag covers the stored role row and its permission set only. Derived fields `is_default` and `privileged` can change under the same tag (app default role changed, a permission's `is_admin` flipped). Same position as ADM-024 Assumption 8; documented in consumers.md.
- `test_two_clients_second_gets_412` runs sequentially on one connection (conftest); a true two-connection race is covered by the lock argument and `test_precondition_read_after_app_lock`, as in ADM-024.
- **Hotspots:** USR-23 (PLANNING, same batch) may touch `app/modules/rbac/schemas.py` (`RoleHolderRead`, L298), `app/modules/rbac/repository.py` (near `page_role_holders`, L541), `app/modules/rbac/router.py`, `tests/test_openapi_contract.py`, `docs/consumers.md` and `docs/spec-v1.md`. Different line ranges; expect a rebase, not a conflict of intent.
- Follow-ups (not in this diff, R7): `If-Match` on PATCH and DELETE roles; `ETag` header on POST/PATCH roles; `If-Match` on permission PATCH and app PATCH.

## Blocking questions
Ming decides these in the batch approval round. Each has a default; the plan is implementable with the defaults, hence `Status: READY`.

1. **412 or 409 on a stale precondition, and the `role_changed` reason.** The ticket offers 412 or 409 `details: [{"reason": "role_changed"}]`.
   - **Default (a):** 412 `precondition_failed`, `details: null`, current `ETag` header: identical to organizations and entitlements (ADM-024, owner OK'd 412 on 2026-11-18). Drop the `role_changed` reason. No `app/core/` change.
   - (b) 412 with `details: [{"reason": "role_changed"}]`: needs an optional `details` argument on `PreconditionFailedError` in `app/core/errors.py` (High path) and makes roles differ from orgs/entitlements.
   - (c) 409 `conflict` with the reason: would mix a stale-version signal with the existing business-rule 409s on this route.
2. **Version and precondition form.**
   - **Default:** bump `roles.updated_at` (no migration) and use the `If-Match` header, as ADM-024.
   - Alternative: integer `version` column + body `expected_version`: needs a migration (High, R2) and a second concurrency mechanism.
3. **Order of 412 versus the content-dependent 403 (and 422/409).** The `admin:*` check on this PUT depends on the body (which permissions are added or removed, `service.py:1292-1296`).
   - **Default (a):** 404 → 412 → 422 → 403 → 409. RFC 9110 §13.2.1 evaluates preconditions before processing request content, and a stale client learns first that its view is old. A non-super caller with a stale tag gets 412 and **no denied audit row** (nothing was attempted against the current state; resending with the fresh tag gives the audited 403).
   - (b) 404 → 422 → 403 (audited) → 412 → 409: every request that would be denied is audited, even when stale. Moves the check after the `record_denied` block (L1311); the order tests flip accordingly.
4. **Rename bump (Approach 4).**
   - **Default:** include the one-line switch of PATCH rename to `bumped_updated_at`, so `updated_at` never goes backwards.
   - Alternative: leave rename on `onupdate=now()` and track it as a follow-up.

## Split proposal
None. About 60 production lines and 300 test lines, one module, no migration.
