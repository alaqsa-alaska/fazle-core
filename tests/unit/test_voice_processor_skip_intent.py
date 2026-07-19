from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_process_voice_default_behavior_still_classifies_intent(monkeypatch):
    """Regression guard: default (WhatsApp) behavior must be unchanged."""
    from modules import voice_processor

    async def fake_call_transcribe(base_url, file_path):
        return "hello world testing"

    called = {"classify": False}

    def fake_classify(text):
        called["classify"] = True
        return "greeting"

    monkeypatch.setattr(voice_processor, "_call_transcribe", fake_call_transcribe)
    monkeypatch.setattr("modules.intent.classify", fake_classify)

    result = await voice_processor.process_voice("/tmp/fake.ogg")

    assert called["classify"] is True
    assert result["intent"] == "greeting"
    assert result["confident"] is True


async def test_process_voice_skip_intent_never_classifies_or_calls_llm(monkeypatch):
    from modules import voice_processor

    async def fake_call_transcribe(base_url, file_path):
        return "hello world testing"

    def fake_classify(text):
        raise AssertionError("intent classifier must not run when skip_intent=True")

    monkeypatch.setattr(voice_processor, "_call_transcribe", fake_call_transcribe)
    monkeypatch.setattr("modules.intent.classify", fake_classify)

    result = await voice_processor.process_voice("/tmp/fake.ogg", skip_intent=True)

    assert result["intent"] == "not_classified"
    assert result["reply"] == ""
    assert result["confident"] is True
    assert result["transcript"] == "hello world testing"


async def test_process_voice_skip_intent_low_confidence_unchanged(monkeypatch):
    """Low-confidence short transcripts must still short-circuit before intent,
    with or without skip_intent — behavior should be identical either way."""
    from modules import voice_processor

    async def fake_call_transcribe(base_url, file_path):
        return "hi"

    monkeypatch.setattr(voice_processor, "_call_transcribe", fake_call_transcribe)

    result = await voice_processor.process_voice("/tmp/fake.ogg", skip_intent=True)

    assert result["confident"] is False
    assert result["intent"] == "unknown"
    assert result["reply"]
