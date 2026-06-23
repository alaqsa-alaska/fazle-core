"""Restricted recruitment AI reply brain."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from app import llm as ai

log = logging.getLogger("fazle.recruitment_ai")

_RECRUITMENT_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "resources"
    / "ops"
    / "recruitment_source_of_truth.txt"
)
_SAFE_FALLBACK = "এই বিষয়ে নিশ্চিত তথ্যের জন্য অফিসে যোগাযোগ করুন।"
_OFFICE_REPLY = (
    "অফিস ঠিকানা: আগ্রপাড়া, Victoria Gate #1, Khokoner Building, AK Khan Mor, Chittagong। "
    "অফিস সময় সকাল ১০টা থেকে বিকাল ৫টা। আসার আগে 01958-122322 নম্বরে যোগাযোগ করুন।"
)
_CONTACT_REPLY = "সর্বশেষ recruitment WhatsApp/যোগাযোগ নম্বর: 01958-122322।"
_AGE_REPLY = "সাধারণ বয়সসীমা ১৮–৫৫ বছর।"
_FEE_PHRASES = (
    "ভর্তি ফি", "জয়েনিং ফি", "জয়েনিং ফি", "ট্রেনিং ফি", "প্রসেসিং ফি",
    "আবেদন ফি", "ফর্ম ফি", "joining fee", "training fee", "processing fee",
    "application fee", "form fee", "ডিপোজিট", "deposit", "টাকা লাগবে", "টাকা লাগে",
)

_QUESTION_HINTS = (
    "who are you",
    "who r u",
    "আপনি কে",
    "তুমি কে",
    "কেন",
    "why",
    "am i asked for job",
    "asked for job",
    "lok lagbe",
    "লোক লাগবে",
    "কাজ আছে",
    "job ache",
)


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[\s,.;:!?।()\[\]{}<>/\\|\"'`~\-]+", text.lower()) if len(t) >= 2}


def _looks_like_fee_question(text: str) -> bool:
    lower = (text or "").lower()
    if any(phrase in lower for phrase in _FEE_PHRASES):
        return True
    return bool(re.search(r"(?:^|[\s,.;:!?।])(?:ফি|fee)(?:$|[\s,.;:!?।])", lower))


def _looks_like_contact_question(text: str) -> bool:
    lower = (text or "").lower()
    return any(phrase in lower for phrase in (
        "যোগাযোগ নম্বর", "কন্টাক্ট নম্বর", "contact number", "whatsapp number",
        "ফোন নম্বর", "office number", "অফিস নম্বর",
    ))


def _deterministic_fact_reply(text: str) -> str:
    lower = (text or "").lower()
    if _looks_like_contact_question(lower):
        return _CONTACT_REPLY
    if any(p in lower for p in ("office location", "office address", "অফিস কোথায়", "অফিস কোথায়", "অফিসের ঠিকানা")):
        return _OFFICE_REPLY
    if any(p in lower for p in ("বয়সসীমা", "বয়সসীমা", "age limit")):
        return _AGE_REPLY
    return ""


def looks_like_recruitment_followup(text: str) -> bool:
    lower = (text or "").strip().lower()
    if not lower:
        return False
    return any(hint in lower for hint in _QUESTION_HINTS)


def build_recruitment_source_context(message: str = "") -> str:
    """Load relevant sections from the single approved recruitment source."""
    try:
        full_context = _RECRUITMENT_SOURCE.read_text(encoding="utf-8").strip()
        message_lower = (message or "").lower()
        if not _looks_like_fee_question(message_lower):
            full_context = re.sub(
                r"\n## ভর্তি ফি / ডিপোজিট প্রশ্নের বাধ্যতামূলক উত্তরনীতি\n.*?(?=\n## |\Z)",
                "",
                full_context,
                flags=re.S,
            )

        preamble, *raw_sections = re.split(r"(?m)^## ", full_context)
        sections: list[tuple[str, str]] = []
        for raw in raw_sections:
            title, _, body = raw.partition("\n")
            sections.append((title.strip(), body.strip()))

        query_tokens = _tokens(message_lower)
        scored: list[tuple[int, int, str, str]] = []
        for idx, (title, body) in enumerate(sections):
            section_tokens = _tokens(f"{title} {body}")
            overlap = len(query_tokens & section_tokens)
            title_overlap = len(query_tokens & _tokens(title))
            score = overlap + (title_overlap * 4)
            if title == "উত্তর দেওয়ার সীমা":
                score += 100
            if _looks_like_fee_question(message_lower) and "ফি" in title:
                score += 100
            if _looks_like_contact_question(message_lower) and title == "অফিস তথ্য":
                score += 100
            scored.append((score, -idx, title, body))

        selected = sorted(scored, reverse=True)[:4]
        if not query_tokens:
            selected = [
                row for row in scored
                if row[2] in {
                    "উত্তর দেওয়ার সীমা",
                    "বর্তমানে নিয়োগ চলমান পদ",
                    "সাধারণ যোগ্যতা ও সুবিধা",
                    "আবেদন ও যোগদান",
                }
            ]

        parts = [preamble.strip()]
        for _score, _idx, title, body in sorted(selected, key=lambda row: -row[1]):
            parts.append(f"## {title}\n{body}")
        return "\n\n".join(part for part in parts if part).strip()[:4500]
    except OSError as exc:
        log.error("[recruit_ai] approved source unavailable: %s", exc)
        return ""


def clean_recruitment_reply(reply: str) -> str:
    """Keep WhatsApp output short and remove common model artifacts."""
    text = (reply or "").strip()
    for marker in (
        "Reply only the WhatsApp message text:",
        "WhatsApp message:",
        "উত্তর:",
        "Reply:",
    ):
        if text.lower().startswith(marker.lower()):
            text = text[len(marker):].strip()
    text = text.replace("```", "").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) > 5:
        lines = lines[:5]
    text = "\n".join(lines)
    if len(text) > 360:
        text = text[:357].rstrip() + "..."
    return text


def enforce_recruitment_reply_policy(message: str, reply: str, source_context: str = "") -> str:
    """Fail closed when a generated reply introduces facts absent from the source."""
    cleaned = clean_recruitment_reply(reply)
    if not cleaned:
        return _SAFE_FALLBACK

    number_re = re.compile(r"[0-9০-৯][0-9০-৯,.\-–+]*")
    source_numbers = set(number_re.findall(source_context))
    reply_numbers = set(number_re.findall(cleaned))
    if not reply_numbers.issubset(source_numbers):
        log.warning(
            "[recruit_ai] blocked unsupported numeric facts: %s",
            sorted(reply_numbers - source_numbers),
        )
        return _SAFE_FALLBACK

    # Place-specific hiring claims are especially risky: a model can copy a
    # location from the user's operational message and invent a vacancy there.
    # [\u0980-\u09ff]{0,3} absorbs Bangla case-markers (র, ে, য়, তে…) so
    # inflected forms like "চরপাড়ার" and "ঘাটে" are matched, not just bare stems.
    place_patterns = re.findall(
        r"[\w\u0980-\u09ff-]{2,}(?:পাড়া|ঘাট|এলাকা|জেলা|বন্দর)[\u0980-\u09ff]{0,3}",
        cleaned,
    )
    # Strip common inflection suffixes before checking against the source text
    # so "আগ্রপাড়ায়" in a reply still matches "আগ্রপাড়া" in the source.
    _INFLECTION_SUFFIXES = ("য়ে", "য়", "তে", "র", "ে", "এ")

    def _place_stem(token: str) -> str:
        for sfx in _INFLECTION_SUFFIXES:
            if token.endswith(sfx):
                return token[: -len(sfx)]
        return token

    unsupported_places = [
        p for p in place_patterns
        if _place_stem(p).lower() not in source_context.lower()
    ]
    if unsupported_places:
        log.warning("[recruit_ai] blocked unsupported location facts: %s", unsupported_places)
        return _SAFE_FALLBACK
    return cleaned

async def _safe_rag_chunks(text: str, k: int = 5) -> tuple[str, list[str]]:
    """Return (formatted_chunks, source_list) for prompt enrichment. Never raises."""
    try:
        from modules.rag import search as rag_search
        chunks = await rag_search(text, k=k, role="candidate")
        if not chunks:
            return "", []
        parts: list[str] = []
        sources: list[str] = []
        for i, chunk in enumerate(chunks, 1):
            title = chunk.get("title") or chunk.get("source") or f"chunk_{i}"
            body = (chunk.get("text") or "").strip()
            if body:
                parts.append(f"[{i}] {title}: {body[:400]}")
                sources.append(chunk.get("source", f"chunk_{i}"))
        return "\n".join(parts), sources
    except Exception as exc:
        log.debug("[recruit_ai] rag enrichment skipped: %s", exc)
        return "", []


async def generate_recruitment_reply(
    *,
    phone: str,
    text: str,
    source: str,
    contact_context: str = "",
    history: str = "",
) -> Optional[str]:
    del contact_context
    deterministic = _deterministic_fact_reply(text)
    if deterministic:
        return deterministic
    kb_context = build_recruitment_source_context(text)
    if not kb_context:
        return _SAFE_FALLBACK
    # Phase 4 — Hybrid RAG enrichment + source tracing
    rag_chunks, rag_sources = await _safe_rag_chunks(text)
    if rag_chunks:
        kb_context = f"{kb_context}\n\n## Related Knowledge Base\n{rag_chunks}"
        log.info("[recruit_ai] rag_sources=%s phone=%s", rag_sources, phone)
    reply = await ai.generate_recruitment_reply(
        user_message=text,
        kb_context=kb_context,
        history=history,
        contact_context="",
        source=source,
    )
    reply = enforce_recruitment_reply_policy(text, reply, kb_context)
    log.info(
        "[recruit_ai] reply phone=%s source=%s chars=%d rag_sources=%d",
        phone, source, len(reply), len(rag_sources),
    )
    return reply
