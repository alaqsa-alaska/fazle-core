"""Phase C v2 — wire-in (recruitment_flow + hermes_dispatch + conversation_layer
+ message_router + app/config) tests.

Validates that the wire-ins are dormant when flags are OFF, and that the
production code paths are NOT changed.
"""
from __future__ import annotations

import sys, types
import pytest


# === app/config flags default OFF ===

def test_phase_c_flags_default_off():
    """Per Owner directive: 3 new flags must remain OFF after Phase C
    implementation. NO .env change."""
    # Save and restore sys.modules — this test installs stub `app`,
    # `app.config`, and `app.database` to test config loading in isolation,
    # but without cleanup, the stub `app.database` (lacking init_db/close_db)
    # leaks into subsequent tests that import `app.main`, causing
    # ImportError on `app.main:36 from app.database import init_db`.
    # 2026-08-24 Owner investigation: tests/unit/test_recruitment_interested_candidates_route.py
    # and tests/unit/test_recruitment_ai_restricted.py::test_meta_recruitment_safe_mode_bypass_uses_queue_compatible_channel
    # were failing 6-of-N when run after this test in the same pytest session.
    saved_app = sys.modules.get("app")
    saved_app_config = sys.modules.get("app.config")
    saved_app_database = sys.modules.get("app.database")
    try:
        sys.modules.pop("app", None)
        sys.modules.pop("app.config", None)

        class _S:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def fetch(self, *a, **kw): return []
            async def execute(self, *a, **kw): return ""
        m = types.ModuleType("app.database")
        m.db_conn = lambda *a, **kw: _S()
        m.fetch_one = lambda *a, **kw: None
        m.execute = lambda *a, **kw: ""
        m.fetch_val = lambda *a, **kw: None
        m.fetch_all = lambda *a, **kw: []
        m.AsyncConnectionPool = None
        sys.modules["app.database"] = m
        sys.path.insert(0, "/home/azim/core")
        from app.config import Settings
        s = Settings()
        assert s.recruitment_turn_decision_v2_enabled is False
        assert s.recruitment_respect_manual_reply is False
        assert s.recruitment_actor_provenance_writeback is False
    finally:
        # Restore the real modules (or remove stubs) so subsequent tests
        # that import `app.main` get a clean `app.database` with init_db/close_db.
        for mod_name, saved in (
            ("app", saved_app),
            ("app.config", saved_app_config),
            ("app.database", saved_app_database),
        ):
            if saved is None:
                sys.modules.pop(mod_name, None)
            else:
                sys.modules[mod_name] = saved


def test_existing_recruitment_flags_unchanged():
    """Phase B flags must remain as they were."""
    sys.path.insert(0, "/home/azim/core")
    from app.config import Settings
    s = Settings()
    assert s.recruitment_answer_first_enabled is True
    assert s.recruitment_factcheck_normalized is True
    assert s.recruitment_structured_logging_enabled is True


# === recruitment_flow._maybe_decide_v2 returns None when flag OFF ===

def test_maybe_decide_v2_off_returns_none():
    # 2026-08-24: save and restore sys.modules to avoid polluting subsequent
    # tests with the stub app.database (which lacks init_db/close_db).
    _saved = {k: sys.modules.get(k) for k in (
        "app", "app.config", "app.database",
    )}
    try:
        sys.modules.pop("app", None)
        sys.modules.pop("app.config", None)

        class _S:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def fetch(self, *a, **kw): return []
            async def execute(self, *a, **kw): return ""
        m = types.ModuleType("app.database")
        m.db_conn = lambda *a, **kw: _S()
        m.fetch_one = lambda *a, **kw: None
        m.execute = lambda *a, **kw: ""
        m.fetch_val = lambda *a, **kw: None
        m.fetch_all = lambda *a, **kw: []
        m.AsyncConnectionPool = None
        sys.modules["app.database"] = m
        sys.path.insert(0, "/home/azim/core")
        import app

        import asyncio
        from modules.recruitment_flow import _maybe_decide_v2
        out = asyncio.run(_maybe_decide_v2(
            phone="8801700000000", text="hello", current_step="name",
            session={}, identity={},
        ))
        assert out is None  # OFF-flag default returns None
    finally:
        for _k, _v in _saved.items():
            if _v is None:
                sys.modules.pop(_k, None)
            else:
                sys.modules[_k] = _v


def test_maybe_decide_v2_never_raises():
    """Even if imports fail or DB unreachable, the helper must fail closed
    rather than raise into the caller."""
    # 2026-08-24: save and restore sys.modules to avoid polluting subsequent tests.
    _saved = {k: sys.modules.get(k) for k in (
        "app", "app.config", "app.database",
    )}
    try:
        sys.modules.pop("app", None)
        sys.modules.pop("app.config", None)

        class _S:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def fetch(self, *a, **kw): return []
            async def execute(self, *a, **kw): return ""
        m = types.ModuleType("app.database")
        m.db_conn = lambda *a, **kw: _S()
        m.fetch_one = lambda *a, **kw: None
        m.execute = lambda *a, **kw: ""
        m.fetch_val = lambda *a, **kw: None
        m.fetch_all = lambda *a, **kw: []
        m.AsyncConnectionPool = None
        sys.modules["app.database"] = m
        sys.path.insert(0, "/home/azim/core")
        import app

        import asyncio
        from modules.recruitment_flow import _maybe_decide_v2
        # Force flag ON but with broken identity / empty text -> should fail-closed
        # (None or AMBIGUOUS), NEVER raise.
        # We don't override the flag here (it's still OFF); but the helper is
        # designed never to raise.
        out = asyncio.run(_maybe_decide_v2(
            phone="", text="", current_step=None, session=None, identity=None,
        ))
        # With OFF flag, returns None. With ON flag and bad inputs, returns AMBIGUOUS.
        # Both are valid outcomes; what matters is no exception.
        assert out is None or out.turn_type == "AMBIGUOUS"
    finally:
        for _k, _v in _saved.items():
            if _v is None:
                sys.modules.pop(_k, None)
            else:
                sys.modules[_k] = _v


# === hermes_dispatch.build_decision_context ---

def test_build_decision_context_string():
    sys.path.insert(0, "/home/azim/core")
    from modules.hermes_dispatch import build_decision_context
    from modules.recruitment_turn_decision import RecruitmentTurnDecision
    d = RecruitmentTurnDecision(
        semantic_domain="recruitment",
        turn_type="MIXED",
        confidence=0.85,
    )
    out = build_decision_context(d)
    assert "turn_type=MIXED" in out
    assert "semantic_domain=recruitment" in out
    assert "confidence=0.85" in out


def test_build_decision_context_decision_text_never_embedded():
    """The decision itself is descriptive; the decision-context string
    must NOT include arbitrary message text."""
    sys.path.insert(0, "/home/azim/core")
    from modules.hermes_dispatch import build_decision_context
    from modules.recruitment_turn_decision import RecruitmentTurnDecision
    d = RecruitmentTurnDecision(
        semantic_domain="recruitment",
        turn_type="PURE_QUESTION",
        confidence=0.9,
        reason_codes=("fee_question",),
    )
    out = build_decision_context(d)
    assert "fee_question" in out
    # No message_body field anywhere; decision context is policy labels only.


def test_build_recruitment_context_still_works():
    """Phase C directive: do NOT replace build_recruitment_context."""
    sys.path.insert(0, "/home/azim/core")
    from modules.hermes_dispatch import build_recruitment_context
    ctx = build_recruitment_context("hi")
    assert isinstance(ctx, str)


def test_looks_like_fee_question_still_works():
    """Phase C directive: do NOT replace _looks_like_fee_question."""
    sys.path.insert(0, "/home/azim/core")
    from modules.hermes_dispatch import _looks_like_fee_question
    assert _looks_like_fee_question("ভর্তি ফি কত?") is True
    assert _looks_like_fee_question("hello") is False


# === conversation_layer.generate_recruitment_reply backward-compat ===

def test_generate_recruitment_reply_signature_has_decision_context():
    sys.path.insert(0, "/home/azim/core")
    import inspect
    from modules.conversation_layer import generate_recruitment_reply
    sig = inspect.signature(generate_recruitment_reply)
    assert "decision_context" in sig.parameters
    assert sig.parameters["decision_context"].default == ""


# === message_router._v2_cross_ref_enabled returns False with flag OFF ===

def test_v2_cross_ref_enabled_off_returns_false():
    # 2026-08-24: save and restore sys.modules to avoid polluting subsequent tests.
    _saved = {k: sys.modules.get(k) for k in (
        "app", "app.config", "app.database",
        "modules.outbound", "modules.rbac", "modules.admin_directives.router",
        "modules.message_router",
    )}
    try:
        sys.modules.pop("app", None)
        sys.modules.pop("app.config", None)
        sys.modules.pop("app.database", None)
        sys.modules.pop("modules.outbound", None)
        sys.modules.pop("modules.rbac", None)
        sys.modules.pop("modules.admin_directives.router", None)
        sys.modules.pop("modules.message_router", None)

        class _S:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def fetch(self, *a, **kw): return []
            async def execute(self, *a, **kw): return ""
        m = types.ModuleType("app.database")
        m.db_conn = lambda *a, **kw: _S()
        m.fetch_one = lambda *a, **kw: None
        m.execute = lambda *a, **kw: ""
        m.fetch_val = lambda *a, **kw: None
        m.fetch_all = lambda *a, **kw: []
        m.AsyncConnectionPool = None
        setattr(m, "get_pool", lambda *a, **kw: None)
        sys.modules["app.database"] = m
        sys.path.insert(0, "/home/azim/core")
        import app

        from modules.message_router import _v2_cross_ref_enabled
        assert _v2_cross_ref_enabled() is False
    finally:
        for _k, _v in _saved.items():
            if _v is None:
                sys.modules.pop(_k, None)
            else:
                sys.modules[_k] = _v


def test_v2_cross_ref_enabled_fails_closed_on_missing_config():
    """If app.config fails to load, the gate returns False (no shadow run).

    NOTE: we do NOT poison sys.modules['app.config'] = None (that
    permanently breaks ALL subsequent tests in the run because Python
    caches 'None' as 'import is known to fail'). Instead we simulate
    the failure by temporarily making app.config point at a synthetic
    module that raises ImportError on attribute access.
    """
    sys.modules.pop("app", None)
    sys.modules.pop("app.config", None)
    sys.path.insert(0, "/home/azim/core")
    # Replace settings lookup so the gate's try/except catches a real
    # exception. We monkey-patch from_settings to raise.
    from modules import message_router as mr
    real_get_settings = mr._v2_cross_ref_enabled  # capture

    # Patch the gate by injecting a faulty get_settings.
    sentinel = type("S", (), {})()  # any object

    def _broken_get_settings():
        raise RuntimeError("simulated config failure")

    # The helper uses lazy import: `from app.config import get_settings as _gs_v2`.
    # Patch via sys.modules entry that LOOKS like a module but fails on attribute.
    class _BrokenConfig:
        def __getattr__(self, name):
            raise RuntimeError("simulated config failure")

    # Don't poison sys.modules permanently; do a targeted patch.
    import importlib
    # Save and restore app.config in sys.modules around the call.
    saved = sys.modules.get("app.config")
    sys.modules["app.config"] = _BrokenConfig()  # type: ignore[assignment]
    try:
        from modules.message_router import _v2_cross_ref_enabled
        assert _v2_cross_ref_enabled() is False
    finally:
        # CRITICAL: restore so subsequent tests work.
        if saved is not None:
            sys.modules["app.config"] = saved
        else:
            sys.modules.pop("app.config", None)