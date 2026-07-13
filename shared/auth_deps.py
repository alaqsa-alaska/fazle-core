"""
Shared FastAPI auth dependency — the plain "is this a valid admin-tier
key at all" check (no role differentiation).

Extracted 2026-07-13: this exact ~10-line check (internal key exact match,
else look up an active admin by API key via modules.rbac) was independently
copy-pasted across modules/contact_roles/routes.py, modules/drafts/routes.py,
modules/kb_upload/routes.py, modules/preflight_guard/routes.py, and
modules/social_auto_reply/routes.py — with only cosmetic differences
(return value never actually used by any caller — every one of them wires
it via `dependencies=[Depends(_require_api_key)]`, not as an injected
parameter; error detail text "Forbidden" vs "Unauthorized"; one had an
extra diagnostic log line on lookup failure, folded in here).

Deliberately NOT touching the role-aware dependencies (_require_admin_or_above,
_require_viewer_or_above, _require_admin_role, etc.) in modules/attendance,
modules/fazle_payroll_engine, modules/escort_roster, modules/client_billing —
those have real per-module differences in which roles they accept and are
higher-risk to consolidate blind (a mistake there could silently broaden
access, not just break something visibly). This file only covers the
binary valid-key-or-not check.
"""
from __future__ import annotations

import logging

from fastapi import Depends, HTTPException
from fastapi.security import APIKeyHeader

from app.config import get_settings

log = logging.getLogger("fazle.shared.auth_deps")

API_KEY_HEADER = APIKeyHeader(name="X-Internal-Key", auto_error=False)


async def require_api_key(key: str = Depends(API_KEY_HEADER)) -> None:
    """Raise 403 unless key is the internal key or an active admin's API key.
    Wire as `dependencies=[Depends(require_api_key)]` — the return value
    carries no information (matching how every existing call site already
    used the pre-extraction versions of this function)."""
    settings = get_settings()
    if key and key == settings.internal_api_key:
        return
    if key:
        try:
            from modules import rbac
            admin = await rbac.get_admin_by_api_key(key)
            if admin and admin.get("status") == "active":
                return
        except Exception as e:
            log.warning(f"Auth error for key {key[:10]}...: {e}")
    raise HTTPException(status_code=403, detail="Forbidden")
