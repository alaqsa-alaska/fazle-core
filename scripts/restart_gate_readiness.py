#!/usr/bin/env python3
"""Readiness classification for scripts/restart_gate.sh's Check 9.

Owner directive (2026-08-27, restart-gate false-negative investigation):
the old Check 9 was a single bash loop that treated "curl returned exactly
HTTP 200" as the only success condition, and anything else (including a
fully-started process correctly reporting its OWN documented 503/critical
health state) as "unreachable". Evidence from the 2026-08-27 17:54 Stage-A
restart: the app logged valid "GET /health ... 503" access-log lines for
the ENTIRE 30s the old check polled -- it was reachable and answering the
whole time; only ambient host CPU load (unrelated to that deploy) pushed
the aggregate status to "critical" (app/main.py's health() route: only
status=="critical" maps to HTTP 503; "degraded" and "ok" both return 200).

This module is the pure, testable decision logic (see
tests/unit/test_restart_gate_readiness.py) extracted out of that bash loop
so it can be exercised with fixtures -- no real systemd restart, no
network. scripts/restart_gate.sh calls this file's CLI entry point
(`python3 scripts/restart_gate_readiness.py poll ...`) to do the actual
bounded polling against the live service. Contains no app/ or modules/
code and is not imported by the running service -- test infrastructure
only, per the Owner's explicit "do not modify business routing, Hermes
behavior, Meta logic, recruitment logic, or RAG behavior" instruction.

Readiness signal used: uvicorn's own "Application startup complete." line,
already written to the systemd unit's StandardError log
(logs/fazle-core-error.log per the unit file) by uvicorn itself -- per the
ASGI lifespan spec, uvicorn does not accept HTTP connections until this
fires, so its presence is an unambiguous, already-existing, zero-
production-code-change proof the new process finished booting. No new
endpoint was added to app/main.py for this.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

# "Core" probes reflect THIS process's own dependencies (DB, bridges,
# outbound queue, Ollama, Qdrant) -- a real problem here is this deploy's
# problem. "Ambient" probes (cpu/disk/mem) reflect the shared host's
# overall load, which a restart cannot fix and which the gate should not
# hold a deploy hostage to -- see app/main.py::_probe_cpu/_probe_disk/
# _probe_mem for their own thresholds (unrelated to this script).
CORE_PROBES = [
    "db", "bridge1_db", "bridge2_db", "bridge3_db",
    "bridge_poller_b1", "bridge_poller_b2", "bridge_poller_b3",
    "outbound", "ollama", "qdrant",
]
AMBIENT_PROBES = ["cpu", "disk", "mem"]

STARTUP_COMPLETE_MARKER = "Application startup complete."
TRACEBACK_MARKER = "Traceback (most recent call last):"


# ── Pure classification functions (fixture-testable, no I/O) ───────────────

def classify_process_state(active_state: str, nrestarts_before: int, nrestarts_now: int) -> str | None:
    """Return a fail-reason string if the service process itself is in a bad
    state, or None if it's fine to keep waiting."""
    if active_state == "failed":
        return "systemd service entered 'failed' state"
    if nrestarts_now > nrestarts_before:
        return (
            f"process crash-looped during startup (NRestarts {nrestarts_before} -> "
            f"{nrestarts_now}) — systemd's Restart=always kept relaunching it before "
            f"it ever reported startup complete"
        )
    return None


def scan_error_log_tail(new_tail: str) -> str | None:
    """Scan only the NEW portion of the error log (appended since this
    restart began) for the startup-complete signal or a hard traceback.

    Returns "STARTED" if uvicorn's startup-complete line is present,
    "EXCEPTION:<snippet>" if an unhandled traceback is present, or None if
    neither has appeared yet (keep waiting). A traceback takes priority --
    a process can't validly print startup-complete after crashing."""
    if TRACEBACK_MARKER in new_tail:
        idx = new_tail.index(TRACEBACK_MARKER)
        snippet = "\n".join(new_tail[idx:].splitlines()[:15])
        return f"EXCEPTION:{snippet}"
    if STARTUP_COMPLETE_MARKER in new_tail:
        return "STARTED"
    return None


def classify_health(status_code: int | None, body: dict | None) -> dict:
    """Classify one /health response, already fetched by the caller.

    status_code is None for a connection failure (refused/timeout).
    body is the parsed JSON dict, or None if the response wasn't valid JSON.
    """
    if status_code is None:
        return {"state": "unreachable"}
    if body is None:
        return {"state": "malformed"}
    probes = body.get("probes", {})
    bad_core = {
        n: probes[n] for n in CORE_PROBES
        if n in probes and probes[n].get("status") != "ok"
    }
    ambient_flagged = {
        n: probes[n].get("status") for n in AMBIENT_PROBES
        if n in probes and probes[n].get("status") != "ok"
    }
    if bad_core:
        return {
            "state": "core_bad", "bad_core": bad_core,
            "ambient": ambient_flagged, "overall": body.get("status"),
        }
    return {"state": "ready", "ambient": ambient_flagged, "overall": body.get("status")}


# ── I/O helpers (thin, not exercised by the pure-function unit tests) ──────

def _systemctl_show(service: str, prop: str) -> str:
    try:
        out = subprocess.run(
            ["systemctl", "show", service, "-p", prop, "--value"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip()
    except Exception:
        return ""


def _read_new_tail(path: str, offset: int) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            return f.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def _fetch_health(url: str, timeout: float = 5.0) -> tuple[int | None, dict | None]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            code = resp.status
            raw = resp.read()
    except urllib.error.HTTPError as e:
        code = e.code
        raw = e.read()
    except Exception:
        return None, None
    try:
        return code, json.loads(raw)
    except Exception:
        return code, None


# ── CLI: the actual bounded poll against a live service ────────────────────

def poll(
    service: str, health_url: str, error_log: str,
    pre_nrestarts: int, pre_errlog_bytes: int,
    max_startup_wait_s: int, max_core_settle_s: int,
) -> int:
    t0 = time.time()
    fail_reason = None
    t_started = None
    phase = "startup"

    while True:
        elapsed = int(time.time() - t0)
        active_state = _systemctl_show(service, "ActiveState")
        nrestarts_now = int(_systemctl_show(service, "NRestarts") or pre_nrestarts)
        reason = classify_process_state(active_state, pre_nrestarts, nrestarts_now)
        if reason:
            fail_reason = reason
            break

        new_tail = _read_new_tail(error_log, pre_errlog_bytes)
        signal = scan_error_log_tail(new_tail)
        if signal == "STARTED":
            t_started = elapsed
            phase = "reachability"
            break
        if signal and signal.startswith("EXCEPTION:"):
            fail_reason = f"startup exception detected in {error_log}:\n{signal[len('EXCEPTION:'):]}"
            break

        if elapsed >= max_startup_wait_s:
            fail_reason = (
                f"no '{STARTUP_COMPLETE_MARKER}' signal in {error_log} within "
                f"{max_startup_wait_s}s — process may be hung, still importing, "
                f"or logging elsewhere; check {error_log} directly"
            )
            break
        time.sleep(2)

    overall_status = "?"
    ambient_note = ""

    if not fail_reason:
        code, body = _fetch_health(health_url)
        result = classify_health(code, body)
        if result["state"] == "unreachable":
            fail_reason = (
                f"HTTP endpoint unreachable at {health_url} despite "
                f"'{STARTUP_COMPLETE_MARKER}' at {t_started}s — possible port/network anomaly"
            )
        elif result["state"] == "malformed":
            fail_reason = f"malformed /health response {t_started}s after startup-complete"
        elif result["state"] == "core_bad":
            settle_start = time.time()
            while True:
                time.sleep(3)
                settle_elapsed = time.time() - settle_start
                code, body = _fetch_health(health_url)
                result = classify_health(code, body)
                if result["state"] == "ready":
                    break
                if settle_elapsed >= max_core_settle_s:
                    fail_reason = (
                        f"core application probe(s) still unhealthy after "
                        f"{max_core_settle_s}s settle window: {result.get('bad_core')}"
                    )
                    break
            if result["state"] == "ready":
                overall_status = result.get("overall", "?")
                ambient_note = _format_ambient(result.get("ambient", {}))
        else:  # ready
            overall_status = result.get("overall", "?")
            ambient_note = _format_ambient(result.get("ambient", {}))

    if not fail_reason:
        print(f"  startup-complete signal: {t_started}s")
        print(f"  first healthy /health:   {int(time.time() - t0)}s (overall status: {overall_status})")
        if ambient_note:
            print(f"  {ambient_note}")
        print("RESTART: SUCCESS")
        return 0
    else:
        print(f"  FAILED at phase={phase} elapsed={int(time.time() - t0)}s", file=sys.stderr)
        print(f"  reason: {fail_reason}", file=sys.stderr)
        print("RESTART: FAILED — service did not become healthy. See RECOVERY.md for rollback procedure.", file=sys.stderr)
        return 1


def _format_ambient(ambient: dict) -> str:
    if not ambient:
        return ""
    flagged = ", ".join(f"{k}={v}" for k, v in ambient.items())
    return f"ambient (non-blocking, host-level): {flagged}"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    poll_p = sub.add_parser("poll")
    poll_p.add_argument("--service", required=True)
    poll_p.add_argument("--health-url", required=True)
    poll_p.add_argument("--error-log", required=True)
    poll_p.add_argument("--pre-nrestarts", type=int, required=True)
    poll_p.add_argument("--pre-errlog-bytes", type=int, required=True)
    poll_p.add_argument("--max-startup-wait-s", type=int, default=60)
    poll_p.add_argument("--max-core-settle-s", type=int, default=30)
    args = p.parse_args()

    if args.cmd == "poll":
        return poll(
            args.service, args.health_url, args.error_log,
            args.pre_nrestarts, args.pre_errlog_bytes,
            args.max_startup_wait_s, args.max_core_settle_s,
        )
    return 2


if __name__ == "__main__":
    sys.exit(main())
