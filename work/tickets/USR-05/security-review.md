## Attempt 1

## Security review: USR-05 slice 05a (app credentials, staff issue/list/revoke)

**Verdict: PASS.** There are no 🔴 blockers. One 🟡 should-fix (a runbook hardening, not a code bug) and a few 💭 notes. I did not run `make check` or pytest, as instructed; the reviewer owns the green-check gate (R8).

Scope reviewed: `git diff origin/main...HEAD` on branch `claude/blissful-wozniak-fx4lm9`, commit 0e60059. That covers `app/modules/app_credentials/*`, the migration `f3a8d51c9e27`, the audit actions, `main.py`, the CLAUDE.md R3/R9 edits, the `docs/deploy.md` runbook and the new and updated tests.

### What I checked against your list
- **Secret generation:** `codec.py:17-18` uses `secrets.token_hex(8)` for the key_id and `secrets.token_urlsafe(32)` for a 256-bit secret, both from the CSPRNG.
- **Format parsing:** `codec.py:12,23` uses a strict anchored regex with `\Z`, so a trailing newline is rejected, and the length is capped at 128 before matching. No ReDoS risk.
- **Hashing:** only SHA-256(secret) is stored. The CHECK enforces 32 bytes on `bytea`. The constant-time comparison (`compare_digest`) is slice 2, since no route accepts credentials yet.
- **Secret never leaves except once:**
  - The raw value exists only in `IssuedCredential` (`service.py:113`) and the POST body.
  - `__repr__` leaves out the hash (`models.py:46`).
  - `AppCredentialRead` has no secret or hash field, so the list endpoint is metadata only.
  - The audit `changes` holds only `key_id` (hex, which passes `_FORBIDDEN_KEY`) and `revoked_at`.
  - The service and router contain no logging.
  - Tests check the list body and audit columns for the raw value, the secret and the hash hex (`test_app_credentials_api.py:64-68`, `test_audit_rows_never_contain_credential`).
- **Cache-Control:** `no-store` is set on the 201 response (`router.py:40`) and asserted at `test_app_credentials_api.py:54`.
- **`admin:*` guard and denied audit:**
  - `require_super` runs after the lock and before any write (`service.py:79-85`).
  - `record_denied` rolls back and then commits the denied row alone (`audit/service.py:182`).
  - The test asserts a 403 `admin_wildcard_required`, no credential row and one `denied` row.
  - Revoke needs only `admin:apps:write`, by documented design (it only removes access).
- **Admin app and disabled app:** both get a 409 (`service.py:86-89`), checked after the super check. Both are tested.
- **The 2-active cap under concurrency:**
  - `lock_app` takes `SELECT … FOR NO KEY UPDATE` on the app row (`rbac/repository.py:75-80`). Two concurrent issuers conflict on that lock, so count-then-insert is serialised.
  - The FK `KEY SHARE` taken by the insert does not conflict with that lock, so there is no self-deadlock.
  - The lock is released on commit, or on the rollback inside `record_failure`.
  - There is no race test; the plan accepted that.
- **IDOR on revoke:** `repo.get` filters on both `id` and `app_id` (`repository.py:45-50`). The conditional UPDATE then runs on the id that was just checked, under the same app lock. A foreign credential gets a 404 and is left unchanged, which is tested (`test_revoke_unknown_or_foreign_credential_404`). Revoking works on disabled apps too, because `lock_app` has no status filter. That is the correct fail-safe for a leaked key.
- **Migration:**
  - The table, FKs, CHECKs and indexes match the model; the CHECK texts are identical.
  - `downgrade()` drops both indexes and the table.
  - The migration is expand-only.
  - FK behaviour: `app_id ON DELETE CASCADE`, `created_by ON DELETE SET NULL`.
  - The downgrade/upgrade round trip is tested.
- **Route auth classes:**
  - All 3 routes are Bearer (`current_admin` on the router) plus one `require_permission` each (`admin:apps:write` / `admin:apps:read` / `admin:apps:write`).
  - They are added to `BEARER` in `tests/test_route_auth_classes.py:80-82`, and `credential_id` is added to `_fill`, so the existing 401 sweep and OpenAPI checks cover them.
  - No route accepts an `mmak_` credential in this slice, so auth class 4 exists only on paper. That is correct.
- **CLAUDE.md:** the diff changes only R3 (class 4) and R9 (the manifest exception), with the planned wording. These are owner-approval edits; they are not approved until merged.

### Findings

🟡 **should-fix: the issue runbook can silently leave an empty or wrong secret file.** `docs/deploy.md` (new section "App credentials (USR-05a)", the `curl … | python3 … > "$cred"` line). The pipeline has no `set -o pipefail` and no check afterwards.
- **Risk:** if python fails after curl succeeds, the operator sees an error on stderr but may still copy the empty file. If curl fails (`-f`), python errors out and leaves the file empty. Either way an empty secret can be pasted into the GitHub Environment secret. Worse, a credential can be issued (and count towards the 2-active cap) while its value is lost, and the operator may not notice it needs revoking.
- **Fix:** add `set -o pipefail` (or confirm the admin subshell already sets it) and a guard after the pipeline: `[ -s "$cred" ] || { echo "issue failed; list credentials and revoke any orphan key_id" >&2; rm -f -- "$cred"; exit 1; }`. Also add one line saying that if the value is lost, staff should revoke that `key_id` straight away.

💭 **note: `assert` on own output** (`service.py:96-97`). Under `python -O` the assert disappears, and `parsed[1]` would raise a `TypeError` if `parse_credential` ever returned `None`. The codec makes that impossible today, so there is no security impact. Optional tidy-up: have `new_credential()` return `(key_id, secret, raw)` and drop the re-parse.

💭 **note: key_id collision.** 64-bit `key_id` with a UNIQUE constraint. A collision is negligible but would surface as an unhandled IntegrityError (500). It is not exploitable and needs no action in v1.

💭 **note: 404 comes before the denied audit for unknown apps** (`service.py:78`). A non-super holder of `admin:apps:write` can tell existing app keys from unknown ones. They can already list apps with `admin:apps:read`, so nothing new is disclosed.

💭 **note: `no-store` is set only on the 201 response.** Error responses carry no secret, so this is fine. Adding `Pragma: no-cache` is optional for legacy proxies and is not needed.

### Follow-ups (out of scope, R7)
- Carry into USR-05b, already in the plan: `hmac.compare_digest` on the hash, the single 401 for missing, malformed, unknown, wrong-secret or revoked credentials, the per-IP rate limit before parsing, and `last_used_at`. Add a test that an `mmak_` value sent to any Bearer route returns 401, and that an admin JWT on the manifest route returns 401.
- A concurrency test for the 2-active cap: two parallel POSTs on separate connections, expecting exactly one 201 and one 409.
- Optional `expires_at` and `label` on credentials. The plan already lists these.

**Final verdict: PASS (security). No blockers; one 🟡 runbook hardening fix in `/home/user/org-admin-api/docs/deploy.md` that can go in this rework or as a follow-up.**
