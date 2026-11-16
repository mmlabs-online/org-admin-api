Status: READY

# Plan — ADM-014: Docs — admin UI hostname and forgot-password credentials

## Spec summary
Docs-only ticket under spec §6.1 Deployment ("Runbook: `docs/deploy.md`"). The admin UI hostname comes from spec §5 (l.74):

> Context (owner, 2026-11-16): the admin UI **`admin-pan`** (repo `mmlabs-online/admin-pan`, served at `https://admin-portal.mmlabs.online`) is a browser SPA that calls this API with Bearer `aud=admin` tokens.

Ticket owner note (2026-11-16): "admin UI hostname is `admin-portal.mmlabs.online`; `admin.mmlabs.online` is not used."

Related: ADR-0008 (ADM-002c amendment, admin origins), ADR-0011 (admin CORS, already says `admin-portal`), ADM-002c. Dependency ADM-002e is `DONE` (work/backlog.md l.34), so the runbook block in `docs/deploy.md` is already in its final post-ADM-002e form. Edit that block, and do not rebase onto anything else.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | `docs/deploy.md`: "Host policy", every `set-admin-origins` example and the admin-token runbook use `https://admin-portal.mmlabs.online`. The doc states that `admin.mmlabs.online` must have no DNS record or tunnel route. | (a) Read deploy.md: runbook step 4, the EXIT trap `Origin:` header, the login and token `Origin:` headers, the fallback `--origin`, the `make set-admin-origins` line and both expected-output lines all say `https://admin-portal.mmlabs.online`. (b) The Host policy paragraph names `admin-portal` as the only production admin origin. It also says `admin.mmlabs.online` is not used and must have no DNS record (wildcard included), no tunnel route (catch-all included), and no entry in any app's `origins` or in `TURNSTILE_ALLOWED_HOSTNAMES`. (c) The diff of the runbook lines changes hostname text only (no quote changes). |
| 2 | ADR-0008 amendment and any other doc that names `admin.mmlabs.online` as the admin origin are updated. A grep check shows no remaining reference except the "must not exist" note. | Reviewer runs `grep -rnE '(^\|[^-[:alnum:].])admin\.mmlabs\.online' docs README.md CLAUDE.md Makefile`. The only expected hits are the "not used / must not exist" sentences in `docs/deploy.md` (Host policy, and the ADM-012 host-move sentence if it names the host) and in the dated ADR-0008 note. The boundary class stops `api-admin.mmlabs.online` in `docs/spec-v1.md` l.128/143 from matching (historical API hostname, not the admin origin). A naive `grep admin\.mmlabs\.online` would report those two lines as false positives. `tests/` and `work/` are excluded on purpose (see Assumptions A2). |
| 3 | `docs/consumers.md`: forgot-password, verify-email and reset work with `credentials: 'omit'`. The guide no longer recommends `include` for those calls. | Read consumers.md. (a) "Every call: include" (l.39) is replaced by a two-part rule: `include` for login and the cookie-class calls (`/token`, `/accounts`, `/apps/{key}/join`, `/logout`, `/verify-email/resend`), and `omit` for signup, forgot, verify-email and reset. (b) The forgot paragraph (l.161) says `credentials: "omit"`, and the "so CORS answers the preflight" rationale is gone. (c) The verify-email (l.129) and reset (l.183) examples contain `credentials: "omit"`. (d) `grep -n 'so CORS answers the preflight' docs/consumers.md` returns nothing. (e) Login is explicitly `include` and the doc says only login sets the cookie. |
| 4 | No code change. Runbook blocks still pass `sh -n`. | (a) `git diff --name-only main...HEAD` lists only the files in the table below (Makefile change is a `##` help comment only, see A1). (b) `awk 'NR>=440 && /^   ```sh$/{f=1;next} f&&/^   ```$/{exit} f' docs/deploy.md \| sh -n` exits 0. Adjust the start line if lines shifted, or extract the block by its "```sh" fence after "Log in, get a token". (c) The fallback block: `awk '/^```bash$/{f=1;b="";next} f&&/^```$/{f=0; if(b~/set-admin-origins/) print b; next} f{b=b $0 "\n"}' docs/deploy.md \| bash -n` exits 0. (d) `make help` (or `grep set-admin-origins Makefile`) shows the new host. (e) `make check` green (unchanged code, sanity only). |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `docs/deploy.md` | Replace the 9 `admin.mmlabs.online` occurrences (l.433, 446, 455, 457, 505, 508, 517, 518) with `admin-portal.mmlabs.online`. Rewrite the Host policy paragraph (l.551–554). Reword l.482–483 ("until an admin UI exists"). Amend the ADM-012 step 1 host-move sentence (l.208–209). | AC 1 |
| `docs/adr/0008-admin-api-authentication.md` | In the ADM-002c amendment (l.55–57), add a dated note (owner, 2026-11-16) saying production uses `https://admin-portal.mmlabs.online` and `admin.mmlabs.online` is not used. Mark the old wording as superseded rather than deleting it silently. | AC 2 |
| `docs/consumers.md` | Credentials rule (l.39–41), signup paragraph (l.112–113), forgot paragraph (l.161), `credentials: "omit"` in the verify-email (l.129) and reset (l.183) examples. | AC 3 |
| `Makefile` | l.29 `##` help comment only: `--origin https://admin-portal.mmlabs.online`. No recipe change. | AC 2 (scoped grep must be clean); see A1 |

No other file may change. Do not edit `tests/` fixtures, `work/tickets/*` history, `app/`, `migrations/` or the ticket files except `impl-log.md`.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| None (no code change; AC 4 forbids code). | Verification is the reviewer commands in the AC table: the scoped grep, `sh -n` / `bash -n` on the extracted blocks, `make check` unchanged green. |

## Approach
1. **deploy.md runbook (AC 1).** Replace the hostname text in l.433, 446, 455, 457. Inside the l.446 trap, change only the characters `admin.mmlabs.online` to `admin-portal.mmlabs.online`, and touch no quotes or backslashes. Afterwards run `sh -n` on the extracted block (AC 4b).
2. **deploy.md "Set admin origins (ADM-002c)".** Replace in l.505, l.508, and both expected-output lines l.517–518.
3. **deploy.md l.481–483.** Reword the last sentence to something like: "The admin UI (`admin-pan`, `https://admin-portal.mmlabs.online`) is the browser path; operators without it use this curl runbook. The browser console is not a supported path."
4. **deploy.md Host policy (l.551–554).** Replace it with a paragraph covering (consult R-2):
   - `https://admin-portal.mmlabs.online` is the admin UI host (`admin-pan`, ADR-0011) and the only production entry in `apps[admin].origins`.
   - Its DNS record and tunnel route point only at `admin-pan`, and are never shared with another service or user content.
   - Check it whenever DNS or tunnel routes change. Whoever serves pages on an admin origin can drive the admin login and token flow (subdomain takeover = admin compromise).
   - `admin.mmlabs.online` is **not used** (owner, 2026-11-16). It must have no DNS record (also not through a wildcard `*.mmlabs.online`) and no tunnel ingress or catch-all route. It must not appear in any app's `origins` or in `TURNSTILE_ALLOWED_HOSTNAMES`. Reusing it needs an owner decision.
   - One-off: if `admin.mmlabs.online` was ever set in production, run `set-admin-origins --origin https://admin-portal.mmlabs.online`. It has replace semantics. Confirm the `- origin https://admin.mmlabs.online` line in the dry run, then run it for real.
5. **deploy.md ADM-012 step 1 (l.208–209).** Qualify "During a host move keep the old origin as the second allowed one" by adding: "this does not apply to `admin.mmlabs.online`, which was never a live admin UI host; do not keep it" (consult R-3).
6. **ADR-0008 (AC 2).** Under the ADM-002c amendment's "Dedicated host" bullet, add a dated sub-note: "Owner, 2026-11-16 (ADM-014): production uses `https://admin-portal.mmlabs.online` (admin UI `admin-pan`, ADR-0011). `admin.mmlabs.online` is not used and must have no DNS record or tunnel route (see `docs/deploy.md` Host policy)." Strike or rephrase the old clause so the ADR does not state two hosts as current. For example, change "production uses `https://admin.mmlabs.online`, reserved for the future admin UI (…)" to "production originally reserved `admin.mmlabs.online`; superseded below". Keep the change minimal and keep the accepted text recognisable.
7. **consumers.md (AC 3).**
   - l.39–41: replace "Every call: include" with two bullets.
     - `credentials: "include"` for **login** (reads the existing device cookie to add the account, and sets/rotates it) and every cookie-class call: `/token`, `/accounts`, `/apps/{key}/join`, `/logout`, `/verify-email/resend`. Using `omit` there silently breaks sign-in (the browser drops `Set-Cookie`) and multi-account.
     - `credentials: "omit"` for **signup, forgot, verify-email and reset**: they neither read nor set the cookie.
     - Keep the sentence that the custom header forces a preflight, and add that the credentials mode does not change whether CORS answers it. Optionally include the small endpoint/credentials table from the consult's F2 (recommended by the consult's Risks section to prevent over-correction).
   - l.112–113 (signup): `credentials: "omit"`. Remove "so CORS answers the preflight", and replace it with "signup sets no cookie; only login does".
   - l.161 (forgot): `credentials: "omit"`, still with `X-MM-App` from a registered origin. Delete the rationale clause.
   - l.129 and l.183: add `credentials: "omit",` to both `fetch` examples. Keep the "public endpoint: no cookie or `X-MM-App` needed" notes, and note that the page must still be on a registered app origin for the response to be CORS-readable (already implied by l.121).
   - Do not change l.63 (admin UI token via `include`, correct) or l.64–66 (admin endpoints `omit`, correct).
8. **Makefile l.29.** Change the help comment's host only.
9. Run the AC verification commands and `make check`, and record the outputs in `impl-log.md`.

Rules: R7 (the scope is the four files above; follow-ups go to the list below). R5/R11 (no secrets added; the runbook's credential handling is unchanged). R3 (auth classes unchanged; only consumer guidance is corrected to match the code in `app/modules/auth/router.py`). No R1/R2/R4/R6/R8/R9 impact (no code, schema or endpoint change).

## Specialist input used
From `consult-security-architect.md`:
- **F1 / R-1 (adopted):** the exact deploy.md occurrence list and the l.482–483 rewording.
- **R-2 (adopted):** Host policy content, including wildcard DNS, catch-all ingress, `origins` and `TURNSTILE_ALLOWED_HOSTNAMES`.
- **R-3 (adopted, as docs only):** the one-off replace check is written into the Host policy. The ADM-012 host-move sentence is qualified.
- **R-4 (adopted):** a dated ADR note instead of a silent rewrite.
- **R-5 (adopted with a fix):** grep scoped to `docs README.md CLAUDE.md Makefile`. I added a boundary class because the consult's plain pattern also matches `api-admin.mmlabs.online` in `docs/spec-v1.md` l.128/143 (verified by grep). The consult listed spec-v1.md as clean, which is true for the admin origin but not for the pattern.
- **R-6 (adopted):** consumers.md edits, including signup (open question 2 → resolved as A3).
- **F2 table (adopted as guidance; optional to embed):** endpoint/credentials mapping. Login stays `include`.
- **F3 (adopted):** rationale for `omit` (ACAO echo works for non-credentialed requests, the `Origin` header is still sent, so the `MailLinkAppContextDep` check still runs).
- **R-7 (deferred to follow-ups):** `@mmlabs/auth-client` audit and the identity-architecture.md l.17 diagram annotation (outside this ticket's files).
- **Open questions 3 and 4 (deferred):** production-state questions go to owner follow-ups (not blockers).

## Assumptions
Planner decisions for the owner to confirm. None blocks the ticket.
- **A1. Makefile help comment is updated in this ticket.** It is a `##` help string with no behaviour, and leaving it would make the scoped grep fail. "No code change" is read as "no behaviour change". The recipe lines stay untouched.
- **A2. The grep check is a reviewer command, not a pytest.** AC 4 says no code change. It is scoped to `docs/ README.md CLAUDE.md Makefile`. `tests/` fixtures (`tests/test_cli_set_admin_origins.py`, `test_admin_guards.py`, `test_auth_join.py`, `test_auth_app_context.py`, `test_cli_register_app.py`) keep `admin.mmlabs.online` as an arbitrary valid D19 origin. `work/tickets/*` is history and is left alone.
- **A3. Signup moves to `omit` too.** The ticket names forgot, verify-email and reset. Signup neither reads nor sets the cookie (`POST /signup` has no `Response` param and `SignupService` has no device logic, consult F2), and l.112 repeats the same wrong "so CORS answers the preflight" rationale. Fixing it here is in the spirit of AC 3. The owner can revert this one paragraph if they prefer `include` for signup.
- **A4. Login and all cookie-class calls stay `include`.** Login both reads (multi-account, D5/D6) and sets the cookie.
- **A5. The ADR is amended with a dated note** (owner, 2026-11-16, ADM-014) rather than a silent rewrite of the accepted ADM-002c text.
- **A6. `admin.mmlabs.online` was never a live admin UI host.** So the "keep the old origin during a host move" overlap does not apply. If it was set in production, the documented one-off replace run removes it (see follow-up F-1).

## Blocking questions
None.

## Split proposal
None. Four doc files, roughly 40–60 changed lines.

## Data model / migration
None.

## API contract
None. No endpoint change; consumers.md guidance is aligned with existing behaviour.

## Follow-ups (not in this diff, for the owner)
- **F-1 (owner, production state):** was `https://admin.mmlabs.online` ever set on production `apps[admin].origins`? If yes, run `set-admin-origins --origin https://admin-portal.mmlabs.online` (dry run first, confirm `- origin https://admin.mmlabs.online`). If no, nothing to do.
- **F-2 (owner, DNS/tunnel):** does `mmlabs.online` have a wildcard DNS record or a catch-all tunnel ingress? If yes, exclude `admin.mmlabs.online` explicitly. Otherwise "no DNS record" alone does not make it unreachable.
- **F-3:** check that `@mmlabs/auth-client` (repo `mmlabs-auth`) uses `include` only for login and cookie-class calls and `omit` for signup/forgot/verify/reset (consumers.md l.32: the page wins, the library gets a fix).
- **F-4 (optional):** annotate the `docs/identity-architecture.md` l.17 diagram with `credentials: include` for cookie-class calls only.
- **F-5 (owner, deployment repo):** make sure `TURNSTILE_ALLOWED_HOSTNAMES` in `mmlabs-online/deployment` does not list `admin.mmlabs.online`.
