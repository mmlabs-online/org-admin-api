# Security review — ADM-011

## Attempt 1

**Verdict: no blockers.** `make check` is green: ruff, format, mypy (strict, 51 files) and pytest (463 passed). Every acceptance criterion is implemented, and I found no exploitable issue in the diff. Two items should be fixed before or at release. The rest are hardening notes.

### Findings

🟡 **S1: Releasing this image before the deployment-repo change is merged blocks the release.**
- Evidence: `app/core/config.py:69-86` adds the staging/production gate. `migrations/env.py:8,23` calls `get_settings()`, so `alembic upgrade` (the `migrate` step) now refuses to start too. `docs/deploy.md:50-62` documents this.
- Risk: the release stops at migrate-failed. It fails closed, because the old API keeps running, but the ADM-011 release (and any release after it) cannot ship. Per the consult, production currently sets only `APP_ENV`, `APP_VERSION` and `TZ`.
- Fix: in the PR body, add a **Deploy prerequisite** section. It says that a `mmlabs-online/deployment` PR adding `TRUSTED_PROXY_CIDRS: '["<web subnet>"]'` to `apps/org-admin-api/compose.yml` → `x-app.environment` must be merged before the `release/vX.Y.Z` branch is pushed. No code change.

🟡 **S2: The CIDR gate accepts the IPv4-mapped IPv6 range and other "private" ranges that are not real proxy networks.**
- Evidence: `app/core/config.py:79-81` relies on `net.is_private` plus a /48 floor. On the repo's Python 3.12.3, these all pass:
  - `::ffff:0:0/96` (is_private=True, prefix 96 ≥ 48)
  - `64:ff9b:1::/48`
  - `2001:db8::/48`
  - `0.0.0.0/8`
  - `127.0.0.0/8`
  - `198.18.0.0/15`
- Risk: `::ffff:0:0/96` matches every IPv4 client in IPv4-mapped form. If uvicorn ever binds dual-stack (`--host ::`), all IPv4 peers show up as `::ffff:a.b.c.d` and are trusted. Any client could then set `CF-Connecting-IP` (`app/core/client_ip.py:18-22`), which defeats the per-IP limit that this gate exists to protect. Today this needs an operator mistake, so it is not a blocker. But the gate is meant to catch exactly that kind of mistake.
- Fix: also refuse
  - IPv6 networks that overlap `::ffff:0:0/96`, `64:ff9b::/96`, `64:ff9b:1::/48` or `2001:db8::/32`;
  - `0.0.0.0/8` and `127.0.0.0/8` / `::1`. Loopback is never cloudflared's peer in the container, and `--no-proxy-headers` means nothing is meant to arrive from loopback.

  A simple option is to allow only RFC 1918 (`10/8`, `172.16/12`, `192.168/16`) and ULA (`fc00::/7`, subnets ≥ /48): `any(net.subnet_of(p) for p in ALLOWED)`. Add a test for `::ffff:0:0/96`.

🟢 **N1: The rate limiter is correct.**
- Evidence: `app/core/rate_limit.py:29-69`.
- Each scope has its own `OrderedDict`. A new or restarted window does `move_to_end`, and `_evict` pops only from the front. Because the window per scope is fixed, front order equals expiry order, so eviction is amortised O(1) and never touches another scope.
- The `Literal` scope plus the build-time `ValueError` (`:83-86`) keep the number of scope stores bounded.
- Residual risk: an attacker rotating IPv6 /64s or IPv4 addresses can still push live buckets out within one scope, which resets their counts. This is documented in ADR-0002 and listed as a follow-up.
- `max_keys_per_scope` is not validated as ≥ 1. With 0, the key just inserted would be popped and the limit never applies. Only the hard-coded default is used (`app/main.py:34`), so this is a note, not a finding.

🟢 **N2: The R6 handler for framework exceptions is safe.**
- Evidence: `app/core/errors.py:99-133`.
- It never echoes `exc.detail`, keeps `exc.headers` (e.g. `Allow`), and gives 204/304 an empty body.
- It also covers FastAPI's `HTTPException` subclass. `grep` finds no app code that raises `HTTPException` and relies on its `detail`.
- Unhandled exceptions still return Starlette's plain-text 500, which is outside the envelope but leaks no data. This is already a follow-up.

🟢 **N3: The display-text validator is ordered correctly, but some invisible or blank text still gets through.**
- Evidence: `app/core/text.py:9-21` and `app/modules/rbac/schemas.py:26-31,93-104`.
- I checked the order directly: NFC first, then length, then the category check. NFD `Quản trị` is stored as NFC. Bidi (U+202E), ZWJ and U+180E are rejected. The 422 handler strips `input` and `ctx` (`errors.py:82-95`), so the value is not echoed. Read schemas stay plain `str`.
- These are still accepted (and are allowed by the AC as written):
  - whitespace-only names (`"   "`);
  - Hangul fillers U+115F and U+3164 (Lo);
  - the blank braille pattern U+2800 (So);
  - the combining grapheme joiner U+034F (Mn);
  - U+FFFC;
  - 250 stacked combining marks ("Zalgo").
- Effect: an admin, or an attacker while the RBAC admin API is unauthenticated (it is flag-gated and barred in staging/production), can publish role or app names in the public catalog that look blank or display badly. This is low risk.
- Suggested follow-up:
  - require at least one non-space, non-filler character (`value.strip()` plus a denylist for U+115F, U+1160, U+3164, U+FFA0, U+2800, U+034F);
  - cap the run of consecutive `Mn`/`Me` characters (e.g. ≤ 4).
- Also a product decision, as the consult noted: rejecting ZWJ breaks emoji sequences (`👨‍👩` → 422).

🟢 **N4: The origin refusal is correct.**
- Evidence: `app/modules/rbac/origins.py:42-43`.
- `host.endswith(".api.mmlabs.online")` cannot be bypassed: the host is lowercased (`:28`), a trailing dot or empty label is rejected by `_LABEL_RE` (`:33-35`), and non-ASCII or IDNA input is rejected (`:26-27`).

🟢 **N5: `--no-proxy-headers` and `hide_input_in_errors` are good additions.**
- Evidence: `Dockerfile:18` and `app/core/config.py:19`.
- `request.client.host` is now always the raw TCP peer, which `client_ip` needs.
- One small leak remains outside this ticket: the CIDR field validator (`config.py:61-65`) re-raises `ipaddress`'s message, which contains the bad CIDR. CIDRs are not secrets, so this is acceptable.

### Follow-ups (out of scope, R7)
- Tighten the trusted-CIDR allow-list to RFC 1918 and ULA ranges only (S2), if it is not fixed in rework.
- Make display text non-blank and cap stacked combining marks (N3). The owner also needs to decide whether ZWJ is allowed.
- Add a catch-all `Exception` handler that returns the 500 `internal_error` envelope with no traceback.
- Deployment repo: pin the `web` subnet, give cloudflared a fixed IP and trust only its `/32`. Trusting the whole `web` subnet lets any container on it (pgadmin, stoktok, …) forge `CF-Connecting-IP`.
- Deployment repo, urgent: rotate the Cloudflare Tunnel token that is committed in plain text and move it to a host secret. Also pin `cloudflared:latest` to a version.
- Before running more than one process, use a shared rate-limit store with a per-prefix budget (ADR-0002).
