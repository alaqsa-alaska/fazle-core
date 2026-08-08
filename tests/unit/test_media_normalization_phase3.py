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


@pytest.mark.asyncio
async def test_video_has_no_real_processing_placeholder_only() -> None:
    """S6 media audit (2026-08-08): 'video' is in the download-eligible type
    set (image/audio/document/video), but normalize_meta_media_message() has
    no video-processing branch at all -- it always falls through to the
    generic placeholder, even with a successful download and a caption.
    Honest acceptance evidence, not a bug fix: video content is never
    actually extracted/transcribed by this system."""
    with patch.object(mn, "_download_meta_media", new=AsyncMock(return_value="/tmp/fake.mp4")):
        with patch("os.path.exists", return_value=False):
            out = await mn.normalize_meta_media_message({
                "type": "video",
                "video": {"id": "m4", "mime_type": "video/mp4", "caption": "escort duty video"},
            })
    assert out["text"] == "[video message] escort duty video"
    assert out["meta"]["normalized"] is False


class TestMetaUnsupportedMessageTypesAreSilentlyDropped:
    """S6 media audit (2026-08-08): app.main._handle_meta_message() only
    special-cases type in {text, image, audio, document, video}. Any other
    inbound type (location, sticker, contacts, reaction, interactive,
    unknown/future types) leaves `text` as "" and hits the function's own
    `if not text: return` -- no reply, no admin draft, no outbound queue
    entry, no DB save. Proven here directly (not inferred from reading the
    code) so this honest gap is test-evidenced, matching the S6 mandate not
    to fabricate media support that doesn't exist."""

    async def _run(self, msg: dict) -> None:
        from app.main import _handle_meta_message

        with (
            patch("app.main._process_message", new=AsyncMock(return_value=("reply", None))) as process,
            patch("app.main._save_message", new=AsyncMock()) as save,
            patch("app.main.record_heartbeat", new=AsyncMock()),
            patch("app.main.fetch_one", new=AsyncMock(return_value=None)),
            patch("modules.outbound.enqueue", new=AsyncMock()) as enqueue,
        ):
            await _handle_meta_message(msg, {})
        process.assert_not_awaited()
        save.assert_not_awaited()
        enqueue.assert_not_awaited()

    async def test_location_message_is_silently_dropped(self):
        await self._run({
            "id": "wamid.test-location",
            "from": "01999999998",
            "type": "location",
            "location": {"latitude": 23.8, "longitude": 90.4},
        })

    async def test_sticker_message_is_silently_dropped(self):
        await self._run({
            "id": "wamid.test-sticker",
            "from": "01999999998",
            "type": "sticker",
            "sticker": {"id": "sticker123"},
        })

    async def test_unknown_future_type_is_silently_dropped(self):
        await self._run({
            "id": "wamid.test-unknown",
            "from": "01999999998",
            "type": "some_future_message_type",
        })
