"""
Hermes Conversation Policy — SHADOW ENGINE (Owner directive 2026-08-30, Phase 3).

Manage conversations by TOPIC / SUBJECT, not by turn-taking. A topic has a
lifecycle:

    open  →  waiting  →  completed  →  closed

A closed topic stays closed until the USER reopens it. A single conversation
may hold several independent topics (payment closed, attendance closed, duty
open) — a new message about one topic never reopens another.

This module is SHADOW-ONLY. It computes and logs a proposed decision for every
eligible turn; it does NOT change any live reply. Promotion to live authority
is a separate, owner-gated step after the shadow corpus is reviewed.

State reuse: the live topic state lives in `fazle_conversation_threads`
(migration 053) — `business_domain`/`business_intent` ≈ active_topic,
`state` ≈ topic_status, `fields` ≈ information_given, `last_system_action` ≈
next_best_action. No new table, no migration.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, asdict
from typing import Optional

log = logging.getLogger("fazle.conversation_policy")

# ── Topic lifecycle vocabulary ──────────────────────────────────────────────
STATUS_OPEN = "open"
STATUS_WAITING = "waiting"
STATUS_COMPLETED = "completed"
STATUS_CLOSED = "closed"
_TERMINAL_STATUSES = frozenset({STATUS_COMPLETED, STATUS_CLOSED})

# ── Relationship type ──────────────────────────────────────────────────────
REL_CLIENT = "client"
REL_EMPLOYEE = "employee"
REL_UNKNOWN = "unknown"

_CLIENT_ROLES = frozenset({
    "client_escort_buyer", "escort_client", "vip_client", "repeat_client",
    "security_service_client", "internal_operation", "government_contact",
    "political_contact", "vendor",
})
_EMPLOYEE_ROLES = frozenset({
    "escort", "escort_worker", "security_guard", "employee", "supervisor",
    "employee_internal", "candidate",
})

# ── Tone ───────────────────────────────────────────────────────────────────
TONE_APNI = "apni_respectful"     # clients / customers / outsiders
TONE_TUMI = "tumi_manager"        # internal employees / juniors — manager voice

# ── Terminal / goodbye phrases (Bengali + transliterations) ─────────────────
_TERMINAL_PATTERNS = [
    r"খোদা\s*হাফেজ", r"আল্লাহ\s*হাফেজ", r"khoda\s*hafez", r"allah\s*hafez",
    r"ধন্যবাদ", r"thank(?:s| you)", r"ঠিক\s*আছে\s*ধন্যবাদ",
    r"আর\s*কিছু\s*(?:জান(?:ার|তে)|লাগবে)?\s*(?:নাই|না|নেই)",
    r"পরে\s*(?:যোগাযোগ|কথা)\s*(?:করব|হবে)", r"পরে\s*কল\s*(?:দিব|দিচ্ছি)",
    r"আজকে\s*এতটুকুই", r"বুঝ(?:েছি|লাম)\s*ধন্যবাদ", r"ok\s*(?:thanks|thank you)",
    r"got\s*it,?\s*thanks", r"no\s*(?:more\s*)?questions?",
]
_TERMINAL_RE = re.compile("|".join(_TERMINAL_PATTERNS), re.IGNORECASE)

# A terminal phrase next to an actual question / new fact is NOT terminal.
_QUESTION_RE = re.compile(r"[?？]|কি\b|কী\b|কেন\b|কীভাবে\b|কোথায়\b|কখন\b|koto|কত\b|"
                          r"koto\b|how much|when|where|why|kivabe", re.IGNORECASE)

# ── Topic detection (deterministic keyword classifier — shadow only) ────────
_TOPIC_KEYWORDS: list[tuple[str, str]] = [
    ("payment",  r"বেতন|salary|payment|পেমেন্ট|টাকা\s*(?:পাইনি|পাব|দেন|দিবেন)|"
                 r"বিকাশ|bkash|নগদ|nagad|/-|\badvance\b|অগ্রিম"),
    ("attendance", r"হাজিরা|attendance|উপস্থিত|absent|ছুটি|leave|present\b|"
                   r"ডিউটি\s*(?:করিনি|করেছি)"),
    ("escort_order", r"m\.?v\.?|mother\s*vessel|এমভি|জাহাজ|lighter|escort\s*lagbe|"
                     r"এসকর্ট\s*(?:\S+\s+){0,2}(?:লাগবে|দরকার|দেন|চাই)|"
                     r"এসকর্ট\s*সার্ভিস|vessel\s*escort|escort\s*service|"
                     r"(?:রেট|rate)\s*কত.*এসকর্ট|এসকর্ট.*(?:রেট|rate)\s*কত"),
    ("escort_assignment", r"duty\s*(?:kothay|koi|ache)|ডিউটি\s*কোথায়|আজ(?:কে)?\s*ডিউটি|"
                          r"কাল(?:কে)?\s*ডিউটি|roster|রোস্টার|assignment|শিপে\s*যাব"),
    ("recruitment", r"চাকরি|চাকুরী|job|নিয়োগ|recruit|বেতন\s*কত|apply|আবেদন|"
                    r"vacancy|পদ\s*(?:খালি|আছে)|interview|ইন্টারভিউ|কাজ\s*(?:পাব|আছে)"),
    ("technical", r"কীভাবে\s*কাজ\s*করে|how\s*does\s*.*work|explain|ব্যাখ্যা|"
                  r"মানে\s*কি|what\s*is\s*(?:a|an|the)\b|কি\s*জিনিস|difference\s*between"),
]
_TOPIC_RES = [(name, re.compile(pat, re.IGNORECASE)) for name, pat in _TOPIC_KEYWORDS]

_GENERAL_KNOWLEDGE_RE = re.compile(
    r"ইতিহাস|history|science|বিজ্ঞান|planet|গ্রহ|capital\s*of|রাজধানী|"
    r"who\s*(?:was|is)\b|কে\s*ছিলেন|কবে\s*(?:হয়|হয়েছিল)|python|programming|"
    r"computer|কম্পিউটার|ai\b|কৃত্রিম\s*বুদ্ধিমত্তা",
    re.IGNORECASE,
)


@dataclass
class ShadowTopicDecision:
    detected_topic: str
    relationship_type: str
    proposed_topic_status: str
    proposed_next_best_action: Optional[str]
    proposed_terminal: bool
    proposed_followup_question: Optional[str]
    is_repeated_question: bool
    proposed_tone: str
    proposed_general_ai_mode: bool
    reason: str
    confidence: float
    prior_topic: Optional[str] = None
    prior_topic_status: Optional[str] = None
    reopened_other_topic: bool = False

    def to_log(self) -> dict:
        return asdict(self)


# ── Public helpers ─────────────────────────────────────────────────────────

def relationship_for_role(role: Optional[str]) -> str:
    r = (role or "").strip()
    if r in _CLIENT_ROLES:
        return REL_CLIENT
    if r in _EMPLOYEE_ROLES:
        return REL_EMPLOYEE
    if r in ("admin", "accountant", "family", "family_ceo", "friend_relative",
             "internal_coordinator"):
        # trusted internal / personal — manager/peer voice, not client deference
        return REL_EMPLOYEE
    return REL_UNKNOWN


def tone_for_relationship(rel: str) -> str:
    return TONE_TUMI if rel == REL_EMPLOYEE else TONE_APNI


def is_terminal_message(text: str) -> bool:
    """A goodbye / thanks / no-more-questions with no fresh question or fact."""
    t = (text or "").strip()
    if not t:
        return False
    if not _TERMINAL_RE.search(t):
        return False
    # If they also ask something concrete, it isn't terminal.
    # Strip the matched terminal phrase before checking for a question.
    stripped = _TERMINAL_RE.sub(" ", t)
    if _QUESTION_RE.search(stripped) and len(stripped.strip()) > 12:
        return False
    return True


def detect_topic(text: str) -> tuple[str, float]:
    t = (text or "").strip()
    if not t:
        return "general", 0.3
    for name, rx in _TOPIC_RES:
        if rx.search(t):
            return name, 0.75
    if _GENERAL_KNOWLEDGE_RE.search(t):
        return "general_knowledge", 0.6
    return "general", 0.4


def _question_signature(text: str) -> Optional[str]:
    """A coarse semantic signature for a question, used to detect that the
    same question is being asked again. None if the text isn't a question."""
    t = (text or "").lower()
    if not _QUESTION_RE.search(t):
        return None
    if re.search(r"বেতন\s*কত|salary|কত\s*টাকা|koto taka|how much", t):
        return "q:pay_amount"
    if re.search(r"ঠিকানা|address|অফিস\s*কোথায়|office\s*(?:location|address)|কোথায়\s*আসব", t):
        return "q:office_address"
    if re.search(r"ডিউটি\s*কোথায়|duty\s*(?:kothay|where)|কোন\s*শিপ|which\s*(?:ship|vessel)", t):
        return "q:duty_location"
    if re.search(r"কি\s*কি\s*(?:কাগজ|ডকুমেন্ট)|documents?\s*(?:lagbe|required|needed)|"
                 r"কাগজপত্র", t):
        return "q:documents"
    if re.search(r"কখন|when|কবে|koto tarikh|what\s*time", t):
        return "q:timing"
    return "q:other"


# ── Main shadow decision ───────────────────────────────────────────────────

def compute_shadow_topic_decision(
    *,
    text: str,
    role: Optional[str],
    active_topic: Optional[str] = None,
    topic_status: Optional[str] = None,
    information_given: Optional[dict] = None,
    unresolved_questions: Optional[list] = None,
) -> ShadowTopicDecision:
    """Pure, deterministic. Never raises. Reads current topic state, returns
    what the topic-managed policy WOULD do this turn."""
    information_given = information_given or {}
    unresolved_questions = list(unresolved_questions or [])

    rel = relationship_for_role(role)
    tone = tone_for_relationship(rel)
    topic, topic_conf = detect_topic(text)
    terminal = is_terminal_message(text)
    general_ai = topic in ("general_knowledge", "technical") or (
        topic == "general" and bool(_GENERAL_KNOWLEDGE_RE.search(text or ""))
    )

    prior_topic = active_topic
    prior_status = (topic_status or "").strip().lower() or None
    reopened_other = False

    # ── Terminal handling — one closing, no follow-up, no restart ───────────
    if terminal:
        return ShadowTopicDecision(
            detected_topic=active_topic or topic,
            relationship_type=rel,
            proposed_topic_status=STATUS_CLOSED,
            proposed_next_best_action="send_one_brief_closing",
            proposed_terminal=True,
            proposed_followup_question=None,
            is_repeated_question=False,
            proposed_tone=tone,
            proposed_general_ai_mode=False,
            reason="terminal_phrase_detected: close topic, no new question, no dump, "
                   "no recruitment restart",
            confidence=0.8,
            prior_topic=prior_topic,
            prior_topic_status=prior_status,
        )

    # ── Multi-topic: a new subject does NOT reopen a completed/closed one ──
    same_topic = (active_topic is not None) and (
        topic == active_topic
        or (topic in ("escort_order", "escort_assignment") and active_topic == "escort")
    )
    if active_topic and prior_status in _TERMINAL_STATUSES and not same_topic:
        # the prior topic is done/closed → this is a brand-new topic; the old
        # one stays closed (never reopened just because the same person wrote
        # again about something else)
        effective_topic = topic
        working_status = STATUS_OPEN
        reason_bits = [f"new_topic={topic!r} distinct from prior {active_topic!r} "
                       f"({prior_status}); prior stays closed"]
    elif active_topic and prior_status not in _TERMINAL_STATUSES:
        # a topic is still open/waiting → a mid-topic message continues it
        # (deterministic keyword detection is a weak signal mid-conversation;
        # do not drift off an open topic on it)
        effective_topic = active_topic
        working_status = prior_status or STATUS_OPEN
        reason_bits = [f"continuing open topic {effective_topic!r} "
                       f"(status={working_status}, detected={topic!r})"]
    else:
        effective_topic = topic
        working_status = prior_status or STATUS_OPEN
        reason_bits = [f"topic {effective_topic!r}"]

    # ── Repeated question detection ────────────────────────────────────────
    sig = _question_signature(text)
    is_repeat = bool(sig and information_given.get(sig))
    # A repeat is suppressed if the user is clearly asking for clarification
    # or adding new context (handled by the caller's clarification check — here
    # we only flag the raw repeat).
    if is_repeat:
        return ShadowTopicDecision(
            detected_topic=effective_topic,
            relationship_type=rel,
            proposed_topic_status=working_status if working_status in _TERMINAL_STATUSES else STATUS_OPEN,
            proposed_next_best_action="gentle_reminder_reference_previous_answer",
            proposed_terminal=False,
            proposed_followup_question=None,
            is_repeated_question=True,
            proposed_tone=tone,
            proposed_general_ai_mode=False,
            reason="; ".join(reason_bits + [f"question {sig!r} already answered "
                                            f"(information_given) — remind, don't repeat"]),
            confidence=0.7,
            prior_topic=prior_topic,
            prior_topic_status=prior_status,
            reopened_other_topic=reopened_other,
        )

    # ── General-purpose AI mode (non-business question) ────────────────────
    if general_ai:
        return ShadowTopicDecision(
            detected_topic="technical" if topic == "technical" else "general_knowledge",
            relationship_type=rel,
            proposed_topic_status=STATUS_OPEN,
            proposed_next_best_action="answer_as_general_assistant_plain_bengali",
            proposed_terminal=False,
            proposed_followup_question=None,
            is_repeated_question=False,
            proposed_tone=tone,
            proposed_general_ai_mode=True,
            reason="; ".join(reason_bits + ["non-business/general-knowledge question — "
                                           "answer helpfully in plain Bengali, no forced "
                                           "business follow-up"]),
            confidence=0.6,
            prior_topic=prior_topic,
            prior_topic_status=prior_status,
        )

    # ── Follow-up gating: only when a genuine unfinished next step exists ──
    followup: Optional[str] = None
    next_action: Optional[str] = "answer_question"
    proposed_status = STATUS_OPEN

    if effective_topic == "recruitment":
        # deterministic recruitment progression
        stages = ["role_interest", "duties", "salary_conditions", "suitability",
                  "documents", "office_location", "invitation"]
        done = [s for s in stages if information_given.get(f"recruit:{s}")]
        remaining = [s for s in stages if s not in done]
        if remaining:
            nxt = remaining[0]
            next_action = f"recruitment_advance:{nxt}"
            followup = {
                "role_interest": "কোন পদের জন্য আগ্রহী জানতে চাই।",
                "duties": "কাজটা কী কী করতে হবে সেটা বলে দিচ্ছি — জানতে চান?",
                "salary_conditions": "বেতন ও শর্তগুলো বলি?",
                "suitability": "আপনার আগের অভিজ্ঞতা একটু বলবেন?",
                "documents": "কী কী কাগজপত্র লাগবে বলে দিই?",
                "office_location": None,  # give address, don't ask
                "invitation": None,
            }.get(nxt)
            if nxt in ("office_location", "invitation"):
                next_action = f"recruitment_advance:{nxt}"
                followup = None
                if nxt == "invitation":
                    proposed_status = STATUS_WAITING
                    next_action = "schedule_office_visit_2_3_days_office_hours"
        else:
            proposed_status = STATUS_COMPLETED
            next_action = "recruitment_topic_complete_stop"
            followup = None
    elif working_status == STATUS_WAITING:
        next_action = "await_user_no_new_question"
        proposed_status = STATUS_WAITING
    else:
        # generic business answer — answer first; ask a follow-up ONLY if there
        # is a real unresolved question on file
        if unresolved_questions:
            followup = None  # caller supplies the real one; we just flag it
            next_action = f"resolve_open_item:{unresolved_questions[0]}"
        else:
            next_action = "answer_and_stop"

    return ShadowTopicDecision(
        detected_topic=effective_topic,
        relationship_type=rel,
        proposed_topic_status=proposed_status,
        proposed_next_best_action=next_action,
        proposed_terminal=False,
        proposed_followup_question=followup,
        is_repeated_question=False,
        proposed_tone=tone,
        proposed_general_ai_mode=False,
        reason="; ".join(reason_bits + [f"conf={topic_conf:.2f}"]),
        confidence=round(0.5 + 0.3 * topic_conf, 2),
        prior_topic=prior_topic,
        prior_topic_status=prior_status,
        reopened_other_topic=reopened_other,
    )


def log_shadow_decision(
    *,
    bridge: str,
    sender: str,
    message_id: Optional[str],
    decision: ShadowTopicDecision,
    live_action_summary: Optional[str] = None,
) -> None:
    """Structured, greppable shadow log line — no DB write, no migration.
    Review with:  grep '\\[CONV_POLICY_SHADOW\\]' logs/fazle-core.log"""
    payload = {
        "bridge": bridge, "sender": sender[-4:].rjust(len(sender), "*") if sender else None,
        "message_id": message_id,
        "decision": decision.to_log(),
        "live_action_summary": live_action_summary,
    }
    try:
        log.info("[CONV_POLICY_SHADOW] %s", json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:  # noqa: BLE001
        log.info("[CONV_POLICY_SHADOW] %r", payload)
