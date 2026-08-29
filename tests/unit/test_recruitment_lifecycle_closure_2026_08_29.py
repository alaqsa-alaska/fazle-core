"""Recruitment lifecycle closure — Owner directive 2026-08-29.

Covers the 23 required scenarios (directive section R) for the four closure
gaps plus the personality/anti-repetition layer:

  GAP 1  candidate -> employee canonical link
  GAP 2  post-invitation lifecycle events (wbom_candidate_conversations.step)
  GAP 3  deterministic shortlist + frozen-recipient batch invitation
  GAP 4  authenticated Admin phone-call / offline context
  G      post-invitation message understanding
  H-M    warm human recruitment-officer persona (tone only, never business facts)

Mock-based where behaviour is deterministic Python; DB-backed (test_db_pool)
only where the SQL itself is under test (linkage last-9 match, shortlist
ordering, event retrieval).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from modules import recruitment_flow as rf


# ══════════════════════════════════════════════════════════════════════════
# A. interest / readiness  (R1-R6)
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_R3_explicit_agreement_classified_agreed():
    raw = '{"post_intake_status": "agreed", "confidence": 0.95}'
    with patch("modules.hermes_dispatch.call_decision_llm", new=AsyncMock(return_value=raw)):
        out = await rf._classify_post_intake_turn("8801700000001", "আমি চাকরিটা করতে রাজি")
    assert out["post_intake_status"] == "agreed"


@pytest.mark.asyncio
async def test_R4_explicit_join_ready_classified_joining_ready():
    raw = '{"post_intake_status": "joining_ready", "confidence": 0.9}'
    with patch("modules.hermes_dispatch.call_decision_llm", new=AsyncMock(return_value=raw)):
        out = await rf._classify_post_intake_turn("8801700000001", "আমি এখনই আসতে পারি")
    assert out["post_intake_status"] == "joining_ready"


@pytest.mark.asyncio
async def test_R5_considering_does_not_over_promote():
    # Model correctly returns "none" for a vague "চিন্তা করে দেখি" — the
    # classifier must NOT upgrade it to agreed/joining_ready.
    raw = '{"post_intake_status": "none", "confidence": 0.4}'
    with patch("modules.hermes_dispatch.call_decision_llm", new=AsyncMock(return_value=raw)):
        out = await rf._classify_post_intake_turn("8801700000001", "আচ্ছা, চিন্তা করে দেখি")
    assert out["post_intake_status"] is None


@pytest.mark.asyncio
async def test_R5b_ambiguous_low_confidence_not_promoted():
    raw = '{"post_intake_status": "agreed", "confidence": 0.3}'  # below floor
    with patch("modules.hermes_dispatch.call_decision_llm", new=AsyncMock(return_value=raw)):
        out = await rf._classify_post_intake_turn("8801700000001", "হুম দেখি")
    assert out["post_intake_status"] is None


def test_R6_deferred_states_are_not_terminal():
    # not_ready_now / future_contact must keep the candidate an active
    # post-intake conversation (they can message again = the readiness signal).
    assert "not_ready_now" not in rf.POST_INTAKE_TERMINAL_STATUSES
    assert "future_contact" not in rf.POST_INTAKE_TERMINAL_STATUSES
    assert rf.POST_INTAKE_TERMINAL_STATUSES == frozenset({"declined"})


def test_R1_R2_frozen_vocabulary_unchanged():
    # The Owner-locked migration-072 vocabulary must not have grown.
    assert rf.POST_INTAKE_STATUSES == frozenset({
        "awaiting_agreement", "agreed", "joining_ready",
        "salary_objection", "not_ready_now", "declined", "future_contact",
    })
    # 'joined' is a lifecycle EVENT, never a post_intake_status value.
    assert "joined" not in rf.POST_INTAKE_STATUSES
    assert "joined" in rf.LIFECYCLE_EVENT_STEPS


# ══════════════════════════════════════════════════════════════════════════
# B. semantic separation  (R1, R13)
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_R1_shortlist_excludes_merely_scored_candidates(test_db_pool):
    import app.database as db
    db._pool = test_db_pool
    async with test_db_pool.acquire() as c:
        await c.execute("DELETE FROM wbom_candidates")
        await c.execute(
            "INSERT INTO wbom_candidates (phone, funnel_stage, score, score_bucket, post_intake_status) "
            "VALUES ('8801711111111','scored',80,'hot',NULL),"
            "       ('8801722222222','scored',10,'cold','agreed'),"
            "       ('8801733333333','scored',90,'hot','joining_ready')"
        )
    got = await rf.get_willing_candidates()
    phones = [r["mobile"] for r in got]
    assert "8801711111111" not in phones  # high score, but no readiness evidence
    assert phones == ["8801733333333", "8801722222222"]  # joining_ready before agreed


def test_R13_persona_is_tone_only_business_rules_intact():
    from shared.reply_policy import build_whatsapp_recruitment_policy
    p = build_whatsapp_recruitment_policy(source="bridge3", user_message="বেতন কত?", kb_context="বেতন ১০,০০০-১৫,০০০")
    # personality present
    assert "recruitment officer" in p or "নিয়োগ সহকারী" in p
    # but the hard business guardrails are still there
    assert "নতুন সংখ্যা বানাবে না" in p
    assert "পদোন্নতি" in p and "নিশ্চয়তা দেবে না" in p
    assert "private payroll" in p


# ══════════════════════════════════════════════════════════════════════════
# C. conversation memory / anti-repetition  (R10, R11)
# ══════════════════════════════════════════════════════════════════════════

def test_R10_R11_history_and_contact_repetition_rules_in_prompt():
    from shared.reply_policy import build_whatsapp_recruitment_policy
    p = build_whatsapp_recruitment_policy(source="bridge3", user_message="hi", kb_context="x")
    assert "ইতিমধ্যে জানা তথ্য" in p and "আবার চাইবে না" in p          # R10
    assert "অফিস নম্বর/ঠিকানা সম্প্রতি দেওয়া থাকলে আবার দেবে না" in p  # R11


def test_R10_already_answered_guard_still_works():
    # A role already collected is not asked again (the freshly-parsed value
    # matches what's stored -> is_already_answered True -> don't re-ask).
    row = {"job_preference": "Operation Officer", "experience_years": 5, "age": 30}
    assert rf.is_already_answered(row, "job_preference", "Operation Officer") is True
    assert rf.is_already_answered(row, "experience", "5") is True
    # A genuine correction bypasses the guard (so it CAN be re-collected).
    assert rf.is_already_answered(row, "job_preference", "Operation Officer",
                                  correction_text="ভুল হয়েছে, আগেরটা সংশোধন করব") is False


# ══════════════════════════════════════════════════════════════════════════
# D. response-needed logic  (R12)
# ══════════════════════════════════════════════════════════════════════════

def test_R12_pause_rule_present_and_no_travelling_false_positive():
    from shared.reply_policy import build_whatsapp_recruitment_policy
    p = build_whatsapp_recruitment_policy(source="bridge3", user_message="x", kb_context="x")
    assert "চিন্তা করে দেখি" in p and "sales pitch শুরু করবে না" in p
    # "দেখি" must NOT be read as a travelling/arrival signal
    assert rf.classify_post_invitation_signal("আচ্ছা দেখি") is None


# ══════════════════════════════════════════════════════════════════════════
# E. persuasion  (nurture)
# ══════════════════════════════════════════════════════════════════════════

def test_nurture_allowed_but_bounded_in_prompt():
    from shared.reply_policy import build_whatsapp_recruitment_policy
    p = build_whatsapp_recruitment_policy(source="bridge3", user_message="প্রথমে কম টাকা কেন?", kb_context="x")
    assert "আপত্তি" in p and "লম্বা motivational রচনা নয়" in p
    assert "নতুন সংখ্যা বানাবে না" in p  # no invented figures


# ══════════════════════════════════════════════════════════════════════════
# F. invitation  (R7, R14-R17)
# ══════════════════════════════════════════════════════════════════════════

@pytest_asyncio.fixture
async def _three_ready_candidates(test_db_pool):
    import app.database as db
    db._pool = test_db_pool
    async with test_db_pool.acquire() as c:
        await c.execute("DELETE FROM wbom_candidate_conversations")
        await c.execute("DELETE FROM wbom_candidates")
        rows = await c.fetch(
            """INSERT INTO wbom_candidates
                   (phone, funnel_stage, score, score_bucket, post_intake_status, full_name, job_preference, updated_at)
               VALUES
                   ('8801710000001','scored',10,'cold','agreed','Karim','Escort', now() - interval '3 days'),
                   ('8801720000002','scored',10,'cold','joining_ready','Rahim','Escort', now() - interval '1 day'),
                   ('8801730000003','scored',10,'cold','joining_ready','Jasim','Escort', now() - interval '5 days')
               RETURNING candidate_id, phone"""
        )
    return {r["phone"]: r["candidate_id"] for r in rows}


@pytest.mark.asyncio
async def test_R14_deterministic_first_n(_three_ready_candidates):
    ids = list(_three_ready_candidates.values())
    ordered = await rf.resolve_invite_batch(ids, limit=2)
    phones = [r["mobile"] for r in ordered]
    # joining_ready first, oldest updated_at first among them -> Jasim(5d) then Rahim(1d)
    assert phones == ["8801730000003", "8801720000002"]


@pytest.mark.asyncio
async def test_R15_recipient_set_frozen_and_not_expanded(_three_ready_candidates):
    ids = list(_three_ready_candidates.values())
    frozen = await rf.resolve_invite_batch(ids, limit=2)
    assert len(frozen) == 2
    res = await rf.send_invite_batch(
        frozen, office_date="2026-09-05",
        message_body="৫ সেপ্টেম্বর অফিসে আসুন", authorized_by="8801700000001",
        _enqueue=AsyncMock(return_value=123),
    )
    assert res["total"] == 2
    assert len(res["sent"]) == 2  # never more than the frozen 2


@pytest.mark.asyncio
async def test_R16_exact_once_idempotency_key(_three_ready_candidates):
    frozen = await rf.resolve_invite_batch(list(_three_ready_candidates.values()), limit=1)
    enq = AsyncMock(return_value=1)
    await rf.send_invite_batch(frozen, office_date="2026-09-05", message_body="x", authorized_by="admin", _enqueue=enq)
    key = enq.call_args.kwargs["idempotency_key"]
    assert key == f"recruit_invite:{frozen[0]['candidate_id']}:2026-09-05"


@pytest.mark.asyncio
async def test_R17_partial_failure_does_not_resend_successes(_three_ready_candidates):
    frozen = await rf.resolve_invite_batch(list(_three_ready_candidates.values()))
    # first recipient already sent (enqueue returns None = dedup), second fails, third ok
    seq = [None, RuntimeError("bridge down"), 77]

    async def _enq(*a, **k):
        v = seq.pop(0)
        if isinstance(v, Exception):
            raise v
        return v

    res = await rf.send_invite_batch(frozen, office_date="2026-09-05", message_body="x", authorized_by="admin", _enqueue=_enq)
    assert len(res["skipped_duplicate"]) == 1
    assert len(res["failed"]) == 1
    assert len(res["sent"]) == 1


@pytest.mark.asyncio
async def test_R7_invitation_records_lifecycle_event(_three_ready_candidates, test_db_pool):
    frozen = await rf.resolve_invite_batch(list(_three_ready_candidates.values()), limit=1)
    cid = frozen[0]["candidate_id"]
    await rf.send_invite_batch(frozen, office_date="2026-09-05", message_body="x", authorized_by="8801700000001", _enqueue=AsyncMock(return_value=5))
    async with test_db_pool.acquire() as c:
        step = await c.fetchval(
            "SELECT step FROM wbom_candidate_conversations WHERE candidate_id=$1 AND step='invited'", cid)
    assert step == "invited"


# ══════════════════════════════════════════════════════════════════════════
# G. phone-call admin event  (R8)
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_R8_admin_phone_call_note_records_context_and_optional_status(test_db_pool):
    import app.database as db
    db._pool = test_db_pool
    async with test_db_pool.acquire() as c:
        await c.execute("DELETE FROM wbom_candidate_conversations")
        await c.execute("DELETE FROM wbom_candidates")
        cid = await c.fetchval(
            "INSERT INTO wbom_candidates (phone, funnel_stage, score, score_bucket, post_intake_status, full_name) "
            "VALUES ('8801799999999','scored',0,'cold','awaiting_agreement','Rahim') RETURNING candidate_id")
    res = await rf.record_admin_phone_call_note(
        "8801799999999", "Candidate Rahim ফোন করেছে। সব বুঝেছে। ৫ তারিখ আসবে।",
        admin_phone="8801700000001", readiness="joining_ready",
    )
    assert res["recorded"] is True and res["candidate_id"] == cid
    assert res["status_updated"] == "joining_ready"
    async with test_db_pool.acquire() as c:
        note = await c.fetchval(
            "SELECT message_text FROM wbom_candidate_conversations WHERE candidate_id=$1 AND step='phone_call_note'", cid)
        st = await c.fetchval("SELECT post_intake_status FROM wbom_candidates WHERE candidate_id=$1", cid)
        src = await c.fetchval(
            "SELECT source_bridge FROM wbom_candidate_conversations WHERE candidate_id=$1 AND step='phone_call_note'", cid)
    assert "ফোন করেছে" in note
    assert st == "joining_ready"
    assert src == "admin:8801700000001"  # Admin-attributed, not "Hermes heard the call"


@pytest.mark.asyncio
async def test_R8b_note_without_readiness_does_not_change_status(test_db_pool):
    import app.database as db
    db._pool = test_db_pool
    async with test_db_pool.acquire() as c:
        await c.execute("DELETE FROM wbom_candidates")
        await c.execute(
            "INSERT INTO wbom_candidates (phone, funnel_stage, score, score_bucket, post_intake_status) "
            "VALUES ('8801788888888','scored',0,'cold','awaiting_agreement')")
    res = await rf.record_admin_phone_call_note(
        "8801788888888", "কথা হয়েছে, ভাবছে", admin_phone="8801700000001")
    assert res["recorded"] is True and res["status_updated"] is None


# ══════════════════════════════════════════════════════════════════════════
# H. post-invitation continuation  (R9)
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("text,expect", [
    ("আমি রওনা দিয়েছি", "travelling"),
    ("আমি গাড়িতে আছি", "travelling"),
    ("অফিসের পথে আছি", "travelling"),
    ("আমি অফিসে এসেছি", "arrived_office"),
    ("গেটে আছি", "arrived_office"),
    ("আচ্ছা আসব ইনশাআল্লাহ", "invitation_acknowledged"),
    ("বেতন কত হবে?", None),
    ("", None),
])
def test_R9_post_invitation_signal_classification(text, expect):
    assert rf.classify_post_invitation_signal(text) == expect


# ══════════════════════════════════════════════════════════════════════════
# I. candidate -> employee  (R18, R19, R21)
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_R18_candidate_links_to_employee_once(test_db_pool):
    import app.database as db
    db._pool = test_db_pool
    async with test_db_pool.acquire() as c:
        await c.execute("DELETE FROM wbom_candidate_conversations")
        await c.execute("DELETE FROM wbom_candidates")
        await c.execute("DELETE FROM wbom_employees")
        cid = await c.fetchval(
            "INSERT INTO wbom_candidates (phone, funnel_stage, score, score_bucket, post_intake_status, full_name) "
            "VALUES ('8801812345678','scored',0,'cold','joining_ready','Karim') RETURNING candidate_id")
        eid = await c.fetchval(
            "INSERT INTO wbom_employees (employee_name, employee_mobile, designation) "
            "VALUES ('Karim','01812345678','Escort') RETURNING employee_id")  # 01-form, candidate is 8801-form
    res = await rf.link_candidate_to_employee("01812345678", eid, employee_name="Karim")
    assert res["linked"] is True and res["candidate_id"] == cid
    async with test_db_pool.acquire() as c:
        linked = await c.fetchval("SELECT linked_employee_id FROM wbom_candidates WHERE candidate_id=$1", cid)
        joined = await c.fetchval(
            "SELECT step FROM wbom_candidate_conversations WHERE candidate_id=$1 AND step='joined'", cid)
    assert linked == eid
    assert joined == "joined"
    # idempotent
    res2 = await rf.link_candidate_to_employee("01812345678", eid)
    assert res2["linked"] is True and res2["reason"] in ("already_linked", "already_linked_same_employee")


@pytest.mark.asyncio
async def test_R18b_never_silently_repoints_to_different_employee(test_db_pool):
    import app.database as db
    db._pool = test_db_pool
    async with test_db_pool.acquire() as c:
        await c.execute("DELETE FROM wbom_candidates")
        await c.execute("DELETE FROM wbom_employees")
        e1 = await c.fetchval("INSERT INTO wbom_employees (employee_name, employee_mobile, designation) VALUES ('A','01900000001','Escort') RETURNING employee_id")
        e2 = await c.fetchval("INSERT INTO wbom_employees (employee_name, employee_mobile, designation) VALUES ('B','01900000002','Escort') RETURNING employee_id")
        await c.execute(
            "INSERT INTO wbom_candidates (phone, funnel_stage, score, score_bucket, linked_employee_id) "
            "VALUES ('8801900000001','scored',0,'cold',$1)", e1)
    res = await rf.link_candidate_to_employee("01900000001", e2)
    assert res["linked"] is False
    assert res["needs_admin_review"] is True
    assert res["reason"] == "already_linked_different_employee"


@pytest.mark.asyncio
async def test_R19_continuity_survives_phone_change(test_db_pool):
    # After link, the candidate is found by employee_id even if the phone
    # on the candidate row no longer matches any employee mobile.
    import app.database as db
    db._pool = test_db_pool
    async with test_db_pool.acquire() as c:
        await c.execute("DELETE FROM wbom_candidates")
        await c.execute("DELETE FROM wbom_employees")
        eid = await c.fetchval("INSERT INTO wbom_employees (employee_name, employee_mobile, designation) VALUES ('Karim','01555000001','Escort') RETURNING employee_id")
        cid = await c.fetchval(
            "INSERT INTO wbom_candidates (phone, funnel_stage, score, score_bucket, linked_employee_id) "
            "VALUES ('8801555000001','scored',0,'cold',$1) RETURNING candidate_id", eid)
        # employee's phone later changes entirely
        await c.execute("UPDATE wbom_employees SET employee_mobile='01999888777' WHERE employee_id=$1", eid)
        found = await c.fetchval("SELECT candidate_id FROM wbom_candidates WHERE linked_employee_id=$1", eid)
    assert found == cid


@pytest.mark.asyncio
async def test_R21_recruitment_history_retrievable_after_conversion(test_db_pool):
    import app.database as db
    db._pool = test_db_pool
    async with test_db_pool.acquire() as c:
        await c.execute("DELETE FROM wbom_candidate_conversations")
        await c.execute("DELETE FROM wbom_candidates")
        cid = await c.fetchval(
            "INSERT INTO wbom_candidates (phone, funnel_stage, score, score_bucket) "
            "VALUES ('8801666000001','scored',0,'cold') RETURNING candidate_id")
        await c.execute(
            "INSERT INTO wbom_candidate_conversations (candidate_id, step, direction, message_text) "
            "VALUES ($1,'name','inbound','আমার নাম করিম'),($1,'job_preference','inbound','অপারেশন')", cid)
    await rf.record_candidate_lifecycle_event(cid, "joined", "employee #9 created", source="employee_creation")
    async with test_db_pool.acquire() as c:
        hist = await c.fetch(
            "SELECT step, direction, message_text FROM wbom_candidate_conversations "
            "WHERE candidate_id=$1 ORDER BY conv_id", cid)
    steps = [h["step"] for h in hist]
    assert "name" in steps and "job_preference" in steps and "joined" in steps
    assert hist[-1]["direction"] == "event"


# ══════════════════════════════════════════════════════════════════════════
# J/K. business continuation + security  (R20, R22, R23)
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_R20_employee_identity_outranks_candidate():
    from modules.identity_brain import _ROLE_PRIORITY
    assert _ROLE_PRIORITY["employee"] > _ROLE_PRIORITY["candidate"]


def test_R22_R23_privacy_and_authority_rules_present():
    from shared.reply_policy import build_whatsapp_recruitment_policy
    p = build_whatsapp_recruitment_policy(source="bridge3", user_message="x", kb_context="x")
    assert "private payroll" in p                      # R23 — no employee payroll data
    assert "recruitment-বহির্ভূত প্রশ্নের উত্তর দেবে না" in p


def test_R22_group_participant_never_admin_authority():
    # Shortlist / batch-invite authorization is a trusted DIRECT admin
    # channel check — a group sender can never reach it.
    from modules.admin_direct_execution import is_trusted_direct_admin_channel
    assert is_trusted_direct_admin_channel(
        sender_phone="8801700000001", source_bridge="group:120363@g.us",
        admin_bridge2_number="8801700000001", control_channels=frozenset(),
    ) is False


# ══════════════════════════════════════════════════════════════════════════
# Housekeeping — event step validation
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_lifecycle_event_rejects_unknown_step():
    out = await rf.record_candidate_lifecycle_event(1, "not_a_real_step", "x")
    assert out is None


# ══════════════════════════════════════════════════════════════════════════
# F2. batch-invitation instruction parsing + trusted-channel gate
# ══════════════════════════════════════════════════════════════════════════

from datetime import datetime, timezone, timedelta
_BD = timezone(timedelta(hours=6))


def test_invite_instruction_parse_and_absolute_date():
    from modules.admin_direct_execution import parse_invite_batch_instruction, _resolve_office_date
    now = datetime(2026, 8, 29, tzinfo=_BD)
    assert _resolve_office_date("৫ সেপ্টেম্বর অফিসে আসতে বলো", now=now) == "2026-09-05"
    assert _resolve_office_date("come on 2026-09-05", now=now) == "2026-09-05"
    assert _resolve_office_date("আগামী ৫ তারিখ", now=now) == "2026-09-05"
    # past date in-year rolls to next year, never stays past
    assert _resolve_office_date("5 January", now=now) == "2027-01-05"

    p = parse_invite_batch_instruction("রেডি লোকদের প্রথম ৫ জনকে ৫ সেপ্টেম্বর অফিসে আসতে বলো")
    assert p == {"count": 5, "office_date": "2026-09-05"}
    # under-specified -> clarification, never executes
    assert parse_invite_batch_instruction("রাজি লোকদের অফিসে আসতে বলো").get("needs_clarification")
    # not an invite instruction at all -> None (falls through to Hermes)
    assert parse_invite_batch_instruction("আজকের হাজিরা দাও") is None


@pytest.mark.asyncio
async def test_invite_batch_rejects_untrusted_channel():
    from modules.admin_direct_execution import execute_trusted_invite_batch
    res = await execute_trusted_invite_batch(
        "রেডি লোকদের প্রথম ৫ জনকে ৫ সেপ্টেম্বর অফিসে আসতে বলো",
        sender_phone="8801999999999", source_bridge="bridge3",  # not the trusted admin channel
    )
    assert res["ok"] is False and res["stage"] == "channel_check"


@pytest.mark.asyncio
async def test_nl_shortlist_intent_is_phoneless_and_admin_readable(test_db_pool):
    import app.database as db
    db._pool = test_db_pool
    async with test_db_pool.acquire() as c:
        await c.execute("DELETE FROM wbom_candidates")
        await c.execute(
            "INSERT INTO wbom_candidates (phone, funnel_stage, score, score_bucket, post_intake_status, full_name, job_preference) "
            "VALUES ('8801755000001','scored',5,'cold','joining_ready','Karim','Escort')")
    from modules.admin_commands.nl_router import classify_nl_intent, process_nl_admin_query
    assert classify_nl_intent("যারা চাকরি করতে রাজি তাদের দেখাও") == "willing_candidates"
    out = await process_nl_admin_query("রেডি লোকদের list দাও", "8801700000001")
    assert "Karim" in out and "8801755000001" in out


@pytest.mark.asyncio
async def test_lifecycle_event_accepts_known_step_via_audit(monkeypatch):
    called = {}

    async def _audit(cid, step, msg, source, mid, *, direction="inbound", conn=None):
        called.update(cid=cid, step=step, direction=direction)
        return True

    monkeypatch.setattr(rf, "_audit_candidate_conversation", _audit)
    out = await rf.record_candidate_lifecycle_event(7, "arrived_office", "গেটে আছি", direction="inbound")
    assert out is True
    assert called == {"cid": 7, "step": "arrived_office", "direction": "inbound"}
