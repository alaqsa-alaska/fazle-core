# Client Billing Profile — Code Implementation Plan (ready to implement, not yet coded)

**Status:** Detailed specification only. No Python, HTML, or JS has been written. Do not start coding until explicit Owner approval, per instruction.
**Status, updated 2026-07-09: fully implemented, applied, and verified live.** Everything this plan specifies has been built: `modules/client_billing/profiles.py` + `routes.py`, mounted in `app/main.py` (mount confirmed in the running service's OpenAPI schema), the `payroll.html` Client Settings section, and `tests/unit/test_client_billing_profiles.py` (7 tests). `db/migrations/034_client_billing_profiles.sql` and `db/migrations/035_client_billing_wage_general.sql` (adds `employee_wage_general`, added after the fact once the original field-list gap was flagged) are both applied to production. The service was restarted and the feature verified end-to-end in the live environment (routes live, Add/Edit/soft-Delete cycle tested via the real API, table structurally confirmed, test rows removed afterward). Full regression: 29 failed / 0 errors, identical to the pre-existing baseline.

**Precedence:** Executes `docs/CLIENT_BILLING_PROFILE_IMPLEMENTATION_PLAN.md` (architecture/decisions) against the schema in `db/migrations/034_client_billing_profiles.sql` + `035_client_billing_wage_general.sql`.

---

## New module: `modules/client_billing/`

Kept separate from `modules/billing` (which owns only invoice numbering, per that module's own docstring), matching this codebase's one-module-one-responsibility convention.

```
modules/client_billing/
    __init__.py       -- module docstring, no logic
    routes.py         -- FastAPI router, mounted in app/main.py
    profiles.py       -- CRUD functions against wbom_client_billing_profiles
```

### `modules/client_billing/profiles.py` — function signatures

```python
async def normalize_and_validate_contact_mobile(raw: str) -> str:
    """
    Reuses modules.escort_roster.routes._normalize_client_phone() for
    formatting, then checks membership in the CURRENT
    modules.escort_roster.routes._escort_client_phone_list() (i.e. the live
    ESCORT_CLIENT_PHONES .env value, re-read each call — never cached
    across requests, since an admin may update the authorized list via the
    existing /escort-clients endpoints at any time).
    Raises HTTPException(400) naming the rejected number if not authorized.
    Returns the normalized 880XXXXXXXXXX form on success.
    """

async def create_client_billing_profile(
    client_name: str,
    contact_person_mobile: str,
    *,
    office_address: str | None = None,
    contact_person_name: str | None = None,
    chairman_name: str | None = None,
    chairman_mobile: str | None = None,
    managing_member_name: str | None = None,
    managing_member_mobile: str | None = None,
    general_rate: float | None = None,
    rate_24h: float | None = None,
    rate_12h: float | None = None,
    rate_monthly: float | None = None,
    employee_wage_24h: float | None = None,
    employee_wage_12h: float | None = None,
    employee_wage_monthly: float | None = None,
    vat_amount: float = 0,
    tax_amount: float = 0,
    other_charges: float = 0,
    notes: str | None = None,
    contact_id: int | None = None,
) -> dict:
    """
    Validates contact_person_mobile (above), validates every numeric field
    is non-negative (reject otherwise), validates contact_id (if given)
    resolves to a real wbom_contacts row (a clearer error than letting the
    raw FK violation surface), then INSERTs. Returns the created row as a
    dict. Raises HTTPException(400) on any validation failure,
    HTTPException(409) if contact_person_mobile is already registered to
    another profile.
    """

async def update_client_billing_profile(client_id: int, **fields) -> dict:
    """Same validation as create; partial update (only supplied fields
    change); bumps updated_at. 404 if client_id doesn't exist."""

async def list_client_billing_profiles(active_only: bool = True) -> list[dict]:
    """List, optionally filtered to is_active=true (the default, matching
    the dashboard's list view default)."""

async def get_client_billing_profile(client_id: int) -> dict | None:
    """Single-row fetch for the View action."""

async def deactivate_client_billing_profile(client_id: int) -> dict:
    """Soft-delete: sets is_active=false. Does NOT hard-DELETE the row —
    see docs/CLIENT_BILLING_PROFILE_IMPLEMENTATION_PLAN.md's still-open
    hard-vs-soft-delete question; this plan defaults to the recommended
    soft-delete pending final confirmation. 404 if client_id doesn't exist."""
```

### `modules/client_billing/routes.py` — routes

| Method | Path | Calls | RBAC (pending final confirmation, see below) |
|---|---|---|---|
| `GET` | `/admin/client-billing-profiles` | `list_client_billing_profiles` | `admin` |
| `GET` | `/admin/client-billing-profiles/{client_id}` | `get_client_billing_profile` | `admin` |
| `POST` | `/admin/client-billing-profiles` | `create_client_billing_profile` | `admin` |
| `PUT` | `/admin/client-billing-profiles/{client_id}` | `update_client_billing_profile` | `admin` |
| `DELETE` | `/admin/client-billing-profiles/{client_id}` | `deactivate_client_billing_profile` | `superadmin` (destructive action, matching `user_remove`'s tier) |
| `GET` | `/admin/escort-clients/authorized-numbers` | `modules.escort_roster.routes._escort_client_phone_list()` (reused directly, thin wrapper) | `admin` |

All routes gated by the existing `_require_key` dashboard-auth dependency (matching `modules/escort_roster/routes.py`'s own pattern) plus a role check against the admin's session role — **exact role-check mechanism to confirm against how `payroll.html`'s existing Admin tab currently determines "is this admin allowed to see this" before writing the route decorators**, since this is dashboard/session-based, not the WhatsApp `COMMAND_ROLE` dict.

**RBAC granularity note (still open, per the implementation plan):** the table above assumes every `admin` can see rates but only `superadmin` can delete. Whether `admin` should also see `employee_wage_*`/profit figures, or whether those need a stricter view-level restriction, is not yet decided — if restricted, the cleanest implementation is a second, narrower response model (`ClientBillingProfilePublic` without wage fields) returned to `admin`-tier sessions, full model only to `superadmin`. Flagged for confirmation before the `GET` handlers are written, not assumed.

---

## `app/main.py` change

One line: `app.include_router(client_billing_router)` — mirroring how `drafts_router`/`social_auto_reply_router` are already mounted (and were the exact routers found unmounted during the very first certification pass this session — a reminder to actually verify this line lands and survives, given that exact regression already happened once).

---

## Frontend: `app/static/payroll.html`

- New subsection inside the existing `data-tab="admin"` block, placed after the current "Admin Console" section.
- **Client Settings** heading, three action buttons: Add Client / Edit Client / Delete Client — reusing the existing button/form CSS classes already in this file (no new stylesheet).
- **Shared form** (same markup for Add and Edit, Edit pre-fills via a `GET .../{client_id}` call on open):
  - Text inputs: Client Name*, Office Address, Contact Person Name, Chairman Name, Managing Member Name.
  - **Contact Person Mobile** — rendered as a `<select>` populated from `GET /admin/escort-clients/authorized-numbers` (not a free-text `<input>`) so an admin physically cannot type an unauthorized number — the strongest form of client-side enforcement, backed by the mandatory server-side check in `normalize_and_validate_contact_mobile()`.
  - Number inputs: General/24h/12h/Monthly Rate, Employee Wage ×3, VAT Amount, Tax Amount, Other Charges — all `type="number" min="0" step="0.01"`, matching the existing non-negative validation rule.
  - Textarea: Notes.
  - Toggle: Active/Inactive (checkbox, matching the existing pattern elsewhere in this file, e.g. escort/employee active toggles).
  - Submit button posts to `POST` (Add) or `PUT` (Edit); on 400/409 error, show the server's message inline (reuse the existing error-display convention in this file, not a new one).
- **List view table**: Client Name, Contact Person, Contact Mobile, Office Address, General/24h/12h/Monthly Rate, Status columns, populated from `GET /admin/client-billing-profiles?active=true` by default with a toggle to show inactive too. Row actions: View (👁), Edit (✏️), Remove (🗑, calls `DELETE`, confirms via the existing confirm-dialog pattern before calling).
- **Not built yet** — every item above describes the plan; no HTML/JS exists.

---

## Validation rules (implementation detail, restated precisely from the architecture plan)

1. `contact_person_mobile`: normalize via `_normalize_client_phone()`, then must be in `_escort_client_phone_list()` — checked fresh on every write, not cached.
2. `client_name`: required, non-empty after `.strip()`.
3. Every rate/wage/VAT/Tax/Other-Charges field: reject if negative (`< 0`); `None`/omitted is allowed (means "not set").
4. `contact_person_mobile` uniqueness: DB unique index is the source of truth; API catches the resulting `UniqueViolationError` and re-raises as `HTTPException(409, "This mobile number is already registered to another client profile")` rather than leaking a raw DB error.
5. `contact_id`, if provided: verify it resolves via a `SELECT 1 FROM wbom_contacts WHERE contact_id=$1` before the INSERT, to raise a clear `HTTPException(400, "contact_id does not exist")` instead of a raw FK violation.
6. No field is ever silently defaulted to a guessed value — `NULL` stays `NULL` until an admin sets it.

---

## Security / RBAC — final recommendation (2 items still need Owner confirmation)

- View/List/Add/Edit: `admin` tier.
- Delete (soft): `superadmin` tier.
- **Open:** should `admin` see `employee_wage_*` fields (used only for the profit-estimate calculation), or restrict those to `superadmin`? Recommend restricting, pending confirmation — commercial cost/profit data is more sensitive than the client-facing rate figures.
- **Open:** hard delete vs. soft delete — this plan implements soft-delete (`is_active=false`) by default; switch to hard `DELETE FROM` only if explicitly requested, since it's harder to reverse.

---

## Tests to write alongside implementation

- `tests/unit/test_client_billing_profiles.py`:
  - `normalize_and_validate_contact_mobile`: accepts an authorized number (mock `_escort_client_phone_list`), rejects an unauthorized one.
  - CRUD round-trip: create → get → update → list (active/inactive filter) → soft-delete → confirm excluded from `active_only=True` list.
  - Uniqueness: two creates with the same `contact_person_mobile` — second must raise `409`.
  - FK validation: create with a non-existent `contact_id` — must raise `400` with a clear message, not a raw DB error.
  - Non-negative validation: create with a negative rate — must raise `400`.
- Regression: full non-e2e suite re-run after implementation (standard practice this session) — must show 0 new failures beyond the existing 29-failure baseline.

---

## Sequencing (once Owner approves starting code)

1. Apply `db/migrations/034_client_billing_profiles.sql` to production (separate explicit approval, per instruction — this plan does not assume migration approval implies code approval or vice versa).
2. Implement `modules/client_billing/profiles.py` + tests, run full regression.
3. Implement `modules/client_billing/routes.py`, mount in `app/main.py`, confirm the mount actually took (per the reminder above — this exact class of regression happened once already this session).
4. Implement the `payroll.html` frontend section.
5. Manual end-to-end pass in a browser: Add → appears in list → Edit → Delete (soft) → confirm it disappears from the default list view, reappears with the inactive filter.
6. Only then is Commit C (bill generation hook, reading from this new table) designed in detail — not part of this plan.
