"""
Fazle Core — Voice Message Processor (Phase 4F)

Calls local media-processor /transcribe to convert audio → text.
Then classifies intent and builds a reply.

Handles: .ogg (WhatsApp), .mp3, .wav, .m4a
"""

import logging
import os
import httpx
from typing import TypedDict, Optional

from app.config import get_settings

log = logging.getLogger("fazle.voice")

# Minimum confidence (word count) to trust transcript
MIN_WORD_COUNT = 2
# Retry: on timeout, attempt once more with a shorter segment window
_TIMEOUT_RETRY_S = 45.0


class VoiceResult(TypedDict):
    transcript: str
    word_count: int
    confident: bool         # False → ask user to send text instead
    language_hint: str      # 'bn' | 'en' | 'mixed' | 'unknown'
    intent: str
    reply: str


async def process_voice(file_path: str, skip_intent: bool = False) -> VoiceResult:
    """
    Full pipeline: transcribe → language detect → intent → reply.
    On timeout: retries once before raising.

    skip_intent=True stops after transcription/confidence/language-detect and
    never calls the intent classifier (which can invoke an LLM) — used by
    callers (e.g. the web chat voice endpoint) that must not send the
    transcript to any LLM themselves; the caller is fully responsible for
    what happens to the transcript.
    """
    settings = get_settings()

    transcript = await _call_transcribe(settings.media_processor_url, file_path)
    words = transcript.split() if transcript else []
    word_count = len(words)
    confident = word_count >= MIN_WORD_COUNT
    lang = _detect_language(transcript)

    # Log confidence detail for monitoring
    file_size = _safe_file_size(file_path)
    log.info(
        "[voice] conf=%s words=%d lang=%s file=%s size_kb=%d",
        confident, word_count, lang, os.path.basename(file_path), file_size // 1024,
    )

    if not confident:
        return VoiceResult(
            transcript=transcript,
            word_count=word_count,
            confident=False,
            language_hint=lang,
            intent="unknown",
            reply="ভয়েস বার্তাটি ভালোভাবে বোঝা যায়নি। অনুগ্রহ করে টেক্সট আকারে পাঠান।",
        )

    if skip_intent:
        return VoiceResult(
            transcript=transcript,
            word_count=word_count,
            confident=True,
            language_hint=lang,
            intent="not_classified",
            reply="",
        )

    # Lazy import to avoid circular
    from modules.intent import classify
    from app import ollama as ai

    intent = classify(transcript)
    if intent == "unknown":
        intent = await ai.classify_intent_llm(transcript)

    log.info(f"[voice] transcript={transcript[:80]!r} lang={lang} intent={intent}")

    return VoiceResult(
        transcript=transcript,
        word_count=word_count,
        confident=True,
        language_hint=lang,
        intent=intent,
        reply="",   # caller is responsible for generating the final reply
    )


def _safe_file_size(file_path: str) -> int:
    try:
        return os.path.getsize(file_path)
    except Exception:
        return 0


async def _call_transcribe(base_url: str, file_path: str) -> str:
    """
    Call media-processor /transcribe. On TimeoutException, retries once
    with a shorter timeout before re-raising to the caller.
    Other errors are raised immediately so bridge_poller can save a draft.
    """
    basename = os.path.basename(file_path)
    try:
        return await _transcribe_attempt(base_url, file_path, timeout=60.0)
    except httpx.TimeoutException:
        log.warning(f"[voice] transcribe timeout (attempt 1) — retrying file={basename}")
        try:
            return await _transcribe_attempt(base_url, file_path, timeout=_TIMEOUT_RETRY_S)
        except httpx.TimeoutException:
            log.error(f"[voice] transcribe timeout (attempt 2, final) file={basename}")
            raise
        except Exception as e:
            log.error(f"[voice] transcribe retry error={type(e).__name__}: {e} file={basename}")
            raise


async def _transcribe_attempt(base_url: str, file_path: str, timeout: float) -> str:
    basename = os.path.basename(file_path)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            f"{base_url.rstrip('/')}/transcribe",
            json={"file_path": file_path},
        )
        if r.status_code == 200:
            text = r.json().get("text", "").strip()
            words = len(text.split()) if text else 0
            log.info(f"[voice] transcribe ok file={basename} words={words}")
            # Log low-confidence transcript for monitoring
            if 0 < words < MIN_WORD_COUNT:
                log.warning(
                    f"[voice] LOW_CONFIDENCE file={basename} words={words} "
                    f"transcript={text!r}"
                )
            return text
        log.warning(f"[voice] transcribe returned {r.status_code} file={basename}")
        # Non-200 is not a timeout — return empty (caller decides draft vs. skip)
        return ""


def _detect_language(text: str) -> str:
    """Heuristic: count Bengali unicode characters."""
    if not text:
        return "unknown"
    bn_chars = sum(1 for c in text if "ঀ" <= c <= "৿")
    latin_chars = sum(1 for c in text if c.isalpha() and ord(c) < 128)
    total = bn_chars + latin_chars
    if total == 0:
        return "unknown"
    bn_ratio = bn_chars / total
    if bn_ratio > 0.6:
        return "bn"
    if bn_ratio < 0.2:
        return "en"
    return "mixed"
