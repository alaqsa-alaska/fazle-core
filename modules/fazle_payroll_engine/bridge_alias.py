"""Display-only aliases for the configured WhatsApp bridge accounts."""
from __future__ import annotations

from typing import Any

from app.config import get_settings
from modules.phone_normalizer import normalize_phone


def bridge_alias_for_phone(phone: str | None, settings: Any = None) -> str | None:
    """Return a configured bridge label without changing stored attribution."""
    canonical = normalize_phone(phone or "")
    if not canonical:
        return None
    settings = settings or get_settings()
    for index in (1, 2, 3):
        configured = normalize_phone(getattr(settings, f"bridge{index}_number", ""))
        if configured and configured == canonical:
            return f"Bridge{index}"
