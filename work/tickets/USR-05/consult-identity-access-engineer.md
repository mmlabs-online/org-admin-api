USR-05 consult: identity-access-engineer (read-only)

Sources I read: `/home/user/org-admin-api/work/tickets/USR-05/ticket.md`, `/home/user/org-admin-api/docs/adr/0001-multi-app-authorization.md` §6, `/home/user/org-admin-api/docs/adr/0003-rbac-keys-and-matching.md` §7–§11, `/home/user/org-admin-api/docs/adr/0008-admin-api-authentication.md` (ADM-002b amendment), `/home/user/org-admin-api/docs/adr/0015-org-roles-claim-and-permission-scope.md`, `/home/user/org-admin-api/docs/identity-architecture.md` §3–§4, `/home/user/org-admin-api/app/modules/rbac/service.py` (`_build_for_app` L279, `AppService.register` L517, `PermissionService._create` L829, `RoleService.replace_permissions` L1133), `/home/user/org-admin-api/app/modules/rbac/actor.py`, `/home/user/org-admin-api/app/modules/rbac/scopes.py`, `/home/user/org-admin-api/app/modules/sessions/service.py`.

## Findings

F1. **R3 and D14 conflict.** R3/D14 allow exactly three auth classes: Bearer admin, cookie and public. identity-architecture.md §4 lists `PUT /api/v1/apps/{key}/manifest` with auth "app credential", which is a fourth class. The plan needs an R3/D14 amendment, as an ADR (e.g. ADR-0016) plus a CLAUDE.md wording change. Otherwise the reviewer has to fail it under R3.

F2. **`register-app` (the `AppService.register` service, run from the CLI) already does almost everything the manifest needs.** It validates everything before the first write and locks the app first. It refuses: `admin` (409 `admin_app`), prefix mismatch, `<app>:*`, `scope_conflict`, `reserved_scope`, `org_mode_required`, existing `is_admin` (409), and `scope_mismatch` (409, no flip). It only adds permissions, and rebuilds through `_build_for_app(app, set_default=False)` only when it added some. It writes one audit row. The manifest sync should be this same pipeline, not a parallel one.

F3. **The actor model blocks a naive reuse.** A non-privileged `SystemActor` hits `require_super` in two places in `PermissionService._create`:
- (a) when creating any `is_admin` permission;
- (b) when creating a non-reserved `scope = app` permission on an `org_mode = required` app. ADR-0015 §2 exempts only `register-app` (`system:cli`, privileged).

So a manifest actor can be neither "privileged" (too broad) nor plain non-privileged (it cannot do what the ticket asks). It needs a narrow, explicit carve-out.

F4. **`_build_for_app` rebuilds the member role fully on every call.** Run on every push, it would silently undo staff tightening of `<app>:member`. That breaks ADR-0003 §7 / D9 ("a staff tightening is not silently undone"). `register` avoids this by rebuilding only when it added permissions.

F5. **The catalog ETag is a hash of the role-catalog bytes** (ADR-0003 §11). The document contains only roles and the permission keys they hold. A manifest that adds only permissions no role holds does not change the ETag. That includes `is_admin` permissions, `scope = org` permissions on org apps, and any addition when there is no rebuild. AC "ETag changes when the manifest changes" is only true when role content changes.

F6. **There is no credential table or hashing helper yet.** The house pattern for 256-bit random secrets is `secrets.token_urlsafe(32)` plus SHA-256 (`device_token_hash`), with `hmac.compare_digest` where secrets are compared.

## Recommendations

**Credential: an opaque per-app API key, not OAuth client credentials** (AC1)
- A client_credentials grant would add a token endpoint and a second token type and kind of client. That moves toward a "full OIDC provider" (R10), all for one deploy-time PUT.
- An opaque key is the boring choice and matches the existing device-token pattern.
- Format: `mmak_<key_id>_<secret>`. `key_id` is 16 random bytes, base64url, and used for lookup. `secret` is `secrets.token_urlsafe(32)`. The fixed prefix lets secret scanners find leaked keys.
- Table `app_credentials`: `id`, `app_id → apps ON DELETE CASCADE`, `key_id` unique, `secret_hash bytea` (SHA-256; the secret is 256 bits, so Argon2 is not needed), `created_by → users NULL`, `created_at`, `last_used_at NULL`, `revoked_at NULL`. Hand-written migration with a downgrade (R2).
- At most 2 active credentials per app, to allow rotation overlap.
- Send it as `Authorization: Bearer mmak_…` on this route only. Reject anything without the `mmak_` prefix with 401.
- Verify: parse, look up by `key_id`, `hmac.compare_digest` the hash, check not revoked.
  - Unknown, malformed, revoked or wrong secret: 401 `unauthorized`, no details (one answer).
  - Never log the raw key (R5/R11). Log `key_id` only.

**Issuance and rotation by staff** (AC1, R3, R4)
- `POST /api/v1/apps/{key}/credentials` returns 201 with the secret shown once.
- `GET /api/v1/apps/{key}/credentials` returns `Page[T]` with metadata only: `id`, `key_id`, `created_at`, `last_used_at`, `revoked_at`. Never the hash.
- `DELETE /api/v1/apps/{key}/credentials/{id}` revokes, 204.
- Rotation = issue a new key, deploy it, then revoke the old one. A third active key is 409 `credential_limit`.
- Permissions: route permission `admin:apps:write` plus `require_super` (`admin:*`). A credential can create `is_admin` permissions and reshape roles, so issuing one is a privileged write in the ADR-0008 G-sense.
- Audit `APP_CREDENTIAL_ISSUED` / `APP_CREDENTIAL_REVOKED` in the same transaction, with `key_id` only. Record denied attempts as well (`record_denied`), as other privileged writes do.
- Issuing a credential for `admin` is refused: 409 `admin_app`. Issuing one for a disabled app is 409 `app_disabled`.

**Binding to exactly one app** (AC1, AC6)
- Verify the credential first, then compare `credential.app_id` with the app at `{key}`. A mismatch is 403 `app_credential_mismatch`. This is the AC's "wrong app credential → 403".
- Check the `admin` path before any DB work, defense in depth: `admin` gets the same 403, or 404 if you prefer. No credential can exist for it anyway.
- A disabled app with a valid credential: 409 `app_disabled`, as in `register`.
- Update `last_used_at` best-effort inside the transaction.
- Add a per-IP rate limit and a body cap (e.g. ≤ 500 permissions, ≤ 50 roles) to limit DoS on the app lock.

**Audit actor**
- Add `SystemActor("system:manifest", privileged=False)`.
- Audit rows use `actor_kind = system` with label `app:<key>/<key_id>`. The existing CHECK requires a label for system actors, so no schema change is needed.
- One `APP_MANIFEST_SYNCED` row per changing push, with the full diff. No row when nothing changed.

**Manifest shape** (AC2)
```
{"permissions": [{"key", "description", "is_admin": bool=false, "scope": "app"|"org"?}],
 "roles": [{"key", "name", "permissions": [...]}]?,
 "member": {"permissions": [...]}?}
```

**Validation: everything before the first write, as in `register`**
- 422 `prefix_mismatch` for any key outside `<app>:`.
- 422 `wildcard_not_allowed` for `<app>:*` (the wildcard stays staff-only).
- 422 `reserved_scope` for a contradiction with `org:create` / `org:invite`. Use `reserved_scope()` from `scopes.py`.
- 422 `scope_required` on `org_mode = required` apps when a non-reserved key has no `scope`. Fail closed: never fall back to `default_scope` for a manifest.
- On `org_mode = none` apps, a missing scope means `app`, and `scope = org` is 422 `org_mode_required`.
- Duplicate keys are 422. Descriptions go through the `display_text` validator.

**`is_admin` handling** (ADR-0003 §9, ADR-0008 G2)
- New permissions may be created with `is_admin = true` under `system:manifest`. This is a narrow carve-out from G-create, to be written into the ADR. It is safe because such a permission reaches nobody: it is never in member, and granting a role that holds it needs `admin:*` (G4).
- No flips through the manifest, in either direction. An existing permission whose stored `is_admin` or `scope` differs from the manifest is 409 (`is_admin_mismatch` / `scope_mismatch`) and nothing is written. Flips stay staff-only with `admin:*`, which keeps the "default role never holds `is_admin`" invariant on one guarded path.
- On org apps, new `scope = app` permissions from the manifest need the same exemption `register-app` has. Make this explicit in the ADR and record it as an owner decision (see Q1).

**Removed permissions** (AC3)
- Permissions in the database but not in the manifest are returned as `undeclared_permissions` (with the roles holding each) and recorded in the audit diff.
- Never delete them and never strip them from roles.

**Member role** (AC3, the 2026-11-17 note, ADR-0015 §3)
- Always go through `_build_for_app(app, set_default=False)`. A NULL default role stays a staff decision.
- **No override:** rebuild only when the push added permissions (`register` parity, F4). Then `is_admin` and, on org apps, `scope = org` permissions are excluded by construction. Pushing twice is a no-op, so `scope = org` never reaches member.
- **Override:** extend `_build_for_app` with an optional `only: set[str]` that is *intersected* with the eligible set (non-`is_admin`, plus `scope = app` on org apps), so an override can narrow but never widen.
  - An override key that is not eligible or not declared is 422 `ineligible_member_permission`, listing the keys.
  - Apply the override on every push where member ≠ target. Document that with an override the manifest owns member content.
  - The `member_is_org_owner` 409 still applies.

**Extra roles**
- Keys must be `<app>:<name>`, never `<app>:member`, and not an `is_system` role or `apps.org_owner_role_id`. Those are 409 `role_not_manifest_managed`.
- Create if absent (inert until staff grant it). Existing roles get additive changes only. Permissions a role holds but the manifest omits are reported, not removed.
- Any addition of an `is_admin` permission to an existing role is 409 `requires_staff` (G2/G3 parity).
- Apply every role write through the same guards as `replace_permissions`: factor its checks into a shared helper rather than re-implementing them.

**Transaction and idempotency**
- One transaction, with `lock_app` first (ADR-0003 §10).
- Return 200 with `outcome: created|updated|unchanged` plus the diff lists.
- A second identical push writes nothing (test it).

**ETag test** (AC4, F5)
- Assert the change with a push that alters role content: a new non-admin `scope = app` permission that reaches member, or a new extra role.
- Separately assert that an `is_admin`-only addition leaves the ETag unchanged. That is the intended behaviour; see Q3.

**Tests beyond the AC6 list**
- 401 cases: no header, malformed, revoked, wrong secret, and an admin JWT presented to the manifest route.
- 403: a credential for app A used on app B. Also `admin`.
- 409: `is_admin` flip, `scope` flip.
- 422: missing scope on a required app, wildcard, reserved scope.
- Two pushes never add a `scope = org` permission to member.
- An override cannot widen member.
- Undeclared permissions are reported and still held by their roles.
- Staff issuance without `admin:*` is 403 and audited as denied.
- The raw secret never appears in logs, audit rows or list responses.

**Docs** (AC5)
- `docs/auth-integration.md`: manifest format, the `mmak_` secret stored in the deployment repo env, and pushing *before* rolling out the new app version (new permissions must exist first; removed ones are only reported).
- Rotation runbook in `docs/deploy.md`.

## Risks

- **A leaked app credential lets an attacker:** add `scope = app` permissions that reach every member, including on org apps through the D10 `roles` union; add inert `is_admin` permissions; and add permissions to extra roles. It cannot flip flags, touch `admin`, or grant anything. Mitigations: `admin:*` to issue, at most 2 keys, `last_used_at`, full audit, and a fast revoke.
- **Override mode** lets each deploy undo staff tightening of member. This is by design, but it has to be documented. Without an override, a push that adds permissions also resets member (the existing `register` behaviour).
- **Carve-outs from `require_super`** for `system:manifest` must be explicit and narrow (create-only). A broad `privileged=True` would also allow flips and admin-app writes through other code paths.
- **`register-app` (CLI) and the manifest both write the catalog.** Their rules must stay identical (shared validation helper), or an operator can do what an app cannot, and the reverse.
- **Scope classification is in the app's hands.** A misclassified org permission declared `scope = app` reaches every org's `roles`. That is the residual risk of ADR-0015, until consumers authorize from `org_roles` only.

## Open questions

- **Q1 (owner):** On `org_mode = required` apps, may the manifest create non-reserved `scope = app` permissions without staff `admin:*`, as `register-app` can (ADR-0015 §2)? My recommendation: yes, with scope required and audited. Otherwise every such push needs a staff step.
- **Q2 (owner):** Should the manifest be allowed to create `is_admin` permissions (the create-only carve-out from ADR-0008), or should those be staff-seeded with the manifest only referencing them? I recommend create-only.
- **Q3 (planner/owner):** AC4 versus F5. Is "ETag changes only when role content changes" acceptable? If not, the catalog needs a field that reflects all permissions (append-only, but outside the ticket's plan).
- **Q4 (planner):** Need an R3/D14 amendment adding a fourth auth class, "app credential", via an ADR and CLAUDE.md. This is a scope and rules conflict to flag to the owner.
- **Q5:** Extra-role semantics are additive-only in v1. Does the owner want replace semantics for manifest-created roles? That would need a `source`/`managed_by` marker column.
