"""Synthetic K3.3 authority-aware retrieval tests.

No live database, Qdrant collection, embedding model, or provider is used.
"""
from __future__ import annotations

import pytest

from modules.recruitment_authority import candidate_evidence_safe, load_recruitment_authority

pytestmark = pytest.mark.unit


def _value(key: str) -> str:
    return next(f.value for f in load_recruitment_authority() if f.key == key)


def test_approved_values_pass_and_stale_or_unresolved_values_fail_closed():
    title = "approved (recruitment)"
    assert candidate_evidence_safe("kb:approved_age", _value("recruitment.eligibility"), title)
    assert candidate_evidence_safe("kb:approved_contact", _value("office.contact.current"), title)
    assert candidate_evidence_safe("kb:approved_salary", _value("salary.by_role"), title)
    assert candidate_evidence_safe("kb:approved_training", _value("training.duration"), title)
    assert candidate_evidence_safe("kb:approved_fee", _value("fee.amounts"), title)
    assert candidate_evidence_safe("kb:approved_hours", _value("office.hours_and_friday"), title)
    assert candidate_evidence_safe("kb:approved_roles", _value("recruitment.roles.available"), title)

    assert not candidate_evidence_safe("kb:stale_age", "General age 18-45; eighth grade", title)
    assert not candidate_evidence_safe("kb:stale_contact", "Contact 01958-122322", title)
    assert not candidate_evidence_safe("kb:stale_training", "Training lasts 45 days", title)
    assert not candidate_evidence_safe("kb:mixed_contact", "01958-122300 and 01958 122322", title)
    assert not candidate_evidence_safe("kb:mixed_hours", "09:00-17:00; Friday closed; 10:00-17:00; Friday may be closed", title)
    assert not candidate_evidence_safe("kb:vacancy", "Currently hiring; limited seats", title)
    assert not candidate_evidence_safe("kb:roles_plus_vacancy", f"{_value('recruitment.roles.available')} Currently hiring", title)


def test_mixed_salary_and_unverified_policy_claims_are_not_current_approved():
    salary = _value("salary.by_role")
    assert not candidate_evidence_safe(
        "kb:mixed_salary",
        f"{salary} Escort post-training BDT 12,000-24,000",
        "mixed (recruitment)",
    )
    assert not candidate_evidence_safe(
        "kb:mixed_benefits",
        f"{_value('office.contact.current')} food and accommodation are free",
        "mixed (recruitment)",
    )


def test_legacy_faq_and_missing_metadata_cannot_bypass_candidate_boundary():
    assert not candidate_evidence_safe(
        "kb:b11_salary",
        "LEGACY salary 10,000-18,000",
        "b11_salary (recruitment)",
    )
    assert not candidate_evidence_safe(
        "kb:b11_vacancy_status",
        "Vacancy still open; apply now",
        "b11_vacancy_status (recruitment)",
    )
    assert candidate_evidence_safe("file:finance/payroll.txt", "salary BDT 10,000", "payroll")


@pytest.mark.asyncio
async def test_bm25_build_and_rebuild_preserve_candidate_authority_boundary(monkeypatch):
    from modules import rag

    async def rows():
        return [
            ("kb:b11_salary", "b11_salary (recruitment)", "LEGACY salary 10,000-18,000", True, rag._DEFAULT_ALLOWED),
            ("kb:approved_contact", "approved (recruitment)", _value("office.contact.current"), True, rag._DEFAULT_ALLOWED),
        ]

    monkeypatch.setattr(rag, "_load_resource_files", lambda: [])
    monkeypatch.setattr(rag, "_load_knowledge_base_files", lambda: [])
    monkeypatch.setattr(rag, "_load_kb_rows", rows)
    monkeypatch.setattr(rag, "HYBRID_SEARCH_ENABLED", False)
    snapshots = []
    for indexer in ("build", "rebuild"):
        if indexer == "build":
            await rag.build_index(sync_qdrant=False)
        else:
            await rag.rebuild_index()
        try:
            docs = {doc["source"]: doc for doc in rag._IDX.docs}
            snapshots.append({
                source: {
                    "candidate_safe": docs[source]["candidate_safe"],
                    "fact_authority": docs[source]["fact_authority"],
                }
                for source in ("kb:b11_salary", "kb:approved_contact")
            })
            assert docs["kb:b11_salary"]["candidate_safe"] is False
            assert docs["kb:approved_contact"]["candidate_safe"] is True
            assert rag._candidate_authority_safe(docs["kb:b11_salary"]) is False
            assert rag._candidate_authority_safe(docs["kb:approved_contact"]) is True
        finally:
            rag._IDX.reset()
    assert snapshots[0] == snapshots[1]


@pytest.mark.asyncio
async def test_qdrant_payload_filter_rejects_old_points_before_hybrid_fusion(monkeypatch):
    from modules import rag

    class _Vector:
        def tolist(self):
            return [0.0, 1.0]

    class _Encoder:
        def encode(self, _texts, **_kwargs):
            return [_Vector()]

    class _Hit:
        def __init__(self, source, title, text, score):
            self.score = score
            self.payload = {
                "source": source,
                "title": title,
                "text": text,
                "chunk_idx": 0,
                "safe_for_customer": True,
            }

    class _Client:
        def search(self, **_kwargs):
            return [
                _Hit("kb:b11_salary", "b11_salary (recruitment)", "LEGACY salary 10,000-18,000", 0.99),
                _Hit("kb:approved_contact", "approved (recruitment)", _value("office.contact.current"), 0.98),
            ]

    monkeypatch.setattr(rag, "_get_encoder", lambda: _Encoder())
    monkeypatch.setattr(rag, "_get_qdrant_client", lambda: _Client())
    results = await rag._vector_search("unique k33 contact query", k=5, role="candidate")
    assert [result["source"] for result in results] == ["kb:approved_contact"]


@pytest.mark.asyncio
async def test_hybrid_fusion_uses_the_same_candidate_authority_filter(monkeypatch):
    from modules import rag

    stale = {
        "source": "kb:b11_salary",
        "title": "b11_salary (recruitment)",
        "chunk_idx": 0,
        "text": "LEGACY salary 10,000-18,000",
        "tokens": rag._tokenize("LEGACY salary 10,000-18,000"),
        "len": 3,
        "allowed_roles": rag._DEFAULT_ALLOWED,
        "safe_for_customer": True,
    }
    approved = {
        "source": "kb:approved_contact",
        "title": "approved (recruitment)",
        "chunk_idx": 0,
        "text": _value("office.contact.current"),
        "tokens": rag._tokenize(_value("office.contact.current")),
        "len": 1,
        "allowed_roles": rag._DEFAULT_ALLOWED,
        "safe_for_customer": True,
    }
    async with rag._IDX.lock:
        rag._IDX.reset()
        rag._IDX.docs.extend([stale, approved])
        for doc in rag._IDX.docs:
            for token in set(doc["tokens"]):
                rag._IDX.df[token] += 1
        rag._IDX.avgdl = 1.0
    async def vector_hits(_query, k, role="candidate"):
        return [
            {"source": stale["source"], "title": stale["title"], "chunk_idx": 0, "text": stale["text"], "fact_authority": []},
            {"source": approved["source"], "title": approved["title"], "chunk_idx": 0, "text": approved["text"], "fact_authority": []},
        ]
    monkeypatch.setattr(rag, "_vector_search", vector_hits)
    try:
        results = await rag._hybrid_search("contact", rag._tokenize("contact"), 2, 5)
        assert results
        assert all(result["source"] != stale["source"] for result in results)
        assert any(result["source"] == approved["source"] for result in results)
    finally:
        rag._IDX.reset()
