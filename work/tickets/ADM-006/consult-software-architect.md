## ADM-006 consult (software-architect): org product entitlements

The scope is clear for staff CRUD. How services read entitlements is still open (spec §8 Q4), and any answer except "staff Bearer only" needs an owner decision. Below is a safe default that ships the ticket without waiting on that.

### Findings
1. **Spec.** §5.6 says entitlements are "readable by mmlabs services (service-to-service auth, §8 Q4)". Q4 is still unchecked. §5.5 merged products into `apps`, so "product" means a row in `apps`. §5.7 puts "webhooks/events to products" in **Later**, which already rules push out of v1.
2. **Organizations** (`app/modules/organizations/models.py`) are platform-wide (OQ1 working assumption), with `status active|suspended` and `created_by`. ADR-0013 §4 sets a house rule: the org FK is RESTRICT, not CASCADE, because a cascade drops grants without a per-row audit entry. Deleting an org that has assignments returns 409 `org_has_assignments` (`organizations/service.py:57-71`). App delete likewise refuses when assignments exist (`rbac/service.py:502-512`). Deleting the `admin` app is refused.
3. **Apps** (`rbac/models.py:59`) have `status active|disabled`, `org_mode none|required` and an immutable `key`. Paths address apps by `key` (the D18 exception to R9's UUID rule).
4. **App credential (R3 class 4, ADR-0016).** The `mmak_` key is allowed on exactly one route (`PUT /apps/{key}/manifest`), pinned in `tests/test_route_auth_classes.py`. It is scoped to its own app (another app gets 403), it is not OAuth client credentials on purpose (§2), it is issued with `admin:*`, and there are at most 2 per app. `current_app_credential` in `app_credentials/deps.py` takes `key` from the path, so it could be reused on `/apps/{key}/…` routes as is. Widening class 4 changes R3 and ADR-0016 §1, so it is the owner's call.
5. **Tokens** (`tokens/service.py:88-97`). The `org` and `org_roles` claims are only added when the app has `org_mode = required` and the user holds a role in exactly that **active** org. A suspended org already gets 403 `org_access_denied` at token issuance. With 12 h tokens, an already-issued token keeps working after a suspension for up to 12 h (OQ8 accepted this tail).
6. **Admin permissions** are seeded by a data migration (`migrations/versions/20261115_5e2a9c7d1f04_seed_admin_permissions.py`, with `is_admin=true` and `ON CONFLICT DO NOTHING`). They are also listed in `deploy.md:435`, `tests/test_rbac_schema.py` and `tests/test_route_auth_classes.py`.

### Recommendations
**R-A. Where entitlements live (AC1, R2).** Put them in a new module `app/modules/entitlements/` with the reference-module shape, and a table `org_entitlements`:
- `id` UUID; `org_id` FK to `organizations.id` **ON DELETE RESTRICT**; `app_id` FK to `apps.id` **ON DELETE RESTRICT**; `UNIQUE(org_id, app_id)`; an index on `app_id` for the reverse lookup.
- `plan` as `String(63)`, free-text and NOT NULL. It should be a slug-ish CHECK like the app key, not an enum, because plan catalogs belong to each product (ADR-0001: business rules stay in the app).
- `limits` as JSONB, NOT NULL, default `{}`. Pydantic should validate it as `dict[str, int ≥ 0]` with a bounded key count and key format, so it holds no arbitrary blobs.
- `enabled` bool, NOT NULL, default true.
- The timestamp mixin.
- Register the model in `app/db/models.py`. The migration needs a working `downgrade()`.

RESTRICT follows ADR-0013 §4. Extend the org-delete and app-delete guards so they return 409 with new reasons `org_has_entitlements` and `app_has_entitlements`. Without that, an IntegrityError would surface as an unclear error.

**R-B. Which apps can be entitled.**
- Refuse `admin` with 409 `admin_app`, as credentials and register already do.
- Allow disabled apps (staff may pre-provision), but never report them as active (see R-E).
- **Default: allow only `org_mode = required` apps** (409 `app_not_org_mode`). For an `org_mode = none` app, no token or request carries an org, so the entitlement can never be enforced. Lifting this later is additive. Flagged as an open question.

**R-C. Staff endpoints (AC2, R1, R3 class 1, R4, R6, R9).** All take Bearer `aud=admin` plus `require_permission`:
- `GET /api/v1/organizations/{org_id}/entitlements` returns `Page[EntitlementRead]`.
- `POST /api/v1/organizations/{org_id}/entitlements` takes `{app_key, plan, limits, enabled}` and returns 201. A duplicate gives 409 `entitlement_exists`; an unknown org or app gives 404.
- `PATCH /api/v1/organizations/{org_id}/entitlements/{entitlement_id}` changes plan, limits or enabled.
- `DELETE …/{entitlement_id}` revokes and returns 204.

Addressing the item by its UUID avoids a new R9 exception; using `app_key` in an organizations path would need one. The response carries `app_key`. "Disable" means `enabled=false` (keeps plan and limits); "revoke" means a hard delete with a before-snapshot in the audit. Write one audit entry per mutation in the same transaction: `entitlement.granted`, `.updated`, `.revoked`, with target org and `app_key` set. Catch the IntegrityError on a concurrent duplicate insert and return 409.

**R-D. Permissions.** Add two new `is_admin` permissions, `admin:entitlements:read` and `admin:entitlements:write`, in a seed migration whose downgrade deletes them. Update `deploy.md`, the schema test and the route-class map. Reusing `admin:organizations:*` is the alternative, but write access to orgs and write access to commercial entitlements are different duties (least privilege, §4 actors).

**R-E. Service read (AC3): safe default that does not block.**
- Ship a **"services view" endpoint under staff Bearer** in v1: `GET /api/v1/organizations/{org_id}/entitlements/active`. It is a single document (needs an R9 exception like D23) or a filtered `Page`.
- **Active** = `enabled` AND org `status='active'` AND app `status='active'`. Put this rule in one repository query so every consumer uses the same definition.
- This meets the AC's "suitable for services" wording without changing R3. Defer the actual service authentication to Q4, and record it in an ADR (ADR-0017, *Entitlement consumption*) with the options below.

**Options for when Q4 is answered:**

| Option | What changes | Gains | Costs |
|---|---|---|---|
| **1. Extend class 4** (recommended if pull is chosen): `GET /api/v1/apps/{key}/entitlements/{org_id}` (and maybe a paged `/apps/{key}/entitlements`) accepts the app's own `mmak_` key; the app can only read its own app's rows | Reuses existing machinery (the path `key` already drives `current_app_credential`); app-scoped by design; immediate revoke | Amends R3 and ADR-0016 §1. Today's key is a deploy-time catalog-writer secret issued with `admin:*`. Putting it in a runtime service gives it the manifest-write power too, so a scope column (`manifest` vs `read`) or separate key kinds would be needed (a migration) |
| **2. Embed in token**: add an `ent: {plan, limits}` claim when the `org` claim is present | No new auth class; no runtime call; works with the existing `mmlabs-auth` verifier | Changes the token contract (additive, but still a D7 change); entitlements are stale for up to 12 h; tokens grow; still nothing for backend jobs that have no user token |
| **3. Push** (webhooks) | Fresh data, decoupled | §5.7 says Later; needs signing, retries and an outbox. Out of v1 |

The token-claim option is the cheapest way to enforce entitlements per user request. Option 1 is needed for org-level batch jobs. "Both" (pull plus token) is reasonable, but only after the owner decides.

**R-F. Suspended-org semantics (AC4).**
- Suspension **does not change** entitlement rows. Grant, update and revoke stay allowed on a suspended org, so staff can prepare reactivation. Reads still list the rows.
- The **active view** treats a suspended org as having no active entitlements: an empty list (or an `org_status` field in the single document), not 403. A staff caller already knows the org exists.
- If an app-credential read is added later, return the same one-answer rule as `org_access_denied`/404 for an unknown org vs a suspended org, to avoid an existence oracle across apps. Decide this in ADR-0017.
- Tests: grant on a suspended org returns 201; the active view for a suspended org is empty; after reactivation the entitlements reappear.

### Risks
- **Wrong reading of "readable by services".** If the planner reads it as "mount `mmak_` on the read route", that silently widens R3 class 4, and the deploy key gains a runtime role. The planner must not do this without the owner (R3, R7).
- **Entitlements and RBAC can drift.** A user can hold an org role for an app the org is not entitled to. Token issuance does not check entitlements. Wiring that in would make every app hard-gated and could lock calechip orgs out at deploy, because no rows exist yet. Recommendation: **do not** gate tokens on entitlements in ADM-006; leave enforcement to products and record it as a follow-up.
- **Unbounded `limits` JSONB.** Without a validated shape it becomes a dumping ground, and future breaking changes inside v1 would be hard. Keep it typed as `{str: int}`.
- **RESTRICT on app/org delete** adds a new 409 path, so admin-pan needs to handle it (it already does for `org_has_assignments`).
- **Free-text `plan`**: typos are possible. Accepted, because plan catalogs belong to each product. A per-app plan list could be added later through the manifest.

### Open questions (owner)
1. **§8 Q4:** how do services consume entitlements: pull with an app key (extends R3 class 4 / ADR-0016), a token `ent` claim, both, or defer? Proposed default: defer, and ship staff Bearer only in ADM-006.
2. If pull: may a runtime service hold an `mmak_` key that can also write the manifest, or must keys gain a scope (`manifest` | `entitlements:read`)?
3. Entitlements only for `org_mode = required` apps (proposed), or for any non-admin app?
4. New `admin:entitlements:read|write` permissions (proposed), or reuse `admin:organizations:*`?
5. Should token issuance or app join ever require an active entitlement? Proposed: not in v1.
6. Does `limits` stay as `{key: non-negative int}`, or do some limits need booleans or strings?

Relevant files:
- /home/user/org-admin-api/docs/spec-v1.md (§5.6, §5.7, §8 Q4)
- /home/user/org-admin-api/docs/adr/0016-app-credential-auth-class-and-manifest.md
- /home/user/org-admin-api/docs/adr/0013-org-scoped-identity.md
- /home/user/org-admin-api/app/modules/organizations/models.py, /home/user/org-admin-api/app/modules/organizations/service.py
- /home/user/org-admin-api/app/modules/rbac/models.py, /home/user/org-admin-api/app/modules/rbac/service.py
- /home/user/org-admin-api/app/modules/app_credentials/deps.py
- /home/user/org-admin-api/app/modules/tokens/service.py
- /home/user/org-admin-api/migrations/versions/20261115_5e2a9c7d1f04_seed_admin_permissions.py
- /home/user/org-admin-api/tests/test_route_auth_classes.py
