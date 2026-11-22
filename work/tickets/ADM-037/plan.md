Status: READY

# Plan — ADM-037: Release log — guide for app repos and the deployment repo

## Scope summary
Ticket Scope: "A guide (e.g. `docs/release-log.md`): request contracts for the app write and the deploy report, warning-only CI steps (`curl -fsS --retry 3 --max-time 15 … || echo "::warning::…"`, `continue-on-error`), secret names (`MMLABS_RELEASE_CREDENTIAL` per app repo in a release-branch Environment secret; `MMLABS_DEPLOYER_CREDENTIAL` in the deployment repo), how to build `changes` from merged PRs, PII rule." and "Owner runbook steps in `docs/deploy.md`: issuing and rotating both key kinds."
ADR-0021 §1 (secrets), §2-§7 (contracts, idempotency, transitions, grammar, validation/PII), §11 ("`admin` = org-admin-api; admin-pan is registered as its own app row"). Spec §5.13.
Docs only. Contracts are taken from the merged code: `app/modules/releases/{app_router,deployment_router,write_schemas,rules,errors}.py`, caps `app/core/config.py:109,114` (256 KiB app write, 4 KiB report), rate-limit buckets `app/core/rate_limit.py:87,126`.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | Paths, fields and codes in the guide match OpenAPI | Run `uv run python -c "from app.main import create_app; import json; print(json.dumps(create_app().openapi()))"` and compare the two routes (paths, body fields, response codes 200/201/401/403/409/411/413/415/422/429) with the guide's tables; also the version grammar (`vX.Y.Z` production, `sha-<7 hex>` staging) against `write_schemas.py` |
| 2 | Copy-paste step for an app release workflow and for `deploy-<app>.yml` | Read the two YAML snippets; check they parse (`python -c "import yaml,sys;yaml.safe_load(open(f))"` on extracted blocks) and use only placeholders (`<app-key>`, `${{ secrets.MMLABS_RELEASE_CREDENTIAL }}`) |
| 3 | States that any error must be a warning only | `grep -n "warning" docs/release-log.md`: a rule near the top plus `|| echo "::warning::…"` / `continue-on-error: true` in both snippets; every 4xx/5xx/429/timeout row says "warn, never fail the job" |
| 4 | Manual test plan from the ticket: curl steps run against a local server | Implementer runs both curls against `make dev` (issue a releases key and a deployer key via admin API; record outcome in impl-log). Real keys are never written into files |
| 5 | Owner runbook for issuing and rotating both key kinds | `docs/deploy.md` "App credentials" section shows `releases` scope issuance (optional `scope`, default `manifest`; 2 active per (app, scope); `admin` app allowed for `releases` only) and rotation; the "Deployer credential" section links to the guide. Check against `app/modules/app_credentials/schemas.py` |
| 6 | No secrets in the diff | `grep -rnE "mm[ad]k_[0-9a-f]{16}_" docs/release-log.md docs/deploy.md` shows only `<key_id>`/`<secret>` placeholders |

## Ming verification after merge
First real run in an app repo release workflow and in `mmlabs-online/deployment` (`deploy-<app>.yml`); the records appear in the admin release list. (Cross-repo adoption is outside this repo.)

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `docs/release-log.md` | Create: overview and "errors are warnings only" rule; who sends what and when (app repo at release: PUT write; deployment repo after deploy: PUT deployment); keys and secret names (`MMLABS_RELEASE_CREDENTIAL` in a release-branch Environment, `MMLABS_DEPLOYER_CREDENTIAL` in deployment repo); request/response contracts and field limits; version grammar; building `changes` from merged PRs (title only, `pr_url`, optional `ticket_id`/`risk`, ≤ 200); PII rule; idempotency/retry (`--retry 3 --max-time 15`, safe to repeat, report-first creates the record, late PUT enrichment); error code table (401, 403 `app_credential_mismatch`/`app_credential_scope`/`app_disabled`, 404, 409 `release_not_pending`/`release_already_deployed`, 411, 413, 415, 422, 429); `admin` (= org-admin-api) vs `admin-pan` (own app row) rule; two copy-paste GitHub Actions steps; rate limits 30/min per IP | Ticket scope | No |
| `docs/deploy.md` | Modify: add `releases`-scope issuance and rotation to "App credentials (USR-05a)" (~l.541-586); link the guide from "Deployer credential (ADM-035)" (~l.588-630); note the `admin` app may only get `releases` keys | Ticket scope (owner runbook) | No |
| `docs/consumers.md` | Optional one-line pointer to the guide, only if a natural place exists (otherwise untouched) | Discoverability | No |
| `work/tickets/ADM-037/{plan,impl-log}.md` | Workflow files | Loop | No |

Expected tier: low (`docs/**`, `work/**`).

## Data model / migration
None.

## API contract
None (docs only; documents existing `PUT /api/v1/apps/{key}/releases/{environment}/{version}` and `PUT …/deployment`).

## Tests to write
None (docs only). Verification is the OpenAPI comparison and manual curl run above; `make check` must still pass.

## Approach
1. Dump OpenAPI and read the two routers/schemas to fix the exact contract tables.
2. Write `docs/release-log.md` in this order: rule (warning-only), flow diagram, keys/secrets, app write, deploy report, `changes` recipe, PII, errors, `admin` vs `admin-pan`, snippets.
3. Update `docs/deploy.md` runbook; keep the existing safe-secret pattern (0600 file, never stdout/argv).
4. Run the curl steps against a local server with throwaway keys; log the result in impl-log.
5. `make check`.

## Specialist input used
None (fast lane).

## Assumptions
- The `admin` app key is org-admin-api; admin-pan uses its own app row (ADR-0021 §11, decided ADM-034).
- Guide documents the current code; spec §5.13 and code agree (reviewer to flag any mismatch as a follow-up, not fix it here).
- Hotspot: `docs/deploy.md` is edited by many tickets; expect a rebase.
- Follow-ups (not in this diff): any code or OpenAPI wording fixes found while comparing; a reusable GitHub Action in the deployment repo; PR-title-to-`changes` helper script.

## Blocking questions
None

## Split proposal
None
