"""
Fazle Core — Client Billing Profiles (Phase 2B)

Client Settings: rate/wage/VAT/Tax configuration per client company,
managed via a frontend UI (Payroll Page -> Admin Tab). Kept separate from
modules.billing (which owns invoice numbering only) and from wbom_contacts
(WhatsApp-contact identity) -- see
docs/CANONICAL_CLIENT_DOMAIN.md "Client Tier, Client Status, and is_active"
and docs/CLIENT_BILLING_PROFILE_IMPLEMENTATION_PLAN.md for the full
architecture and Owner decisions this module implements.

  → profiles.create_client_billing_profile() / update_ / list_ / get_ / deactivate_
  → routes.router — mounted in app/main.py

Explicitly out of scope here (per Owner instruction): General Ledger,
payroll formula changes, VAT/Tax added to a client's invoice total (this
phase, they are internal profit-estimation deductions only).
"""
