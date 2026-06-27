"""Batch 19 — RBAC & Audit module.

Tables
------
* ``fazle_roles`` (seeded: viewer / operator / accountant / admin / superadmin)
* ``fazle_admins`` — humans who can act on the system
* ``fazle_admin_roles`` — admin↔role mapping
* ``fazle_admin_audit`` — every command attempt (allowed or denied)

Concepts
--------
* Each command has a *required_role*. The admin's max role-level must be ≥
  that command's level.
* Phones listed in env ``ADMIN_NUMBERS`` (B11/B12) are auto-bootstrapped on
  first sight as ``superadmin`` so the system is never locked out.
* API keys are stored as SHA-256 hashes; lookup is by hash.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
from typing import Any, Optional

from app.database import execute, fetch_all, fetch_one, fetch_val

log = logging.getLogger("fazle.rbac")


# ── command → required role table ────────────────────────────────────────────
COMMAND_ROLE: dict[str, str] = {
    # viewer (read-only)
    "status":              "viewer",
    "schedule_status":     "viewer",
    "report_list":         "viewer",
    "report_daily":        "viewer",
    "report_payroll":      "viewer",
    "report_cash":         "viewer",
    "report_recon":        "viewer",
    "report_escort":       "viewer",
    "backup_status":       "viewer",
    "backup_list":         "viewer",
    "payroll_list":        "viewer",
    # operator
    "approve":             "operator",
    "reject":              "operator",
    "edit":                "operator",
    "paid":                "operator",
    "advance":             "operator",
    "release":             "operator",
    "escortconfirm":       "operator",
    # accountant
    "payimport":           "accountant",
    "payroll_compute":     "accountant",
    "payroll_trans":       "accountant",
    "payroll_paid":        "accountant",
    "payroll_cancel":      "accountant",
    # admin
    "schedule_run":        "admin",
    "backup_now":          "admin",
    "user_list":           "admin",
    # superadmin
    "user_add":            "superadmin",
    "user_role":           "superadmin",
    "user_remove":         "superadmin",
    "user_apikey":         "superadmin",
}

DEFAULT_REQUIRED_ROLE = "admin"  # unknown cmd defaults to admin


# ── helpers ──────────────────────────────────────────────────────────────────
def _normalize_phone(p: str) -> str:
    from modules.phone_normalizer import normalize_phone as _pn
    return _pn(p or "") or re.sub(r"\D", "", p or "")


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _normalize_username(username: str) -> str:
    return (username or "").strip().lower()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    iterations = 200_000
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    salt_b64 = base64.urlsafe_b64encode(salt).decode("ascii")
    hash_b64 = base64.urlsafe_b64encode(derived).decode("ascii")
    return f"pbkdf2_sha256${iterations}${salt_b64}${hash_b64}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, iterations_s, salt_b64, hash_b64 = (stored_hash or "").split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iterations_s)
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(hash_b64.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def new_login_token() -> str:
    return "fk_" + secrets.token_urlsafe(32)


def _bootstrap_phones() -> list[str]:
    raw = os.getenv("ADMIN_NUMBERS", "")
    out = []
    for p in re.split(r"[,;\s]+", raw):
        p = _normalize_phone(p)
        if p:
            out.append(p)
    return out


# ── core lookups ─────────────────────────────────────────────────────────────
async def get_admin_by_phone(phone: str) -> Optional[dict]:
    p = _normalize_phone(phone)
    if not p:
        return None
    row = await fetch_one(
        "SELECT id, phone, name, username, status FROM fazle_admins WHERE phone=$1", p,
    )
    return dict(row) if row else None


async def get_admin_by_api_key(key: str) -> Optional[dict]:
    if not key:
        return None
    h = hash_api_key(key)
    row = await fetch_one(
        "SELECT id, phone, name, username, status FROM fazle_admins WHERE api_key_hash=$1 OR login_token_hash=$2",
        h, h,
    )
    return dict(row) if row else None


async def get_admin_by_username(username: str) -> Optional[dict]:
    u = _normalize_username(username)
    if not u:
        return None
    row = await fetch_one(
        "SELECT id, phone, name, status, username, password_hash FROM fazle_admins WHERE LOWER(username)=$1",
        u,
    )
    return dict(row) if row else None


async def get_roles(admin_id: int) -> list[str]:
    rows = await fetch_all(
        "SELECT r.name FROM fazle_admin_roles ar "
        "JOIN fazle_roles r ON r.name = ar.role_name "
        "WHERE ar.admin_id = $1 ORDER BY r.level DESC",
        admin_id,
    )
    return [r["name"] for r in rows]


async def get_role_level(role_name: str) -> int:
    v = await fetch_val("SELECT level FROM fazle_roles WHERE name=$1", role_name)
    return int(v) if v is not None else 0


async def max_role_level(admin_id: int) -> int:
    v = await fetch_val(
        "SELECT COALESCE(MAX(r.level), 0) FROM fazle_admin_roles ar "
        "JOIN fazle_roles r ON r.name = ar.role_name WHERE ar.admin_id=$1",
        admin_id,
    )
    return int(v or 0)


# ── bootstrap ────────────────────────────────────────────────────────────────
async def ensure_bootstrap_admins() -> int:
    """Create env ADMIN_NUMBERS entries as superadmin if missing."""
    created = 0
    for phone in _bootstrap_phones():
        existing = await get_admin_by_phone(phone)
        if existing:
            # ensure they are superadmin
            roles = await get_roles(existing["id"])
            if "superadmin" not in roles:
                await execute(
                    "INSERT INTO fazle_admin_roles (admin_id, role_name, granted_by) "
                    "VALUES ($1,'superadmin','bootstrap') "
                    "ON CONFLICT DO NOTHING",
                    existing["id"],
                )
                log.info(f"[rbac] promoted bootstrap admin {phone} → superadmin")
            continue
        admin_id = await fetch_val(
            "INSERT INTO fazle_admins (phone, name, status, notes) "
            "VALUES ($1, $2, 'active', 'auto-bootstrap from ADMIN_NUMBERS') "
            "RETURNING id",
            phone, f"bootstrap_{phone[-4:]}",
        )
        await execute(
            "INSERT INTO fazle_admin_roles (admin_id, role_name, granted_by) "
            "VALUES ($1,'superadmin','bootstrap')",
            admin_id,
        )
        created += 1
        log.info(f"[rbac] bootstrapped {phone} as superadmin (id={admin_id})")
    return created


# ── permission check ─────────────────────────────────────────────────────────
async def check_permission(
    *, phone: Optional[str] = None,
    api_key: Optional[str] = None,
    command: str,
) -> dict[str, Any]:
    """Return {allowed, admin, required_role, reason}."""
    required = COMMAND_ROLE.get(command, DEFAULT_REQUIRED_ROLE)
    req_level = await get_role_level(required)

    admin: Optional[dict] = None
    if phone:
        admin = await get_admin_by_phone(phone)
    if not admin and api_key:
        admin = await get_admin_by_api_key(api_key)

    if not admin:
        return {
            "allowed": False, "admin": None,
            "required_role": required, "reason": "unknown actor",
        }
    if admin["status"] != "active":
        return {
            "allowed": False, "admin": admin,
            "required_role": required, "reason": f"actor status={admin['status']}",
        }
    user_level = await max_role_level(admin["id"])
    if user_level < req_level:
        return {
            "allowed": False, "admin": admin,
            "required_role": required,
            "reason": f"need {required} (level {req_level}) have level {user_level}",
        }
    # touch last_seen_at (best effort)
    try:
        await execute(
            "UPDATE fazle_admins SET last_seen_at=now() WHERE id=$1", admin["id"],
        )
    except Exception:
        pass
    return {
        "allowed": True, "admin": admin,
        "required_role": required, "reason": "",
    }


# ── audit ────────────────────────────────────────────────────────────────────
async def record_audit(
    *, channel: str, command: str,
    actor_phone: Optional[str] = None,
    actor_admin: Optional[dict] = None,
    args: Optional[str] = None,
    allowed: bool,
    required_role: Optional[str] = None,
    denied_reason: Optional[str] = None,
    result_summary: Optional[str] = None,
) -> int:
    label = (actor_admin or {}).get("name") or actor_phone or "unknown"
    aid = (actor_admin or {}).get("id")
    rid = await fetch_val(
        "INSERT INTO fazle_admin_audit (actor_phone, actor_user_id, actor_label, "
        "channel, command, args, allowed, required_role, denied_reason, result_summary) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING id",
        actor_phone, aid, label, channel, command,
        (args[:4000] if args else None),
        allowed, required_role, denied_reason,
        (result_summary[:4000] if result_summary else None),
    )
    return int(rid)


async def list_audit(limit: int = 50, command: Optional[str] = None) -> list[dict]:
    if command:
        rows = await fetch_all(
            "SELECT * FROM fazle_admin_audit WHERE command=$1 "
            "ORDER BY created_at DESC LIMIT $2",
            command, limit,
        )
    else:
        rows = await fetch_all(
            "SELECT * FROM fazle_admin_audit ORDER BY created_at DESC LIMIT $1",
            limit,
        )
    return [dict(r) for r in rows]


# ── admin (user) management ──────────────────────────────────────────────────
async def add_admin(
    phone: str,
    name: str,
    role: str = "viewer",
    granted_by: str = "system",
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> dict[str, Any]:
    p = _normalize_phone(phone)
    if not p:
        raise ValueError("invalid phone")
    u = _normalize_username(username) if username else None
    if bool(u) != bool(password):
        raise ValueError("username and password must be provided together")
    if u:
        existing_username = await fetch_val("SELECT 1 FROM fazle_admins WHERE LOWER(username)=$1", u)
        if existing_username:
            raise ValueError(f"username already exists: {u}")
    exists = await get_admin_by_phone(p)
    if exists:
        if u and password:
            existing_username = _normalize_username(exists.get("username") or "")
            if existing_username and existing_username != u:
                raise ValueError("existing admin has different username")
            owner_id = await fetch_val("SELECT id FROM fazle_admins WHERE LOWER(username)=$1", u)
            if owner_id and int(owner_id) != int(exists["id"]):
                raise ValueError(f"username already exists: {u}")
            await execute(
                "UPDATE fazle_admins SET username=$1, password_hash=$2 WHERE id=$3",
                u,
                hash_password(password),
                exists["id"],
            )
            return {"status": "updated_credentials", "admin_id": exists["id"]}
        return {"status": "exists", "admin_id": exists["id"]}
    password_hash = hash_password(password) if password else None
    aid = await fetch_val(
        "INSERT INTO fazle_admins (phone, name, username, password_hash, status) "
        "VALUES ($1,$2,$3,$4,'active') RETURNING id",
        p, name, u, password_hash,
    )
    await execute(
        "INSERT INTO fazle_admin_roles (admin_id, role_name, granted_by) "
        "VALUES ($1,$2,$3)",
        aid, role, granted_by,
    )
    return {"status": "created", "admin_id": aid, "role": role}


async def set_role(
    phone: str, role: str, granted_by: str = "system",
) -> dict[str, Any]:
    a = await get_admin_by_phone(phone)
    if not a:
        raise ValueError("unknown admin")
    # validate role exists
    if not await fetch_val("SELECT 1 FROM fazle_roles WHERE name=$1", role):
        raise ValueError(f"unknown role: {role}")
    await execute(
        "INSERT INTO fazle_admin_roles (admin_id, role_name, granted_by) "
        "VALUES ($1,$2,$3) ON CONFLICT (admin_id, role_name) DO NOTHING",
        a["id"], role, granted_by,
    )
    return {"status": "ok", "admin_id": a["id"], "role": role}


async def revoke_role(phone: str, role: str) -> dict[str, Any]:
    a = await get_admin_by_phone(phone)
    if not a:
        raise ValueError("unknown admin")
    await execute(
        "DELETE FROM fazle_admin_roles WHERE admin_id=$1 AND role_name=$2",
        a["id"], role,
    )
    return {"status": "ok", "admin_id": a["id"], "role": role}


async def disable_admin(phone: str) -> dict[str, Any]:
    a = await get_admin_by_phone(phone)
    if not a:
        raise ValueError("unknown admin")
    await execute("UPDATE fazle_admins SET status='disabled' WHERE id=$1", a["id"])
    return {"status": "ok", "admin_id": a["id"]}


async def issue_api_key(phone: str) -> dict[str, Any]:
    """Generate a new API key, store its hash, return the plaintext (once)."""
    a = await get_admin_by_phone(phone)
    if not a:
        raise ValueError("unknown admin")
    key = "fk_" + secrets.token_urlsafe(32)
    h = hash_api_key(key)
    await execute(
        "UPDATE fazle_admins SET api_key_hash=$1 WHERE id=$2", h, a["id"],
    )
    return {"status": "ok", "admin_id": a["id"], "api_key": key}


async def list_admins() -> list[dict]:
    rows = await fetch_all(
        "SELECT a.id, a.phone, a.name, a.username, a.status, a.created_at, a.last_seen_at, "
        "       COALESCE(string_agg(ar.role_name, ',' ORDER BY r.level DESC), '') AS roles "
        "FROM fazle_admins a "
        "LEFT JOIN fazle_admin_roles ar ON ar.admin_id = a.id "
        "LEFT JOIN fazle_roles r ON r.name = ar.role_name "
        "GROUP BY a.id ORDER BY a.id"
    )
    return [dict(r) for r in rows]
