# ADM-013: OpenAPI contract that clients can generate types from

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P2 |
| Areas | api |
| Attempts | 0 |
| Created | 2026-11-16 |
| PR | — |

## Goal
The generated OpenAPI document describes the real error shape and headers, so clients such as `admin-pan` can generate types without hand-written overrides. It can also be dumped offline as a stable snapshot.

## Spec reference
R6 (one error shape), R9 (API conventions); §2 (admin web UI consumer). Follow-up F3 from USR-03b (shared `ErrorEnvelope` model and stable `operation_id`s).

## Acceptance criteria
- [ ] An `ErrorEnvelope` schema `{error: {code, message, details}}` is referenced by every documented error response (4xx/5xx) across all routers, admin routers included.
- [ ] FastAPI's default `HTTPValidationError` and `ValidationError` schemas are gone from the document. 422 responses reference `ErrorEnvelope` (`code=validation_error`), matching what the server actually returns.
- [ ] The `origin` header is no longer declared as a required parameter on `password/forgot` and `verify-email/resend`. The server still checks it, and the existing tests prove that.
- [ ] `make openapi` (or `python -m app.cli openapi`) writes `create_app().openapi()` offline, without a database or secrets, with all routers mounted. The output is deterministic: running it twice gives identical bytes.
- [ ] Tests: a schema test asserts the points above (no `HTTPValidationError`, envelope on errors, `origin` not required), plus a determinism test for the dump.
- [ ] Optional, if cheap: `X-Request-ID` is documented as a response header, and the error envelope carries the request id. Record the decision in the plan.

## Dependencies
ADM-002 (admin routers), USR-03b.

## Notes / answers from owner
- 2026-11-16 (owner): `admin-pan` currently hand-writes the error type and has to cast the `origin` header, which browsers send themselves.
