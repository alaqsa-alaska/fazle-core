from __future__ import annotations

from shared.reply_policy import (
    build_provider_context_package,
    build_whatsapp_recruitment_policy,
    build_whatsapp_reply_policy,
)


def test_provider_context_package_separates_authority_from_continuity() -> None:
    package = build_provider_context_package(
        source="bridge1",
        user_message="বেতন কত?",
        role="candidate",
        intent="recruitment",
        knowledge="salary evidence",
        history="Them: Escort পদটি জানতে চাই\nYou: বিস্তারিত বলছি",
        identity_context="role=candidate; relationship=applicant",
        knowledge_source="legacy-recruitment-source",
        knowledge_authority="source_declared",
        knowledge_status="unreconciled",
        conflicts=("legacy_policy_variants",),
    )

    rendered = "\n".join(
        (package.render_identity(), package.render_knowledge(), package.render_conversation())
    )
    assert "role=candidate" in rendered
    assert "role_is_context_only=true" in rendered
    assert "authority=source_declared" in rendered
    assert "status=unreconciled" in rendered
    assert "conflicts=legacy_policy_variants" in rendered
    assert "prior_answer_evidence=present" in rendered
    assert "conversation_is_context_not_policy=true" in rendered


def test_unlabelled_knowledge_remains_unverified() -> None:
    package = build_provider_context_package(
        source="bridge1",
        user_message="কাজ কী?",
        knowledge="unlabelled retrieved text",
    )

    assert "authority=unclassified" in package.render_knowledge()
    assert "status=unverified" in package.render_knowledge()
    assert "business_fact_status_not_explicit" in package.render_conversation()


def test_general_builder_exposes_same_context_shape_to_providers() -> None:
    prompt = build_whatsapp_reply_policy(
        source="bridge1",
        user_message="হ্যাঁ",
        role="employee",
        intent="salary_query",
        db_context="নিজের payroll context",
        history="Them: বেতন নিয়ে জানতে চাই\nYou: কোন মাসের কথা বলছেন?",
    )

    assert "Identity / Relationship Context" in prompt
    assert "role=employee" in prompt
    assert "conversation_is_context_not_policy=true" in prompt
    assert "নিজের payroll context" in prompt


def test_recruitment_builder_keeps_contact_context_outside_policy_authority() -> None:
    prompt = build_whatsapp_recruitment_policy(
        source="bridge1",
        user_message="বেতন কত?",
        kb_context=(
            "[FAZLE_EVIDENCE authority=source_declared status=unreconciled "
            "conflicts=legacy_policy_variants source=legacy]\n"
            "role evidence"
        ),
        contact_context="known contact role=candidate",
        history="Them: Escort-এর কাজ কী?\nYou: তথ্য দিচ্ছি",
    )

    assert "role=candidate" in prompt
    assert "known contact role=candidate" in prompt
    assert "authority=source_declared" in prompt
    assert "status=unreconciled" in prompt
    assert "prior_answer_evidence=present" in prompt


def test_recruitment_fact_register_never_promotes_unresolved_values() -> None:
    from modules.recruitment_authority import facts_for_evidence

    facts = {fact.key: fact for fact in facts_for_evidence(
        "resources/ops/recruitment_source_of_truth.txt",
        "বেতন কত? ফি কত?",
    )}

    assert facts["salary.by_role"].status == "conflicting"
    assert facts["salary.by_role"].value is None
    assert facts["salary.by_role"].answerable is False
    assert facts["recruitment.fee.existence"].answerable is True


def test_fact_authority_markers_preserve_provenance_and_status() -> None:
    from modules.recruitment_authority import render_authority_markers

    markers = render_authority_markers(
        "resources/ops/recruitment_source_of_truth.txt",
        "বেতন কত?",
    )

    assert "salary.by_role" in markers
    assert '"status": "conflicting"' in markers
    assert "resources/ops/recruitment_source_of_truth.txt" in markers


def test_recruitment_prompt_consumes_fact_authority_without_approving_salary() -> None:
    from modules.recruitment_authority import render_authority_markers

    context = render_authority_markers(
        "resources/ops/recruitment_source_of_truth.txt",
        "বেতন কত? ফি কত?",


    )
    prompt = build_whatsapp_recruitment_policy(
        source="bridge1",
        user_message="বেতন কত?",
        kb_context=context,
    )

    assert "status=conflicting" in prompt
    assert "authority=evidence_only" in prompt
    assert "No approved candidate-facing value" in prompt
    assert "authority=owner_approved_fact" in prompt
    assert "Conversation/History শুধু অর্থ" in prompt


def test_rag_fusion_preserves_authority_metadata_independent_of_score() -> None:
    from modules.rag import _rrf_fuse

    results = _rrf_fuse(
        [(99.0, {"source": "kb:salary", "chunk_idx": 0, "title": "salary",
                 "text": "legacy salary", "fact_authority": [{"status": "conflicting"}]})],
        [],
        top_k=1,
    )

    assert results[0]["text"] == "legacy salary"
    assert results[0]["fact_authority"] == [{"status": "conflicting"}]


async def test_exact_recruitment_kb_does_not_bypass_fact_authority(monkeypatch) -> None:
    from modules import knowledge_base

    async def fake_fetch_all(_query):
        return [{
            "key": "b11_salary",
            "trigger_keywords": ["বেতন কত"],
            "reply_text": "বেতন ১০,০০০–১৫,০০০ টাকা",
        }]

    monkeypatch.setattr(knowledge_base, "fetch_all", fake_fetch_all)

    assert await knowledge_base.get_recruitment_reply("বেতন কত") is None
