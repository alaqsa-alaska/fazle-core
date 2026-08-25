"""
modules.admin_directives.router -- WhatsApp admin command dispatch for the
Unified Admin AI Engine (Phase 3 Step 5).

Public entry points (mirrors modules.admin_commands.nl_router's own
is_X/process_X convention):
  - is_admin_ai_command(text) -> bool
  - process_admin_ai_command(text, sender_phone, source_bridge="") -> str | None

Wired into modules.message_router's admin branch AFTER is_admin_command
and is_nl_admin_query, BEFORE the hardcoded-help fallback (Step 6, not
this step). Kept out of modules.admin_commands.nl_router deliberately --
that module's own docstring is a hard "NO LLM" contract; the AI <question>
command here is the one command in this file that does call an LLM.

Every command enforces RBAC via modules.rbac.check_permission() before
doing anything else, fails closed on any check_permission() exception,
and records an audit row on every attempt (allowed or denied) via
modules.rbac.record_audit() -- same primitives every other admin command
in this codebase already uses, no new RBAC mechanism introduced. Of the
5 original command names, none are registered in modules.rbac.COMMAND_ROLE
(they fall back to DEFAULT_REQUIRED_ROLE ("admin")). The 6th command added
2026-08-04, HERMES <request> (real Hermes relay, not the AI console --
see _handle_hermes_relay), IS registered there at "superadmin" and is
additionally gated to the designated Hermes control channels, a stricter
bar than every other command in this file. That channel gate is TWO
independent checks (S6, 2026-08-08; widened 2026-08-12 to add bridge1 as
a second dedicated channel, see _HERMES_CONTROL_CHANNELS): the sender's
phone must equal settings.admin_bridge2_number AND the message must have
physically arrived via source_bridge in _HERMES_CONTROL_CHANNELS --
checking phone identity alone would (mis)trust a same-numbered admin
message replayed or misrouted through an unrecognized bridge;
source_bridge is the caller-trusted value message_router already resolves
from which poller/webhook ingested the message, never taken from message
text.

process_admin_ai_command() never raises to its caller -- every branch is
exception-guarded and returns a user-facing Bangla string on failure,
per the B25(H4) design constraint that WhatsApp admin free text must
never crash or silently misbehave.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime
from typing import Optional

import httpx

from app.config import get_settings
from modules import outbound, rbac
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
# Phase 4 (2026-08-04): real Hermes relay -- deliberately distinct keyword
# from "AI " (that stays routed to fazle-core's own AI console, see
# process_admin_natural_query's docstring for why the two must never be
# conflated).
_RE_HERMES = re.compile(r"^hermes[,:\s]\s*(.+)$", re.IGNORECASE | re.DOTALL)

# ── Rate limiter (AI query only) ─────────────────────────────────────────────
_ai_cooldown: dict[str, float] = {}
_AI_COOLDOWN_SECONDS = 5.0

# ── Rate limiter + in-flight tracking (Hermes relay only) ───────────────────
# A real Hermes turn spawns a real subprocess with real LLM/tool cost and can
# take up to hermes-runner's own ~170s budget -- far longer than this
# codebase's MESSAGE_ROUTE_TIMEOUT (30s default), so it must run as a
# fire-and-forget background task (see _handle_hermes_relay), not be awaited
# inline. A longer cooldown than the AI-console query above is deliberate:
# this gates a much more expensive, real-compute operation, one bridge2
# Super Admin at a time.
_hermes_cooldown: dict[str, float] = {}
_HERMES_COOLDOWN_SECONDS = 15.0
# Keeps a strong reference to in-flight background tasks so they aren't
# garbage-collected mid-flight (a well-known asyncio footgun for
# fire-and-forget create_task() calls) -- discarded via the done-callback.
_hermes_relay_tasks: set = set()

# ── Conversation continuity (Hermes relay only, 2026-08-13) ─────────────────
# Bridge1 Hermes Control Channel follow-up: (source_bridge, sender_phone) ->
# (hermes_session_id, last_used_monotonic). Keyed per channel, not just per
# phone, so the bridge1 control channel and the older bridge2 self-chat
# mechanism are two independent conversation threads even for the same
# Admin -- switching channels never silently resumes the other's context.
# Process-local, in-memory, same pattern/durability tradeoff as
# _hermes_cooldown above: it does not survive a fazle-core restart (the
# next message after a restart simply starts a fresh Hermes conversation,
# not an error). hermes-runner independently enforces that only a
# session_id IT registered from a prior read_only turn may ever be resumed
# (see hermes-runner/server.py's _readonly_originated_sessions) -- this map
# is only ever used to CHOOSE which session_id to attempt, never itself the
# security boundary.
_hermes_conversation_sessions: dict[tuple[str, str], tuple[str, float]] = {}
_HERMES_CONVERSATION_IDLE_TTL_SECONDS = 1800  # 30 min, matches hermes-runner's own default TTL convention
_HERMES_RESET_KEYWORDS = {"reset", "clear", "নতুন কথোপকথন", "নতুন চ্যাট", "start over", "new conversation"}


def _get_active_hermes_session(source_bridge: str, sender_phone: str) -> str | None:
    """Returns the still-fresh session_id for this (channel, sender) pair,
    or None if there isn't one / it's gone idle-stale. Never raises."""
    key = (source_bridge, sender_phone)
    entry = _hermes_conversation_sessions.get(key)
    if not entry:
        return None
    session_id, last_used = entry
    if time.monotonic() - last_used > _HERMES_CONVERSATION_IDLE_TTL_SECONDS:
        _hermes_conversation_sessions.pop(key, None)
        return None
    return session_id


def _remember_hermes_session(source_bridge: str, sender_phone: str, session_id: str | None) -> None:
    """Stores/refreshes the session_id for this (channel, sender) pair.
    A None session_id (e.g. the call errored before Hermes ever returned
    one) clears any prior entry rather than storing a falsy placeholder."""
    key = (source_bridge, sender_phone)
    if session_id:
        _hermes_conversation_sessions[key] = (session_id, time.monotonic())
    else:
        _hermes_conversation_sessions.pop(key, None)


def _clear_hermes_session(source_bridge: str, sender_phone: str) -> None:
    _hermes_conversation_sessions.pop((source_bridge, sender_phone), None)

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

# Bridge1 Hermes Control Channel (Owner-directed, 2026-08-12): the two
# arrival channels this codebase now recognizes as the dedicated Admin<->
# Hermes conversation. bridge2 = the original self-chat mechanism
# (unchanged, still requires "hermes," prefix -- see the separate
# is_from_me=1 outgoing-message scanner that calls process_admin_ai_command
# directly). bridge1 = the new dedicated control channel (bare text, no
# prefix -- see process_admin_natural_query's bypass below). This
# constant only widens which ARRIVAL channel is accepted; sender_phone ==
# settings.admin_bridge2_number remains an independent, unweakened check
# everywhere it's used -- widening this set never changes WHO is admitted,
# only which bridge their message may travel through.
_HERMES_CONTROL_CHANNELS = frozenset({"bridge2", "bridge1"})


def is_admin_ai_command(text: str) -> bool:
    """Fast, synchronous, never raises. True if text matches any of the 6
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
            or _RE_HERMES.match(stripped)
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


_HERMES_NOT_CONFIGURED_MSG = "⚠️ Hermes relay কনফিগার করা নেই (HERMES_RUNNER_SECRET সেট করা হয়নি)।"


async def _call_hermes_readonly(
    request_text: str, *, log_label: str, readonly_key: str | None = None,
    hermes_session_id: str | None = None, force_read_only: bool = True,
) -> tuple[str, str | None]:
    """Shared core of the hermes-runner call -- extracted (2026-08-04,
    Phase 5B) so both the WhatsApp-triggered relay (_deliver_hermes_reply)
    and the alert-triggered investigation (trigger_admin_investigation) use
    the exact same HTTP-call logic rather than a second, drifting copy.
    Returns (reply_text, hermes_session_id) -- reply_text is always a
    display-ready string (a real reply or a Bangla error message), never
    raises. hermes_session_id is hermes-runner's own returned id when it
    gave us one (even on some error responses), else None -- callers that
    care about conversation continuity (_deliver_hermes_reply) persist it;
    trigger_admin_investigation ignores it, matching its own always-
    stateless nature.

    readonly_key (2026-08-05, Owner decision): hermes-runner serializes
    read_only calls per readonly_key instead of one shared bucket when this
    is set (see hermes-runner/server.py's _lock_key_for) -- so the WhatsApp
    relay and each Phase 5B job's investigation no longer contend with each
    other just because neither carries a real hermes_session_id. Omitting
    it keeps the prior shared-bucket behavior, unchanged. Also selects the
    MiniMax-M3 model override in hermes-runner (WHATSAPP_ADMIN_READONLY_KEY)
    -- this is independent of force_read_only below, see that flag's own
    note.

    hermes_session_id (2026-08-13, Bridge1 Hermes Control Channel
    follow-up): when set, asks hermes-runner to --resume that exact
    conversation instead of starting a new one. When force_read_only=True,
    hermes-runner only honors this for a session_id it previously
    registered as originating from a read_only call (see its own
    _readonly_originated_sessions); when force_read_only=False, it's a
    normal interactive resume (same as the web Hermes UI) -- fazle-core
    never supplies an id here that didn't itself come back from a prior
    hermes-runner call for this exact (source_bridge, sender_phone) pair
    (see _get_active_hermes_session), so there is no cross-tenant session
    guessing risk either way.

    force_read_only (2026-08-13, Full-authority Phase 1, Owner-approved):
    True (default, unchanged for every pre-existing caller) sends
    read_only=true, which hermes-runner hard-locks to force_mode="READ"
    regardless of whatever BUILD/RUN mode is currently persisted for the
    interactive web Hermes UI -- trigger_admin_investigation (Phase 5B,
    an unattended/automated alert trigger, never a live human turn) must
    always use this default; it would be a real safety regression for a
    scheduled job's investigation to silently inherit an elevation the
    Owner granted for their own live session. False (used ONLY by
    _deliver_hermes_reply's live, human-initiated Super Admin relay turn)
    omits read_only entirely, so hermes-runner falls through to whatever
    mode is currently persisted in its own current_mode.txt -- the EXACT
    same value the interactive web UI already uses. This does not add a
    new elevation mechanism: BUILD/RUN is still only ever granted via the
    existing `/api/hermes/mode` web endpoint (admin JWT via assistant-
    platform's requireAdmin middleware, chat.iamazim.com -> a thin proxy
    to hermes-runner's own Bearer-secret-gated POST /mode) or a direct
    call to that same hermes-runner endpoint.

    STALE-DOCSTRING CORRECTIONS (2026-08-25, verified against current
    code, not assumed):
    (1) "existing TTL/auto-revert" is no longer universally true --
    hermes-runner's write_mode_state() gained an explicit `permanent`
    parameter (2026-08-25 Owner decision) that sets expires_at=None for
    BUILD/RUN with no auto-revert; current_mode.txt is a permanent RUN
    grant as of this writing. The web UI's /mode proxy (assistant-
    platform/backend/src/routes/hermes.js) does NOT yet forward this
    `permanent` flag -- only a direct hermes-runner API call can request
    it today; the web toggle still only sends mode/ttl_seconds/scope.
    (2) Since a 2026-08-20 hermes-runner change, any call with
    readonly_key=="readonly:whatsapp_relay" (i.e. every _deliver_hermes_
    reply call, this function) unconditionally receives the RUN toolset
    (file/code_execution/terminal) regardless of what mode is currently
    persisted/reported -- the "inherits whatever mode is persisted"
    description above is therefore incomplete for this specific caller;
    the reported `mode` in logs can say READ while the actual toolset
    granted is RUN-level. All three
    pre-existing gates on reaching this call at all -- RBAC superadmin,
    exact admin_bridge2_number identity, and source_bridge in
    _HERMES_CONTROL_CHANNELS (see _handle_hermes_relay) -- are completely
    unchanged; this flag only changes what happens once someone has
    already passed all three AND the Owner has separately, out-of-band,
    elevated mode. Within an elevated turn, Hermes's own built-in
    confirm-before-destructive prompt (never bypassed, --yolo never
    passed) and scripts/restart_gate.sh's independent checks are what
    bound any actually irreversible action -- this function does not add
    a second confirmation layer of its own, per this project's standing
    rule against inventing a parallel mechanism where one already exists."""
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=175.0) as client:
            resp = await client.post(
                f"{settings.hermes_runner_url}/run",
                headers={"Authorization": f"Bearer {settings.hermes_runner_secret}"},
                json={
                    "message": request_text,
                    "read_only": force_read_only,
                    **({"readonly_key": readonly_key} if readonly_key else {}),
                    **({"hermes_session_id": hermes_session_id} if hermes_session_id else {}),
                },
            )
        try:
            data = resp.json()
        except ValueError:
            data = {}
        returned_session_id = data.get("hermes_session_id") or None
        if resp.status_code != 200:
            return f"⚠️ Hermes ত্রুটি: {data.get('error', f'status {resp.status_code}')}", returned_session_id
        # Mode is logged (never the message/reply content) so every
        # elevated-capable turn leaves a durable, restart-surviving trail
        # of which mode it actually ran under -- Full-authority Phase 1
        # attributability requirement (Super Admin -> Hermes session ->
        # mode -> result), same journald sink as every other log line in
        # this codebase.
        log.info(
            "[admin_ai_router] %s: hermes call completed mode=%s session=%s force_read_only=%s",
            log_label, data.get("mode"), returned_session_id, force_read_only,
        )
        return (data.get("reply") or "⚠️ Hermes থেকে কোনো উত্তর আসেনি।"), returned_session_id
    except httpx.TimeoutException:
        return "⚠️ Hermes সময়মতো উত্তর দেয়নি।", None
    except Exception as exc:
        log.error("[admin_ai_router] %s: hermes call failed: %s", log_label, exc)
        return "⚠️ Hermes-এর সাথে যোগাযোগ করা যায়নি।", None


async def _deliver_hermes_reply(request_text: str, sender_phone: str, source_bridge: str = "bridge2") -> None:
    """Background task: makes the actual (potentially ~170s) call to
    hermes-runner and delivers the result as a separate outbound WhatsApp
    message. Never awaited inline by _handle_hermes_relay -- see that
    function's docstring for why (MESSAGE_ROUTE_TIMEOUT).

    source_bridge (Owner-directed, 2026-08-12, bridge1 Hermes Control
    Channel): which bridge the REPLY goes out on -- always the same bridge
    the triggering request arrived on (see _handle_hermes_relay's call
    site), never a hardcoded literal, so a bridge2 self-chat request keeps
    replying via bridge2 exactly as before, and a bridge1 request replies
    via bridge1. Defaults to "bridge2" so every other/older call site's
    behavior is unchanged byte-for-byte. Also the key (together with
    sender_phone) for conversation continuity below -- bridge1 and bridge2
    self-chat are two independent Hermes conversation threads.

    Conversation continuity (2026-08-13): looks up any still-fresh session
    for this (source_bridge, sender_phone) pair and asks hermes-runner to
    resume it; always stores whatever session_id comes back (a fresh one
    if there was none, the same one if resumed, or none if the call
    errored before Hermes returned anything) so the NEXT message in this
    conversation continues correctly. A recognized reset keyword clears
    any existing session first, starting a deliberately fresh
    conversation -- see _HERMES_RESET_KEYWORDS.

    force_read_only=False (Full-authority Phase 1, Owner-approved,
    2026-08-13): this is the one live, human-initiated Super Admin turn in
    this module -- see _call_hermes_readonly's own docstring for exactly
    what this does and does not change. trigger_admin_investigation
    (below) deliberately keeps the default True and must never be changed
    to match this."""
    resume_session_id = None
    if request_text.strip().lower() in _HERMES_RESET_KEYWORDS:
        _clear_hermes_session(source_bridge, sender_phone)
    else:
        resume_session_id = _get_active_hermes_session(source_bridge, sender_phone)

    reply_text, new_session_id = await _call_hermes_readonly(
        request_text, log_label=f"hermes relay for {sender_phone}", readonly_key="readonly:whatsapp_relay",
        hermes_session_id=resume_session_id, force_read_only=False,
    )
    _remember_hermes_session(source_bridge, sender_phone, new_session_id)

    try:
        await outbound.enqueue(
            sender_phone,
            f"🤖 Hermes:\n{reply_text}",
            source_bridge=source_bridge,
            purpose="hermes_relay_reply",
        )
    except Exception as exc:
        log.error("[admin_ai_router] hermes relay reply enqueue failed for %s: %s", sender_phone, exc)


# ── Phase 5B (2026-08-04): proactive alert-triggered investigation ──────────
# Owner-approved option (b) ONLY (see management_decisions.md "Hermes Phase
# 5B"): fazle-core's existing, already-proven alert jobs (dlq_alert,
# health_summary, bridge_watchdog) are the trigger source -- no new
# detection engine, no always-on LLM polling. Each job gains exactly one
# additional call, inside its own existing "alert fired" branch, wrapped in
# try/except at the CALL SITE too so a failure here can never regress the
# job's own already-proven native alert delivery.
#
# Default-DISABLED via settings.hermes_alert_investigation_enabled -- a
# fail-closed kill switch independent of code deployment; must be
# explicitly turned on in .env. Delivery always targets
# settings.admin_bridge2_number via source_bridge="bridge2", matching the
# Phase 4 relay's own established convention -- deliberately distinct from
# the underlying jobs' own native notification transport (bridge1,
# unchanged, untouched by this phase).
_investigation_cooldown: dict[str, float] = {}


async def trigger_admin_investigation(job_name: str, alert_context: str) -> None:
    """Fires a one-shot, read-only Hermes investigation for an already-
    alerting job, and delivers the report to the canonical Super Admin
    (bridge2) via the existing relay path. Fail-closed: any missing
    config, empty context, or per-job cooldown hit means no send, with a
    logged reason -- never a silent no-op. Never raises (matches every
    other function in this file's contract) -- a caller in a scheduler
    job's own alert branch must never have this break that job."""
    settings = get_settings()

    if not job_name or not (alert_context or "").strip():
        log.warning("[phase5b_trigger] job=%s event=skipped reason=empty_context", job_name or "unknown")
        return

    if not settings.hermes_alert_investigation_enabled:
        log.info("[phase5b_trigger] job=%s event=skipped reason=feature_disabled", job_name)
        return

    if not settings.hermes_runner_secret:
        log.warning("[phase5b_trigger] job=%s event=skipped reason=hermes_not_configured", job_name)
        return

    bridge2_number = settings.admin_bridge2_number or ""
    if not bridge2_number:
        log.warning("[phase5b_trigger] job=%s event=skipped reason=bridge2_not_configured", job_name)
        return

    now = time.monotonic()
    cooldown_s = max(int(settings.hermes_alert_investigation_cooldown_seconds or 3600), 60)
    last = _investigation_cooldown.get(job_name, 0.0)
    if now - last < cooldown_s:
        log.info(
            "[phase5b_trigger] job=%s event=skipped reason=cooldown remaining_s=%d",
            job_name, int(cooldown_s - (now - last)),
        )
        return
    _investigation_cooldown[job_name] = now

    log.info("[phase5b_trigger] job=%s event=triggered", job_name)

    request_text = (
        f"The fazle-core scheduler job '{job_name}' just fired an alert. "
        f"Alert context:\n{alert_context}\n\n"
        "Investigate (read-only) and report: likely root cause if "
        "determinable from available evidence, severity, and a recommended "
        "next action. If there is insufficient evidence, say so explicitly "
        "rather than guessing."
    )
    # Deliberately stateless -- no hermes_session_id passed in and the
    # returned one (2nd tuple element) is discarded, unchanged from before
    # this function had a session_id to discard at all. Each alert
    # investigation is its own independent turn; see _call_hermes_readonly's
    # own docstring for why this differs from _deliver_hermes_reply.
    #
    # force_read_only intentionally left at its default (True) -- Full-
    # authority Phase 1 (2026-08-13) only extends elevated-mode capability
    # to _deliver_hermes_reply's live, human-initiated Super Admin turn.
    # This function fires unattended, from inside a scheduler job's own
    # alert branch; it must never silently inherit a BUILD/RUN elevation
    # the Owner granted for their own live session. Do not add
    # force_read_only=False here.
    reply_text, _ = await _call_hermes_readonly(
        request_text, log_label=f"phase5b investigation for job={job_name}", readonly_key=f"readonly:job:{job_name}",
    )
    log.info("[phase5b_trigger] job=%s event=investigation_complete", job_name)

    try:
        await outbound.enqueue(
            bridge2_number,
            f"🔍 Hermes investigation — {job_name}:\n{reply_text}",
            source_bridge="bridge2",
            purpose="phase5b_alert_investigation",
            idempotency_key=f"phase5b-{job_name}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
        )
        log.info("[phase5b_trigger] job=%s event=report_sent", job_name)
    except Exception as exc:
        log.error("[phase5b_trigger] job=%s event=report_send_failed error=%s", job_name, exc)


async def _handle_hermes_relay(request_text: str, sender_phone: str, source_bridge: str = "") -> str:
    """Phase 4 (2026-08-04): relays an admin's WhatsApp request to the real
    Hermes (assistant-platform's BUILD/RUN-capable agent).

    UPDATED, Full-authority Phase 1 (2026-08-13, Owner-approved): the
    delivered reply (_deliver_hermes_reply, see its own docstring for the
    2026-08-25 corrections -- permanent-mode grants now exist, and since
    2026-08-20 this caller unconditionally gets the RUN toolset regardless
    of reported mode) now inherits whatever READ/BUILD/RUN mode is
    currently persisted for Hermes -- it is no longer unconditionally
    hard-locked to READ. Mode is still ONLY ever elevated via the existing
    `/api/hermes/mode` web endpoint (admin JWT, chat.iamazim.com) or a
    direct hermes-runner API call; nothing in this WhatsApp relay path can
    elevate mode itself. When mode is at its fail-closed default (READ),
    a relayed message's *reported* mode still says READ -- see
    _deliver_hermes_reply's docstring for why that no longer means
    investigate-only for this specific caller. Nothing about the three gates
    below changed.

    Gated on three INDEPENDENT checks, all required: RBAC role (superadmin,
    via _authorize below and COMMAND_ROLE['hermes_relay']), the sender's
    phone matching settings.admin_bridge2_number, AND source_bridge itself
    being one of _HERMES_CONTROL_CHANNELS (checked together, after
    _authorize, so every attempt -- including a channel-mismatch denial --
    gets its own honest audit row rather than silently reusing
    _authorize's RBAC-only verdict).

    _HERMES_CONTROL_CHANNELS (Owner-directed, 2026-08-12, bridge1 Hermes
    Control Channel): widened from "bridge2" only to {"bridge2", "bridge1"}
    -- bridge1 is a NEW dedicated Admin<->Hermes channel (the Admin
    messages bridge1's own number directly, a genuine is_from_me=0 inbound
    message, no self-chat ambiguity), alongside the original bridge2
    self-chat mechanism, not a replacement for it. The identity check
    below (wrong_identity) is untouched -- widening the channel set never
    widens WHO is admitted, only which bridge their message may arrive on.
    bridge3 and any other value remain denied, unchanged. A reply to a
    bridge1-arriving request is delivered back out via bridge1 (see
    _deliver_hermes_reply's source_bridge parameter) -- this deliberately
    lands as a new inbound message on bridge2 (the Admin's own number is
    the recipient), which is why modules.message_router.process_message()
    has its own matching cross-bridge echo guard (2026-08-12) recognizing
    and silently ignoring exactly that reply shape arriving back on
    bridge2, so it's never mistaken for a new admin request.

    source_bridge (S6, 2026-08-08): phone-identity alone is not a channel
    check -- it would (mis)trust an admin_bridge2_number-matching sender
    replayed or misrouted through bridge1/bridge3. source_bridge is
    message_router's own trusted `source` value (which poller/webhook
    actually ingested this message), never derived from message text, so
    it can't be spoofed by anything the sender types.

    Runs the actual Hermes call as a background task rather than awaiting
    it inline: this codebase's message-routing pipeline wraps every inbound
    message in MESSAGE_ROUTE_TIMEOUT (30s default), far shorter than a real
    Hermes turn can take (~170s). Returns an immediate ack; the real reply
    is delivered as a separate outbound WhatsApp message once Hermes
    actually responds (see _deliver_hermes_reply)."""
    admin = await _authorize(sender_phone, "hermes_relay")
    if admin is None:
        return _DENIAL_MSG

    settings = get_settings()
    wrong_identity = sender_phone != (settings.admin_bridge2_number or "")
    wrong_channel = source_bridge not in _HERMES_CONTROL_CHANNELS
    if wrong_identity or wrong_channel:
        try:
            await rbac.record_audit(
                channel="whatsapp", command="hermes_relay", actor_phone=sender_phone,
                actor_admin=admin, allowed=False,
                denied_reason=(
                    "RBAC allowed but sender is not on a designated Hermes control channel "
                    f"(identity_mismatch={wrong_identity}, source_bridge={source_bridge or 'unknown'})"
                ),
            )
        except Exception:
            log.error("[admin_ai_router] record_audit failed for hermes_relay channel-mismatch, phone=%s", sender_phone)
        return _DENIAL_MSG

    if not request_text:
        return "⚠️ Hermes-কে কী জিজ্ঞেস করতে চান লিখুন।"

    if not settings.hermes_runner_secret:
        return _HERMES_NOT_CONFIGURED_MSG

    now = time.monotonic()
    last = _hermes_cooldown.get(sender_phone, 0.0)
    if now - last < _HERMES_COOLDOWN_SECONDS:
        return "⏳ একটু অপেক্ষা করুন। Hermes ইতিমধ্যে একটি অনুরোধ প্রক্রিয়া করছে।"
    _hermes_cooldown[sender_phone] = now

    task = asyncio.create_task(_deliver_hermes_reply(request_text, sender_phone, source_bridge))
    _hermes_relay_tasks.add(task)
    task.add_done_callback(_hermes_relay_tasks.discard)

    # UX fix (2026-08-12): no intermediate "sent to Hermes" acknowledgement
    # -- the Admin already knows they're talking to Hermes. An empty string
    # still short-circuits process_admin_ai_command's dispatch correctly
    # ("" is not None), and bridge_poller only sends a WhatsApp message when
    # `if reply:` is truthy, so this turn produces no visible message at
    # all; the real answer still arrives moments later as its own message
    # via _deliver_hermes_reply's outbound.enqueue() call above.
    return ""


async def process_admin_ai_command(text: str, sender_phone: str, source_bridge: str = "") -> Optional[str]:
    """Returns a plain-text WhatsApp reply, or None if text matches none of
    the 6 patterns (caller falls through to its own next check). Never
    raises -- the outer try/except is a final safety net on top of every
    handler's own internal exception handling.

    source_bridge: message_router's trusted `source` value for this inbound
    message (which poller/webhook ingested it). Only the HERMES <request>
    command uses it (see _handle_hermes_relay) -- every other command here
    is phone-identity-scoped only, unchanged. Defaults to "" for existing
    callers/tests that don't pass it; that default can never satisfy the
    Hermes relay's source_bridge in _HERMES_CONTROL_CHANNELS check, so
    omitting it is fail-closed, not a bypass."""
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

        m = _RE_HERMES.match(stripped)
        if m:
            return await _handle_hermes_relay(m.group(1).strip(), sender_phone, source_bridge)

        return None
    except Exception as exc:
        log.error("[admin_ai_router] unhandled exception processing command from %s: %s", sender_phone, exc)
        return "⚠️ একটি সমস্যা হয়েছে। আবার চেষ্টা করুন অথবা এডমিনকে জানান।"


async def process_admin_natural_query(text: str, sender_phone: str, source_bridge: str = "") -> Optional[str]:
    """Owner decision 2026-08-04 (bridge3 natural-language admin routing):
    fallback path for admin text that matched none of the 5 explicit
    command patterns above (and none of message_router's own
    structured-command/keyword checks). Routes straight to the same
    _handle_ai_query() -> orchestrate_console_request() engine used for
    explicit 'AI <question>' commands -- tool-grounded and RAG-grounded,
    NOT the raw ungrounded LLM call the B25(H4) fallback in message_router
    was hardened against. Reuses the same rate-limit/RBAC/sanitization the
    explicit-prefix path already has. Returns None only for empty text.

    NOTE (2026-08-04, corrected same day): this is fazle-core's own AI
    console, NOT the real Hermes (assistant-platform's BUILD/RUN-capable
    agent) -- earlier comments in this file/message_router.py incorrectly
    called this "Hermes"; fixed to avoid conflating the two systems, per
    the same 2026-08-04 decision that keeps them deliberately separate.
    The real Hermes is reached via the HERMES <request> command below.

    EXCEPTION (2026-08-12, direct Owner instruction -- Hermes conversation
    UX fix; widened same day to add bridge1, see _HERMES_CONTROL_CHANNELS):
    on the channels this codebase treats as dedicated Admin<->Hermes
    channels -- sender phone == settings.admin_bridge2_number and
    source_bridge in _HERMES_CONTROL_CHANNELS ({"bridge2", "bridge1"}), the
    same channel identity _handle_hermes_relay itself requires -- unprefixed
    free text is no longer conflated with the AI console: it's delegated
    straight to _handle_hermes_relay, so the Admin doesn't have to type
    "Hermes," on every message once already in that conversation.
    _handle_hermes_relay still does its own full RBAC check
    (_authorize("hermes_relay")) and audit logging internally --
    deliberately not duplicated here, so an implicit message gets exactly
    one authorize+audit call, same as an explicit "Hermes," message. Every
    other admin/channel (bridge3, a non-superadmin/non-bridge2 phone) is
    unaffected and keeps this function's original AI-console behavior,
    unchanged."""
    stripped = (text or "").strip()
    if not stripped:
        return None

    settings = get_settings()
    if sender_phone == (settings.admin_bridge2_number or "") and source_bridge in _HERMES_CONTROL_CHANNELS:
        return await _handle_hermes_relay(stripped, sender_phone, source_bridge)

    try:
        return await _handle_ai_query(stripped, sender_phone)
    except Exception as exc:
        log.error("[admin_ai_router] natural-query fallback failed for %s: %s", sender_phone, exc)
        return "⚠️ একটি সমস্যা হয়েছে। আবার চেষ্টা করুন অথবা এডমিনকে জানান।"
