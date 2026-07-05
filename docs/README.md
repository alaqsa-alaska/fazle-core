# Fazle Core Documentation

Welcome to the Fazle Core docs. This is the engine that turns WhatsApp
messages into structured HR / Payroll / Escort operations for Al-Aqsa
Security.

## Table of Contents

| Doc | What's inside |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System diagram, components, data flow, module map |
| [API.md](API.md) | Every HTTP endpoint with auth + example |
| [OPERATIONS.md](OPERATIONS.md) | Deploy, restart, backup, monitor, troubleshoot |
| [ROADMAP.md](ROADMAP.md) | Batch-by-batch ledger (B11 → B24) |

## Quick links

- Health: `curl http://127.0.0.1:8200/health`
- Dashboard: <http://127.0.0.1:8200/dashboard>
- Metrics: `curl http://127.0.0.1:8200/metrics`
- Live logs: `tail -f /home/azim/fazle-core/logs/fazle-core.log`
- Service: `sudo systemctl status fazle-core.service`

## Related repos / services

- `whatsapp-mcp/` — Bridge1 (HR, port 8080)
- `whatsapp2/` — Bridge2 (OPS, port 8081)
- `whatsapp-erp/` — read-only ERP query layer (separate service)

## 2026-07-05 Operations Note

- Escort confirmed exact-duplicate prevention is now deterministic and
	enforceable via API audit/cleanup plus DB schema unique index.
- Use `/api/escort-roster/audit-exact-duplicates` and
	`/api/escort-roster/cleanup-exact-duplicates` for operational verification.

See per-batch implementation notes under `/memories/repo/batch*.md`.
