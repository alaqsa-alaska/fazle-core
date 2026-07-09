"""Phase 3 FG2 tests — quality threshold and manual-review enforcement."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

# Ensure settings initialization can run during test collection.
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5432/postgres")

from modules.escort_lifecycle import build_release_draft
from modules.voice_processor import process_voice

pytestmark = pytest.mark.unit


def test_build_release_draft_low_confidence_requires_manual_review() -> None:
    draft = build_release_draft(
        {
            "employee_name": "Karim",
            "vessel": "AMENA-1",
            "date": "2026-07-01",
            "location": "Mongla",
            "confidence_score": 20,
            "raw_text": "escort duty slip release",
        }
    )
    assert "LOW OCR CONFIDENCE" in draft
    assert "DRAFT ONLY" in draft
    assert "admin must review" in draft


@pytest.mark.asyncio
async def test_voice_low_word_count_forces_text_fallback() -> None:
    with patch("modules.voice_processor._call_transcribe", new=AsyncMock(return_value="hello")):
        out = await process_voice("/tmp/fake.ogg")
    assert out["confident"] is False
    assert out["intent"] == "unknown"
    assert "টেক্সট" in out["reply"]
