# ADR-0025: Org members read scope (app-facing member list)

Status: Proposed (accepted when the owner merges USR-29)
Date: 2026-11-21

Amends ADR-0016 §1 (allow-list of routes), ADR-0021 §1/§3 (scope set, rate limits) and ADR-0022 §1/§10 (runtime scopes).

## Context
simap (`org_mode = required`) must show the people of an organization, including those who have
not opened the app yet. Only this service knows them. The spec has no app-facing member read:
§5.7 lists "Org members view" as Later and §8 Q4 says "no pull endpoint and no app-credential
widening for now". The owner approved this widening at the USR-29 plan gate (batch 2026-11-21-1,
all defaults) and the spec carries dated lines for it.

## Decision

1. **Scope.** App credential scope `org-members:read`, one scope per key (ADR-0021 §1), at most 2
   active per (app, scope). The CHECK `ck_app_credentials_scope_valid` lists it; ADM-042 recreates
   the CHECK from this list. Refused for the `admin` app.
2. **Route.** `GET /api/v1/apps/{key}/orgs/{org_id}/members`, R3 class 4 (a): the credential's app
   must equal `{key}` (403 `app_credential_mismatch`), wrong scope 403 `app_credential_scope`.
   Server-to-server only, no CORS. The key is `MMLABS_ORG_MEMBERS_CREDENTIAL` in the app's backend
   deploy environment only: never CI, never browser or mobile code, never the value of a CI scope.
3. **Runtime path.** No `lock_app`. `last_used_at` is written by a conditional, throttled UPDATE
   (at most once per 60 s, never on a revoked row). A request already in flight during a revoke
   may complete (accepted, as in ADR-0022 §3). `RUNTIME_SCOPES` (in `models.py`) holds the runtime
   scopes; `RequireAppCredential` refuses `lock=True` for them. On the 404 path the failure audit
   rolls back the request's pending work, including that call's touch (accepted).
4. **Rate limits.** Per IP before auth (default 120 per 60 s) and per credential after auth
   (default 60 per 60 s, `enforce_subject_limit`), both in config. In-process: with N workers the
   effective limit is N times the setting (ADR-0002).
5. **Relation rule.** An org is "related" to the app when at least one non-expired org-scoped role
   of the app is held in it and the org is active. Entitlements are not consulted. Unknown,
   unrelated and suspended orgs give the same 404 `org_access_denied` (no existence oracle). The
   cookie-class routes answer 403 with the same code, because there the caller is a signed-in user
   and the ticket for this route asked for 404; both are documented in `docs/consumers.md`.
   `org_mode = none` gives 422 `org_not_supported`.
6. **Fields.** `user_id` (= token `sub`), `email`, `email_verified`, `display_name`, `status`,
   `org_roles` (full role keys of the app held in that org, non-expired, sorted), `joined_at`
   (earliest creation among those assignments; not "joined the org"). A disabled user is listed
   with `email` and `display_name` null. Order: `user_id` (immutable and unique, so pages are
   stable). `Page[T]`, `limit` <= 100, `Cache-Control: no-store`.
7. **`updated_since`.** Additive only: users whose `updated_at` is newer, or who received a newer
   active grant of the app in that org. Removals and expiry are invisible to it; consumers run a
   full read at least daily. No tombstones.
8. **Audit.** One row per call (`organization.members_read`, actor `app:{key}/{key_id}`) with
   counts and filter flags only, plus a denied row for each 404. Never emails, names or the body.
   Logs carry `key_id` and `org_id` only.
9. **Blast radius.** A leaked key exposes the emails of every org member of that app (PII, spec
   §8 Q6). It cannot write anything. Revoke and re-issue; consumers store only what they need.

## Rejected alternatives
- A `name` claim in the access token alone: does not cover people who have not opened the app.
- Relating an org to the app through `org_entitlements`: adds a second source of truth.
- A multi-scope key: breaks the one-scope rule of ADR-0021.
- A keyset cursor: `Page[T]` with `limit`/`offset` is the project convention (R9).
- Tombstones for removals: out of scope; see USR-30.
