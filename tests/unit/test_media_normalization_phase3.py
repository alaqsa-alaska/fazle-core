"""Phase 3 FG2 tests — media normalization (audio/image/document) remains advisory-only."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from modules import media_normalization as mn

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_audio_transcript_replaces_placeholder() -> None:
    with patch.object(mn, "_download_meta_media", new=AsyncMock(return_value="/tmp/fake.ogg")), \
         patch.object(mn, "process_voice", new=AsyncMock(return_value={"transcript": "আমি বেতন চাই", "language_hint": "bn", "confident": True})):
        with patch("os.path.exists", return_value=False):
            out = await mn.normalize_meta_media_message({"type": "audio", "audio": {"id": "m1", "mime_type": "audio/ogg"}})
    assert out["text"].startswith("[audio message]")
    assert "আমি বেতন চাই" in out["text"]
    assert out["meta"]["normalized"] is True


@pytest.mark.asyncio
async def test_image_ocr_preserves_marker_and_adds_text() -> None:
    with patch.object(mn, "_download_meta_media", new=AsyncMock(return_value="/tmp/fake.jpg")), \
         patch.object(mn, "process_image", new=AsyncMock(return_value={"raw_text": "escort slip text", "slip_type": "escort_slip", "is_duplicate": False})):
        with patch("os.path.exists", return_value=False):
            out = await mn.normalize_meta_media_message({"type": "image", "image": {"id": "m2", "mime_type": "image/jpeg"}})
    assert out["text"].startswith("[image message]")
    assert "escort slip text" in out["text"]
    assert out["meta"]["slip_type"] == "escort_slip"


@pytest.mark.asyncio
async def test_pdf_document_extracts_text() -> None:
    with patch.object(mn, "_download_meta_media", new=AsyncMock(return_value="/tmp/fake.pdf")), \
         patch.object(mn, "process_document", new=AsyncMock(return_value={
             "extracted_text": "candidate CV text",
             "doc_type": "cv",
             "confidence_score": 80,
         })):
        with patch("os.path.exists", return_value=False):
            out = await mn.normalize_meta_media_message({
                "type": "document",
                "document": {"id": "m3", "mime_type": "application/pdf", "filename": "cv.pdf"},
            })
    assert out["text"] == "[document message] candidate CV text"
    assert out["meta"]["normalized"] is True
    assert out["meta"]["doc_type"] == "cv"


@pytest.mark.asyncio
async def test_missing_media_id_stays_placeholder() -> None:
    out = await mn.normalize_meta_media_message({"type": "image", "image": {}})
    assert out["text"] == "[image message]"
    assert out["meta"]["normalized"] is False
