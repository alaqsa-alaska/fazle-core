#!/usr/bin/env bash
# AI Console production monitor — runs every 5 minutes via cron
# Checks:  1) fazle-core /health → ok
#          2) /chat/models → ai_console_enabled=true
#          3) Response latency
#          4) Read-only policy guard (no mutation verbs in chat log)
# Alerts:  WhatsApp via Bridge2 on CRITICAL (once per hour max)
# Log:     /home/azim/core/logs/ai-console-monitor.log

set -uo pipefail

# ── Config ────────────────────────────────────────────────────
CORE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${CORE_DIR}/.env"
LOG_FILE="${CORE_DIR}/logs/ai-console-monitor.log"
ALERT_STAMP="/tmp/ai-console-alert.stamp"
ALERT_COOLDOWN=3600   # seconds between WhatsApp alerts
APP_PORT=8200
BRIDGE_URL="http://localhost:8081"
OWNER_JID="8801880446111@s.whatsapp.net"
WARN_LATENCY_MS=3000
CRIT_LATENCY_MS=8000

# Load INTERNAL_API_KEY from env file
if [[ -f "$ENV_FILE" ]]; then
    INTERNAL_API_KEY=$(grep -E "^INTERNAL_API_KEY=" "$ENV_FILE" | head -1 | cut -d= -f2)
else
    INTERNAL_API_KEY=""
fi

TS=$(date "+%Y-%m-%d %H:%M:%S")
FAIL=0
WARNINGS=()
CRITS=()

log() { echo "[$TS] $*" >> "$LOG_FILE"; }

send_wa_alert() {
    local msg="$1"
    # Rate-limit: skip if sent within cooldown window
    if [[ -f "$ALERT_STAMP" ]]; then
        local last sent_at
        last=$(cat "$ALERT_STAMP" 2>/dev/null || echo 0)
        sent_at=$(date +%s)
        if (( sent_at - last < ALERT_COOLDOWN )); then
            log "[ALERT_SKIP] cooldown active, not resending"
            return
        fi
    fi
    local json_msg
    json_msg=$(python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' <<< "$msg")
    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST "${BRIDGE_URL}/api/send" \
        -H "Content-Type: application/json" \
        -d "{\"recipient\": \"${OWNER_JID}\", \"message\": ${json_msg}}" 2>/dev/null || echo "000")
    date +%s > "$ALERT_STAMP"
    log "[ALERT_SENT] bridge_http=${http_code} | ${msg:0:80}"
}

mkdir -p "$(dirname "$LOG_FILE")"

# ── Check 1: fazle-core /health ───────────────────────────────
START_MS=$(date +%s%3N)
HEALTH_BODY=$(curl -sf --max-time 5 "http://127.0.0.1:${APP_PORT}/health" 2>/dev/null || echo "")
HEALTH_MS=$(( $(date +%s%3N) - START_MS ))

if [[ -z "$HEALTH_BODY" ]]; then
    CRITS+=("fazle-core /health unreachable (${HEALTH_MS}ms)")
    FAIL=1
else
    HEALTH_STATUS=$(echo "$HEALTH_BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','unknown'))" 2>/dev/null || echo "parse_error")
    if [[ "$HEALTH_STATUS" != "ok" && "$HEALTH_STATUS" != "degraded" ]]; then
        CRITS+=("fazle-core health=${HEALTH_STATUS}")
        FAIL=1
    elif [[ "$HEALTH_STATUS" == "degraded" ]]; then
        WARNINGS+=("fazle-core degraded")
    fi
    if (( HEALTH_MS > CRIT_LATENCY_MS )); then
        CRITS+=("health latency ${HEALTH_MS}ms > ${CRIT_LATENCY_MS}ms")
        FAIL=1
    elif (( HEALTH_MS > WARN_LATENCY_MS )); then
        WARNINGS+=("health latency ${HEALTH_MS}ms > ${WARN_LATENCY_MS}ms")
    fi
fi

# ── Check 2: AI Console enabled ───────────────────────────────
if [[ -n "$INTERNAL_API_KEY" ]]; then
    START_MS=$(date +%s%3N)
    MODELS_BODY=$(curl -sf --max-time 5 \
        -H "X-Internal-Key: ${INTERNAL_API_KEY}" \
        "http://127.0.0.1:${APP_PORT}/chat/models" 2>/dev/null || echo "")
    MODELS_MS=$(( $(date +%s%3N) - START_MS ))

    if [[ -z "$MODELS_BODY" ]]; then
        CRITS+=("AI Console /chat/models unreachable (${MODELS_MS}ms)")
        FAIL=1
    else
        AI_ENABLED=$(echo "$MODELS_BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('ai_console_enabled',''))" 2>/dev/null || echo "parse_error")
        if [[ "$AI_ENABLED" != "True" ]]; then
            CRITS+=("AI Console disabled in response: ai_console_enabled=${AI_ENABLED}")
            FAIL=1
        fi
        if (( MODELS_MS > CRIT_LATENCY_MS )); then
            CRITS+=("models latency ${MODELS_MS}ms > ${CRIT_LATENCY_MS}ms")
        elif (( MODELS_MS > WARN_LATENCY_MS )); then
            WARNINGS+=("models latency ${MODELS_MS}ms > ${WARN_LATENCY_MS}ms")
        fi
    fi
else
    WARNINGS+=("INTERNAL_API_KEY not found — skipping AI Console check")
fi

# ── Check 3: Read-only policy guard ──────────────────────────
# Scan last 100 lines of the core log for AI Console mutation attempts
CORE_LOG="${CORE_DIR}/logs/fazle-core.log"
if [[ -f "$CORE_LOG" ]]; then
    MUTATION_HITS=$(tail -100 "$CORE_LOG" 2>/dev/null | \
        grep -iE "\[chat/message\].*\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE)\b" | wc -l)
    if (( MUTATION_HITS > 0 )); then
        CRITS+=("READ-ONLY VIOLATION: ${MUTATION_HITS} mutation pattern(s) detected in AI Console chat log")
        FAIL=1
    fi
fi

# ── Report ────────────────────────────────────────────────────
if [[ $FAIL -eq 0 && ${#WARNINGS[@]} -eq 0 ]]; then
    log "[OK] health=${HEALTH_STATUS} ai_console=enabled health_latency=${HEALTH_MS}ms"
elif [[ $FAIL -eq 0 ]]; then
    log "[WARN] ${WARNINGS[*]}"
else
    CRIT_MSG=$(IFS="; "; echo "${CRITS[*]}")
    log "[CRIT] ${CRIT_MSG}"
    ALERT_TEXT="🚨 AI Console ALERT [$(hostname -s)]
$(date)
${CRIT_MSG}
⚠️ READ-ONLY policy: AI Console শুধু পড়তে পারবে, লিখতে পারবে না।"
    send_wa_alert "$ALERT_TEXT"
fi

exit $FAIL
