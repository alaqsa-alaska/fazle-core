# Workflow Engine

## Purpose
Coordinate role routing, state transitions, and backend writes.

## State Machines
Recruitment, Attendance, Escort Order, Release Slip, Payment, Cash Ledger, Salary, Unknown Fallback.

## Requirements
- Each workflow has allowed roles.
- Each state has allowed actions.
- Backend writes occur only from authorized states.
- Frontend state changes must sync to backend and audit log.
