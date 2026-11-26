# Consult — api-platform-engineer — USR-40 (2026-11-26)

**Findings**
- `list_mine` (app/modules/organizations/auth_service.py:167-189) checks org_mode, resolves the user, calls `repo.list_for_user_app` (repository.py:104-153). `MyOrgRead` (schemas.py:63-68: id, slug, name, status, roles) has no `access` field.
- The repository runs 3 queries: count (122), page ordered by `Organization.name, id` (125-133), one batched roles query over the page's org ids (134-152). The `held` subquery (111-121) filters on user, app, `org_id` not null, unexpired (DB clock).
- The current list does not filter on org status: a member sees their suspended orgs (`status` in the item). "Unchanged for non-holders" keeps suspended member orgs; only the extra super_admin orgs are active-only (OQ89). The ticket text "suspended orgs are not listed" is ambiguous for member orgs.
- docs/consumers.md:155-160 is the picker flow; line 151 describes `org_access_denied` as "holds no role" (USR-39 changes it).
- R9: an additive always-present field is safe in v1. R3 class (2), cookie: no change.
- USR-40 cannot ship before USR-38 (`apps.super_admin_role_id`).

**Recommendations**
1. `access: Literal["member","super_admin"]` on `MyOrgRead`, no default, always serialized; clients must tolerate unknown values.
2. One predicate for the list and the USR-39 mint: a shared repository helper `is_super_admin_holder(user_id, app)` and an "org of the app" `EXISTS` clause; pass `holder: bool` into `list_for_user_app`.
3. Single paged query over `member_exists OR (holder AND status='active' AND app_org_exists)`, selecting `access` via `CASE WHEN member_exists THEN 'member' ELSE 'super_admin' END`; the count uses the same WHERE (correct `total`). Keep `ORDER BY name, id`.
4. Roles batch query unchanged: member orgs get roles, super_admin orgs `[]`; member path wins.
5. Constant query count (user, holder check, count, page, roles = 5; 4 if the holder check is folded in). Assert with an SQL event listener at limit 1 and 50, holder and non-holder.
6. Holder check = unexpired app-scope assignment of the designated role, same expiry rule as mint; under the OQ82 mint-time re-check the list must also re-check `admin:super-admin` live via the shared helper, or the picker shows orgs the mint refuses.
7. Tests: holder with mixed member + extra orgs, ordering across page boundaries, offset past the member orgs, suspended org / org of another app, non-holder unchanged except `access`, role lost, holder with `org_mode = none` (still `org_not_supported`).
8. Index: `ix_role_assignments_org_id` + join to `roles` is fine now; follow-up if orgs exceed ~10k.

**Risks**
- Picker and mint disagree if predicates diverge (2, 6).
- An unexpired assignment of any user (incl. disabled accounts) makes an abandoned org appear — harmless, state it.
- Name-ordered offset over a mixed list stable only with the `id` tiebreaker (present).
- Existence and names of every org exposed to staff holders — intended, gated by the holder check.

**Open questions for Ming (defaults)**
- OQ89 "org of the app": (b) orgs with an unexpired role assignment of the app or an active entitlement for it (whatever `OrgEntitlement` uses), active status only.
- OQ89 suspended orgs: hidden in the picker and refused at mint for holders; member orgs still listed with `status`, as today.
- OQ84 `access` with `member|super_admin`, always present — yes.
- OQ82 list re-checks live `admin:super-admin` — yes, shared helper.
