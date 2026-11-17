# Pushing an app's permission manifest (USR-05b)

An app declares its permissions (and optional extra roles) to org-admin-api by pushing a
**manifest** at deploy time, so adding a permission is a deploy of the app. The call is
authenticated with a per-app **app credential** (R3 class 4, ADR-0016), not with a person's token.
Staff issue the credential: see [deploy.md](deploy.md) "App credentials". This service stores the
catalog and assigns it; it does not interpret it (ADR-0001 section 6).

`PUT /api/v1/apps/{key}/manifest`, header `Authorization: Bearer mmak_<key_id>_<secret>`. The
credential works for the app `{key}` only, on this route only. No CORS: call it from a server or
CI job, never from a browser.

## Manifest format

```json
{
  "permissions": [
    {"key": "myapp:items:read", "description": "Read items"},
    {"key": "myapp:items:write"},
    {"key": "myapp:billing:manage", "is_admin": true}
  ],
  "roles": [
    {"key": "myapp:editor", "name": "Editor", "permissions": ["myapp:items:read", "myapp:items:write"]}
  ],
  "member": {"permissions": ["myapp:items:read"]}
}
```

| Field | Rule |
|---|---|
| `permissions[]` (max 200) | `key` `<app>:<resource>:<action>`; `description` optional (max 500; omitted or `null` keeps the stored one); `is_admin` default `false`; `scope` `app` or `org` |
| `roles[]` (max 50, optional) | extra roles: `key` `<app>:<name>`, `name`, `permissions` (max 200, each declared above) |
| `member` (optional) | override: `<app>:member` holds exactly these (narrows only) |

Organization apps (`org_mode = required`): every key needs `scope`, except the reserved
`<app>:org:create` (always `app`) and `<app>:org:invite` (always `org`) (ADR-0015). Example:

```json
{"permissions": [
  {"key": "calechip:org:create"},
  {"key": "calechip:org:invite"},
  {"key": "calechip:events:manage", "scope": "org"}
]}
```

## Rules

| Rule | Detail |
|---|---|
| Namespace | every key (permissions, role keys, role and member permissions) starts with `<app>:`; otherwise 422 `prefix_mismatch`. `<app>:*` is refused (`wildcard_not_allowed`) |
| Declared only | roles and `member` may only list permissions declared in the same manifest (`undeclared_permission`) |
| `is_admin` | set when the permission is created, inert for the app's own members: never in `<app>:member`, never in a manifest role. It cannot be flipped later (409 `is_admin_mismatch`) |
| `scope` | cannot be changed later (409 `scope_mismatch`); `scope = org` needs an `org_mode = required` app (422 `org_mode_required`) |
| New `scope = app` permission on an org app | 409 `requires_staff`: staff create it once (it reaches every member, ADR-0015) and the next push passes |
| Extra roles | created with `is_system = false`; later pushes only add permissions to them (never remove); `name` is set once. A role that is system, the app's default role or its org owner role is refused (409 `role_not_manifest_managed`); `<app>:member` cannot be listed (422 `reserved_role`) |
| `<app>:member` | rebuilt (all non-admin permissions, app scope on org apps) only when the push creates a permission, or from the `member` override. A rebuild also re-adds a non-admin permission that staff removed from the member role. The override narrows, never widens. 409 `member_is_org_owner` if the member role is the org owner role |
| Removed permissions | never deleted and never taken out of roles; they are reported in `permissions.not_in_manifest` (with `held_by_roles`) for staff to clean up |
| App fields | name, status, origins, default role, org owner role are never touched |

## Pushing at deploy time

Run it from the deploy job only (never on `pull_request` / `pull_request_target`), **before** rolling
out the new app version. Keep the credential in a GitHub Environment secret exposed as an env var;
send it only through a 0600 header file, never on the command line.

```sh
set -eu
umask 077
hdr=$(mktemp)
trap 'rm -f -- "$hdr"' EXIT
printf 'Authorization: Bearer %s\n' "$APP_CREDENTIAL" > "$hdr"
curl -fsS --retry 2 -X PUT \
  -H @"$hdr" -H 'Content-Type: application/json' \
  --data-binary @manifest.json \
  "https://api.mmlabs.online/api/v1/apps/myapp/manifest"
```

`-f` fails the deploy on any non-2xx answer.

## Responses

```json
{
  "app": "myapp",
  "changed": true,
  "permissions": {"added": ["myapp:items:write"], "description_updated": [],
                  "not_in_manifest": [{"key": "myapp:old:read", "held_by_roles": ["myapp:member"]}]},
  "roles": {"added": [], "updated": []},
  "member": {"role_key": "myapp:member", "rebuilt": true, "added": ["myapp:items:write"], "removed": []}
}
```

Pushing the same manifest again is a no-op: `changed: false`, nothing written, no audit row. A
changing push writes one `app.manifest_synced` audit row (actor `app:<key>/<key_id>`).

| Status | Code | Meaning |
|---|---|---|
| 200 | | applied or no-op |
| 401 | `unauthorized` | missing, malformed, unknown, wrong or revoked credential (one identical answer, `WWW-Authenticate: Bearer realm="app"`); an admin JWT is also refused |
| 403 | `app_credential_mismatch` | the credential belongs to another app (audited as `denied`) |
| 403 | `app_disabled` | the app is disabled |
| 409 | `conflict` | `details[].reason`: `is_admin_mismatch`, `scope_mismatch`, `requires_staff`, `role_not_manifest_managed`, `member_is_org_owner` |
| 422 | `unprocessable` / `validation_error` | invalid manifest (`details[].reason`), limits, unknown fields |
| 429 | `rate_limited` | per-IP limit (default 30 per 60 s), counted before authentication; `Retry-After` is set |

An invalid or refused manifest changes nothing. `last_used_at` of the credential is set by each
authentication of a push that was applied or was a no-op.

## Rotation and the role catalog cache

Rotate credentials as described in [deploy.md](deploy.md) (2 active at once). Consumers that cache
`GET /api/v1/apps/{key}/roles` use its `ETag` / `If-None-Match`: the ETag changes exactly when
catalog-visible content changes (roles, their names and permissions); a push that only adds an
`is_admin` permission, or only changes a description, leaves it unchanged.
