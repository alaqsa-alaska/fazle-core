"""FastAPI router for Ollama memory inspection."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from . import get_recent_questions, list_memory_stats, recall_facts


router = APIRouter(prefix="/api/ollama-memory", tags=["ollama-memory"])


@router.get("/stats")
async def stats():
    try:
        return await list_memory_stats()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/questions")
async def questions(limit: int = 20):
    return await get_recent_questions(limit=min(limit, 100))


@router.get("/facts/{subject_type}/{subject_key}")
async def facts(subject_type: str, subject_key: str, limit: int = 10):
    return await recall_facts(subject_type, subject_key, limit=min(limit, 50))
