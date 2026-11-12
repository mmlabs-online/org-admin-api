# ADM-011: Platform hardening for public endpoints (client IP, limiter, error envelope, display text)

| Field | Value |
|---|---|
| State | DONE |
| Priority | P1 |
| Areas | security, api |
| Attempts | 0 |
| Created | 2026-11-11 |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/15 |

## Goal
Make the public endpoints (signup, role catalog) robust in production: correct per-client rate limiting, isolated limiter scopes, one error shape everywhere, and safer public display text.

## Spec reference
§6 Non-functional, §6.1; R6, R11; identity-architecture.md §5 S3; follow-ups from USR-01, USR-02d and USR-02e security reviews

## Acceptance criteria
- [ ] Refuse to start in staging/production when `TRUSTED_PROXY_CIDRS` is empty (USR-02e FU3 / F1)
- [ ] Rate limiter: per-scope key caps (or separate stores) and O(1) amortised eviction, so one scope can't evict another's buckets (USR-01 / USR-02e FU2)
- [ ] Framework 404/405 (and other `StarletteHTTPException`) responses use the R6 `{"error": …}` envelope (USR-02e F3)
- [ ] Display-text validator by Unicode category (reject `Cc, Cf, Zl, Zp, Co, Cs, Cn`) for app/role names and permission descriptions, with NFC normalization before storing; ADR-0003 §11 amended (USR-02e F2, FU5)
- [ ] Optional: refuse origins under `.api.mmlabs.online` (USR-02d/02e FU6); `--no-proxy-headers` in the Dockerfile uvicorn CMD (USR-01 security review finding 4)
- [ ] Tests for each item

## Notes / answers from owner
- 2026-11-11: created by the orchestrator to collect hardening follow-ups from the USR-01, USR-02d and USR-02e security reviews.
- No dependencies. Release note: until this ships, `TRUSTED_PROXY_CIDRS` must be set by hand in the deployment repo.
