# Copilot Instructions — Fazle Core

FastAPI + asyncpg WhatsApp-first operations backend for Al-Aqsa Security &
Logistics Services Ltd. No ORM, no build step — runs directly off this git
working tree. Root: `/home/azim/core`.

## Orientation

Read in this order before making non-trivial changes:

1. `README.md` — overview, ports, restart/health commands, RBAC table.
2. `SYSTEM_ATLAS.md` — canonical doc map.
3. `docs/DEVELOPER_GUIDE.md` — module-by-module reference.
4. `docs/MODULE_MAP.md` — module → route → table mapping.
5. `knowledge_base/00_governance/CANONICAL_BUSINESS_RULES.md` — business rules that take precedence over code comments if they disagree.

Business-rule questions (payment parsing, RBAC, payroll, escort lifecycle)
are answered faster by `knowledge_base/04_business_rules/` and
`knowledge_base/05_workflows/` than by tracing the call graph — check there
first.

## Conventions

- Modules live under `modules/<name>/` (see `modules/` for the full list — attendance, payroll, escort_roster, client_billing, payment_ingest, rbac, rag, etc.), each typically with `__init__.py` (logic) and `routes.py` (FastAPI router), mounted in `app/main.py`.
- No ORM: raw SQL via `asyncpg`, helpers in `shared/`. Follow the query style already in a module rather than introducing a new pattern.
- DB migrations live in `db/migrations/`, tracked in `db/MIGRATION_INVENTORY.md`. Two other legacy migration directories exist — do not add new migrations there.
- `fpe_cash_transactions` is the only canonical cash ledger table. `wbom_cash_transactions` is a legacy archive — never write new rows to it.
- RBAC roles are DB data, not hardcoded constants: `fazle_roles` table, hierarchy `viewer(10) < office_assistant(20) < operator(30) < accountant(50) < admin(70) ≤ superadmin(100)`. Shared dependency helpers are in `shared/auth_deps.py`.
- Frontend pages are static HTML/JS under `app/static/`, each using its own `localStorage` key for the `X-Internal-Key` API auth header (`fazle_api_key`, `fpe_api_key`, `drafts_api_key`, `kb_api_key` — not unified, don't assume one covers all pages).

## Do not

- Do not write to `wbom_cash_transactions` or introduce a second cash-ledger write path.
- Do not add a DB migration or schema change unless the task explicitly calls for one.
- Do not hardcode role/permission checks that bypass `fazle_roles` / `shared/auth_deps.py`.
- Do not restart `fazle-core.service` directly in scripts/automation — use `scripts/restart_gate.sh --restart`, which runs pre-restart checks (migrations applied, tests green, no leaked secrets).
- Do not commit `.env`, backup `.env.bak.*` files, or any credential — run `scripts/secret_scan.sh` before committing if config files were touched (this repo previously had a leaked production DB password in git history).

## Testing

- `tests/pytest.ini` is the config actually used for `pytest tests/...` runs; root `pytest.ini` only makes a bare `pytest` invocation from repo root safe.
- `tests/conftest.py` enforces e2e test isolation via a collection hook — don't work around it.
- Run `make test-unit` (fast) before proposing a change is done; `make test-integration` / `make test-resilience` need a test DB (`TEST_DB_PASSWORD` env var required, no default).
- Prefer editing/extending an existing test file over adding a new one for a change to existing behavior.

## Docs stay in sync

If a change alters behavior documented in `knowledge_base/**/*.md` or
`docs/*.md`, update that doc in the same change. The knowledge base is
RAG-indexed and read by the in-app AI console as a runtime source of truth,
not just human-facing reference material.
