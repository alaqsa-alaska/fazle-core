"""Unit tests -- Second rebrand (Al-Rifa'i Integrated Services) + recruitment
RAG indexing fix (2026-07-22).

Covers: the no-reply gate's new brand tokens (augment-don't-replace, all
three brand generations must keep matching); modules.rag._load_resource_files()
now recursing one level into resources/ops/ (previously every subdirectory
was unconditionally skipped, so resources/ops/recruitment_source_of_truth.txt
-- the file shared/reply_policy.py's own prompt calls the "Approved
Recruitment Source of Truth" -- was never actually indexed); and the
company-name centralization plumbing (app/config.py Settings.company_name
as the single source of truth, read dynamically rather than baked into a
module-level constant at import time).
"""
from __future__ import annotations

import os
import tempfile

import pytest

pytestmark = pytest.mark.unit


class TestNoReplyGateBrandTokens:

    def test_all_three_brand_generations_still_match(self):
        from modules.message_router import _SILENT_SKIP_PATTERN
        for name in (
            "Al-Aqsa Office Chattogram",
            "Al-Manaratayn Escort Desk",
            "Al-Rifa'i Client Team",
            "AL RIFAI Karim",
        ):
            assert _SILENT_SKIP_PATTERN.search(name), f"{name!r} should still match"

    def test_new_tokens_present_in_the_static_list(self):
        from modules.message_router import _SILENT_SKIP_NAME_TOKENS
        assert "al-rifa'i" in _SILENT_SKIP_NAME_TOKENS
        assert "al rifai" in _SILENT_SKIP_NAME_TOKENS
        # augment, not replace -- older brand tokens must still be present
        assert "al-aqsa" in _SILENT_SKIP_NAME_TOKENS
        assert "al-manaratayn" in _SILENT_SKIP_NAME_TOKENS

    def test_unrelated_name_still_not_matched(self):
        from modules.message_router import _SILENT_SKIP_PATTERN
        assert not _SILENT_SKIP_PATTERN.search("Rahim Uddin")


class TestRecruitmentRagIndexing:

    def test_resources_ops_subdirectory_is_now_indexed(self):
        """Regression guard for the real bug found this pass: every
        subdirectory under resources/ used to be unconditionally skipped."""
        from modules.rag import _load_resource_files
        sources = _load_resource_files()
        ops_ids = {s[0] for s in sources if s[0].startswith("file:ops/")}
        assert "file:ops/recruitment_source_of_truth.txt" in ops_ids
        assert "file:ops/salary_structure.txt" in ops_ids
        assert "file:ops/food_expense_policy.txt" in ops_ids

    def test_excluded_subdirectory_still_skipped(self):
        """The exclude-list itself must still work for a real subdirectory
        that IS on it -- this fix must not accidentally index everything."""
        from modules.rag import _load_resource_files, RESOURCES_DIR, _EXCLUDED_DIRS
        assert _EXCLUDED_DIRS, "test premise: exclude list must be non-empty"
        excluded_name = next(iter(_EXCLUDED_DIRS))
        target_dir = os.path.join(RESOURCES_DIR, excluded_name)
        marker_file = os.path.join(target_dir, "_test_marker_should_not_index.txt")
        made_dir = not os.path.isdir(target_dir)
        if made_dir:
            os.makedirs(target_dir, exist_ok=True)
        with open(marker_file, "w", encoding="utf-8") as f:
            f.write("this must never be indexed")
        try:
            sources = _load_resource_files()
            ids = {s[0] for s in sources}
            assert f"file:{excluded_name}/_test_marker_should_not_index.txt" not in ids
        finally:
            os.remove(marker_file)
            if made_dir:
                os.rmdir(target_dir)

    def test_recursion_is_only_one_level_deep(self):
        """A nested sub-subdirectory (two levels down) must not be indexed
        -- this fix is deliberately shallow (one level), not full recursion,
        per the 'minimal, easily-audited change' design note."""
        from modules.rag import _load_resource_files, RESOURCES_DIR
        nested_dir = os.path.join(RESOURCES_DIR, "ops", "_test_nested_dir")
        marker_file = os.path.join(nested_dir, "should_not_index.txt")
        os.makedirs(nested_dir, exist_ok=True)
        with open(marker_file, "w", encoding="utf-8") as f:
            f.write("nested content that must not be indexed")
        try:
            sources = _load_resource_files()
            ids = {s[0] for s in sources}
            assert not any("_test_nested_dir" in i for i in ids)
        finally:
            os.remove(marker_file)
            os.rmdir(nested_dir)

    def test_historical_ops_files_are_not_candidate_visible(self):
        from modules.rag import _load_resource_files
        sources = _load_resource_files()
        recruitment_src = next(
            s for s in sources if s[0] == "file:ops/recruitment_source_of_truth.txt"
        )
        source_id, title, text, safe_for_customer, allowed_roles = recruitment_src
        assert safe_for_customer is True
        assert "candidate" not in allowed_roles
        assert "admin" in allowed_roles


class TestCompanyNameCentralization:

    def test_settings_company_name_is_the_new_brand(self):
        from app.config import get_settings
        assert get_settings().company_name == "Al-Rifa'i Integrated Services"

    def test_social_auto_reply_rules_use_the_live_setting(self):
        from modules.social_auto_reply import reply_rules
        from app.config import get_settings
        assert reply_rules.COMPANY_NAME == get_settings().company_name
        assert reply_rules.COMPANY_NAME in reply_rules.WELCOME_REPLY
        assert reply_rules.COMPANY_NAME in reply_rules.LOCATION_REPLY
        assert "{COMPANY_NAME}" not in reply_rules.WELCOME_REPLY, (
            "WELCOME_REPLY must be an f-string -- a literal '{COMPANY_NAME}' "
            "in the output means the f-prefix was lost"
        )

    def test_knowledge_base_fallback_uses_new_brand(self):
        import modules.knowledge_base as kb
        joined = "\n".join(reply for _kw, reply in kb._FALLBACK)
        assert "Al-Rifa'i Integrated Services" in joined
        assert "আল-মানারাতাইন" not in joined
        assert "Al-Manaratayn" not in joined

    def test_playbooks_canonical_facts_use_new_brand(self):
        from modules.conversation_layer.playbooks import CANONICAL_FACTS
        assert "Al-Rifa'i Integrated Services" in CANONICAL_FACTS["office_location"]
        assert "Al-Rifa'i Integrated Services" in CANONICAL_FACTS["trust"]
