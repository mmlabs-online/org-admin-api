# ADM-034: Release log — scoped app credentials and the app's release write

| Field | Value |
|---|---|
| State | PLANNING |
| Priority | P2 |
| Size | M |
| Areas | db, api, auth, security |
| Gate | plan-approval |
| Depends on | ADM-033 |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-19 |
| Claimed by | claude@2026-11-20T13:00Z |
| PR | — |

## Goal
Each app's release workflow records its release right after opening the deployment PR, with a key that can do nothing else.

## Scope reference
`docs/spec-v1.md` §5.13 (OQ36, OQ37, OQ44–OQ49); ADR-0021 (amends ADR-0016 §1, ADR-0017 §2, R3 class 4).

## Scope
- `app_credentials.scope` (`manifest` | `releases`, default `manifest`; existing keys stay manifest) via migration; at most 2 active per (app, scope); issuance takes optional `scope`; releases-scoped keys may be issued for the `admin` app (the `admin_app` refusal stays for manifest only).
- `authenticate` takes the required scope and the denied audit action; wrong scope → 403 `app_credential_scope` (audited denied); another app → 403 `app_credential_mismatch`; wrong key kind or admin JWT → 401.
- `PUT /api/v1/apps/{key}/releases/{environment}/{version}` (releases-scoped key, own app only): insert → 201 `pending`; pending → full replace 200; identical → 200 no-op (no audit); deployed/failed → fill NULL fields (enrichment, audited), differing non-null field → 409 `release_not_pending`, identical → 200. Never changes status. Concurrent first writes never 500.
- Validation (OQ45): version grammar, digests `sha256:<64 hex>`, GitHub URL allow-lists, `changes` ≤ 200, titles ≤ 200 with control chars stripped, ticket id / risk formats, unknown fields 422; body size cap (OQ46); per-IP rate limit; no CORS.
- Docs in the same PR: R3 class 4 and R9 text in `CLAUDE.md` (from ADR-0021 §11), D30 in `docs/identity-architecture.md`, "Amended by ADR-0021" pointers in ADR-0016/0017; route-class test registry (route → scope).

## Out of scope
- Deployer credential and deploy report (ADM-035, ADM-036).

## Acceptance criteria
- [ ] Existing manifest keys and manifest pushes behave exactly as before (regression tests).
- [ ] A releases key cannot push a manifest and a manifest key cannot write a release (403 `app_credential_scope`).
- [ ] Every PUT outcome in the scope list is tested, including retry, enrichment and the race on first insert.
- [ ] 422 cases for each validation rule; 429 on the rate limit; 404 unknown app.
- [ ] Audit rows only for real changes; no PII (no author emails) stored.

## Technical notes
- Files: `app/modules/app_credentials/`, `app/modules/releases/`, migration, `app/core/rate_limit.py`/config, `CLAUDE.md`, docs.
- Resolve the `admin` app collision (org-admin-api vs admin-pan) at PLAN.

## Test plan
- API tests per criterion; route-class tests; migration round-trip.

## Notes / answers from Ming

## Handoff
