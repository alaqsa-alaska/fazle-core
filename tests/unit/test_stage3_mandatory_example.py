"""Phase B Stage 3 — the mandatory real two-message example (spec Section
13), 2026-07-19.

Must pass end-to-end in controlled testing with BOTH
MULTI_MESSAGE_ESCORT_PARSER_ENABLED and BANGLA_SEMANTIC_PARSER_ENABLED on
(both still False in production pending their own Owner checkpoint).
DB-backed -- skipped automatically when TEST_DB_PASSWORD is unavailable
(this session's standing constraint), hand-verified against a disposable
Postgres container instead (see scratchpad mandatory_example.py, run
2026-07-19: all assertions passed).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

MSG_1 = "Bushra Biva-1(2nd Trip)\n1250MT\n01821375926\nOuter To Local\nAC:SST/NMT"
MSG_2_BANGLA = "ভেসেল নেম Horda"


class TestMandatoryRealExample:

    @pytest.mark.asyncio
    async def test_two_message_bushra_biva_horda_example(self, test_db_pool, monkeypatch):
        from app.config import get_settings
        from modules.conversation_memory.thread_reconstruction import shadow_observe_escort_client_message
        import modules.conversation_memory as cm
        from app.database import fetch_val

        monkeypatch.setenv("CONVERSATION_MEMORY_ENABLED", "true")
        monkeypatch.setenv("WHATSAPP_THREAD_RECONSTRUCTION_ENABLED", "true")
        monkeypatch.setenv("MULTI_MESSAGE_ESCORT_PARSER_ENABLED", "true")
        monkeypatch.setenv("BANGLA_SEMANTIC_PARSER_ENABLED", "true")
        monkeypatch.setenv("ESCORT_CLIENT_PHONES", "")
        get_settings.cache_clear()

        phone = "01900000501"
        try:
            programs_before = await fetch_val("SELECT count(*) FROM wbom_escort_programs")

            await shadow_observe_escort_client_message(text=MSG_1, sender_phone=phone, source="bridge2", message_id="MAND-1", sender_role="client_escort_buyer")
            await shadow_observe_escort_client_message(text=MSG_2_BANGLA, sender_phone=phone, source="bridge2", message_id="MAND-2", sender_role="client_escort_buyer")

            programs_after = await fetch_val("SELECT count(*) FROM wbom_escort_programs")

            threads = await cm.get_open_threads(bridge_id="bridge2", counterparty_phone=phone, business_domain="escort")
            assert len(threads) == 1, "the two messages must converge on exactly one candidate"
            f = threads[0]["fields"]

            assert f["mother_vessel"]["value"] == "MV HORDA"
            assert f["lighter_vessel"]["value"] == "Bushra Biva-1"
            assert f["trip"]["value"] == "2nd Trip"
            assert f["capacity"]["value"] == "1250 MT"
            assert f["master_mobile"]["value"] == "01821375926"
            assert f["destination"]["value"] == "Chattogram"

            is_complete = {"mother_vessel", "lighter_vessel", "master_mobile"} <= set(f.keys())
            would_create_draft = is_complete and not threads[0]["conflict_flags"]
            assert is_complete is True
            assert would_create_draft is True
            assert programs_after == programs_before, "no actual production draft may be created"
        finally:
            monkeypatch.delenv("CONVERSATION_MEMORY_ENABLED", raising=False)
            monkeypatch.delenv("WHATSAPP_THREAD_RECONSTRUCTION_ENABLED", raising=False)
            monkeypatch.delenv("MULTI_MESSAGE_ESCORT_PARSER_ENABLED", raising=False)
            monkeypatch.delenv("BANGLA_SEMANTIC_PARSER_ENABLED", raising=False)
            monkeypatch.delenv("ESCORT_CLIENT_PHONES", raising=False)
            get_settings.cache_clear()
