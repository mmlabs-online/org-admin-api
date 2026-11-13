# consult-identity-access-engineer — USR-04d

**Findings**
- `get_for_update` (`users/repository.py:21`) has two callers: `PasswordResetService.reset` (`password_reset.py:99`) and `EmailVerificationService.verify` (`email_verification.py:95`). No others. RBAC already uses `key_share=True`.
- `FOR NO KEY UPDATE` conflicts with `FOR SHARE` (login, forgot), `FOR NO KEY UPDATE` and `FOR UPDATE`. It does not conflict with `FOR KEY SHARE`, so the FK check on resend's `email_tokens` insert no longer blocks. The login/reset guarantee holds, and reset and verify still serialize against each other. The later `UPDATE`s on `password_hash` and `email_verified_at` touch no unique columns, so the lock is not upgraded. `email` must stay untouched in these flows.
- After the fix, verify vs resend can still race on the token rows. If resend's `issue()` supersedes token X first, verify's `consume` re-evaluates and gets 0 rows, so the result is 422 `token_invalid`. That is correct (superseded), not a 500.
- `aclose()` just cancels the worker. Queued mail is dropped silently, with no log line.

**Recommendations**
- S1: one line, `.with_for_update(key_share=True)`. Fix the docstring and the test docstring that still say "FOR UPDATE".
- Tests: reuse the `race` fixture, `_bounded` and `_wait_for_lock_waiter`, and get a deterministic interleave by monkeypatching `EmailTokenRepository.issue`.
  - Resend vs reset: pause resend after `invalidate`, before the insert. Start reset and wait until it is a lock waiter (it is blocked on the verify-token rows). Then release resend. Expect 202 for resend and 204 for reset, no 40P01, and the new verify token superseded afterwards.
  - Resend vs verify: same setup. Expect 202, then 422, and the user still unverified.
  - Check that both tests fail with the old `FOR UPDATE`.
- S2: `RESET_CAP_PERCENT = 50` next to the verify 80, each at least 1. This guarantees verify ≥ 50 % and reset ≥ 20 %. Keep the global lock and count under it, and use reset = total − verify. Log `reason=purpose_cap` separately from `daily_cap` so the runbook can tell them apart. Retention: `email_send_log` > 48 h is OK. For tokens, delete only rows with `created_at < now() − 7 d` and used or expired, which is ≥ 24 h.
- Resend: put `_app: AppContextDep` before `DeviceDep`, so a wrong Origin or app returns 403 before any device lookup or cookie rotation. The dependency is cached, so it runs once. Test: valid cookie with no `X-MM-App` → 403 and no `Set-Cookie`.
- Lifespan: add a `closed` flag; `enqueue` after close logs `email_dropped reason=shutdown`. Drain with a bounded timeout (~5 s, below the docker stop grace), then cancel and log `email_dropped reason=shutdown count=N`. Log the count only, no addresses.

**Risks**
- `ASGITransport` tests do not run the lifespan. Test through `app.router.lifespan_context(app)`.
- Cancelling mid-send may lose a message the provider already accepted, or send it twice. That is acceptable but should be documented.

**Open questions**
- Should the 50/80 percentages be env settings (R5) or constants?
- Is a drain timeout of 5 s OK given the uvicorn `timeout_graceful_shutdown` used in deploy?
