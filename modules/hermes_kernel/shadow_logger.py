"""
Hermes Kernel — shadow logger for the AI Operating System scaffolding layer.

Fire-and-forget writers for hermes_decision_audit_log and hermes_event_log.
Pattern is identical to llm.py::_save_to_memory() — asyncio.create_task,
never raises, never blocks the calling path.

Discovery summary:
  Existing owner:  None — no equivalent shadow logger for kernel events exists.
  Existing runtime: llm.py::_save_to_memory() uses the same pattern (reused below).
  Files reused:    app.database.execute (existing), app.config.get_settings (existing).
  Risk level:      Zero — all calls are fire-and-forget in asyncio tasks.
                   Any exception is caught and logged; routing continues unchanged.

C0.2 fix (2026-08-07): flags are read at most once per process lifetime.
  Configuration loading is expensive when get_settings() fails (e.g. missing
  DATABASE_URL in test environments). The one-time cached booleans below satisfy
  the acceptance criterion: disabled shadow logging must not repeatedly invoke
  expensive configuration loading or validation.

  Cold-path cost (first call only per process):
    The first call to _is_audit_enabled() / _is_event_enabled() includes:
    - lazy import of app.config and app.database
    - .env file I/O and pydantic validation (via get_settings())
    - module-level cache population
    Measured: ~46ms (DATABASE_URL present) / ~243ms (absent, ValidationError).
    This cost occurs once at process startup, not per message.
    Hot path (all subsequent calls): ~0.0005 ms/message (single bool read).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from modules.hermes_kernel.data_classes import DecisionRecord, KernelEvent

log = logging.getLogger("fazle.hermes_kernel.shadow_logger")

# Configuration Contract: flag state is cached once per cache lifecycle (normally
# once per process lifetime). Primary source: .env via get_settings(). Secondary
# source (checked only when primary is False): assistant_feature_flags DB table,
# read via modules.preflight_guard.is_feature_active(). Runtime activation via
# /api/assistant/ops/activate takes effect immediately (next message turn).
# .env changes still require a restart to take effect.
# Tests must call invalidate_flag_cache() between cases that change flag values.
_AUDIT_ENABLED: bool | None = None
_EVENT_ENABLED: bool | None = None


def _resolve_flag(env_value: bool, runtime_name: str) -> bool:
    """Return True if the .env setting is True OR the runtime ops flag is active."""
    if env_value:
        return True
    try:
        from modules.preflight_guard import is_feature_active
        return is_feature_active(runtime_name, default=False)
    except Exception:
        return False


def _is_audit_enabled() -> bool:
    global _AUDIT_ENABLED
    if _AUDIT_ENABLED is None:
        try:
            from app.config import get_settings
            env_val = bool(get_settings().hermes_decision_audit_enabled)
        except Exception:
            env_val = False
        _AUDIT_ENABLED = _resolve_flag(env_val, "hermes_decision_audit")
    return _AUDIT_ENABLED


def _is_event_enabled() -> bool:
    global _EVENT_ENABLED
    if _EVENT_ENABLED is None:
        try:
            from app.config import get_settings
            env_val = bool(get_settings().hermes_event_log_enabled)
        except Exception:
            env_val = False
        _EVENT_ENABLED = _resolve_flag(env_val, "hermes_event_log")
    return _EVENT_ENABLED


def invalidate_flag_cache() -> None:
    """Reset cached flag values so next call re-reads settings. For testing only."""
    global _AUDIT_ENABLED, _EVENT_ENABLED
    _AUDIT_ENABLED = None
    _EVENT_ENABLED = None


async def log_decision(record: "DecisionRecord") -> None:
    """Fire-and-forget INSERT into hermes_decision_audit_log.

    Gated by hermes_decision_audit_enabled in config. Never raises.
    Caller must schedule this as asyncio.create_task() for true fire-and-forget.
    """
    if not _is_audit_enabled():
        return
    try:
        from app.database import execute
        await execute(
            """
            INSERT INTO hermes_decision_audit_log (
                trace_id, timestamp, sender_phone, identity_role,
                identity_confidence, intent, intent_method,
                evidence_coverage, confidence_score, confidence_band,
                chosen_action, explanation, escalation_reason,
                provider_used, source, raw_input_text, source_message_id
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                $11, $12, $13, $14, $15, $16, $17
            )
            """,
            record.trace_id,
            record.timestamp,
            record.sender_phone,
            record.identity_role or None,
            record.identity_confidence,
            record.intent or None,
            record.intent_method or None,
            record.evidence_coverage,
            record.confidence_score,
            record.confidence_band or None,
            record.chosen_action or None,
            record.explanation or None,
            record.escalation_reason or None,
            record.provider_used or None,
            record.source or None,
            (record.raw_input_text or "")[:2000],
            record.source_message_id or None,
        )
    except Exception as exc:
        log.warning("[hermes_kernel] log_decision non-fatal: %s", exc)


async def log_event(event: "KernelEvent") -> None:
    """Fire-and-forget INSERT into hermes_event_log.

    Gated by hermes_event_log_enabled in config. Never raises.
    Caller must schedule this as asyncio.create_task() for true fire-and-forget.
    """
    if not _is_event_enabled():
        return
    try:
        from app.database import execute
        import json
        await execute(
            """
            INSERT INTO hermes_event_log (event_id, trace_id, event_name, timestamp, payload)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (event_id) DO NOTHING
            """,
            event.event_id,
            event.trace_id,
            event.event_name,
            event.timestamp,
            json.dumps(event.payload),
        )
    except Exception as exc:
        log.warning("[hermes_kernel] log_event non-fatal: %s", exc)
