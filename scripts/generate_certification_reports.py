#!/usr/bin/env python3
"""Generate certification reports from baseline, KB audit, registry, and traceability data."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "certification"


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def pct(done: int, total: int) -> float:
    return round((done / total) * 100, 2) if total else 0.0


def write(path: Path, title: str, lines: list[str]) -> None:
    path.write_text("# " + title + "\n\n" + "\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    generated = datetime.now(timezone.utc).isoformat()
    baseline = load_json(ROOT / "reports" / "baseline" / "baseline_inventory.json", {})
    kb = load_json(ROOT / "reports" / "kb_audit" / "kb_audit_manifest.json", {})
    trace = load_json(ROOT / "reports" / "traceability_matrix.json", {"rows": []})
    kb_files = kb.get("files", [])
    kb_total = kb.get("files_scanned", len(kb_files))
    kb_good = sum(1 for item in kb_files if not item.get("missing_metadata") and not item.get("stale"))
    modules = baseline.get("modules", [])
    module_total = len(modules)
    covered_modules = {m for row in trace.get("rows", []) for m in row.get("modules", [])}
    routes = baseline.get("routes", [])
    routes_with_kb = {r for row in trace.get("rows", []) for r in row.get("api_routes", [])}
    ai_readiness_checks = [
        (ROOT / "modules" / "ollama_memory").exists(),
        (ROOT / "modules" / "ai_readonly_tools").exists(),
        (ROOT / "scripts" / "sync_knowledge_base_to_runtime.py").exists(),
        (ROOT / "db" / "migrations" / "012_ai_readonly_views.sql").exists(),
    ]
    ops_checks = [
        (ROOT / "modules" / "operations_health").exists(),
        bool(baseline.get("services")),
        bool(routes),
        bool((ROOT / "reports" / "module_registry.md").exists()),
    ]
    scores = {
        "KB Coverage %": pct(max(kb_good, 0), kb_total),
        "Module Coverage %": pct(len(covered_modules), module_total),
        "Workflow Coverage %": pct(len(routes_with_kb), len(routes)),
        "AI Readiness %": pct(sum(ai_readiness_checks), len(ai_readiness_checks)),
        "Operations Readiness %": pct(sum(ops_checks), len(ops_checks)),
    }
    common = [f"Generated: {generated}", ""]
    write(
        OUT / "knowledge_coverage_report.md",
        "Knowledge Coverage Report",
        common + [f"- KB files scanned: {kb_total}", f"- Metadata/staleness healthy files: {kb_good}", f"- KB Coverage: {scores['KB Coverage %']}%"],
    )
    write(
        OUT / "module_alignment_report.md",
        "Module Alignment Report",
        common + [f"- Modules discovered: {module_total}", f"- Modules with KB references: {len(covered_modules)}", f"- Module Coverage: {scores['Module Coverage %']}%"],
    )
    write(
        OUT / "ollama_readiness_report.md",
        "Ollama Readiness Report",
        common + [f"- AI Readiness: {scores['AI Readiness %']}%", "- Ollama memory and read-only tool modules are checked by file presence."],
    )
    write(
        OUT / "system_readiness_report.md",
        "System Readiness Report",
        common + [f"- Operations Readiness: {scores['Operations Readiness %']}%", f"- Workflow Coverage: {scores['Workflow Coverage %']}%", "", "## Scores", *[f"- {k}: {v}%" for k, v in scores.items()]],
    )
    (OUT / "certification_scores.json").write_text(json.dumps({"generated_at": generated, "scores": scores}, indent=2) + "\n")
    print(f"Wrote certification reports in {OUT}")


if __name__ == "__main__":
    main()
