#!/usr/bin/env python3
"""Sync active Knowledge Base Markdown into generated runtime resources."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
KB_ROOT = ROOT / "knowledge_base"
GENERATED = ROOT / "resources" / "generated_kb"
REPORT = ROOT / "reports" / "kb_sync_manifest.json"


def load_env_file() -> None:
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    for env_path in (
        ROOT / ".env",
        Path("/home/azim/secure-env-backup/runtime-services.env"),
        Path("/home/azim/.env"),
    ):
        if env_path.exists():
            load_dotenv(env_path, override=False)


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    metadata: dict[str, object] = {}
    for raw in text[4:end].splitlines():
        if ":" not in raw or raw.startswith(" "):
            continue
        key, _, value = raw.partition(":")
        value = value.strip().strip("'\"")
        if value.lower() in {"true", "false"}:
            metadata[key.strip()] = value.lower() == "true"
        else:
            metadata[key.strip()] = value
    return metadata, text[end + 4 :].lstrip()


def slugify(path: Path) -> str:
    value = rel(path).replace("/", "__")
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    return value.rsplit(".", 1)[0] + ".txt"


def normalize_generated_text(text: str) -> str:
    """Keep generated runtime text stable and free of trailing whitespace."""
    return "\n".join(line.rstrip() for line in text.splitlines()).strip() + "\n"


def scan() -> list[dict]:
    entries: list[dict] = []
    for path in sorted(KB_ROOT.rglob("*.md")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        metadata, body = parse_frontmatter(text)
        archived = "07_archived" in path.parts
        runtime_index = bool(metadata.get("runtime_index", True))
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        out_path = GENERATED / slugify(path)
        entries.append(
            {
                "source": rel(path),
                "target": rel(out_path),
                "sha256": digest,
                "version": metadata.get("version", digest[:12]),
                "metadata": metadata,
                "skip": archived or runtime_index is False,
                "skip_reason": "archived" if archived else ("runtime_index=false" if runtime_index is False else ""),
                "body": body,
            }
        )
    return entries


def write_generated(entries: list[dict]) -> list[dict]:
    GENERATED.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    previous: dict[str, str] = {}
    if REPORT.exists():
        try:
            old = json.loads(REPORT.read_text())
            previous = {item["source"]: item.get("sha256", "") for item in old.get("files", [])}
        except Exception:
            previous = {}
    for entry in entries:
        item = {k: v for k, v in entry.items() if k != "body"}
        item["changed"] = previous.get(entry["source"]) != entry["sha256"]
        if not entry["skip"]:
            target = ROOT / entry["target"]
            generated_text = (
                f"Source: {entry['source']}\n"
                f"SHA256: {entry['sha256']}\n"
                f"Version: {entry['version']}\n\n"
                f"{entry['body']}"
            )
            target.write_text(normalize_generated_text(generated_text), encoding="utf-8")
        manifest.append(item)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_dir": rel(GENERATED),
        "files": manifest,
        "indexed_count": sum(1 for item in manifest if not item["skip"]),
        "skipped_count": sum(1 for item in manifest if item["skip"]),
        "changed_count": sum(1 for item in manifest if item["changed"]),
    }
    REPORT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


async def apply_database(manifest: list[dict]) -> None:
    import asyncpg

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is required for --apply-db")
    conn = await asyncpg.connect(url)
    try:
        for item in manifest:
            if item["skip"]:
                continue
            content = (ROOT / item["target"]).read_text(encoding="utf-8")
            key = "kb:" + item["source"]
            await conn.execute(
                """
                INSERT INTO fazle_knowledge_base (key, value, category, subcategory, trigger_keywords, reply_text, is_active)
                VALUES ($1, $4, 'knowledge_base', $2, $3, $4, true)
                ON CONFLICT (key) DO UPDATE SET
                    value = EXCLUDED.value,
                    reply_text = EXCLUDED.reply_text,
                    trigger_keywords = EXCLUDED.trigger_keywords,
                    is_active = true
                """,
                key,
                item["source"].split("/")[1] if "/" in item["source"] else "root",
                [item["source"], item["sha256"][:12]],
                content,
            )
    finally:
        await conn.close()


async def maybe_rebuild_rag() -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from modules import rag

    await rag.rebuild_index()


async def main_async() -> None:
    load_env_file()
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply-db", action="store_true", help="Upsert generated KB into fazle_knowledge_base.")
    parser.add_argument("--rebuild-rag", action="store_true", help="Trigger in-process RAG rebuild after sync.")
    args = parser.parse_args()
    entries = scan()
    manifest = write_generated(entries)
    if args.apply_db:
        await apply_database(manifest)
    if args.rebuild_rag:
        await maybe_rebuild_rag()
    print(f"Wrote {REPORT}")


if __name__ == "__main__":
    asyncio.run(main_async())
