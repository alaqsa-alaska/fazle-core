#!/usr/bin/env bash
# Composite security gate (2026-09-29).
#
# This does NOT redefine or widen the meaning of the existing Fazle-Core
# restart gate. scripts/restart_gate.sh keeps its current 9 checks and its
# current meaning. This wrapper simply CONJECTS four independent terms and
# reports the composite:
#
#   FAZLE_CORE_GATE                  (fazle-core/scripts/restart_gate.sh --check-only)
#   FAZLE_MCP_TESTS                  (fazle-mcp pytest suite)
#   HERMES_PLUGIN_INTEGRITY_GATE     (canonical source tracked + deployed == canonical)
#   OUTBOUND_AUTHORIZATION_TESTS     (required outbound-authority invariants)
#
# Fails closed: any term FAIL => COMPOSITE_SECURITY_GATE = FAIL.
#
# Read-only. Performs no restart, no deploy, no push, and sends no message.
#
# Usage:  scripts/composite_security_gate.sh

set -uo pipefail

CORE_REPO="${FAZLE_CORE_REPO:-/home/azim/core}"
MCP_REPO="${FAZLE_MCP_REPO:-/home/azim/fazle-mcp}"

results=()
composite_rc=0

term() {
  local name="$1"; shift
  echo
  echo "── $name ──"
  if "$@"; then
    results+=("PASS  $name")
  else
    results+=("FAIL  $name")
    composite_rc=1
  fi
}

run_fazle_core_gate() {
  bash "$CORE_REPO/scripts/restart_gate.sh" --check-only
}

run_fazle_mcp_tests() {
  ( cd "$MCP_REPO" && ./venv/bin/python -m pytest tests/ -q )
}

run_plugin_integrity() {
  bash "$MCP_REPO/scripts/install_task_action_policy_plugin.sh" --check || return 1
  ( cd "$CORE_REPO" && venv/bin/pytest \
      tests/test_hermes_task_action_policy_plugin_integrity.py -q -p no:randomly )
}

# The characterization suite documents current behavior. The INVARIANT suite
# states required behavior. Both are reported, but only the invariant suite
# decides this term -- and a still-failing invariant is a FAIL, not a pass.
run_outbound_authorization() {
  ( cd "$MCP_REPO" && ./venv/bin/python -m pytest \
      tests/test_outbound_authorization.py -q ) || return 1
  ( cd "$MCP_REPO" && ./venv/bin/python -m pytest \
      tests/test_outbound_invariants.py -q -rxX )
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    echo
    echo "OUTBOUND_AUTHORIZATION: required invariants are NOT satisfied."
    echo "A required invariant is still failing (xfail) or broke (XPASS)."
    return 1
  fi
  # xfails present => at least one required invariant is still violated.
  if "$MCP_REPO/venv/bin/python" -m pytest "$MCP_REPO/tests/test_outbound_invariants.py" \
       -q -p no:randomly 2>/dev/null | grep -qE "xfail"; then
    echo
    echo "OUTBOUND_AUTHORIZATION: required invariants still unmet (xfail present)."
    return 1
  fi
  return 0
}

run_process_coverage() {
  # The Fazle-Core restart gate governs ONE process: fazle-core.service.
  # But the outbound-authority work spans two runtimes, and the second one
  # is not covered by that gate at all. A fazle-core restart alone would load
  # the grant enforcement while leaving the tool-side changes (restored
  # approve_draft / scoped set_mode_state / check_action_capability) running
  # from the previous code in the long-lived Hermes MCP server.
  #
  # This term makes that visible instead of letting it be assumed. It reads
  # only; it restarts nothing.
  local mcp_repo="$MCP_REPO"
  local stale=0

  echo "fazle-core.service      : governed by scripts/restart_gate.sh --check-only"
  echo "hermes fazle-core MCP   : NOT governed by the Fazle-Core restart gate"

  # The MCP server is spawned by hermes, not by fazle-core, so it survives a
  # fazle-core restart. If the tool sources are newer than the running
  # process, the running process is on older code.
  #
  # Matched precisely on the interpreter + script path, and this script's own
  # pid is excluded: a bare `pgrep -f fazle-mcp/server.py` also matches the
  # shell running this check, which would make the comparison meaningless.
  local running
  running="$(
    ps -eo pid=,etimes=,args= \
      | awk -v self="$$" '$1 != self && /python3?/ && /fazle-mcp\/server\.py/ && !/awk/ {print $1; exit}'
  )"
  if [ -z "$running" ]; then
    echo "  no fazle-mcp MCP server process found (nothing stale to flag)"
    return 0
  fi

  # etimes = seconds since the process started, so the file is stale when it
  # is younger than that elapsed time.
  local age
  age="$(ps -o etimes= -p "$running" 2>/dev/null | tr -d ' ')"
  if [ -z "$age" ] || [ "$age" -le 0 ] 2>/dev/null; then
    echo "  could not determine MCP server age; treating as unknown, not stale"
    return 0
  fi
  echo "  running MCP server pid $running, up ${age}s"
  for f in send_whatsapp_tools.py mode_tools.py server.py capability_check_tools.py; do
    local mtime age_of
    mtime="$(stat -c %Y "$mcp_repo/$f" 2>/dev/null || echo 0)"
    age_of=$(( $(date +%s) - mtime ))
    if [ "$mtime" -gt 0 ] && [ "$age_of" -lt "$age" ]; then
      echo "  STALE: $f was edited ${age_of}s ago, server is ${age}s old"
      stale=1
    fi
  done

  if [ "$stale" -eq 1 ]; then
    echo
    echo "PROCESS COVERAGE FAIL: tool-side changes are not live. A fazle-core"
    echo "restart does NOT reload them -- the Hermes MCP server must be"
    echo "restarted separately. See README of this script."
    return 1
  fi
  echo "  OK: running MCP server is not behind its sources"
  return 0
}

term "FAZLE_CORE_GATE" run_fazle_core_gate
term "FAZLE_MCP_TESTS" run_fazle_mcp_tests
term "HERMES_PLUGIN_INTEGRITY_GATE" run_plugin_integrity
term "OUTBOUND_AUTHORIZATION_TESTS" run_outbound_authorization
term "PROCESS_COVERAGE" run_process_coverage

echo
echo "════════ COMPOSITE SECURITY GATE ════════"
for r in "${results[@]}"; do echo "  $r"; done
echo
if [ "$composite_rc" -eq 0 ]; then
  echo "COMPOSITE_SECURITY_GATE = PASS"
else
  echo "COMPOSITE_SECURITY_GATE = FAIL"
fi
echo "NOTE: this gate is advisory. A PASS still does NOT authorize a restart;"
echo "      restart requires explicit Owner approval."
exit "$composite_rc"
