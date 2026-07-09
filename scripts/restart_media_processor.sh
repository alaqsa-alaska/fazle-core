#!/usr/bin/env bash
set -euo pipefail

old_pids=$(pgrep -f '^/usr/bin/python3 /home/azim/shared/media/media-processor/server.py$' || true)
echo "OLD_PIDS=${old_pids:-none}"
if [[ -n "${old_pids:-}" ]]; then
  kill ${old_pids}
  sleep 1
fi

nohup /usr/bin/python3 /home/azim/shared/media/media-processor/server.py \
  >> /home/azim/whatsapp-mcp/logs/media-processor.log 2>&1 < /dev/null &
new_pid=$!
echo "NEW_PID=$new_pid"
sleep 2
pgrep -af '^/usr/bin/python3 /home/azim/shared/media/media-processor/server.py$' | cat
lsof -iTCP:8090 -sTCP:LISTEN -n -P | cat
curl -m 5 -sS -w '\nHTTP=%{http_code}\n' http://127.0.0.1:8090/health
