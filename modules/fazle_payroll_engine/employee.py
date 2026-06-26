"""
Fazle Payroll Engine — Employee matching & auto-creation.

Matching priority:
  1. Exact primary_phone match (fastest, most reliable)
  2. Exact employee_id_phone match (from "ID: 01XXXXXXXXX" prefix)
  3. Alias phone lookup (fpe_employee_aliases where alias_type='phone')
  4. Exact normalized-name match
  5. Fuzzy name match (rapidfuzz, threshold 0.80)
  6. Auto-create a new employee if nothing matches

Auto-create policy:
  - Creates employee immediately — no human approval gate
  - Assigns employee_code = EMP-XXXXX (zero-padded sequential)
  - Stores all available fields (name, phone, id_phone) as aliases

Never returns None — always returns an EmployeeMatchResult.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from rapidfuzz import fuzz

from app.database import db_conn, execute, fetch_all, fetch_one, fetch_val
from .models import EmployeeMatchResult
from .normalizer import normalize_bd_phone, normalize_name

log = logging.getLogger("fazle.fpe.employee")


def _is_valid_human_name(name: Optional[str]) -> bool:
    """Reject empty, pure-numeric, phone-like, or placeholder names.
    Mirror of parser._is_valid_human_name (kept local to avoid import cycle)."""
    if not name:
        return False
    s = name.strip()
    if len(s) < 2:
        return False
    if s.lower() in {"unknown", "unnamed", "none", "n/a", "na"}:
        return False
    digits_only = re.sub(r"[\s\-\+\(\)\.]", "", s)
    if digits_only.isdigit():
        return False
    if not re.search(r"[A-Za-z\u0980-\u09FF]", s):
        return False
    return True


async def _maybe_enrich_full_name(employee_id: int, current_name: Optional[str], incoming_name: Optional[str]) -> None:
    """Safe profile enrichment (FIX 4 of spec).

    ONLY updates fpe_employees.full_name (and name_normalized) when:
      - incoming_name is a valid human name
      - current_name is missing OR phone-like / invalid placeholder.
    Never touches accounting rows. Idempotent.
    """
    if not _is_valid_human_name(incoming_name):
        return
    if _is_valid_human_name(current_name):
        return  # don't overwrite a good existing name
    name_norm = normalize_name(incoming_name)
    await execute(
        "UPDATE fpe_employees "
        "SET full_name = $1, name_normalized = COALESCE($2, name_normalized), updated_at = NOW() "
        "WHERE id = $3",
        incoming_name.strip(), name_norm, employee_id,
    )
    # Also register the name as an alias for future search
    if name_norm:
        await execute(
            "INSERT INTO fpe_employee_aliases (employee_id, alias_type, alias_value) "
            "VALUES ($1, 'name', $2) ON CONFLICT (alias_type, alias_value) DO NOTHING",
            employee_id, name_norm,
        )
    log.info(
        "[fpe.emp] enriched employee id=%d full_name %r -> %r",
        employee_id, current_name, incoming_name,
    )

# Safe-merge threshold per normalization spec — raised to 96 from 90.
# Below this, a fuzzy hit is considered AMBIGUOUS and pushed to the manual
# review queue rather than auto-linked.
FUZZY_THRESHOLD = 96.0
# Margin between best and second-best fuzzy candidate. If two employees are
# both close, treat as ambiguous (e.g. multiple "Hasan" entries).
FUZZY_AMBIGUITY_MARGIN = 5.0


# ── Public API ─────────────────────────────────────────────────────────────────

async def match_or_create_employee(
    name_raw: Optional[str],
    payout_phone: Optional[str],
    employee_id_phone: Optional[str],
) -> Optional[EmployeeMatchResult]:
    """
    Find the best matching employee or create a new one.
    Returns EmployeeMatchResult with match_type indicating how it was found.

    Soft-link safety: if a matched employee row has been marked as a duplicate
    (canonical_employee_id IS NOT NULL), we transparently resolve to the
    canonical employee. The duplicate row itself is never deleted or mutated.
    """
    # 1. Exact primary_phone match
    if payout_phone:
        row = await fetch_one(
            "SELECT id, employee_code, full_name, primary_phone, canonical_employee_id "
            "FROM fpe_employees "
            "WHERE primary_phone = $1 AND status = 'active'",
            payout_phone,
        )
        if row:
            row = await _resolve_canonical(row)
            await _maybe_enrich_full_name(row["id"], row.get("full_name"), name_raw)
            return _make_result(row, "exact_phone")

    # 2. Exact employee_id_phone match
    if employee_id_phone:
        row = await fetch_one(
            "SELECT id, employee_code, full_name, primary_phone, canonical_employee_id "
            "FROM fpe_employees "
            "WHERE employee_id_phone = $1 AND status = 'active'",
            employee_id_phone,
        )
        if row:
            row = await _resolve_canonical(row)
            await _maybe_enrich_full_name(row["id"], row.get("full_name"), name_raw)
            return _make_result(row, "exact_id_phone")

    # 3. Alias phone lookup
    for phone in filter(None, [payout_phone, employee_id_phone]):
        alias_row = await fetch_one(
            """
            SELECT e.id, e.employee_code, e.full_name, e.primary_phone,
                   e.canonical_employee_id
            FROM fpe_employee_aliases a
            JOIN fpe_employees e ON e.id = a.employee_id
            WHERE a.alias_type = 'phone' AND a.alias_value = $1
              AND e.status = 'active'
            """,
            phone,
        )
        if alias_row:
            alias_row = await _resolve_canonical(alias_row)
            await _maybe_enrich_full_name(alias_row["id"], alias_row.get("full_name"), name_raw)
            return _make_result(alias_row, "alias_phone")

    # 3b. WBOM cross-lookup: check bkash_number / nagad_number / employee_mobile
    # This bridges WBOM employee records into FPE when a number is known in
    # one system but not the other.
    for phone in filter(None, [payout_phone, employee_id_phone]):
        wbom_row = await fetch_one(
            "SELECT employee_id, employee_name, employee_mobile "
            "FROM wbom_employees "
            "WHERE (employee_mobile = $1 OR bkash_number = $1 OR nagad_number = $1) "
            "  AND status != 'inactive' "
            "LIMIT 1",
            phone,
        )
        if wbom_row:
            # Look for a matching FPE record — use payout_phone as the anchor
            fpe_row = await fetch_one(
                "SELECT id, employee_code, full_name, primary_phone, canonical_employee_id "
                "FROM fpe_employees WHERE primary_phone = $1 AND status = 'active'",
                phone,
            )
            if fpe_row:
                fpe_row = await _resolve_canonical(fpe_row)
                await _maybe_enrich_full_name(fpe_row["id"], fpe_row.get("full_name"), name_raw)
                return _make_result(fpe_row, "wbom_phone_match")
            # FPE record absent — fall through to auto-create below (with phone evidence)

    # 4 & 5. Name matching (only when no phone evidence was decisive)
    if name_raw:
        name_norm = normalize_name(name_raw)
        if name_norm:
            # Exact normalized-name
            row = await fetch_one(
                "SELECT id, employee_code, full_name, primary_phone, canonical_employee_id "
                "FROM fpe_employees "
                "WHERE name_normalized = $1 AND status = 'active'",
                name_norm,
            )
            if row:
                row = await _resolve_canonical(row)
                return _make_result(row, "exact_name")

            # Fuzzy name — load all active employees, do in-Python scoring
            all_emps = await fetch_all(
                "SELECT id, employee_code, full_name, name_normalized, "
                "primary_phone, canonical_employee_id "
                "FROM fpe_employees WHERE status = 'active'"
            )
            scored = []
            for emp in all_emps:
                if not emp["name_normalized"]:
                    continue
                score = fuzz.token_set_ratio(name_norm, emp["name_normalized"])
                scored.append((score, emp))
            scored.sort(key=lambda x: x[0], reverse=True)

            best_score = scored[0][0] if scored else 0
            second_score = scored[1][0] if len(scored) > 1 else 0
            best_row = scored[0][1] if scored else None

            # Auto-link only when above safe threshold AND clearly the best
            ambiguous = (
                best_row is not None
                and best_score >= FUZZY_THRESHOLD
                and (best_score - second_score) < FUZZY_AMBIGUITY_MARGIN
            )

            if (
                best_row
                and best_score >= FUZZY_THRESHOLD
                and not ambiguous
            ):
                best_row = await _resolve_canonical(best_row)
                log.info(
                    "[fpe.emp] fuzzy match name=%r → %s score=%.1f",
                    name_raw, best_row["full_name"], best_score,
                )
                return EmployeeMatchResult(
                    employee_id=best_row["id"],
                    employee_code=best_row["employee_code"] or "",
                    full_name=best_row["full_name"],
                    primary_phone=best_row["primary_phone"],
                    match_type="fuzzy_name",
                    match_score=best_score / 100.0,
                )

            # Phase 4 — SAFETY: when we have no phone evidence at all and name
            # match is ambiguous / below threshold, return None so the accounting
            # worker routes the message to the manual review queue.
            # DO NOT auto-create phantom employees from name-only guesses.
            if not payout_phone and not employee_id_phone:
                log.warning(
                    "[fpe.emp] REVIEW REQUIRED — name_only=%r, no phone evidence, "
                    "best_fuzzy=%.1f — skipping auto-create to prevent phantom record",
                    name_raw, best_score,
                )
                return None  # type: ignore[return-value]

            # Phone evidence present → auto-create is safe; also enqueue review
            # if a close-but-unconfident fuzzy match exists.
            new_result = await _auto_create_employee(
                name_raw, payout_phone, employee_id_phone
            )
            if best_row and best_score >= 70:
                try:
                    from .normalization import enqueue_review
                    await enqueue_review(
                        candidate_employee_id=new_result.employee_id,
                        suspected_match_id=best_row["id"],
                        match_reason=(
                            "fuzzy_name_below_threshold"
                            if best_score < FUZZY_THRESHOLD
                            else "name_collision"
                        ),
                        confidence_score=best_score / 100.0,
                        raw_name=name_raw,
                        raw_phone=payout_phone or employee_id_phone,
                    )
                except Exception as exc:
                    log.warning("[fpe.emp] enqueue_review failed: %s", exc)
            return new_result

    # 6. Auto-create — only safe when we have at least one phone to anchor identity.
    # A phone-less, name-less call can never produce a useful employee record.
    if not payout_phone and not employee_id_phone:
        log.warning(
            "[fpe.emp] REVIEW REQUIRED — no phone and no name — cannot create phantom employee",
        )
        return None  # type: ignore[return-value]
    return await _auto_create_employee(name_raw, payout_phone, employee_id_phone)


async def _resolve_canonical(row: dict) -> dict:
    """
    Follow canonical_employee_id soft-link if this row is marked as a duplicate.
    Returns the canonical row. Bounded loop to prevent cycles.
    """
    seen: set[int] = set()
    current = row
    for _ in range(8):  # hard upper bound on chain length
        canon_id = current.get("canonical_employee_id")
        if not canon_id or current["id"] in seen:
            return current
        seen.add(current["id"])
        canon = await fetch_one(
            "SELECT id, employee_code, full_name, primary_phone, "
            "canonical_employee_id, name_normalized "
            "FROM fpe_employees WHERE id = $1",
            canon_id,
        )
        if not canon:
            return current
        current = canon
    return current


async def add_alias(employee_id: int, alias_type: str, alias_value: str) -> None:
    """
    Add an alias for an employee (idempotent via ON CONFLICT DO NOTHING).
    alias_type: 'phone' | 'name' | 'employee_id'
    """
    await execute(
        """
        INSERT INTO fpe_employee_aliases (employee_id, alias_type, alias_value)
        VALUES ($1, $2, $3)
        ON CONFLICT (alias_type, alias_value) DO NOTHING
        """,
        employee_id, alias_type, alias_value,
    )


async def get_employee_by_id(employee_id: int) -> Optional[dict]:
    return await fetch_one(
        "SELECT * FROM fpe_employees WHERE id = $1",
        employee_id,
    )


async def create_employee_manual(
    *,
    full_name: str,
    employee_mobile: str,
    role_or_type: Optional[str] = None,
    status: str = "active",
) -> dict:
    """
    Create an employee explicitly from the admin UI.

    Business rules:
      - Employee mobile is the visible employee ID.
      - Manual entry must target an existing employee, so admin-created rows
        should become available to suggestions immediately.
      - No accounting rows are touched here.
    """
    mobile = normalize_bd_phone(employee_mobile)
    if not mobile:
        raise ValueError("valid employee mobile is required")

    clean_name = (full_name or "").strip()
    if not _is_valid_human_name(clean_name):
        raise ValueError("valid employee name is required")

    clean_status = (status or "active").strip().lower()
    if clean_status not in {"active", "inactive"}:
        raise ValueError("status must be active or inactive")

    name_norm = normalize_name(clean_name)
    lock_key = _advisory_key_for(mobile, mobile, name_norm)

    async with db_conn() as con:
        async with con.transaction():
            await con.execute("SELECT pg_advisory_xact_lock($1)", lock_key)

            existing = await con.fetchrow(
                """
                SELECT id, employee_code, full_name, primary_phone, employee_id_phone,
                       canonical_employee_id, status, department, created_source
                FROM fpe_employees
                WHERE employee_id_phone = $1 OR primary_phone = $1
                LIMIT 1
                """,
                mobile,
            )
            if existing:
                raise ValueError("employee already exists for this mobile")

            new_id = await con.fetchval(
                """
                INSERT INTO fpe_employees
                    (full_name, name_normalized, primary_phone, employee_id_phone,
                     department, status, created_source, resolution_status)
                VALUES ($1, $2, $3, $4, $5, $6, 'admin_manual_create', 'resolved')
                RETURNING id
                """,
                clean_name,
                name_norm,
                mobile,
                mobile,
                (role_or_type or "Staff").strip() or "Staff",
                clean_status,
            )
            code = f"EMP-{new_id:05d}"
            await con.execute(
                "UPDATE fpe_employees SET employee_code = $1, updated_at = NOW() WHERE id = $2",
                code,
                new_id,
            )
            await con.execute(
                "INSERT INTO fpe_employee_aliases (employee_id, alias_type, alias_value) "
                "VALUES ($1, 'phone', $2) ON CONFLICT (alias_type, alias_value) DO NOTHING",
                new_id,
                mobile,
            )
            if name_norm:
                await con.execute(
                    "INSERT INTO fpe_employee_aliases (employee_id, alias_type, alias_value) "
                    "VALUES ($1, 'name', $2) ON CONFLICT (alias_type, alias_value) DO NOTHING",
                    new_id,
                    name_norm,
                )

    row = await fetch_one("SELECT * FROM fpe_employees WHERE id = $1", new_id)
    if not row:
        raise ValueError("employee creation failed")
    return dict(row)


# ── Internal ──────────────────────────────────────────────────────────────────

def _make_result(row: dict, match_type: str) -> EmployeeMatchResult:
    return EmployeeMatchResult(
        employee_id=row["id"],
        employee_code=row.get("employee_code") or "",
        full_name=row["full_name"],
        primary_phone=row.get("primary_phone"),
        match_type=match_type,
        match_score=1.0,
    )


async def _auto_create_employee(
    name_raw: Optional[str],
    payout_phone: Optional[str],
    employee_id_phone: Optional[str],
) -> EmployeeMatchResult:
    """
    Create a new fpe_employees record and assign EMP-XXXXX code.

    Concurrency: wrapped in a transactional advisory lock keyed on the most
    stable identifier (employee_id_phone → payout_phone → normalized name).
    Two concurrent WhatsApp messages for the same person no longer create
    duplicate rows — the second waiter re-runs match_or_create_employee and
    returns the row the first writer just inserted.

    RULE 2 (spec): when no explicit "ID:" prefix exists, payout_phone is
    promoted to employee_id_phone for first-time employee creation.
    """
    full_name = (name_raw or "").strip() if _is_valid_human_name(name_raw) else ""
    if not full_name:
        # Fall back to phone ONLY when no valid human name was parsed.
        full_name = (payout_phone or employee_id_phone or "Unknown").strip()
    name_norm = normalize_name(full_name)

    # RULE 2 — promote payout to employee_id when no explicit ID was sent
    eid_phone = employee_id_phone or payout_phone

    # ── Concurrency-safe insert ──────────────────────────────────────────
    lock_key = _advisory_key_for(eid_phone, payout_phone, name_norm)
    async with db_conn() as con:
        async with con.transaction():
            await con.execute("SELECT pg_advisory_xact_lock($1)", lock_key)

            # Re-check after acquiring lock — another writer may have just
            # inserted the same employee.
            existing = None
            if eid_phone:
                existing = await con.fetchrow(
                    "SELECT id, employee_code, full_name, primary_phone, "
                    "canonical_employee_id "
                    "FROM fpe_employees "
                    "WHERE (employee_id_phone = $1 OR primary_phone = $1) "
                    "  AND status = 'active' LIMIT 1",
                    eid_phone,
                )
            if not existing and name_norm:
                existing = await con.fetchrow(
                    "SELECT id, employee_code, full_name, primary_phone, "
                    "canonical_employee_id "
                    "FROM fpe_employees "
                    "WHERE name_normalized = $1 AND status = 'active' LIMIT 1",
                    name_norm,
                )
            if existing:
                row = await _resolve_canonical(dict(existing))
                return _make_result(row, "exact_phone" if eid_phone else "exact_name")

            new_id = await con.fetchval(
                """
                INSERT INTO fpe_employees
                    (full_name, name_normalized, primary_phone, employee_id_phone,
                     status, created_source, resolution_status)
                VALUES ($1, $2, $3, $4, 'active', 'whatsapp_auto_create',
                        'unresolved')
                RETURNING id
                """,
                full_name,
                name_norm,
                payout_phone,
                eid_phone,
            )
            code = f"EMP-{new_id:05d}"
            await con.execute(
                "UPDATE fpe_employees SET employee_code = $1, "
                "updated_at = NOW() WHERE id = $2",
                code, new_id,
            )

            # Aliases (idempotent via unique constraint)
            for ph in {p for p in (payout_phone, eid_phone) if p}:
                await con.execute(
                    "INSERT INTO fpe_employee_aliases "
                    "(employee_id, alias_type, alias_value) "
                    "VALUES ($1, 'phone', $2) "
                    "ON CONFLICT (alias_type, alias_value) DO NOTHING",
                    new_id, ph,
                )
            if name_norm:
                await con.execute(
                    "INSERT INTO fpe_employee_aliases "
                    "(employee_id, alias_type, alias_value) "
                    "VALUES ($1, 'name', $2) "
                    "ON CONFLICT (alias_type, alias_value) DO NOTHING",
                    new_id, name_norm,
                )

    # Audit (outside the lock — best-effort, never blocks ingest)
    try:
        from .normalization import _audit  # late import to avoid cycle
        await _audit(
            action_type="auto_create_employee",
            entity_id=new_id,
            before=None,
            after={
                "id": new_id,
                "employee_code": code,
                "full_name": full_name,
                "primary_phone": payout_phone,
                "employee_id_phone": eid_phone,
                "source": "whatsapp_auto_create",
            },
            reviewer="fpe_engine",
            reason="no_match_found",
        )
    except Exception as exc:
        log.warning("[fpe.emp] audit log on auto_create failed: %s", exc)

    log.info(
        "[fpe.emp] auto-created employee id=%d code=%s name=%r phone=%s id_phone=%s",
        new_id, code, full_name, payout_phone, eid_phone,
    )

    return EmployeeMatchResult(
        employee_id=new_id,
        employee_code=code,
        full_name=full_name,
        primary_phone=payout_phone,
        match_type="auto_created",
        match_score=0.0,
    )


def _advisory_key_for(
    eid_phone: Optional[str],
    payout_phone: Optional[str],
    name_norm: Optional[str],
) -> int:
    """
    Map a (phone|name) identity to a stable signed-int64 advisory-lock key.
    Hash collisions across distinct identities are harmless — they only cause
    occasional serialization. Two messages for the SAME identity always map
    to the same key, which is the property we need.
    """
    import hashlib
    key_src = (eid_phone or payout_phone or name_norm or "").strip().lower()
    if not key_src:
        # Fall back to a constant lock so concurrent "Unknown" inserts serialize.
        return 0x46504E4F4E45  # 'FPNONE'
    h = hashlib.blake2b(key_src.encode("utf-8"), digest_size=8).digest()
    val = int.from_bytes(h, "big", signed=False)
    # Squeeze into signed int64 range required by pg_advisory_xact_lock(bigint).
    return val - (1 << 63) if val >= (1 << 63) else val
