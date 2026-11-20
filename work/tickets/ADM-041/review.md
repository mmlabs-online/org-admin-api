## Attempt 1 — 2026-11-20
Verdict: PASS

### Checks (run by reviewer)
- `uv run ruff check . --exclude .github/scripts` — All checks passed.
- `make typecheck` — Success: no issues found in 167 source files.
- `uv run pytest tests/test_route_auth_classes.py tests/test_openapi_contract.py -q` — 201 passed (matches impl-log's route-class run; openapi contract added). Full suite not run: the product diff (3ab03e0) is docs-only (CLAUDE.md, ADRs, identity-architecture.md, spec-v1.md), no app/ or tests/ change.

### Acceptance criteria
- [x] ADR accepted by Ming at the plan gate; CLAUDE.md R3 and D-log updated in the same PR — evidence: docs/adr/0022-usage-key-scopes.md "Status: Accepted (2026-11-20)" citing batch 2026-11-20-1 Q1, 21-28; CLAUDE.md R3 class 4 rewritten; docs/identity-architecture.md D31 row; ticket.md Notes records Ming's "duyệt hết; câu 1–28 theo mặc định".
- [x] No code change (docs only) — evidence: `git show 3ab03e0 --stat` touches only CLAUDE.md, ADR-0021 (one "Amended by" line), ADR-0022, identity-architecture.md, spec-v1.md, impl-log.md.
- Scope bullets: scopes (ADR §1); on_behalf_of with `<app>:usage:manage`, app-scope only (§4); idempotency (§5); fail-closed default / staff-only opt-in grace (§6); per-key rate limit (§7); why issuance stays ungated (§8); R3 class 4 (CLAUDE.md); D31. All covered.
- Approved defaults: Q21 generic R3 text (yes); Q22 token-bound on_behalf_of (§4, spec dated note on OQ61 in §5.14 and §8); Q23 self-grant 403 (§4); Q24 three scopes, one per key, usage:write includes the per-user read (§1); Q25 grace staff-only, transport/timeout/5xx only, 24 h, original period, cap deferred to ADM-046 (§6); Q26 verified email (§4); Q27 admin app refused for all three (§1); Q1 renumber done in 5a0ba90, ADR-0022/D31 used.

### Issues
None blocking.

### Rules (CLAUDE.md)
No violations found. R3 class 4 text is consistent with tests/test_route_auth_classes.py: the only live app-credential route is `PUT /api/v1/apps/{key}/manifest` (line 247 asserts exactly that), and the text says manifest is live and releases/usage/limits routes arrive with ADM-034/042/044, which extend the registry. 401 / 403 `app_credential_mismatch` / `app_credential_scope` wording matches ADR-0021 §12. R7: no out-of-plan files.

### Blockers from review agents
None — appsec-engineer attempt 1: PASS, no 🔴; five 🟡 should-fix ADR clarifications (security-review.md) listed for Ming.

### Nits (do not block)
- The D30 row is absent from identity-architecture.md §8 (table goes D29 -> D31) while ADR-0021 and spec reference D30. Decision: follow-up, not a blocker. ADR-0021 §12 and ADM-034 ticket.md line 28 assign the D30 row to ADM-034, the plan said not to add it, and adding it here would be a scope change. The gap is explained by ADM-034.
- ADR-0022 §3 says runtime scopes skip `lock_app` while ADR-0021 §1 still says both scopes take it; this is covered by the "Amends" header and the new "Amended by" pointer in ADR-0021, but ADM-034/042 implementers must read both.
- Spec §5.14 line 272 still lists ADM-041 wording as pending ("final names set at PLAN"); harmless, can be tidied by a later spec pass.

### Follow-ups (out of scope)
- ADM-034 must add the D30 row (and keep R3/R9 text consistent with ADR-0021 §12 and ADR-0022 §10) before or with D31's neighbours merge.
- ADM-042/044 to decide numeric rate limits, amount maximum, latency target, `on_behalf_of` header vs field and error-code names (ADR-0022 "Left to implementing tickets").
