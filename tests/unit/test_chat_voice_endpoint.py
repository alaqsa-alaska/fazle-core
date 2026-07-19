from __future__ import annotations

import asyncio
import os

import pytest

pytestmark = pytest.mark.asyncio


def _tiny_webm_bytes() -> bytes:
    # Not a real decodable webm — the route never decodes audio itself,
    # it only forwards the file path to modules.voice_processor (mocked
    # in these tests), so arbitrary non-empty bytes are sufficient.
    return b"\x1a\x45\xdf\xa3fake-webm-payload"


async def _post_voice(ac, headers, **kwargs):
    files = kwargs.pop("files", None) or {
        "audio": ("voice-1.webm", _tiny_webm_bytes(), "audio/webm"),
    }
    data = kwargs.pop("data", {"language": "auto", "duration_ms": "1500"})
    return await ac.post("/chat/voice", headers=headers, files=files, data=data)


@pytest.fixture
def auth_headers():
    from app.config import get_settings
    return {"X-Internal-Key": get_settings().internal_api_key}


@pytest.fixture
def voice_upload_dir(tmp_path, monkeypatch):
    from app import main
    monkeypatch.setattr(main, "_VOICE_UPLOAD_DIR", str(tmp_path))
    return tmp_path


async def _client():
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def test_chat_voice_requires_auth(voice_upload_dir):
    async with await _client() as ac:
        response = await _post_voice(ac, headers={})
    assert response.status_code == 403


async def test_chat_voice_rejects_unsupported_extension(auth_headers, voice_upload_dir):
    async with await _client() as ac:
        response = await _post_voice(
            ac, headers=auth_headers,
            files={"audio": ("note.txt", b"hello", "text/plain")},
        )
    assert response.status_code == 400
    assert "Unsupported audio type" in response.json()["detail"]


async def test_chat_voice_rejects_unsupported_content_type(auth_headers, voice_upload_dir):
    async with await _client() as ac:
        response = await _post_voice(
            ac, headers=auth_headers,
            files={"audio": ("voice.webm", _tiny_webm_bytes(), "application/zip")},
        )
    assert response.status_code == 400
    assert "content type" in response.json()["detail"].lower()


async def test_chat_voice_rejects_empty_file(auth_headers, voice_upload_dir):
    async with await _client() as ac:
        response = await _post_voice(
            ac, headers=auth_headers,
            files={"audio": ("voice.webm", b"", "audio/webm")},
        )
    assert response.status_code == 400
    assert "Empty audio" in response.json()["detail"]


async def test_chat_voice_rejects_oversized_file(auth_headers, voice_upload_dir):
    from app import main
    oversized = b"0" * (main._VOICE_MAX_SIZE + 1)
    async with await _client() as ac:
        response = await _post_voice(
            ac, headers=auth_headers,
            files={"audio": ("voice.webm", oversized, "audio/webm")},
        )
    assert response.status_code == 400
    assert "too large" in response.json()["detail"].lower()


async def test_chat_voice_rejects_duration_far_over_max(auth_headers, voice_upload_dir):
    async with await _client() as ac:
        response = await _post_voice(
            ac, headers=auth_headers,
            data={"language": "auto", "duration_ms": "999999"},
        )
    assert response.status_code == 400
    assert "duration" in response.json()["detail"].lower()


async def test_chat_voice_success_confident_transcript(auth_headers, voice_upload_dir, monkeypatch):
    from modules import voice_processor

    async def fake_process_voice(file_path, skip_intent=False):
        assert skip_intent is True
        assert os.path.exists(file_path)
        return {
            "transcript": "কাল সকালে আসবো",
            "word_count": 3,
            "confident": True,
            "language_hint": "bn",
            "intent": "not_classified",
            "reply": "",
        }

    monkeypatch.setattr(voice_processor, "process_voice", fake_process_voice)

    async with await _client() as ac:
        response = await _post_voice(ac, headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["transcript"] == "কাল সকালে আসবো"
    assert data["language"] == "bn"
    assert data["confident"] is True
    assert data["confidence"] is None
    assert data["editable"] is True
    assert data["warning"] is None
    assert data["duration_ms"] == 1500

    # temp file must not survive the request
    assert list(voice_upload_dir.iterdir()) == []


async def test_chat_voice_low_confidence_returns_warning_not_fabricated_text(
    auth_headers, voice_upload_dir, monkeypatch,
):
    from modules import voice_processor

    async def fake_process_voice(file_path, skip_intent=False):
        return {
            "transcript": "",
            "word_count": 0,
            "confident": False,
            "language_hint": "unknown",
            "intent": "unknown",
            "reply": "ভয়েস বার্তাটি ভালোভাবে বোঝা যায়নি। অনুগ্রহ করে টেক্সট আকারে পাঠান।",
        }

    monkeypatch.setattr(voice_processor, "process_voice", fake_process_voice)

    async with await _client() as ac:
        response = await _post_voice(ac, headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["transcript"] == ""
    assert data["confident"] is False
    assert data["warning"]
    assert list(voice_upload_dir.iterdir()) == []


async def test_chat_voice_processor_exception_returns_502_and_cleans_up(
    auth_headers, voice_upload_dir, monkeypatch,
):
    from modules import voice_processor

    async def fake_process_voice(file_path, skip_intent=False):
        raise RuntimeError("media-processor unreachable")

    monkeypatch.setattr(voice_processor, "process_voice", fake_process_voice)

    async with await _client() as ac:
        response = await _post_voice(ac, headers=auth_headers)

    assert response.status_code == 502
    assert list(voice_upload_dir.iterdir()) == []


async def test_chat_voice_processor_timeout_returns_504_and_cleans_up(
    auth_headers, voice_upload_dir, monkeypatch,
):
    from modules import voice_processor

    async def fake_process_voice(file_path, skip_intent=False):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(voice_processor, "process_voice", fake_process_voice)

    async with await _client() as ac:
        response = await _post_voice(ac, headers=auth_headers)

    assert response.status_code == 504
    assert list(voice_upload_dir.iterdir()) == []


async def test_chat_voice_never_calls_llm_reply_generation(auth_headers, voice_upload_dir, monkeypatch):
    """The route must not submit the transcript to any LLM/chat orchestrator itself."""
    from modules import voice_processor

    called = {"orchestrate": False}

    async def fake_process_voice(file_path, skip_intent=False):
        return {
            "transcript": "hello",
            "word_count": 1,
            "confident": True,
            "language_hint": "en",
            "intent": "not_classified",
            "reply": "",
        }

    async def fake_orchestrate(*args, **kwargs):
        called["orchestrate"] = True
        raise AssertionError("chat orchestrator must not be invoked by /chat/voice")

    monkeypatch.setattr(voice_processor, "process_voice", fake_process_voice)
    monkeypatch.setattr("modules.ai_console.orchestrate_console_request", fake_orchestrate, raising=False)
    monkeypatch.setattr("modules.admin_chat_lab.orchestrate_chat_message", fake_orchestrate, raising=False)

    async with await _client() as ac:
        response = await _post_voice(ac, headers=auth_headers)

    assert response.status_code == 200
    assert called["orchestrate"] is False
