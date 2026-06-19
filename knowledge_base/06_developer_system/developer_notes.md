# Developer Notes

## Application Reliability Notes
- Backend is source of truth; frontend must not maintain unsynced role state.
- All writes require validation and audit log.
- Identity cache must refresh after role edits.
- Payment completion trigger is accountant handoff from admin message.
- Candidate auto-reply must be disabled for sensitive/internal or unclear messages.
- Business-rule files should be used by decision engine before workflow execution.

## Missing Data Handling
When a required field is missing, ask the user for that field if safe; otherwise create admin draft/manual review.
