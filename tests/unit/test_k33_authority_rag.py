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


@pytest.mark.asyncio
async def test_qdrant_refreshes_missing_stale_metadata_and_skips_current_points(monkeypatch):
    from types import SimpleNamespace
    from modules import rag
    from modules.recruitment_authority import facts_for_evidence

    approved_text = _value("office.contact.current")
    approved_facts = [
        fact.as_marker()
        for fact in facts_for_evidence("kb:approved_contact", approved_text, include_all=True)
    ]
    chunks = [
        {
            "source": "kb:missing_metadata",
            "title": "missing (recruitment)",
            "chunk_idx": 0,
            "text": "Legacy policy text",
            "candidate_safe": False,
            "fact_authority": [],
        },
        {
            "source": "kb:stale_metadata",
            "title": "approved (recruitment)",
            "chunk_idx": 0,
            "text": approved_text,
            "candidate_safe": True,
            "fact_authority": approved_facts,
        },
        {
            "source": "file:finance/payroll.txt",
            "title": "payroll",
            "chunk_idx": 0,
            "text": "Salary BDT 10,000",
            "candidate_safe": True,
            "fact_authority": [],
        },
    ]

    class _Point:
        def __init__(self, point_id, payload):
            self.id = point_id
            self.payload = payload

    class _Client:
        def __init__(self):
            self.points = {}
            self.payload_updates = []
            for chunk, payload in zip(chunks, ({}, {"candidate_safe": True, "fact_authority": ["stale"]}, {"candidate_safe": True, "fact_authority": []})):
                point_id = rag._chunk_point_id(chunk)
                stored = {"content_hash": rag._chunk_content_hash(chunk["text"]), **payload}
                self.points[point_id] = stored

        def retrieve(self, *, ids, **_kwargs):
            return [_Point(point_id, dict(self.points[point_id])) for point_id in ids if point_id in self.points]

        def set_payload(self, *, payload, points, **_kwargs):
            self.payload_updates.append((dict(payload), list(points)))
            for point_id in points:
                self.points[point_id].update(payload)

        def scroll(self, **_kwargs):
            return ([_Point(point_id, {}) for point_id in self.points], None)

        def delete(self, **_kwargs):
            return None

    client = _Client()
    monkeypatch.setattr(rag, "_get_qdrant_client", lambda: client)
    monkeypatch.setattr(rag, "_qdrant_ensure_collection", lambda: None)
    monkeypatch.setattr(rag, "_seed_embedding_cache", lambda: None)
    monkeypatch.setattr(rag, "_get_encoder", lambda: (_ for _ in ()).throw(AssertionError("metadata-only path encoded")))

    result = await rag._build_qdrant_index(chunks)

    assert result == {
        "status": "ok",
        "total_chunks": 3,
        "encoded": 0,
        "metadata_updated": 2,
        "skipped": 1,
        "stale_removed": 0,
    }
    assert len(client.payload_updates) == 2
    by_source = {
        rag._chunk_point_id(chunk): client.points[rag._chunk_point_id(chunk)]
        for chunk in chunks
    }
    assert by_source[rag._chunk_point_id(chunks[0])]["candidate_safe"] is False
    assert by_source[rag._chunk_point_id(chunks[0])]["fact_authority"] == []
    assert by_source[rag._chunk_point_id(chunks[1])]["fact_authority"] == approved_facts
    assert "recruitment" not in str(by_source[rag._chunk_point_id(chunks[2])]["fact_authority"]).lower()
    assert candidate_evidence_safe(chunks[0]["source"], chunks[0]["text"], chunks[0]["title"], by_source[rag._chunk_point_id(chunks[0])]["fact_authority"]) is False
    assert candidate_evidence_safe(chunks[1]["source"], chunks[1]["text"], chunks[1]["title"], by_source[rag._chunk_point_id(chunks[1])]["fact_authority"]) is True


@pytest.mark.asyncio
async def test_qdrant_reconciliation_retries_after_partial_metadata_failure(monkeypatch):
    from types import SimpleNamespace
    from modules import rag

    chunk = {
        "source": "kb:b11_salary",
        "title": "b11_salary (recruitment)",
        "chunk_idx": 0,
        "text": "LEGACY salary 10,000-18,000",
        "candidate_safe": False,
        "fact_authority": [],
    }
    point_id = rag._chunk_point_id(chunk)
    point = SimpleNamespace(id=point_id, payload={"content_hash": rag._chunk_content_hash(chunk["text"])})

    class _Client:
        def __init__(self):
            self.failed = False
            self.payload = dict(point.payload)

        def retrieve(self, **_kwargs):
            return [SimpleNamespace(id=point_id, payload=dict(self.payload))]

        def set_payload(self, *, payload, points, **_kwargs):
            if not self.failed:
                self.failed = True
                raise RuntimeError("synthetic metadata write failure")
            self.payload.update(payload)

        def scroll(self, **_kwargs):
            return ([SimpleNamespace(id=point_id, payload={})], None)

        def delete(self, **_kwargs):
            return None

    client = _Client()
    monkeypatch.setattr(rag, "_get_qdrant_client", lambda: client)
    monkeypatch.setattr(rag, "_qdrant_ensure_collection", lambda: None)

    first = await rag._build_qdrant_index([chunk])
    assert first["status"] == "error"
    assert first["metadata_updated"] == 0
    second = await rag._build_qdrant_index([chunk])
    assert second["status"] == "ok"
    assert second["metadata_updated"] == 1
    assert client.payload["candidate_safe"] is False
    assert client.payload["fact_authority"] == []


@pytest.mark.asyncio
async def test_qdrant_changed_content_uses_existing_upsert_path(monkeypatch):
    from types import SimpleNamespace
    from modules import rag

    chunk = {
        "source": "file:finance/payroll.txt",
        "title": "payroll",
        "chunk_idx": 0,
        "text": "Salary BDT 20,000",
        "candidate_safe": True,
        "fact_authority": [],
    }
    point_id = rag._chunk_point_id(chunk)

    class _Vectors(list):
        @property
        def shape(self):
            return (len(self), 2)

    class _Encoder:
        def encode(self, _texts, **_kwargs):
            return _Vectors([SimpleNamespace(tolist=lambda: [0.0, 1.0])])

    class _Client:
        def __init__(self):
            self.upserts = []

        def retrieve(self, **_kwargs):
            return [SimpleNamespace(id=point_id, payload={"content_hash": "old-hash", "candidate_safe": True, "fact_authority": []})]

        def upsert(self, **kwargs):
            self.upserts.append(kwargs)

        def set_payload(self, **_kwargs):
            raise AssertionError("changed content must use upsert")

        def scroll(self, **_kwargs):
            return ([SimpleNamespace(id=point_id, payload={})], None)

        def delete(self, **_kwargs):
            return None

    client = _Client()
    monkeypatch.setattr(rag, "_get_qdrant_client", lambda: client)
    monkeypatch.setattr(rag, "_qdrant_ensure_collection", lambda: None)
    monkeypatch.setattr(rag, "_get_encoder", lambda: _Encoder())
    async def _seed():
        return None
    monkeypatch.setattr(rag, "_seed_embedding_cache", _seed)

    result = await rag._build_qdrant_index([chunk])
    assert result["status"] == "ok"
    assert result["encoded"] == 1
    assert result["metadata_updated"] == 0
    assert len(client.upserts) == 1
    assert client.upserts[0]["points"][0].payload["content_hash"] == rag._chunk_content_hash(chunk["text"])


@pytest.mark.asyncio
async def test_build_and_rebuild_send_identical_authority_metadata_to_qdrant(monkeypatch):
    from modules import rag

    async def rows():
        return [
            ("kb:b11_salary", "b11_salary (recruitment)", "LEGACY salary 10,000-18,000", True, rag._DEFAULT_ALLOWED),
            ("kb:approved_contact", "approved (recruitment)", _value("office.contact.current"), True, rag._DEFAULT_ALLOWED),
        ]

    snapshots = []

    async def capture(chunks):
        snapshots.append([
            (chunk["source"], chunk["candidate_safe"], chunk["fact_authority"])
            for chunk in chunks
        ])
        return {"status": "ok", "total_chunks": len(chunks)}

    monkeypatch.setattr(rag, "_load_resource_files", lambda: [])
    monkeypatch.setattr(rag, "_load_knowledge_base_files", lambda: [])
    monkeypatch.setattr(rag, "_load_kb_rows", rows)
    monkeypatch.setattr(rag, "HYBRID_SEARCH_ENABLED", True)
    monkeypatch.setattr(rag, "_build_qdrant_index", capture)
    try:
        first = await rag.build_index(sync_qdrant=True)
        rag._IDX.reset()
        second = await rag.rebuild_index()
    finally:
        rag._IDX.reset()

    assert first["qdrant"]["status"] == "ok"
    assert second["qdrant"]["status"] == "ok"
    assert snapshots[0] == snapshots[1]
