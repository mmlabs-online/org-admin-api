# Consult — security-architect — USR-29 (2026-11-21)

### Findings
1. **Blast radius of a leaked key.** A leaked member-read key would let anyone walk every org of that app (if they have or can guess the org ids) and copy out `email`, `display_name`, verification state and roles. That is a full staff directory of each customer org, which is ideal material for phishing ("your simap Manager asked you to…") and credential stuffing against `/auth/login`. The key can't write anything: one scope per key and the scope is checked (`app/modules/app_credentials/service.py:188-191`). But there is no per-key ceiling today. `app/core/rate_limit.py:142-148` counts per IP only (`ip_key(client_ip…)`), and the per-key bucket described in ADR-0022 §3 (`docs/adr/0022-usage-key-scopes.md:82`) has not been built yet. A leaked key used from many IPs is therefore not limited at all.
2. **Org enumeration.** Org ids are UUIDv4, so guessing them is not practical. The real exposure is ids the app already knows (from token claims or its own DB). So the important control is the "related to {key}" filter, not keeping the UUID secret.
3. **"Related" means two different things in the data.** `org_entitlements` (`app/modules/entitlements/models.py:24-44`, unique `(org_id, app_id)`) is an explicit link meaning the org has the right to use the app. "Has at least one member with a `{key}` org role" is implicit. The repo already uses the implicit rule for a user (`app/modules/organizations/repository.py:111-120`).
4. **The USR-23 query returns assignments, not people.** A user with two roles appears twice (`app/modules/organizations/router.py:177-178`). That query also has no app scoping by default and a tri-state `active` filter. Reusing it as-is would leak other apps' roles or produce duplicate rows.
5. **The auth check takes a lock and writes on every call.** `authenticate(lock=True)` → `confirm` → `lock_app` + `touch_last_used` (`service.py:192-218`). That takes a row lock on `apps` for a read route that apps may poll often (incremental sync), so it is both a contention and a DoS lever.
6. **No-store precedent exists.** USR-23 already sets `Cache-Control: no-store` (`router.py:198`). Denials are already logged with `key_id` only (`service.py:41,181-189`), which is good.

### Recommendations
- **R3 / ADR-0021:** add a new scope `org-members:read`, refused for the `admin` app (like the runtime scopes). Widen the CHECK at `app/modules/app_credentials/models.py:32` in a migration with a working downgrade (R2). Only the new route goes in `APP_CREDENTIAL` (`tests/test_route_auth_classes.py:65`).
- **Rate limit:** per IP before auth, and **per key_id after auth** (for example 60/min, set from config per R5). Build the generic per-key hook once in `app/core/rate_limit.py` (High path) so ADM-042 can reuse it. Return 429 with `Retry-After`.
- **Lock:** call `authenticate(..., lock=False)`. For a read, re-check the app status without `FOR UPDATE` and throttle the `last_used_at` touch (at most once a minute). Note the deviation from ADR-0021 §1 in the ADR amendment.
- **"Related" check:** default to the implicit rule — at least one non-expired `{key}` org role in an `active` org. Run it in one query before paging, and return a uniform 404 `org_access_denied` with the same code path for unknown, unrelated and suspended orgs. Optionally also require an `org_entitlements` row where entitlements are used.
- **Field minimisation:** keep `email` (seat assignment needs a stable label people recognise), but make it opt-in with `?fields=email` or a manifest flag. Default to `user_id`, `display_name`, `status`, `org_roles`, `joined_at`. Drop `email_verified`: joining already requires a verified email (R11), so for listed users it is always true.
- **Disabled users:** list them with `status=disabled` but blank `email` and `display_name`. The app only needs the id to revoke seats.
- **Audit:** one row per call, action `org_members.read`, actor `SystemActor("app:{key}/{key_id}")`, `changes={org_id, count, offset, filters}`. Add the action to the audit coverage registry, and write denied rows for mismatch and scope (already done at `service.py:186,190`). The volume is fine with the per-key cap. The rows are what let staff spot a scraping pattern (many distinct org_ids per key per hour). Add a follow-up to alert on that.
- **Logging:** no email or body in logs. Add a test that captures `caplog` and asserts no `@` appears. Validate `updated_since` as a typed datetime, and don't echo the value back in 422 errors.
- **No CORS:** add a test that an `Origin` header gets no `Access-Control-Allow-Origin`.
- **Docs:** in `docs/consumers.md`, require server-side storage of the key and rotation on staff off-boarding.

### Risks
- PII spreads into each app's backend DB. That needs a §8 Q6 note: apps must not cache emails beyond what they need.
- `updated_since` shows when people join or leave, which is minor extra metadata.
- If an attacker obtains a staff JWT, nothing changes here: that is a 401 on this route by design.

### Open questions for Ming (defaults in brackets)
1. Should "related" mean any non-expired `{key}` org role, or require an `org_entitlements` row? [role-based; entitlement optional later]
2. Should `email` be returned by default or opt-in? [opt-in with `fields=email`]
3. For disabled users, include email and name? [no, id and status only]
4. Per-key limit? [60 req/min, 1000/day]
5. Pending invitations in v1? [follow-up ticket]
6. Add a spec §8 line on PII handling by consuming apps? [yes]
