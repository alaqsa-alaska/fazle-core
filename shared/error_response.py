"""
shared.error_response — Standardized error response builder.

All user-facing error returns SHOULD use make_error_response() for
consistent error structure across the system.

Format:
  {
    "error_code": str,    # machine-readable, e.g. "employee_not_found"
    "message": str,       # human-readable Bangla message
    "retry": bool,        # True if retrying may succeed
    "suggestion": str,    # what the user/admin should do next
    "timestamp": str      # ISO 8601 UTC timestamp
  }
"""
from __future__ import annotations

from datetime import datetime, timezone


def make_error_response(
    code: str,
    message_bn: str,
    retry: bool = False,
    suggestion: str = "",
) -> dict:
    """Build a standardized error response dict.

    Args:
        code:       Machine-readable error code (snake_case).
        message_bn: Human-readable message in Bangla.
        retry:      True if the caller can retry the operation.
        suggestion: What the user/admin should do next (optional).

    Returns:
        Standardized error dict.
    """
    return {
        "error_code": code,
        "message": message_bn,
        "retry": retry,
        "suggestion": suggestion,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }


# Common pre-built error response factories for frequent cases.
# Factories ensure each call includes a fresh timestamp and isolated dict.
def err_employee_not_found() -> dict:
    return make_error_response(
        "employee_not_found",
        "কর্মী খুঁজে পাওয়া যায়নি। ফোন নম্বর যাচাই করুন।",
        retry=False,
        suggestion="সিস্টেমে নিবন্ধিত ফোন নম্বর দিয়ে আবার চেষ্টা করুন।",
    )


def err_processing_timeout() -> dict:
    return make_error_response(
        "processing_timeout",
        "প্রক্রিয়াকরণ সময় শেষ। আবার চেষ্টা করুন।",
        retry=True,
        suggestion="কয়েক সেকেন্ড পরে আবার বার্তা পাঠান।",
    )


def err_permission_denied() -> dict:
    return make_error_response(
        "permission_denied",
        "⛔ অনুমতি নেই। এই কাজের জন্য পর্যাপ্ত রোল নেই।",
        retry=False,
        suggestion="প্রয়োজনীয় রোলের জন্য সুপারঅ্যাডমিনের সাথে যোগাযোগ করুন।",
    )


# Backward-compatible uppercase aliases.
def ERR_EMPLOYEE_NOT_FOUND() -> dict:
    return err_employee_not_found()


def ERR_PROCESSING_TIMEOUT() -> dict:
    return err_processing_timeout()


def ERR_PERMISSION_DENIED() -> dict:
    return err_permission_denied()
