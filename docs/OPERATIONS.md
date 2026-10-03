# Operations Runbook

## Service control

```bash
# Status / restart / stop / logs
sudo systemctl status  fazle-core.service
sudo systemctl restart fazle-core.service
sudo systemctl stop    fazle-core.service
sudo journalctl -u fazle-core -n 200 -f

# App log (structured)
tail -f /home/azim/fazle-core/logs/fazle-core.log
```

Service runs as user `azim`, venv at `/home/azim/fazle-core/venv`,
binds `127.0.0.1:8200`. Public traffic terminates at nginx.

## Environment

`.env` lives at `/home/azim/fazle-core/.env` (NEVER committed).
Critical keys:

| Var | Meaning |
|---|---|
| `DATABASE_URL` | `postgresql://user:pass@host:5432/fazle` |
| `INTERNAL_API_KEY` | Legacy single key (kept alongside per-admin keys) |
| `AUTO_REPLY_ENABLED` | `false` = SAFE MODE (drafts only, no send) |
| `OLLAMA_HOST` | Default `http://127.0.0.1:11434` |
| `ADMIN_NUMBERS` | Comma list; bootstrapped to superadmin on boot |
| `OCR_CONCURRENCY` | OCR semaphore (default 2) |
| `PAYROLL_BULK_CONCURRENCY` | Payroll semaphore (default 1) |
| `USE_OUTBOUND_QUEUE` | `true` recommended (B15.9) |
| `BRIDGE_MEDIA_ACQUISITION_ENABLED` | `false` (default) = inbound bridge media stays `media_unprocessed` (no OCR/STT). `true` = the poller fetches the file from the bridge's `/api/download` and runs the existing image/audio/document extraction. Global (all 3 bridges). See `docs/BRIDGE_MEDIA_ACQUISITION_INTEGRATION_PLAN_2026-08-29.md`. |
| `BRIDGE_MEDIA_DOWNLOAD_TIMEOUT_S` | Per-file `/api/download` timeout, seconds (default `45`). Only used when `BRIDGE_MEDIA_ACQUISITION_ENABLED=true`. |

Backups of `.env` live in `/home/azim/secure-env-backup/`.

## Deploy

```bash
cd /home/azim/fazle-core
git checkout develop && git pull
# Run any new migrations
ls db/migrations/*.sql                # apply manually if new file appears
# Local CI gate
bash scripts/run_ci.sh
# Promote
git checkout main && git merge develop && git push
sudo systemctl restart fazle-core.service
sleep 4 && curl -s http://127.0.0.1:8200/health | jq
```

**⚠️ 2026-07-09 note:** this checkout (`/home/azim/core`) runs directly off the working tree with no build/artifact step — the `git checkout`/`git pull` steps above don't reflect how this specific instance is actually deployed. See `docs/DEPLOYMENT_RESTART_GATE.md` for the designed (not yet implemented) pre-restart gate — working-tree cleanliness, secret scan, xfail count, targeted tests, migration inventory, `.env` critical-flag acknowledgment, and backup freshness — that should run before any restart of this instance.

## Database

- Postgres 15 via Docker Compose (`ai-call-platform/ai-infra/docker-compose.yaml`).
- Manual migration: `psql "$DATABASE_URL" -f db/migrations/00X_*.sql`.
- Backup ad-hoc: `curl -X POST -H "X-Internal-Key: $KEY" http://127.0.0.1:8200/backup/run`.
- Snapshots land in `/home/azim/backups/` (rotated by B18 scheduler job).

## Monitoring

### Prometheus scrape (loopback)

`/metrics` is intentionally unauthenticated — bind is `127.0.0.1` so only
local processes can scrape. Sample scrape config:

```yaml
scrape_configs:
  - job_name: fazle-core
    static_configs:
      - targets: ["127.0.0.1:8200"]
```

### Quick dashboard pulse

```bash
KEY=$(grep ^INTERNAL_API_KEY= /home/azim/fazle-core/.env | cut -d= -f2-)
curl -s -H "X-Internal-Key: $KEY" http://127.0.0.1:8200/observability/summary | jq
curl -s -H "X-Internal-Key: $KEY" http://127.0.0.1:8200/observability/errors?limit=20 | jq
```

### Metrics worth watching

| Metric | Concern |
|---|---|
| `fazle_uptime_seconds` | Recent restart? |
| `fazle_http_requests_total{status="5xx"}` | Server errors |
| `fazle_http_request_duration_ms` p95 | Latency drift |
| `fazle_outbound_queue_depth` | Bridge backpressure |
| `fazle_scheduler_job_failures_total` | Cron health |
| `errors_24h` (from `/observability/summary`) | Error log spikes |

## Common tasks

### Mint a per-admin API key

```bash
curl -s -X POST -H "X-Internal-Key: $KEY" \
  http://127.0.0.1:8200/admin/users/8801999000111/apikey | jq
# response → { "api_key": "fk_…" } — give to that admin only
```

### Toggle SAFE MODE

Edit `.env`, set `AUTO_REPLY_ENABLED=true|false`, restart service. Live
flag visible at `GET /admin/safe-mode`.

### Reindex RAG after KB edits

```bash
curl -s -X POST -H "X-Internal-Key: $KEY" http://127.0.0.1:8200/rag/reindex | jq
curl -s -H "X-Internal-Key: $KEY" http://127.0.0.1:8200/rag/stats | jq
```

### Trigger backup / scheduler job manually

```bash
curl -s -X POST -H "X-Internal-Key: $KEY" http://127.0.0.1:8200/backup/run | jq
curl -s -X POST -H "X-Internal-Key: $KEY" http://127.0.0.1:8200/scheduler/run/outbound_drain | jq
```

## Troubleshooting

### Service won't start
1. `sudo journalctl -u fazle-core -n 80` — usually a missing env var or DB down.
2. `pg_isready -h 127.0.0.1 -p 5432` — confirm DB.
3. `/home/azim/fazle-core/venv/bin/python -c "from app.main import app"` — import smoke.

### `/health` 503
- DB ping failed. Check `DATABASE_URL` and Postgres container.

### Drafts not delivering
- Check `AUTO_REPLY_ENABLED` — `false` blocks sends except admin approvals.
- `GET /scheduler/status` — confirm `outbound_drain` is running.
- Check `/observability/errors` for bridge timeouts.

### Bridges silent
- Bridge1 (HR): `curl http://127.0.0.1:8080/health`
- Bridge2 (OPS): `curl http://127.0.0.1:8081/health`
- Both must be QR-authed; check WhatsApp Linked Devices.

### Rolling back
```bash
git -C /home/azim/fazle-core log --oneline -5
git -C /home/azim/fazle-core checkout <good-sha>
sudo systemctl restart fazle-core.service
```

## Tests

- Offline (no services): `venv/bin/python scripts/test_batch22_observability.py`
- Live (needs running service + DB): `bash scripts/run_ci.sh`
- Per-batch suites: `scripts/test_batch{11..22}*.py`
