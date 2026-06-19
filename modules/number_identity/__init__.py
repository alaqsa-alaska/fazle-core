import hashlib
import os
import re
from datetime import datetime, timezone

from app.critical_numbers import normalize_phone_880
from app.config import get_settings


def canonical_phone(raw: str) -> str:
    if not raw:
        return ""
    if raw.startswith("unresolved:"):
        return raw
    return normalize_phone_880(raw)


def normalize_phone(raw: str) -> list[str]:
    """Return all valid normalized variants of a Bangladesh mobile number.

    Order: [01XXXXXXXXXX, 8801XXXXXXXXXX, +8801XXXXXXXXXX]
    Returns empty list if input cannot be normalized.
    Delegates to phone_normalizer for canonical validation (operator code check included).
    """
    from modules.phone_normalizer import normalize_phone as _canonical
    canonical = _canonical(raw)
    if not canonical:
        return []
    return ["0" + canonical[3:], canonical, "+" + canonical]


def phone_last10(raw: str) -> str:
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    if len(digits) >= 10:
        return digits[-10:]
    return digits


def is_critical_phone(phone: str, identity_role: str = "") -> bool:
    settings = get_settings()
    canon = canonical_phone(phone)
    role = (identity_role or "").strip().lower()
    if not canon:
        return False
    if canon in settings.critical_phone_set:
        return True
    if phone_last10(canon) in settings.critical_phone_last10_set:
        return True
    return bool(role and role in settings.critical_role_set)


def build_message_hash(
    *,
    platform: str,
    canonical_sender: str,
    direction: str,
    text: str,
    event_ts: datetime | None,
    source_ref: str = "",
) -> str:
    if event_ts is None:
        event_ts = datetime.now(timezone.utc)
    ts = event_ts.astimezone(timezone.utc).isoformat()
    payload = "\x1f".join([
        platform or "",
        canonical_sender or "",
        direction or "",
        text or "",
        ts,
        source_ref or "",
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def append_critical_log(
    *,
    phone: str,
    direction: str,
    text: str,
    platform: str,
    identity_role: str = "",
    event_ts: datetime | None = None,
    original_phone: str = "",
) -> str:
    settings = get_settings()
    canon = canonical_phone(phone)
    if not canon:
        return ""
    os.makedirs(settings.critical_log_dir, exist_ok=True)
    path = os.path.join(settings.critical_log_dir, f"{canon}.txt")
    line = format_critical_log_line(
        phone=canon,
        direction=direction,
        text=text,
        platform=platform,
        identity_role=identity_role,
        event_ts=event_ts,
        original_phone=original_phone,
    )
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line)
    return path


def format_critical_log_line(
    *,
    phone: str,
    direction: str,
    text: str,
    platform: str,
    identity_role: str = "",
    event_ts: datetime | None = None,
    original_phone: str = "",
) -> str:
    canon = canonical_phone(phone)
    event_ts = (event_ts or datetime.now(timezone.utc)).astimezone(timezone.utc)
    safe_text = (text or "").replace("\r", "").replace("\n", " ").strip()
    role = (identity_role or "unknown").strip() or "unknown"
    original = f" original={original_phone}" if original_phone and original_phone != canon else ""
    return (
        f"[{event_ts.isoformat()}] [{platform}] [{direction}]"
        f" [role={role}]{original} {safe_text}\n"
    )