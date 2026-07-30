"""
modules.admin_directives.router -- WhatsApp admin command dispatch for the
Unified Admin AI Engine (Phase 3 Step 5).

Public entry points (mirrors modules.admin_commands.nl_router's own
is_X/process_X convention):
  - is_admin_ai_command(text) -> bool
  - process_admin_ai_command(text, sender_phone) -> str | None

Wired into modules.message_router's admin branch AFTER is_admin_command
and is_nl_admin_query, BEFORE the hardcoded-help fallback (Step 6, not
this step). Kept out of modules.admin_commands.nl_router deliberately --
that module's own docstring is a hard "NO LLM" contract; the AI <question>
command here is the one command in this file that does call an LLM.

Every command enforces RBAC via modules.rbac.check_permission() before
doing anything else, fails closed on any check_permission() exception,
and records an audit row on every attempt (allowed or denied) via
modules.rbac.record_audit() -- same primitives every other admin command
in this codebase already uses, no new RBAC mechanism introduced. None of
the 5 new command names are registered in modules.rbac.COMMAND_ROLE --
they fall back to DEFAULT_REQUIRED_ROLE ("admin"), which already matches
every role requirement this step needs, so modules/rbac/__init__.py is
untouched by this step.

process_admin_ai_command() never raises to its caller -- every branch is
exception-guarded and returns a user-facing Bangla string on failure,
per the B25(H4) design constraint that WhatsApp admin free text must
never crash or silently misbehave.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

from modules import rbac
from modules.admin_directives import revoke_directive, save_directive
from modules.ai_console.orchestrator import orchestrate_console_request
from modules.wa_chat_frontend import get_runtime_setting, set_runtime_setting
from shared.draft_reply import create_draft_reply

log = logging.getLogger("fazle.admin_ai_router")

# ── Command patterns ─────────────────────────────────────────────────────────
_RE_AI_QUERY = re.compile(r"^(ai|এআই)[,:\s]\s*(.+)$", re.IGNORECASE | re.DOTALL)
_RE_AUTOREPLY = re.compile(r"^autoreply\s+(on|off|status)\s+(\+?[\d\s-]{7,15})$", re.IGNORECASE | re.DOTALL)
_RE_REMEMBER = re.compile(r"^remember[:\s]\s*(.+)$", re.IGNORECASE | re.DOTALL)
_RE_FORGET = re.compile(r"^forget[:\s]\s*(\d+)$", re.IGNORECASE | re.DOTALL)
_RE_SEND = re.compile(r"^send\s+(\+?[\d\s-]{7,15})\s*\|\s*(.+)$", re.IGNORECASE | re.DOTALL)

# ── Rate limiter (AI query only) ─────────────────────────────────────────────
_ai_cooldown: dict[str, float] = {}
_AI_COOLDOWN_SECONDS = 5.0

# ── Output sanitization ──────────────────────────────────────────────────────
MAX_REPLY_CHARS = 3500
_SPILLOVER_NOTE = "\n\n[সম্পূর্ণ উত্তরের জন্য ওয়েব চ্যাট ব্যবহার করুন]"
_DANGEROUS_PREFIXES = (
    "PAID ", "APPROVE ", "APPROVED ",
    "REJECT ", "REJECTED ", "REVERSE ",
    "AUTOREPLY ", "REMEMBER ", "FORGET ",
    "SEND ",
)

_DENIAL_MSG = "❌ এই কাজের অনুমতি নেই।"
_DEFAULT_SEND_BRIDGE = "bridge2"


def is_admin_ai_command(text: str) -> bool:
    """Fast, synchronous, never raises. True if text matches any of the 5
    command patterns; False otherwise (including on any error)."""
    try:
        stripped = (text or "").strip()
        if not stripped:
            return False
        return bool(
            _RE_AI_QUERY.match(stripped)
            or _RE_AUTOREPLY.match(stripped)
            or _RE_REMEMBER.match(stripped)
            or _RE_FORGET.match(stripped)
            or _RE_SEND.match(stripped)
        )
    except Exception:
        return False


def _normalize_phone(raw: str) -> str:
    """Strip spaces/dashes; '0...' -> '880...'; strip leading '+'. Matches
    the 880-prefixed format phone_block.{phone} keys are already written
    in by modules.wa_chat_frontend (WBOM/contact domain), so AUTOREPLY/SEND
    stay consistent with the gate wired in bridge_poller (Step 3)."""
    digits = re.sub(r"[\s-]+", "", raw or "")
    if digits.startswith("+"):
        digits = digits[1:]
    if digits.startswith("0"):
        digits = "880" + digits[1:]
    return digits


def _sanitize_ai_reply(text: str) -> str:
    """Prefix-warn (never strip) if the LLM's reply looks like a command
    keyword, so it can't be accidentally re-parsed as an instruction on a
    later message, then cap length with a spillover note if truncated."""
    text = text.strip()
    upper = text.upper()
    for prefix in _DANGEROUS_PREFIXES:
        if upper.startswith(prefix):
            text = "⚠️ " + text
            break
    if len(text) > MAX_REPLY_CHARS:
        cutoff = max(MAX_REPLY_CHARS - len(_SPILLOVER_NOTE), 0)
        text = text[:cutoff] + _SPILLOVER_NOTE
    return text


async def _authorize(sender_phone: str, command: str) -> Optional[dict]:
    """Returns the admin record dict if allowed; None if denied or if
    check_permission() itself raised (fail closed either way). Every
    attempt is audited via rbac.record_audit(), including denials and
    exceptions -- audit failures themselves are logged but never block
    returning the actual authorization result."""
    try:
        perm = await rbac.check_permission(phone=sender_phone, command=command)
    except Exception as exc:
        log.error(
            "[admin_ai_router] check_permission raised for command=%s phone=%s: %s",
            command, sender_phone, exc,
        )
        try:
            await rbac.record_audit(
                channel="whatsapp", command=command, actor_phone=sender_phone,
                allowed=False, denied_reason=f"check_permission exception: {exc}",
            )
        except Exception:
            log.error("[admin_ai_router] record_audit failed after check_permission exception")
        return None

    allowed = bool(perm.get("allowed"))
    try:
        await rbac.record_audit(
            channel="whatsapp", command=command, actor_phone=sender_phone,
            actor_admin=perm.get("admin"), allowed=allowed,
            required_role=perm.get("required_role"),
            denied_reason=None if allowed else perm.get("reason"),
            result_summary="allowed" if allowed else None,
        )
    except Exception:
        log.error("[admin_ai_router] record_audit failed for command=%s phone=%s", command, sender_phone)

    return perm.get("admin") if allowed else None


async def _handle_ai_query(question: str, sender_phone: str) -> str:
    now = time.monotonic()
    last = _ai_cooldown.get(sender_phone, 0.0)
    if now - last < _AI_COOLDOWN_SECONDS:
        return "⏳ একটু অপেক্ষা করুন। কিছুক্ষণ পর আবার জিজ্ঞেস করুন।"

    admin = await _authorize(sender_phone, "ai_query")
    if admin is None:
        return _DENIAL_MSG

    _ai_cooldown[sender_phone] = now

    try:
        actor_role_level = await rbac.max_role_level(admin["id"])
    except Exception:
        actor_role_level = 0  # fail closed: unresolved level -> no elevated tool access

    try:
        orchestration = await orchestrate_console_request(
            question,
            include_provider_response=True,
            actor_role_level=actor_role_level,
        )
    except Exception as exc:
        log.error("[admin_ai_router] orchestrate_console_request failed for %s: %s", sender_phone, exc)
        return "⚠️ উত্তর তৈরি করা যায়নি। আবার চেষ্টা করুন।"

    provider_result = orchestration.provider_result or {}
    evidence = orchestration.evidence or {}
    answer = provider_result.get("response") or evidence.get("response") or "উত্তর পাওয়া যায়নি।"
    return _sanitize_ai_reply(str(answer))


async def _handle_autoreply(action: str, raw_phone: str, sender_phone: str) -> str:
    admin = await _authorize(sender_phone, "ai_autoreply")
    if admin is None:
        return _DENIAL_MSG

    phone = _normalize_phone(raw_phone)

    if action == "status":
        try:
            value = await get_runtime_setting(f"phone_block.{phone}", "false")
        except Exception as exc:
            log.error("[admin_ai_router] autoreply status read failed for %s: %s", phone, exc)
            return "⚠️ স্ট্যাটাস পড়া যায়নি।"
        state = "🔴 বন্ধ" if value == "true" else "🟢 চালু"
        return f"{state}: {phone}"

    blocked = action == "off"
    try:
        await set_runtime_setting(f"phone_block.{phone}", "true" if blocked else "false")
    except Exception as exc:
        log.error("[admin_ai_router] autoreply toggle failed for %s: %s", phone, exc)
        return "⚠️ পরিবর্তন করা যায়নি।"

    try:
        await save_directive(
            f"Auto-reply {'disabled' if blocked else 'enabled'} for {phone}",
            created_by_phone=sender_phone,
            created_by_channel="whatsapp",
            category="autoreply_policy",
            subject_type="phone",
            subject_key=phone,
        )
    except Exception as exc:
        # The gate itself already changed successfully above -- a directive-
        # log failure is a secondary, non-fatal problem, not reported as if
        # the toggle failed.
        log.error("[admin_ai_router] autoreply directive log failed for %s: %s", phone, exc)

    if blocked:
        return f"✅ অটো-রিপ্লে বন্ধ করা হয়েছে: {phone}"
    return f"✅ অটো-রিপ্লে চালু করা হয়েছে: {phone}"


async def _handle_remember(directive_text: str, sender_phone: str) -> str:
    admin = await _authorize(sender_phone, "ai_remember")
    if admin is None:
        return _DENIAL_MSG
    if not directive_text:
        return "⚠️ মনে রাখার জন্য কিছু লিখুন।"
    try:
        directive_id = await save_directive(
            directive_text,
            created_by_phone=sender_phone,
            created_by_channel="whatsapp",
            category="general",
        )
    except Exception as exc:
        log.error("[admin_ai_router] save_directive failed for %s: %s", sender_phone, exc)
        return "⚠️ মনে রাখা যায়নি।"
    return f"✅ মনে রাখা হয়েছে। ID: {directive_id}"


async def _handle_forget(raw_id: str, sender_phone: str) -> str:
    admin = await _authorize(sender_phone, "ai_forget")
    if admin is None:
        return _DENIAL_MSG
    try:
        directive_id = int(raw_id)
    except ValueError:
        return "⚠️ সঠিক ID দিন।"
    try:
        revoked = await revoke_directive(directive_id, revoked_by_phone=sender_phone)
    except Exception as exc:
        log.error("[admin_ai_router] revoke_directive failed for id=%s: %s", directive_id, exc)
        return "⚠️ মুছে ফেলা যায়নি।"
    return "✅ মুছে ফেলা হয়েছে।" if revoked else "⚠️ পাওয়া যায়নি।"


async def _handle_send(raw_phone: str, message_text: str, sender_phone: str) -> str:
    admin = await _authorize(sender_phone, "ai_send_draft")
    if admin is None:
        return _DENIAL_MSG
    if not message_text:
        return "⚠️ বার্তা লিখুন।"

    phone = _normalize_phone(raw_phone)
    try:
        draft_id = await create_draft_reply(
            sender=phone,
            bridge=_DEFAULT_SEND_BRIDGE,
            draft_text=message_text,
            role="admin_ai_composed",
            intent="ai_send_request",
            context=f"AI-composed via WhatsApp admin command by {sender_phone}",
            source_module="admin_directives",
        )
    except Exception as exc:
        log.error("[admin_ai_router] create_draft_reply failed target=%s: %s", phone, exc)
        return "⚠️ ড্রাফট তৈরি করা যায়নি।"

    if draft_id is None:
        return "⚠️ একই বার্তার জন্য ইতিমধ্যে একটি পেন্ডিং ড্রাফট আছে।"

    return f"📋 ড্রাফট তৈরি হয়েছে। অনুমোদনের জন্য অপেক্ষা করুন। ID: {draft_id}"


async def process_admin_ai_command(text: str, sender_phone: str) -> Optional[str]:
    """Returns a plain-text WhatsApp reply, or None if text matches none of
    the 5 patterns (caller falls through to its own next check). Never
    raises -- the outer try/except is a final safety net on top of every
    handler's own internal exception handling."""
    try:
        stripped = (text or "").strip()
        if not stripped:
            return None

        m = _RE_AI_QUERY.match(stripped)
        if m:
            return await _handle_ai_query(m.group(2).strip(), sender_phone)

        m = _RE_AUTOREPLY.match(stripped)
        if m:
            return await _handle_autoreply(m.group(1).lower(), m.group(2), sender_phone)

        m = _RE_REMEMBER.match(stripped)
        if m:
            return await _handle_remember(m.group(1).strip(), sender_phone)

        m = _RE_FORGET.match(stripped)
        if m:
            return await _handle_forget(m.group(1), sender_phone)

        m = _RE_SEND.match(stripped)
        if m:
            return await _handle_send(m.group(1), m.group(2).strip(), sender_phone)

        return None
    except Exception as exc:
        log.error("[admin_ai_router] unhandled exception processing command from %s: %s", sender_phone, exc)
        return "⚠️ একটি সমস্যা হয়েছে। আবার চেষ্টা করুন অথবা এডমিনকে জানান।"
