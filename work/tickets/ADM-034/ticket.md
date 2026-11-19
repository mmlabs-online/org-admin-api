# ADM-034: Release log — app credential widening and the app's release write

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P2 |
| Size | M |
| Areas | db, api, auth, security |
| Gate | plan-approval |
| Depends on | ADM-033 |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-19 |
| Claimed by | — |
| PR | — |

## Goal
Each app's release workflow records its release right after opening the deployment PR, with the app's existing app credential.

## Scope reference
`docs/spec-v1.md` §5.13 (OQ36, OQ37, OQ44–OQ49); ADR-0018 (amends ADR-0016 §1, ADR-0017 §2, R3 class 4).

## Scope
- The app's existing `mmak_` key also authorizes `PUT …/releases/…` for its **own app**; **no per-key scopes** (OQ44, owner 2026-11-19). The `admin` app has no app credential, so its releases are not recorded (OQ48).
- Auth errors: wrong key kind or an admin JWT → 401; another app's key → 403 `app_credential_mismatch` (audited `denied`); disabled app → 403 `app_disabled` (ADR-0016 §4).
- `PUT /api/v1/apps/{key}/releases/{environment}/{version}` (the app's app credential, own app only): insert → 201 `pending`; pending → full replace 200; identical → 200 no-op (no audit); deployed/failed → 409 `release_not_pending`, no backfill of empty fields, also for a record created from a report (OQ37, owner 2026-11-19). Never changes status. Concurrent first writes never 500.
- Validation (OQ45): version grammar, digests `sha256:<64 hex>`, GitHub URL allow-lists, `changes` ≤ 200, titles ≤ 200 with control chars stripped, ticket id / risk formats, unknown fields 422; body size cap (OQ46); per-IP rate limit; no CORS.
- Docs in the same PR: R3 class 4 and R9 text in `CLAUDE.md` (from ADR-0018 §11), D28 in `docs/identity-architecture.md`, "Amended by ADR-0018" pointers in ADR-0016/0017; route-class test registry (route → credential type).

## Out of scope
- Deployer credential and deploy report (ADM-035, ADM-036).

## Acceptance criteria
- [ ] Existing manifest keys and manifest pushes behave exactly as before (regression tests).
- [ ] The same app key pushes the manifest and writes its own app's releases; another app's key gets 403 `app_credential_mismatch` on both routes.
- [ ] Every PUT outcome in the scope list is tested, including retry, 409 after `pending` and the race on first insert.
- [ ] 422 cases for each validation rule; 429 on the rate limit; 404 unknown app.
- [ ] Audit rows only for real changes; no PII (no author emails) stored.

## Technical notes
- Files: `app/modules/app_credentials/`, `app/modules/releases/`, migration, `app/core/rate_limit.py`/config, `CLAUDE.md`, docs.
- The `admin` app is out of scope (OQ48); no scope column, no migration on `app_credentials`.

## Test plan
- API tests per criterion; route-class tests; migration round-trip.

## Notes / answers from Ming

## Handoff
