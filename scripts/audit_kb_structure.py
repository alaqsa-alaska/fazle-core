#!/usr/bin/env python3
"""Audit Knowledge Base structure, metadata, duplicates, and stale content."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
KB_ROOT = ROOT / "knowledge_base"
OUT_DIR = ROOT / "reports" / "kb_audit"
REQUIRED = {"title", "owner", "status", "last_verified", "runtime_index"}


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def parse_frontmatter(text: str) -> dict:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    metadata: dict[str, object] = {}
    for raw in text[4:end].splitlines():
        if ":" not in raw or raw.startswith(" "):
            continue
        key, _, value = raw.partition(":")
        value = value.strip()
        if value.lower() in {"true", "false"}:
            metadata[key.strip()] = value.lower() == "true"
        elif value.startswith("[") and value.endswith("]"):
            metadata[key.strip()] = [item.strip().strip("'\"") for item in value[1:-1].split(",") if item.strip()]
        else:
            metadata[key.strip()] = value.strip("'\"")
    return metadata


def content_fingerprint(text: str) -> str:
    text = re.sub(r"^---.*?---", "", text, flags=re.S)
    text = re.sub(r"\s+", " ", text.lower()).strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stale(metadata: dict, stale_days: int) -> bool:
    raw = str(metadata.get("last_verified") or "")
    if not raw:
        return True
    try:
        verified = date.fromisoformat(raw[:10])
    except ValueError:
        return True
    return (date.today() - verified).days > stale_days


def audit(stale_days: int) -> dict:
    files = sorted(KB_ROOT.rglob("*.md"))
    by_hash: dict[str, list[str]] = {}
    rows: list[dict] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        metadata = parse_frontmatter(text)
        fp = content_fingerprint(text)
        by_hash.setdefault(fp, []).append(rel(path))
        missing = sorted(REQUIRED - set(metadata.keys()))
        is_archived = "07_archived" in path.parts
        runtime_index = bool(metadata.get("runtime_index", not is_archived))
        rows.append(
            {
                "path": rel(path),
                "metadata": metadata,
                "missing_metadata": missing,
                "stale": stale(metadata, stale_days),
                "archived": is_archived,
                "runtime_index": runtime_index and not is_archived,
                "sha256": fp,
                "bytes": path.stat().st_size,
            }
        )
    duplicates = {fp: paths for fp, paths in by_hash.items() if len(paths) > 1}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stale_after_days": stale_days,
        "files_scanned": len(files),
        "files": rows,
        "duplicates": duplicates,
        "missing_metadata_count": sum(1 for row in rows if row["missing_metadata"]),
        "stale_count": sum(1 for row in rows if row["stale"]),
        "runtime_index_count": sum(1 for row in rows if row["runtime_index"]),
    }


def write_report(data: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "kb_audit_manifest.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    lines = [
        "# Knowledge Base Audit Report",
        "",
        f"Generated: {data['generated_at']}",
        f"Files scanned: **{data['files_scanned']}**",
        f"Runtime-index eligible: **{data['runtime_index_count']}**",
        f"Files missing metadata: **{data['missing_metadata_count']}**",
        f"Stale files: **{data['stale_count']}**",
        f"Duplicate content groups: **{len(data['duplicates'])}**",
        "",
        "## Missing Metadata",
        "| File | Missing fields |",
        "|---|---|",
    ]
    for row in data["files"]:
        if row["missing_metadata"]:
            lines.append(f"| `{row['path']}` | {', '.join(row['missing_metadata'])} |")
    lines += ["", "## Stale Content", "| File | Last verified |", "|---|---|"]
    for row in data["files"]:
        if row["stale"]:
            lines.append(f"| `{row['path']}` | {row['metadata'].get('last_verified', '')} |")
    lines += ["", "## Duplicates", "| SHA256 | Files |", "|---|---|"]
    for fp, paths in data["duplicates"].items():
        lines.append(f"| `{fp[:12]}` | {', '.join(f'`{p}`' for p in paths)} |")
    lines += [
        "",
        "## Result",
        "KB audit completed. This script performs static file reads and report writes only.",
        "",
    ]
    (OUT_DIR / "kb_audit_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stale-days", type=int, default=60)
    args = parser.parse_args()
    data = audit(args.stale_days)
    write_report(data)
    print(f"Wrote {OUT_DIR / 'kb_audit_report.md'}")


if __name__ == "__main__":
    main()
