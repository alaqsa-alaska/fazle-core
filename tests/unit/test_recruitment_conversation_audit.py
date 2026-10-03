"""Focused S5 tests for the recruitment candidate conversation audit trail."""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


pytestmark = pytest.mark.asyncio


def _transaction_db_conn():
    connection = MagicMock()
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=None)
    transaction.__aexit__ = AsyncMock(return_value=False)
    connection.transaction.return_value = transaction

    @asynccontextmanager
    async def fake_db_conn():
        yield connection

    return fake_db_conn, connection, transaction


async def test_audit_insert_preserves_candidate_step_and_message():
    from modules.recruitment_flow import _audit_candidate_conversation

    with patch(
        "modules.recruitment_flow.fetch_val", new=AsyncMock(return_value=41)
    ) as fetch_val_mock:
        inserted = await _audit_candidate_conversation(
            candidate_id=17,
            step="age",
            message="25",
            source="bridge1",
            message_id="msg-100",
        )

    assert inserted is True
    sql, *args = fetch_val_mock.await_args.args
    assert "INSERT INTO wbom_candidate_conversations" in sql
    assert "ON CONFLICT (source_bridge, message_id)" in sql
    # 2026-08-29: _audit_candidate_conversation gained a `direction` param
    # (default 'inbound'); it is positional arg 5, message_id stays last.
    assert args == [17, "age", "25", "bridge1", "inbound", "msg-100"]


async def test_intake_audits_pre_transition_step():
    from modules.recruitment_flow import intake_message

    session = {
        "id": 9,
        "phone": "8801700000009",
        "candidate_id": 17,
        "collection_step": "age",
        "funnel_stage": "collecting",
    }
    audit_mock = AsyncMock(return_value=True)
    update_mock = AsyncMock()
    fake_db_conn, connection, _ = _transaction_db_conn()

    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=session)), \
         patch("modules.recruitment_flow.db_conn", new=fake_db_conn), \
         patch("modules.recruitment_flow._audit_candidate_conversation", new=audit_mock), \
         patch("modules.recruitment_flow._update_session", new=update_mock):
        result = await intake_message(
            session["phone"], " 25\n", source="bridge1", message_id="msg-age"
        )

    assert result["action"] == "collecting"
    audit_mock.assert_awaited_once_with(
        candidate_id=17,
        step="age",
        message=" 25\n",
        source="bridge1",
        message_id="msg-age",
        conn=connection,
    )
    assert update_mock.await_args.args[1]["collection_step"] == "area"


async def test_audit_rows_remain_scoped_to_the_supplied_candidate():
    from modules.recruitment_flow import _audit_candidate_conversation

    fetch_val_mock = AsyncMock(side_effect=[101, 102])
    with patch("modules.recruitment_flow.fetch_val", new=fetch_val_mock):
        await _audit_candidate_conversation(11, "name", "Candidate A", "bridge1", "a-1")
        await _audit_candidate_conversation(22, "name", "Candidate B", "bridge1", "b-1")

    assert fetch_val_mock.await_args_list[0].args[1] == 11
    assert fetch_val_mock.await_args_list[1].args[1] == 22


async def test_duplicate_message_id_does_not_advance_the_session():
    from modules.recruitment_flow import STEP_QUESTIONS, intake_message

    session = {
        "id": 9,
        "phone": "8801700000009",
        "candidate_id": 17,
        "collection_step": "age",
        "funnel_stage": "collecting",
    }
    update_mock = AsyncMock()
    fake_db_conn, _, _ = _transaction_db_conn()

    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=session)), \
         patch("modules.recruitment_flow.db_conn", new=fake_db_conn), \
         patch(
             "modules.recruitment_flow._audit_candidate_conversation",
             new=AsyncMock(return_value=False),
         ), \
         patch("modules.recruitment_flow._update_session", new=update_mock):
        result = await intake_message(
            session["phone"], "25", source="bridge1", message_id="msg-retry"
        )

    assert result == {
        "reply": STEP_QUESTIONS["age"],
        "action": "collecting",
        "session_id": 9,
    }
    update_mock.assert_not_awaited()


async def test_audit_failure_is_logged_and_intake_continues(caplog):
    from modules.recruitment_flow import intake_message

    session = {
        "id": 9,
        "phone": "8801700000009",
        "candidate_id": 17,
        "collection_step": "age",
        "funnel_stage": "collecting",
    }
    update_mock = AsyncMock()
    fake_db_conn, _, _ = _transaction_db_conn()

    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=session)), \
         patch("modules.recruitment_flow.db_conn", new=fake_db_conn), \
         patch(
             "modules.recruitment_flow.fetch_val",
             new=AsyncMock(side_effect=RuntimeError("audit unavailable")),
         ), \
         patch("modules.recruitment_flow._update_session", new=update_mock):
        result = await intake_message(
            session["phone"], "25", source="bridge1", message_id="msg-fail"
        )

    assert result["action"] == "collecting"
    assert result["reply"]
    update_mock.assert_awaited_once()
    assert update_mock.await_args.kwargs["conn"] is not None
    assert "conversation audit failed" in caplog.text


async def test_audit_outage_session_failure_rolls_back_without_advancing():
    from modules.recruitment_flow import STEP_QUESTIONS, intake_message

    session = {
        "id": 9,
        "phone": "8801700000009",
        "candidate_id": 17,
        "collection_step": "age",
        "funnel_stage": "collecting",
    }
    fake_db_conn, _, transaction = _transaction_db_conn()
    update_mock = AsyncMock(side_effect=RuntimeError("update failed"))
    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=session)), \
         patch("modules.recruitment_flow.db_conn", new=fake_db_conn), \
         patch(
             "modules.recruitment_flow._audit_candidate_conversation",
             new=AsyncMock(side_effect=RuntimeError("audit unavailable")),
         ), \
         patch("modules.recruitment_flow._update_session", new=update_mock):
        result = await intake_message(
            session["phone"], "25", source="bridge1", message_id="msg-fail-update"
        )

    assert result == {
        "reply": STEP_QUESTIONS["age"],
        "action": "collecting",
        "session_id": 9,
    }
    assert transaction.__aexit__.await_count == 2
    assert transaction.__aexit__.await_args.args[0] is RuntimeError
    update_mock.assert_awaited_once()


async def test_audit_outage_final_failure_rolls_back_without_scoring_session():
    from modules.recruitment_flow import STEP_QUESTIONS, intake_message

    session = {
        "id": 9,
        "phone": "8801700000009",
        "candidate_id": 17,
        "collection_step": "phone_confirm",
        "funnel_stage": "collecting",
        "full_name": "Candidate",
        "age": 25,
        "area": "Dhaka",
        "job_preference": "Security Guard",
        "experience_years": 2,
    }
    fake_db_conn, connection, transaction = _transaction_db_conn()
    update_mock = AsyncMock()
    finalize_mock = AsyncMock(side_effect=RuntimeError("finalize failed"))
    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=session)), \
         patch("modules.recruitment_flow.db_conn", new=fake_db_conn), \
         patch(
             "modules.recruitment_flow._audit_candidate_conversation",
             new=AsyncMock(side_effect=RuntimeError("audit unavailable")),
         ), \
         patch("modules.recruitment_flow._update_session", new=update_mock), \
         patch("modules.recruitment_flow._finalize_candidate", new=finalize_mock):
        result = await intake_message(
            session["phone"], "01700000009", source="bridge1", message_id="msg-fail-final"
        )

    assert result == {
        "reply": STEP_QUESTIONS["phone_confirm"],
        "action": "collecting",
        "session_id": 9,
    }
    assert transaction.__aexit__.await_count == 2
    assert transaction.__aexit__.await_args.args[0] is RuntimeError
    update_mock.assert_awaited_once()
    assert update_mock.await_args.kwargs["conn"] is connection
    finalize_mock.assert_awaited_once()
    assert finalize_mock.await_args.kwargs["conn"] is connection


async def test_new_intake_links_candidate_and_audits_trigger():
    from modules.recruitment_flow import intake_message

    audit_mock = AsyncMock(return_value=True)
    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=None)), \
         patch("modules.recruitment_flow._create_session", new=AsyncMock(return_value=99)), \
         patch(
             "modules.recruitment_flow._ensure_candidate_for_session",
             new=AsyncMock(return_value=17),
         ) as ensure_mock, \
         patch("modules.recruitment_flow._audit_candidate_conversation", new=audit_mock):
        result = await intake_message(
            "8801700000009",
            "চাকরি দরকার",
            source="bridge1",
            role="candidate",
            message_id="msg-start",
        )

    assert result["action"] == "created"
    ensure_mock.assert_awaited_once()
    audit_mock.assert_awaited_once_with(
        candidate_id=17,
        step="intake",
        message="চাকরি দরকার",
        source="bridge1",
        message_id="msg-start",
    )


async def test_candidate_creation_links_the_session():
    from modules.recruitment_flow import _ensure_candidate_for_session

    fetch_val_mock = AsyncMock(return_value=17)
    execute_mock = AsyncMock()
    with patch("modules.recruitment_flow.fetch_val", new=fetch_val_mock), \
         patch("modules.recruitment_flow.execute", new=execute_mock):
        candidate_id = await _ensure_candidate_for_session(
            "8801700000009", 99, "চাকরি দরকার", "bridge1"
        )

    assert candidate_id == 17
    assert "ON CONFLICT (phone) DO NOTHING" in fetch_val_mock.await_args.args[0]
    link_sql, *link_args = execute_mock.await_args.args
    assert "UPDATE fazle_recruitment_sessions" in link_sql
    assert link_args == [17, 99]


async def test_route_propagates_message_id_to_active_intake():
    from modules.recruitment_flow import route_recruitment_message

    session = {
        "id": 9,
        "collection_step": "age",
        "funnel_stage": "collecting",
    }
    intake_mock = AsyncMock(return_value={"reply": "next", "action": "collecting"})
    with patch(
        "modules.recruitment_flow.recruitment_eligibility",
        new=AsyncMock(return_value={
            "eligible": True,
            "autosend": True,
            "reason": "explicit_recruitment",
            "active_session": session,
        }),
    ), patch("modules.recruitment_flow.intake_message", new=intake_mock):
        result = await route_recruitment_message(
            "8801700000009",
            "25",
            source="bridge1",
            role="candidate",
            message_id="msg-age",
        )

    assert result["reply"] == "next"
    intake_mock.assert_awaited_once_with(
        "8801700000009",
        "25",
        "bridge1",
        role="candidate",
        message_id="msg-age",
    )


async def test_llm_priority_active_session_audits_before_returning_reply():
    from modules.recruitment_flow import route_recruitment_message

    session = {
        "id": 9,
        "candidate_id": 17,
        "collection_step": "age",
        "funnel_stage": "collecting",
    }
    audit_mock = AsyncMock(return_value=True)
    with patch(
        "modules.recruitment_flow.recruitment_eligibility",
        new=AsyncMock(return_value={
            "eligible": True,
            "autosend": True,
            "reason": "session_continuation",
            "active_session": session,
        }),
    ), patch(
        "modules.recruitment_flow._is_llm_priority_phone", return_value=True
    ), patch(
        "modules.conversation_layer.generate_recruitment_reply",
        new=AsyncMock(return_value="priority reply"),
    ), patch(
        "modules.recruitment_flow._audit_candidate_conversation", new=audit_mock
    ):
        result = await route_recruitment_message(
            "8801700000009",
            "25",
            source="bridge1",
            role="candidate",
            message_id="msg-priority",
        )

    assert result["reply"] == "priority reply"
    audit_mock.assert_awaited_once_with(
        candidate_id=17,
        step="age",
        message="25",
        source="bridge1",
        message_id="msg-priority",
    )


async def test_route_b_history_fallback_persists_only_exact_inbound_text():
    from modules.route_b_handoff import _check_recruitment_handoff

    decisions = [
        {"eligible": False},
        {"eligible": True, "reason": "history_signal"},
    ]
    intake_mock = AsyncMock(return_value={"reply": "name?", "action": "created"})
    with patch(
        "modules.route_b_handoff.recruitment_eligibility",
        new=AsyncMock(side_effect=decisions),
    ), patch(
        "modules.route_b_handoff.route_recruitment_message",
        new=AsyncMock(return_value={"eligible": False, "reply": None}),
    ), patch("modules.route_b_handoff.intake_message", new=intake_mock):
        result = await _check_recruitment_handoff(
            "8801700000009",
            "yes",
            "bridge1",
            "unknown",
            "unknown",
            "I need a job",
            "recruitment",
            "msg-history",
        )

    assert result is not None
    intake_mock.assert_awaited_once_with(
        "8801700000009",
        "yes",
        "bridge1",
        role="unknown",
        message_id="msg-history",
        trigger_text="I need a job\nyes",
    )


async def test_transition_failure_rolls_back_without_advancing():
    from modules.recruitment_flow import intake_message

    session = {
        "id": 9,
        "phone": "8801700000009",
        "candidate_id": 17,
        "collection_step": "age",
        "funnel_stage": "collecting",
    }
    fake_db_conn, connection, transaction = _transaction_db_conn()

    audit_mock = AsyncMock(return_value=True)
    update_mock = AsyncMock(side_effect=RuntimeError("update failed"))
    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=session)), \
         patch("modules.recruitment_flow.db_conn", new=fake_db_conn), \
         patch("modules.recruitment_flow._audit_candidate_conversation", new=audit_mock), \
         patch("modules.recruitment_flow._update_session", new=update_mock):
        result = await intake_message(
            session["phone"], "25", source="bridge1", message_id="msg-atomic"
        )

    assert result["action"] == "collecting"
    assert result["reply"].startswith("আপনার বয়স")
    transaction.__aexit__.assert_awaited_once()
    assert transaction.__aexit__.await_args.args[0] is RuntimeError
    assert audit_mock.await_args.kwargs["conn"] is connection
    update_mock.assert_awaited_once()
    assert update_mock.await_args.kwargs["conn"] is connection


async def test_final_candidate_failure_rolls_back_without_scoring_session():
    from modules.recruitment_flow import STEP_QUESTIONS, intake_message

    session = {
        "id": 9,
        "phone": "8801700000009",
        "candidate_id": 17,
        "collection_step": "phone_confirm",
        "funnel_stage": "collecting",
        "full_name": "Candidate",
        "age": 25,
        "area": "Dhaka",
        "job_preference": "Security Guard",
        "experience_years": 2,
    }
    fake_db_conn, connection, transaction = _transaction_db_conn()
    update_mock = AsyncMock()
    finalize_mock = AsyncMock(side_effect=RuntimeError("finalize failed"))
    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=session)), \
         patch("modules.recruitment_flow.db_conn", new=fake_db_conn), \
         patch(
             "modules.recruitment_flow._audit_candidate_conversation",
             new=AsyncMock(return_value=True),
         ), \
         patch("modules.recruitment_flow._update_session", new=update_mock), \
         patch("modules.recruitment_flow._finalize_candidate", new=finalize_mock):
        result = await intake_message(
            session["phone"], "01700000009", source="bridge1", message_id="msg-final"
        )

    assert result == {
        "reply": STEP_QUESTIONS["phone_confirm"],
        "action": "collecting",
        "session_id": 9,
    }
    transaction.__aexit__.assert_awaited_once()
    assert transaction.__aexit__.await_args.args[0] is RuntimeError
    update_mock.assert_awaited_once()
    assert update_mock.await_args.kwargs["conn"] is connection


async def test_null_message_ids_remain_append_only():
    from modules.recruitment_flow import _audit_candidate_conversation

    fetch_val_mock = AsyncMock(side_effect=[101, 102])
    with patch("modules.recruitment_flow.fetch_val", new=fetch_val_mock):
        first = await _audit_candidate_conversation(17, "age", "25", "bridge1")
        second = await _audit_candidate_conversation(17, "age", "25", "bridge1")

    assert first is True
    assert second is True
    assert fetch_val_mock.await_count == 2
    assert fetch_val_mock.await_args_list[0].args[-1] is None
    assert fetch_val_mock.await_args_list[1].args[-1] is None