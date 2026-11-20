# Consult — software-architect — ADM-041 (2026-11-20)

## Findings

1. **Two ADR numbering collisions, not one.**
   - **ADR number.** `0018-attachment-storage-google-drive.md` (ADM-027, PR #77) was merged first. It is cited as "ADR-0018" in code: `app/cli.py:19`, `app/core/config.py:138`, `app/main.py:94`, `app/modules/attachments/{drive,errors,store,sweep}.py`, `tests/test_config.py:424`, and also `docs/deploy.md:941`. The release-log ADR is cited only in docs and tickets: `docs/spec-v1.md` (§5.13, §5.14 lines 272/304/312/315), tickets ADM-033 to 038 and ADM-041, USR-29, and the board row at `work/backlog.md:103`.
   - **D-number.** The release-log ADR (§12 and the Consequences section) claims **D28**. In `docs/identity-architecture.md:320`, D28 is already bug-report submit (ADM-028) and D29 is triage. The next free number is **D30**.
2. **The release-log ADR's R3 text has never been applied.** Its §12 R3 class-4 text is meant to land with "the first implementing ticket". That ticket is ADM-034, and it is still BACKLOG. So `CLAUDE.md` R3 class 4 still says "only `PUT …/manifest`". If ADM-041 edits R3 now, it would describe `releases` and usage routes that don't exist yet.
3. **A parallel class-4 widening exists.** USR-29 (P1, BACKLOG) adds a member-read scope on the same mechanism. Three tickets (ADM-034, ADM-041, USR-29) would each rewrite the same R3 sentence and fight over the next ADR/D numbers.
4. **The new scopes differ from the existing ones.** Spec §5.14 (OQ56/61/66) describes `usage:*` and `limits:write` as **runtime** secrets held in app backends, while `manifest` and `releases` are CI-held. The new scopes also add `on_behalf_of`, idempotency, a fail-open policy and per-key limits. That is enough new material for a standalone decision, not a small amendment.

## Recommendations

- **R-A: renumber the release-log ADR to ADR-0021, before ADM-041 is planned.** The attachment ADR merged first and is cited in code, including `app/core/`, which is a High path. The release-log ADR has no code references and is not implemented. Steps:
  - `git mv` it to `0021-release-log-and-deployer-credential.md`.
  - Change its D28 to D30.
  - Update the references in the spec, tickets ADM-033 to 038, USR-29 and the board text.
  - Add a one-line note in the file saying it was "originally numbered 0018, renumbered 2026-11-20 (collision with attachment storage)", so old PR/commit references can still be traced.
  - Do this as a separate `solo:` change on `main`, not inside ADM-041 (R7), because it touches the board and other tickets.
  - Rejected options: renumbering the Drive ADR (touches code and a High path), and `0018a`/`0018b` suffixes (breaks the numbering convention and tooling).
- **R-B: ADM-041 writes a new ADR-0022 instead of editing the accepted release-log ADR in place.** ADR-0022 "amends ADR-0021 §1 (scope set), §3 (rate limit)". Add an "Amended by ADR-0022" pointer to ADR-0021 and log it as D31. Accepted ADRs keep their content; changes are added as new records. Rename the ticket and board row to "Usage key scopes (ADR-0022, amends ADR-0021)". This matches the ticket's own "(or ADR-0021)" option, shifted by one.
- **R-C: make R3 class 4 scope-generic once, so each new scope doesn't rewrite it.** Suggested `CLAUDE.md` text: "(a) app credential `mmak_…` with exactly one scope; each scope is accepted only on the routes listed for it in ADR-0021/0022 and `tests/test_route_auth_classes.py`; own app only (mismatch → 403 `app_credential_mismatch`, wrong scope → 403 `app_credential_scope`)", followed by a one-line scope list (`manifest`, `releases`, `usage:read`, `usage:write`, `limits:write`) with "(route lands in ADM-0xx)" markers. This satisfies AC1 without describing routes that don't exist yet, and USR-29 only has to add a word.
- **R-D: content ADR-0022 should cover** (ties to the ticket's scope bullet):
  - **One scope per key, kept.** It keeps one simple check and fits the existing 2-active-per-(app, scope) limit. Trade-off: an app backend holds up to 3 runtime secrets.
  - **Runtime-key rule.** These keys are never in browser code or CI. Name the env var.
  - **`on_behalf_of`.** Only on `limits:write`; any other scope sending it gets 422.
    - The named user must be active, a member of the key's app, and hold `<app>:usage:manage` through an **app-scope** assignment (org-scoped assignments don't count). Otherwise 403.
    - The audit actor records both the key and the user. Reason 1–500 chars (OQ67).
  - **Idempotency key.** Required on consume, settle and refund, scoped per (app, key). Whether it is a header or a body field is PLAN's.
  - **Fail-closed by default; fail-open is per-app opt-in, set by `admin:usage:write` only.** Who may change it is an open question (below).
  - **Per-key rate limit after authentication, on top of the per-IP limit before it.** Many app backends share egress IPs. Note the ADR-0002 caveat: the in-process limiter is per worker.
  - **Token issuance stays ungated.** Cite ADR-0017 §5 and spec §5.14 "Out of scope": enforcement happens at the atomic consume call.

## Risks

- **Merge order.** If ADM-034 or USR-29 merges with the old §12 R3 text after ADM-041, it will overwrite R-C. Their plans must rebase onto the generic wording.
- **Stale links.** The renumber breaks links in merged PR descriptions and old commits. The traceability note limits the damage.
- **Blast radius and operations.** A leaked `usage:write` key can drain or refund any user's balance for its app. A leaked `limits:write` key with a forged `on_behalf_of` is limited only by the RBAC check, so that check must be fail-closed and audited. Runtime keys also make issuance and rotation an operational matter, not a CI one.
- **Fail-open grace** allows usage over the limit by design. Reconciliation (ADM-046) must not retroactively break idempotency.

## Open questions (for Ming)

1. Approve R-A (release-log ADR becomes 0021, its D28 becomes D30) as a separate `solo:` change on `main`?
2. Is the generic R3 wording (R-C) acceptable instead of listing every route in `CLAUDE.md`?
3. Single scope per runtime key, or allow one key to hold `usage:read` + `usage:write`?
4. May app admins (via `limits:write` + `on_behalf_of`) toggle fail-open grace, or only `admin:usage:write`?
5. Must the `on_behalf_of` user have a verified email (R11 requires one only for joining apps and getting tokens)?
