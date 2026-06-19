# Payment Workflow

## Flow
Payment Request -> Identity Verification -> Duty/Slip Verification -> Method/Number Confirmation -> Draft/Admin Message -> Accountant WhatsApp -> Payment Complete -> Ledger Update.

## Completion Rule
Payment is considered complete when admin's payment message reaches accountant WhatsApp.

## Required Data
Employee ID or name, phone/payment number, method, amount, purpose, admin sender, accountant recipient, timestamp.

## Cross References
- ../04_business_rules/payment_business_rules.md
- cash_workflow.md
