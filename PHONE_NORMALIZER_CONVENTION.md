# Phone Normalization Convention

## Canonical normalizer

**`modules.phone_normalizer.normalize_phone(raw)`** is the single source of truth for validating and canonicalizing Bangladeshi mobile numbers.

- **Input:** any format — `01XXXXXXXXX`, `8801XXXXXXXXX`, `+8801XXXXXXXXX`, `+880 17XX-XXXXXX`, Bengali digits, etc.
- **Output:** `8801XXXXXXXXX` (13-digit canonical) or `None` (invalid/unrecognized).
- Validates operator code (`01[3-9]XXXXXXXX` range). Rejects malformed numbers.

## Lookup normalizer

**`modules.number_identity.normalize_phone(raw)`** returns all 3 variants for DB queries.

- **Output:** `["01XXXXXXXXX", "8801XXXXXXXXX", "+8801XXXXXXXXX"]` or `[]`.
- Use with `= ANY($1)` (asyncpg accepts a list) to match regardless of storage format.
- Delegates to `phone_normalizer` for validation — same operator check applies.

```python
variants = normalize_phone(raw_mobile)
row = await fetch_one(
    "SELECT ... FROM wbom_employees WHERE employee_mobile = ANY($1) ...",
    variants,
)
```

## Storage

All phones written to `wbom_*` tables must be canonicalized via `phone_normalizer.normalize_phone()` first. Storage format: `8801XXXXXXXXX`.

## NL / text extraction

Use a regex to locate the phone-like string in free text, then feed the match into `phone_normalizer.normalize_phone()` for validation. Keep extraction and validation separate.

## FPE exception

`modules.fazle_payroll_engine.normalizer.normalize_bd_phone()` remains the sole normalizer for FPE employee lookups until `fpe_employees.mobile` column format is verified and migrated to `8801XXXXXXXXX`. Output: `01XXXXXXXXX` (11-digit).

## What NOT to do

- Do not add a new inline `re.sub(r"\D", "", raw)` to strip digits and use the result as a DB key — it produces an unvalidated raw digit string that may not match any stored format.
- Do not use last-N-digits tricks (`digits[-10:]`) — use `= ANY($1)` with 3 variants instead.
- Do not create a new normalizer module. If `phone_normalizer` is missing a feature (e.g. Bengali digit support), add it there.
