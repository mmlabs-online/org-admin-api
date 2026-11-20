# ADR-0024: Usage metrics: identity, periods and change rules

Status: Proposed (accepted when the owner merges ADM-039)
Date: 2026-11-20

## Context
Spec §5.14 (OQ55, 61, 63, 64, 67, 68): each app has its own usage metrics, each with a unit, a
default limit and a reset period. Staff configure them through the admin API (ADM-039); per-user
overrides (ADM-040), consume/settle (ADM-042) and app-admin changes (ADM-044) build on the same
model. The owner approved the defaults at the plan gate (batch 2026-11-20-1, questions 13-20).

## Decision

1. **Identity.** A metric is `(app, key)` with an immutable `key` (`^[a-z][a-z0-9-]{1,47}$`) and an
   immutable `unit` (`^[a-z][a-z0-9-]{0,31}$`). Metrics are never deleted: they are archived
   (`status` `archived`, reversible through PATCH) and the key stays reserved so usage history is
   never orphaned. The `admin` app has no metrics (404). Ids are UUIDs; `UNIQUE (id, app_id)` lets
   later tables use a composite FK `(metric_id, app_id)` that proves same-app. An archived metric
   refuses consume (ADM-042).
2. **Limit.** `default_limit` is a non-negative integer up to 2^53-1 (JS-safe); 0 blocks the metric.
3. **Periods.** Values are `day`, `week`, `month` (the spec's "daily/weekly/monthly"). Periods are
   calendar periods in `Asia/Ho_Chi_Minh`: day at 00:00, week on Monday 00:00, month on the 1st
   00:00, no rolling windows. `app/modules/usage/periods.py` is the only place that computes them;
   bounds are `[start, end)` in UTC. There is no app-level period setting in v1: a new metric's
   period defaults to the constant `week`; a later ticket may add one without a breaking change.
4. **Change rules.** A default-limit change applies to the running period at once; it is read live
   and never copied into per-user rows (ADM-040 stores overrides only). A period change applies at
   once: the running period becomes the calendar period of the new kind that contains now, and its
   usage is what was consumed since that period's start (ADM-042 keys totals by
   `(metric, user, period, period_start)`, so old buckets are not reused).
5. **Reason.** Every usage-config or limit write requires a `reason` of 1-500 characters (trimmed,
   not blank, NFC, no control or bidi characters). It is stored as a flat string `changes.reason`
   next to the `{from, to}` diffs of the audit entry, never returned by the API and never logged.
6. **Audit.** `usage_metric.created`, `.updated`, `.archived`, `.restored`. A PATCH that changes
   nothing writes nothing. A PATCH that changes `status` and other fields writes two rows, both
   with the reason.
7. **Actors.** `UsageMetricService` takes any `Actor` and never checks permissions (the route
   dependency does). ADM-044 adds an app-admin actor limited to `default_limit` and `period`, and an
   audit `actor_kind` extension for it.
8. **Access.** `admin:usage:read` and `admin:usage:write` (`is_admin`, granted to no role, covered
   by `admin:*`). PATCH supports optional `If-Match` (412 `precondition_failed`).

## Consequences
Metrics cannot be removed, only archived; renaming a unit means a new key. Disabled apps stay
configurable (config is inert). Deleting an app that has metrics is refused by the FK (generic 409);
a specific reason is a follow-up.
