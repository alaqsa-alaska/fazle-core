"""
Unit tests — modules.rag's knowledge_base/*.md indexing (2026-07-11).

knowledge_base/ contains internal governance/developer docs, several
explicitly marked "Visibility: Developer/Admin only" -- this must never be
retrievable by a customer-facing (role="candidate") RAG call, only by the
admin AI console (role="admin"/"developer").
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


class TestLoadKnowledgeBaseFiles:
    def test_loads_real_files_admin_only(self):
        from modules.rag import _load_knowledge_base_files, _KB_ADMIN_ONLY_ROLES
        sources = _load_knowledge_base_files()
        assert len(sources) > 0
        for source_id, title, text, safe, allowed_roles in sources:
            assert source_id.startswith("kb_file:")
            assert text.strip()
            assert allowed_roles == _KB_ADMIN_ONLY_ROLES
            assert "candidate" not in allowed_roles
            assert "employee" not in allowed_roles

    def test_archived_directory_excluded(self):
        from modules.rag import _load_knowledge_base_files
        sources = _load_knowledge_base_files()
        assert not any("07_archived" in source_id for source_id, *_ in sources)

    def test_non_markdown_files_excluded(self):
        from modules.rag import _load_knowledge_base_files
        sources = _load_knowledge_base_files()
        assert all(source_id.endswith(".md") for source_id, *_ in sources)


class TestRoleBasedVisibility:
    """End-to-end: build the real index, confirm role gating actually works
    through search(), not just at the loader level."""

    async def test_candidate_role_never_sees_kb_file_content(self):
        from modules import rag
        await rag.build_index()
        hits = await rag.search("office payroll eligibility salary rule", k=10, role="candidate")
        assert not any(h["source"].startswith("kb_file:") for h in hits)

    async def test_admin_role_can_see_kb_file_content(self):
        from modules import rag
        await rag.build_index()
        hits = await rag.search("office payroll eligibility salary rule", k=10, role="admin")
        assert any(h["source"].startswith("kb_file:") for h in hits)

    async def test_answer_defaults_to_candidate_role(self):
        """answer()'s default must stay role='candidate' -- every existing
        caller (recruitment replies) that doesn't pass role must keep
        getting the same restrictive behavior as before this change."""
        from modules import rag
        await rag.build_index()
        result = await rag.answer("office payroll eligibility salary rule", k=10, min_score=0.0)
        if result:
            for citation in result["citations"]:
                assert not citation["source"].startswith("kb_file:")

    async def test_answer_with_admin_role_can_reach_kb_content(self):
        from modules import rag
        await rag.build_index()
        result = await rag.answer("office payroll eligibility salary rule", k=10, min_score=0.0, role="admin")
        assert result is not None
        assert any(c["source"].startswith("kb_file:") for c in result["citations"])
