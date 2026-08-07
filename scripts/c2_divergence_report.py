#!/usr/bin/env python3
"""
Hermes C2 — Read-only Decision Comparison Script

Compares Hermes shadow decisions (hermes_decision_audit_log) against
ground-truth routing outcomes (wbom_whatsapp_messages) over a configurable
date range and produces a divergence report.

Usage:
  python3 scripts/c2_divergence_report.py [--days N] [--dry-run] [--write-snapshots]

  --days N           Analyze the last N days of shadow rows (default: 7)
  --dry-run          Print report only; do not write to hermes_divergence_snapshots
  --write-snapshots  Write comparison results to hermes_divergence_snapshots

Output: plain-text divergence report to stdout.
Exit code 0 = gate PASS (divergence rate < 5%), 1 = gate FAIL or error.

This script is read-only by default. It never touches routing, replies,
business tables, or existing shadow rows. Production behavior is unchanged.

Divergence definition:
  A "role divergence" occurs when shadow_role != wbom_role for the same
  message (same sender_phone within a 60-second window around the shadow
  timestamp). This indicates the identity_brain resolved a different role
  in the shadow context vs the production context — or the wbom record
  was updated after routing.

Rollback: disabling HERMES_DECISION_AUDIT_ENABLED stops collection.
  The snapshot table can be dropped without any business impact.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta

import asyncpg

DB_URL = os.environ.get("DATABASE_URL", "")
DIVERGENCE_GATE_PCT = 5.0  # gate: divergence rate must be below this


async def run(days: int, dry_run: bool, write_snapshots: bool) -> int:
    if not DB_URL:
        print("ERROR: DATABASE_URL environment variable is not set.", file=sys.stderr)
        return 1

    conn = await asyncpg.connect(DB_URL)
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        # 1. Fetch shadow rows in date range
        shadow_rows = await conn.fetch(
            """
            SELECT trace_id, sender_phone, timestamp,
                   identity_role, intent, chosen_action,
                   explanation, intent_method, provider_used
            FROM hermes_decision_audit_log
            WHERE created_at >= $1
            ORDER BY created_at ASC
            """,
            cutoff,
        )

        total = len(shadow_rows)
        print(f"=== Hermes C2 Divergence Report ===")
        print(f"Period: last {days} days (since {cutoff.strftime('%Y-%m-%d %H:%M UTC')})")
        print(f"Shadow rows analyzed: {total}")

        if total == 0:
            print("\nNo shadow rows found in the analysis period.")
            print("Ensure HERMES_DECISION_AUDIT_ENABLED=true and messages are flowing.")
            return 1

        # 2. Join against wbom_whatsapp_messages for ground truth
        role_divergences = 0
        unmatched = 0
        branch_coverage: dict[str, int] = {}
        role_breakdown: dict[str, int] = {}
        snapshots = []

        for row in shadow_rows:
            explanation = row["explanation"] or "unknown"
            branch_coverage[explanation] = branch_coverage.get(explanation, 0) + 1
            shadow_role = row["identity_role"] or "unknown"
            role_breakdown[shadow_role] = role_breakdown.get(shadow_role, 0) + 1

            # Find the corresponding wbom message (same phone, within ±60s)
            ts = row["timestamp"]
            wbom = await conn.fetchrow(
                """
                SELECT identity_role, direction, received_at
                FROM wbom_whatsapp_messages
                WHERE sender_number = ANY($1::text[])
                  AND received_at BETWEEN $2 AND $3
                  AND direction = 'inbound'
                ORDER BY ABS(EXTRACT(EPOCH FROM (received_at - $4))) ASC
                LIMIT 1
                """,
                [row["sender_phone"], row["sender_phone"].replace("880", "0", 1)],
                ts - timedelta(seconds=60),
                ts + timedelta(seconds=60),
                ts,
            )

            is_role_match = None
            is_regression = False
            regression_reason = None
            wbom_role = None
            wbom_direction = None
            wbom_received_at = None

            if wbom:
                wbom_role = wbom["identity_role"] or "unknown"
                wbom_direction = wbom["direction"]
                wbom_received_at = wbom["received_at"]
                is_role_match = shadow_role == wbom_role
                if not is_role_match:
                    role_divergences += 1
                    is_regression = True
                    regression_reason = f"role mismatch: shadow={shadow_role} wbom={wbom_role}"
            else:
                unmatched += 1

            if write_snapshots and not dry_run:
                snapshots.append({
                    "trace_id": row["trace_id"],
                    "sender_phone": row["sender_phone"],
                    "shadow_role": shadow_role,
                    "shadow_intent": row["intent"],
                    "shadow_action": row["chosen_action"],
                    "shadow_explanation": explanation,
                    "shadow_intent_method": row["intent_method"],
                    "shadow_provider": row["provider_used"],
                    "wbom_role": wbom_role,
                    "wbom_direction": wbom_direction,
                    "wbom_received_at": wbom_received_at,
                    "is_role_match": is_role_match,
                    "is_regression": is_regression,
                    "regression_reason": regression_reason,
                })

        # 3. Compute divergence rate (only over matched rows)
        matched = total - unmatched
        divergence_rate = (role_divergences / matched * 100) if matched > 0 else 0.0

        print()
        print("=== Routing Branch Coverage ===")
        for branch, count in sorted(branch_coverage.items(), key=lambda x: -x[1]):
            pct = count / total * 100
            print(f"  {branch:<40s} {count:>5d} ({pct:.1f}%)")

        print()
        print("=== Identity Role Distribution ===")
        for role, count in sorted(role_breakdown.items(), key=lambda x: -x[1]):
            pct = count / total * 100
            print(f"  {role:<30s} {count:>5d} ({pct:.1f}%)")

        print()
        print("=== Divergence Summary ===")
        print(f"  Total shadow rows:      {total}")
        print(f"  Matched to wbom:        {matched}")
        print(f"  Unmatched (no wbom):    {unmatched}")
        print(f"  Role divergences:       {role_divergences}")
        print(f"  Divergence rate:        {divergence_rate:.2f}% (gate: < {DIVERGENCE_GATE_PCT}%)")
        print()

        # 4. Write snapshots if requested
        if write_snapshots and not dry_run and snapshots:
            for snap in snapshots:
                await conn.execute(
                    """
                    INSERT INTO hermes_divergence_snapshots (
                        trace_id, sender_phone, shadow_role, shadow_intent,
                        shadow_action, shadow_explanation, shadow_intent_method,
                        shadow_provider, wbom_role, wbom_direction, wbom_received_at,
                        is_role_match, is_regression, regression_reason
                    ) VALUES (
                        $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14
                    ) ON CONFLICT DO NOTHING
                    """,
                    snap["trace_id"], snap["sender_phone"], snap["shadow_role"],
                    snap["shadow_intent"], snap["shadow_action"], snap["shadow_explanation"],
                    snap["shadow_intent_method"], snap["shadow_provider"],
                    snap["wbom_role"], snap["wbom_direction"], snap["wbom_received_at"],
                    snap["is_role_match"], snap["is_regression"], snap["regression_reason"],
                )
            print(f"  Snapshot rows written: {len(snapshots)}")

        # 5. Gate decision
        gate_pass = matched >= 10 and divergence_rate < DIVERGENCE_GATE_PCT
        if matched < 10:
            print(f"C2 GATE: INSUFFICIENT DATA (need ≥10 matched rows, have {matched})")
            print("         Continue collecting data and re-run after more traffic.")
            return 1
        elif gate_pass:
            print(f"C2 GATE: PASS — divergence rate {divergence_rate:.2f}% < {DIVERGENCE_GATE_PCT}%")
            return 0
        else:
            print(f"C2 GATE: FAIL — divergence rate {divergence_rate:.2f}% ≥ {DIVERGENCE_GATE_PCT}%")
            print("         Investigate role mismatches before proceeding to C3.")
            return 1

    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hermes C2 divergence analysis")
    parser.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    parser.add_argument("--dry-run", action="store_true", help="Print only, no DB writes")
    parser.add_argument("--write-snapshots", action="store_true", help="Write to hermes_divergence_snapshots")
    args = parser.parse_args()
    exit_code = asyncio.run(run(args.days, args.dry_run, args.write_snapshots))
    sys.exit(exit_code)
