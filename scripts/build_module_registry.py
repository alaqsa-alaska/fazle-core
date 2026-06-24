#!/usr/bin/env python3
"""Build a static module registry from code, routes, DB refs, and KB refs."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULES = ROOT / "modules"
YAML_OUT = ROOT / "knowledge_base" / "module_registry.yaml"
REPORT_OUT = ROOT / "reports" / "module_registry.md"


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def dump_yaml(value, indent: int = 0) -> str:
    pad = " " * indent
    if isinstance(value, dict):
        lines = []
        for k, v in value.items():
            if isinstance(v, (dict, list)):
                lines.append(f"{pad}{k}:")
                lines.append(dump_yaml(v, indent + 2))
            else:
                lines.append(f"{pad}{k}: {json.dumps(v, ensure_ascii=False)}")
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return f"{pad}[]"
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}-")
                lines.append(dump_yaml(item, indent + 2))
            else:
                lines.append(f"{pad}- {json.dumps(item, ensure_ascii=False)}")
        return "\n".join(lines)
    return f"{pad}{json.dumps(value, ensure_ascii=False)}"


def route_paths(text: str) -> list[str]:
    return sorted(set(m.group(2).strip().strip("'\"") for m in re.finditer(r"@(app|router)\.(?:get|post|put|patch|delete)\(([^),]+)", text)))


def build() -> dict:
    modules = []
    kb_text = "\n".join(path.read_text(errors="ignore") for path in (ROOT / "knowledge_base").rglob("*.md"))
    for module_dir in sorted(p for p in MODULES.iterdir() if p.is_dir()):
        py_files = sorted(module_dir.rglob("*.py"))
        combined = "\n".join(path.read_text(errors="ignore") for path in py_files)
        deps = sorted(set(re.findall(r"from modules\.([a-zA-Z0-9_]+)|import modules\.([a-zA-Z0-9_]+)", combined)))
        deps_flat = sorted({a or b for a, b in deps if (a or b) and (a or b) != module_dir.name})
        db_refs = sorted(set(re.findall(r"\b(?:wbom|fazle|ai_read|ai_memory)_[a-zA-Z0-9_]+", combined)))
        routes = sorted({route for path in py_files for route in route_paths(path.read_text(errors="ignore"))})
        kb_refs = []
        for kb_file in (ROOT / "knowledge_base").rglob("*.md"):
            text = kb_file.read_text(errors="ignore")
            if module_dir.name in text or module_dir.name in kb_file.name:
                kb_refs.append(rel(kb_file))
        modules.append(
            {
                "name": module_dir.name,
                "purpose": "Derived from module directory; owner should refine in KB.",
                "dependencies": deps_flat,
                "database_usage": db_refs,
                "routes": routes,
                "services": [],
                "kb_references": sorted(set(kb_refs)),
                "health_status": "unknown",
                "owner": "unassigned",
                "last_verified": datetime.now(timezone.utc).date().isoformat(),
            }
        )
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "modules": modules}


def write(data: dict) -> None:
    YAML_OUT.write_text(dump_yaml(data) + "\n", encoding="utf-8")
    REPORT_OUT.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Module Registry",
        "",
        f"Generated: {data['generated_at']}",
        "",
        "| Module | Routes | DB refs | KB refs | Owner | Health |",
        "|---|---:|---:|---:|---|---|",
    ]
    for module in data["modules"]:
        lines.append(
            f"| `{module['name']}` | {len(module['routes'])} | {len(module['database_usage'])} | {len(module['kb_references'])} | {module['owner']} | {module['health_status']} |"
        )
    REPORT_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    data = build()
    write(data)
    print(f"Wrote {YAML_OUT} and {REPORT_OUT}")


if __name__ == "__main__":
    main()
