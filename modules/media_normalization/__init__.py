from __future__ import annotations

import mimetypes
import os
import tempfile
from typing import Any, Optional, TypedDict

import httpx

from app.config import get_settings
from modules import observability as obs
from modules.ocr_processor import process_document, process_image
from modules.voice_processor import process_voice


class MediaNormalizationResult(TypedDict):
    text: str
    media_type: str
    meta: dict[str, Any]


def _placeholder_text(msg_type: str, *, caption: str = "", filename: str = "") -> str:
    marker = f"[{msg_type} message]"
    for extra in (caption.strip(), filename.strip()):
        if extra:
            return f"{marker} {extra}"
    return marker


def _extension_for(mime_type: str, filename: str) -> str:
    if filename and "." in filename:
        return os.path.splitext(filename)[1]
    guess = mimetypes.guess_extension(mime_type or "")
    return guess or ""


def _is_ocr_candidate(msg_type: str, mime_type: str, filename: str) -> bool:
    if msg_type == "image":
        return True
    if msg_type != "document":
        return False
    mime = (mime_type or "").strip().lower()
    if mime.startswith("image/"):
        return True
    ext = (os.path.splitext(filename or "")[1] or "").strip().lower()
    return ext in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


async def normalize_meta_media_message(msg: dict[str, Any]) -> MediaNormalizationResult:
    msg_type = str(msg.get("type") or "").strip().lower()
    payload = msg.get(msg_type) or {}
    caption = str(payload.get("caption") or "").strip()
    filename = str(payload.get("filename") or "").strip()
    mime_type = str(payload.get("mime_type") or "").strip()
    media_id = str(payload.get("id") or "").strip()
    placeholder = _placeholder_text(msg_type or "media", caption=caption, filename=filename)

    if msg_type not in {"image", "audio", "document", "video"} or not media_id:
        obs.inc("media_normalization_total", labels={"type": msg_type or "unknown", "result": "placeholder"})
        return MediaNormalizationResult(text=placeholder, media_type=msg_type or "unknown", meta={"normalized": False})

    file_path = await _download_meta_media(media_id, mime_type=mime_type, filename=filename)
    if not file_path:
        obs.inc("media_normalization_total", labels={"type": msg_type, "result": "download_failed"})
        return MediaNormalizationResult(text=placeholder, media_type=msg_type, meta={"normalized": False, "download": "failed"})

    try:
        if msg_type == "audio":
            voice = await process_voice(file_path)
            transcript = str(voice.get("transcript") or "").strip()
            if transcript:
                obs.inc("media_normalization_total", labels={"type": msg_type, "result": "transcribed"})
                text = f"[audio message] {transcript}".strip()[:4000]
                return MediaNormalizationResult(
                    text=text,
                    media_type=msg_type,
                    meta={
                        "normalized": True,
                        "language_hint": voice.get("language_hint") or "unknown",
                        "confident": bool(voice.get("confident")),
                    },
                )

        if _is_ocr_candidate(msg_type, mime_type, filename):
            ocr = await process_image(file_path)
            raw_text = str(ocr.get("raw_text") or "").strip()
            if raw_text:
                obs.inc("media_normalization_total", labels={"type": msg_type, "result": "ocr"})
                text = f"[{msg_type} message] {raw_text}".strip()[:4000]
                return MediaNormalizationResult(
                    text=text,
                    media_type=msg_type,
                    meta={
                        "normalized": True,
                        "slip_type": ocr.get("slip_type") or "unknown",
                        "duplicate": bool(ocr.get("is_duplicate")),
                    },
                )

        if msg_type == "document":
            document = await process_document(file_path, filename=filename)
            extracted_text = str(document.get("extracted_text") or "").strip()
            if extracted_text:
                obs.inc("media_normalization_total", labels={"type": msg_type, "result": "extracted"})
                text = f"[document message] {extracted_text}".strip()[:4000]
                return MediaNormalizationResult(
                    text=text,
                    media_type=msg_type,
                    meta={
                        "normalized": True,
                        "doc_type": document.get("doc_type") or "unknown",
                        "confidence_score": int(document.get("confidence_score") or 0),
                    },
                )

        obs.inc("media_normalization_total", labels={"type": msg_type, "result": "placeholder"})
        return MediaNormalizationResult(text=placeholder, media_type=msg_type, meta={"normalized": False})
    finally:
        try:
            if os.path.exists(file_path):
                os.unlink(file_path)
        except Exception:
            pass


async def _download_meta_media(media_id: str, *, mime_type: str = "", filename: str = "") -> Optional[str]:
    settings = get_settings()
    headers = {"Authorization": f"Bearer {settings.meta_api_token}"}
    ext = _extension_for(mime_type, filename)
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            meta_resp = await client.get(f"{settings.meta_api_url.rstrip('/')}/{media_id}", headers=headers)
            meta_resp.raise_for_status()
            media_url = str(meta_resp.json().get("url") or "").strip()
            if not media_url:
                return None
            download_resp = await client.get(media_url, headers=headers)
            download_resp.raise_for_status()
            tmp = tempfile.NamedTemporaryFile(prefix="fazle_meta_media_", suffix=ext, delete=False)
            try:
                tmp.write(download_resp.content)
                tmp.flush()
                return tmp.name
            finally:
                tmp.close()
    except Exception:
        return None
