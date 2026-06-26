"""
Fazle Core — Payment Ingest (Batch 12)

Pipeline:
  raw bKash / Nagad SMS or admin paste
    → parse_payment_sms()  → {amount, mobile, name?, method, trxid?}
    → match_employee()     → (employee_id, ratio, match_type)
    → ingest_payment_sms() → INSERT wbom_staging_payments (status=pending|unmatched)

  Admin → Accountant final instruction
    → parse_admin_cash_shorthand()
    → match/create employee by ID: mobile if present, otherwise payout mobile
    → INSERT wbom_cash_transactions directly (no draft)
       on (mobile exact OR name_ratio>=0.92) AND amount > 0

Idempotency:
  idempotency_key = sha256(method|trxid|amount|mobile|date)  — unique per real payment
  duplicates return existing staging row.

Logs every parse/match/finalize step with [pay-ingest] prefix.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import date
from typing import Optional, Tuple

from rapidfuzz import fuzz, process as rf_process

from app.database import fetch_one, fetch_all, execute, fetch_val
from modules.payment_workflow import finalize_payment

log = logging.getLogger("fazle.pay_ingest")

AUTO_APPROVE_NAME_RATIO = 0.92
AUTO_APPROVE_MIN_AMOUNT = 100         # ignore tiny test transfers
AUTO_APPROVE_MAX_AMOUNT = 50000       # safety cap; above this stays pending

# ── Phone normalization ───────────────────────────────────────────────────────

_DIGIT_RE = re.compile(r"\D+")


def _norm_mobile(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    d = _DIGIT_RE.sub("", str(raw))
    if not d:
        return None
    # Bangladesh: keep last 11 digits as canonical (01XXXXXXXXX)
    if len(d) >= 11:
        d = d[-11:]
    if d.startswith("1") and len(d) == 10:
        d = "0" + d
    return d


# ── SMS parsers ───────────────────────────────────────────────────────────────
# Real-world bKash / Nagad / Rocket SMS formats vary. We match the most common
# patterns observed for Personal / Merchant accounts. All amounts in BDT.

# bKash: "Cash In Tk 5,000.00 from 01XXXXXXXXX successful. Fee Tk 0.00. Balance ..."
#        "You have received Tk 1500 from 01XXXXXXXXX. TrxID ABC123 at 12/01/2026 14:30"
#        "Payment received Tk 2,000.00 from MD KARIM 01712345678 TrxID ..."
_BKASH_RES = [
    re.compile(
        r"(?:cash\s*in|received|payment\s+received)\s+tk\.?\s*([\d,]+(?:\.\d+)?)\s+from\s+"
        r"(?:([A-Za-z][A-Za-z .\-]{1,60}?)\s+)?(\+?\d[\d\s\-]{8,20})",
        re.IGNORECASE,
    ),
    re.compile(
        r"tk\.?\s*([\d,]+(?:\.\d+)?)\s+(?:received\s+)?from\s+(\+?\d[\d\s\-]{8,20})"
        r"(?:[^A-Za-z]+([A-Za-z][A-Za-z .\-]{2,60}))?",
        re.IGNORECASE,
    ),
]

# Nagad: "Money Received. Amount: Tk 3,000.00 Sender: 01XXXXXXXXX TxnID: 7XK..."
_NAGAD_RES = [
    re.compile(
        r"amount[:\s]+tk\.?\s*([\d,]+(?:\.\d+)?)[^\d]+sender[:\s]+(\+?\d[\d\s\-]{8,20})",
        re.IGNORECASE,
    ),
]

# Rocket: "You have received Tk 1000.00 from A/C 01XXXXXXXXX TxnId ..."
_ROCKET_RES = [
    re.compile(
        r"received\s+tk\.?\s*([\d,]+(?:\.\d+)?)\s+from\s+a/?c\s+(\+?\d[\d\s\-]{8,20})",
        re.IGNORECASE,
    ),
]

# Generic fallback for admin paste:
#   "PAY 5000 to Karim 01712345678 bkash"
#   "৫০০০ টাকা পেয়েছি 01712345678"
_GENERIC_RE = re.compile(
    r"(?:tk\.?|৳|taka)?\s*([\d,]{2,9}(?:\.\d+)?)\s*(?:taka|tk|৳)?"
    r"(?:[^0-9]{1,40}?)(\+?\d[\d\s\-]{8,20})",
    re.IGNORECASE,
)

_TRX_RE = re.compile(r"\b(?:trx|txn|trxid|txnid)[:\s]*([A-Z0-9]{4,20})", re.IGNORECASE)
_NAME_RE = re.compile(r"(?:from|sender)[:\s]+([A-Za-z][A-Za-z .\-]{2,60})", re.IGNORECASE)


def _detect_method(text: str) -> str:
    t = text.lower()
    if "bkash" in t or "বিকাশ" in text:
        return "bkash"
    if "nagad" in t or "নগদ" in text:
        return "nagad"
    if "rocket" in t or "রকেট" in text:
        return "rocket"
    return "cash"


def _amount(s: str) -> Optional[float]:
    try:
        v = float(s.replace(",", ""))
        return v if v > 0 else None
    except Exception:
        return None


def parse_payment_sms(text: str) -> Optional[dict]:
    """
    Parse bKash / Nagad / Rocket SMS or generic admin paste.
    Returns dict {amount, mobile, name?, method, trxid?} or None.
    """
    if not text or len(text) < 6:
        return None
    method = _detect_method(text)
    trx_m = _TRX_RE.search(text)
    trxid = trx_m.group(1) if trx_m else None

    candidates: list[re.Pattern] = []
    if method == "bkash":
        candidates += _BKASH_RES
    elif method == "nagad":
        candidates += _NAGAD_RES
    elif method == "rocket":
        candidates += _ROCKET_RES
    candidates += _BKASH_RES + _NAGAD_RES + _ROCKET_RES + [_GENERIC_RE]

    for pat in candidates:
        m = pat.search(text)
        if not m:
            continue
        groups = m.groups()
        amt = _amount(groups[0])
        if amt is None:
            continue
        # Identify which group is mobile vs name (mobile is digit-heavy)
        mobile = None
        name = None
        for g in groups[1:]:
            if g is None:
                continue
            d = _DIGIT_RE.sub("", g)
            if len(d) >= 9 and mobile is None:
                mobile = _norm_mobile(g)
            elif name is None and re.search(r"[A-Za-z]", g):
                name = g.strip()
        if mobile is None:
            continue
        if name is None:
            nm = _NAME_RE.search(text)
            if nm:
                name = nm.group(1).strip()
        return {
            "amount": amt,
            "mobile": mobile,
            "name": name,
            "method": method,
            "trxid": trxid,
        }
    return None


# ── Employee matching ─────────────────────────────────────────────────────────

async def match_employee(
    extracted_mobile: Optional[str],
    extracted_name: Optional[str],
) -> Tuple[Optional[int], float, str]:
    """
    Returns (employee_id, ratio_0_to_1, match_type) where match_type ∈
      {"mobile_exact", "bkash_exact", "nagad_exact", "name_fuzzy", "none"}
    """
    mob = _norm_mobile(extracted_mobile)
    if mob:
        # exact match against employee_mobile / bkash / nagad
        row = await fetch_one(
            """SELECT employee_id, employee_name,
                      CASE WHEN regexp_replace(employee_mobile,'\\D','','g') LIKE '%'||$1 THEN 'mobile_exact'
                           WHEN regexp_replace(COALESCE(bkash_number,''),'\\D','','g') LIKE '%'||$1 THEN 'bkash_exact'
                           WHEN regexp_replace(COALESCE(nagad_number,''),'\\D','','g') LIKE '%'||$1 THEN 'nagad_exact'
                      END AS mtype
               FROM wbom_employees
               WHERE regexp_replace(employee_mobile,'\\D','','g') LIKE '%'||$1
                  OR regexp_replace(COALESCE(bkash_number,''),'\\D','','g') LIKE '%'||$1
                  OR regexp_replace(COALESCE(nagad_number,''),'\\D','','g') LIKE '%'||$1
               LIMIT 1""",
            mob,
        )
        if row:
            return int(row["employee_id"]), 1.0, row["mtype"] or "mobile_exact"

    if extracted_name and len(extracted_name) >= 3:
        emps = await fetch_all(
            "SELECT employee_id, employee_name FROM wbom_employees WHERE employee_name IS NOT NULL"
        )
        if emps:
            choices = {e["employee_id"]: e["employee_name"] for e in emps}
            best = rf_process.extractOne(
                extracted_name, choices, scorer=fuzz.WRatio, score_cutoff=70
            )
            if best:
                _, score, eid = best
                return int(eid), float(score) / 100.0, "name_fuzzy"

    return None, 0.0, "none"


# ── Idempotency ───────────────────────────────────────────────────────────────

def _idempotency_key(method: str, trxid: Optional[str], amount: float,
                     mobile: Optional[str], on_date: date) -> str:
    raw = f"{method}|{trxid or ''}|{amount:.2f}|{mobile or ''}|{on_date.isoformat()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


# ── Main ingest ───────────────────────────────────────────────────────────────

async def ingest_payment_sms(
    text: str,
    sender_number: Optional[str] = None,
    message_id: Optional[int] = None,
    auto_finalize: bool = False,
) -> dict:
    """
    Parse SMS → match → stage. Generic SMS/request ingestion never creates a
    final transaction by default. Final ledger writes are reserved for explicit
    Admin → Accountant payment instructions.
    Always returns a dict with at least {ok, status} and parsed fields.
    """
    parsed = parse_payment_sms(text)
    if not parsed:
        return {"ok": False, "status": "unparsed", "reason": "Could not parse SMS"}
    return await _ingest_parsed(parsed, sender_number, message_id, auto_finalize)


async def _ingest_parsed(
    parsed: dict,
    sender_number: Optional[str] = None,
    message_id: Optional[int] = None,
    auto_finalize: bool = False,
) -> dict:
    """Shared ingest core — called by ingest_payment_sms and ingest_admin_cash_entry."""
    eid, ratio, mtype = await match_employee(parsed["mobile"], parsed.get("name"))

    idem = _idempotency_key(
        parsed["method"], parsed.get("trxid"), parsed["amount"], parsed["mobile"], date.today()
    )

    # Duplicate?
    existing = await fetch_one(
        "SELECT staging_id, status, matched_employee_id, final_transaction_id "
        "FROM wbom_staging_payments WHERE idempotency_key = $1",
        idem,
    )
    if existing:
        log.info(f"[pay-ingest] duplicate idem={idem[:12]} staging_id={existing['staging_id']}")
        return {
            "ok": True, "status": "duplicate", "staging_id": existing["staging_id"],
            "duplicate_of": existing["staging_id"], **parsed,
            "matched_employee_id": existing.get("matched_employee_id"),
        }

    # Decide initial status
    high_conf = (
        eid is not None
        and (mtype.endswith("_exact") or ratio >= AUTO_APPROVE_NAME_RATIO)
        and AUTO_APPROVE_MIN_AMOUNT <= parsed["amount"] <= AUTO_APPROVE_MAX_AMOUNT
    )
    initial_status = "auto_approved" if (auto_finalize and high_conf) else (
        "pending" if eid else "unmatched"
    )

    staging_id = await fetch_val(
        """INSERT INTO wbom_staging_payments
              (message_id, sender_number, extracted_name, extracted_mobile, amount,
               payment_method, transaction_type, matched_employee_id, name_match_ratio,
               status, idempotency_key)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
           RETURNING staging_id""",
        message_id, sender_number, parsed.get("name"), parsed["mobile"],
        parsed["amount"], parsed["method"], "received",
        eid, round(ratio, 2), initial_status, idem,
    )

    log.info(
        f"[pay-ingest] staging_id={staging_id} method={parsed['method']} "
        f"amt={parsed['amount']} mobile={parsed['mobile']} eid={eid} "
        f"ratio={ratio:.2f} mtype={mtype} status={initial_status}"
    )

    result = {
        "ok": True, "status": initial_status, "staging_id": staging_id,
        "matched_employee_id": eid, "name_match_ratio": round(ratio, 2),
        "match_type": mtype, **parsed,
    }

    if initial_status == "auto_approved":
        try:
            fin = await _bridge_to_finalize(staging_id, eid, parsed)
            result["finalized"] = fin
        except Exception as e:
            log.error(f"[pay-ingest] auto-finalize failed staging_id={staging_id}: {e}")
            result["finalize_error"] = str(e)

    return result


async def _bridge_to_finalize(staging_id: int, employee_id: int, parsed: dict) -> dict:
    """
    Create a fazle_payment_drafts row (so finalize_payment has a draft to update),
    call finalize_payment, then link draft + transaction back to staging row.
    """
    emp = await fetch_one(
        "SELECT employee_id, employee_name, employee_mobile FROM wbom_employees WHERE employee_id=$1",
        employee_id,
    )
    if not emp:
        raise RuntimeError(f"Employee {employee_id} vanished")

    draft_text = (
        f"AUTO-INGEST {parsed['method'].upper()} ৳{parsed['amount']:.0f} "
        f"to {emp['employee_name']} ({emp['employee_mobile']})"
        + (f" TrxID {parsed['trxid']}" if parsed.get('trxid') else "")
    )
    draft_id = await fetch_val(
        """INSERT INTO fazle_payment_drafts
              (employee_id, employee_name, employee_mobile, draft_text,
               expected_amount, method, status, draft_type, source)
           VALUES ($1,$2,$3,$4,$5,$6,'pending','auto_payment','payment_ingest')
           RETURNING id""",
        employee_id, emp["employee_name"], emp["employee_mobile"],
        draft_text, parsed["amount"], parsed["method"],
    )

    fin = await finalize_payment(int(draft_id), float(parsed["amount"]), parsed["method"])

    # Link transaction back into staging
    txn_id = await fetch_val(
        """SELECT transaction_id FROM wbom_cash_transactions
           WHERE employee_id=$1 AND amount=$2 AND payment_method=$3
           ORDER BY transaction_id DESC LIMIT 1""",
        employee_id, parsed["amount"], parsed["method"],
    )
    if txn_id:
        await execute(
            "UPDATE wbom_staging_payments SET final_transaction_id=$1, "
            "approved_by='auto-ingest', approved_at=NOW() WHERE staging_id=$2",
            int(txn_id), staging_id,
        )

    log.info(f"[pay-ingest] auto-finalized staging_id={staging_id} draft_id={draft_id} txn_id={txn_id}")
    return {"draft_id": int(draft_id), "transaction_id": int(txn_id) if txn_id else None,
            "accountant_msg": fin.get("accountant_msg")}


# ── Detection helper for inbound message routing ───────────────────────────────

_PAYMENT_HINTS = (
    "bkash", "nagad", "rocket", "trxid", "txnid", "cash in",
    "money received", "payment received", "tk ", "৳", "টাকা পেয়েছি",
)


def looks_like_payment_sms(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    hits = sum(1 for h in _PAYMENT_HINTS if h in t)
    return hits >= 1 and bool(re.search(r"\d{4,}", text))


# ── Admin → Accountant final instruction ─────────────────────────────────────
# Examples:
#   ID: 01795122311 Manik Mea 01789123456(B) 5000/-
#   Manik Mea 01789123456(N) 200/-
#   Saiful op +880 1849-258074(C) 305/-

_ADMIN_PHONE_PAT = r"(?:\+?880[\s\-]?1[3-9](?:[\s\-]?\d){8}|0?1[3-9](?:[\s\-]?\d){8})"

_SHORTHAND_RE = re.compile(
    rf"^\s*(?:ID\s*[:\-]?\s*(?P<employee_id_mobile>{_ADMIN_PHONE_PAT})\s+)?"
    r"(?P<name>.+?)\s+"
    rf"(?P<payout_mobile>{_ADMIN_PHONE_PAT})"
    r"\s*\(\s*(?P<method>N|B|C|R|BANK)\s*\)"
    r"[\s,]*(?P<amount>[\d,]+)\s*/-",
    re.IGNORECASE,
)
_SHORTHAND_METHOD: dict[str, str] = {
    "n": "nagad", "b": "bkash", "c": "cash", "r": "rocket", "bank": "bank",
}


def is_admin_cash_shorthand(text: str) -> bool:
    """Return True if text matches the accountant cash shorthand format."""
    return bool(_SHORTHAND_RE.search(text))


def parse_admin_cash_shorthand(text: str) -> Optional[dict]:
    """
    Parse final Admin → Accountant payment instruction into a parsed dict.
    Returns None if the pattern is not matched or required fields are missing.
    """
    m = _SHORTHAND_RE.search(text)
    if not m:
        return None
    payout_mobile = _norm_mobile(m.group("payout_mobile"))
    employee_id_mobile = _norm_mobile(m.group("employee_id_mobile")) if m.group("employee_id_mobile") else None
    method = _SHORTHAND_METHOD.get(m.group("method").lower(), "cash")
    amount = _amount(m.group("amount"))
    if not payout_mobile or not amount:
        return None
    name = re.sub(r"\s+", " ", m.group("name").strip(" ,;:-"))
    lookup_mobile = employee_id_mobile or payout_mobile
    return {
        "amount": amount,
        "mobile": lookup_mobile,
        "employee_id_mobile": lookup_mobile,
        "payout_mobile": payout_mobile,
        "name": name if 2 <= len(name) <= 80 else None,
        "method": method,
        "trxid": None,
    }


async def _match_or_create_instruction_employee(parsed: dict) -> tuple[int, str, str]:
    """Find/create wbom employee for a final admin-accountant instruction."""
    lookup_mobile = parsed.get("employee_id_mobile") or parsed.get("mobile")
    payout_mobile = parsed.get("payout_mobile") or parsed.get("mobile")
    name = parsed.get("name") or "Unknown"

    eid, _ratio, _mtype = await match_employee(lookup_mobile, name)
    if eid:
        row = await fetch_one(
            "SELECT employee_id, employee_name, employee_mobile FROM wbom_employees WHERE employee_id=$1",
            eid,
        )
        return int(row["employee_id"]), row["employee_name"], row["employee_mobile"]

    # Final admin instruction is authoritative: create minimal employee when
    # no lookup key exists. employee_mobile remains the employee-id-mobile key.
    employee_mobile = lookup_mobile or payout_mobile
    if not employee_mobile:
        raise RuntimeError("missing employee lookup mobile")
    eid = await fetch_val(
        """INSERT INTO wbom_employees
               (employee_name, employee_mobile, designation, status, joining_date)
           VALUES ($1, $2, 'Staff', 'Active', CURRENT_DATE)
           ON CONFLICT (employee_mobile) DO UPDATE
              SET employee_name = COALESCE(NULLIF(EXCLUDED.employee_name, ''), wbom_employees.employee_name),
                  status = 'Active'
           RETURNING employee_id""",
        name,
        employee_mobile,
    )
    return int(eid), name, employee_mobile


async def ingest_admin_cash_entry(
    text: str,
    sender_number: Optional[str] = None,
    message_id: Optional[int] = None,
) -> dict:
    """
    Parse a final Admin → Accountant instruction and write the ledger directly.
    This path intentionally does not create `fazle_payment_drafts`.
    """
    parsed = parse_admin_cash_shorthand(text)
    if not parsed:
        return {"ok": False, "status": "unparsed", "reason": "Could not parse cash shorthand"}

    employee_id, employee_name, employee_mobile = await _match_or_create_instruction_employee(parsed)
    idem = f"admin-accountant-message:{message_id}" if message_id is not None else None
    if idem:
        existing = await fetch_one(
            "SELECT transaction_id FROM wbom_cash_transactions WHERE idempotency_key=$1",
            idem,
        )
        if existing:
            return {
                "ok": True,
                "status": "duplicate",
                "transaction_id": existing["transaction_id"],
                "duplicate_of": existing["transaction_id"],
                "employee_id": employee_id,
                "employee_name": employee_name,
                **parsed,
            }

    row = await fetch_one(
        """INSERT INTO wbom_cash_transactions
              (employee_id, amount, transaction_type, payment_method,
               payment_mobile, payment_number, employee_phone,
               transaction_date, remarks, created_by, source,
               idempotency_key, whatsapp_message_id)
           VALUES ($1, $2, 'advance', $3, $4::text, $4::text, $5,
                   CURRENT_DATE, $6, $7, 'admin-accountant-instruction',
                   $8, $9)
           ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
           RETURNING transaction_id""",
        employee_id,
        parsed["amount"],
        parsed["method"],
        parsed.get("payout_mobile") or parsed.get("mobile"),
        parsed.get("employee_id_mobile") or employee_mobile,
        f"Admin→Accountant instruction: {text.strip()[:500]}",
        sender_number,
        idem,
        message_id,
    )
    if not row and idem:
        existing = await fetch_one(
            "SELECT transaction_id FROM wbom_cash_transactions WHERE idempotency_key=$1",
            idem,
        )
        return {
            "ok": True,
            "status": "duplicate",
            "transaction_id": existing["transaction_id"] if existing else None,
            "employee_id": employee_id,
            "employee_name": employee_name,
            **parsed,
        }

    txn_id = row["transaction_id"] if row else None
    log.info(
        "[pay-ingest] final admin-accountant txn=%s employee_id=%s amount=%s method=%s",
        txn_id, employee_id, parsed["amount"], parsed["method"],
    )
    return {
        "ok": True,
        "status": "finalized",
        "transaction_id": txn_id,
        "employee_id": employee_id,
        "employee_name": employee_name,
        "employee_mobile": employee_mobile,
        **parsed,
    }
