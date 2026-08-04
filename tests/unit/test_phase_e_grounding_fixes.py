"""
Unit tests -- Phase E forensic-investigation fixes (2026-08-04, follow-up
session after the initial bridge3 restart surfaced real Admin<->Hermes
failures). Covers three root causes found via live production tracing:

1. modules.bridge_poller: AI_SAFE_MODE (and the rest of the customer-facing
   safety cascade) was incorrectly applying to Admin<->Hermes replies,
   silently diverting them to drafts the Admin never checks on WhatsApp.
2. modules.ai_console.provider_registry: cash_ledger_summary (BUS-006) had
   no exact_answer template, so its real tool data fell through to a
   generic LLM chat-completion that ignored it in favor of irrelevant RAG
   text -- producing a vague non-answer despite real data being available.
3. modules.ai_readonly_tools: get_daily_payments()/get_employee_month_
   payments() queried fpe_cash_transactions directly with a bad column
   reference (fpe_employees.employee_id, which doesn't exist -- the real PK
   is .id) AND the fazle_ai_reader role (what production actually uses) has
   no SELECT grant on that raw table at all, only on the sanctioned
   ai_read_cash_transactions view -- confirmed empirically via
   has_table_privilege() against both objects.

All three were verified against real production data during the
investigation (not just these unit tests) -- see management_decisions.md
for the full trace.
"""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ── Fix 1: AI_SAFE_MODE must not apply to Admin-role replies ─────────────────

class TestCrossBridgeLoopGuard:
    """Owner-approved incident fix (2026-08-04, same session): a real,
    active cross-bridge message loop was found live in production --
    bridge1/bridge2/bridge3 share overlapping WhatsApp account/LID
    identity, so an admin-reply sent to another bridge's own number
    arrives there as a brand-new genuine inbound message and bounces
    indefinitely once the Part 3 direct-send fix removed the draft-gate
    brake that used to stop this incidentally."""

    def test_loop_guard_precedes_direct_send_in_source(self):
        import modules.bridge_poller as bp
        source = inspect.getsource(bp)
        admin_block = source[source.index('if id_role == "admin":'):]
        i_guard = admin_block.index("LOOP_GUARD")
        i_send = admin_block.index("cascade bypassed for admin role")
        assert i_guard < i_send, "loop guard must be checked before the direct-send path"

    def test_guard_excludes_same_bridge_self_chat(self):
        """Replying to the Admin's own number via the SAME bridge it
        arrived on (the established self-chat pattern) must NOT be
        treated as a cross-bridge bounce."""
        import modules.bridge_poller as bp
        source = inspect.getsource(bp)
        assert "if name != bridge_name and num" in source

    def test_guard_checks_all_three_bridge_numbers(self):
        import modules.bridge_poller as bp
        source = inspect.getsource(bp)
        admin_block = source[source.index('if id_role == "admin":'):source.index('if id_role == "admin":') + 2500]
        for bridge_num_ref in ("bridge1_number", "bridge2_number", "bridge3_number"):
            assert bridge_num_ref in admin_block


class TestAdminHermesSafetyBypass:
    def test_admin_branch_precedes_customer_cascade_in_source(self):
        import modules.bridge_poller as bp
        source = inspect.getsource(bp)
        reply_block = source[source.index("    if reply:"):]
        i_admin_check = reply_block.index('id_role == "admin"')
        i_safe_mode = reply_block.index("if _settings.ai_safe_mode:")
        assert i_admin_check < i_safe_mode, (
            "the admin-role direct-send branch must be checked before the "
            "customer-facing AI_SAFE_MODE cascade, not after"
        )

    def test_admin_direct_send_uses_admin_hermes_purpose(self):
        import modules.bridge_poller as bp
        source = inspect.getsource(bp)
        assert "admin-hermes-reply:" in source
        # The customer-cascade's own purpose label must be unchanged.
        assert "customer-reply:" in source

    def test_customer_cascade_still_has_safe_mode_and_draft_gates(self):
        """Regression guard: the external/customer-facing safety cascade
        (the `else` branch) must be completely untouched -- same gates,
        same order, same behavior."""
        import modules.bridge_poller as bp
        source = inspect.getsource(bp)
        for marker in (
            "AI_SAFE_MODE",
            "DRAFT_ALWAYS",
            "FINANCIAL_DRAFT",
            "LOOP_DETECT",
            "OUTBOUND_SANITIZER_BLOCK",
            "OUTBOUND_LENGTH_GUARD",
            "P17-RECRUIT",
        ):
            assert marker in source, f"customer safety cascade marker missing: {marker!r}"


# ── Fix 2: cash_ledger_summary exact_answer template ──────────────────────────

class TestCashLedgerSummaryExactAnswer:
    async def test_cash_ledger_summary_uses_tool_answer_not_llm(self):
        from modules.ai_console.provider_registry import LLMProviderAdapter

        fake_evidence = MagicMock()
        fake_evidence.classification = {"requested_model": None, "history": []}
        fake_evidence.runtime = []
        fake_evidence.business = [{
            "tool_name": "cash_ledger_summary",
            "data": {
                "answer": "this month পেমেন্ট দেয়া হয়েছে 15 জনকে।\n(সোর্স: ai_read_cash_transactions)",
                "total_amount": 10400.0,
            },
        }]
        fake_evidence.memory = []
        fake_evidence.knowledge = []

        adapter = LLMProviderAdapter.__new__(LLMProviderAdapter)
        with patch("modules.ai_console.provider_registry.get_settings") as mock_settings, \
             patch("modules.ai_console.provider_registry.ai") as mock_ai:
            mock_settings.return_value.ollama_model = "test-model"
            mock_ai.generate_chat_reply = AsyncMock(side_effect=AssertionError("must not call generic LLM path"))
            result = await adapter.respond("এই মাসে মোট cash received কত?", fake_evidence)

        assert "ai_read_cash_transactions" in result["response"]
        assert result["status"] == "ok"
        mock_ai.generate_chat_reply.assert_not_called()

    async def test_falls_back_to_llm_when_tool_has_no_answer_field(self):
        """Regression guard: tools that still have no exact_answer template
        (and no data['answer']) keep using the existing generic LLM path --
        this fix is additive, not a removal of the fallback mechanism."""
        from modules.ai_console.provider_registry import LLMProviderAdapter

        fake_evidence = MagicMock()
        fake_evidence.classification = {"requested_model": None, "history": []}
        fake_evidence.runtime = []
        fake_evidence.business = [{"tool_name": "some_other_tool", "data": {"foo": "bar"}}]
        fake_evidence.memory = []
        fake_evidence.knowledge = []

        adapter = LLMProviderAdapter.__new__(LLMProviderAdapter)
        with patch("modules.ai_console.provider_registry.get_settings") as mock_settings, \
             patch("modules.ai_console.provider_registry.ai") as mock_ai:
            mock_settings.return_value.ollama_model = "test-model"
            mock_ai.generate_chat_reply = AsyncMock(return_value="llm answer")
            result = await adapter.respond("some question", fake_evidence)

        assert result["response"] == "llm answer"
        mock_ai.generate_chat_reply.assert_awaited_once()


# ── Fix 3: ai_readonly_tools queries the sanctioned view, correct join ────────

class TestAiReadonlyToolsCashQueries:
    def test_no_query_references_raw_fpe_cash_transactions_table(self):
        """fazle_ai_reader (the role production actually uses) has no
        SELECT grant on fpe_cash_transactions -- only on the
        ai_read_cash_transactions view. Any direct reference to the raw
        table in these two functions would always fail in production."""
        import modules.ai_readonly_tools as art
        for fn in (art.get_daily_payments, art.get_employee_month_payments):
            source = inspect.getsource(fn)
            assert "FROM fpe_cash_transactions" not in source, (
                f"{fn.__name__} still queries the raw table directly -- "
                "fazle_ai_reader cannot SELECT from it, must use "
                "ai_read_cash_transactions"
            )
            assert "FROM ai_read_cash_transactions" in source

    def test_no_bad_employee_id_column_reference_remains(self):
        """Regression guard for the original bug: fpe_employees' PK is
        `id`, not `employee_id` -- confirmed via the real FK constraint
        (fpe_cash_transactions_employee_id_fkey -> fpe_employees.id)."""
        import modules.ai_readonly_tools as art
        source = inspect.getsource(art)
        assert "e.employee_id" not in source
