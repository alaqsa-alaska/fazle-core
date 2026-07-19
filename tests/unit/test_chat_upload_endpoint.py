from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.asyncio


@pytest.fixture
def auth_headers():
    from app.config import get_settings
    return {"X-Internal-Key": get_settings().internal_api_key}


@pytest.fixture
def upload_temp_dir(tmp_path, monkeypatch):
    from app import main
    monkeypatch.setattr(main, "_UPLOAD_TEMP_DIR", str(tmp_path))
    return tmp_path


async def _client():
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def test_chat_upload_requires_auth(upload_temp_dir):
    async with await _client() as ac:
        response = await ac.post(
            "/chat/upload", files={"file": ("a.txt", b"hello", "text/plain")},
        )
    assert response.status_code == 403


async def test_chat_upload_valid_text(auth_headers, upload_temp_dir):
    async with await _client() as ac:
        response = await ac.post(
            "/chat/upload", headers=auth_headers,
            files={"file": ("notes.txt", "hello world".encode("utf-8"), "text/plain")},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["extracted_text"] == "hello world"
    assert data["type"] == "txt"
    assert data["temporary"] is True
    assert "attachment_id" in data
    assert list(upload_temp_dir.iterdir()) == []


async def test_chat_upload_valid_markdown(auth_headers, upload_temp_dir):
    async with await _client() as ac:
        response = await ac.post(
            "/chat/upload", headers=auth_headers,
            files={"file": ("readme.md", b"# Title\nbody", "text/markdown")},
        )
    assert response.status_code == 200
    assert "# Title" in response.json()["extracted_text"]


async def test_chat_upload_valid_csv(auth_headers, upload_temp_dir):
    csv_bytes = b"name,age\nAlice,30\nBob,25\n"
    async with await _client() as ac:
        response = await ac.post(
            "/chat/upload", headers=auth_headers,
            files={"file": ("data.csv", csv_bytes, "text/csv")},
        )
    assert response.status_code == 200
    data = response.json()
    assert "name" in data["extracted_text"]
    assert "Alice" in data["extracted_text"]
    assert data["type"] == "csv"


async def test_chat_upload_csv_caps_rows_with_warning(auth_headers, upload_temp_dir):
    rows = "\n".join(f"row{i},{i}" for i in range(200))
    csv_bytes = f"a,b\n{rows}\n".encode("utf-8")
    async with await _client() as ac:
        response = await ac.post(
            "/chat/upload", headers=auth_headers,
            files={"file": ("big.csv", csv_bytes, "text/csv")},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["warning"] is not None
    assert "50" in data["warning"]


async def test_chat_upload_valid_pdf(auth_headers, upload_temp_dir, monkeypatch):
    def fake_extract_text(filepath, filename):
        return "extracted pdf body text"

    monkeypatch.setattr("modules.kb_upload.parser.extract_text", fake_extract_text)

    async with await _client() as ac:
        response = await ac.post(
            "/chat/upload", headers=auth_headers,
            files={"file": ("report.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["extracted_text"] == "extracted pdf body text"
    assert data["warning"] is None


async def test_chat_upload_scanned_pdf_honest_empty_warning(auth_headers, upload_temp_dir, monkeypatch):
    def fake_extract_text(filepath, filename):
        return ""

    monkeypatch.setattr("modules.kb_upload.parser.extract_text", fake_extract_text)

    async with await _client() as ac:
        response = await ac.post(
            "/chat/upload", headers=auth_headers,
            files={"file": ("scanned.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["extracted_text"] == ""
    assert "scanned" in data["warning"].lower()


async def test_chat_upload_pdf_extraction_exception_is_422(auth_headers, upload_temp_dir, monkeypatch):
    def fake_extract_text(filepath, filename):
        raise RuntimeError("encrypted PDF")

    monkeypatch.setattr("modules.kb_upload.parser.extract_text", fake_extract_text)

    async with await _client() as ac:
        response = await ac.post(
            "/chat/upload", headers=auth_headers,
            files={"file": ("encrypted.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )
    assert response.status_code == 422
    assert list(upload_temp_dir.iterdir()) == []


async def test_chat_upload_valid_image_ocr(auth_headers, upload_temp_dir, monkeypatch):
    from PIL import Image
    import io as _io

    buf = _io.BytesIO()
    Image.new("RGB", (10, 10), color="white").save(buf, format="PNG")
    png_bytes = buf.getvalue()

    async def fake_call_ocr(base_url, file_path):
        return "OCR extracted text"

    monkeypatch.setattr("modules.ocr_processor._call_ocr", fake_call_ocr)

    async with await _client() as ac:
        response = await ac.post(
            "/chat/upload", headers=auth_headers,
            files={"file": ("photo.png", png_bytes, "image/png")},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["extracted_text"] == "OCR extracted text"
    assert list(upload_temp_dir.iterdir()) == []


async def test_chat_upload_image_ocr_no_text_honest_warning(auth_headers, upload_temp_dir, monkeypatch):
    from PIL import Image
    import io as _io

    buf = _io.BytesIO()
    Image.new("RGB", (10, 10), color="white").save(buf, format="PNG")
    png_bytes = buf.getvalue()

    async def fake_call_ocr(base_url, file_path):
        return ""

    monkeypatch.setattr("modules.ocr_processor._call_ocr", fake_call_ocr)

    async with await _client() as ac:
        response = await ac.post(
            "/chat/upload", headers=auth_headers,
            files={"file": ("blank.png", png_bytes, "image/png")},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["extracted_text"] == ""
    assert data["warning"]


async def test_chat_upload_malformed_image_rejected(auth_headers, upload_temp_dir):
    async with await _client() as ac:
        response = await ac.post(
            "/chat/upload", headers=auth_headers,
            files={"file": ("fake.png", b"not-a-real-png-file", "image/png")},
        )
    assert response.status_code == 422
    assert list(upload_temp_dir.iterdir()) == []


async def test_chat_upload_rejects_unsupported_extension(auth_headers, upload_temp_dir):
    async with await _client() as ac:
        response = await ac.post(
            "/chat/upload", headers=auth_headers,
            files={"file": ("script.exe", b"MZ\x90\x00", "application/octet-stream")},
        )
    assert response.status_code == 400
    assert "Unsupported file type" in response.json()["detail"]


async def test_chat_upload_rejects_content_type_mismatch(auth_headers, upload_temp_dir):
    async with await _client() as ac:
        response = await ac.post(
            "/chat/upload", headers=auth_headers,
            files={"file": ("photo.png", b"not really an image", "application/zip")},
        )
    assert response.status_code == 400
    assert "content type" in response.json()["detail"].lower() or "Content type" in response.json()["detail"]


async def test_chat_upload_rejects_empty_file(auth_headers, upload_temp_dir):
    async with await _client() as ac:
        response = await ac.post(
            "/chat/upload", headers=auth_headers,
            files={"file": ("empty.txt", b"", "text/plain")},
        )
    assert response.status_code == 400
    assert "Empty" in response.json()["detail"]


async def test_chat_upload_rejects_oversized_file(auth_headers, upload_temp_dir):
    from app import main
    oversized = b"a" * (main._UPLOAD_MAX_SIZE + 1)
    async with await _client() as ac:
        response = await ac.post(
            "/chat/upload", headers=auth_headers,
            files={"file": ("big.txt", oversized, "text/plain")},
        )
    assert response.status_code == 400
    assert "too large" in response.json()["detail"].lower()


async def test_chat_upload_sanitizes_malicious_filename(auth_headers, upload_temp_dir):
    async with await _client() as ac:
        response = await ac.post(
            "/chat/upload", headers=auth_headers,
            files={"file": ("../../../etc/passwd.txt", b"content", "text/plain")},
        )
    assert response.status_code == 200
    assert response.json()["filename"] == "passwd.txt"
    assert list(upload_temp_dir.iterdir()) == []


async def test_chat_upload_never_returns_server_path(auth_headers, upload_temp_dir):
    async with await _client() as ac:
        response = await ac.post(
            "/chat/upload", headers=auth_headers,
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )
    body_text = response.text
    assert str(upload_temp_dir) not in body_text
    assert "/home/azim" not in body_text
