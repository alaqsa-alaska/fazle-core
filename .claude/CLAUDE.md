# Fazle Core — Agent Config

Verified against the live server on 2026-07-13. If anything here conflicts
with what you observe on disk or in the running service, trust what you
observe and treat this file as stale — then update it.

## What this is

FastAPI + asyncpg WhatsApp-first operations backend for Al-Manaratayn Skill
Solutions (rebranded 2026-07-21; formerly Al-Aqsa Security & Logistics Services
Ltd) — attendance, escort duty, payroll, cash payments, billing, recruitment,
AI console. No ORM. No build step — runs directly off
this git working tree via `run.py`. Root: `/home/azim/core` (not `/root/...`
— verify any path a human gives you against the live filesystem before
trusting it).

## Read this first

1. `README.md` (root) — system overview, ports, restart/health commands, RBAC table, auth mechanism.
2. `SYSTEM_ATLAS.md` — canonical entry point / doc map, answers "where do I look for X."
3. `docs/DEVELOPER_GUIDE.md` — module-by-module developer reference (sections B, C, E–H, J, K).
4. `docs/MODULE_MAP.md` — module → route → table mapping.
5. `knowledge_base/00_governance/CANONICAL_BUSINESS_RULES.md` — business rules that override anything else if they conflict.

For any business-logic question (payment parsing, RBAC, payroll, escort
lifecycle), check `knowledge_base/04_business_rules/` and
`knowledge_base/05_workflows/` before reading code — the KB is kept
synchronized with runtime behavior and is faster to read than tracing the
call graph.

## Non-negotiable rules

- **No feature/business-logic change without it being explicitly asked for.** Several past tasks in this repo were docs-only or fix-only with an explicit "don't touch anything else" scope — respect scope as given, don't drift into adjacent cleanup or refactors.
- **No DB schema change unless a task explicitly asks for a migration.** `fpe_cash_transactions` is the only canonical cash ledger table (Owner Directive 2026-06-29) — `wbom_cash_transactions` is legacy, never write to it.
- **Never restart the production service casually.** Use `scripts/restart_gate.sh --restart` (9 checks: migrations applied, tests green, no leaked secrets, env flags acknowledged, etc.) — it is deliberately manual, not auto-wired into CI. `sudo systemctl restart fazle-core.service` directly, without the gate, skips those checks.
- **Never commit secrets.** Run `scripts/secret_scan.sh` before committing if you touched `.env`-adjacent files, Makefile, or test config — this repo had a real leaked production Postgres password in git history (rotated 2026-07-12); don't repeat that mistake.
- **RBAC hierarchy is data, not code.** Roles live in the `fazle_roles` DB table: `viewer(10) < office_assistant(20) < operator(30) < accountant(50) < admin(70) ≤ superadmin(100)`. Don't hardcode role assumptions — check the live table or `shared/auth_deps.py`.
- **Two employee identity systems exist and are bridged, not merged.** `wbom_employees` (operational) and `fpe_employees` (payroll-engine) link via `fpe_employees.wbom_employee_id`. Don't assume one is a subset of the other without checking.
- **Docs and KB must stay in sync with code.** If you change behavior described in `knowledge_base/**/*.md` or `docs/*.md`, update the doc in the same change — this repo's KB is RAG-indexed and treated as a runtime source of truth by the AI console, not just human reference.
- **`modules/admin_directives` is write-capable, unlike almost every other AI-Console-adjacent module.** (2026-07-30) Its writes are always human-triggered — an admin's own typed WhatsApp command (`REMEMBER`/`FORGET`/`AUTOREPLY`) or an authenticated `/admin/directives*` web request — never something the LLM decides on its own; `orchestrate_console_request()`'s `AI <question>` path only ever reaches read-only adapters. Don't assume "AI Console tool" implies read-only in this codebase anymore — check the specific tool (`BUS-016` is the one exception, see `docs/MODULE_MAP.md`).
- **`phone_block.{phone}` is now live** (2026-07-30) — `modules.bridge_poller._is_draft_always()` reads it before its static env-list checks. It used to be a dead flag (written by `/api/wa/contacts/{phone}/block` but never read); it isn't anymore. Don't reintroduce a second, uncoordinated per-number gate mechanism — this is now the one.
- **When making any function async, grep repo-wide for every call site, not just the file you're editing.** (2026-07-30 lesson, `modules/social_auto_reply/planner_worker.py`) An underscore-prefixed "private" function is not a reliable signal that nothing outside its module calls it — `bridge_poller._is_draft_always()` had a second caller in a different module that a file-local grep missed. Calling an async function without `await` doesn't raise — it silently returns a truthy coroutine object, which can invert boolean logic without any visible error. Use `grep -rn "function_name(" --include="*.py" .` from repo root before converting any function's signature.

## Testing

- `pytest.ini` (root) exists only to make a bare `pytest` invocation from repo root safe; `tests/pytest.ini` is the one actually used for `pytest tests/...` invocations (ini discovery searches upward from the given args).
- `tests/conftest.py` has a `pytest_collection_modifyitems` hook enforcing e2e test isolation — don't bypass it.
- `make test-unit` / `make test-integration` / `make test-resilience` / `make test-load` — see `Makefile` for the full target list.
- Integration/DB tests need `TEST_DB_PASSWORD` env var set (no hardcoded password in test config — this was the leaked-password fix).
- Before claiming a fix works, run the specific test file, not just skim the diff.

## Restart / health verification (when explicitly asked to restart)

```
scripts/restart_gate.sh --check-only    # checks 1-7, exits non-zero on BLOCK
scripts/restart_gate.sh --restart       # checks 1-7, then restarts, then checks 8-9
curl -s localhost:8200/health           # sanity check after restart
```

Service is `fazle-core.service` (systemd), working directory
`/home/azim/core`, runs `/home/azim/.venv/bin/python run.py`, logs to
`logs/fazle-core.log` / `logs/fazle-core-error.log`.

## Process notes from past sessions

- When a task gives explicit report-back formatting (e.g. "tell me: which file, which function, before → after"), follow it exactly rather than substituting your own summary style.
- When a task says "ask me before implementing X" or offers a choice, use it — don't guess and proceed silently on genuinely ambiguous scope.
- If a refactor introduces a concurrency bug you can't root-cause quickly on a production-critical path (payments, payroll, escort lifecycle), revert fully rather than shipping a partial fix — this happened once with an escort roster/lifecycle dual-write attempt (reverted after finding a real hang in concurrent release-event handling).
