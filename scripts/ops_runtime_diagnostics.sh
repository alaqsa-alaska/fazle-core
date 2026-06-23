#!/usr/bin/env bash
# Runtime diagnostics for Phase-1 operations.
# Checks disk, memory, Ollama model state, and RAG/Qdrant reachability.
set -euo pipefail

CORE_ENV="/home/azim/core/.env"
CORE_HEALTH_URL="http://127.0.0.1:8200/health"
OLLAMA_URL="http://127.0.0.1:11434"
QDRANT_URL="http://127.0.0.1:6333"

extract_env() {
  local key="$1"
  if [[ -f "$CORE_ENV" ]]; then
    grep -E "^${key}=" "$CORE_ENV" | tail -n1 | cut -d'=' -f2-
  fi
}

INTERNAL_KEY="$(extract_env INTERNAL_API_KEY || true)"
if [[ -z "$INTERNAL_KEY" ]]; then
  INTERNAL_KEY="$(extract_env FAZLE_INTERNAL_KEY || true)"
fi

printf "\n== Host Disk ==\n"
df -h /

printf "\n== Host Memory ==\n"
free -h

printf "\n== Fazle Core Health ==\n"
curl -sS --max-time 6 "$CORE_HEALTH_URL" || true
printf "\n"

printf "\n== Ollama Models ==\n"
curl -sS --max-time 8 "$OLLAMA_URL/api/tags" || true
printf "\n"

printf "\n== Qdrant Health (host-mapped) ==\n"
if curl -sS --max-time 4 "$QDRANT_URL/health" >/dev/null 2>&1; then
  curl -sS "$QDRANT_URL/health"
  printf "\n"
else
  echo "Qdrant not reachable via $QDRANT_URL (may be docker-internal only)."
fi

if [[ -n "$INTERNAL_KEY" ]]; then
  printf "\n== RAG Stats ==\n"
  curl -sS --max-time 8 -H "X-Internal-Key: $INTERNAL_KEY" "http://127.0.0.1:8200/api/rag/stats" || true
  printf "\n"

  printf "\n== RAG Rebuild Trigger ==\n"
  curl -sS --max-time 20 -X POST -H "X-Internal-Key: $INTERNAL_KEY" "http://127.0.0.1:8200/api/rag/rebuild" || true
  printf "\n"
else
  echo "Skipping RAG endpoints: INTERNAL_API_KEY/FAZLE_INTERNAL_KEY not found in $CORE_ENV"
fi

printf "\nDone.\n"
