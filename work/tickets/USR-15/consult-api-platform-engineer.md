## USR-15 consult (api-platform-engineer)

### Findings
- Join (cookie class) today:
  - 200 `{"status":"member"}`.
  - 202 `{"status":"pending","request_id"}`. A repeat join while a request is pending is idempotent: it returns 202 with the same request (membership/service.py ~157).
  - 403 for refusals. The error classes are in `app/modules/membership/errors.py`: `join_closed`, `join_unavailable`, `join_rejected`, `app_mismatch`, plus `email_unverified`.
  - 429 `rate_limited` is the per-IP/account limiter. It carries `Retry-After` (core/errors.py:111-118).
  - `join_rejected` is deliberately opaque: no reason, decider or date.
  - The join order is: existing pending (202) -> cooldown (403) -> insert. The cap check must go after the idempotent-pending lookup, and ideally after the cooldown check, so a user who already has a pending request never sees the cap error.
- Staff list: `JoinRequestStatusFilter = Literal["pending","approved","rejected"]` (join_requests/schemas.py:7). `JoinRequestRead.status` is a plain `str`. The router defaults the filter to `pending`.
- Join-table docs are at docs/consumers.md ~358-370. They already list 202, 403 `join_*` and 429.
- Config: `JOIN_RATE_LIMIT`, `JOIN_RATE_WINDOW_SECONDS` and `JOIN_REJECT_COOLDOWN_SECONDS` are in `.env.example` (lines 28-31) and `app/core/config.py:68`. The cooldown is `int`, `ge=0`, and 0 means off.
- Cleanup CLI:
  - `python -m app.cli cleanup [--dry-run]` is the email retention command (`EmailRetentionService`, USR-04d). It takes only `--dry-run`, and the parser is strict (`allow_abbrev=False`, `CLEANUP_USAGE`).
  - `audit-purge` takes `--older-than-days` as an ASCII-digit string, validated in code.
  - docs/deploy.md has the "Email retention cleanup (USR-04d)" section at ~885-928. The ops workflow exposes only `cleanup` and `dry_run` as choices; the deployment repo is separate.

### Recommendations
1. **Cap-reached error: 403, with a new code, not 429 or 409.**
   - Suggested name: `join_queue_full`. A `PermissionDeniedError` subclass in `membership/errors.py` fits the existing family. R6 allows only 403 for this kind of refusal, and all other join refusals are 403.
   - Why not 429: that code means "this client sent too much", it is already used by the per-IP limiter, and clients would back off and retry blindly. This is a global per-app state.
   - Why not 409: 409 is used for staff-side state conflicts in `_refusal`, not for the user-facing join.
   - Details: none, like `join_rejected`. Do not leak the cap value or the queue length (enumeration or DoS signal).
   - Retry-After: omit it. The queue drains on staff action and no wait time is knowable. A made-up value would mislead clients. Document it as "try later; contact staff if it persists".
   - Message: neutral, e.g. "Joining this app is not available right now" (same text as `join_rejected`).
   - Any 403 is a new code in an existing status class. The docs say clients must handle unknown 403 codes, so this is additive. Add a docs line saying that.
2. **`not_member.details.join_available`:** consider whether it should turn false while the cap is full. That would make the not_member response consistent with join, as it does for cooldown. It adds a count query to `/token`, and it leaks queue state. I recommend leaving it true (the cap is transient) and noting that in the plan.
3. **`cancelled` is additive for the staff list, with caveats.**
   - Do it in three places: `JoinRequestStatusFilter`, the `JoinRequestStatus` enum, and the DB CHECK constraint if there is one. A constraint change needs an Alembic migration with a working downgrade (R2). Downgrade must refuse or remap if `cancelled` rows exist.
   - Response: `status` is a plain `str`, so a new value is additive. Pre-existing consumers of the default `pending` list are not affected.
   - Do not widen the default filter.
   - Document in the OpenAPI/schema description that clients must tolerate unknown status values. Consider typing `status` as the Literal incl. `cancelled`, which changes the OpenAPI enum.
   - Cancelled rows: `decided_by` is NULL (system cancel, actor is the disabling staff or system). `decided_at` is set. Decide whether `decided_by` should be the staff user who disabled the account. I recommend recording the disabling actor, since the audit row names them anyway.
   - Cancelled is not a rejection, so it must not trigger the cooldown. `rejected_within` is keyed on `rejected`, so this holds.
   - Add an audit action, e.g. `app.join_request_cancelled`, in `audit/actions.py`, one row per cancelled request (or one per user with a count, in the same transaction).
4. **Cap config:** `JOIN_MAX_PENDING_PER_APP` (int, `ge=0`, 0 = unlimited, like the cooldown). Add it to `.env.example` and `config.py`, and thread it through `MembershipService` like `reject_cooldown_seconds`. Count pending rows under the app lock (join already holds it), so no race. `auth/router.py` (line 198) and `organizations/auth_router.py` (lines 56, 83) construct the service with the cooldown and would need the new arg.
5. **Retention CLI: do not overload `cleanup`'s flags.** Either:
   - (a) extend `cleanup` to also purge join requests, reading the age from env (`JOIN_REQUEST_RETENTION_DAYS`, default 90, ASCII-digit validated, a `ge` floor), keeping only `--dry-run`. The ops workflow then needs no change. Or
   - (b) add `--join-requests-older-than-days N` (optional) to `cleanup`.
   Option (a) matches the acceptance criterion ("configurable age") and the workflow choices. Print one more line in the existing format, `join_requests: would delete N (decided, older than Nd, ...)`. Keep the latest `rejected` row per (app, user) while the cooldown applies, i.e. while `decided_at > now() - cooldown`. If the cooldown is 0, nothing needs to be kept. Never delete pending rows. Cancelled rows are purgeable like approved ones. Make sure the retention age is not shorter than the cooldown. Validate this at startup, or use `max(age, cooldown)`, otherwise a short retention silently shortens the cooldown.
6. **Open join lock:** `get_for_share` on the user in the open-join path, in the lock order app -> user -> request (as in the service docstring). The disable-cancels-requests path should take the same order, as `set_status` does.
7. **Docs to update:**
   - docs/consumers.md join table: add `| 403 join_queue_full | ... |`, and the unknown-403-codes note.
   - docs/consumers.md approval paragraph: mention the cap.
   - docs/deploy.md "Email retention cleanup" section: rename it to cover join requests, list the new row line and the env var.
   - `.env.example`: the new keys.
   - Spec §5.3 and the identity-architecture API section if they list join error codes.
   - The staff API docs (status filter now includes `cancelled`).
   - The ticket's tests: 403 cap, a repeat join at the cap still 202, cooldown/cap order, cancel on disable with audit rows, the purge keeping the latest rejection, and the dry run.

### Risks
- Cap scope: per-app pending count lets one abusive actor fill the queue and block legitimate users (a DoS on joining). This is mitigated by the per-IP rate limit, Turnstile on sign-up, and verified email being required. Consider a high default, with 0 = off.
- Retention shorter than the cooldown would silently shorten the cooldown. Keeping only the latest rejection is correct only while the cooldown is active.
- A migration that adds a CHECK value must not be applied to rows from the old image during a rolling deploy. Migrations run as a separate step, so deploy migration first (the old code never writes `cancelled`).
- `cleanup` currently has one purpose (email). Adding join-request purging changes what an operator's scheduled run does, so the summary lines must be clearly separated.
- Locking: cancelling on disable must take the app lock for every affected app (or a documented order), or it can deadlock with approve/join. Apps should be locked in a stable order (e.g. by key or id).

### Open questions
- Cap default and name, and is 0 = unlimited accepted?
- Should `decided_by` on a cancelled row be the disabling staff user or NULL?
- Should `not_member.details.join_available` reflect a full queue?
- Retention age: default (suggested 90 days) and env name; env-driven (a) or flag (b)?
- Should an audit row be one per cancelled request or one aggregate per disable?
- Should a re-enabled user's cancelled requests be restorable? I recommend not: they must join again.
