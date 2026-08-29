"""
Fazle Core — Admin Command Processor
Parses and executes admin commands received via WhatsApp.

Command formats (case-insensitive):
  APPROVE <id>                    — approve draft reply AND send to recipient
  REJECT <id>                     — reject draft
  EDIT <id> <new text>            — edit draft text
  PAID <id> <amount> <method>     — mark payment as paid and notify accountant
  ADVANCE <id> <amount> <method>  — approve advance payment and notify accountant
  STATUS                          — show pending drafts count
  DRAFTS                          — list recent pending drafts

Sprint-3B (Canonical Financial Approval):
  APPROVED <id> <amount> <method> — approve payment draft via canonical create_transaction()
  DREDIT <id> <amount> <method> [payout=<phone>] — edit a pending payment draft
  DREJECT <id> [reason]           — reject a payment draft (no transaction)

APPROVE completes the full loop: load draft → mark approved → deliver to recipient
via the correct bridge → mark sent_at. This fires even in SAFE MODE — admin approval
IS the decision to send.
"""
import logging
import re
from typing import Optional

from app.database import fetch_one, fetch_all, execute, fetch_val
from app.config import get_settings

log = logging.getLogger("fazle.admin_cmd")

# ── Stage G6.15 (2026-08-28): read-only Admin command help surface ──────────
# Single source of truth -- was previously only reachable as the
# "command not understood" fallback (still is, unchanged); now ALSO
# reachable explicitly via a bare "HELP" command. Only ever lists commands
# genuinely implemented below in this same file -- never advertises
# something unimplemented. Never exposes internal RBAC role names/config.
_HELP_TEXT = (
    "ব্যবহার:\n"
    "  APPROVE <id>            — ড্রাফট পাঠান\n"
    "  APPROVE <id> <id> ...   — একসাথে একাধিক\n"
    "  REJECT <id>             — বাতিল\n"
    "  EDIT <id> <নতুন বার্তা>\n"
    "  PAID <id> <amount> <method>\n"
    "  STATUS / DRAFTS         — পেন্ডিং তালিকা\n\n"
    "Cash transaction review (Stage H):\n"
    "  PROPOSAL DETAILS <id>           — inspect only, no mutation\n"
    "  PROPOSAL APPROVE <id>           — one approval = one execution, no second APPROVE needed\n"
    "  PROPOSAL REJECT <id> [reason]   — rejects the proposal only, business record untouched\n"
    "  CASH DETAILS <txn-ref>          — inspect only, no mutation\n"
    "  CASH APPROVE <txn-ref>\n"
    "  CASH REJECT <txn-ref> <reason>   — rejects the REVIEW SUBMISSION only;\n"
    "                                      the transaction itself stays pending\n"
    "  CASH REVERSE <txn-ref> <reason>   — final transactions only, always requires a reason\n\n"
    "বাংলা সংখ্যাও কাজ করে: APPROVE ১৬৫"
)

# ── Bengali digit normalisation (Batch 25) ─────────────────────────────────────
# Allow admins to type APPROVE ১৬৫. Map Bangla digits to ASCII before regex.
_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

# ── Duplicate-command suppression (Batch 25, H1) ───────────────────────────────
# In-process LRU keyed by sha1(text+admin_phone). 30s TTL, max 256 entries.
# Single-worker assumption (uvicorn --workers 1). Documented in /memories.
import hashlib as _hashlib
import time as _time
from collections import OrderedDict as _OrderedDict
_DEDUP_TTL_S = 30.0
_DEDUP_MAX = 256
_dedup_cache: "_OrderedDict[str, float]" = _OrderedDict()

def _dedup_seen(text: str, admin_phone: str) -> bool:
    """Return True if this (text, admin) was processed within TTL. Updates cache."""
    key = _hashlib.sha1(f"{admin_phone}|{text.strip()}".encode("utf-8")).hexdigest()
    now = _time.time()
    # Evict expired
    while _dedup_cache:
        k0, t0 = next(iter(_dedup_cache.items()))
        if now - t0 > _DEDUP_TTL_S:
            _dedup_cache.popitem(last=False)
        else:
            break
    if key in _dedup_cache:
        return True
    _dedup_cache[key] = now
    if len(_dedup_cache) > _DEDUP_MAX:
        _dedup_cache.popitem(last=False)
    return False

# ── Command patterns ───────────────────────────────────────────────────────────
# H5: APPROVE / REJECT now accept one OR many IDs separated by spaces or commas.
_APPROVE_RE       = re.compile(r"^approve\s+([\d,\s]+)$", re.IGNORECASE)
_REJECT_RE        = re.compile(r"^reject\s+([\d,\s]+)$", re.IGNORECASE)
_EDIT_RE          = re.compile(r"^edit\s+(\d+)\s+(.+)$", re.IGNORECASE | re.DOTALL)
# 2026-07-27: Human-format admin replies from the draft-review message.
# Supports: "Draft 123 / 1", "Draft 123 / Approved", "Draft 123 / 1. Approved",
#           "Draft 123 / 2 / <edited text>", "Draft 123 / Edit / <edited text>",
#           "Draft 123 / 3", "Draft 123 / Reject"
# Option keywords map: 1/Approved→approve, 2/Edit→edit, 3/Reject→reject
_DRAFT_REPLY_RE   = re.compile(
    r"^draft\s+(\d+)\s*[/\-:]\s*(.+)$",
    re.IGNORECASE | re.DOTALL,
)
_PAID_RE          = re.compile(r"^paid\s+(\d+)\s+(\d[\d,]*)\s*(bkash|nagad|cash)?$", re.IGNORECASE)
_ADVANCE_RE       = re.compile(r"^advance\s+(\d+)\s+(\d[\d,]*)\s*(bkash|nagad|cash)?$", re.IGNORECASE)
# Sprint-3B: Canonical Draft Approval commands
_APPROVED_RE      = re.compile(r"^approved\s+(\d+)\s+(\d[\d,]*)\s*(bkash|nagad|cash)?$", re.IGNORECASE)
_DREDIT_RE        = re.compile(r"^dredit\s+(\d+)\s+(\d[\d,]*)\s*(bkash|nagad|cash)?(?:\s+payout=(\S+))?\s*$", re.IGNORECASE)
_DREJECT_RE       = re.compile(r"^dreject\s+(\d+)(?:\s+(.+))?\s*$", re.IGNORECASE)
_STATUS_RE        = re.compile(r"^(status|drafts|pending)$", re.IGNORECASE)
_PAYIMPORT_RE     = re.compile(r"^pay-?import\b\s*(.+)$", re.IGNORECASE | re.DOTALL)
# RELEASE <program_id> <YYYY-MM-DD> <D|N> <release_point...> [days=<float>]
_RELEASE_RE       = re.compile(
    r"^release\s+(\d+)\s+(\d{4}-\d{1,2}-\d{1,2})\s+([DNdn])\s+(.+?)(?:\s+days=([\d.]+))?\s*$",
    re.IGNORECASE,
)
# ── PAYROLL (Batch 14) ────────────────────────────────────────────────────────
_PAYROLL_COMPUTE_RE = re.compile(
    r"^payroll\s+compute\s+(\d{4})-(\d{1,2})(?:\s+(\d+))?\s*$", re.IGNORECASE,
)
_PAYROLL_TRANS_RE = re.compile(
    r"^payroll\s+(submit|approve|lock)\s+(\d+)\s*$", re.IGNORECASE,
)
_PAYROLL_PAID_RE = re.compile(
    r"^payroll\s+paid\s+(\d+)\s+(\d[\d,]*)\s+(bkash|nagad|cash)(?:\s+ref=(\S+))?\s*$",
    re.IGNORECASE,
)
_PAYROLL_CANCEL_RE = re.compile(
    r"^payroll\s+cancel\s+(\d+)\s+(.+)$", re.IGNORECASE | re.DOTALL,
)
_PAYROLL_LIST_RE = re.compile(
    r"^payroll\s+list\s+(\d{4})-(\d{1,2})(?:\s+(\w+))?\s*$", re.IGNORECASE,
)
# ── SCHEDULER (Batch 16) ──────────────────────────────────────────────────────
_SCHEDULE_STATUS_RE = re.compile(
    r"^(?:schedule\s+status|সময়সূচী)\s*$", re.IGNORECASE,
)
_SCHEDULE_RUN_RE = re.compile(
    r"^(?:run\s+job|কাজ\s+চালাও)\s+([a-zA-Z_][\w\-]*)\s*$", re.IGNORECASE,
)
# ── REPORTS (Batch 17) ─────────────────────────────────────────────────────────
_REPORT_DAILY_RE = re.compile(
    r"^(?:report\s+daily|রিপোর্ট\s+দিনের)(?:\s+(\d{4}-\d{1,2}-\d{1,2}))?\s*$", re.IGNORECASE,
)
_REPORT_PAYROLL_RE = re.compile(
    r"^(?:report\s+payroll|রিপোর্ট\s+বেতন)\s+(\d{4})-(\d{1,2})\s*$", re.IGNORECASE,
)
_REPORT_CASH_RE = re.compile(
    r"^(?:report\s+cash|রিপোর্ট\s+ক্যাশ)(?:\s+(\d+))?\s*$", re.IGNORECASE,
)
_REPORT_RECON_RE = re.compile(
    r"^(?:report\s+recon|report\s+reconciliation|রিপোর্ট\s+মিল)(?:\s+(\d+))?\s*$", re.IGNORECASE,
)
_REPORT_ESCORT_RE = re.compile(
    r"^(?:report\s+escort|রিপোর্ট\s+এসকর্ট)\s+(\d{4}-\d{1,2}-\d{1,2})\s+(\d{4}-\d{1,2}-\d{1,2})\s*$", re.IGNORECASE,
)
_REPORT_LIST_RE = re.compile(
    r"^(?:report\s+list|রিপোর্ট\s+তালিকা)\s*$", re.IGNORECASE,
)
# ── BACKUP (Batch 18) ─────────────────────────────────────────────────────────
_BACKUP_STATUS_RE = re.compile(
    r"^(?:backup\s+status|ব্যাকআপ\s+স্ট্যাটাস)\s*$", re.IGNORECASE,
)
_BACKUP_NOW_RE = re.compile(
    r"^(?:backup\s+now|ব্যাকআপ\s+এখন)\s*$", re.IGNORECASE,
)
_BACKUP_LIST_RE = re.compile(
    r"^(?:backup\s+list|ব্যাকআপ\s+তালিকা)(?:\s+(\d+))?\s*$", re.IGNORECASE,
)
# ── USER MGMT (Batch 19) ──────────────────────────────────────────────────────
_USER_LIST_RE = re.compile(
    r"^(?:user\s+list|ইউজার\s+তালিকা)\s*$", re.IGNORECASE,
)
# user add <phone> <name…> [role]
_USER_ADD_RE = re.compile(
    r"^(?:user\s+add|ইউজার\s+যোগ)\s+(\+?\d{8,15})\s+(.+?)(?:\s+(viewer|operator|accountant|admin|superadmin))?\s*$",
    re.IGNORECASE,
)
# user role <phone> <role>
_USER_ROLE_RE = re.compile(
    r"^(?:user\s+role|ইউজার\s+রোল)\s+(\+?\d{8,15})\s+(viewer|operator|accountant|admin|superadmin)\s*$",
    re.IGNORECASE,
)
# user remove <phone>  (disable, not delete)
_USER_REMOVE_RE = re.compile(
    r"^(?:user\s+remove|ইউজার\s+মুছো)\s+(\+?\d{8,15})\s*$", re.IGNORECASE,
)
# user apikey <phone>
_USER_APIKEY_RE = re.compile(
    r"^(?:user\s+apikey|ইউজার\s+কী)\s+(\+?\d{8,15})\s*$", re.IGNORECASE,
)
# ESCORTCONFIRM <order_id> | <escort_name> | <escort_mobile> | <date> | <shift D/N>
_ESCORTCONFIRM_RE = re.compile(
    r"^escortconfirm\s+(\d+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([DNdn])",
    re.IGNORECASE,
)
# Escort confirmation review (Step 5, 2026-08-01): admin replies to an
# "Admin REMINDER <id>" push (see modules.escort._create_escort_confirmation_
# review_draft) with "<id> Type: APPROVE|EDIT|REJECT [<corrected message>]".
# Deliberately a separate command/handler from the generic APPROVE/EDIT/
# REJECT above -- that EDIT sends the edited text to the original sender,
# which is the wrong meaning for a confirmation correction (see
# docs/PLAN_ESCORT_CONFIRMATION_MULTI_ENTRY_2026-08-01.md Step 5).
_ESCORT_REVIEW_RE = re.compile(
    r"^(\d+)\s+type\s*:\s*(approve|edit|reject)(?:\s+(.+))?$",
    re.IGNORECASE | re.DOTALL,
)
# REVERSE <transaction_id> <reason…>
_REVERSE_RE = re.compile(r"^reverse\s+\d+.*$", re.IGNORECASE)
# ADJUST <draft_id> <amount> <method>
_ADJUST_RE = re.compile(r"^adjust\s+\d+.*$", re.IGNORECASE)

# ── Stage H6/H7/H8 (2026-08-28): CASH APPROVE/REJECT/REVERSE ──────────────
# Distinct from the bare (pre-existing, never-wired) REVERSE/ADJUST above --
# those match a plain fpe_payment_drafts-style numeric id with no "CASH"
# prefix and are dead code (recognized by is_admin_command() but never
# classified/dispatched); left untouched, out of this stage's scope. These
# operate on modules.financial_operations, resolving a txn-ref (or numeric
# id) against the ALREADY-CREATED fpe_cash_transactions row, not a draft.
_CASH_APPROVE_RE = re.compile(r"^cash\s+approve\s+(\S+)\s*$", re.IGNORECASE)
_CASH_REJECT_RE  = re.compile(r"^cash\s+reject\s+(\S+)\s+(.+)$", re.IGNORECASE | re.DOTALL)
_CASH_REVERSE_RE = re.compile(r"^cash\s+reverse\s+(\S+)\s+(.+)$", re.IGNORECASE | re.DOTALL)
# Stage I5 (2026-08-29): read-only inspection, checked as its own
# pattern (not folded into _CASH_APPROVE_RE) so "CASH DETAILS ..." never
# accidentally matches a mutating command.
_CASH_DETAILS_RE = re.compile(r"^cash\s+details\s+(\S+)\s*$", re.IGNORECASE)
# Stage J7 (2026-08-29): business-proposal commands are deliberately
# namespaced under "PROPOSAL ..." rather than overloading bare APPROVE/
# REJECT -- APPROVE <id> already targets fazle_draft_replies exclusively
# (see the dispatch chain below); a draft and a proposal can share the
# same numeric id (both do right now: draft ids and proposal ids 29/30/
# 31 are independent sequences), so a single shared "APPROVE <id>"
# surface would need collision detection this stage did not judge safe
# to build blind. This prefix makes resolution deterministic by
# construction -- no guessing which table an id belongs to.
_PROPOSAL_APPROVE_RE = re.compile(r"^proposal\s+approve\s+(\d+)\s*$", re.IGNORECASE)
_PROPOSAL_REJECT_RE  = re.compile(r"^proposal\s+reject\s+(\d+)(?:\s+(.+))?\s*$", re.IGNORECASE | re.DOTALL)
_PROPOSAL_DETAILS_RE = re.compile(r"^proposal\s+details\s+(\d+)\s*$", re.IGNORECASE)


def is_report_command(text: str) -> bool:
    """Narrow subset of is_admin_command() — matches ONLY the report_*
    command shapes (report list/daily/payroll/cash/recon/escort), nothing
    else. Added 2026-08-12 (Owner-directed, Phase 1 "Accountant Reports")
    so modules.message_router's Accountant branch can safely offer report
    access without also exposing APPROVE/PAID/REVERSE/etc. -- this
    function is the entire safety boundary on the message_router side;
    process_admin_command() itself additionally re-checks RBAC
    permission (rbac.check_permission(), fails closed) keyed on the
    caller's own phone, so even a caller of this function bypassing the
    regex gate would still need a registered, sufficiently-privileged
    fazle_admins row to get anything back. All 6 report commands are
    read-only (see modules/reports/__init__.py) -- no report handler
    writes to any table."""
    t = text.strip()
    return bool(
        _REPORT_DAILY_RE.match(t) or _REPORT_PAYROLL_RE.match(t) or
        _REPORT_CASH_RE.match(t) or _REPORT_RECON_RE.match(t) or
        _REPORT_ESCORT_RE.match(t) or _REPORT_LIST_RE.match(t)
    )


def is_admin_command(text: str) -> bool:
    """Quick check if message is an admin command."""
    t = text.strip()
    return bool(
        _APPROVE_RE.match(t) or _REJECT_RE.match(t) or _EDIT_RE.match(t) or
        _DRAFT_REPLY_RE.match(t) or
        _PAID_RE.match(t) or _ADVANCE_RE.match(t) or _STATUS_RE.match(t) or
        _ESCORTCONFIRM_RE.match(t) or _PAYIMPORT_RE.match(t) or _RELEASE_RE.match(t) or
        _PAYROLL_COMPUTE_RE.match(t) or _PAYROLL_TRANS_RE.match(t) or
        _PAYROLL_PAID_RE.match(t) or _PAYROLL_CANCEL_RE.match(t) or
        _PAYROLL_LIST_RE.match(t) or
        _SCHEDULE_STATUS_RE.match(t) or _SCHEDULE_RUN_RE.match(t) or
        _REPORT_DAILY_RE.match(t) or _REPORT_PAYROLL_RE.match(t) or
        _REPORT_CASH_RE.match(t) or _REPORT_RECON_RE.match(t) or
        _REPORT_ESCORT_RE.match(t) or _REPORT_LIST_RE.match(t) or
        _BACKUP_STATUS_RE.match(t) or _BACKUP_NOW_RE.match(t) or
        _BACKUP_LIST_RE.match(t) or
        _USER_LIST_RE.match(t) or _USER_ADD_RE.match(t) or
        _USER_ROLE_RE.match(t) or _USER_REMOVE_RE.match(t) or
        _USER_APIKEY_RE.match(t) or
        _REVERSE_RE.match(t) or _ADJUST_RE.match(t) or
        # Sprint-3B
        _APPROVED_RE.match(t) or _DREDIT_RE.match(t) or _DREJECT_RE.match(t) or
        # Step 5 (2026-08-01) escort confirmation review
        _ESCORT_REVIEW_RE.match(t) or
        # Stage H6/H7/H8 (2026-08-28)
        _CASH_APPROVE_RE.match(t) or _CASH_REJECT_RE.match(t) or _CASH_REVERSE_RE.match(t) or
        # Stage I5 (2026-08-29)
        _CASH_DETAILS_RE.match(t) or
        # Stage J7 (2026-08-29)
        _PROPOSAL_APPROVE_RE.match(t) or _PROPOSAL_REJECT_RE.match(t) or _PROPOSAL_DETAILS_RE.match(t)
    )


def _classify_command(t: str) -> Optional[str]:
    """Map text → COMMAND_ROLE key (used by RBAC). Returns None if not admin cmd."""
    pairs = (
        (_APPROVE_RE, "approve"), (_REJECT_RE, "reject"), (_EDIT_RE, "edit"),
        (_DRAFT_REPLY_RE, "approve"),  # human-format draft reply — same RBAC as approve
        (_PAID_RE, "paid"), (_ADVANCE_RE, "advance"),
        (_STATUS_RE, "status"),
        (_PAYIMPORT_RE, "payimport"), (_RELEASE_RE, "release"),
        (_PAYROLL_COMPUTE_RE, "payroll_compute"),
        (_PAYROLL_TRANS_RE, "payroll_trans"),
        (_PAYROLL_PAID_RE, "payroll_paid"),
        (_PAYROLL_CANCEL_RE, "payroll_cancel"),
        (_PAYROLL_LIST_RE, "payroll_list"),
        (_SCHEDULE_STATUS_RE, "schedule_status"),
        (_SCHEDULE_RUN_RE, "schedule_run"),
        (_REPORT_LIST_RE, "report_list"),
        (_REPORT_DAILY_RE, "report_daily"),
        (_REPORT_PAYROLL_RE, "report_payroll"),
        (_REPORT_CASH_RE, "report_cash"),
        (_REPORT_RECON_RE, "report_recon"),
        (_REPORT_ESCORT_RE, "report_escort"),
        (_BACKUP_STATUS_RE, "backup_status"),
        (_BACKUP_NOW_RE, "backup_now"),
        (_BACKUP_LIST_RE, "backup_list"),
        (_USER_LIST_RE, "user_list"),
        (_USER_ADD_RE, "user_add"),
        (_USER_ROLE_RE, "user_role"),
        (_USER_REMOVE_RE, "user_remove"),
        (_USER_APIKEY_RE, "user_apikey"),
        (_ESCORTCONFIRM_RE, "escortconfirm"),
        # Sprint-3B — reuse paid/edit/reject RBAC roles
        (_APPROVED_RE, "paid"),
        (_DREDIT_RE, "edit"),
        (_DREJECT_RE, "reject"),
                # Step 5 (2026-08-01) — same RBAC tier as escortconfirm (admin
        # managing escort assignments)
        (_ESCORT_REVIEW_RE, "escortconfirm"),
        # Stage H6/H7/H8 (2026-08-28) — CASH APPROVE/REJECT/REVERSE
        (_CASH_APPROVE_RE, "cash_approve"),
        (_CASH_REJECT_RE, "cash_reject"),
        (_CASH_REVERSE_RE, "cash_reverse"),
        # Stage I5 (2026-08-29) — CASH DETAILS (read-only)
        (_CASH_DETAILS_RE, "cash_details"),
        # Stage J7 (2026-08-29) — PROPOSAL APPROVE/REJECT/DETAILS
        (_PROPOSAL_APPROVE_RE, "proposal_approve"),
        (_PROPOSAL_REJECT_RE, "proposal_reject"),
        (_PROPOSAL_DETAILS_RE, "proposal_details"),
    )
    for rx, name in pairs:
        if rx.match(t):
            return name
    return None


async def process_admin_command(text: str, admin_phone: str) -> str:
    """
    Parse and execute admin command. Returns confirmation text to send back to admin.
    NEVER sends to external parties — only returns text.
    Calling code decides whether to forward to accountant etc.
    """
    # B25: normalise Bengali digits + dedup duplicate sends within TTL window
    t = text.strip().translate(_BN_DIGITS)

    # Stage G6.15: explicit, read-only HELP -- no RBAC command classification,
    # no mutation, no dedup tracking (asking for help twice must always work).
    if t.strip().upper() == "HELP":
        return _HELP_TEXT

    if _dedup_seen(t, admin_phone):
        try:
            from modules import observability as _obs
            _obs.inc("admin_command_dedup_total")
        except Exception:
            pass
        log.info(f"[admin_cmd] dedup_suppressed admin={admin_phone} text={t[:60]!r}")
        return ""  # silent drop, no double-process

    # ── Batch 19 — RBAC guard + audit ────────────────────────────────────────
    # SECURITY: this gate must fail CLOSED. If rbac.check_permission() itself
    # raises (DB hiccup, etc.), the command is denied — it must never fall
    # through to execution just because the permission check couldn't run.
    cmd_name = _classify_command(t)
    if cmd_name is not None:
        from modules import rbac
        try:
            perm = await rbac.check_permission(phone=admin_phone, command=cmd_name)
        except Exception as e:
            log.error(f"[admin_cmd] rbac check raised — failing CLOSED, command denied: {e}")
            try:
                await rbac.record_audit(
                    channel="whatsapp", command=cmd_name,
                    actor_phone=admin_phone, actor_admin=None,
                    args=t[:400], allowed=False,
                    required_role=None,
                    denied_reason=f"rbac_check_exception: {e}",
                )
            except Exception as audit_exc:
                log.warning(f"[admin_cmd] audit (rbac exception) failed: {audit_exc}")
            return (
                "⛔ অনুমতি যাচাই করা যায়নি (সিস্টেম ত্রুটি)। নিরাপত্তার কারণে কমান্ডটি বাতিল করা হলো। "
                "আবার চেষ্টা করুন বা অ্যাডমিনকে জানান।"
            )
        if not perm["allowed"]:
            await rbac.record_audit(
                channel="whatsapp", command=cmd_name,
                actor_phone=admin_phone, actor_admin=perm.get("admin"),
                args=t[:400], allowed=False,
                required_role=perm.get("required_role"),
                denied_reason=perm.get("reason"),
            )
            return (
                f"⛔ অনুমতি নেই: কমান্ড `{cmd_name}` চালাতে "
                f"`{perm.get('required_role')}` রোল প্রয়োজন।"
            )
        # allowed — record (best-effort, non-blocking)
        try:
            await rbac.record_audit(
                channel="whatsapp", command=cmd_name,
                actor_phone=admin_phone, actor_admin=perm.get("admin"),
                args=t[:400], allowed=True,
                required_role=perm.get("required_role"),
            )
        except Exception as e:
            log.warning(f"[admin_cmd] audit (allowed) failed: {e}")
    # ─────────────────────────────────────────────────────────────────────────

    # 2026-07-27: Human-format "Draft <id> / <option>" replies — checked
    # BEFORE the legacy APPROVE/REJECT/EDIT patterns so the new format wins
    # when admin replies directly to the draft-review WhatsApp message.
    m = _DRAFT_REPLY_RE.match(t)
    if m:
        draft_id = int(m.group(1))
        return await _cmd_draft_reply(draft_id, m.group(2), admin_phone)

    m = _APPROVE_RE.match(t)
    if m:
        ids = _parse_id_list(m.group(1))
        if not ids:
            return "❌ কোনো বৈধ ID পাওয়া যায়নি। উদাহরণ: APPROVE 165 অথবা APPROVE 165,162,161"
        if len(ids) == 1:
            return await _cmd_approve(ids[0], admin_phone)
        return await _cmd_approve_many(ids, admin_phone)

    m = _REJECT_RE.match(t)
    if m:
        ids = _parse_id_list(m.group(1))
        if not ids:
            return "❌ কোনো বৈধ ID পাওয়া যায়নি। উদাহরণ: REJECT 167 অথবা REJECT 167,164"
        if len(ids) == 1:
            return await _cmd_reject(ids[0], admin_phone)
        return await _cmd_reject_many(ids, admin_phone)

    m = _EDIT_RE.match(t)
    if m:
        return await _cmd_edit(int(m.group(1)), m.group(2).strip(), admin_phone)

    m = _PAID_RE.match(t)
    if m:
        draft_id = int(m.group(1))
        amount   = float(m.group(2).replace(",", ""))
        method   = (m.group(3) or "cash").lower()
        return await _cmd_paid(draft_id, amount, method, admin_phone, draft_type="escort_payment")

    m = _ADVANCE_RE.match(t)
    if m:
        draft_id = int(m.group(1))
        amount   = float(m.group(2).replace(",", ""))
        method   = (m.group(3) or "cash").lower()
        return await _cmd_paid(draft_id, amount, method, admin_phone, draft_type="advance")

    # ── Sprint-3B: Canonical Draft Approval ────────────────────────────────
    m = _APPROVED_RE.match(t)
    if m:
        draft_id = int(m.group(1))
        amount   = float(m.group(2).replace(",", ""))
        method   = (m.group(3) or "cash").lower()
        return await _cmd_approved(draft_id, amount, method, admin_phone)

    m = _DREDIT_RE.match(t)
    if m:
        draft_id = int(m.group(1))
        amount   = float(m.group(2).replace(",", ""))
        method   = (m.group(3) or "cash").lower()
        payout   = m.group(4)
        return await _cmd_dredit(draft_id, amount, method, payout, admin_phone)

    m = _DREJECT_RE.match(t)
    if m:
        draft_id = int(m.group(1))
        reason   = m.group(2).strip() if m.group(2) else None
        return await _cmd_dreject(draft_id, admin_phone, reason)

    # ── Stage I5 (2026-08-29): CASH DETAILS (read-only) ─────────────────────
    m = _CASH_DETAILS_RE.match(t)
    if m:
        return await _cmd_cash_details(m.group(1))

    # ── Stage J7 (2026-08-29): PROPOSAL APPROVE/REJECT/DETAILS ──────────────
    m = _PROPOSAL_DETAILS_RE.match(t)
    if m:
        return await _cmd_proposal_details(int(m.group(1)))

    m = _PROPOSAL_APPROVE_RE.match(t)
    if m:
        return await _cmd_proposal_approve(int(m.group(1)), admin_phone)

    m = _PROPOSAL_REJECT_RE.match(t)
    if m:
        return await _cmd_proposal_reject(int(m.group(1)), (m.group(2) or "").strip() or None, admin_phone)

    # ── Stage H6/H7/H8 (2026-08-28): CASH APPROVE/REJECT/REVERSE ────────────
    m = _CASH_APPROVE_RE.match(t)
    if m:
        return await _cmd_cash_approve(m.group(1), admin_phone)

    m = _CASH_REJECT_RE.match(t)
    if m:
        return await _cmd_cash_reject(m.group(1), m.group(2).strip(), admin_phone)

    m = _CASH_REVERSE_RE.match(t)
    if m:
        return await _cmd_cash_reverse(m.group(1), m.group(2).strip(), admin_phone)

    if _STATUS_RE.match(t):
        return await _cmd_status()

    m = _PAYIMPORT_RE.match(t)
    if m:
        return await _cmd_pay_import(m.group(1).strip(), admin_phone)

    m = _RELEASE_RE.match(t)
    if m:
        return await _cmd_release(
            int(m.group(1)), m.group(2), m.group(3).upper(),
            m.group(4).strip(), float(m.group(5)) if m.group(5) else None,
            admin_phone,
        )

    m = _PAYROLL_COMPUTE_RE.match(t)
    if m:
        y = int(m.group(1)); mo = int(m.group(2))
        emp = int(m.group(3)) if m.group(3) else None
        return await _cmd_payroll_compute(y, mo, emp, admin_phone)

    m = _PAYROLL_TRANS_RE.match(t)
    if m:
        return await _cmd_payroll_transition(m.group(1).lower(), int(m.group(2)), admin_phone)

    m = _PAYROLL_PAID_RE.match(t)
    if m:
        run_id = int(m.group(1))
        amount = float(m.group(2).replace(",", ""))
        method = m.group(3).lower()
        ref    = m.group(4)
        return await _cmd_payroll_paid(run_id, amount, method, ref, admin_phone)

    m = _PAYROLL_CANCEL_RE.match(t)
    if m:
        return await _cmd_payroll_cancel(int(m.group(1)), m.group(2).strip(), admin_phone)

    m = _PAYROLL_LIST_RE.match(t)
    if m:
        y = int(m.group(1)); mo = int(m.group(2))
        st = m.group(3).lower() if m.group(3) else None
        return await _cmd_payroll_list(y, mo, st)

    if _SCHEDULE_STATUS_RE.match(t):
        return await _cmd_schedule_status()

    m = _SCHEDULE_RUN_RE.match(t)
    if m:
        return await _cmd_schedule_run(m.group(1), admin_phone)

    if _REPORT_LIST_RE.match(t):
        return await _cmd_report_list()

    m = _REPORT_DAILY_RE.match(t)
    if m:
        return await _cmd_report_daily(m.group(1), admin_phone)

    m = _REPORT_PAYROLL_RE.match(t)
    if m:
        return await _cmd_report_payroll(int(m.group(1)), int(m.group(2)), admin_phone)

    m = _REPORT_CASH_RE.match(t)
    if m:
        return await _cmd_report_cash(int(m.group(1)) if m.group(1) else 30, admin_phone)

    m = _REPORT_RECON_RE.match(t)
    if m:
        return await _cmd_report_recon(int(m.group(1)) if m.group(1) else 7, admin_phone)

    m = _REPORT_ESCORT_RE.match(t)
    if m:
        return await _cmd_report_escort(m.group(1), m.group(2), admin_phone)

    if _BACKUP_STATUS_RE.match(t):
        return await _cmd_backup_status()

    if _BACKUP_NOW_RE.match(t):
        return await _cmd_backup_now(admin_phone)

    m = _BACKUP_LIST_RE.match(t)
    if m:
        return await _cmd_backup_list(int(m.group(1)) if m.group(1) else 10)

    m = _ESCORTCONFIRM_RE.match(t)
    if m:
        order_id     = int(m.group(1))
        escort_name  = m.group(2).strip()
        escort_mobile = m.group(3).strip()
        date_str     = m.group(4).strip()
        shift        = m.group(5).upper()
        return await _cmd_escort_confirm(order_id, escort_name, escort_mobile, date_str, shift, admin_phone)

    # Step 5 (2026-08-01): "<id> Type: APPROVE|EDIT|REJECT [<corrected message>]"
    m = _ESCORT_REVIEW_RE.match(t)
    if m:
        review_id = int(m.group(1))
        action = m.group(2).lower()
        extra = (m.group(3) or "").strip()
        from modules.escort import (
            approve_escort_confirmation_review,
            edit_escort_confirmation_review,
            reject_escort_confirmation_review,
        )
        if action == "approve":
            return await approve_escort_confirmation_review(review_id, admin_phone)
        if action == "reject":
            return await reject_escort_confirmation_review(review_id, admin_phone)
        # edit
        if not extra:
            return (
                f"❌ #{review_id} EDIT-এর জন্য সম্পূর্ণ সংশোধিত confirmation বার্তা দিতে হবে "
                f"(শুধু '{review_id} Type: EDIT' যথেষ্ট নয়)।"
            )
        return await edit_escort_confirmation_review(review_id, extra, admin_phone)

    # ── user mgmt (B19) ──
    if _USER_LIST_RE.match(t):
        return await _cmd_user_list()

    m = _USER_ADD_RE.match(t)
    if m:
        return await _cmd_user_add(
            m.group(1), m.group(2).strip(),
            (m.group(3) or "viewer").lower(), admin_phone,
        )

    m = _USER_ROLE_RE.match(t)
    if m:
        return await _cmd_user_role(m.group(1), m.group(2).lower(), admin_phone)

    m = _USER_REMOVE_RE.match(t)
    if m:
        return await _cmd_user_remove(m.group(1), admin_phone)

    m = _USER_APIKEY_RE.match(t)
    if m:
        return await _cmd_user_apikey(m.group(1), admin_phone)

    return "❌ কমান্ড বুঝিনি।\n\n" + _HELP_TEXT


# ── Multi-ID helpers (Batch 25, H5) ─────────────────────────────────────────────
import re as _re

_ID_SPLIT = _re.compile(r"[,\s]+")


def _parse_id_list(raw: str) -> list[int]:
    """Parse 'APPROVE/REJECT' tail like '165 162,161' → [165, 162, 161]. Dedups."""
    seen: set[int] = set()
    out: list[int] = []
    for tok in _ID_SPLIT.split(raw.strip()):
        if not tok:
            continue
        try:
            n = int(tok)
        except ValueError:
            continue
        if n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


async def _cmd_approve_many(ids: list[int], admin_phone: str) -> str:
    lines = [f"📦 Bulk APPROVE — {len(ids)} ড্রাফট"]
    sent_ok = 0
    for did in ids:
        try:
            res = await _cmd_approve(did, admin_phone)
            if res.startswith("✅"):
                sent_ok += 1
            # Compress per-line response (first line only)
            first = res.splitlines()[0] if res else ""
            lines.append(f"  #{did}: {first[:90]}")
        except Exception as e:
            lines.append(f"  #{did}: ❌ {e}")
    lines.append(f"\nফলাফল: {sent_ok}/{len(ids)} পাঠানো হয়েছে।")
    return "\n".join(lines)


async def _cmd_reject_many(ids: list[int], admin_phone: str) -> str:
    lines = [f"🚫 Bulk REJECT — {len(ids)} ড্রাফট"]
    rejected = 0
    for did in ids:
        try:
            res = await _cmd_reject(did, admin_phone)
            if res.startswith("🚫"):
                rejected += 1
            first = res.splitlines()[0] if res else ""
            lines.append(f"  #{did}: {first[:90]}")
        except Exception as e:
            lines.append(f"  #{did}: ❌ {e}")
    lines.append(f"\nফলাফল: {rejected}/{len(ids)} বাতিল।")
    return "\n".join(lines)


# ── Command implementations ────────────────────────────────────────────────────

async def _cmd_approve(draft_id: int, admin_phone: str) -> str:
    """
    Approve a draft reply — mark approved then SEND to recipient immediately.
    Admin approval IS the decision to send, regardless of AUTO_REPLY_ENABLED.
    """
    try:
        row = await fetch_one(
            "SELECT * FROM fazle_draft_replies WHERE id = $1", draft_id
        )
        if not row:
            return f"❌ Draft #{draft_id} পাওয়া যায়নি।"
        if row.get("status") not in ("pending", None, "edited"):
            return f"⚠️ Draft #{draft_id} ইতিমধ্যে {row.get('status', 'processed')}।"

        recipient = row.get("recipient", "")
        reply_text = row.get("reply_text", "")
        source = row.get("source", "bridge1")
        intent = row.get("intent", "")

        # Attendance drafts: shared finalization service (Owner decision,
        # 2026-07-11) -- validates, resolves the employee, and saves
        # atomically with marking the draft approved. Never marks approved
        # if finalization fails, unlike the old inline flow. Also used by
        # modules.drafts.routes.approve_draft (frontend Review Queue) so
        # both channels share one implementation.
        if intent == "attendance":
            from modules.attendance import finalize_attendance_draft
            result = await finalize_attendance_draft(draft_id, channel="whatsapp_message", actor=admin_phone)
            if not result.get("ok"):
                return f"❌ Draft #{draft_id} হাজিরা নিশ্চিত করা যায়নি: {result.get('error')}"
            if not result.get("already_approved"):
                await _send_attendance_confirmation(result, source)
                return f"✅ হাজিরা draft #{draft_id} অনুমোদিত ও সংরক্ষিত হয়েছে।"
            return f"ℹ️ Draft #{draft_id} ইতিমধ্যে অনুমোদিত ছিল (কোনো পরিবর্তন হয়নি)।"

        # Mark approved first
        await execute(
            "UPDATE fazle_draft_replies SET status='approved', admin_phone=$1, approved_at=NOW() WHERE id=$2",
            admin_phone, draft_id,
        )

        # Attempt to deliver — import bridges here to avoid circular imports
        sent = False
        error_text = ""
        try:
            from app.bridge import get_bridge1, get_bridge2
            bridge = get_bridge1() if source in ("bridge1", "meta") else get_bridge2()
            # Bridge expects JID format for non-meta sends
            jid = recipient if "@" in recipient else f"{recipient}@s.whatsapp.net"
            sent = await bridge.send(jid, reply_text)
            if sent:
                await execute(
                    "UPDATE fazle_draft_replies SET status='sent', sent_at=NOW() WHERE id=$1",
                    draft_id,
                )
                log.info(f"[admin_cmd] Draft #{draft_id} sent to {recipient} via {source}")
            else:
                error_text = "Bridge send returned false"
                await execute(
                    "UPDATE fazle_draft_replies SET error_text=$1 WHERE id=$2",
                    error_text, draft_id,
                )
        except Exception as send_err:
            error_text = str(send_err)[:200]
            log.error(f"[admin_cmd] Draft #{draft_id} send error: {send_err}")
            await execute(
                "UPDATE fazle_draft_replies SET error_text=$1 WHERE id=$2",
                error_text, draft_id,
            )

        preview = reply_text[:200] if reply_text else ""
        if sent:
            return (
                f"✅ Draft #{draft_id} অনুমোদিত ও পাঠানো হয়েছে।\n\n"
                f"প্রাপক: {recipient}\n"
                f"বার্তা:\n{preview}"
            )
        else:
            return (
                f"✅ Draft #{draft_id} অনুমোদিত — কিন্তু পাঠাতে সমস্যা হয়েছে।\n"
                f"ত্রুটি: {error_text}\n\n"
                f"প্রাপক: {recipient}\n"
                f"বার্তা:\n{preview}"
            )
    except Exception as e:
        log.error(f"[admin_cmd] approve error: {e}")
        return f"❌ ত্রুটি: {e}"


async def _cmd_reject(draft_id: int, admin_phone: str) -> str:
    """Reject a draft reply."""
    try:
        row = await fetch_one(
            "SELECT id, status FROM fazle_draft_replies WHERE id = $1", draft_id
        )
        if not row:
            return f"❌ Draft #{draft_id} পাওয়া যায়নি।"
        if row.get("status") in ("sent", "rejected"):
            return f"⚠️ Draft #{draft_id} ইতিমধ্যে {row.get('status')}।"

        await execute(
            "UPDATE fazle_draft_replies SET status='rejected', admin_phone=$1, approved_at=NOW() WHERE id=$2",
            admin_phone, draft_id,
        )
        return f"🚫 Draft #{draft_id} বাতিল করা হয়েছে।"
    except Exception as e:
        log.error(f"[admin_cmd] reject error: {e}")
        return f"❌ ত্রুটি: {e}"


async def _cmd_edit(draft_id: int, new_text: str, admin_phone: str) -> str:
    """Edit draft reply text AND send the edited text to the original sender.

    2026-07-27: Previously only updated the draft text in DB without sending.
    Now completes the full edit flow: update draft → send edited text to the
    original recipient via the SAME inbound bridge/Meta number/channel
    (stored in fazle_draft_replies.source) → mark sent. This mirrors the
    _cmd_approve flow's delivery mechanism exactly.
    """
    try:
        row = await fetch_one(
            "SELECT * FROM fazle_draft_replies WHERE id = $1", draft_id
        )
        if not row:
            return f"❌ Draft #{draft_id} পাওয়া যায়নি।"

        recipient = row.get("recipient", "")
        source = row.get("source", "bridge1")

        await execute(
            "UPDATE fazle_draft_replies SET reply_text=$1, status='edited', admin_phone=$2 WHERE id=$3",
            new_text, admin_phone, draft_id,
        )

        # Persist admin-corrected reply for future reuse (non-fatal if it fails)
        try:
            from modules import reviewed_reply_memory as _rrm
            await _rrm.create_or_update_from_edit(
                draft_row=dict(row),
                new_text=new_text,
                admin_phone=admin_phone,
            )
        except Exception as _rrm_err:
            log.debug("[admin_cmd] reviewed_reply_memory store non-fatal: %s", _rrm_err)

        # Send the edited text to the original sender via the SAME bridge
        sent = False
        error_text = ""
        try:
            from app.bridge import get_bridge1, get_bridge2
            bridge = get_bridge1() if source in ("bridge1", "meta") else get_bridge2()
            jid = recipient if "@" in recipient else f"{recipient}@s.whatsapp.net"
            sent = await bridge.send(jid, new_text)
            if sent:
                await execute(
                    "UPDATE fazle_draft_replies SET status='sent', sent_at=NOW() WHERE id=$1",
                    draft_id,
                )
                log.info(f"[admin_cmd] Edited draft #{draft_id} sent to {recipient} via {source}")
            else:
                error_text = "Bridge send returned false"
                await execute(
                    "UPDATE fazle_draft_replies SET error_text=$1 WHERE id=$2",
                    error_text, draft_id,
                )
        except Exception as send_err:
            error_text = str(send_err)[:200]
            log.error(f"[admin_cmd] Edited draft #{draft_id} send error: {send_err}")
            await execute(
                "UPDATE fazle_draft_replies SET error_text=$1 WHERE id=$2",
                error_text, draft_id,
            )

        preview = new_text[:200] if new_text else ""
        if sent:
            return (
                f"✏️ Draft #{draft_id} আপডেট করে পাঠানো হয়েছে।\n\n"
                f"প্রাপক: {recipient}\n"
                f"বার্তা:\n{preview}"
            )
        else:
            return (
                f"✏️ Draft #{draft_id} আপডেট হয়েছে — কিন্তু পাঠাতে সমস্যা হয়েছে।\n"
                f"ত্রুটি: {error_text}\n\n"
                f"প্রাপক: {recipient}\n"
                f"বার্তা:\n{preview}"
            )
    except Exception as e:
        log.error(f"[admin_cmd] edit error: {e}")
        return f"❌ ত্রুটি: {e}"


# ── Human-format draft-reply parser (2026-07-27) ───────────────────────────────

# Maps option keywords/numbers to actions. Case-insensitive.
# Accepted: "1", "approved", "2", "edit", "3", "reject"
_APPROVE_TOKENS = frozenset({"1", "approved", "approve"})
_EDIT_TOKENS    = frozenset({"2", "edit", "edited"})
_REJECT_TOKENS  = frozenset({"3", "reject", "rejected"})


def _parse_draft_reply_option(option_text: str) -> tuple[str, Optional[str]]:
    """Parse the option portion of a "Draft <id> / <option>" reply.

    Returns (action, edited_text) where action is 'approve'|'edit'|'reject'
    and edited_text is only set for 'edit'.

    Accepted formats (case-insensitive):
      "1" / "1." / "1. Approved" / "Approved"   → approve
      "2" / "2." / "2 / <text>" / "Edit / <text>" → edit (with text)
      "3" / "3." / "3. Reject" / "Reject"       → reject
    """
    raw = option_text.strip()
    if not raw:
        return "unknown", None

    # Split on "/" to separate option number from edited text (for edit flow)
    # e.g. "2 / hello world" → option="2", text="hello world"
    # e.g. "Edit / hello world" → option="Edit", text="hello world"
    parts = raw.split("/", 1)
    option_part = parts[0].strip()

    # Strip leading "N." or "N)" numbering like "1. Approved"
    num_prefix = re.match(r"^(\d+)[.)]\s*(.*)$", option_part, re.IGNORECASE)
    if num_prefix:
        option_part = (num_prefix.group(2) or num_prefix.group(1)).strip()

    option_lower = option_part.lower().strip()

    # Check for edit with text: "2 / <text>" or "Edit / <text>"
    if option_lower in _EDIT_TOKENS and len(parts) > 1:
        edited_text = parts[1].strip()
        return "edit", edited_text if edited_text else None

    # Bare option without text
    if option_lower in _APPROVE_TOKENS:
        return "approve", None
    if option_lower in _EDIT_TOKENS:
        # Edit without text — invalid, but return so caller can error
        return "edit", None
    if option_lower in _REJECT_TOKENS:
        return "reject", None

    return "unknown", None


async def _cmd_draft_reply(draft_id: int, option_text: str, admin_phone: str) -> str:
    """Handle human-format "Draft <id> / <option>" admin replies.

    Routes to the existing _cmd_approve / _cmd_edit / _cmd_reject handlers
    so the actual decision logic is never duplicated.
    """
    action, edited_text = _parse_draft_reply_option(option_text)

    if action == "approve":
        return await _cmd_approve(draft_id, admin_phone)

    if action == "reject":
        return await _cmd_reject(draft_id, admin_phone)

    if action == "edit":
        if not edited_text or len(edited_text.strip()) < 2:
            return (
                f"❌ Draft #{draft_id}: Edit করতে নতুন বার্তা লিখুন।\n"
                f"উদাহরণ: Draft {draft_id} / 2 / আপনার নতুন বার্তা এখানে"
            )
        return await _cmd_edit(draft_id, edited_text.strip(), admin_phone)

    return (
        f"❌ Draft #{draft_id}: অবৈধ অপশন।\n"
        f"ব্যবহার: Draft {draft_id} / 1 (Approved) | Draft {draft_id} / 2 / <বার্তা> (Edit) | Draft {draft_id} / 3 (Reject)"
    )


# ── Sprint-3B: Canonical Draft Approval handlers ──────────────────────────────

async def _cmd_approved(
    draft_id: int, amount: float, method: str, admin_phone: str
) -> tuple[str, Optional[str]]:
    """
    Sprint-3B — APPROVED <id> <amount> <method>

    Routes a pending payment draft through the canonical financial pipeline:
        draft_approval.approve_draft()
            → create_transaction()  (canonical, protected)
            → _upsert_ledger()      (called inside create_transaction)
            → audit logged
            → draft status = 'completed'

    Returns (admin_confirm_text, accountant_message_to_forward).
    """
    try:
        from modules.draft_approval import approve_draft
        result = await approve_draft(draft_id, amount, method, admin_phone)
        if not result.get("ok"):
            return (f"❌ {result.get('error', 'unknown error')}", None)

        emp_name = result.get("employee_name") or "?"
        txn_id = result.get("transaction_id")
        txn_ref = result.get("txn_ref", "")[:16]
        accountant_msg = result.get("accountant_msg")

        confirm = (
            f"✅ Draft #{draft_id} → Canonical Transaction #{txn_id}\n"
            f"কর্মী: {emp_name} | পরিমাণ: ৳{amount:,.0f} | পদ্ধতি: {method.upper()}\n"
            f"Txn Ref: {txn_ref}…\n"
            f"Ledger updated ✓ | Audit logged ✓\n\n"
            f"একাউন্ট্যান্টকে পেমেন্ট বার্তা পাঠানো হচ্ছে..."
        )
        return (confirm, accountant_msg)

    except Exception as e:
        log.error(f"[admin_cmd] APPROVED error: {e}")
        return (f"❌ ত্রুটি: {e}", None)


async def _cmd_dredit(
    draft_id: int, amount: float, method: str,
    payout: Optional[str], admin_phone: str,
) -> str:
    """
    Sprint-3B — DREDIT <id> <amount> <method> [payout=<phone>]

    Edits a pending payment draft (version increment, before/after state saved).
    Does NOT create a transaction — admin must APPROVED after edit.
    """
    try:
        from modules.draft_approval import edit_draft
        result = await edit_draft(
            draft_id, amount, method, payout, admin_phone,
            reason=f"DREDIT by {admin_phone}",
        )
        if not result.get("ok"):
            return f"❌ {result.get('error', 'unknown error')}"

        version = result.get("version", 1)
        return (
            f"✏️ Draft #{draft_id} edited (v{version}).\n"
            f"Amount: ৳{amount:,.0f} | Method: {method.upper()}\n"
            f"Now send: APPROVED {draft_id} {int(amount)} {method}"
        )

    except Exception as e:
        log.error(f"[admin_cmd] DREDIT error: {e}")
        return f"❌ ত্রুটি: {e}"


async def _cmd_dreject(
    draft_id: int, admin_phone: str, reason: Optional[str] = None,
) -> str:
    """
    Sprint-3B — DREJECT <id> [reason]

    Rejects a pending payment draft. NO transaction, NO ledger.
    """
    try:
        from modules.draft_approval import reject_draft
        result = await reject_draft(draft_id, admin_phone, reason)
        if not result.get("ok"):
            return f"❌ {result.get('error', 'unknown error')}"

        return (
            f"🚫 Draft #{draft_id} rejected.\n"
            f"Reason: {reason or 'admin_reject'}\n"
            f"No transaction created. No ledger updated."
        )

    except Exception as e:
        log.error(f"[admin_cmd] DREJECT error: {e}")
        return f"❌ ত্রুটি: {e}"


async def _cmd_cash_details(ref: str) -> str:
    """Stage I5 -- CASH DETAILS <txn-ref>. READ-ONLY: never mutates
    anything, the preferred way for an Owner to inspect a transaction
    before deciding APPROVE/REJECT/REVERSE."""
    from modules.financial_operations import cash_details, format_cash_result
    try:
        result = await cash_details(ref)
    except Exception as e:
        log.error(f"[admin_cmd] CASH DETAILS error: {e}")
        return f"❌ ত্রুটি: {e}"
    if not result.get("ok"):
        return format_cash_result("details", result)
    return result["card"]


async def _cmd_proposal_details(proposal_id: int) -> str:
    """Stage J7 -- PROPOSAL DETAILS <id>. Read-only."""
    try:
        from modules.hermes_tasks import get_action
        action = await get_action(proposal_id)
    except Exception as e:
        log.error(f"[admin_cmd] PROPOSAL DETAILS error: {e}")
        return f"❌ ত্রুটি: {e}"
    if not action:
        return f"❌ Proposal #{proposal_id} not found"
    return (
        f"PROPOSAL {proposal_id} — {action.get('domain')}\n"
        f"Reference: {action.get('business_reference')}\n"
        f"Status: {action.get('status')}\n"
        f"Summary: {action.get('summary')}\n"
        f"Expected effect: {action.get('expected_effect')}"
    )


async def _cmd_proposal_approve(proposal_id: int, admin_phone: str) -> str:
    """Stage J2/J7 -- PROPOSAL APPROVE <id>. One Owner approval = one
    execution: this single command both authorizes (approve_action(),
    the existing hermes_tasks gate) AND immediately triggers revalidated
    execution (execute_approved_business_action(), Stage H15/H16 +
    Stage J's proposed_operation contract) -- never a second APPROVE/
    CASH APPROVE required for the same declared operation. If execution
    is refused (stale state, unsupported operation, or the proposal is
    genuinely review-only with no proposed_operation), that is reported
    plainly -- the approval itself still stands (recorded), only the
    subsequent execution attempt failed."""
    from modules.hermes_tasks import approve_action, TaskError
    from modules.business_action_execution import execute_approved_business_action, StaleProposalError, BusinessExecutionError

    try:
        await approve_action(proposal_id, admin_phone)
    except TaskError as e:
        return f"❌ {e}"
    except Exception as e:
        log.error(f"[admin_cmd] PROPOSAL APPROVE error: {e}")
        return f"❌ ত্রুটি: {e}"

    try:
        result = await execute_approved_business_action(proposal_id, executor=admin_phone)
    except StaleProposalError as e:
        return f"✅ Proposal {proposal_id} approved, but execution was refused: {e}"
    except BusinessExecutionError as e:
        return f"✅ Proposal {proposal_id} approved (review-only or not execution-enabled): {e}"
    except Exception as e:
        log.error(f"[admin_cmd] PROPOSAL APPROVE execution error: {e}")
        return f"✅ Proposal {proposal_id} approved, but execution raised an error: {e}"

    if result["result"].get("no_op"):
        return f"✅ Proposal {proposal_id} approved and acknowledged (review-only, no business mutation)."
    return f"✅ Proposal {proposal_id} approved and executed — {result['result']}"


async def _cmd_proposal_reject(proposal_id: int, reason: str, admin_phone: str) -> str:
    """Stage J7/J26 -- PROPOSAL REJECT <id> [reason]. Rejects the
    Hermes proposal only -- never touches the underlying business
    object (structurally proven: reject_action() never references any
    business domain table)."""
    from modules.hermes_tasks import reject_action, TaskError
    try:
        await reject_action(proposal_id, admin_phone, reason)
    except TaskError as e:
        return f"❌ {e}"
    except Exception as e:
        log.error(f"[admin_cmd] PROPOSAL REJECT error: {e}")
        return f"❌ ত্রুটি: {e}"
    return f"🚫 Proposal {proposal_id} rejected. The underlying business record is untouched."


async def _cmd_cash_approve(ref: str, admin_phone: str) -> str:
    """Stage H6 -- CASH APPROVE <txn-ref>. Thin wrapper: RBAC already
    passed (this function is only reached after process_admin_command's
    cash_approve RBAC gate above), delegates the actual resolve/validate/
    execute work entirely to modules.financial_operations (the one
    authoritative adapter -- never duplicated here)."""
    from modules.financial_operations import cash_approve, format_cash_result
    try:
        result = await cash_approve(ref, admin_phone)
    except Exception as e:
        log.error(f"[admin_cmd] CASH APPROVE error: {e}")
        return f"❌ ত্রুটি: {e}"
    return format_cash_result("approve", result)


async def _cmd_cash_reject(ref: str, reason: str, admin_phone: str) -> str:
    """Stage H7 -- CASH REJECT <txn-ref> <reason>."""
    from modules.financial_operations import cash_reject, format_cash_result
    try:
        result = await cash_reject(ref, reason, admin_phone)
    except Exception as e:
        log.error(f"[admin_cmd] CASH REJECT error: {e}")
        return f"❌ ত্রুটি: {e}"
    return format_cash_result("reject", result)


async def _cmd_cash_reverse(ref: str, reason: str, admin_phone: str) -> str:
    """Stage H8 (highest risk) -- CASH REVERSE <txn-ref> <reason>. NO
    standing autonomous reverse: this is only ever invoked from an
    explicit, this-transaction-specific admin command with a reason --
    there is no scheduled job, no auto-trigger, nothing else in this repo
    that calls modules.financial_operations.cash_reverse()."""
    from modules.financial_operations import cash_reverse, format_cash_result
    try:
        result = await cash_reverse(ref, reason, admin_phone)
    except Exception as e:
        log.error(f"[admin_cmd] CASH REVERSE error: {e}")
        return f"❌ ত্রুটি: {e}"
    return format_cash_result("reverse", result)


async def _cmd_paid(
    draft_id: int, amount: float, method: str, admin_phone: str, draft_type: str
) -> tuple[str, Optional[str]]:
    """
    Approve payment draft, write ledger entry, build accountant message.
    Returns (admin_confirm_text, accountant_message).
    """
    try:
        row = await fetch_one(
            "SELECT * FROM fazle_payment_drafts WHERE id = $1", draft_id
        )
        if not row:
            return (f"❌ Payment draft #{draft_id} পাওয়া যায়নি।", None)

        emp_name   = row.get("employee_name") or "?"

        # ── C1B: Write to fpe_cash_transactions via payment_workflow.finalize_payment ──
        from modules.payment_workflow import finalize_payment
        result = await finalize_payment(draft_id, amount, method)
        if "error" in result:
            log.error(f"[admin_cmd] finalize_payment failed: {result['error']}")
            # Continue — still update draft and notify accountant

        # Update draft status and record admin
        await execute(
            """UPDATE fazle_payment_drafts
               SET status='approved', approved_amount=$1, payment_method=$2,
                   admin_phone=$3, updated_at=NOW()
               WHERE id=$4""",
            amount, method, admin_phone, draft_id,
        )

        accountant_msg = result.get("accountant_msg") or (
            f"💳 পেমেন্ট নির্দেশনা:\n\n"
            f"কর্মী: {emp_name}\n"
            f"মোবাইল: {row.get('employee_mobile','?')}\n"
            f"পরিমাণ: ৳{amount:,.0f}\n"
            f"পদ্ধতি: {method.upper()}\n"
            f"ধরন: {'অগ্রিম' if draft_type == 'advance' else 'এস্কর্ট ডিউটি পেমেন্ট'}\n"
            f"Draft ID: #{draft_id}"
        )

        # Notify employee directly via bridge
        emp_phone = row.get("employee_mobile", "")
        bridge_src = row.get("source", "bridge1")
        if emp_phone:
            try:
                from app.bridge import get_bridge1, get_bridge2
                emp_bridge = get_bridge1() if bridge_src == "bridge1" else get_bridge2()
                emp_jid = emp_phone if "@" in emp_phone else f"{emp_phone}@s.whatsapp.net"
                emp_msg = (
                    f"✅ আপনার {'অগ্রিম' if draft_type == 'advance' else 'পেমেন্ট'} অনুরোধ অনুমোদিত হয়েছে।\n"
                    f"পরিমাণ: ৳{amount:,.0f} ({method.upper()})\n"
                    f"একাউন্ট্যান্ট শীঘ্রই যোগাযোগ করবেন।"
                )
                await emp_bridge.send(emp_jid, emp_msg)
                log.info(f"[admin_cmd] payment confirmation sent to employee {emp_phone}")
            except Exception as ne:
                log.warning(f"[admin_cmd] employee payment notify error: {ne}")

        confirm = (
            f"✅ Payment #{draft_id} অনুমোদিত ও লেজারে রেকর্ড হয়েছে।\n"
            f"কর্মী: {emp_name} | পরিমাণ: ৳{amount:,.0f} | পদ্ধতি: {method.upper()}\n\n"
            f"একাউন্ট্যান্টকে পেমেন্ট বার্তা পাঠানো হচ্ছে..."
        )
        return (confirm, accountant_msg)

    except Exception as e:
        log.error(f"[admin_cmd] paid error: {e}")
        return (f"❌ ত্রুটি: {e}", None)


async def _cmd_escort_confirm(
    program_id: int,
    escort_name: str,
    escort_mobile: str,
    date_str: str,
    shift: str,
    admin_phone: str,
) -> tuple[str, Optional[str]]:
    """
    Handle ESCORTCONFIRM <id> | <name> | <mobile> | <date> | <D/N>
    Loads draft escort program, fills escort details, sends slip to buyer,
    auto-creates employee record if escort not in DB.
    Returns (admin_reply, buyer_message_to_forward).
    """
    try:
        from app.database import fetch_one as _fo, execute as _ex, fetch_val as _fv
        from datetime import date as _date, datetime as _dt

        program = await _fo(
            "SELECT * FROM wbom_escort_programs WHERE program_id = $1", program_id
        )
        if not program:
            return (f"❌ Escort program #{program_id} পাওয়া যায়নি।", None)

        # Resolve (or auto-create) the wbom_employees row for this escort and
        # link it via escort_employee_id. Without this, find_active_program_
        # for_employee() (modules/escort_lifecycle) can never find this
        # program for the guard's own attendance/release messages, since it
        # filters strictly on escort_employee_id, not the JSONB remarks blob.
        from modules.escort import _resolve_escort_employee_id
        emp_id = await _resolve_escort_employee_id(escort_mobile, escort_name, auto_create=True)

        # Update program with escort details
        await _ex(
            """UPDATE wbom_escort_programs
               SET status = 'confirmed', shift = $2,
                   escort_employee_id = COALESCE($6, escort_employee_id),
                   remarks = remarks::jsonb || jsonb_build_object(
                       'escort_name', $3::text,
                       'escort_mobile', $4::text,
                       'confirmed_by', $5::text
                   )
               WHERE program_id = $1""",
            program_id, shift, escort_name, escort_mobile, admin_phone, emp_id,
        )

        # Build final slip
        mv = program.get("mother_vessel", "—")
        lv = program.get("lighter_vessel", "—")
        master_mob = program.get("master_mobile", "—")

        slip = (
            f"Mother Vessel: {mv}\n"
            f"Lighter: {lv}\n"
            f"Master Number: {master_mob}\n"
            f"Escort Name: {escort_name}\n"
            f"Escort Mobile: {escort_mobile}\n"
            f"{date_str} ({shift})\n"
            f"{get_settings().company_name}"
        )

        # Get buyer phone from remarks JSON
        import json
        try:
            remarks = json.loads(program.get("remarks") or "{}")
        except Exception:
            remarks = {}
        buyer_phone = remarks.get("sender_phone")

        admin_reply = (
            f"✅ Escort #{program_id} নিশ্চিত হয়েছে।\n"
            f"Escort: {escort_name} | {escort_mobile}\n"
            f"Shift: {shift}\n\n"
            f"📋 Slip:\n{slip}"
        )

        # Determine which bridge the buyer used (stored in remarks as source_bridge)
        buyer_bridge_src = remarks.get("source_bridge", "bridge2")

        if buyer_phone:
            try:
                from app.bridge import get_bridge1, get_bridge2
                buyer_bridge = get_bridge1() if buyer_bridge_src == "bridge1" else get_bridge2()
                buyer_jid = buyer_phone if "@" in buyer_phone else f"{buyer_phone}@s.whatsapp.net"
                await buyer_bridge.send(buyer_jid, slip)
                admin_reply += f"\n\n✅ ক্রেতা ({buyer_phone}) কে স্লিপ পাঠানো হয়েছে।"
            except Exception as be:
                log.error(f"[admin_cmd] escort slip send to buyer failed: {be}")
                admin_reply += f"\n\n⚠️ ক্রেতার ({buyer_phone}) কাছে পাঠাতে সমস্যা হয়েছে।"
            return (admin_reply, None)

        admin_reply += "\n\n⚠️ ক্রেতার নম্বর পাওয়া যায়নি — নিজে পাঠিয়ে দিন।"
        return (admin_reply, None)

    except Exception as e:
        log.error(f"[admin_cmd] escort_confirm error: {e}")
        return (f"❌ ত্রুটি: {e}", None)


async def _cmd_status() -> str:
    """Stage I20: STATUS now answers "what needs my approval?" across the
    whole business, not just drafts -- reuses modules.reports.
    build_owner_overview(), itself built entirely from
    _b_business_communication_summary()'s already-computed data (no
    second reporting engine, no new queries). Falls back to the old
    drafts-only view if the overview builder itself fails, so STATUS
    never goes fully silent on an unrelated report-layer error."""
    try:
        from modules.reports import build_owner_overview, render_owner_overview
        overview = await build_owner_overview(hours=24)
        return render_owner_overview(overview)
    except Exception as e:
        log.error(f"[admin_cmd] STATUS overview failed, falling back to drafts-only: {e}")
        try:
            draft_count = await fetch_val(
                "SELECT COUNT(*) FROM fazle_draft_replies WHERE COALESCE(status,'pending') = 'pending'"
            )
            pay_count = await fetch_val(
                "SELECT COUNT(*) FROM fazle_payment_drafts WHERE status='pending'"
            )
            rows = await fetch_all(
                "SELECT id, recipient, intent, LEFT(reply_text,60) AS preview FROM fazle_draft_replies "
                "WHERE COALESCE(status,'pending') = 'pending' ORDER BY created_at DESC LIMIT 5"
            )
            lines = [f"📊 পেন্ডিং ড্রাফট: {draft_count} | পেমেন্ট ড্রাফট: {pay_count}\n"]
            for r in rows:
                lines.append(f"#{r['id']} [{r.get('intent','?')}] → {r.get('recipient','?')}: {r.get('preview','')!r}")
            return "\n".join(lines)
        except Exception as e2:
            return f"❌ স্ট্যাটাস লোড ব্যর্থ: {e2}"


async def _send_attendance_confirmation(result: dict, source: str) -> None:
    """
    Send the "attendance confirmed" message to the original sender, after
    modules.attendance.finalize_attendance_draft() has already committed the
    attendance row. Never called if finalization failed (see _cmd_approve
    and modules.drafts.routes.approve_draft) -- this replaces the old
    _save_attendance_from_draft's inline confirmation send, now shared by
    both approval channels via the one finalize_attendance_draft() result.
    """
    shift_label = "Day" if result.get("shift") == "D" else "Night"
    att_date = result.get("attendance_date")
    date_str = att_date.strftime("%d/%m/%Y") if hasattr(att_date, "strftime") else str(att_date or "")
    confirm_msg = (
        f"✅ হাজিরা নিশ্চিত হয়েছে:\n"
        f"কর্মী: {result.get('employee_name') or result.get('employee_id')}\n"
        f"তারিখ: {date_str}\n"
        f"শিফট: {shift_label}"
    )
    sender_phone = result.get("recipient") or ""
    if not sender_phone:
        return
    try:
        from app.bridge import get_bridge1, get_bridge2
        bridge = get_bridge1() if source in ("bridge1", "meta") else get_bridge2()
        jid = sender_phone if "@" in sender_phone else f"{sender_phone}@s.whatsapp.net"
        await bridge.send(jid, confirm_msg)
        log.info(f"[admin_cmd] attendance confirmation sent to {sender_phone}")
    except Exception as se:
        log.warning(f"[admin_cmd] attendance confirmation send error: {se}")


async def list_payment_drafts(admin_phone: str) -> str:
    """List pending payment drafts for admin."""
    try:
        rows = await fetch_all(
            "SELECT id, employee_name, employee_mobile, expected_amount, payment_method, draft_type "
            "FROM fazle_payment_drafts WHERE status='pending' ORDER BY created_at DESC LIMIT 10"
        )
        if not rows:
            return "✅ কোনো পেন্ডিং পেমেন্ট ড্রাফট নেই।"
        lines = ["💳 পেন্ডিং পেমেন্ট ড্রাফটসমূহ:\n"]
        for r in rows:
            dtype = 'অগ্রিম' if r.get('draft_type') == 'advance' else 'এস্কর্ট পেমেন্ট'
            lines.append(
                f"#{r['id']} {r.get('employee_name','?')} "
                f"| ৳{r.get('expected_amount') or 0:,.0f} "
                f"| {dtype}"
            )
        lines.append("\nঅনুমোদন দিতে: PAID <id> <amount> bkash/nagad/cash")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ ত্রুটি: {e}"


# ── PAY-IMPORT (Batch 12: payment SMS reconciliation) ─────────────────────────

async def _cmd_pay_import(raw_text: str, admin_phone: str) -> str:
    """Admin pastes a bKash/Nagad SMS or free-form line. Ingest, match, preview."""
    from modules.payment_ingest import ingest_payment_sms
    try:
        res = await ingest_payment_sms(raw_text, sender_number=admin_phone, auto_finalize=False)
    except Exception as e:
        log.error(f"[admin] pay-import error: {e}")
        return f"❌ পেমেন্ট ইমপোর্ট ত্রুটি: {e}"

    if not res.get("ok"):
        return f"❌ পার্স করা গেল না।\nReason: {res.get('reason','unknown')}"

    status = res.get("status")
    sid = res.get("staging_id")
    eid = res.get("matched_employee_id")
    ratio = res.get("name_match_ratio") or 0
    amt = res.get("amount", 0)
    method = (res.get("method") or "").upper()
    mob = res.get("mobile") or "?"
    name = res.get("name") or "(no name)"

    head = f"💳 PAY-IMPORT #{sid} — {status.upper()}"
    body = (
        f"\nMethod: {method}\nAmount: ৳{amt:,.0f}\nMobile: {mob}\n"
        f"Name (raw): {name}\nMatch eid={eid} ratio={ratio:.2f} type={res.get('match_type')}"
    )
    if status == "auto_approved":
        fin = res.get("finalized") or {}
        body += f"\n\n✅ Auto-finalized\nDraft #{fin.get('draft_id')} TxnID {fin.get('transaction_id')}"
        if fin.get("accountant_msg"):
            body += f"\n\n--- ACCOUNTANT MSG ---\n{fin['accountant_msg']}"
    elif status == "duplicate":
        body += f"\n\n⚠️ Duplicate of staging #{res.get('duplicate_of')}"
    elif status == "unmatched":
        body += "\n\n⚠️ কোনো কর্মী মিল পাওয়া যায়নি — manually approve via PAID <draft_id> <amount> <method>"
    else:
        body += "\n\n⏳ Pending review (low confidence). Use PAID/REJECT once draft created."
    return head + body


# ── RELEASE (Batch 13: escort lifecycle manual closure) ───────────────────────

async def _cmd_release(
    program_id: int,
    end_date_str: str,
    end_shift: str,
    release_point: str,
    days: float | None,
    admin_phone: str,
) -> str:
    return (
        "❌ RELEASE command no longer finalizes financial state.\n"
        "Review the release and send an exact [RELEASE CONFIRMED] message "
        "containing End Date, Shift, Days, Food, Conveyance, Escort and Lighter."
    )


# ── PAYROLL handlers (Batch 14) ────────────────────────────────────────────────

def _fmt_money(x) -> str:
    try:
        return f"৳{float(x):,.0f}"
    except Exception:
        return str(x)


async def _cmd_payroll_compute(year: int, month: int, employee_id, admin_phone: str) -> str:
    from modules.payroll import compute_run, compute_all_for_period
    if employee_id:
        r = await compute_run(int(employee_id), year, month, f"admin:{admin_phone}")
        if not r.get("ok"):
            return f"❌ Compute ব্যর্থ: {r.get('error')}"
        if r.get("already_exists"):
            return (f"ℹ️ Run আগেই আছে #{r['run_id']} "
                    f"({year}-{month:02d}, status={r['status']})\n"
                    f"Net: {_fmt_money(r['net_salary'])}")
        return (f"✅ Run #{r['run_id']} তৈরি ({year}-{month:02d})\n"
                f"কর্মী: {r.get('employee_name')} | প্রোগ্রাম: {r['total_programs']} "
                f"({r['total_days']:.1f} দিন)\n"
                f"Basic: {_fmt_money(r['basic_salary'])} | Programs: {_fmt_money(r['program_allowance'])}\n"
                f"Advance: {_fmt_money(r['total_advances'])}\n"
                f"নেট: {_fmt_money(r['net_salary'])}\n"
                f"পরবর্তী: PAYROLL SUBMIT {r['run_id']}")
    r = await compute_all_for_period(year, month, f"admin:{admin_phone}")
    return (f"✅ Bulk compute {year}-{month:02d}\n"
            f"নতুন: {r['created']} | আগে ছিল: {r['existing']} | ব্যর্থ: {r['failed']} "
            f"| মোট কর্মী: {r['total']}")


async def _cmd_payroll_transition(action: str, run_id: int, admin_phone: str) -> str:
    from modules.payroll import submit_run, approve_run, lock_run
    actor = f"admin:{admin_phone}"
    if action == "submit":
        r = await submit_run(run_id, actor)
    elif action == "approve":
        r = await approve_run(run_id, actor)
    elif action == "lock":
        r = await lock_run(run_id, actor)
    else:
        return "❌ অজানা action"
    if not r.get("ok"):
        return f"❌ {r.get('error')}"
    return (f"✅ Run #{run_id}: {r['from_status']} → {r['to_status']}\n"
            f"নেট: {_fmt_money(r['net_salary'])}")


async def _cmd_payroll_paid(run_id: int, amount: float, method: str,
                              ref, admin_phone: str) -> str:
    from modules.payroll import mark_paid
    r = await mark_paid(run_id, f"admin:{admin_phone}", amount, method, ref)
    if not r.get("ok"):
        return f"❌ {r.get('error')}"
    return (f"✅ Run #{run_id} PAID {_fmt_money(amount)} via {method}"
            f"{(' ref=' + ref) if ref else ''}\n"
            f"({r['from_status']} → paid)")


async def _cmd_payroll_cancel(run_id: int, reason: str, admin_phone: str) -> str:
    from modules.payroll import cancel_run
    r = await cancel_run(run_id, f"admin:{admin_phone}", reason)
    if not r.get("ok"):
        return f"❌ {r.get('error')}"
    return f"✅ Run #{run_id} CANCELLED ({r['from_status']} → cancelled)\nReason: {reason}"


async def _cmd_payroll_list(year: int, month: int, status) -> str:
    from modules.payroll import list_runs
    rows = await list_runs(year, month, status)
    if not rows:
        flt = f" status={status}" if status else ""
        return f"📭 কোনো run নেই {year}-{month:02d}{flt}"
    lines = [f"📋 Payroll {year}-{month:02d}" + (f" ({status})" if status else "") + f" — {len(rows)}টি:"]
    for r in rows[:25]:
        lines.append(f"#{r['run_id']} {r['employee_name']} | {r['status']} | "
                     f"net: {_fmt_money(r['net_salary'])} | prog: {r['total_programs']}")
    if len(rows) > 25:
        lines.append(f"… আরও {len(rows)-25}টি")
    return "\n".join(lines)


# ── SCHEDULER (Batch 16) ──────────────────────────────────────────────────────
async def _cmd_schedule_status() -> str:
    from modules import scheduler as sch
    s = await sch.get_status()
    if not s.get("enabled"):
        return "⏰ সময়সূচি বন্ধ আছে (SCHEDULER_ENABLED=false)"
    jobs = s.get("jobs", [])
    if not jobs:
        return "⏰ কোনো job রেজিস্টার্ড নেই"
    lines = [f"⏰ সময়সূচি ({s.get('tz')}) — {len(jobs)}টি job:"]
    for j in jobs:
        nxt = (j["next_run_at"] or "—")[:19].replace("T", " ")
        last = (j["last_run_at"] or "—")[:19].replace("T", " ")
        st = j["last_status"] or "—"
        emoji = "✅" if st == "ok" else ("❌" if st == "error" else "⏳")
        lines.append(f"{emoji} {j['job_name']} | last: {last} ({st}) | next: {nxt} | runs: {j['run_count']}")
    return "\n".join(lines)


async def _cmd_schedule_run(job_name: str, admin_phone: str) -> str:
    from modules import scheduler as sch
    if job_name not in sch.list_job_names():
        avail = ", ".join(sch.list_job_names())
        return f"❌ অজানা job: {job_name}\nউপলব্ধ: {avail}"
    log.info(f"[admin_cmd] manual job trigger: {job_name} by {admin_phone}")
    result = await sch.trigger_job(job_name)
    status = result.get("status", "?")
    emoji = "✅" if status == "ok" else "❌"
    extras = []
    for k in ("matched", "unmatched", "scanned", "stale", "alerted", "dlq", "pending", "newest_age_h", "overall"):
        if k in result:
            extras.append(f"{k}={result[k]}")
    detail = " | ".join(extras) if extras else (result.get("error") or "done")
    return f"{emoji} job '{job_name}' → {status}\n{detail}"


# ── REPORTS (Batch 17) ────────────────────────────────────────────────────────
async def _cmd_report_list() -> str:
    from modules import reports as r
    names = r.list_reports()
    lines = ["📊 উপলব্ধ রিপোর্ট:"]
    for n in names:
        lines.append(f"  • {n}")
    lines.append("")
    lines.append("ব্যবহার:")
    lines.append("  report daily [YYYY-MM-DD]")
    lines.append("  report payroll YYYY-MM")
    lines.append("  report cash [days]")
    lines.append("  report recon [days]")
    lines.append("  report escort YYYY-MM-DD YYYY-MM-DD")
    return "\n".join(lines)


async def _cmd_report_daily(date_str, admin_phone: str) -> str:
    from modules import reports as r
    args = {"date": date_str} if date_str else {}
    try:
        payload = await r.run_report("daily_summary", args, requested_by=admin_phone)
    except Exception as e:
        return f"❌ রিপোর্ট ব্যর্থ: {e}"
    return r.render_text(payload, max_rows=20)


async def _cmd_report_payroll(year: int, month: int, admin_phone: str) -> str:
    from modules import reports as r
    try:
        payload = await r.run_report(
            "monthly_payroll", {"year": year, "month": month}, requested_by=admin_phone,
        )
    except Exception as e:
        return f"❌ রিপোর্ট ব্যর্থ: {e}"
    return r.render_text(payload, max_rows=25)


async def _cmd_report_cash(days: int, admin_phone: str) -> str:
    from modules import reports as r
    try:
        payload = await r.run_report(
            "cash_position", {"days": days}, requested_by=admin_phone,
        )
    except Exception as e:
        return f"❌ রিপোর্ট ব্যর্থ: {e}"
    return r.render_text(payload, max_rows=20)


async def _cmd_report_recon(days: int, admin_phone: str) -> str:
    from modules import reports as r
    try:
        payload = await r.run_report(
            "payment_reconciliation", {"days": days}, requested_by=admin_phone,
        )
    except Exception as e:
        return f"❌ রিপোর্ট ব্যর্থ: {e}"
    return r.render_text(payload, max_rows=20)


async def _cmd_report_escort(start: str, end: str, admin_phone: str) -> str:
    from modules import reports as r
    try:
        payload = await r.run_report(
            "escort_utilization", {"start": start, "end": end}, requested_by=admin_phone,
        )
    except Exception as e:
        return f"❌ রিপোর্ট ব্যর্থ: {e}"
    return r.render_text(payload, max_rows=30)


# ── BACKUP HANDLERS (Batch 18) ────────────────────────────────────────────────
async def _cmd_backup_status() -> str:
    from modules import backup as b
    s = await b.backup_status()
    return b.render_status_text(s)


async def _cmd_backup_now(admin_phone: str) -> str:
    from modules import backup as b
    res = await b.run_backup()
    if res.get("status") != "ok":
        return f"❌ ব্যাকআপ ব্যর্থ: {str(res.get('error', ''))[:200]}"
    rot = await b.rotate_backups()
    sz_mb = (res.get("size_bytes") or 0) / (1024 * 1024)
    return (
        "✅ ব্যাকআপ সম্পন্ন\n"
        "────────────────\n"
        f"• ফাইল: {res['filename']}\n"
        f"• আকার: {sz_mb:.2f} MB\n"
        f"• সময়: {res['duration_ms']} ms\n"
        f"• rotation: kept={rot['kept']} deleted={rot['deleted']}"
    )


async def _cmd_backup_list(limit: int = 10) -> str:
    from modules import backup as b
    rows = await b.list_backups(limit=limit)
    if not rows:
        return "📦 কোনো ব্যাকআপ নেই"
    lines = ["📦 ব্যাকআপ তালিকা", "─" * 16]
    for r in rows:
        sz_mb = (r.get("size_bytes") or 0) / (1024 * 1024)
        ts = r["started_at"].strftime("%Y-%m-%d %H:%M") if r.get("started_at") else "?"
        lines.append(f"• {ts}  [{r['status']}]  {sz_mb:.1f}MB  {r['filename']}")
    return "\n".join(lines)


# ── Batch 19 — user mgmt command handlers ─────────────────────────────────────
async def _cmd_user_list() -> str:
    from modules import rbac
    rows = await rbac.list_admins()
    if not rows:
        return "👥 কোনো admin user নেই।"
    lines = [f"👥 Admin Users ({len(rows)}):"]
    for r in rows:
        lines.append(
            f"• #{r['id']} {r['phone']}  {r['name']}  "
            f"[{r['status']}] roles={r['roles'] or '-'}"
        )
    return "\n".join(lines)


async def _cmd_user_add(phone: str, name: str, role: str, admin_phone: str) -> str:
    from modules import rbac
    try:
        res = await rbac.add_admin(phone, name, role=role, granted_by=admin_phone)
    except ValueError as e:
        return f"❌ {e}"
    if res["status"] == "exists":
        return f"⚠️ {phone} আগেই আছে (id={res['admin_id']})।"
    return f"✅ User যোগ হয়েছে: id={res['admin_id']} {phone} ({name}) role={role}"


async def _cmd_user_role(phone: str, role: str, admin_phone: str) -> str:
    from modules import rbac
    try:
        res = await rbac.set_role(phone, role, granted_by=admin_phone)
    except ValueError as e:
        return f"❌ {e}"
    return f"✅ Role যোগ: {phone} → {role}"


async def _cmd_user_remove(phone: str, admin_phone: str) -> str:
    from modules import rbac
    try:
        res = await rbac.disable_admin(phone)
    except ValueError as e:
        return f"❌ {e}"
    return f"✅ User নিষ্ক্রিয়: {phone} (id={res['admin_id']})"


async def _cmd_user_apikey(phone: str, admin_phone: str) -> str:
    from modules import rbac
    try:
        res = await rbac.issue_api_key(phone)
    except ValueError as e:
        return f"❌ {e}"
    return (
        f"🔑 API key issued for {phone} (id={res['admin_id']}):\n"
        f"`{res['api_key']}`\n"
        f"⚠️ এই key এখনই save করুন — আবার দেখানো হবে না।"
    )
