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
# REVERSE <transaction_id> <reason…>
_REVERSE_RE = re.compile(r"^reverse\s+\d+.*$", re.IGNORECASE)
# ADJUST <draft_id> <amount> <method>
_ADJUST_RE = re.compile(r"^adjust\s+\d+.*$", re.IGNORECASE)


def is_admin_command(text: str) -> bool:
    """Quick check if message is an admin command."""
    t = text.strip()
    return bool(
        _APPROVE_RE.match(t) or _REJECT_RE.match(t) or _EDIT_RE.match(t) or
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
        _APPROVED_RE.match(t) or _DREDIT_RE.match(t) or _DREJECT_RE.match(t)
    )


def _classify_command(t: str) -> Optional[str]:
    """Map text → COMMAND_ROLE key (used by RBAC). Returns None if not admin cmd."""
    pairs = (
        (_APPROVE_RE, "approve"), (_REJECT_RE, "reject"), (_EDIT_RE, "edit"),
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

    if _dedup_seen(t, admin_phone):
        try:
            from modules import observability as _obs
            _obs.inc("admin_command_dedup_total")
        except Exception:
            pass
        log.info(f"[admin_cmd] dedup_suppressed admin={admin_phone} text={t[:60]!r}")
        return ""  # silent drop, no double-process

    # ── Batch 19 — RBAC guard + audit ────────────────────────────────────────
    cmd_name = _classify_command(t)
    if cmd_name is not None:
        try:
            from modules import rbac
            perm = await rbac.check_permission(phone=admin_phone, command=cmd_name)
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
        except Exception as e:
            log.warning(f"[admin_cmd] rbac check failed (failing open for now): {e}")
    # ─────────────────────────────────────────────────────────────────────────

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

    return (
        "❌ কমান্ড বুঝিনি।\n\n"
        "ব্যবহার:\n"
        "  APPROVE <id>            — ড্রাফট পাঠান\n"
        "  APPROVE <id> <id> ...   — একসাথে একাধিক\n"
        "  REJECT <id>             — বাতিল\n"
        "  EDIT <id> <নতুন বার্তা>\n"
        "  PAID <id> <amount> <method>\n"
        "  STATUS / DRAFTS         — পেন্ডিং তালিকা\n\n"
        "বাংলা সংখ্যাও কাজ করে: APPROVE ১৬৫"
    )


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

        # Mark approved first
        await execute(
            "UPDATE fazle_draft_replies SET status='approved', admin_phone=$1, approved_at=NOW() WHERE id=$2",
            admin_phone, draft_id,
        )

        # Attendance draft: write to wbom_attendance + send confirmation to sender
        if intent == "attendance":
            await _save_attendance_from_draft(draft_id, reply_text, recipient, admin_phone, source)

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
    """Edit draft reply text."""
    try:
        row = await fetch_one(
            "SELECT * FROM fazle_draft_replies WHERE id = $1", draft_id
        )
        if not row:
            return f"❌ Draft #{draft_id} পাওয়া যায়নি।"

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

        return f"✏️ Draft #{draft_id} আপডেট করা হয়েছে।\n\nনতুন বার্তা:\n{new_text[:300]}"
    except Exception as e:
        log.error(f"[admin_cmd] edit error: {e}")
        return f"❌ ত্রুটি: {e}"


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

        # Update program with escort details
        await _ex(
            """UPDATE wbom_escort_programs
               SET status = 'confirmed', shift = $2,
                   remarks = remarks::jsonb || jsonb_build_object(
                       'escort_name', $3::text,
                       'escort_mobile', $4::text,
                       'confirmed_by', $5::text
                   )
               WHERE program_id = $1""",
            program_id, shift, escort_name, escort_mobile, admin_phone,
        )

        # Auto-create employee if escort mobile not in DB
        escort_norm = escort_mobile.replace("+", "").replace("-", "").strip()
        existing = await _fo(
            "SELECT employee_id FROM wbom_employees WHERE employee_mobile = $1",
            escort_norm,
        )
        if not existing:
            try:
                await _ex(
                    """INSERT INTO wbom_employees
                           (employee_name, employee_mobile, designation, status, joining_date)
                       VALUES ($1, $2, 'Escort', 'Active', CURRENT_DATE)""",
                    escort_name, escort_norm,
                )
                log.info(f"[admin_cmd] auto-created escort employee: {escort_name} ({escort_norm})")
            except Exception as emp_err:
                log.warning(f"[admin_cmd] escort employee auto-create failed: {emp_err}")

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
            f"Al-Aqsa Security Service"
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
    """Return pending drafts count."""
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
    except Exception as e:
        return f"❌ স্ট্যাটাস লোড ব্যর্থ: {e}"


async def _save_attendance_from_draft(
    draft_id: int,
    draft_text: str,
    sender_phone: str,
    recorded_by: str,
    source: str = "bridge1",
):
    """
    Save attendance to wbom_attendance using draft meta JSON.
    Auto-creates employee if not found. Sends confirmation to sender.
    """
    import json as _json
    from datetime import date as _date
    from modules.attendance import save_attendance

    try:
        # Load meta from DB (preferred over text parsing)
        row = await fetch_one(
            "SELECT meta, source FROM fazle_draft_replies WHERE id = $1", draft_id
        )
        meta: dict = {}
        if row and row.get("meta"):
            try:
                meta = dict(row["meta"]) if isinstance(row["meta"], dict) else _json.loads(row["meta"])
            except Exception as _e:
                from app.error_log import record_error
                await record_error("admin_commands.meta_parse", _e)
        if row and row.get("source"):
            source = row["source"]

        emp_id       = meta.get("employee_id")
        att_date_str = meta.get("att_date")
        shift        = meta.get("shift", "D")
        emp_name     = meta.get("employee_name") or ""
        emp_mobile   = meta.get("employee_mobile") or ""

        # ── Auto-create employee if not found ──────────────────────────────────
        if not emp_id:
            # Try one more DB lookup
            emp = None
            if emp_mobile:
                emp = await fetch_one(
                    "SELECT employee_id, employee_name FROM wbom_employees WHERE employee_mobile=$1",
                    emp_mobile,
                )
            if not emp and emp_name:
                emp = await fetch_one(
                    "SELECT employee_id, employee_name FROM wbom_employees "
                    "WHERE LOWER(employee_name) LIKE LOWER($1) AND status='Active' LIMIT 1",
                    f"%{emp_name.split()[0]}%",
                )
            if emp:
                emp_id   = emp["employee_id"]
                emp_name = emp["employee_name"]
            elif emp_name or emp_mobile:
                # Auto-create minimal employee record
                try:
                    emp_id = await fetch_val(
                        """INSERT INTO wbom_employees
                               (employee_name, employee_mobile, designation, status, joining_date)
                           VALUES ($1, $2, 'Staff', 'Active', CURRENT_DATE)
                           RETURNING employee_id""",
                        emp_name or "Unknown", emp_mobile or None,
                    )
                    log.info(f"[admin_cmd] auto-created employee: {emp_name} ({emp_mobile}), id={emp_id}")
                except Exception as ce:
                    log.warning(f"[admin_cmd] employee auto-create failed: {ce}")

        if not emp_id:
            log.warning(f"[admin_cmd] attendance draft #{draft_id}: cannot resolve employee — skipping DB save")
            return

        if not att_date_str:
            att_date = _date.today()
        else:
            try:
                att_date = _date.fromisoformat(att_date_str)
            except Exception:
                att_date = _date.today()

        # ── Save to wbom_attendance ────────────────────────────────────────────
        ok = await save_attendance(
            employee_id=emp_id,
            attendance_date=att_date,
            status="Present",
            location="",
            recorded_by=recorded_by,
            remarks=f"Shift:{shift} — Approved from draft #{draft_id}",
        )
        if ok:
            log.info(f"[admin_cmd] attendance draft #{draft_id} → emp={emp_id} date={att_date} shift={shift}")

        # ── Send confirmation to original sender ───────────────────────────────
        shift_label = "Day" if shift == "D" else "Night"
        confirm_msg = (
            f"✅ হাজিরা নিশ্চিত হয়েছে:\n"
            f"কর্মী: {emp_name or emp_id}\n"
            f"তারিখ: {att_date.strftime('%d/%m/%Y')}\n"
            f"শিফট: {shift_label}"
        )
        try:
            from app.bridge import get_bridge1, get_bridge2
            bridge = get_bridge1() if source in ("bridge1", "meta") else get_bridge2()
            jid = sender_phone if "@" in sender_phone else f"{sender_phone}@s.whatsapp.net"
            await bridge.send(jid, confirm_msg)
            log.info(f"[admin_cmd] attendance confirmation sent to {sender_phone}")
        except Exception as se:
            log.warning(f"[admin_cmd] attendance confirmation send error: {se}")

    except Exception as e:
        log.error(f"[admin_cmd] attendance save error for draft #{draft_id}: {e}")


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
