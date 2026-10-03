"""Offline regression tests for the live Recruitment fallback authority boundary."""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

import pytest

import modules.recruitment_flow as recruitment_flow
from modules.conversation_layer import generate_recruitment_reply as public_fallback
from modules.conversation_layer.recruitment import generate_recruitment_reply


async def _fallback(text: str, *, llm_reply: str | None = None, history: str | None = None, contact: str | None = None):
    with patch(
        "modules.conversation_layer.recruitment._safe_kb_reply",
        new=AsyncMock(return_value="legacy KB evidence"),
    ), patch(
        "modules.conversation_layer.recruitment._safe_rag_context",
        new=AsyncMock(return_value=("legacy RAG evidence", [{"authority_statuses": ["conflicting"]}])),
    ), patch(
        "modules.conversation_layer.recruitment._generate_shadow_llm_reply",
        new=AsyncMock(return_value=llm_reply),
    ) as llm:
        result = await generate_recruitment_reply(
            "8801700000000",
            text,
            history=history,
            contact_context=contact,
        )
    return result, llm


def test_hermes_success_is_before_the_existing_fallback_and_failure_enters_it():
    source = inspect.getsource(recruitment_flow.route_recruitment_message)
    hermes = source.index("_hermes_recruitment_reply(")
    fallback = source.rindex("from modules.conversation_layer import generate_recruitment_reply")
    assert hermes < fallback
    fallback_guard = source.index("if not reply:", fallback)
    assert fallback_guard > fallback


async def test_hermes_failure_uses_existing_authority_safe_fallback_without_send():
    result, llm = await _fallback("বেতন কত?", llm_reply="বেতন ৳৯,০০০")
    assert result["path"] == "authority_safe_unresolved_fact"
    assert "৯,০০০" not in result["reply"]
    llm.assert_not_awaited()
    assert result["mutated"] is False
    assert result["sent"] is False


async def test_approved_current_fact_remains_answerable_when_llm_is_unavailable():
    result, llm = await _fallback("অফিস কোথায়?", llm_reply=None)
    assert "আগ্রাপাড়া" in result["reply"]
    assert "Victoria Gate #1" in result["reply"]
    llm.assert_awaited_once()


@pytest.mark.parametrize(
    "question,forbidden",
    [
        ("বেতন কত?", ("৯,০০০", "১৫,০০০", "৳৩,৫০০")),
        ("কাজ কী?", ("কাজ", "দায়িত্ব", "দায়িত্ব")),
        ("থাকা খাওয়ার সুবিধা আছে?", ("থাকা", "খাওয়া", "খাওয়া")),
        ("ছুটি ও overtime আছে?", ("ছুটি", "overtime", "ওভারটাইম")),
        ("post-training Escort Survey বেতন কত?", ("বেতন", "৳", "salary")),
        ("এখন vacancy আছে?", ("নিয়োগ চলছে", "মোট", "পদে নিয়োগ")),
        ("seed FAQ-র তথ্য দিন", ("৳", "বেতন", "জয়েনিং")),
    ],
)
async def test_unverified_conflicting_and_superseded_facts_are_not_current_answers(question, forbidden):
    result, llm = await _fallback(question, llm_reply="legacy value ৳৩,৫০০")
    assert result["path"] == "authority_safe_unresolved_fact"
    assert all(value not in result["reply"] for value in forbidden)
    llm.assert_not_awaited()


async def test_legacy_hardcoded_value_cannot_override_approved_register():
    result, llm = await _fallback(
        "অফিস কোথায়?",
        llm_reply="অফিসে যোগাযোগ 01958-122311, বেতন ৳৯,০০০।",
    )
    assert result["path"] == "ollama_reply_rejected_authority_numeric_check"
    assert "01958-122311" not in result["reply"]
    assert "৯,০০০" not in result["reply"]
    assert "আগ্রাপাড়া" in result["reply"]
    llm.assert_awaited_once()


async def test_identity_and_conversation_context_are_preserved_without_becoming_policy():
    with patch(
        "modules.conversation_layer.recruitment._safe_kb_reply",
        new=AsyncMock(return_value=None),
    ), patch(
        "modules.conversation_layer.recruitment._safe_rag_context",
        new=AsyncMock(return_value=(None, [])),
    ), patch(
        "modules.conversation_layer.recruitment._generate_shadow_llm_reply",
        new=AsyncMock(return_value="ঠিক আছে, আপনার আবেদন নিয়ে এগোই।"),
    ) as llm:
        reply = await public_fallback(
            phone="8801700000000",
            text="অফিস কোথায়?",
            source="offline-test",
            contact_context="নাম: করিম; আবেদন ধাপ: শিক্ষা",
            history="আগের বার অফিসের ঠিকানা জানতে চেয়েছিলেন।",
        )
    assert reply == "ঠিক আছে, আপনার আবেদন নিয়ে এগোই।"
    prompt = llm.await_args.args[1]
    assert "নাম: করিম" in prompt
    assert "আগের বার অফিসের ঠিকানা" in prompt
    assert "APPROVED CURRENT FACT office.address.current" in prompt
    assert "Canonical public facts" not in prompt


async def test_missing_fact_continues_naturally_and_never_generates_outbound_message():
    result, llm = await _fallback("এই পদের বেতন কত?", llm_reply="salary ৳৯,০০০")
    assert "নিশ্চিত" in result["reply"]
    assert "অফিসে যাচাই" in result["reply"]
    assert result["mutated"] is False
    assert result["sent"] is False
    llm.assert_not_awaited()
