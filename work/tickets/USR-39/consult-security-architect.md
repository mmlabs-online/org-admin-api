# Consult — security-architect — USR-39 (2026-11-26)

**Findings**
1. Mint branch at `app/modules/tokens/service.py:133-134`; lines 118-126 already treat the app-scope role as app membership.
2. Nothing in the mint path is per account: only per-IP `rate_limit("token")` (`auth/router.py:178`, `token_rate_limit=60`, `config.py:65`). Per-user limiter to copy: `RateLimit(profile_user_rate_limit…)` (`users/auth_router.py:44-50`).
3. Recent re-auth cannot be built from existing data: `upsert_account` (`sessions/repository.py:188-197`) keeps `account_sessions.created_at` on re-sign-in and only updates `last_used_at`; a "password within N minutes" check needs a new `authenticated_at` column (migration).
4. Device sessions are long (`device_session_idle_days=30`, `max_days=365`, `config.py:161-162`): a stolen staff `mm_device` cookie can mint cross-org tokens for up to a year, limited only per IP.
5. Audit plumbing fits: `AuditLogger.record` (`audit/service.py:130-154`) flushes in the request transaction, committed before the response (`app/db/session.py:27-32`); failed audit → failed request, no unaudited token.
6. Key-name trap: `_FORBIDDEN_KEY` (`audit/service.py:28`) blocks `changes` keys containing `token`, `email`, …; `token_ttl` or `access_token_jti` → `ValueError` → 500.
7. `AuditTarget.ORGANIZATION` exists (`audit/actions.py:19`); add `TOKEN_SUPER_ADMIN_ORG_ISSUED` to `AuditAction` (~line 37).
8. `jti` generated after claims (`service.py:148`): audit write after signing, still inside `issue`.

**Recommendations**
1. Constant opt-in claim `org_access: "super_admin"`, never on a member token. Re-read designation, role and `admin:super-admin` in the mint transaction. Any failure → existing `OrgAccessDeniedError(details={})`.
2. Audit `record(TOKEN_SUPER_ADMIN_ORG_ISSUED, actor=EndUser(user.id), target=(ORGANIZATION, org_id), app_key, org_id, changes={"jti", "account_session_id", "role_key", "expires_at"})`; no email or name; IP/UA/request id via `AuditContextDep` (the token route must start taking it).
3. Per-account limit on super-admin mints only: `SUPER_ADMIN_ORG_MINT_USER_RATE_LIMIT` (30) and `…_WINDOW_SECONDS` (3600), keyed on `user.id`, applied after the holder branch is chosen.
4. TTL `super_admin_org_token_ttl_seconds` `ge=60`, default 3600, validator rejects > `access_token_ttl_seconds`; `.env.example`; `expires_in` from the branch taken.
5. No refused-mint audit for non-holders; `logger.warning` (no email) when a former holder is refused (designation, role or `admin:super-admin` gone).
6. `docs/consumers.md`: grant super-admin powers only when `org` present AND `org_access == "super_admin"` AND `roles` contains `<app>:super-admin`; never infer from empty `org_roles`; unknown `org_access` = deny; log `sub` and `jti` for staff actions; show staff in the UI.
7. Tests: claim snapshot; audit without email; no other app's role in the token; 429 on the limit; mint blocked right after removal of role, designation or `admin:super-admin`; member path wins.

**Risks**
- XSS on an app origin (High): script can call `/token` with the staff cookie, `Origin` and `X-MM-App` and open every org. Mitigations: 1 h TTL, per-account limit, audit row. Real fix: staff MFA or a separate staff account (OQ3).
- Stolen device session (High): up to 365 days of minting; revocation of the role stops it only if noticed.
- Revocation lag (Medium): live tokens until TTL; no `sid`, no deny-list.
- Consumer contract (Medium): consumers checking only `roles` or a truthy `org_access` could over-grant; simap / `mmlabs-auth` changes merged before this ticket could break.
- Audit retention: the purge job may delete mint rows before an incident review.

**Open questions for Ming (defaults)**
1. Per-account mint limit — yes, 30/h, 429 `rate_limited`.
2. Recent re-auth — not in USR-39 (needs `authenticated_at` migration); follow-up + staff MFA (OQ3).
3. Shorter device session for holders — no for v1; document a separate staff account.
4. TTL — 3600 s, `SUPER_ADMIN_ORG_TOKEN_TTL_SECONDS`.
5. Audit refused mints by former holders — log only.
6. Exempt mint rows from audit purge — no; confirm retention is long enough.
7. Consumer check — all three conditions; unknown `org_access` = deny.
