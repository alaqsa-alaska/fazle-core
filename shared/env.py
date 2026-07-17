"""
shared.env — Unified environment / settings accessors.

A thin, convenience layer on top of app.config.get_settings().
Centralises all "which phone is the admin?" / "which bridge is authoritative?"
lookups so modules do not have to repeat config-parsing logic.

Usage
-----
  from shared.env import Env

  env = Env()
  env.admin_phone()              # "8801880446111"
  env.bridge_numbers()           # {"bridge1": "8801958122300", ...}
  env.is_admin_number(phone)     # True / False
  env.is_trusted_escort_source(src)  # True / False
  env.escort_client_numbers()    # list[str]
  env.draft_ttl_hours()          # 24
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from app.config import get_settings
from shared.phone import phone_last10


class Env:
    """
    Lightweight wrapper around the application Settings object.

    All methods are synchronous (config is in-memory after load).
    """

    # ── Admin / authority ─────────────────────────────────────────────────────

    def admin_phone(self) -> str:
        """Primary admin authority phone (Bridge2 OPS line)."""
        return get_settings().admin_bridge2_number

    def hr_phone(self) -> str:
        """HR / Bridge1 phone."""
        return get_settings().admin_bridge1_number

    def admin_number_list(self) -> list[str]:
        """All admin numbers from the comma-separated config value."""
        raw = get_settings().admin_numbers or ""
        return [n.strip() for n in raw.split(",") if n.strip()]

    def is_admin_number(self, phone: str) -> bool:
        """Return True if `phone` matches any configured admin number (last-10 match)."""
        p10 = phone_last10(phone)
        return any(phone_last10(a) == p10 for a in self.admin_number_list())

    # ── Bridges ───────────────────────────────────────────────────────────────

    def bridge_numbers(self) -> dict[str, str]:
        """Map of bridge_id → bridge phone number."""
        s = get_settings()
        return {
            "bridge1": s.admin_bridge1_number,
            "bridge2": s.admin_bridge2_number,
        }

    # ── Escort sources / clients ──────────────────────────────────────────────

    def escort_trusted_sources(self) -> list[str]:
        """Bridge IDs trusted to submit new escort orders (Rule 7)."""
        raw = getattr(get_settings(), "escort_trusted_sources", "bridge1,bridge2,meta")
        return [s.strip() for s in raw.split(",") if s.strip()]

    def is_trusted_escort_source(self, source: str) -> bool:
        """Return True if `source` is an authorised escort submission bridge."""
        return source in self.escort_trusted_sources()

    def escort_client_numbers(self) -> list[str]:
        """Phone numbers authorised to send escort client orders (Rule 7)."""
        raw = getattr(get_settings(), "escort_client_phones", "")
        return [n.strip() for n in raw.split(",") if n.strip()]

    def is_escort_client(self, phone: str) -> bool:
        """
        Return True if phone is in the escort_client_phones whitelist.
        When the whitelist is EMPTY, all phones are considered allowed
        (open mode — same behaviour as before Rule 7 was implemented).

        DEAD CODE — found unused (zero callers repo-wide) during the Phase
        1B.2 escort-client identity convergence audit (2026-07-17); a
        second, divergent implementation of the same allowlist check exists
        at modules.escort._is_allowed_escort_client() / _escort_client_phone_list(),
        which is the one actually used by the parser gate, and
        modules.identity_brain._matches_env_escort_phone(), which is the one
        used by identity resolution (that one deliberately does NOT treat an
        empty list as "match" — see its own docstring). Kept, not deleted,
        per this repo's standing "document dead code, don't clean it up
        inline" convention — do not add a new caller here; use the
        canonical implementations above instead. See
        knowledge_base/00_governance/management_decisions.md, Session 21
        Phase 1B.2.
        """
        clients = self.escort_client_numbers()
        if not clients:
            return True  # open mode
        p10 = phone_last10(phone)
        return any(phone_last10(c) == p10 for c in clients)

    # ── Draft config ──────────────────────────────────────────────────────────

    def draft_ttl_hours(self) -> int:
        """Hours after which a pending payment draft is auto-expired."""
        return int(getattr(get_settings(), "draft_ttl_hours", 24))

    # ── Feature flags ─────────────────────────────────────────────────────────

    def draft_creation_enabled(self) -> bool:
        return bool(get_settings().draft_creation_enabled)

    def auto_reply_enabled(self) -> bool:
        return bool(get_settings().auto_reply_enabled)


# Module-level singleton — import and call methods directly:
#   from shared.env import env
#   env.admin_phone()
env = Env()


@lru_cache(maxsize=1)
def get_env() -> Env:
    """Cached Env singleton (use `env` above for convenience)."""
    return Env()
