"""
modules/identity_resolver — Phase-2 canonical inbound-identity resolver
=========================================================================

Owner-authoritative identity principle (2026-08-31):
  "For WhatsApp identity, the normalized WhatsApp mobile number is the
   primary identity key. Names are supporting/display evidence only.
   Never identify a person primarily by name when a WhatsApp phone number
   is available."

This module is the SINGLE shared identity helper used by:
  * bridge poller (modules.bridge_poller._poll_bridge)
  * bridge1/2 webhook handler (app.main._handle_bridge_event)
  * Meta WhatsApp webhook handler (app.main._handle_meta_message)
  * MCP / Hermes read tools (audit_lookup_whatsapp_messages etc.)

It deliberately exposes `identity_phone` as an explicit, normalized lookup
key so callers (including Hermes) never have to decide between
`sender_number`, `canonical_phone`, `phone_last10`, `contact_identifier`,
LID, or JID — they all flow through this one resolver and get ONE
authoritative `identity_phone` back.

Inputs are intentionally permissive: callers pass whatever they have
(sender_number, canonical_phone, resolved_phone, LID, chat JID, push-name,
business-name, Meta profile name, message text for ad-hoc name search).
Output is a flat dict with the contract documented at resolve_inbound_identity().

The phone-based precedence itself is implemented here, NOT in identity_brain,
to make the new contract explicit and testable. identity_brain.detect_identity()
remains the canonical role-resolution engine for downstream callers that
want its richer role priority / trust level / notes model — this resolver
is a thin, opinionated wrapper that adds the Phase-2 fields (identity_phone,
trusted_identity_name, sender_name_snapshot/_source/_confidence, employee_id,
contact_id) without altering identity_brain's behavior.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

from app.database import fetch_one, fetch_all
from app.config import get_settings
from modules.phone_normalizer import normalize_phone, to_ascii_digits

log = logging.getLogger("fazle.identity_resolver")


def is_phase2_identity_enabled() -> bool:
    """Master switch for the Phase-2 inbound-identity enrichment (identity_phone
    resolution, sender_name snapshot, canonical_message_key). Default OFF — when
    OFF, every ingestion path persists rows exactly as it did before Phase-2.
    Same env-flag convention as BRIDGE_MEDIA_ACQUISITION_ENABLED /
    PAYMENT_RELAY_ACCOUNTING_ENABLED (read at the call site, not via app.config)."""
    return os.getenv("PHASE2_IDENTITY_RESOLVER_ENABLED", "false").strip().lower() in (
        "1", "true", "yes", "on",
    )


# WhatsApp-only platforms — Phase-2 identity enrichment runs for inbound rows on
# these sources only. Messenger / Instagram / Facebook-comment senders are PSIDs,
# never phone numbers, and must never flow through the phone resolver.
WHATSAPP_PLATFORMS = frozenset({"bridge1", "bridge2", "bridge3", "meta", "whatsapp"})

# Sender-name provenance enum (mirrors the CHECK constraint on
# wbom_whatsapp_messages.sender_name_source).
SOURCE_EMPLOYEE = "employee"
SOURCE_WBOM_CONTACTS = "wbom_contacts"
SOURCE_IDENTITY_BRAIN = "identity_brain"
SOURCE_WHATSMEOW_FULL = "whatsmeow_full_name"
SOURCE_WHATSMEOW_PUSH = "whatsmeow_push_name"
SOURCE_WHATSMEOW_BUSINESS = "whatsmeow_business_name"
SOURCE_META_PROFILE = "meta_profile"
SOURCE_SOCIAL_INBOX = "social_inbox"
SOURCE_UNKNOWN = "unknown"

_VALID_SOURCES = frozenset({
    SOURCE_EMPLOYEE, SOURCE_WBOM_CONTACTS, SOURCE_IDENTITY_BRAIN,
    SOURCE_WHATSMEOW_FULL, SOURCE_WHATSMEOW_PUSH, SOURCE_WHATSMEOW_BUSINESS,
    SOURCE_META_PROFILE, SOURCE_SOCIAL_INBOX, SOURCE_UNKNOWN,
})


# ── Helpers ────────────────────────────────────────────────────────────────────

_LID_RE = re.compile(r"^unresolved:(\d+)$")


def _is_unresolved_lid(value: str | None) -> bool:
    """True when sender_number is the LID-unresolved sentinel
    `unresolved:<digits>`. Never treat this as a phone number."""
    if not value:
        return False
    return bool(_LID_RE.match(value.strip()))


_COLUMN_CACHE: dict[str, frozenset[str]] = {}


async def available_columns(table_name: str) -> frozenset[str]:
    """Column-name lookup against information_schema, cached for the life of
    the process (a table's column set does not change under a running
    fazle-core — migrations require a restart). Used by schema-tolerant
    queries: production schema has columns older test DBs may lack
    (is_protected, processed_at, fpe_employees.phone, ...).

    Call ``reset_column_cache()`` in a test that mutates schema mid-process."""
    cached = _COLUMN_CACHE.get(table_name)
    if cached is not None:
        return cached
    cols = frozenset(
        r["column_name"] for r in await fetch_all(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=$1",
            table_name,
        )
    )
    # Only cache a non-empty result — an empty set usually means the pool
    # wasn't ready yet, not that the table has no columns.
    if cols:
        _COLUMN_CACHE[table_name] = cols
    return cols


def reset_column_cache() -> None:
    """Test hook — drop the per-process column cache after a schema change."""
    _COLUMN_CACHE.clear()


def _norm(value: str | None) -> Optional[str]:
    """Return modules.phone_normalizer.normalize_phone() result, or None.
    Strips a `unresolved:<digits>` sentinel so callers don't accidentally
    hand a LID to the normalizer."""
    if not value:
        return None
    if _is_unresolved_lid(value):
        return None
    return normalize_phone(value)


# ── Public: identity_phone selection ────────────────────────────────────────────

def compute_identity_phone(
    *,
    sender_number: str | None = None,
    canonical_phone: str | None = None,
    resolved_phone: str | None = None,
    sender_lid: str | None = None,
) -> Optional[str]:
    """
    Select the single authoritative `identity_phone` for a WhatsApp sender.

    Precedence (exactly the contract specified by the Boss, 2026-08-31):
      1. canonical_phone  (if it normalizes to a valid 8801XXXXXXXXX)
      2. resolved_phone    (later LID resolution result, if valid)
      3. sender_number     (if it normalizes to a valid 8801XXXXXXXXX)
      4. sender_lid        (last resort: digits-only, prefixed `lid:` so it's
                            obvious this is NOT a phone but a stable handle
                            for grouping cross-bridge duplicates)

    Rules:
      - Never return `unresolved:<digits>` as identity_phone.
      - Never include `+`, spaces, dashes, or JID suffix.
      - Return None when no usable value exists.
    """
    for candidate in (canonical_phone, resolved_phone, sender_number):
        norm = _norm(candidate)
        if norm:
            return norm
    if sender_lid:
        digits = re.sub(r"\D", "", to_ascii_digits(sender_lid))
        if digits:
            return f"lid:{digits}"
    return None


# ── Public: sender-name resolution ──────────────────────────────────────────────

async def _resolve_protected_config_role(phone: str) -> Optional[dict]:
    """Tier 1 — environment-configured protected numbers.
    Reads ONLY the already-supported phone/role settings; never reads raw
    env, never returns secret values. Returns dict dict or None."""
    if not phone:
        return None
    s = get_settings()
    # Order matters: more specific (admin_meta_number, admin_bridge2_number)
    # first so admin assignments are precise.
    candidates: list[tuple[str, str]] = []
    if s.admin_meta_number:
        candidates.append((_norm(s.admin_meta_number) or s.admin_meta_number, "admin"))
    if s.admin_bridge2_number:
        candidates.append((_norm(s.admin_bridge2_number) or s.admin_bridge2_number, "admin"))
    if s.admin_bridge1_number:
        candidates.append((_norm(s.admin_bridge1_number) or s.admin_bridge1_number, "admin"))
    if s.admin_bridge3_number:
        candidates.append((_norm(s.admin_bridge3_number) or s.admin_bridge3_number, "admin"))
    for admin_number in (s.admin_number_list or []):
        candidates.append((_norm(admin_number) or admin_number, "admin"))
    if s.accountant_phone:
        candidates.append((_norm(s.accountant_phone) or s.accountant_phone, "accountant"))

    for cfg_phone, role in candidates:
        if cfg_phone and cfg_phone == phone:
            return {
                "identity_role": role,
                "identity_confidence": 100,
                "identity_source": "protected_config",
                "trusted_identity_name": None,
                "employee_id": None,
                "contact_id": None,
            }
    return None


async def _lookup_employee(phone: str) -> Optional[dict]:
    """Tier 2a — wbom_employees.employee_mobile (UNIQUE). Returns active AND
    inactive rows; the resolver never creates new rows. Mirrors the
    identity_brain contract.

    Schema-tolerant: production schema has bkash_number + nagad_number;
    older test DBs may have one, both, or neither. We add OR-clauses only
    for columns that actually exist so the query is portable.
    """
    if not phone:
        return None
    available = await available_columns("wbom_employees")
    where_clauses = [
        "employee_mobile = $1",
        "employee_mobile = $2",
    ]
    if "bkash_number" in available:
        where_clauses.append("bkash_number = $1")
    if "nagad_number" in available:
        where_clauses.append("nagad_number = $1")
    sql = f"""SELECT employee_id, employee_name, designation, status
           FROM wbom_employees
           WHERE {' OR '.join(where_clauses)}
           ORDER BY (employee_mobile = $1) DESC, employee_id DESC
           LIMIT 1"""
    alt = "0" + phone[3:] if phone.startswith("880") and len(phone) == 13 else phone
    row = await fetch_one(sql, phone, alt)
    if not row:
        return None
    return {
        "identity_role": _designation_to_role(row.get("designation"), default="employee"),
        "identity_confidence": 100,
        "identity_source": "wbom_employees",
        "trusted_identity_name": row.get("employee_name"),
        "employee_id": row.get("employee_id"),
        "contact_id": None,
        "identity_lifecycle": (row.get("status") or "active").lower(),
    }


def _designation_to_role(designation: str | None, default: str = "employee") -> str:
    """Mirror identity_brain._designation_to_role without re-importing it
    (that module pulls a lot of transitive deps)."""
    d = (designation or "").strip()
    if not d:
        return default
    if re.search(r"escort", d, re.IGNORECASE):
        return "escort"
    if re.search(r"guard", d, re.IGNORECASE):
        return "security_guard"
    return "employee"


async def _lookup_fpe_employee(phone: str) -> Optional[dict]:
    """Tier 2b — fpe_employees (Fazle Payroll Engine mirror).

    Schema-tolerant: production schema has `phone` + `name`; older test
    DBs use `primary_phone` + `full_name`. We try both shapes.
    """
    if not phone:
        return None
    available = await available_columns("fpe_employees")
    if not available:
        return None
    phone_col = "phone" if "phone" in available else ("primary_phone" if "primary_phone" in available else None)
    if not phone_col:
        return None
    name_col = "name" if "name" in available else ("full_name" if "full_name" in available else None)
    role_col = "role" if "role" in available else ("designation" if "designation" in available else None)
    status_col = "status" if "status" in available else None
    wbom_col = "wbom_employee_id" if "wbom_employee_id" in available else ("canonical_employee_id" if "canonical_employee_id" in available else None)
    select_fields = [phone_col + " AS phone"]
    if name_col:
        select_fields.append(f"{name_col} AS name")
    if role_col:
        select_fields.append(f"{role_col} AS role")
    if status_col:
        select_fields.append(f"{status_col} AS status")
    if wbom_col:
        select_fields.append(f"{wbom_col} AS wbom_employee_id")
    sql = f"""SELECT {', '.join(select_fields)}
           FROM fpe_employees
           WHERE {phone_col} = $1
           ORDER BY id DESC LIMIT 1"""
    row = await fetch_one(sql, phone)
    if not row:
        return None
    return {
        "identity_role": row.get("role") or "employee",
        "identity_confidence": 95,
        "identity_source": "fpe_employees",
        "trusted_identity_name": row.get("name"),
        "employee_id": row.get("wbom_employee_id"),
        "contact_id": None,
        "identity_lifecycle": (row.get("status") or "active").lower(),
    }


async def _lookup_contact_role(phone: str) -> Optional[dict]:
    """Tier 2c — fazle_contact_roles. (phone, platform) is UNIQUE, so at most
    one whatsapp row per phone; the ORDER BY only matters if that constraint
    is ever missing. `is_active = true` is deliberate: an INACTIVE role row
    confers no current role authority. Identity *existence* for an inactive
    contact is still covered by _lookup_employee / _lookup_wbom_contact
    (which returns inactive rows marked as such). `id` is the final,
    fully-deterministic tie-break."""
    if not phone:
        return None
    row = await fetch_one(
        """SELECT role, sub_role, confidence, name, source, priority
           FROM fazle_contact_roles
           WHERE phone = $1 AND platform = 'whatsapp' AND is_active = true
           ORDER BY priority DESC, confidence DESC, id
           LIMIT 1""",
        phone,
    )
    if not row:
        return None
    return {
        "identity_role": row.get("role") or "unknown",
        "identity_confidence": int(row.get("confidence") or 0),
        "identity_source": f"fazle_contact_roles:{row.get('source') or 'seed_rule'}",
        "trusted_identity_name": row.get("name") or None,
        "employee_id": None,
        "contact_id": None,
        "identity_priority": int(row.get("priority") or 50),
    }


async def _lookup_wbom_contact(phone: str) -> Optional[dict]:
    """Tier 3 — wbom_contacts. JOIN-key is (whatsapp_number, platform='whatsapp').
    UNIQUE index enforces at most one row per (number, platform).

    Schema-tolerant: queries are built dynamically against the actual
    columns present in wbom_contacts (older test DB schemas may lack
    is_protected, personality_hint, etc.). The boss's authoritative rule
    "phone number is the primary identity key" holds regardless.
    """
    if not phone:
        return None
    available = await available_columns("wbom_contacts")
    select_cols = ["contact_id", "display_name"]
    if "relation" in available:
        select_cols.append("relation")
    else:
        select_cols.append("NULL::varchar AS relation")
    if "is_protected" in available:
        select_cols.append("is_protected")
    else:
        select_cols.append("false AS is_protected")
    if "is_active" in available:
        select_cols.append("is_active")
    else:
        select_cols.append("true AS is_active")
    sql = f"""SELECT {', '.join(select_cols)}
           FROM wbom_contacts
           WHERE whatsapp_number = $1 AND platform = 'whatsapp'
           LIMIT 1"""
    row = await fetch_one(sql, phone)
    if not row:
        return None
    if row.get("is_active") is False:
        # Keep identity-matchable but mark inactive.
        return {
            "identity_role": _map_relation(row.get("relation") or "unknown"),
            "identity_confidence": 70,
            "identity_source": "wbom_contacts:inactive",
            "trusted_identity_name": row.get("display_name") or None,
            "employee_id": None,
            "contact_id": row.get("contact_id"),
            "identity_lifecycle": "inactive",
        }
    is_protected = bool(row.get("is_protected"))
    return {
        "identity_role": _map_relation(row.get("relation") or "unknown"),
        "identity_confidence": 95 if is_protected else 80,
        "identity_source": "wbom_contacts" + (":protected" if is_protected else ""),
        "trusted_identity_name": row.get("display_name") or None,
        "employee_id": None,
        "contact_id": row.get("contact_id"),
    }


def _map_relation(relation: str) -> str:
    """Mirror identity_brain._map_relation."""
    r = (relation or "").lower()
    if r in ("escort_client", "escort client", "escort buyer", "client_escort_buyer"):
        return "client_escort_buyer"
    if r in ("vip", "vip_client"):
        return "vip_client"
    if r == "vendor":
        return "vendor"
    if r == "accountant":
        return "accountant"
    if r in ("employee", "guard", "staff"):
        return "employee"
    if r in ("supervisor", "manager"):
        return "supervisor"
    if r == "family":
        return "family"
    return "repeat_client"


# ── Sender-name snapshot precedence (Phase-2) ───────────────────────────────────

def _best_name(
    *,
    identity_phone: str | None,
    emp: Optional[dict] = None,
    contact: Optional[dict] = None,
    fpe: Optional[dict] = None,
    fcr: Optional[dict] = None,
    bridge_full_name: str | None = None,
    bridge_push_name: str | None = None,
    bridge_business_name: str | None = None,
    meta_profile_name: str | None = None,
    social_inbox_name: str | None = None,
) -> tuple[Optional[str], Optional[str], int]:
    """
    Returns (sender_name_snapshot, sender_name_source, sender_name_confidence).

    Pure function — the trusted-source rows (`emp`, `contact`, `fpe`, `fcr`)
    are fetched ONCE by resolve_inbound_identity() and passed in, so the tier
    chain is never run twice per message.

    Precedence (highest trust first):
      1. trusted employee name from wbom_employees / fpe_employees   (100)
      2. wbom_contacts.display_name (protected=95, active=80, inactive=70)
      3. fazle_contact_roles.name                                    (its own confidence)
      4. meta_profile               (50; reasonably trusted, mutable)
      5. whatsmeow_full_name        (40)   — display metadata only
      6. whatsmeow_push_name        (30)   — display metadata only
      7. social_inbox               (30)
      8. whatsmeow_business_name    (20)   — display metadata only
      9. unknown / NULL

    A sender-supplied display name (bridge_*/meta_profile/social_inbox) can
    NEVER outrank a trusted DB name and never sets identity_role.
    """
    if not identity_phone:
        return None, SOURCE_UNKNOWN, 0

    if emp and emp.get("trusted_identity_name"):
        return emp["trusted_identity_name"], SOURCE_EMPLOYEE, 100

    if contact and contact.get("trusted_identity_name"):
        return (
            contact["trusted_identity_name"],
            SOURCE_WBOM_CONTACTS,
            int(contact.get("identity_confidence", 80)),
        )

    if fpe and fpe.get("trusted_identity_name"):
        return fpe["trusted_identity_name"], SOURCE_EMPLOYEE, 100

    if fcr and fcr.get("trusted_identity_name"):
        return (
            fcr["trusted_identity_name"],
            SOURCE_IDENTITY_BRAIN,
            int(fcr.get("identity_confidence") or 0),
        )

    # Display metadata only — never authoritative identity.
    if meta_profile_name:
        return meta_profile_name, SOURCE_META_PROFILE, 50
    if bridge_full_name:
        return bridge_full_name, SOURCE_WHATSMEOW_FULL, 40
    if bridge_push_name:
        return bridge_push_name, SOURCE_WHATSMEOW_PUSH, 30
    if social_inbox_name:
        return social_inbox_name, SOURCE_SOCIAL_INBOX, 30
    if bridge_business_name:
        return bridge_business_name, SOURCE_WHATSMEOW_BUSINESS, 20

    return None, SOURCE_UNKNOWN, 0


# ── Public: canonical-message-key ──────────────────────────────────────────────

def compute_canonical_message_key(
    *,
    platform: str,
    source_message_ref: str | None,
    sender_phone: str | None,
    chat_jid: str | None,
    message_type: str | None = None,
    # accepted-but-ignored (kept for call-site stability): the key is derived
    # from the transport message ID only — see the docstring.
    source_timestamp_iso: str | None = None,
    content: str | None = None,
    media_fingerprint: str | None = None,
) -> Optional[str]:
    """
    Generate a stable cross-source canonical key for an inbound message.

    **FORENSIC / OBSERVABILITY ONLY.** This key is written to
    ``wbom_whatsapp_messages.canonical_message_key`` so an operator can group
    the copies of one physical message observed on multiple bridges. It is
    NOT a suppression mechanism: nothing in fazle-core reads this column to
    gate a business action, and it must never be wired into
    ``process_bridge_inbound`` or any payment / admin / attendance / recruitment
    idempotency check. Transport-row dedup stays on ``message_hash`` (UNIQUE);
    business idempotency stays on the specific transaction / stanza key.

    Key is derived from a real transport message ID only:
      - Meta:   ``meta:wamid:<wamid>``   (globally unique)
      - Bridge: ``bridge:msgid:<sender10>:<chat>:<wa-msg-id>``
    Reactions/receipts/revokes/deleted/protocol get no key. A message with no
    transport ID at all gets no key (returns None) — the previous
    content+60s-bucket heuristic was removed 2026-09-01 because every wired
    ingestion path supplies a transport ID and a fuzzy fallback risked
    collapsing legitimate identical resends.
    """
    if not platform:
        return None
    platform = platform.strip().lower()

    msg_type = (message_type or "").strip().lower()
    if msg_type in {"reaction", "receipt", "revoke", "deleted", "protocol"}:
        return None

    if not source_message_ref:
        return None
    ref_seg = re.sub(r"\s+", "", source_message_ref).lower()
    if not ref_seg:
        return None

    if platform == "meta":
        return f"meta:wamid:{ref_seg}"

    sender_seg = _sender_key_segment(sender_phone)
    if sender_seg is None:
        return None
    chat_seg = (chat_jid or "").strip().lower() or "unknown_chat"
    return f"bridge:msgid:{sender_seg}:{chat_seg}:{ref_seg}"


def _sender_key_segment(phone: str | None) -> Optional[str]:
    if not phone:
        return None
    if _is_unresolved_lid(phone):
        digits = re.sub(r"\D", "", to_ascii_digits(phone))
        return f"lid:{digits}" if digits else None
    norm = normalize_phone(phone)
    if norm:
        return norm[-10:] if len(norm) >= 10 else norm
    digits = re.sub(r"\D", "", to_ascii_digits(phone))
    return digits[-10:] if len(digits) >= 10 else (digits or None)


# ── Public: resolve_inbound_identity ────────────────────────────────────────────

async def resolve_inbound_identity(
    *,
    platform: str,
    sender_number: str | None = None,
    canonical_phone: str | None = None,
    resolved_phone: str | None = None,
    sender_lid: str | None = None,
    chat_jid: str | None = None,
    message_body: str | None = None,
    source_message_ref: str | None = None,
    source_timestamp: str | None = None,
    media_fingerprint: str | None = None,
    message_type: str | None = None,
    bridge_full_name: str | None = None,
    bridge_push_name: str | None = None,
    bridge_business_name: str | None = None,
    meta_profile_name: str | None = None,
    social_inbox_name: str | None = None,
) -> dict:
    """
    The ONE canonical inbound-identity resolver. Used by every ingestion
    path (poller / bridge webhook / Meta webhook) and by Hermes read tools
    that reconstruct identity context for a stored row.

    Returns a flat dict — see schema at top of module. Never raises; on
    any unexpected error returns the minimal `{identity_phone, identity_role:
    unknown, identity_source: 'unknown', identity_confidence: 0}` shape.
    """
    # compute_identity_phone is pure (no DB) — do it OUTSIDE the try so a
    # later DB error in a tier lookup can never discard an already-correct
    # normalized identity phone.
    identity_phone = compute_identity_phone(
        sender_number=sender_number,
        canonical_phone=canonical_phone,
        resolved_phone=resolved_phone,
        sender_lid=sender_lid,
    )

    # canonical_message_key is also pure — compute it up front so it survives
    # a DB failure too.
    canonical_key = compute_canonical_message_key(
        platform=platform,
        source_message_ref=source_message_ref,
        sender_phone=identity_phone,
        chat_jid=chat_jid,
        source_timestamp_iso=source_timestamp,
        content=message_body,
        media_fingerprint=media_fingerprint,
        message_type=message_type,
    )

    identity_role = "unknown"
    identity_confidence = 0
    identity_source = "unknown"
    trusted_identity_name = None
    employee_id = None
    contact_id = None
    identity_lifecycle = None
    snapshot, snap_source, snap_conf = None, SOURCE_UNKNOWN, 0

    try:
        p = identity_phone or ""
        # Fetch every phone-keyed source EXACTLY ONCE (was previously run
        # twice — once for role, once inside _best_name).
        cfg = await _resolve_protected_config_role(p) if p else None
        emp = await _lookup_employee(p) if p else None
        fpe = await _lookup_fpe_employee(p) if p else None
        fcr = await _lookup_contact_role(p) if p else None
        ctc = await _lookup_wbom_contact(p) if p else None

        # Role precedence: protected_config > employee > fpe > contact_role > wbom_contact.
        role_pick = cfg or emp or fpe or fcr or ctc
        if role_pick:
            identity_role = role_pick["identity_role"]
            identity_confidence = role_pick["identity_confidence"]
            identity_source = role_pick["identity_source"]
            trusted_identity_name = role_pick.get("trusted_identity_name") or None
            identity_lifecycle = role_pick.get("identity_lifecycle")
        # Cross-reference ids from whichever source has them (e.g. admin from
        # protected-config who is also a known employee).
        employee_id = (
            (role_pick or {}).get("employee_id")
            or (emp or {}).get("employee_id")
            or (fpe or {}).get("employee_id")
        )
        contact_id = (role_pick or {}).get("contact_id") or (ctc or {}).get("contact_id")

        # Name precedence (see _best_name) — reuses the rows already fetched.
        snapshot, snap_source, snap_conf = _best_name(
            identity_phone=identity_phone,
            emp=emp, contact=ctc, fpe=fpe, fcr=fcr,
            bridge_full_name=bridge_full_name,
            bridge_push_name=bridge_push_name,
            bridge_business_name=bridge_business_name,
            meta_profile_name=meta_profile_name,
            social_inbox_name=social_inbox_name,
        )
    except Exception as exc:
        log.warning("identity_resolver.resolve_inbound_identity non-fatal: %s", exc)
        # identity_phone / canonical_key are preserved (computed above); only
        # the DB-derived fields fall back to the unknown shape.

    return {
        "identity_phone": identity_phone,
        "identity_role": identity_role,
        "identity_confidence": int(identity_confidence),
        "identity_source": identity_source,
        "trusted_identity_name": trusted_identity_name,
        "sender_name_snapshot": snapshot,
        "sender_name_source": snap_source,
        "sender_name_confidence": int(snap_conf),
        "employee_id": employee_id,
        "contact_id": contact_id,
        "identity_lifecycle": identity_lifecycle,
        "canonical_message_key": canonical_key,
    }


# ── Public: name-based admin search (for Hermes) ───────────────────────────────

async def search_identity_by_name(
    name_query: str,
    *,
    limit: int = 10,
) -> list[dict]:
    """
    Search trusted identity/contact sources by name. Returns a list of
    `{phone, display_name, source, role, employee_id, contact_id}` rows.

    Used by Hermes for admin queries like 'Rahim এর message দেখাও' — when
    the query is by name, resolve candidate phones FIRST, then perform the
    message lookup by phone. Caller is responsible for ambiguity handling.

    Sources searched (in priority order, results dedup'd by phone):
      1. wbom_employees.employee_name  (employee_id, trusted)
      2. wbom_contacts.display_name     (contact_id)
      3. fazle_contact_roles.name
      4. fpe_employees.name
    """
    if not name_query or not name_query.strip():
        return []
    needle = name_query.strip()
    rows: list[dict] = []

    emp_rows = await fetch_all(
        """SELECT employee_id, employee_mobile AS phone, employee_name AS display_name,
                  designation
           FROM wbom_employees
           WHERE employee_name ILIKE $1
           ORDER BY (LOWER(status)='active') DESC, employee_id DESC
           LIMIT $2""",
        f"%{needle}%", limit,
    )
    for r in emp_rows:
        rows.append({
            "phone": r.get("phone"),
            "display_name": r.get("display_name"),
            "source": "wbom_employees",
            "role": _designation_to_role(r.get("designation"), default="employee"),
            "employee_id": r.get("employee_id"),
            "contact_id": None,
        })

    # Schema-tolerant contact query.
    contact_available = await available_columns("wbom_contacts")
    select_contact = ["contact_id", "whatsapp_number AS phone", "display_name"]
    if "relation" in contact_available:
        select_contact.append("relation")
    if "is_protected" in contact_available:
        select_contact.append("is_protected")
    order_contact = ["contact_id DESC"]
    if "is_protected" in contact_available:
        order_contact = ["is_protected DESC"] + order_contact
    _contact_platform_pred = (
        "AND COALESCE(platform, 'whatsapp') = 'whatsapp'"
        if "platform" in contact_available else ""
    )
    contact_sql = f"""SELECT {', '.join(select_contact)}
                      FROM wbom_contacts
                      WHERE display_name ILIKE $1
                      {_contact_platform_pred}
                      ORDER BY {', '.join(order_contact)}
                      LIMIT $2"""
    contact_rows = await fetch_all(contact_sql, f"%{needle}%", limit)
    for r in contact_rows:
        rows.append({
            "phone": r.get("phone"),
            "display_name": r.get("display_name"),
            "source": "wbom_contacts",
            "role": _map_relation(r.get("relation") or "unknown"),
            "employee_id": None,
            "contact_id": r.get("contact_id"),
        })

    fcr_rows = await fetch_all(
        """SELECT role, name AS display_name, phone
           FROM fazle_contact_roles
           WHERE name ILIKE $1 AND is_active = true
             AND COALESCE(platform, 'whatsapp') = 'whatsapp'
           ORDER BY priority DESC, id LIMIT $2""",
        f"%{needle}%", limit,
    )
    for r in fcr_rows:
        rows.append({
            "phone": r.get("phone"),
            "display_name": r.get("display_name"),
            "source": "fazle_contact_roles",
            "role": r.get("role"),
            "employee_id": None,
            "contact_id": None,
        })

    # Schema-tolerant fpe_employees query.
    fpe_available = await available_columns("fpe_employees")
    if fpe_available:
        phone_col = "phone" if "phone" in fpe_available else "primary_phone"
        name_col = "name" if "name" in fpe_available else "full_name"
        role_col = "role" if "role" in fpe_available else "designation"
        fpe_sql = f"""SELECT id, {phone_col} AS phone, {name_col} AS display_name, {role_col} AS role
                       FROM fpe_employees
                       WHERE {name_col} ILIKE $1
                       ORDER BY id DESC LIMIT $2"""
        fpe_rows = await fetch_all(fpe_sql, f"%{needle}%", limit)
        for r in fpe_rows:
            rows.append({
                "phone": r.get("phone"),
                "display_name": r.get("display_name"),
                "source": "fpe_employees",
                "role": r.get("role") or "employee",
                "employee_id": None,
                "contact_id": None,
            })

    # Deduplicate by phone, keep first occurrence (priority order).
    seen: dict[str, dict] = {}
    for r in rows:
        phone = r.get("phone")
        if not phone:
            continue
        key = phone
        if key not in seen:
            seen[key] = r
    return list(seen.values())[:limit]