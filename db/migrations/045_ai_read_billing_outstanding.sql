-- Migration 045: ai_read_billing_outstanding view (Phase 2B, Commit D)
--
-- Read-only view for the AI console's new billing-outstanding-balance tool
-- (modules/ai_readonly_tools/billing.py), matching the exact ai_read_%
-- naming/grant convention established in migration 012. The reader role
-- (fazle_ai_reader) has no direct GRANT on wbom_billing_records — querying
-- the table directly from ai_readonly_tools would fail at runtime, so this
-- view (not a fresh grant on the base table) is required, same reasoning
-- as every other ai_read_* view in this file.

CREATE OR REPLACE VIEW ai_read_billing_outstanding AS
SELECT
    b.bill_id,
    b.bill_number,
    b.contact_id,
    COALESCE(cbp.client_name, c.display_name, 'Unknown') AS client_name,
    b.bill_date,
    b.total_amount,
    b.payment_status,
    (CURRENT_DATE - b.bill_date) AS days_outstanding
FROM wbom_billing_records b
LEFT JOIN wbom_client_billing_profiles cbp ON cbp.contact_id = b.contact_id
LEFT JOIN wbom_contacts c ON c.contact_id = b.contact_id
WHERE b.payment_status = 'Pending'
ORDER BY b.bill_date ASC;

GRANT SELECT ON ai_read_billing_outstanding TO fazle_ai_reader;
