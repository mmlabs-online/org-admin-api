# Impl log — ADM-037

## Attempt 1
### Files touched
- `docs/release-log.md` (new): guide; contracts checked against `app/modules/releases/{app_router,deployment_router,write_schemas,rules,errors}.py`, config caps (256 KiB / 4 KiB, 30/min) and `app_credentials/{schemas,service}.py`. Includes the ADM-038 note (`app_version` = exact release version string).
- `docs/deploy.md`: `releases`-scope issuance, per-(app, scope) limit, `admin` app allowed for `releases` only; link from the deployer section.
### Tests added
None (docs only).
### Checks
- Both YAML snippets parsed with `yaml.safe_load`.
- No key-shaped strings in the docs (grep clean).
- `uv run ruff check app tests` and `uv run mypy app`: see commit run, unchanged code.
- Manual curl run against `make dev` not performed (no local server/DB here); contracts verified from code instead. For Ming/reviewer.
### Out-of-plan files
None.
### Follow-ups
- Manual curl test plan (criterion 4) still to run; `docs/consumers.md` pointer skipped (no natural place).
### Scope self-check
Only docs/** and work/tickets/ADM-037/**.
