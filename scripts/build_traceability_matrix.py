#!/usr/bin/env python3
"""Build a static KB-to-code traceability matrix."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "traceability_matrix.md"
JSON_OUT = ROOT / "reports" / "traceability_matrix.json"


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def main() -> None:
    modules = [p.name for p in (ROOT / "modules").iterdir() if p.is_dir()]
    routes_by_file: dict[str, list[str]] = {}
    db_by_file: dict[str, list[str]] = {}
    tests_by_name: dict[str, list[str]] = {}
    for py in [ROOT / "app" / "main.py", *ROOT.glob("modules/**/*.py")]:
        if not py.exists() or "__pycache__" in py.parts:
            continue
        text = py.read_text(errors="ignore")
        routes = sorted(set(m.group(1).strip().strip("'\"") for m in re.finditer(r"@(?:app|router)\.(?:get|post|put|patch|delete)\(([^),]+)", text)))
        if routes:
            routes_by_file[rel(py)] = routes
        refs = sorted(set(re.findall(r"\b(?:wbom|fazle|ai_read|ai_memory)_[a-zA-Z0-9_]+", text)))
        if refs:
            db_by_file[rel(py)] = refs
    for test in ROOT.glob("tests/**/*.py"):
        low = test.name.lower()
        for module in modules:
            if module.lower() in low or module.lower() in test.read_text(errors="ignore").lower():
                tests_by_name.setdefault(module, []).append(rel(test))
    rows = []
    for kb in sorted((ROOT / "knowledge_base").rglob("*.md")):
        text = kb.read_text(errors="ignore").lower()
        module_refs = sorted(m for m in modules if m.lower() in text or m.lower() in kb.as_posix().lower())
        route_refs = sorted({route for routes in routes_by_file.values() for route in routes if route.lower() in text})
        db_refs = sorted(set(re.findall(r"\b(?:wbom|fazle|ai_read|ai_memory)_[a-zA-Z0-9_]+", text)))
        test_refs = sorted({t for m in module_refs for t in tests_by_name.get(m, [])})
        rows.append(
            {
                "kb_article": rel(kb),
                "modules": module_refs,
                "database": db_refs,
                "workflows": sorted(set(re.findall(r"\b[a-zA-Z0-9_-]+ workflow\b", text))),
                "api_routes": route_refs,
                "frontend_pages": sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "app" / "static").glob("*.html") if p.stem.lower() in text),
                "tests": test_refs,
            }
        )
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "rows": rows}
    JSON_OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Knowledge Traceability Matrix",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        "| KB Article | Modules | DB | Routes | Frontend | Tests |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| `{row['kb_article']}` | {len(row['modules'])} | {len(row['database'])} | {len(row['api_routes'])} | {len(row['frontend_pages'])} | {len(row['tests'])} |"
        )
    lines += [
        "",
        "## Coverage Gaps",
        "",
        "Active components with zero KB references should be reviewed in `knowledge_base/module_registry.yaml`.",
    ]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {REPORT}")


if __name__ == "__main__":
    main()
