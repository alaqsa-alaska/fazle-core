#!/usr/bin/env python3
"""Add missing governance metadata to Knowledge Base Markdown files."""
from __future__ import annotations

import argparse
import re
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
KB_ROOT = ROOT / "knowledge_base"
REQUIRED_ORDER = ["title", "owner", "status", "last_verified", "runtime_index"]


def title_from(path: Path, body: str) -> str:
    for line in body.splitlines():
        clean = line.lstrip("\ufeff").strip()
        if clean.startswith("# "):
            return clean[2:].strip()
    stem = path.stem.replace("_", " ").replace("-", " ").strip()
    return " ".join(word.capitalize() for word in stem.split()) or path.name


def parse_frontmatter(text: str) -> tuple[dict[str, str], list[str], str]:
    if not text.startswith("---\n"):
        return {}, [], text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, [], text
    raw_lines = text[4:end].splitlines()
    metadata: dict[str, str] = {}
    for line in raw_lines:
        if ":" not in line or line.startswith((" ", "\t")):
            continue
        key, _, value = line.partition(":")
        metadata[key.strip()] = value.strip()
    return metadata, raw_lines, text[end + 4 :].lstrip("\n")


def render_frontmatter(existing_lines: list[str], metadata: dict[str, str]) -> str:
    present = {line.partition(":")[0].strip() for line in existing_lines if ":" in line and not line.startswith((" ", "\t"))}
    lines = list(existing_lines)
    insert_at = 0
    for required in REQUIRED_ORDER:
        if required not in present:
            lines.insert(insert_at, f"{required}: {metadata[required]}")
            insert_at += 1
    return "---\n" + "\n".join(lines).rstrip() + "\n---\n\n"


def normalize_file(path: Path, verified_date: str) -> bool:
    original = path.read_text(encoding="utf-8-sig", errors="ignore")
    metadata, frontmatter_lines, body = parse_frontmatter(original)
    archived = "07_archived" in path.parts
    defaults = {
        "title": title_from(path, body),
        "owner": "Fazle Core Admin",
        "status": "archived" if archived else "active",
        "last_verified": verified_date,
        "runtime_index": "false" if archived else "true",
    }
    missing = [key for key in REQUIRED_ORDER if key not in metadata]
    if not missing:
        return False
    updated = render_frontmatter(frontmatter_lines, defaults) + body
    if updated != original:
        path.write_text(updated, encoding="utf-8")
        return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    changed: list[str] = []
    for path in sorted(KB_ROOT.rglob("*.md")):
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
        metadata, _, body = parse_frontmatter(text)
        if all(key in metadata for key in REQUIRED_ORDER):
            continue
        if args.dry_run:
            changed.append(path.relative_to(ROOT).as_posix())
            continue
        if normalize_file(path, args.date):
            changed.append(path.relative_to(ROOT).as_posix())
    print(f"{'Would update' if args.dry_run else 'Updated'} {len(changed)} KB files")
    for item in changed:
        print(item)


if __name__ == "__main__":
    main()
