Status: READY

# Plan — ADM-041: Usage key scopes (ADR-0022, amends ADR-0021)

Docs-only ticket. It writes a new **ADR-0022**, which amends the release-log ADR. A separate `solo:` change on `main` renumbers that ADR from `0018-release-log-and-deployer-credential.md` to **ADR-0021**, with decision-log row **D30**. That change must merge **before** this ticket's build (Q1). In this plan, "ADR-0021" always means the release-log ADR after the renumber. "ADR-0018" without a filename is ambiguous because two files carry that number.

Status is READY because every question below has a default that this plan already uses. Ming confirms or overrides them in the batch 2026-11-20-1 approval round. The implementer writes the ADR with the final answers.

## Scope summary
- Ticket scope: "ADR-0018 amendment (or ADR-0021): scopes `usage:read`, `usage:write`, `limits:write`; `on_behalf_of` rules for app-admin calls (`<app>:usage:manage`, app-scope assignment only); idempotency keys; fail-closed default and opt-in fail-open grace; per-key rate limit; why token issuance stays ungated." / "R3 class 4 wording in `CLAUDE.md`; D-log entry in `docs/identity-architecture.md`."
- Spec §5.14 (`docs/spec-v1.md:272`): "Usage calls therefore **add new scopes on the same scoped-key mechanism** — `usage:read`, `usage:write`, `limits:write` (final names set at PLAN) — which **requires an ADR-0018 amendment** and the matching R3 wording in `CLAUDE.md` (ticket **ADM-041** …). **No usage route ships before that amendment is accepted.** The new scopes are **runtime** keys held by each app backend (unlike the CI-held `manifest`/`releases` keys) and must never reach browser code. Routes and rate limits are PLAN's."
- OQ61 (`docs/spec-v1.md:277`): "the app backend calls this API with a `limits:write`-scoped key and `on_behalf_of = <user id>`, and this API checks that the user holds `<app>:usage:manage` on that app before applying the change (audit records both the key and the user)." Also: held "through an **app-scope** assignment only (org-scoped assignments do not count)".
- OQ65 (`:281`): "the app backend reads the user's limits and usage with a `usage:read`-scoped key."
- OQ66 (`:282`): "**atomic consume** (metric, amount, idempotency key; `usage:write`-scoped key) **before** the AI action; over the limit → **409 `usage_limit_exceeded`** … a retried idempotency key returns the first result. Duration metrics consume an estimate first and settle … **refunded** by the app (same idempotency key). … **fail-closed** by default; an app may opt in (per-app setting) to a **fail-open grace** … Disabled users and non-members of the app are refused at consume (**403**). Token issuance stays ungated (ADR-0017 §5)."
- OQ67 (`:283`): "A reason (1–500 chars) is required on every staff and app-admin adjustment and stored in the audit entry (R4)."
- OQ69 (`:285`): "Per-use events kept **90 days**."
- Out of scope (`:290`): "Changing token issuance: ADR-0017 §5 … is not reopened: enforcement is the consume call (OQ66)".
- Release-log ADR §1 (`docs/adr/0018-release-log-and-deployer-credential.md:33-51`): one scope per key, at most 2 active keys per (app, scope), wrong scope gives 403 `app_credential_scope`, and "App-credential authentication keeps taking `lock_app` … for both scopes." §3 (`:82-83`): "Per-IP rate limit before authentication … 30/min". §12 (`:167-181`): the R3 class-4 text "applied by the first implementing ticket" (ADM-034, still BACKLOG).
- `work/decisions.md`: nothing relevant (grep for ADR-0018/usage/app credential finds no matches).

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | ADR accepted by Ming at the plan gate | `work/batches/2026-11-20-1.md` row 5 has "Plan approved" ticked. `docs/adr/0022-usage-key-scopes.md` line 3 reads `Status: Accepted (<date>)`, and every answer Ming gave in "Answers from Ming" (Q1–Q8 below) appears in the ADR. Where Ming overrode a default, the ADR follows his answer, not the default. |
| 2 | ADR-0022 covers every item in the ticket's scope bullet | The ADR has sections §1–§9 as listed under Approach step 2: scope set, runtime-key rule, auth hot path, `on_behalf_of`, idempotency, fail-closed and fail-open, rate limits, ungated issuance, data exposure. Also a Consequences section that gives the blast radius per scope, and an "Amends ADR-0021 §1, §3, §12" line. |
| 3 | `CLAUDE.md` R3 updated in the same PR | `git diff main -- CLAUDE.md` changes only the R3 row, class (4), and its text matches the draft under Approach step 4 (or Ming's override of Q2). The rows R1–R2 and R4–R11 are unchanged. |
| 4 | D-log updated in the same PR | `docs/identity-architecture.md` §8 has a new row **D31** right after D30 (D30 comes from the solo renumber). It cites ADR-0022 and says "amends ADR-0021". `grep -n "^| D31" docs/identity-architecture.md` finds exactly one line. |
| 5 | ADR-0021 has a back-pointer | `docs/adr/0021-release-log-and-deployer-credential.md` line 3 area reads "Amended by ADR-0022 (§1 scope set and auth path, §3 rate limits, §12 R3 text)". No other line of ADR-0021 changes. |
| 6 | No code change (docs only) | `git diff --name-only main...HEAD` lists only the files in the table below. No path under `app/`, `migrations/`, `tests/`, `.env.example` or `.github/` appears. `make check` is green (unchanged code). |
| 7 | No stale or ambiguous ADR references in the new text | `grep -n "ADR-0018" docs/adr/0022-usage-key-scopes.md CLAUDE.md` returns no hit that means the release-log ADR. Only a reference to the attachment ADR by its full filename is allowed. |

## Ming verification after merge
None. Docs only. Ming already gives the ADR acceptance at the plan gate (AC1).

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `docs/adr/0022-usage-key-scopes.md` | **Create.** New ADR, `Status: Accepted (<date>)`, "Amends ADR-0021 §1, §3, §12". Sections as in Approach step 2. | Ticket scope bullet 1, AC1 | No (Low tier, `docs/**`) |
| `docs/adr/0021-release-log-and-deployer-credential.md` | **Modify** (one line under Status): "Amended by ADR-0022 …". The file exists only after the solo renumber (Q1). | Accepted ADRs keep their content; changes are recorded as new records with a pointer (architect R-B) | No |
| `CLAUDE.md` | **Modify** the R3 row, class (4) only: replace the manifest-only text with the generic scope-based machine-credential text (Approach step 4). | Ticket scope bullet 2, AC1 | No, but **Medium** tier: `risk-tiers.yml:34` says `CLAUDE.md` is never auto-merged. This PR is therefore Medium and needs Ming's "cho merge". |
| `docs/identity-architecture.md` | **Modify** §8: add row D31 after D30 (after `:321` once the solo change adds D30). | Ticket scope bullet 2, AC1 | No |
| `docs/spec-v1.md` | **Modify only if Ming accepts the Q3 default** (bound `on_behalf_of`). Add one dated sentence at the end of the OQ61 bullet in §5.14 (`:277`) and in §8 OQ61 (`:424`): "*Update 2026-11-2x (owner, ADM-041):* `on_behalf_of` is proven with the app admin's own access token (ADR-0022 §4)". The spec stays the source of truth, so a decision that deviates from it must be written into the spec too. | CLAUDE.md "spec wins; flag the conflict" | No |

No other file. The ticket and board renames ("Usage key scopes (ADR-0022, amends ADR-0021)") are the orchestrator's job on `main`. Updating references in ADM-042/044/046 is follow-up work (R7).

## Data model / migration
None. The ADR records future model facts so the implementing tickets do not have to guess:
- ADM-042 extends the `app_credentials.scope` CHECK (the column is added by ADM-034) with `usage:read`, `usage:write`, `limits:write`.
- ADM-042 adds a uniqueness constraint on (app_id, idempotency_key).

## API contract
None in this ticket: no endpoint is added or changed. ADR-0022 fixes only the **auth rules** for future routes. Paths, schemas and the latency target belong to ADM-042 (consume, settle, refund, current-usage read), ADM-044 (`limits:write` actions) and ADM-046 (late reports).

## Tests to write
None: docs-only, no code. The route-class registry (`tests/test_route_auth_classes.py:61`, `APP_CREDENTIAL = {("PUT", MANIFEST)}`) gains a scope mapping in ADM-034, and the usage routes are added in ADM-042 and ADM-044. ADR-0022 says this explicitly. The reviewer runs `make check` to confirm nothing broke.

## Approach
1. **Precondition.** Confirm `docs/adr/0021-release-log-and-deployer-credential.md` exists on `main` and that D30 is in `docs/identity-architecture.md`, i.e. the solo renumber has merged. If not, stop and record it in impl-log; do not do the renumber here (R7).
2. **Write ADR-0022**, in the same shape as ADR-0021 (Status, Date, Context, Decision §§, Rejected alternatives, Consequences). Context: spec §5.14, OQ56/61/65/66/67/69, ADR-0021 §1/§3, ADR-0017 §5. Decisions (defaults; Ming's answers replace them):
   - **§1 Scope set** (amends ADR-0021 §1):
     - Add three scopes: `usage:read`, `usage:write`, `limits:write`. Keep **exactly one scope per key** and **at most 2 active keys per (app, scope)**.
     - Each scope's route set:
       - `usage:read`: the per-user limits/used/remaining read.
       - `usage:write`: consume, settle and refund, **plus the same per-user current-usage read**, so a backend that only meters needs one key (Q5).
       - `limits:write`: the app-admin actions of ADM-044 only.
     - All three are **refused for the `admin` app** at issuance (no AI metrics; same as the `manifest` refusal).
     - The error codes are unchanged: wrong scope gives 403 `app_credential_scope`, another app's key gives 403 `app_credential_mismatch`, a disabled app gives 403 `app_disabled`, and a key of the wrong type or an admin JWT gives the single 401 of ADR-0016 §4.
   - **§2 Runtime-key rule.**
     - The three usage scopes are *runtime* keys. They live only in the app backend's deploy environment, as `MMLABS_USAGE_CREDENTIAL` (`usage:write`), `MMLABS_USAGE_READ_CREDENTIAL` and `MMLABS_LIMITS_CREDENTIAL`. They never go in CI secrets or browser or mobile bundles. No CORS.
     - A runtime key never shares a value with a CI scope (`manifest`, `releases`).
     - Apps without a backend cannot take part (consequence).
   - **§3 Auth hot path** (amends ADR-0021 §1, last bullet):
     - Runtime scopes **do not take `lock_app`**. Today's path (`app/modules/app_credentials/service.py:177-180`) stays for `manifest`/`releases` only.
     - `last_used_at` is written by a conditional `UPDATE … WHERE revoked_at IS NULL AND (last_used_at IS NULL OR last_used_at < now() - 60 s)`.
     - Revocation and app disable stay immediate, because every request reads the key and app rows. No caching. A request already in flight while a revoke happens may complete; this is accepted and documented.
   - **§4 `on_behalf_of`** (Q3, Q4, Q7):
     - **Where it may appear.** It is required on every `limits:write` call. If it is missing there → 422. If any other scope sends it → 422.
     - **Proof of the user (default).** It is the app admin's own access token, sent in the request (exact header or field at ADM-044 PLAN). Checks:
       - the signature is valid against this API's own keys (`kid`);
       - `aud` = the key's app;
       - not expired;
       - `sub` is the acting user.
     - **Live DB checks on every call**, never from claims:
       - the user is active and has a verified email;
       - the user holds `<app>:usage:manage` through an assignment with `org_id IS NULL` on the key's app;
       - the target user is a member of that app.
     - **Self-grant refused:** acting user = target user on a per-user grant → 403. Changes to app defaults and periods stay allowed.
     - **Reason** 1–500 chars (OQ67).
     - **Audit.** Actor = the user. `changes` holds `via_credential = <key_id>` and the reason. A denied `on_behalf_of` writes a `denied` row with `SystemActor("app:{key}/{key_id}")`.
     - **Error codes** (names are ADM-044's): failed proof or failed checks → 403.
   - **§5 Idempotency** (OQ66, OQ69):
     - **The key.** Every consume carries an `idempotency_key` body field: ≤ 128 printable ASCII characters, unique per **(app, idempotency_key)**. The scope is the app, not the key id, so a refund still works after a key rotation.
     - **Payload fingerprint.** Each key is bound to a fingerprint (user, metric, operation, amount). A replay with the same fingerprint returns the first result. A different payload → 409 `idempotency_key_reused`.
     - **State machine** on one key: consume → at most one settle → at most one refund.
       - Settle and refund are themselves idempotent and are charged to the period of the original consume.
       - A refund never credits more than was charged.
       - A settle above the estimate is accepted even past the limit, because the cost is already incurred; it is recorded and never answered with 409.
     - **Amounts** are positive integers with a per-call maximum (in config, value at ADM-042); a negative amount or zero → 422.
     - **Retention.** Idempotency records are kept at least as long as events (90 days).
   - **§6 Fail-closed and fail-open grace** (Q6):
     - Fail-closed is the default. Fail-open grace is a per-app setting that **only staff with `admin:usage:write`** can change. It is audited, and `limits:write` cannot change it.
     - Apps may apply the grace **only** on transport errors, timeouts and 5xx. Never on 401, 403, 409, 422 or 429.
     - Late reports:
       - are accepted only while the setting is on;
       - use the same idempotency keys;
       - are dated at most **24 h** back and counted in the original period;
       - may record overage, never 409;
       - are subject to a per-user grace cap (value at ADM-046 PLAN).
     - The ADR states plainly that enforcement during the grace is client-side only.
   - **§7 Rate limits** (amends ADR-0021 §3 for runtime scopes):
     - three buckets: a generous per-IP bucket before auth, a stricter per-IP bucket for failed auth, and a **per-key** bucket after auth;
     - values in `app/core/config.py` (R5), set at ADM-042;
     - note the ADR-0002 caveat (in-process, so the effective limit is N workers × the setting);
     - note the separate latency target required by spec §6 / §5.14, also set at ADM-042.
   - **§8 Token issuance stays ungated.** Reasons:
     - ADR-0017 §5;
     - limits are per metric and per period and change mid-period, which a claim cannot express;
     - claims can be up to 12 h stale (ADR-0005 amendment, `ACCESS_TOKEN_TTL_SECONDS` ≤ 43200), while consume is atomic and current;
     - gating login would turn a quota problem into an auth outage.
   - **§9 Data exposure.** `usage:read` returns only that app's metrics for a member of that app, by user id, with no email (R11). Consume and refund are not audit entries (spec §5.14 "Volume and audit"). Staff and app-admin changes are.
   - **Rejected alternatives:**
     - in-place edit of ADR-0021;
     - multi-scope runtime keys;
     - bare-UUID `on_behalf_of` (if Q3 stays at the default);
     - OAuth client credentials (ADR-0016 §2);
     - gating token issuance;
     - fail-open on any error.
   - **Consequences:**
     - blast radius per scope: a leaked `usage:write` key can burn or refund any member's quota; a leaked `usage:read` key exposes all members' usage; a leaked `limits:write` key is useless without a live app-admin token (under the Q3 default);
     - the availability coupling;
     - which tickets change `tests/test_route_auth_classes.py`: ADM-034, ADM-042, ADM-044.
3. **ADR-0021 back-pointer.** One line under its Status.
4. **CLAUDE.md R3 class 4** (Q2 default: generic text written once). It folds in the not-yet-applied ADR-0021 §12 text, so ADM-034, ADM-035, ADM-036, ADM-042, ADM-044 and USR-29 only flip a marker or add a scope word. Replace the text from "(4) **app credential** (D25, ADR-0016)" through "per-IP rate-limited." with:

   > (4) **machine credential** (D25, D30, D31; ADR-0016, ADR-0021, ADR-0022) — opaque keys issued by staff (`admin:*`), stored only as a SHA-256 hash, sent as `Authorization: Bearer …`, server-side only (never in browser or mobile code), no CORS, rate-limited (per IP before auth; runtime scopes also per key after auth), accepted only on the routes its ADR lists for it (registry: `tests/test_route_auth_classes.py`) and on no other route; a key of the wrong type or an admin JWT → 401: (a) **app credential** `mmak_<key_id>_<secret>` with exactly one scope, accepted only on that scope's routes and only for its own app (another app → 403 `app_credential_mismatch`, wrong scope → 403 `app_credential_scope`). Scopes: `manifest` — `PUT /api/v1/apps/{key}/manifest` (live; until ADM-034 adds the scope column every key is `manifest`); `releases` — the app's release write (ADM-034); `usage:read`, `usage:write`, `limits:write` — runtime keys, refused for the `admin` app (ADM-042, ADM-044; `limits:write` requires a verified `on_behalf_of`, ADR-0022 §4); (b) **deployer credential** `mmdk_<key_id>_<secret>` — only the release deployment report (ADM-036), for any registered app.

   R9's exception list is **not** touched here. The releases and usage path exceptions land with their implementing tickets (R7).
5. **D31 row:** "**Usage key scopes (owner, 2026-11-2x, ADM-041):** app-credential scopes `usage:read`, `usage:write` (also the per-user usage read), `limits:write` (runtime keys, one scope per key, refused for `admin`); no `lock_app` on runtime auth; `on_behalf_of` [bound to the app admin's access token]; per-app idempotency keys; fail-closed default, staff-only fail-open grace; per-key rate limit; issuance stays ungated. R3 class 4 rewritten scope-generic. Amends ADR-0021. See ADR-0022."
6. **Spec note** (only if Q3 is accepted): see the file table.
7. Run `make check`, then self-check AC6 and AC7 with `git diff --name-only` and grep.

Rules: R3 (the text itself), R7 (renumber, R9 entries and ticket references stay out), R10 (no billing: usage counters only), R11 (no full emails in exposure, verified-email requirement extended to the `on_behalf_of` actor).

## Specialist input used
- **software-architect**
  - R-A adopted as the prerequisite solo change (Q1). Not done inside this ticket, per R7.
  - R-B adopted: a new ADR-0022 amending ADR-0021, plus D31.
  - R-C adopted as the Q2 default: generic R3 text, which also folds in ADR-0021 §12 so it is written once.
  - R-D adopted, with two differences: idempotency is scoped per (app, key) rather than per (app, key id), so a refund survives a key rotation (security's reading); and `on_behalf_of` is bound to the app admin's token (security / identity-access).
  - Open questions 1–5 map to Q1, Q2, Q5, Q6 and Q7.
- **security-architect**
  - Adopted: no `lock_app` and a throttled `last_used_at` on runtime scopes; three-bucket rate limits with the per-worker note; full `on_behalf_of` live checks; token binding offered as the default (Q3); idempotency 409 on a payload mismatch and 90-day retention; the settle/refund caps; fail-open staff-only, 24 h age limit and per-user cap; ungated-issuance reasons; `usage:read` without emails; refusal for the `admin` app.
  - Not adopted here: "fix the release ADR's stale D28 in the same PR" (the solo renumber does it), and the in-place R3 rewrite in two passes (replaced by the single generic text).
- **identity-access-engineer**
  - Adopted: R-1 (adjusted to 0022/D31 because of the renumber); R-2, with `usage:write` also covering the per-user read (Q5); R-3; R-4; R-5 (never fail-open on a 4xx or 429); R-6 (fingerprint, `idempotency_key_reused`, ≤ 128 ASCII); R-7 (`org_id IS NULL`, 422 when missing, actor = the user with `key_id` in details); R-8; R-9.
  - Changed: R-6 allowed one or more settles; this plan allows at most one settle, which is simpler to reason about. ADM-042 can relax this later with a new ADR.

## Assumptions
- **Order.** The solo renumber (ADR-0018 release log → ADR-0021, D28 → D30, with references updated in the spec, ADM-033 to ADM-038, USR-29 and the board) merges to `main` before this ticket's build. The implementer rebases onto it.
- **Hotspots in the same batch:**
  - `CLAUDE.md`: ADM-035 may touch R3 (consults ask who applies the §12 text) and ADM-039 may add an R9 exception.
  - `docs/identity-architecture.md`: ADM-035 and ADM-039 may add D-rows.
  - The orchestrator should build ADM-041 **before** ADM-035 and ADM-039, or have them rebase. Whoever merges second takes the next free D-number; if a D-row lands before this one, D31 shifts (the implementer re-reads §8 and uses the next free number).
  - Once Q2 is accepted, ADM-034, ADM-035, ADM-036 and USR-29 must edit the generic R3 text, not paste the old ADR-0021 §12 text. USR-29 then adds its scope word to the list.
- **Tier.** The PR is **Medium** because of `CLAUDE.md`, even though it is docs-only: no auto-merge, and Ming's "cho merge" is needed.
- **Left to the implementing tickets** (PLAN items the ADR names but does not size): exact routes, header vs field for the `on_behalf_of` token, numeric rate limits, per-call amount maximum, the consume latency target (spec §5.14 "Availability coupling"), and the error-code names for `on_behalf_of` failures.
- **Follow-ups** (not in this diff):
  - ADM-042, ADM-044 and ADM-046 ticket text should cite ADR-0022.
  - `CLAUDE.md` header says "decision log D1–D12" and `loop.config.md` says "D1–D25", both stale.
  - Rename the ticket and board title.

## Blocking questions
Status is READY: the plan uses every default below. Ming confirms or overrides each one in the batch round.

1. **Renumber prerequisite.** Should the release-log ADR become ADR-0021 (its D28 becoming D30) through a separate `solo:` change on `main` before this build, with ADM-041 writing ADR-0022 / D31? **Default: yes.** If no, this ticket writes ADR-0021 / D30 amending `0018-release-log-and-deployer-credential.md` by full filename, and the plan is otherwise unchanged.
2. **R3 class 4 written once, scope-generic (architect R-C).** Should ADM-041 replace R3 (4) with the generic machine-credential text in Approach step 4? That text names scopes and marks each with its pending ticket, instead of listing every route, and it also absorbs the unapplied ADR-0021 §12 text. **Default: yes.** If no, R3 stays as it is today, ADR-0022 gives the final text, and ADM-034 applies both (AC3 then becomes "ADR-0022 contains the R3 text").
3. **How `on_behalf_of` proves the user (OQ61).** Options: a bare user id, as OQ61 is worded, or a binding to the app admin's own access token (`aud=<app>`, `sub` = acting user, unexpired, signature checked; DB checks still live)? With a bare id, a leaked `limits:write` key plus any app admin's UUID acts as that admin across the whole app. **Default: bind to the token**, and add a dated note to spec §5.14 / OQ61.
4. **Self-grant.** May an app admin raise or lower their own per-user limit through `limits:write`? **Default: no** (403). Staff (`admin:usage:write`) still can. App-wide defaults and periods are allowed even though they also apply to the admin.
5. **2 vs 3 keys.** Keep three single-scope keys, with `usage:write` also allowed on the per-user current-usage read so a typical backend holds 2 keys (`usage:write`, plus `limits:write` if it has an app-admin UI)? The other option is multi-scope runtime keys. **Default: three scopes, one scope per key, `usage:write` includes the read.**
6. **Fail-open grace.** Is it staff-only to enable (`admin:usage:write`, audited, never through `limits:write`), triggered only by transport errors, timeouts or 5xx (never a 4xx or 429), with a late-report maximum age of **24 h** in the original period and a per-user grace cap (value at ADM-046)? **Default: yes to all.**
7. **Verified email for the `on_behalf_of` actor.** R11 requires a verified email only to join an app or get a token. Should it also apply here? **Default: yes** (an unverified account cannot hold an app token anyway under R11, so this only hardens the bare-id fallback).
8. **`admin` app.** Should all three usage scopes be refused for the `admin` app at issuance? **Default: yes** (no AI metrics; the role catalog already returns 404 for `admin`).

## Split proposal
None. About 150–250 changed lines across four or five docs files.
