# consult-security-architect — USR-02b

## Findings
1. **The "APP_ENV must be explicit" rule is implementable as planned.** Tested with pydantic-settings and `env_ignore_empty=True`: `"app_env" in self.model_fields_set` is False when `APP_ENV` is unset or empty, and True when it comes from the environment, `.env`, or a constructor kwarg. `.dockerignore` excludes `.env`, so in the container only the real environment counts; a missing `APP_ENV` with a flag on fails at import (`app = create_app()`) → fails closed. Existing tests are unaffected: `tests/conftest.py:11` sets `APP_ENV=test`, and `Settings(admin_users_api_enabled=True)` (`test_users_admin.py:305`) still passes. Caveat: `model_copy(update=...)` skips validators — nothing must build a flagged `Settings` that way.
2. **02b creates persistent privileges even without an assignments endpoint.** `PATCH /apps/admin {join_policy:"open", default_role_id:<admin:super-admin id>}` is accepted by the contract; once USR-03/USR-04 join ships, every verified user joining `admin` gets super-admin. Same for any app: a default role holding `is_admin` permissions or `<app>:*` (via `PUT …/roles/{id}/permissions`) turns open join into admin. The contract protects `admin` only against delete/re-key; it doesn't block `status=disabled` on `admin` (would lock out the admin API).
3. **Origins in the DB become credentialed CORS trust in USR-03 (S4/D12).** Anything stored in `apps.origins` while the flag is on, plus any http-loopback entry, is trusted later. The write path never runs in staging/production (flag refused there), so a write-time env check for `http://localhost` adds nothing; the real control must be at read time in USR-03.
4. **The catalog GET is low-sensitivity.** Keeping it flag-gated (Q1 default) costs nothing — no consumer exists before USR-05.

## Recommendations
- **AC1 config:** merge the gates into one `model_validator` covering both flags: refuse staging/production; refuse when a flag is on and `"app_env" not in self.model_fields_set`. Test with `monkeypatch.delenv("APP_ENV")` and `Settings(_env_file=None, admin_rbac_api_enabled=True)` — without `_env_file=None`, a developer's `.env` supplies `APP_ENV` and the test passes for the wrong reason. Also test an empty `APP_ENV=` is refused. Mount only in `create_app`, never in `app/api/router.py`.
- **AC4 escalation guards in the service (422/409, with tests):**
  - `admin` app: `join_policy` fixed at `closed`, `default_role_id` must stay NULL, `status` must stay `active` (409).
  - Any app: `default_role_id` may not point to a role holding an `is_admin` permission; `PUT` may not add an `is_admin` permission to the app's current default role; `PATCH permission is_admin=true` on a permission the default role holds → 409 (or remove it from the role in the same transaction).
  - `# ADM-002 guard` comments where `actor` will require `admin:*`: `admin` app origins, `<app>:*`/`is_admin` grants to roles, `is_admin` changes.
- **AC4 origins:** lowercase, then full-match a strict pattern before parsing: `https://` + LDH labels + optional `:1-65535`, or `http://(localhost|127.0.0.1|[::1])(:port)?`. **Reject non-ASCII hosts** rather than IDNA-converting (Python's built-in codec is IDNA2003; rejecting avoids homograph/normalization mismatches with no new dependency). Reject trailing dot, `%`, `\`, whitespace, userinfo, path other than a lone `/`, query, fragment, `*`, `null`, non-loopback IP literals. Drop default ports (`:443`; `:80` for http — browsers never send them). Dedupe after normalizing; ≤ 20 entries, each ≤ 255 chars. Value for USR-03: exact string equality against the browser's `Origin` becomes sound.
- **AC5 catalog:** keep it flag-gated; decide now that `admin` returns 404 (like unknown/disabled apps) so making it public later (Q1) isn't a breaking change. Expose only key, name, `is_system`, permission keys.
- **R5:** `ADMIN_RBAC_API_ENABLED=` (no value) in `.env.example`.

## Risks
- A local/test instance on the tunnel with the flag on is a full escalation and origin-injection path (the data doesn't carry over — the flag is refused in production).
- USR-03 follow-ups: drop `http://` origins when `app_env` is staging/production; bind `Origin` to the app named by `X-MM-App`/audience, not the union of all apps' origins (otherwise one app's origin could mint tokens for another audience).
- ADM-002 release checklist: review `apps.origins`, `default_role_id`, `join_policy` and `is_admin` on every app before removing the flag.
- Informational: on a refused flag, the startup `ValidationError` prints a truncated `input_value` dict; today the visible start/end don't reach secrets, but key order isn't guaranteed. A later ticket could raise a plain `RuntimeError` after construction instead.

## Open questions
1. Q1: public catalog (R3 class 3) or wait for the USR-05 app credential? Recommend waiting; if public later, add a per-IP limit and `Cache-Control: public, max-age=60`.
2. Q3: recommend a configured suffix allowlist (`APP_ORIGIN_SUFFIXES`, e.g. `.mmlabs.online`) plus loopback in local/test — under §7 (same-site, `SameSite=Lax`) an off-site origin can't use the cookie anyway, so the restriction costs nothing.
3. Should changes to `admin` app origins require `admin:*` (super-admin) rather than `admin:apps:write` once ADM-002 lands?
