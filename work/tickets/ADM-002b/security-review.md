## Attempt 1

# security-review — ADM-002b, attempt 1 (appsec-engineer)

Scope: `git diff origin/main...HEAD` (commit 2f2231e plus the plan, consult and start commits). I read the code only. I did not run `make check` or pytest, as instructed.

## Escalation paths a–o: where each is blocked and the test that proves it

| Path | Blocking code | Test |
|---|---|---|
| a. PUT `admin:*` or a leaf permission into one's own admin-app role | `RoleService.replace_permissions` (rbac/service.py:776-797): `wildcard_super_admin_only` 422 for every caller, then `require_super` on any diff for the admin app | `test_guard_matrix[role_put_admin_app]`, `test_wildcard_only_on_super_admin_422` |
| b. Create or PATCH admin-app permissions and roles | service.py:605 (permission create), :636 (permission update), :706 (role create), :726 (role rename) | matrix `permission_create_admin_app`, `permission_update_admin_app`, `role_create_admin_app`, `role_rename_admin_app` |
| c. Put an is_admin permission into a role that has assignments | service.py:740-754: a diff that touches is_admin needs `admin:*`, then a 409 `role_has_assignments` for every caller | matrix `role_put_adds_is_admin`, `test_is_admin_onto_assigned_role_409` |
| d. Flip `is_admin`, in either direction | service.py:633-637 (`admin:*`), :634-637 (409 when false→true and an assigned role holds the permission) | matrix `permission_is_admin_false_to_true` / `_true_to_false`, `test_is_admin_flip_on_assigned_role_409` |
| e. Delete or rename admin-app roles and permissions | service.py:665, :709, :726 | matrix `permission_delete_admin_app`, `role_delete_admin_app`, `role_rename_admin_app` |
| f. Grant an admin-app role, including to self or a sock puppet | `_guard_privileged` (service.py:~968), called after the lock in `_grant_locked` | matrix `grant_super_admin`, `grant_admin_app_role`; `test_sock_puppet_grant_refused[3]`; `test_self_grant_admin_role_403` |
| g. Grant a `<app>:*` or other is_admin role | same guard, via `role_has_admin_permission` | matrix `grant_is_admin_role`, `sock_puppet[app_wildcard]` |
| h. Revoke other admins, down to the last one | `revoke` re-reads the row after the lock, then the guard, then `LastSuperAdminError` | matrix `revoke_admin_app_role`, `revoke_is_admin_role`; `test_last_super_admin_revoke_409`; `test_self_revoke_admin_role_403`; the serialized revoke/disable pair in `test_rbac_locking.py` |
| i. Renewal branch as a second grant path | the guard runs before `find_assignment`; `granted_by` is set from the actor on both branches | `test_grant_renewal_branch_guarded` |
| j. PATCH the admin app's origins | service.py:330 (`require_super` on any change); `_validate_admin_origins` covers apex, max 2, and loopback in staging/prod | matrix `app_admin_origins_patch`, `test_admin_origins_patch_super_validated`, `…loopback_refused_in_production`, `test_admin_app_same_value_patch_leaf_ok` |
| k. Origin collision, incl. with `admin` | `app_keys_holding_origins` (any status, `admin` included) in create (:299) and update (:355). Inputs are normalised first: lowercase, default port dropped, trailing slash dropped, trailing-dot hosts refused | `test_app_create_origin_taken_409[active\|disabled]`, `…admin_origin_taken_409`, `test_app_patch_origin_taken_409` |
| l. Re-enable a disabled staff user | users/service.py:42 checks `user_has_privileged_assignment`, expired rows included | matrix `reenable_disabled_staff`, `reenable_expired_staff` |
| m. Disable super-admins until one is left | same check, plus the last-super-admin check under `lock_app("admin")` | matrix `disable_privileged_user`; `test_last_super_admin_disable_409`; `test_disable_one_of_two_super_admins_ok`; `test_concurrent_disable_pair_serialized` |
| n. Self-disable or self-enable | users/service.py: the self check runs before the G5 check | `test_self_status_change_403[2]` |
| o. Disable without revoking sessions | users/service.py:59 `revoke_all_accounts_of_user`, in the same transaction | `test_disable_revokes_account_sessions` (all devices revoked, other users untouched, `/auth/token` → 401); `test_disabled_user_pending_reset_token_unusable` |
| Unverified super-admin counted as the last one | repository `_counting_super_admin` now requires `email_verified_at IS NOT NULL` | `test_unverified_super_admin_not_counted[revoke\|disable]` |

**All 15 paths are closed, and each one has a test for the refused case and the allowed case.**

## Extra probes (all clean)

- **Missing or defaulted actor:** every mutator takes a keyword-only `actor: Actor` with no default, so `mypy --strict` catches a missing one. The routers use `AdminDep`. The only leftover is `RoleCatalogService.build_default_role(actor: uuid.UUID | None = None)` (service.py:240). It has no production or HTTP caller and never touches `is_admin` (💭 below).
- **`SYSTEM_CLI` / `SYSTEM_JOIN` reachable from HTTP:** `SYSTEM_CLI` is used only inside `register`, `set_admin_origins` and `bootstrap_super_admin`, which only `app/cli.py` calls. `SYSTEM_JOIN` is not privileged, so a join onto a privileged default role (or onto `admin`) fails closed in `_guard_privileged`. Join also refuses that case earlier.
- **`admin:*` obtained by a non-super role:**
  - Creating or updating admin-app permissions needs super, and `key` is immutable (`extra="forbid"`).
  - A role PUT of `admin:*` onto any role other than `admin:super-admin` gets a 422, super included.
  - Creating an admin-app role needs super.
  - The prefix check plus `APP_KEY_RE` stop another app from minting an `admin:`-prefixed key.
  - `effective_permissions` drops keys with a foreign prefix.
- **Cross-app escalation:** `role_permissions` are resolved per `app.id`. `<app>:*` is always `is_admin` (forced on create; a PATCH to false gets 422). A default role can never hold `is_admin`, whether via PUT, app PATCH or join.
- **Guards decided before the lock:** every guard runs after `_locked_app` / `lock_app`. `revoke` and `assign` re-read the row or role after the lock. Two windows remain, both documented (A7 stale permissions, A8 origin race); a third is the 💭 below.
- **Last-super-admin bypasses:**
  - Expiry is refused on the super-admin role.
  - The role is `is_system`; `admin:*` cannot be deleted, flipped or removed from super-admin.
  - The `admin` app cannot be disabled or deleted.
  - Only `UserService.set_status` writes `users.status`, and nothing un-verifies an email.
- **Mass assignment:** `granted_by` is not an input field. `UserStatusUpdate` and all the RBAC schemas use `extra="forbid"`.
- **Error leakage:**
  - `origin_taken` does not name the app that holds the origin.
  - The new 403s carry `details` that are `{"required":"admin:*"}` or null.
  - 404 comes before 403 only for staff callers.
- **Docs:** `docs_url` and `openapi_url` are `None` in production unless `API_DOCS_ENABLED=true`. FastAPI then also drops `/docs/oauth2-redirect`. `test_docs_off_in_production` checks that all routers are still mounted and that the doc paths return a 404 envelope.
- **Newly exposed surface:** the users and RBAC routers are now mounted. `test_route_auth_classes` runs on plain `create_app()`. Every mutating route keeps its router-level `current_admin` plus `require_permission`. `require_permission` returns a fresh `responses` dict, so the `_with_409` / `_WRITE` mutation is safe.

## Findings

No 🔴.

🟡 **1. No audit trail now that privileged mutations are live in production (R4, ADM-004).** Every guard site still carries only an R4 comment. Misuse by a super-admin, or a leaf holder's legitimate but harmful writes (disabling apps, re-enabling users), cannot be reconstructed.
- Fix: schedule ADM-004 immediately after this ticket (the plan already lists it). Until then, the owner should keep the number of `admin:*` holders to the minimum. Not a merge blocker: the spec gates R4 on ADM-004.

🟡 **2. The only check that forced an explicit `APP_ENV` is gone with `_admin_apis_gate` (app/core/config.py, previously the explicit-`APP_ENV` check; `app_env` default `"local"` at config.py:28).** A production container started without `APP_ENV` now boots as `local`, with every router mounted. In that state:
- `/docs` and `/openapi.json` are on;
- loopback admin origins pass `_validate_admin_origins`;
- loopback app origins become CORS-allowed;
- the identity-HMAC / Argon2 production gates are skipped.

`docs/deploy.md:298` does set `APP_ENV=production`, so this is defence in depth.
- Fix (small, could go in ADM-002d): refuse startup when `app_env` was not explicitly set and `DATABASE_URL` does not point at localhost, or log a loud warning at startup. Alternatively, require `APP_ENV` in the deploy compose.

💭 **3. `set_status` reads `user_has_privileged_assignment` (users/service.py:42) under the `admin` lock only.** A concurrent super-admin grant of a privileged role in another app (e.g. `stoktok:admin`) to a disabled user, racing a leaf `users:write` re-enable, can leave the user active and privileged. A super chose that grant, so this is not an escalation by the leaf holder. Document it next to A7 and A8.

💭 **4. Disable does not invalidate the user's outstanding reset or verification email tokens.** The reset and verify paths refuse non-active users, so the tokens are unusable while the user is disabled. But a re-enable within the token TTL makes them live again.
- Fix (follow-up): consume or delete the user's `email_tokens` on disable.

💭 **5. The 403 `admin_wildcard_required` from `PATCH /users/{id}` tells a `users:write`-only holder that the target is staff or an app admin.** That holder may not hold `roles:assign`, which is needed to list assignments. The disclosure is internal-only and low value; accept it, or note it in the ADR.

💭 **6. `org_mode` on the `admin` app can still be changed by a super-admin through `PATCH /apps/admin`.** The invariant at service.py:332-337 pins status, join_policy and default_role but not org_mode, and `register` refuses org_mode changes. An org-scoped admin-app assignment is inert today: effective permissions and the super-admin count use app scope only. Still, it would be cleaner to add `org_mode == none` to the admin invariant (ADM-002d).

💭 **7. `build_default_role(actor: uuid.UUID | None = None)` (service.py:240) is the last defaulted actor.** It has no caller today. Make the actor required, or remove the parameter, when USR-05 wires the function up (already in the impl-log follow-ups).

💭 **8. Docs off in production is the safer direction, but it reverses an owner note (spec §6.1, 2026-11-11).** It was adopted as planner default A1. The owner should confirm it, and product-manager should update the spec line. Staging keeps docs on by default; if staging is reachable from the internet, consider `API_DOCS_ENABLED=false` there as well.

## Follow-ups (out of scope)
- ADM-004 audit log next (🟡1).
- A DB-level origin-uniqueness guard (A8), and an optional re-read of `admin:*` after the lock (A7).
- Invalidate email tokens on disable (💭4), and pin the admin app's `org_mode` (💭6).
- Require an explicit `APP_ENV` (🟡2), which could join ADM-002d.

**Verdict: PASS (security).** I found no open escalation path. All of a–o are blocked after the lock and tested for both the refused and the allowed case. The two 🟡s are hardening and process items, not exploitable gaps.
