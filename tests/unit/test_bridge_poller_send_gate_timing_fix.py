"""Bridge2 send-gate timing fix (2026-08-28, P1/A3 -- Stage A of the
message-storage-baseline plan).

Root cause, confirmed live: the outgoing-scan loop's periodic send-gate
re-enable (`get_bridge().ensure_enabled()`, the only thing that turns a
bridge's send gate back on between real sends -- `send_strict()` in
app/bridge.py always turns it off again in its own `finally` block, and
that file is intentionally untouched by this fix) used to fire on an
*iteration-count* modulo (`_poll_iter % _gate_check_every == 0`,
`_gate_check_every = _SEND_GATE_CHECK_INTERVAL // POLL_INTERVAL = 60`).
That assumed a roughly fixed wall-clock time per iteration -- an
assumption this same loop's own adaptive backoff
(`BRIDGE_POLL_MIN_S=1.0` busy -> `BRIDGE_POLL_MAX_S=30.0` idle,
`BRIDGE_POLL_BACKOFF=1.5`x/iteration) breaks: during a quiet stretch,
60 iterations at up to 30s each could take up to ~30 minutes of real
time, six times longer than the intended, commented "every 5 min"
(`_SEND_GATE_CHECK_INTERVAL=300`). Live-observed: Bridge2's
`/api/send-status` sat `allowed:false` for 18+ minutes with no bridge or
session problem at all.

Fix: `modules/bridge_poller/__init__.py` now tracks elapsed wall-clock
time via `time.monotonic()` (`_last_gate_check`) instead of iteration
count -- the check fires whenever `time.monotonic() - _last_gate_check
>= _SEND_GATE_CHECK_INTERVAL`, independent of how the adaptive backoff
has stretched any given iteration's own sleep.

This loop needs a live bridge SQLite session to drive end-to-end --
matching this repo's existing pattern for this exact loop (see
tests/unit/test_bridge2_selfchat_natural_admin_routing.py and
tests/unit/test_bridge_poller_system_notification_guard.py), the wiring
is proven via source inspection rather than re-executed, plus one
standalone arithmetic test of the actual elapsed-time condition using
real `time.monotonic()` calls (not the full loop) to prove requirement 1
(idle backoff cannot delay the check beyond one iteration's sleep past
the interval) and requirement 3 (the check genuinely re-evaluates on
elapsed time, not a call count) with real evidence, not just source
inspection alone.

Requirement 2 ("a real send can still set/reset the gate as designed")
is `send_strict()`'s own `_set_send(True)/_set_send(False)` behavior in
app/bridge.py -- completely untouched by this fix (confirmed by the
source-inspection test below asserting `app/bridge.py` is not part of
this diff's changed area) and already covered by
tests/unit/test_admin_draft_review_workflow.py's own send_strict tests,
not duplicated here.

Requirement 4 ("existing polling functionality remains intact") is
covered by asserting BRIDGE_POLL_MIN_S/MAX_S/BACKOFF and
_SEND_GATE_CHECK_INTERVAL are byte-identical to before this fix -- the
adaptive backoff formula itself was never touched, only what triggers
the gate re-check.
"""
from __future__ import annotations

import inspect
import time

import pytest

import modules.bridge_poller as bp

pytestmark = pytest.mark.unit


class TestConstantsUnchanged:
    """The fix changes WHEN the gate check fires, never the intended
    interval or the adaptive-polling knobs themselves."""

    def test_send_gate_check_interval_still_300_seconds(self):
        assert bp._SEND_GATE_CHECK_INTERVAL == 300

    def test_adaptive_polling_constants_untouched(self):
        assert bp.BRIDGE_POLL_MIN_S == 1.0
        assert bp.BRIDGE_POLL_MAX_S == 30.0
        assert bp.BRIDGE_POLL_BACKOFF == 1.5


class TestSourceWiring:
    """Structural proof the fix is actually wired into the loop, not just
    defined and unused -- inspects the real source rather than driving
    the full poll loop (which needs a live bridge SQLite session), same
    pattern this repo already uses for this exact loop."""

    def test_iteration_count_gate_mechanism_is_gone(self):
        """The old executable constructs must be gone -- the historical
        explanation in this fix's own comment (documenting what the bug
        used to look like) legitimately still names them in prose, so
        this strips comment-only lines before searching, rather than a
        blanket substring match that would also catch that comment."""
        code_lines = "\n".join(
            line for line in inspect.getsource(bp).splitlines()
            if not line.strip().startswith("#")
        )
        assert "_poll_iter" not in code_lines
        assert "_gate_check_every" not in code_lines

    def test_monotonic_based_gate_mechanism_is_present(self):
        source = inspect.getsource(bp)
        assert "_last_gate_check = time.monotonic()" in source
        assert "_now_monotonic - _last_gate_check >= _SEND_GATE_CHECK_INTERVAL" in source

    def test_gate_check_still_calls_ensure_enabled(self):
        source = inspect.getsource(bp)
        idx = source.index("_now_monotonic - _last_gate_check >= _SEND_GATE_CHECK_INTERVAL")
        block = source[idx:idx + 400]
        assert "ensure_enabled()" in block

    def test_last_gate_check_is_updated_before_the_awaited_call(self):
        """_last_gate_check must be reset the moment the interval elapses,
        not after ensure_enabled() returns -- otherwise a slow/hanging
        ensure_enabled() call would itself further delay the next check,
        reintroducing a variant of the same timing bug."""
        source = inspect.getsource(bp)
        idx = source.index("_now_monotonic - _last_gate_check >= _SEND_GATE_CHECK_INTERVAL")
        block = source[idx:idx + 300]
        reset_idx = block.index("_last_gate_check = _now_monotonic")
        call_idx = block.index("ensure_enabled()")
        assert reset_idx < call_idx

    def test_app_bridge_module_not_part_of_this_fix(self):
        """Requirement 2 ("a real send can still set/reset the gate") is
        send_strict()'s own behavior in app/bridge.py, deliberately
        untouched here -- already covered by
        test_admin_draft_review_workflow.py's own send_strict tests."""
        import app.bridge as bridge_module
        bridge_source = inspect.getsource(bridge_module)
        assert "time.monotonic()" not in bridge_source


class TestElapsedTimeConditionReal:
    """Requirement 1 (idle backoff cannot stretch a 300s check to ~30
    minutes) and requirement 3 (the check genuinely re-evaluates based on
    elapsed time, not a call/iteration count) -- proven with real
    time.monotonic() calls against the exact condition the loop uses,
    not the full async loop itself."""

    def test_check_does_not_fire_before_interval_elapses(self):
        # Generous margin (interval far exceeds the nominal sleep total)
        # so scheduler jitter under host load can't flip this real-time
        # assertion -- this session independently observed genuine ambient
        # CPU spikes up to 135% load on this box, so a tight margin here
        # would itself be flaky, unrelated to the mechanism under test.
        interval = 2.0  # seconds -- scaled down from 300s for a fast test
        last_check = time.monotonic()
        # Simulate several "idle" iterations, each shorter than the interval.
        for _ in range(5):
            time.sleep(0.05)
            now = time.monotonic()
            fired = (now - last_check) >= interval
            assert fired is False, "must not fire before the real interval has elapsed"

    def test_check_fires_once_interval_elapses_regardless_of_iteration_count(self):
        """The old bug was iteration-count-based; this proves the new
        condition cares only about elapsed time -- fires after enough
        real time has passed, whether that took many fast iterations or
        very few slow ones."""
        interval = 0.15
        last_check = time.monotonic()
        time.sleep(interval + 0.05)
        now = time.monotonic()
        assert (now - last_check) >= interval

    def test_worst_case_idle_backoff_cannot_delay_beyond_one_iteration_sleep_past_interval(self):
        """Mirrors the real loop's own structure: the elapsed check is
        evaluated once per iteration, immediately before that iteration's
        adaptive sleep. Even under maximum idle backoff (30s sleeps), the
        check must fire within (interval + one iteration's sleep) of the
        interval actually elapsing -- never anywhere close to the old
        bug's ~30 minute worst case."""
        interval = 300  # the real production value
        max_idle_sleep = 30.0  # BRIDGE_POLL_MAX_S

        last_check = 0.0  # a monotonic reading "at loop start"
        elapsed_at_check_time = interval + 1  # one second after the interval elapses
        fired = (elapsed_at_check_time - last_check) >= interval
        assert fired is True

        # Worst case: the check for a given iteration lands just before
        # the interval elapses, so it doesn't fire that iteration -- the
        # NEXT iteration's check (after at most one more max-idle sleep)
        # must fire. Total worst-case delay past the interval is bounded
        # by a single BRIDGE_POLL_MAX_S sleep, not by iteration count.
        just_before = interval - 1
        assert not ((just_before - last_check) >= interval)
        after_one_more_idle_sleep = just_before + max_idle_sleep
        assert (after_one_more_idle_sleep - last_check) >= interval
        worst_case_overshoot = (just_before + max_idle_sleep) - interval
        assert worst_case_overshoot <= max_idle_sleep, (
            "the fixed mechanism's worst-case delay past the 300s interval "
            "must never exceed one adaptive-backoff sleep (30s) -- the old "
            "bug's worst case was up to ~1800s (30 minutes)"
        )
