"""
KB Upload — API routes
POST /admin/kb/upload    → upload PDF/DOCX/TXT → KB
GET  /admin/kb/list      → list KB entries by category
GET  /admin/kb/stats     → KB statistics
POST /admin/kb/delete    → delete KB entries by upload_id or entry_id
"""
from __future__ import annotations
import asyncio
import logging
import os
import pathlib
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from app.config import get_settings

log = logging.getLogger("fazle.kb_upload")
router = APIRouter(tags=["kb_upload"])

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".text", ".md", ".csv"}
UPLOAD_DIR = "/home/azim/core/resources/uploads"
RESOURCES_DIR = "/home/azim/core/resources"

_KB_KEY_HEADER = APIKeyHeader(name="X-Internal-Key", auto_error=False)


async def _require_api_key(key: str = Depends(_KB_KEY_HEADER)) -> None:
    settings = get_settings()
    if key and key == settings.internal_api_key:
        return
    if key:
        try:
            from modules import rbac
            admin = await rbac.get_admin_by_api_key(key)
            if admin and admin.get("status") == "active":
                return
        except Exception:
            pass
    raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/admin/kb/upload")
async def upload_to_kb(
    _: None = Depends(_require_api_key),
    file: UploadFile = File(...),
    category: str = Form("uploaded"),
    subcategory: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
):
    """Upload PDF/DOCX/TXT → extract → chunk → DB + file → RAG rebuild."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename")

    safe_name = pathlib.Path(file.filename).name
    ext = os.path.splitext(safe_name)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ext}. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    content_bytes = await file.read()
    if len(content_bytes) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large: {len(content_bytes)//1024}KB. Max: {MAX_FILE_SIZE//1024}KB",
        )
    if len(content_bytes) == 0:
        raise HTTPException(status_code=400, detail="Empty file")

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    upload_id = f"kb_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    temp_path = os.path.join(UPLOAD_DIR, f"{upload_id}{ext}")

    with open(temp_path, "wb") as fh:
        fh.write(content_bytes)
    log.info("File saved: %s (%d bytes)", temp_path, len(content_bytes))

    try:
        from modules.kb_upload.parser import extract_text, chunk_text
        full_text = extract_text(temp_path, file.filename)
    except Exception as e:
        os.remove(temp_path)
        log.error("Text extraction failed: %s", e)
        raise HTTPException(status_code=422, detail=f"Text extraction failed: {str(e)}")

    if not full_text or len(full_text.strip()) < 10:
        os.remove(temp_path)
        raise HTTPException(status_code=422, detail="No text could be extracted from file")

    from modules.kb_upload.parser import chunk_text
    chunks = chunk_text(full_text)
    if not chunks:
        os.remove(temp_path)
        raise HTTPException(status_code=422, detail="No chunks generated from file")

    tag_list = None
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    txt_filename = f"{upload_id}.txt"
    txt_path = os.path.join(RESOURCES_DIR, txt_filename)
    try:
        with open(txt_path, "w", encoding="utf-8") as fh:
            fh.write(f"# Source: {safe_name}\n")
            fh.write(f"# Category: {category}\n")
            fh.write(f"# Upload ID: {upload_id}\n")
            fh.write(f"# Uploaded: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            fh.write(full_text)
        log.info("TXT saved: %s", txt_path)
    except Exception as e:
        log.warning("Failed to save txt: %s", e)

    from app.database import execute
    inserted = 0
    for i, chunk in enumerate(chunks):
        chunk_key = f"{upload_id}_chunk_{i + 1:03d}"
        try:
            await execute(
                """
                INSERT INTO fazle_knowledge_base
                  (category, subcategory, key, value, reply_text,
                   language, confidence, tags, is_active)
                VALUES ($1, $2, $3, $4, $5, 'bn-en', 0.85, $6, true)
                """,
                category,
                subcategory or safe_name,
                chunk_key,
                chunk[:200],
                chunk,
                tag_list,
            )
            inserted += 1
        except Exception as e:
            log.warning("Chunk %d insert failed: %s", i + 1, e)

    log.info("KB upload complete: %s → %d chunks, upload_id=%s",
             safe_name, inserted, upload_id)

    try:
        from modules.rag import build_index
        asyncio.create_task(build_index())
        log.info("RAG index rebuild scheduled (background)")
    except Exception as e:
        log.warning("RAG rebuild schedule failed: %s", e)

    return {
        "ok": True,
        "upload_id": upload_id,
        "filename": file.filename,
        "file_type": ext,
        "total_chars": len(full_text),
        "total_chunks": len(chunks),
        "inserted": inserted,
        "category": category,
        "rag_rebuild": "scheduled",
        "txt_path": txt_filename,
    }


@router.get("/admin/kb/stats")
async def kb_stats(_: None = Depends(_require_api_key)):
    """KB statistics by category."""
    from app.database import fetch_all
    rows = await fetch_all(
        """
        SELECT
          category,
          COUNT(*) as n,
          SUM(CASE WHEN is_active THEN 1 ELSE 0 END) as active,
          MAX(created_at) as newest
        FROM fazle_knowledge_base
        GROUP BY category
        ORDER BY n DESC
        """
    )
    total = sum(r["n"] for r in rows)
    return {"stats": [dict(r) for r in rows], "total": total}


@router.get("/admin/kb/list")
async def kb_list(
    _: None = Depends(_require_api_key),
    category: Optional[str] = None,
    upload_id: Optional[str] = None,
    limit: int = 50,
):
    """List KB entries, optionally filtered by category or upload_id."""
    from app.database import fetch_all

    if upload_id:
        rows = await fetch_all(
            """
            SELECT id, category, subcategory, key,
              LEFT(value, 100) as value_preview,
              LEFT(reply_text, 200) as reply_preview,
              is_active, created_at
            FROM fazle_knowledge_base
            WHERE key LIKE $1
            ORDER BY key
            LIMIT $2
            """,
            f"{upload_id}%", limit,
        )
    elif category:
        rows = await fetch_all(
            """
            SELECT id, category, subcategory, key,
              LEFT(value, 100) as value_preview,
              LEFT(reply_text, 200) as reply_preview,
              is_active, created_at
            FROM fazle_knowledge_base
            WHERE category = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            category, limit,
        )
    else:
        rows = await fetch_all(
            """
            SELECT id, category, subcategory, key,
              LEFT(value, 100) as value_preview,
              LEFT(reply_text, 200) as reply_preview,
              is_active, created_at
            FROM fazle_knowledge_base
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit,
        )
    return {"entries": [dict(r) for r in rows], "count": len(rows)}


class _DeleteBody(BaseModel):
    upload_id: Optional[str] = None
    entry_id: Optional[int] = None


@router.post("/admin/kb/delete")
async def kb_delete(
    _: None = Depends(_require_api_key),
    body: _DeleteBody = _DeleteBody(),
):
    """Delete KB entries by upload_id (all chunks) or single entry_id."""
    upload_id = body.upload_id
    entry_id = body.entry_id
    from app.database import execute
    import glob

    if upload_id:
        await execute(
            "DELETE FROM fazle_knowledge_base WHERE key LIKE $1",
            f"{upload_id}%",
        )
        for f in glob.glob(f"{RESOURCES_DIR}/{upload_id}*"):
            try:
                os.remove(f)
            except Exception:
                pass
        for f in glob.glob(f"{UPLOAD_DIR}/{upload_id}*"):
            try:
                os.remove(f)
            except Exception:
                pass
        try:
            from modules.rag import build_index
            await build_index()
        except Exception:
            pass
        return {"ok": True, "deleted_upload": upload_id}

    elif entry_id:
        await execute("DELETE FROM fazle_knowledge_base WHERE id = $1", entry_id)
        return {"ok": True, "deleted_id": entry_id}

    else:
        raise HTTPException(status_code=400, detail="upload_id or entry_id required")
