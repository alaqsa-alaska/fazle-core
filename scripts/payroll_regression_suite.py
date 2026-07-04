#!/usr/bin/env python3
"""Focused payroll regression suite.

Covers transactions, income, and operator flows under the live runtime.

Usage:
  python scripts/payroll_regression_suite.py
  python scripts/payroll_regression_suite.py --write
  python scripts/payroll_regression_suite.py --base-url http://127.0.0.1:8200 --key <API_KEY> --write
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any


def load_key(explicit_key: str | None) -> str:
    if explicit_key:
        return explicit_key

    env_key = os.getenv("INTERNAL_API_KEY")
    if env_key:
        return env_key

    env_path = Path("/home/azim/.env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("INTERNAL_API_KEY="):
                return line.split("=", 1)[1].strip()

    raise RuntimeError("INTERNAL_API_KEY not found (use --key or set env var)")


def http_json(base_url: str, path: str, key: str | None = None, method: str = "GET", payload: dict[str, Any] | None = None) -> tuple[int, Any]:
    headers: dict[str, str] = {}
    data = None
    if key:
        headers["X-Internal-Key"] = key
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(base_url.rstrip("/") + path, headers=headers, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode("utf-8", "replace")
            return resp.status, json.loads(text) if text else {}
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", "replace")
        try:
            body = json.loads(text)
        except Exception:
            body = text
        return exc.code, body


def main() -> int:
    parser = argparse.ArgumentParser(description="Run focused payroll regression checks")
    parser.add_argument("--base-url", default="http://127.0.0.1:8200")
    parser.add_argument("--key", default=None)
    parser.add_argument("--write", action="store_true", help="Run controlled write-path checks with cleanup")
    args = parser.parse_args()

    key = load_key(args.key)
    base = args.base_url.rstrip("/")

    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        results.append((name, ok, detail))

    # Read-only suite
    st, body = http_json(base, "/api/fpe/health")
    check("health", st == 200 and isinstance(body, dict) and "status" in body, f"status={st}")

    st, _ = http_json(base, "/api/fpe/transactions?page=1&page_size=5")
    check("transactions unauthorized", st in (401, 403), f"status={st}")

    st, body = http_json(base, "/api/fpe/transactions?page=1&page_size=5", key=key)
    tx_rows = body.get("transactions", []) if isinstance(body, dict) else []
    check("transactions authorized", st == 200 and isinstance(tx_rows, list), f"status={st} rows={len(tx_rows)}")

    st, _ = http_json(base, "/api/fpe/income?page=1&page_size=5")
    check("income unauthorized", st in (401, 403), f"status={st}")

    st, body = http_json(base, "/api/fpe/income?page=1&page_size=5", key=key)
    inc_rows = body.get("transactions", []) if isinstance(body, dict) else []
    check("income authorized", st == 200 and isinstance(inc_rows, list), f"status={st} rows={len(inc_rows)}")

    st, _ = http_json(base, "/api/fpe/operator/pending?limit=5")
    check("operator pending unauthorized", st in (401, 403), f"status={st}")

    st, body = http_json(base, "/api/fpe/operator/pending?limit=5", key=key)
    op_rows = body.get("items", []) if isinstance(body, dict) else []
    check("operator pending authorized", st == 200 and isinstance(op_rows, list), f"status={st} rows={len(op_rows)}")

    st, openapi = http_json(base, "/openapi.json")
    as_text = json.dumps(openapi)
    check("openapi operator routes", st == 200 and all(p in as_text for p in [
        "/api/fpe/operator/submit",
        "/api/fpe/operator/{pending_id}/approve",
        "/api/fpe/operator/{pending_id}/reject",
    ]), f"status={st}")

    if args.write:
        tag = "REGTEST_" + datetime.utcnow().strftime("%Y%m%d_%H%M%S")

        st, body = http_json(base, "/api/fpe/employees?page=1&page_size=1", key=key)
        if st != 200 or not isinstance(body, dict) or not body.get("employees"):
            check("write probe employee", False, f"status={st}")
        else:
            emp = body["employees"][0]
            emp_id = int(emp["id"])
            emp_name = emp.get("full_name") or emp.get("display_name") or f"Emp#{emp_id}"
            emp_phone = emp.get("employee_id_phone") or emp.get("primary_phone")
            check("write probe employee", True, f"emp_id={emp_id}")

            # income create (cleanup possible if income delete route exists)
            inc_payload = {
                "txn_date": date.today().isoformat(),
                "income_head": "Others",
                "received_from": f"{emp_name} {tag}",
                "amount": 1,
                "money_received_by": "regression_bot",
                "mobile_number": emp_phone,
                "money_receipt_no": f"{tag}_INC",
            }
            st, body = http_json(base, "/api/fpe/income", key=key, method="POST", payload=inc_payload)
            if st == 200 and isinstance(body, dict) and body.get("id"):
                income_id = int(body["id"])
                check("income create", True, f"income_id={income_id}")
                # Try cleanup through new admin route when available
                st2, body2 = http_json(base, f"/api/admin/income/{income_id}?deleted_by=regression_bot", key=key, method="DELETE", payload={})
                check("income cleanup", st2 == 200 and isinstance(body2, dict) and body2.get("ok") is True, f"status={st2}")
            else:
                check("income create", False, f"status={st}")

            # operator submit then reject cleanup
            op_payload = {
                "employee_id": emp_id,
                "employee_name_raw": emp_name,
                "amount": "1.00",
                "payout_method": "cash",
                "payout_phone": emp_phone,
                "txn_date": date.today().isoformat(),
                "txn_category": "salary",
                "notes": f"{tag} operator write-path test",
            }
            st, body = http_json(base, "/api/fpe/operator/submit", key=key, method="POST", payload=op_payload)
            if st == 200 and isinstance(body, dict) and body.get("pending_id"):
                pending_id = int(body["pending_id"])
                check("operator submit", True, f"pending_id={pending_id}")
                reason = urllib.parse.quote(f"{tag}_cleanup")
                st2, body2 = http_json(base, f"/api/fpe/operator/{pending_id}/reject?reviewer=regression_bot&reason={reason}", key=key, method="POST", payload={})
                check("operator reject cleanup", st2 == 200 and isinstance(body2, dict) and body2.get("status") == "rejected", f"status={st2}")
            else:
                check("operator submit", False, f"status={st}")

            # manual transaction create then soft-delete cleanup
            tx_payload = {
                "employee_id": emp_id,
                "amount": "1.00",
                "payout_method": "cash",
                "payout_phone": emp_phone,
                "txn_date": date.today().isoformat(),
                "txn_category": "salary",
                "reason": f"{tag} manual transaction write-path test",
            }
            st, body = http_json(base, "/api/fpe/transactions/manual", key=key, method="POST", payload=tx_payload)
            if st == 200 and isinstance(body, dict) and body.get("transaction", {}).get("id"):
                txn_id = int(body["transaction"]["id"])
                check("manual transaction create", True, f"txn_id={txn_id}")
                st2, body2 = http_json(base, f"/api/admin/transactions/{txn_id}?deleted_by=regression_bot", key=key, method="DELETE", payload={})
                check("manual transaction cleanup", st2 == 200 and isinstance(body2, dict) and body2.get("ok") is True, f"status={st2}")
            else:
                check("manual transaction create", False, f"status={st}")

    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print(json.dumps({"passed": passed, "failed": failed, "total": len(results), "write_mode": bool(args.write)}))
    for name, ok, detail in results:
        print(("PASS" if ok else "FAIL") + " | " + name + " | " + detail)

    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
