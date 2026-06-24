#!/usr/bin/env python3
"""Generate Phase 0 baseline inventories for Fazle Core."""
from __future__ import annotations

import ast
import json
import re
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "reports" / "baseline"


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def files_under(*parts: str, suffixes: tuple[str, ...] | None = None) -> list[str]:
    base = ROOT.joinpath(*parts)
    if not base.exists():
        return []
    items: list[str] = []
    for path in base.rglob("*"):
        if path.is_file() and (suffixes is None or path.suffix in suffixes):
            if "__pycache__" not in path.parts and ".pytest_cache" not in path.parts:
                items.append(rel(path))
    return sorted(items)


def discover_modules() -> list[dict]:
    modules_dir = ROOT / "modules"
    modules: list[dict] = []
    if not modules_dir.exists():
        return modules
    for item in sorted(p for p in modules_dir.iterdir() if p.is_dir()):
        py_files = list(item.rglob("*.py"))
        routers = []
        imports_db = False
        for py in py_files:
            text = py.read_text(errors="ignore")
            imports_db = imports_db or any(token in text for token in ("asyncpg", "app.database", "fetch_all", "execute("))
            if "APIRouter" in text or "@router." in text:
                routers.append(rel(py))
        modules.append(
            {
                "name": item.name,
                "files": len(py_files),
                "has_router": bool(routers),
                "router_files": routers,
                "uses_database": imports_db,
                "readme": rel(item / "README.md") if (item / "README.md").exists() else None,
            }
        )
    return modules


def discover_routes() -> list[dict]:
    routes: list[dict] = []
    pattern = re.compile(r"@(app|router)\.(get|post|put|patch|delete)\(([^)]*)\)")
    for py in [ROOT / "app" / "main.py", *ROOT.glob("modules/**/*.py")]:
        if not py.exists() or "__pycache__" in py.parts:
            continue
        text = py.read_text(errors="ignore")
        for match in pattern.finditer(text):
            route_arg = match.group(3).split(",", 1)[0].strip().strip("'\"")
            routes.append(
                {
                    "file": rel(py),
                    "decorator": match.group(1),
                    "method": match.group(2).upper(),
                    "path": route_arg,
                }
            )
    return sorted(routes, key=lambda r: (r["path"], r["method"], r["file"]))


def discover_database() -> dict:
    sql_files = files_under("db", suffixes=(".sql",)) + files_under("migrations", suffixes=(".sql",))
    table_names: set[str] = set()
    view_names: set[str] = set()
    for file_name in sql_files:
        text = (ROOT / file_name).read_text(errors="ignore")
        table_names.update(re.findall(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-zA-Z0-9_\.]+)", text, flags=re.I))
        view_names.update(re.findall(r"CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+([a-zA-Z0-9_\.]+)", text, flags=re.I))
    db_refs: dict[str, list[str]] = {}
    for py in ROOT.glob("**/*.py"):
        if any(part in py.parts for part in ("__pycache__", ".git", "node_modules")):
            continue
        text = py.read_text(errors="ignore")
        refs = sorted(set(re.findall(r"\b(?:wbom|fazle|ai_read|ai_memory)_[a-zA-Z0-9_]+", text)))
        if refs:
            db_refs[rel(py)] = refs
    return {
        "sql_files": sorted(sql_files),
        "declared_tables": sorted(table_names),
        "declared_views": sorted(view_names),
        "code_references": db_refs,
    }


def discover_services() -> list[dict]:
    service_hits: list[dict] = []
    patterns = {
        "Ollama": ["OLLAMA", "ollama", "11434"],
        "Groq": ["GROQ", "groq"],
        "GitHub Models": ["GITHUB_MODELS", "github_models", "models.github.ai"],
        "PostgreSQL": ["DATABASE_URL", "asyncpg", "postgresql://"],
        "Redis": ["REDIS", "redis"],
        "WhatsApp Bridge": ["whatsapp-bridge", "mcp1", "mcp2", "BRIDGE"],
        "Meta API": ["META_", "webhook/meta"],
        "Media Processor": ["MEDIA_PROCESSOR", "8090"],
        "LocationWhere": ["LocationWhere", "locationwhere"],
        "SMS Gateway": ["SMSGateway", "sms gateway", "SMS"],
    }
    for name, tokens in patterns.items():
        files: set[str] = set()
        for py in [*ROOT.glob("app/**/*.py"), *ROOT.glob("modules/**/*.py"), *ROOT.glob("scripts/**/*.py")]:
            if "__pycache__" in py.parts:
                continue
            text = py.read_text(errors="ignore")
            if any(token in text for token in tokens):
                files.add(rel(py))
        service_hits.append({"service": name, "referenced_by": sorted(files)})
    return service_hits


def discover_kb() -> dict:
    md_files = files_under("knowledge_base", suffixes=(".md",))
    by_folder: dict[str, int] = {}
    for file_name in md_files:
        parts = Path(file_name).parts
        folder = parts[1] if len(parts) > 1 else "root"
        by_folder[folder] = by_folder.get(folder, 0) + 1
    return {
        "markdown_files": md_files,
        "markdown_count": len(md_files),
        "by_folder": dict(sorted(by_folder.items())),
        "root_files": files_under("knowledge_base", suffixes=(".yaml", ".yml", ".json", ".txt")),
    }


def discover_frontend() -> dict:
    pages = files_under("app", "static", suffixes=(".html", ".js", ".css"))
    pages += files_under("frontend", suffixes=(".html", ".js", ".css", ".ts", ".tsx", ".jsx"))
    return {"files": sorted(pages), "count": len(pages)}


def write_report(data: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "baseline_inventory.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    lines: list[str] = [
        "# Phase 0 Baseline Report",
        "",
        f"Generated: {data['generated_at']}",
        f"Repository: `{ROOT}`",
        "",
        "## Module Inventory",
        f"Modules discovered: **{len(data['modules'])}**",
        "",
        "| Module | Python files | Router | DB use | README |",
        "|---|---:|---|---|---|",
    ]
    for module in data["modules"]:
        lines.append(
            f"| `{module['name']}` | {module['files']} | {module['has_router']} | {module['uses_database']} | {module['readme'] or ''} |"
        )
    lines += [
        "",
        "## Service Inventory",
        "| Service | Referenced files |",
        "|---|---:|",
    ]
    for svc in data["services"]:
        lines.append(f"| {svc['service']} | {len(svc['referenced_by'])} |")
    lines += [
        "",
        "## Route Inventory",
        f"Routes discovered: **{len(data['routes'])}**",
        "",
        "| Method | Path | File |",
        "|---|---|---|",
    ]
    for route in data["routes"]:
        lines.append(f"| {route['method']} | `{route['path']}` | `{route['file']}` |")
    lines += [
        "",
        "## Database Inventory",
        f"SQL files: **{len(data['database']['sql_files'])}**",
        f"Declared tables: **{len(data['database']['declared_tables'])}**",
        f"Declared views: **{len(data['database']['declared_views'])}**",
        "",
        "Declared tables/views are from static SQL inspection only; no production database was queried.",
        "",
        "## Knowledge Base Inventory",
        f"Markdown files: **{data['kb']['markdown_count']}**",
        "",
        "| Folder | Markdown files |",
        "|---|---:|",
    ]
    for folder, count in data["kb"]["by_folder"].items():
        lines.append(f"| `{folder}` | {count} |")
    lines += [
        "",
        "## Frontend Inventory",
        f"Frontend/static files: **{data['frontend']['count']}**",
        "",
        "## Integration Inventory",
        "Integration references are captured in `baseline_inventory.json` under `services` and `database.code_references`.",
        "",
        "## Phase 0 Result",
        "Read-only baseline audit completed. No production logic, database schema, bridge store, or service configuration was modified.",
        "",
    ]
    (OUT_DIR / "baseline_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "modules": discover_modules(),
        "services": discover_services(),
        "routes": discover_routes(),
        "database": discover_database(),
        "kb": discover_kb(),
        "frontend": discover_frontend(),
    }
    write_report(data)
    print(f"Wrote {OUT_DIR / 'baseline_report.md'}")


if __name__ == "__main__":
    main()
