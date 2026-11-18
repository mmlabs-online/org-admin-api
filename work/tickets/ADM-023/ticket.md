# ADM-023: Declare 404/409 responses and admin:* requirements in OpenAPI

| Field | Value |
|---|---|
| State | REVIEW |
| Priority | P2 |
| Areas | api |
| Attempts | 0 |
| Created | 2026-11-17 |
| PR | — |

## Goal
`/openapi.json` lists every real error response and permission requirement, so admin-pan's generated types match the API.

## Spec reference
Contract quality of existing §5.1, §5.3, §5.6, §5.8 endpoints; follows ADM-013 (OpenAPI contract clients can generate types from).

## Acceptance criteria
- [ ] 404 is declared on the organizations, entitlements, app-credentials and join-requests routes that can return it.
- [ ] 409 is declared on every route that can return it (routes using `_with_409`: POST/PATCH apps, permission PATCH, role PUT, assignment DELETE, entitlement grant), with the error envelope schema (R6).
- [ ] The `admin:*` requirement of `POST /api/v1/apps/{key}/credentials` (and any other route that needs `admin:*` beyond its declared permission) is machine-readable (e.g. `x-requires-super: true`).
- [ ] POST /apps with a taken key returns 409 with a distinct reason/code (e.g. `app_key_taken`) so the UI can map it to the key field; documented in OpenAPI.
- [ ] A test walks the OpenAPI document and asserts the declared responses above; existing tests still pass.

## Dependencies
ADM-013. ADM-020 also declares 404/409 for organizations — whichever lands first does it, the other skips. Requested by admin-pan (batch 2026-11-17).

## Notes / answers from owner
