# consult-security-architect — ADM-002b

**Findings**

The guards are comments only today, at `rbac/service.py` lines 296, 574, 610, 671, 690, 744, 878 and 929. Routers do not pass an actor (`actor=None` everywhere), and `UserService.set_status` has no guards. Public signup gives anyone sock-puppet accounts, so a "no self-grant" rule on its own closes nothing. The real boundary has to be **`admin:*` on the sensitive objects**.

Escalation paths that work once the routers are mounted, by leaf permission:

- **`admin:roles:manage`**
  - (a) Run PUT role-permissions on an admin-app role the actor holds and add `admin:*` or `admin:roles:assign`.
  - (b) Create or PATCH admin-app permissions or roles.
  - (c) PUT an is_admin permission such as `stoktok:*` into a role that already has assignments. This is the USR-02d member escalation.
  - (d) PATCH `is_admin` true→false on an app-admin permission, which makes it eligible for the default role. Or PATCH false→true on a permission held by an assigned role, which promotes every holder silently. Today only the default-role case is refused.
  - (e) Delete or rename admin-app roles and permissions.
- **`admin:roles:assign`**
  - (f) Grant `admin:super-admin` or any admin-app role to self or to a sock puppet.
  - (g) Grant a role holding `<app>:*` or another is_admin permission, which makes the grantee an admin of another app.
  - (h) Revoke other admins, including super-admins down to the last one.
  - (i) The expired-duplicate renewal branch in `_grant_locked` is a second grant path, and it needs the same guard.
- **`admin:apps:write`**
  - (j) PATCH the `admin` app's origins to an origin the actor controls, which phishes admin tokens. join_policy, default role and status on `admin` are already hard-refused.
  - (k) Create or PATCH any app with an origin another app holds, including `admin`.
- **`admin:users:write`**
  - (l) **Re-enable a disabled super-admin or staff account.** Assignments survive disable, so re-enabling restores the privilege. The ticket does not list this path.
  - (m) Disable super-admins until one is left, a privilege-inversion DoS.
  - (n) Disable self.
  - (o) Disable without revoking device sessions.
- **Last-super-admin count.** `_counting_super_admin` counts users that are active but have an unverified email. Such a user cannot mint a token, so the guard can be satisfied by a super-admin who cannot log in.

**Recommendations** (the minimal rule set; `is_super = "admin:*" in actor.perms`)

- **G0.** Routers pass an `Actor(id, perms: frozenset)` built from `current_admin` into services, satisfying R1. Every guard is decided after `lock_app`.
- **G1.** Any write that touches the `admin` app requires is_super. That covers app PATCH including origins, permission and role create/update/delete, role-permission PUT, and grant/revoke of an admin-app role. Closes a, b, e, f, h, j.
- **G2.** On any app, these require is_super:
  - creating an is_admin or `<app>:*` permission;
  - any change to `is_admin`;
  - a role-permission PUT whose **diff adds or removes** an is_admin permission;
  - grant or revoke of a role that holds an is_admin permission, checked under the lock.

  Closes c, d, g.
- **G3. Invariants that apply even to super-admins:**
  - `admin:*` may sit only on `admin:super-admin`;
  - adding an is_admin permission to a role with assignments → 409;
  - **flipping `is_admin` false→true on a permission held by an assigned role → 409**. This is the same escalation by another path.
- **G4.** No self grant or self revoke of admin-app roles, and no self status change. `granted_by = actor.id` on both the insert and the renewal branch (i).
- **G5. User status:**
  - Disable **or enable** of a user who holds any admin-app assignment or an is_admin-holding role requires is_super. Closes l and m.
  - Lock order is `lock_app("admin")` → user `FOR UPDATE`, the same as the bootstrap.
  - Last-super-admin guard; the count should also require `email_verified_at IS NOT NULL`.
  - Revoke all of the user's device sessions in the same transaction.
- **G6.** App create and update refuse origins held by any other app, whatever its status. Reuse the normalization and validation from `set_admin_origins`: D19, apex refusal, and loopback refusal in production.

**Tests required before the flags go:**

- Every guard site: refused for a leaf holder (403, nothing written) and allowed for super. Include the renewal branch, both diff directions of the PUT, and both `is_admin` flip directions.
- A sock-puppet scenario: a `roles:assign` holder granting admin roles to another user.
- The re-enable case.
- Self grant, self revoke and self disable.
- Last super-admin under a concurrent disable/revoke, with at least one serialized pair.
- `admin:*` on a role other than super-admin; the 409s from G3.
- Origin collision with `admin`.
- Session revocation on disable.
- The route-enumeration test passes with the flags removed.

**`/docs` and `/openapi.json`.** Recommend turning both off in production (`docs_url=None, openapi_url=None` when `app_env == "production"`) and keeping them in local and staging. Once the routers are mounted, the schema maps the whole admin surface and lists each `x-permission`, and nothing needs it at runtime. The admin UI can pull the schema from staging or CI. This needs the owner's decision: it reverses the 2026-11-11 note in spec §6.1 and the comment in `main.py`.

**Pre-removal audit** (read-only; run in local, staging and prod, output to impl-log with emails masked). The flags were refused in staging and production, so prod should be clean by construction; record a baseline anyway.

1. Admin-app assignments: `role_assignments ⋈ roles ⋈ apps WHERE apps.key='admin'`, with role key, `granted_by`, `created_at` and `expires_at`. Flag rows where `granted_by = user_id`, and rows where `granted_by IS NULL` beyond the bootstrap.
2. Admin-app permissions that are not among the 9 seeded keys or have `is_admin = false`. Admin-app roles that are not seeded, with their permissions. Any role other than super-admin holding `admin:*`.
3. Every role holding an is_admin permission that has assignments. Every default role holding an is_admin permission, which should be none.
4. `admin` app state: `status`, `join_policy`, `default_role_id` and `origins`. Plus any origin held by more than one app (`unnest(origins) GROUP BY HAVING count > 1`).
5. Users who are disabled but still hold admin-app assignments (re-enable candidates).

**Split out** into an ADM-002d hardening/docs ticket. None of these gates the flag removal:

- `strict_aud`;
- the seed `DO UPDATE`;
- the deploy.md notes (`JWT_PUBLIC_KEYS_EXTRA`, token kept out of shell history, trap/logout);
- `--clear` on a drifted row (lower urgency once G1 lands, though it is still break-glass);
- refusing signup and forgot-password for `admin`.

**Risks**

- There is no audit log until ADM-004 lands (R4), so misuse of a guard cannot be reconstructed. That argues for prioritizing ADM-004 straight after this ticket.
- The actor's permissions are read before the app lock. A concurrent revocation of `admin:*` slips through for one request. This is acceptable; document it.
- The cross-app origin uniqueness check races, because each app is locked on its own row. Prod is serialized by the deploy concurrency group, but a DB-level guard is still a follow-up.
- Access tokens that consumer apps already hold stay valid for up to 15 minutes after a disable.
- A large diff in a P1 security ticket: the reviewer needs per-site test mapping.

**Open questions**

1. Should the API refuse writes to admin origins entirely, keeping them CLI-only per the ADR-0008 amendment, or allow them for is_super?
2. Does G5 (super-only status change for staff users, including re-enable) meet the owner's intent?
3. Should G2 also cover *removing* is_admin permissions from a role (demoting another app's admins), or is that allowed for `roles:manage`?
4. Should the super-admin count require a verified email?
5. Owner sign-off on turning off `/docs` and `/openapi.json` in production.
