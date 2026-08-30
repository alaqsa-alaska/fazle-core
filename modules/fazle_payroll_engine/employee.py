"""
Fazle Payroll Engine — Employee matching & auto-creation.

Matching priority (payment_business_rules.md / Payment Parser Canonical Spec
section 7.2/9.1 — "ID:" mobile is the employee lookup key; the payout mobile
is where money is transferred and is NOT authoritative identity on its own):
  1. Exact employee_id_phone match (from "ID: 01XXXXXXXXX" prefix)
  2. Exact primary_phone match (covers the common case where no ID: is
     given, so the payout phone doubles as the lookup key)
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
    # ── Identity phone (Owner business rule, 2026-08-30 — Phase 1) ────────────
    # The employee identity is a PHONE, never a name.
    #
    #   * If an explicit employee-ID phone is present it is the SOLE identity
    #     key. The payout phone is only where the money is sent — it must
    #     NEVER resolve to, or create, a different employee.
    #   * If only one phone is present (no "ID:" prefix), the parser has
    #     already promoted it to employee_id_phone upstream, so that phone is
    #     the identity.
    #   * A phone that belongs to an existing row matches it regardless of
    #     active / inactive status (an active row wins when both exist). This
    #     removes the idx_fpe_emp_phone_unique duplicate-key crash.
    #   * A previously unseen identity phone is a NEW person even if the name
    #     matches an existing employee — different canonical phones mean
    #     different people; name spelling never creates or merges a second
    #     employee once the phone is known. We therefore do NOT fall through
    #     to payout-phone or name matching when the identity phone is set but
    #     unmatched — we create keyed on the identity phone.
    #
    # Activation of a matched inactive employee on a successful payment is
    # handled downstream in accounting.create_transaction(), not here.
    #
    # Normalise BOTH phones to the canonical 01XXXXXXXXX form first — a phone
    # is the identity key, so "8801811111111", "+8801811111111" and
    # "01811111111" must all resolve to the same employee row. (Direct callers
    # like draft_approval pass raw phones; the FPE parser already normalises.)
    payout_phone = normalize_bd_phone(payout_phone) if payout_phone else None
    employee_id_phone = normalize_bd_phone(employee_id_phone) if employee_id_phone else None
    identity_phone = employee_id_phone or payout_phone

    if identity_phone:
        # 1. Direct column match on the identity phone (id-phone or primary).
        row = await fetch_one(
            "SELECT id, employee_code, full_name, primary_phone, canonical_employee_id "
            "FROM fpe_employees "
            "WHERE employee_id_phone = $1 OR primary_phone = $1 "
            "ORDER BY (status = 'active') DESC, id ASC LIMIT 1",
            identity_phone,
        )
        if row:
            row = await _resolve_canonical(row)
            await _maybe_enrich_full_name(row["id"], row.get("full_name"), name_raw)
            return _make_result(row, "exact_id_phone" if employee_id_phone else "exact_phone")

        # 2. Phone-alias lookup on the identity phone.
        alias_row = await fetch_one(
            """
            SELECT e.id, e.employee_code, e.full_name, e.primary_phone,
                   e.canonical_employee_id
            FROM fpe_employee_aliases a
            JOIN fpe_employees e ON e.id = a.employee_id
            WHERE a.alias_type = 'phone' AND a.alias_value = $1
            ORDER BY (e.status = 'active') DESC, e.id ASC LIMIT 1
            """,
            identity_phone,
        )
        if alias_row:
            alias_row = await _resolve_canonical(alias_row)
            await _maybe_enrich_full_name(alias_row["id"], alias_row.get("full_name"), name_raw)
            return _make_result(alias_row, "alias_phone")

        # 3. WBOM cross-system lookup on the identity phone — bridges a number
        #    known in wbom_employees but not yet linked into FPE.
        wbom_row = await fetch_one(
            "SELECT employee_id, employee_name, employee_mobile "
            "FROM wbom_employees "
            "WHERE (employee_mobile = $1 OR bkash_number = $1 OR nagad_number = $1) "
            "  AND LOWER(status) != 'inactive' "
            "LIMIT 1",
            identity_phone,
        )
        if wbom_row:
            fpe_row = await fetch_one(
                "SELECT id, employee_code, full_name, primary_phone, "
                "canonical_employee_id, wbom_employee_id "
                "FROM fpe_employees "
                "WHERE employee_id_phone = $1 OR primary_phone = $1 "
                "ORDER BY (status = 'active') DESC, id ASC LIMIT 1",
                identity_phone,
            )
            if fpe_row:
                fpe_row = await _resolve_canonical(fpe_row)
                await _maybe_enrich_full_name(fpe_row["id"], fpe_row.get("full_name"), name_raw)
                if not fpe_row.get("wbom_employee_id"):
                    try:
                        await execute(
                            """UPDATE fpe_employees
                               SET wbom_employee_id = $1,
                                   resolution_status = CASE WHEN resolution_status = 'unresolved'
                                                             THEN 'resolved' ELSE resolution_status END
                               WHERE id = $2""",
                            wbom_row["employee_id"], fpe_row["id"],
                        )
                    except Exception as _link_err:
                        log.debug("[fpe.emp] wbom_employee_id link failed: %s", _link_err)
                return _make_result(fpe_row, "wbom_phone_match")

        # 4. Identity phone is KNOWN but nothing matched → a new person.
        #    Create keyed on the identity phone. NO payout-phone or name
        #    fallthrough (Owner rule).
        new_result = await _auto_create_employee(name_raw, identity_phone, identity_phone)
        # Best-effort: if an existing active employee has a closely similar
        # name, queue a human de-dup review. This never blocks or merges —
        # the identity phone remains authoritative.
        if name_raw and new_result and new_result.match_type == "auto_created":
            try:
                name_norm = normalize_name(name_raw)
                if name_norm:
                    cand = await fetch_one(
                        """
                        SELECT id, similarity(name_normalized, $1) AS sim
                        FROM fpe_employees
                        WHERE name_normalized IS NOT NULL AND status = 'active'
                          AND id <> $2
                          AND similarity(name_normalized, $1) >= 0.80
                        ORDER BY similarity(name_normalized, $1) DESC
                        LIMIT 1
                        """,
                        name_norm, new_result.employee_id,
                    )
                    if cand:
                        from .normalization import enqueue_review
                        await enqueue_review(
                            candidate_employee_id=new_result.employee_id,
                            suspected_match_id=cand["id"],
                            match_reason="name_similar_new_identity_phone",
                            confidence_score=float(cand["sim"]),
                            raw_name=name_raw,
                            raw_phone=identity_phone,
                        )
            except Exception as exc:
                log.debug("[fpe.emp] post-create dedup review enqueue skipped: %s", exc)
        return new_result

    # ── No phone evidence at all — name-only path ─────────────────────────────
    # Never auto-creates (no phone to anchor identity). A confident exact or
    # fuzzy name match resolves; anything less goes to manual review (None).
    if name_raw:
        name_norm = normalize_name(name_raw)
        if name_norm:
            row = await fetch_one(
                "SELECT id, employee_code, full_name, primary_phone, canonical_employee_id "
                "FROM fpe_employees "
                "WHERE name_normalized = $1 AND status = 'active'",
                name_norm,
            )
            if row:
                row = await _resolve_canonical(row)
                return _make_result(row, "exact_name")

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

            ambiguous = (
                best_row is not None
                and best_score >= FUZZY_THRESHOLD
                and (best_score - second_score) < FUZZY_AMBIGUITY_MARGIN
            )
            if best_row and best_score >= FUZZY_THRESHOLD and not ambiguous:
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

        log.warning(
            "[fpe.emp] REVIEW REQUIRED — name_only=%r, no phone evidence — "
            "not auto-creating a phantom employee",
            name_raw,
        )
        return None  # type: ignore[return-value]

    log.warning(
        "[fpe.emp] REVIEW REQUIRED — no phone and no name — cannot create phantom employee",
    )
    return None  # type: ignore[return-value]


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
                     department, status, created_source, resolution_status, basic_salary)
                VALUES ($1, $2, $3, $4, $5, $6, 'admin_manual_create', 'resolved', 10000)
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
                # 2026-08-30 (Owner rule): match an existing employee row for
                # this phone regardless of active/inactive status so we never
                # collide with idx_fpe_emp_phone_unique on an inactive row.
                existing = await con.fetchrow(
                    "SELECT id, employee_code, full_name, primary_phone, "
                    "canonical_employee_id "
                    "FROM fpe_employees "
                    "WHERE (employee_id_phone = $1 OR primary_phone = $1) "
                    "ORDER BY (status = 'active') DESC, id ASC LIMIT 1",
                    eid_phone,
                )
            # NOTE (Owner rule, Phase 1): no name-based re-check here. A caller
            # only reaches _auto_create_employee with an identity phone that
            # matched nothing — a name collision with a *different* phone is a
            # different person and must NOT short-circuit creation. The
            # name-only resolution path never calls this function.
            if not eid_phone and name_norm:
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
                     status, created_source, resolution_status, basic_salary)
                VALUES ($1, $2, $3, $4, 'active', 'whatsapp_auto_create',
                        'unresolved', 10000)
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

            # Link to an existing WBOM record if this phone already has one
            # (mirrors _seed_fpe()'s WBOM->FPE link, reverse direction) --
            # never creates a new wbom_employees row here, only links to one
            # that already exists, to avoid the payroll-eligibility/bot-
            # identity risks a fabricated stub row would carry.
            wbom_match = await con.fetchrow(
                "SELECT employee_id FROM wbom_employees WHERE employee_mobile = ANY($1::text[]) LIMIT 1",
                [p for p in (eid_phone, payout_phone) if p],
            )
            if wbom_match:
                await con.execute(
                    """UPDATE fpe_employees
                       SET wbom_employee_id = $1,
                           resolution_status = CASE WHEN resolution_status = 'unresolved'
                                                     THEN 'resolved' ELSE resolution_status END
                       WHERE id = $2""",
                    wbom_match["employee_id"], new_id,
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
