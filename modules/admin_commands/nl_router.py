"""
Fazle Core — Admin Natural-Language Query Router (Phase 1.1, v1.1.0)

Lightweight intent router for admin chat queries. NO LLM.
Bangla + English + Banglish via regex + keyword maps.

Phase 1.1 ships TWO intents only (validation slice):
  - chat_history  : "show last 10 chats of <phone>" / "<phone> এর শেষ ১০ চ্যাট"
  - last_contact  : "last contact of <phone>" / "<phone> এর সর্বশেষ যোগাযোগ"

Conventions:
  - Public entry: is_nl_admin_query(text), process_nl_admin_query(text, admin_phone)
  - Reads only from wbom_whatsapp_messages (canonical inbound store).
  - Reply size capped at MAX_INLINE_CHARS; overflow written to reports/.
  - Bengali digits normalised before regex.

Wired into modules.message_router admin branch AFTER is_admin_command.
Owner directive (2026-04-27): admin numbers only, no LLM, low RAM, safe.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Optional

from app.database import fetch_all

log = logging.getLogger("fazle.admin_nl")

# ── Constants ─────────────────────────────────────────────────────────────────
MAX_INLINE_CHARS = 3500
REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "reports")

_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

# ── Phone extraction (BD numbers) ─────────────────────────────────────────────
# Accepts: 01XXXXXXXXX, 8801XXXXXXXXX, +8801XXXXXXXXX. Returns canonical 8801XXXXXXXXX.
_PHONE_RE = re.compile(r"(?:\+?88)?(01[3-9]\d{8})")


def extract_phone(text: str) -> Optional[str]:
    """Find first valid BD phone in text. Returns 8801XXXXXXXXX canonical form."""
    from modules.phone_normalizer import normalize_phone
    t = text.translate(_BN_DIGITS)
    m = _PHONE_RE.search(t)
    if not m:
        return None
    return normalize_phone("88" + m.group(1))


def _phone_variants(canonical: str) -> list[str]:
    """Return SQL LIKE patterns covering common stored formats."""
    # canonical = 8801XXXXXXXXX
    local = canonical[2:] if canonical.startswith("88") else canonical
    return [canonical, local, f"+{canonical}"]


# ── Count parser ──────────────────────────────────────────────────────────────
_COUNT_RE = re.compile(r"\b(?:last|recent|শেষ|গত)\s+(\d{1,3})\b", re.IGNORECASE)


def parse_count(text: str, default: int = 10, cap: int = 200) -> int:
    t = text.translate(_BN_DIGITS)
    m = _COUNT_RE.search(t)
    if not m:
        return default
    n = int(m.group(1))
    return max(1, min(cap, n))


# ── Intent patterns ───────────────────────────────────────────────────────────
# chat_history: keywords + a phone present
_CHAT_KEYWORDS = re.compile(
    r"\b(chat|chats|history|messages?|conversation|মেসেজ|মেসেজগুলো|চ্যাট|হিস্টরি|কথা|যোগাযোগের ইতিহাস)\b",
    re.IGNORECASE,
)
_LAST_CONTACT_KEYWORDS = re.compile(
    r"\b(last contact|last seen|last message|last chat|সর্বশেষ যোগাযোগ|শেষ মেসেজ|শেষ কথা|কখন কথা)\b",
    re.IGNORECASE,
)


def classify_nl_intent(text: str) -> Optional[str]:
    """Return intent name or None. Phone presence is required for both intents."""
    t = text.strip()
    if not extract_phone(t):
        return None
    if _LAST_CONTACT_KEYWORDS.search(t):
        return "last_contact"
    if _CHAT_KEYWORDS.search(t):
        return "chat_history"
    return None


def is_nl_admin_query(text: str) -> bool:
    return classify_nl_intent(text) is not None


# ── Handlers ──────────────────────────────────────────────────────────────────
async def _intent_chat_history(text: str, phone: str) -> str:
    n = parse_count(text, default=10, cap=200)
    variants = _phone_variants(phone)
    rows = await fetch_all(
        """
        SELECT received_at, direction, COALESCE(message_body, '') AS body
        FROM wbom_whatsapp_messages
        WHERE sender_number = ANY($1::text[])
           OR contact_identifier = ANY($1::text[])
        ORDER BY received_at DESC
        LIMIT $2
        """,
        variants, n,
    )
    if not rows:
        return f"❌ {phone} এর কোনো মেসেজ পাওয়া যায়নি।"

    rows = list(reversed(rows))  # oldest → newest for readability
    lines = [f"📜 {phone} — শেষ {len(rows)}টি মেসেজ:\n"]
    for r in rows:
        ts = r["received_at"].strftime("%d %b %H:%M")
        arrow = "→" if r["direction"] == "outbound" else "←"
        body = (r["body"] or "").replace("\n", " ").strip()
        if len(body) > 200:
            body = body[:197] + "..."
        lines.append(f"{ts} {arrow} {body}")
    return "\n".join(lines)


async def _intent_last_contact(_text: str, phone: str) -> str:
    variants = _phone_variants(phone)
    rows = await fetch_all(
        """
        SELECT received_at, direction, COALESCE(message_body, '') AS body
        FROM wbom_whatsapp_messages
        WHERE sender_number = ANY($1::text[])
           OR contact_identifier = ANY($1::text[])
        ORDER BY received_at DESC
        LIMIT 1
        """,
        variants,
    )
    if not rows:
        return f"❌ {phone} এর সাথে কোনো যোগাযোগের রেকর্ড নেই।"
    r = rows[0]
    ts = r["received_at"]
    age = datetime.now(ts.tzinfo) - ts
    days = age.days
    hours = age.seconds // 3600
    when = f"{days}d {hours}h আগে" if days else f"{hours}h আগে" if hours else "এইমাত্র"
    arrow = "→ পাঠানো" if r["direction"] == "outbound" else "← এসেছে"
    body = (r["body"] or "").replace("\n", " ").strip()
    if len(body) > 300:
        body = body[:297] + "..."
    return (
        f"📞 {phone} — শেষ যোগাযোগ:\n"
        f"{ts.strftime('%Y-%m-%d %H:%M')}  ({when})\n"
        f"{arrow}: {body}"
    )


_HANDLERS = {
    "chat_history": _intent_chat_history,
    "last_contact": _intent_last_contact,
}


# ── Output overflow → file ────────────────────────────────────────────────────
def _maybe_spill_to_file(intent: str, phone: str, body: str) -> str:
    if len(body) <= MAX_INLINE_CHARS:
        return body
    try:
        os.makedirs(REPORTS_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_phone = re.sub(r"\D", "", phone)
        fname = f"{ts}_{intent}_{safe_phone}.txt"
        fpath = os.path.join(REPORTS_DIR, fname)
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(body)
        head = body[:800].rstrip()
        return (
            f"{head}\n…\n\n"
            f"📄 ফলাফল বড় — সম্পূর্ণ ফাইল:\n"
            f"reports/{fname}\n"
            f"({len(body)} chars)"
        )
    except Exception as e:
        log.warning(f"spill-to-file failed: {e}")
        return body[:MAX_INLINE_CHARS] + "\n…(truncated)"


# ── Public entry ──────────────────────────────────────────────────────────────
async def process_nl_admin_query(text: str, admin_phone: str) -> str:
    intent = classify_nl_intent(text)
    if not intent:
        return ""
    phone = extract_phone(text)
    if not phone:
        return "❌ ফোন নম্বর পাইনি। উদাহরণ: show last 10 chats of 01836743754"

    try:
        from modules import observability as _obs
        _obs.inc("admin_nl_query_total", labels={"intent": intent})
    except Exception:
        pass

    log.info(f"[admin_nl] admin={admin_phone} intent={intent} phone={phone}")
    try:
        body = await _HANDLERS[intent](text, phone)
    except Exception as e:
        log.exception(f"[admin_nl] handler {intent} failed: {e}")
        return f"⚠️ ক্যোয়েরি ব্যর্থ: {e}"

    return _maybe_spill_to_file(intent, phone, body)
