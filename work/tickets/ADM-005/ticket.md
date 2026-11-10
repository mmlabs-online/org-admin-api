# ADM-005: Products registry

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P2 |
| Areas | db, api |
| Attempts | 0 |
| Created | 2026-11-10 |
| PR | — |

## Goal
Keep a registry of mmlabs products/services that entitlements can refer to.

## Spec reference
§5.5 Products registry (initial list depends on §8 Q1)

## Acceptance criteria
- [ ] `products` table + migration: unique key, name, status, base URL
- [ ] CRUD endpoints under `/api/v1/products` following the organizations module conventions
- [ ] Tests: happy path, 404, duplicate key 409, validation 422, pagination

## Notes / answers from owner
