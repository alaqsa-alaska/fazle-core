"""scripts/restart_gate.sh Check 9 — readiness classification regression
tests (Owner directive, 2026-08-27, restart-gate false-negative
investigation).

Pure/unit: exercise scripts/restart_gate_readiness.py's classification
functions directly with fixture inputs.

End-to-end (mocked I/O): exercise poll() itself with subprocess/urlopen/
file-read patched out, covering the 6 scenarios the Owner asked for. No
real systemd, no real network, no real restart -- these tests run against
the SAME already-running service state only via mocks, never touching it.

test infrastructure only -- scripts/restart_gate_readiness.py contains no
app/ or modules/ code and is not imported by the running service.
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from scripts import restart_gate_readiness as rgr

pytestmark = pytest.mark.unit


# ── Pure classification functions ───────────────────────────────────────────

class TestClassifyProcessState:
    def test_active_ok(self):
        assert rgr.classify_process_state("active", 0, 0) is None

    def test_failed_state(self):
        reason = rgr.classify_process_state("failed", 0, 0)
        assert reason is not None
        assert "failed" in reason

    def test_crash_loop_detected(self):
        reason = rgr.classify_process_state("activating", 0, 2)
        assert reason is not None
        assert "crash-looped" in reason
        assert "0 -> 2" in reason

    def test_activating_without_restart_increase_is_fine(self):
        assert rgr.classify_process_state("activating", 3, 3) is None


class TestScanErrorLogTail:
    def test_empty_tail_no_signal(self):
        assert rgr.scan_error_log_tail("") is None

    def test_unrelated_lines_no_signal(self):
        assert rgr.scan_error_log_tail("INFO:     Started server process\n") is None

    def test_startup_complete_detected(self):
        tail = "INFO:     Started server process [123]\nINFO:     Application startup complete.\n"
        assert rgr.scan_error_log_tail(tail) == "STARTED"

    def test_traceback_detected(self):
        tail = (
            "Traceback (most recent call last):\n"
            '  File "run.py", line 10, in <module>\n'
            "    s = get_settings()\n"
            "pydantic_core._pydantic_core.ValidationError: 1 validation error\n"
        )
        result = rgr.scan_error_log_tail(tail)
        assert result is not None
        assert result.startswith("EXCEPTION:")
        assert "ValidationError" in result

    def test_traceback_takes_priority_over_startup_complete(self):
        """A process can't validly log both in the same window -- but if it
        somehow does (e.g. crashed right after logging startup-complete on
        a later worker), the exception must win: never report success on a
        log tail that also contains an unhandled traceback."""
        tail = (
            "INFO:     Application startup complete.\n"
            "Traceback (most recent call last):\n"
            "    raise RuntimeError('boom')\n"
        )
        result = rgr.scan_error_log_tail(tail)
        assert result.startswith("EXCEPTION:")


class TestClassifyHealth:
    def _body(self, overall, **probe_overrides):
        probes = {n: {"status": "ok"} for n in rgr.CORE_PROBES + rgr.AMBIENT_PROBES}
        for k, v in probe_overrides.items():
            probes[k] = v
        return {"status": overall, "probes": probes}

    def test_unreachable_when_no_status_code(self):
        assert rgr.classify_health(None, None)["state"] == "unreachable"

    def test_malformed_when_body_missing(self):
        assert rgr.classify_health(200, None)["state"] == "malformed"

    def test_all_ok_is_ready(self):
        body = self._body("ok")
        result = rgr.classify_health(200, body)
        assert result["state"] == "ready"
        assert result["ambient"] == {}

    def test_only_ambient_degraded_is_still_ready(self):
        """The core bug fix: a valid 503 whose ONLY bad probes are cpu/disk/
        mem (host-level, unrelated to this deploy) must be treated as ready,
        not as a failure -- this is exactly the Stage-A false negative."""
        body = self._body("critical", cpu={"status": "critical", "load1": 13.18})
        result = rgr.classify_health(503, body)
        assert result["state"] == "ready"
        assert result["ambient"] == {"cpu": "critical"}

    def test_core_probe_bad_is_core_bad(self):
        body = self._body("critical", db={"status": "critical", "error": "connection refused"})
        result = rgr.classify_health(503, body)
        assert result["state"] == "core_bad"
        assert "db" in result["bad_core"]

    def test_core_and_ambient_both_bad_reports_both_but_state_is_core_bad(self):
        body = self._body(
            "critical",
            db={"status": "degraded"},
            cpu={"status": "critical", "load1": 20.0},
        )
        result = rgr.classify_health(503, body)
        assert result["state"] == "core_bad"
        assert "db" in result["bad_core"]
        assert result["ambient"] == {"cpu": "critical"}


# ── poll() end-to-end, all I/O mocked ───────────────────────────────────────

def _fake_clock(start: float = 1000.0, step: float = 2.0):
    """time.time() side_effect that advances by `step` on every call --
    lets tests simulate elapsed time passing without any real sleep."""
    state = {"t": start}

    def _next(*_a, **_kw):
        state["t"] += step
        return state["t"]
    return _next


class TestPollEndToEnd:
    """Owner's 6 required regression scenarios."""

    def test_1_connection_refused_fails(self, capsys):
        with patch.object(rgr, "_systemctl_show", side_effect=lambda s, p: "active" if p == "ActiveState" else "0"), \
             patch.object(rgr, "_read_new_tail", return_value="INFO: Application startup complete.\n"), \
             patch.object(rgr, "_fetch_health", return_value=(None, None)), \
             patch.object(rgr.time, "sleep", return_value=None), \
             patch.object(rgr.time, "time", side_effect=_fake_clock()):
            code = rgr.poll("fazle-core.service", "http://x/health", "err.log", 0, 0, 60, 6)
        assert code == 1
        assert "unreachable" in capsys.readouterr().err

    def test_2_process_dies_during_startup_fails(self, capsys):
        with patch.object(rgr, "_systemctl_show", side_effect=lambda s, p: "activating" if p == "ActiveState" else "1"), \
             patch.object(rgr, "_read_new_tail", return_value=""), \
             patch.object(rgr.time, "sleep", return_value=None), \
             patch.object(rgr.time, "time", side_effect=_fake_clock()):
            code = rgr.poll("fazle-core.service", "http://x/health", "err.log", 0, 0, 60, 6)
        assert code == 1
        err = capsys.readouterr().err
        assert "crash-looped" in err

    def test_3_temporary_warmup_then_ready_passes(self, capsys):
        """Valid 503 responses (critical, ambient-only) followed by ready --
        the exact Stage-A shape."""
        call_count = {"n": 0}

        def fake_fetch(url, timeout=5.0):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return 503, {"status": "critical", "probes": {
                    **{n: {"status": "ok"} for n in rgr.CORE_PROBES},
                    "cpu": {"status": "critical", "load1": 15.0},
                    "disk": {"status": "ok"}, "mem": {"status": "ok"},
                }}
            return 200, {"status": "degraded", "probes": {
                **{n: {"status": "ok"} for n in rgr.CORE_PROBES},
                "cpu": {"status": "degraded", "load1": 7.0},
                "disk": {"status": "ok"}, "mem": {"status": "ok"},
            }}

        with patch.object(rgr, "_systemctl_show", side_effect=lambda s, p: "active" if p == "ActiveState" else "0"), \
             patch.object(rgr, "_read_new_tail", return_value="INFO: Application startup complete.\n"), \
             patch.object(rgr, "_fetch_health", side_effect=fake_fetch), \
             patch.object(rgr.time, "sleep", return_value=None), \
             patch.object(rgr.time, "time", side_effect=_fake_clock()):
            code = rgr.poll("fazle-core.service", "http://x/health", "err.log", 0, 0, 60, 6)
        assert code == 0
        out = capsys.readouterr().out
        assert "RESTART: SUCCESS" in out
        # ambient-only critical on the first read must not block success,
        # and must still be surfaced (not silently dropped).
        assert "ambient" in out

    def test_4_continuous_warmup_beyond_max_window_fails(self, capsys):
        """The signal never appears -- must fail, not hang or silently pass."""
        with patch.object(rgr, "_systemctl_show", side_effect=lambda s, p: "active" if p == "ActiveState" else "0"), \
             patch.object(rgr, "_read_new_tail", return_value=""), \
             patch.object(rgr.time, "sleep", return_value=None), \
             patch.object(rgr.time, "time", side_effect=_fake_clock(step=2.0)):
            code = rgr.poll("fazle-core.service", "http://x/health", "err.log", 0, 0, 6, 6)
        assert code == 1
        err = capsys.readouterr().err
        assert "no 'Application startup complete.' signal" in err

    def test_5_degraded_ambient_only_on_already_started_service_passes(self, capsys):
        """A service that already finished startup and is reporting a valid
        ambient-only degraded/critical state must be classified ready per
        documented health semantics, not treated as still-warming."""
        with patch.object(rgr, "_systemctl_show", side_effect=lambda s, p: "active" if p == "ActiveState" else "0"), \
             patch.object(rgr, "_read_new_tail", return_value="INFO: Application startup complete.\n"), \
             patch.object(rgr, "_fetch_health", return_value=(503, {
                 "status": "critical",
                 "probes": {
                     **{n: {"status": "ok"} for n in rgr.CORE_PROBES},
                     "disk": {"status": "critical", "used_pct": 95},
                     "cpu": {"status": "ok"}, "mem": {"status": "ok"},
                 },
             })), \
             patch.object(rgr.time, "sleep", return_value=None), \
             patch.object(rgr.time, "time", side_effect=_fake_clock()):
            code = rgr.poll("fazle-core.service", "http://x/health", "err.log", 0, 0, 60, 6)
        assert code == 0
        assert "RESTART: SUCCESS" in capsys.readouterr().out

    def test_6_normal_immediate_200_startup_passes(self, capsys):
        with patch.object(rgr, "_systemctl_show", side_effect=lambda s, p: "active" if p == "ActiveState" else "0"), \
             patch.object(rgr, "_read_new_tail", return_value="INFO: Application startup complete.\n"), \
             patch.object(rgr, "_fetch_health", return_value=(200, {
                 "status": "ok",
                 "probes": {n: {"status": "ok"} for n in rgr.CORE_PROBES + rgr.AMBIENT_PROBES},
             })), \
             patch.object(rgr.time, "sleep", return_value=None), \
             patch.object(rgr.time, "time", side_effect=_fake_clock()):
            code = rgr.poll("fazle-core.service", "http://x/health", "err.log", 0, 0, 60, 6)
        assert code == 0
        out = capsys.readouterr().out
        assert "RESTART: SUCCESS" in out
        assert "ambient" not in out  # nothing flagged -> no ambient note printed

    def test_core_probe_bad_that_never_clears_fails_with_named_probe(self, capsys):
        """A REAL app-level regression (e.g. db actually broken) must still
        fail the gate -- the fix must not become 'always pass after
        startup-complete'."""
        with patch.object(rgr, "_systemctl_show", side_effect=lambda s, p: "active" if p == "ActiveState" else "0"), \
             patch.object(rgr, "_read_new_tail", return_value="INFO: Application startup complete.\n"), \
             patch.object(rgr, "_fetch_health", return_value=(503, {
                 "status": "critical",
                 "probes": {
                     **{n: {"status": "ok"} for n in rgr.CORE_PROBES if n != "db"},
                     "db": {"status": "critical", "error": "connection refused"},
                     "cpu": {"status": "ok"}, "disk": {"status": "ok"}, "mem": {"status": "ok"},
                 },
             })), \
             patch.object(rgr.time, "sleep", return_value=None), \
             patch.object(rgr.time, "time", side_effect=_fake_clock()):
            code = rgr.poll("fazle-core.service", "http://x/health", "err.log", 0, 0, 60, 6)
        assert code == 1
        err = capsys.readouterr().err
        assert "core application probe" in err
        assert "db" in err

    def test_core_probe_bad_that_clears_within_settle_window_passes(self, capsys):
        """A transient core-probe blip (e.g. DB pool still initializing)
        that clears within the bounded settle window must pass -- proves
        the fix doesn't over-correct into failing on every transient blip."""
        call_count = {"n": 0}

        def fake_fetch(url, timeout=5.0):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return 503, {
                    "status": "critical",
                    "probes": {
                        **{n: {"status": "ok"} for n in rgr.CORE_PROBES if n != "db"},
                        "db": {"status": "critical", "error": "pool not ready"},
                        "cpu": {"status": "ok"}, "disk": {"status": "ok"}, "mem": {"status": "ok"},
                    },
                }
            return 200, {
                "status": "ok",
                "probes": {n: {"status": "ok"} for n in rgr.CORE_PROBES + rgr.AMBIENT_PROBES},
            }

        with patch.object(rgr, "_systemctl_show", side_effect=lambda s, p: "active" if p == "ActiveState" else "0"), \
             patch.object(rgr, "_read_new_tail", return_value="INFO: Application startup complete.\n"), \
             patch.object(rgr, "_fetch_health", side_effect=fake_fetch), \
             patch.object(rgr.time, "sleep", return_value=None), \
             patch.object(rgr.time, "time", side_effect=_fake_clock()):
            code = rgr.poll("fazle-core.service", "http://x/health", "err.log", 0, 0, 60, 6)
        assert code == 0
        assert "RESTART: SUCCESS" in capsys.readouterr().out

    def test_traceback_during_startup_fails_immediately(self, capsys):
        with patch.object(rgr, "_systemctl_show", side_effect=lambda s, p: "active" if p == "ActiveState" else "0"), \
             patch.object(rgr, "_read_new_tail", return_value=(
                 "Traceback (most recent call last):\n"
                 "pydantic_core._pydantic_core.ValidationError: 1 validation error\n"
             )), \
             patch.object(rgr.time, "sleep", return_value=None), \
             patch.object(rgr.time, "time", side_effect=_fake_clock()):
            code = rgr.poll("fazle-core.service", "http://x/health", "err.log", 0, 0, 60, 6)
        assert code == 1
        err = capsys.readouterr().err
        assert "startup exception detected" in err
        assert "ValidationError" in err

    def test_systemd_failed_state_fails_immediately(self, capsys):
        with patch.object(rgr, "_systemctl_show", side_effect=lambda s, p: "failed" if p == "ActiveState" else "0"), \
             patch.object(rgr, "_read_new_tail", return_value=""), \
             patch.object(rgr.time, "sleep", return_value=None), \
             patch.object(rgr.time, "time", side_effect=_fake_clock()):
            code = rgr.poll("fazle-core.service", "http://x/health", "err.log", 0, 0, 60, 6)
        assert code == 1
        assert "'failed' state" in capsys.readouterr().err
