# ADM-032: Bug reports — guide for app teams

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P3 |
| Size | S |
| Areas | api |
| Gate | none |
| Depends on | ADM-030 |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-19 |
| Claimed by | — |
| PR | — |

## Goal
App teams (ricotdin, tudocuatui, …) can add a "report a bug" form that works the first time.

## Scope reference
`docs/spec-v1.md` §5.12 (submit side, limits, EXIF, OQ35 per-file failures).

## Scope
- A section in `docs/consumers.md`: request shapes for submit and attachment upload, required headers (`X-MM-App`, credentials), limits and allowed types, client-side EXIF stripping (canvas re-encode), error codes, per-file failure handling, privacy note about screenshots.

## Acceptance criteria
- [ ] The guide matches the shipped endpoints (paths, fields, codes) checked against OpenAPI.
- [ ] It states the limits (≤ 5 images, ≤ 5 MB, PNG/JPEG/WebP/GIF) and that video is not supported yet.
- [ ] It includes a minimal fetch example for submit and upload.

## Technical notes
- Docs only.

## Test plan
- Manual: follow the guide against a local server.

## Notes / answers from Ming

## Handoff
