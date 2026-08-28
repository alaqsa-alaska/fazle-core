"""Stage G13/G14 (2026-08-28) -- Trusted Direct Admin Execution lane.

A direct instruction from the authenticated, trusted Super Admin channel
can itself constitute authorization for a supported business operation
(here: employee creation) -- the direct instruction IS the authorization,
no second approval request is created for the same explicit Owner
instruction (mirrors the existing precedent this codebase already
established for remember_business_rule/revoke_directive, see CLAUDE.md).

Reuses the EXISTING authoritative operation
(modules.admin_employees.create_employee(), the same function the
frontend's employee-creation UI calls -- Stage G15's "frontend and
WhatsApp converge on the same authoritative operation") and the EXISTING
trusted-channel identity check
(modules.admin_directives.router._handle_hermes_relay's own
sender_phone==admin_bridge2_number AND source_bridge in
_HERMES_CONTROL_CHANNELS gate -- reimplemented as one small, directly
reusable predicate here rather than duplicated inline).

Never guesses. If name or mobile cannot be confidently, unambiguously
extracted, returns a clarification request instead of executing anything
(Stage G14's explicit rule -- "এটা approve করে দাও" must not mutate
anything unless the intended object is uniquely established; the same
principle applies here to an under-specified creation instruction).
"""
from __future__ import annotations

import re
from typing import Optional

# ── Instruction pattern ──────────────────────────────────────────────────
# Matches "<Name>, <mobile>" as a comma-separated pair anywhere in the
# text (the canonical Owner-example shape:
# "Mohammad Karamat Ali, 01712345678, employee হিসাবে create করো") --
# word order around it (English "create ... as employee", Bangla
# "employee হিসাবে create করো") is deliberately NOT hard-coded into the
# same pattern; instead both "employee" and "create"-family keywords are
# required to appear SOMEWHERE in the text (order-independent), checked
# separately below. A wrong non-match just means "ask for
# clarification", never a wrong guess that executes.
_NAME_MOBILE_RE = re.compile(
    r"(?P<name>[A-Za-zঀ-৿][A-Za-zঀ-৿.\s]{1,60}?)\s*,\s*"
    r"(?P<mobile>(?:\+?88)?0?1[3-9]\d{8})",
)
_EMPLOYEE_KEYWORD_RE = re.compile(r"employee|কর্মচারী|কর্মী", re.IGNORECASE)
_CREATE_KEYWORD_RE = re.compile(r"\bcreate\b|করো|করুন|তৈরি", re.IGNORECASE)


def parse_employee_creation_instruction(text: str) -> Optional[dict]:
    """Deterministic (non-Ollama) extraction, same discipline as
    modules.route_b_handoff._extract_employee_claim_fields: narrow,
    conservative matching is fine here specifically because a non-match
    or partial match never executes anything on its own -- it only ever
    produces a clarification request or nothing at all."""
    if not text:
        return None
    if not _EMPLOYEE_KEYWORD_RE.search(text) or not _CREATE_KEYWORD_RE.search(text):
        return None
    m = _NAME_MOBILE_RE.search(text)
    if not m:
        return None
    name = m.group("name").strip().rstrip(",")
    # Strip a leading action-verb word ("create Mohammad Karamat Ali" ->
    # "Mohammad Karamat Ali") that a reordered instruction (verb before
    # the name) can otherwise fold into the captured name -- a narrow,
    # explicit cleanup, not a guess: only ever removes exactly this
    # keyword, never invents or reorders anything else.
    name = _CREATE_KEYWORD_RE.sub("", name, count=1).strip()
    mobile = m.group("mobile").strip()
    if not name or not mobile:
        return None
    return {"employee_name": name, "employee_mobile": mobile}


def is_trusted_direct_admin_channel(sender_phone: str, source_bridge: str, admin_bridge2_number: str, control_channels: frozenset) -> bool:
    """The exact identity+channel check modules.admin_directives.router.
    _handle_hermes_relay already uses (sender_phone == admin_bridge2_number
    AND source_bridge in _HERMES_CONTROL_CHANNELS) -- reused as a single
    predicate rather than re-derived inline at each new call site."""
    return bool(sender_phone) and sender_phone == (admin_bridge2_number or "") and source_bridge in control_channels


async def execute_trusted_employee_creation(
    text: str, sender_phone: str, source_bridge: str, *, dry_run: bool = True,
) -> dict:
    """G13's full flow: trusted Admin direct message -> parse -> validate
    target -> duplicate/identity check -> execute (or dry-run stop short
    of executing) -> report result.

    dry_run=True (the default, and the ONLY mode used for live proof this
    stage per explicit instruction "do not create a fake employee purely
    for testing"): runs every check (channel, parse, duplicate) and
    reports exactly what WOULD happen, without calling create_employee()
    at all. dry_run=False actually calls the existing, authoritative
    create_employee() -- identical code path either way up to that one
    final call, so a dry-run result is a genuine preview, not a
    simulation."""
    from app.config import get_settings
    from modules.admin_directives.router import _HERMES_CONTROL_CHANNELS

    settings = get_settings()
    if not is_trusted_direct_admin_channel(
        sender_phone, source_bridge, settings.admin_bridge2_number or "", _HERMES_CONTROL_CHANNELS,
    ):
        return {"ok": False, "stage": "channel_check", "error": "not a trusted direct Admin channel"}

    parsed = parse_employee_creation_instruction(text)
    if not parsed:
        return {
            "ok": False, "stage": "parse", "needs_clarification": True,
            "error": "could not unambiguously extract employee_name and employee_mobile from the instruction -- "
                     "expected shape: '<Full Name>, <mobile>, employee হিসাবে create করো' (or the English equivalent)",
        }

    # Same normalization create_employee() itself uses, so this pre-check
    # matches exactly what create_employee()'s own duplicate check would
    # find -- a different normalizer here could produce a false negative
    # (missing a real duplicate) purely from a format mismatch.
    from modules.fazle_payroll_engine.normalizer import normalize_bd_phone
    from app.database import fetch_one
    norm_mobile = normalize_bd_phone(parsed["employee_mobile"])
    if not norm_mobile:
        return {"ok": False, "stage": "parse", "error": f"invalid mobile number: {parsed['employee_mobile']!r}"}
    existing = await fetch_one(
        "SELECT employee_id, employee_name, status FROM wbom_employees WHERE employee_mobile = $1",
        norm_mobile,
    )
    if existing:
        return {
            "ok": False, "stage": "duplicate_check",
            "error": f"an employee with this mobile already exists: employee_id={existing['employee_id']}, "
                     f"name={existing['employee_name']}, status={existing['status']}",
            "existing_employee_id": existing["employee_id"],
        }

    if dry_run:
        return {
            "ok": True, "stage": "dry_run", "would_create": parsed,
            "note": "dry_run=True -- create_employee() was NOT called; this is a validated preview only",
        }

    from modules.admin_employees import create_employee, EmployeeCreate
    body = EmployeeCreate(employee_name=parsed["employee_name"], employee_mobile=parsed["employee_mobile"])
    result = await create_employee(body)
    return {"ok": True, "stage": "executed", "result": result}
