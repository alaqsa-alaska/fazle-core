"""
Fazle Payroll Engine — Message-type-aware validation registry.

Each message type declares its own required fields and payload structure.
The accounting worker calls validate_for_accounting() instead of applying
generic assumptions (e.g. "all messages have a top-level amount"), which
caused escort_payment messages to be silently skipped before this refactor.

Adding a new message type:
  1. Add a MessageTypeRule entry to MESSAGE_RULES
  2. Set handler= to the logical handler name used in _process_parsed_batch
  3. Set requires_entries=True for multi-entry payloads (e.g. escort_payment)
  4. Set required_fields for scalar payloads (e.g. ["amount"])
  5. Map failure_codes for human-readable skip/fail reasons
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional


# ── Result object ─────────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    valid: bool
    failure_code: Optional[str] = None
    failure_detail: Optional[str] = None


# ── Per-type rule definition ──────────────────────────────────────────────────

@dataclass
class MessageTypeRule:
    """
    Declarative description of a message type's accounting requirements.

    handler:          logical handler name used in _process_parsed_batch dispatch.
    required_fields:  scalar fields that MUST be present and non-empty.
    requires_entries: True when the payload is entries[] not a scalar amount.
    min_amount:       minimum acceptable amount when 'amount' is a required field.
    failure_codes:    maps semantic failure keys → skip/fail reason codes stored
                      in fpe_unmatched_messages.skip_reason.
    """
    handler: str
    required_fields: list[str] = field(default_factory=list)
    requires_entries: bool = False
    min_amount: Decimal = Decimal("0.01")
    failure_codes: dict[str, str] = field(default_factory=dict)


# ── Registry ──────────────────────────────────────────────────────────────────
#
# IMPORTANT: when adding a new actionable type also update the SQL IN clause
# in _process_parsed_batch() in workers.py (or use = ANY($n) with ACCOUNTING_TYPES).

MESSAGE_RULES: dict[str, MessageTypeRule] = {
    # Standard WhatsApp payment message (owner-sent)
    "payment": MessageTypeRule(
        handler="standard_payment",
        required_fields=["employee_name_raw", "payout_phone", "payout_method", "amount"],
        failure_codes={
            "missing_employee_name_raw": "missing_employee_name",
            "missing_payout_phone":   "missing_payout_phone",
            "missing_payout_method":  "missing_payout_method",
            "missing_amount":       "no_amount_in_parse",
            "non_positive_amount":  "non_positive_amount",
            "no_employee":          "no_employee_match",
        },
    ),

    # "Cash <name> <amount>" command — strict employee lookup, bridge reply on miss
    "cash_command": MessageTypeRule(
        handler="cash_command",
        required_fields=["amount"],
        failure_codes={
            "missing_amount":       "no_amount_in_parse",
            "non_positive_amount":  "non_positive_amount",
            "no_employee":          "cash_command_no_employee",
        },
    ),

    # "Income <phone> <name> <amount>" command — auto-creates employee
    "income_command": MessageTypeRule(
        handler="income_command",
        required_fields=["amount"],
        failure_codes={
            "missing_amount":       "no_amount_in_parse",
            "non_positive_amount":  "non_positive_amount",
        },
    ),

    # Multi-employee escort duty payment list:
    #   Zakir=150/\nBabul=300/\n...\nTotall=1830/\nNight Shift\n05/5/26
    # Payload: entries[], shift, duty_date — NO top-level amount.
    "escort_payment": MessageTypeRule(
        handler="escort_payment",
        requires_entries=True,          # entries[] is the payload, not amount
        failure_codes={
            "empty_entries":        "escort_payment_no_entries",
            "unusable_entries":     "escort_payment_entries_invalid",
        },
    ),

    # Escort roster lifecycle update (future)
    "escort_roster": MessageTypeRule(
        handler="escort_roster",
        required_fields=[],
        failure_codes={},
    ),

    # Escort slip OCR extraction (future)
    "escort_slip": MessageTypeRule(
        handler="escort_slip",
        required_fields=[],
        failure_codes={},
    ),

    # Informational only — no accounting action
    "balance_summary": MessageTypeRule(
        handler="skip",
        required_fields=[],
    ),

    # Catch-all
    "other": MessageTypeRule(
        handler="skip",
        required_fields=[],
    ),
}

# Convenience set: types the accounting worker should dequeue and process.
# Derive from MESSAGE_RULES so it stays in sync as new types are added.
ACCOUNTING_TYPES: frozenset[str] = frozenset(
    k for k, v in MESSAGE_RULES.items()
    if v.handler not in ("skip",)
)


# ── Public API ────────────────────────────────────────────────────────────────

def get_rule(msg_type: str) -> MessageTypeRule:
    """Return rule for msg_type, or a generic 'skip' rule for unknown types."""
    return MESSAGE_RULES.get(msg_type) or MessageTypeRule(
        handler="skip",
        failure_codes={"unknown_type": f"unknown_message_type_{msg_type}"},
    )


def validate_for_accounting(msg_type: str, pdata: dict) -> ValidationResult:
    """
    Validate parsed_data against the type's declared requirements.

    Returns ValidationResult(valid=True) when the data is ready for accounting.
    Returns ValidationResult(valid=False, failure_code=...) on structural error.

    This is the ONLY place that should decide whether a parsed message
    has sufficient data for accounting.  Never add ad-hoc field checks
    inside _process_parsed_batch() — add them here instead.
    """
    rule = get_rule(msg_type)

    # ── Entry-based types (escort_payment) ───────────────────────────────────
    if rule.requires_entries:
        entries = pdata.get("entries") or []
        if not entries:
            return ValidationResult(
                valid=False,
                failure_code=rule.failure_codes.get("empty_entries", "missing_entries"),
                failure_detail=f"message_type={msg_type}: entries[] is absent or empty",
            )
        usable = [
            e for e in entries
            if (e.get("name") or "").strip() and _to_decimal(e.get("amount")) is not None
        ]
        if not usable:
            return ValidationResult(
                valid=False,
                failure_code=rule.failure_codes.get("unusable_entries", "escort_entries_invalid"),
                failure_detail=(
                    f"message_type={msg_type}: {len(entries)} entries present "
                    f"but none have valid name+amount"
                ),
            )
        return ValidationResult(valid=True)

    # ── Field-based types (payment, cash_command, income_command) ─────────────
    for fname in rule.required_fields:
        val = pdata.get(fname)
        if val is None or str(val).strip() == "":
            code = rule.failure_codes.get(f"missing_{fname}", f"missing_{fname}")
            return ValidationResult(
                valid=False,
                failure_code=code,
                failure_detail=f"message_type={msg_type}: required field '{fname}' is absent",
            )
        if fname == "amount":
            d = _to_decimal(val)
            if d is None:
                return ValidationResult(
                    valid=False,
                    failure_code=rule.failure_codes.get("missing_amount", "no_amount_in_parse"),
                    failure_detail=f"message_type={msg_type}: amount='{val}' is not numeric",
                )
            if d < rule.min_amount:
                return ValidationResult(
                    valid=False,
                    failure_code=rule.failure_codes.get(
                        "non_positive_amount", "non_positive_amount"
                    ),
                    failure_detail=f"message_type={msg_type}: amount={d} < {rule.min_amount}",
                )

    return ValidationResult(valid=True)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _to_decimal(val) -> Optional[Decimal]:
    """Parse val as Decimal; return None on failure."""
    if val is None:
        return None
    try:
        return Decimal(str(val))
    except (InvalidOperation, ValueError):
        return None
