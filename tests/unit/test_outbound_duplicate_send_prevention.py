"""Unit tests -- duplicate outbound send prevention (2026-08-21, urgent
bridge3 recruitment-reply recovery pass, Task 9): repeated inbound/retry
must not create duplicate outbound sends to the same recipient.

Two independent mechanisms combine to guarantee this for the recruitment
path:
  1. modules.outbound.enqueue()'s idempotency_key has a real UNIQUE DB
     constraint (fazle_outbound_queue_idempotency_key_key) with
     ON CONFLICT (idempotency_key) DO NOTHING -- a retry with the same key
     is a true no-op, not a second row.
  2. modules.bridge_poller's REPLY_COOLDOWN (60s) gate, independent of (1),
     stops a rapid burst of genuine new inbound messages from each
     triggering their own separate reply within the cooldown window (see
     test_recruitment_cooldown_lead_creation_fix.py for that regression).
"""
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


async def test_enqueue_sql_uses_on_conflict_do_nothing_for_idempotency_key():
    import inspect
    from modules import outbound
    source = inspect.getsource(outbound.enqueue)
    assert "ON CONFLICT (idempotency_key) DO NOTHING" in source


async def test_duplicate_idempotency_key_returns_none_not_a_new_id():
    from modules import outbound
    # ON CONFLICT ... DO NOTHING with no RETURNING match -> fetch_one gets
    # no row back, matching a real duplicate-key insert attempt.
    with patch("modules.outbound.fetch_one", new=AsyncMock(return_value=None)):
        qid = await outbound.enqueue(
            "8801966011609", "reply text",
            source_bridge="bridge3", purpose="customer-reply:recruitment",
            idempotency_key="recruit:8801966011609:msg-14479",
        )
    assert qid is None


async def test_first_enqueue_with_a_fresh_idempotency_key_returns_a_real_id():
    from modules import outbound
    with patch("modules.outbound.fetch_one", new=AsyncMock(return_value={"id": 9999})):
        qid = await outbound.enqueue(
            "8801966011609", "reply text",
            source_bridge="bridge3", purpose="customer-reply:recruitment",
            idempotency_key="recruit:8801966011609:msg-14479",
        )
    assert qid == 9999
