"""
Fazle Core — Billing (Phase 2B)

Invoice numbering only. Client billing profiles (rates, VAT/Tax, contact
linkage) live in modules.client_billing — a separate module, kept apart
per this codebase's one-module-one-responsibility convention.

  → invoice_numbering.next_invoice_number()  — AQS-YYYY-NNNNNN generator

Not yet present: bill creation (Commit C), payment recording,
outstanding-balance queries — these read from modules.client_billing's
wbom_client_billing_profiles once built; see
docs/CLIENT_DOMAIN_IMPLEMENTATION_PLAN.md Phase 2B.
"""
