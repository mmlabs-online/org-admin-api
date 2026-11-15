# consult-security-architect — ADM-002c

**Findings**

1. The trust chain holds today. `TokenService.issue` requires `audience == X-MM-App` (`tokens/service.py:55`), and the Origin check is per app. CORS echoes the origin of any active app (ADR-0006 §consequences), but CSRF still returns 403 when the Origin does not belong to the named app. So `apps[admin].origins` is the only thing that decides where an `aud=admin` token can be minted with the shared `__Host-mm_device` cookie. Script execution on an admin origin (XSS, a compromised dependency, or a dangling DNS record taken over) can mint admin tokens for any staff member signed in on that browser, because the device session is shared across apps.
2. The Origin check is CSRF protection, not authentication. With no admin UI, step 5 of the runbook in practice means curl with a forged `Origin: https://<admin-host>`. That is fine: a reserved host that serves nothing has no XSS surface.
3. Existing validation already covers part of the brief. `normalize_origin` (D19) allows only https on mmlabs hosts at the default port, never `api.mmlabs.online` or its subdomains, and has no wildcard syntax. `allowed_origins` drops loopback in staging/production. `app_keys_holding_origins` checks apps of any status, `admin` included. So `register-app` already refuses to take an origin held by `admin`, and the reverse check only needs `exclude_key="admin"`.
4. Nothing in the database stops two apps holding the same origin (no unique constraint on `apps.origins` elements). Two writes running at the same moment could both take one origin. The risk is low because ops runs are serialized by an approval step.
5. `AppService.update` (RBAC API, behind a flag) can already change admin origins. The `admin:*` requirement there is only a comment (`service.py:272`), the same gap as 🟡3 in ADM-002.

**Recommendations**

- **R1 – Write only origins.** `set_admin_origins` locks the admin row (`lock_app`) and writes only `origins`. It never touches roles, permissions or assignments. It **refuses (fails closed) rather than repairs** if the seeded row has drifted: status must be `active`, `join_policy=closed`, `org_mode=none`, `default_role_id IS NULL`. Error: "admin app not in seeded state", exit 1. Tests cover each drift case.
- **R2 – Admin-specific origin policy, on top of D19 and enforced in the service, not only the CLI:**
  - Refuse the apex `https://mmlabs.online`, since it is a shared site likely to run third-party scripts.
  - Refuse any origin held by another app, of any status.
  - Refuse loopback in staging/production.
  - Allow at most 2 origins.
  - Document that the admin origin must be a **dedicated host** (e.g. `admin.mmlabs.online`) that serves no other app and, until an admin UI exists, either has no DNS record or points at an owner-controlled tunnel route. Never reuse an existing app host.
- **R3 – Lifecycle.**
  - Support `--clear` as the emergency kill switch: an empty origin list means no new admin tokens within 60 s. Tokens already issued live up to the TTL.
  - "Replace the set" semantics. A re-run with the same set prints `unchanged`.
  - Print `+ origin` / `- origin` lines and the 60 s CORS note, as `register-app` does.
- **R4 – Operator path.** The ops workflow command `set-admin-origins` runs in the `org-admin-api-ops` environment (owner as required reviewer, `main` only), with `dry_run` on by default, plus a workflow `concurrency` group shared with `register-app` (covers Finding 4). The run (actor, inputs, summary) is the audit trail until ADM-004 (R4). Note in the code that an audit entry is required once ADM-004 lands.
- **R5 – Non-production.** Do not refuse to run outside production. The real risk is loopback in staging/production, and that is already refused. Refusing in dev only hurts local testing. The env check stays in the service (`app_env`), as in `register`.
- **R6 – Errors and output.** Use fixed lines (R5/R11) that never echo the submitted origin on refusal. Reuse `_REASONS`, adding `admin_not_seeded` and `apex_not_allowed`. Parse through a schema that uses `normalize_origins`.
- **R7 – Keep the existing refusal.** `register-app --key admin` keeps its refusal. `set-admin-origins` is the only path.
- **Tests:** in addition to the ticket's acceptance criteria, cover the apex being refused, a third origin being refused, `--clear`, the drift refusals, and `register-app` refusing an origin already held by `admin`.
- **Follow-up (R7):** ADM-002b must enforce `admin:*` on admin origin changes in `AppService.update`.

**Risks**

- XSS on a future admin UI origin means admin tokens can be stolen. That UI then needs a strict CSP, no third-party scripts and SRI. Record this as a gating requirement on the admin UI ticket.
- Subdomain takeover: if the admin host's DNS is left pointing at a removed service, an attacker gains an admin origin. Mitigate by not creating DNS until it is needed and by auditing DNS records.
- The 60 s per-process CORS cache plus up to 15 min of token TTL mean `--clear` is not instant. The runbook must say so.
- Two concurrent claims on one origin (Finding 4). Mitigated by the workflow concurrency group, not by the database.

**Open questions**

1. Which host does the owner choose (`admin.mmlabs.online`?), and does an admin UI exist yet? If not, should DNS stay absent while operators use curl with an explicit Origin?
2. Is `--clear` (empty origins) in scope for this ticket, or a follow-up?
3. Should staging use a separate admin origin, or should staging stay admin-closed?
4. Is the 2-origin cap acceptable, or is exactly 1 preferred?
