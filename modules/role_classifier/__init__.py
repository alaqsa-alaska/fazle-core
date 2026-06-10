"""
Per-contact role context loader.

Reads user_profiles and user_memory tables to build a context dict
that llm.py / message_router can inject into system prompts.
"""
import logging
from typing import Optional

from app.database import fetch_one, fetch_all

log = logging.getLogger("fazle.role_classifier")

ROLE_PRIORITY: dict[str, int] = {
    "vip_client":     10,
    "manager":         9,
    "supervisor":      8,
    "client":          7,
    "employee":        6,
    "accountant":      6,
    "escort":          5,
    "security_guard":  5,
    "buyer":           4,
    "seller":          4,
    "vendor":          3,
    "candidate":       3,
    "family":          2,
    "friend":          2,
    "unknown":         1,
}

_ROLE_PROMPTS: dict[str, str] = {
    "vip_client":     "এই ব্যক্তি একজন VIP ক্লায়েন্ট। অত্যন্ত সম্মানজনক ও সহায়ক ভাষায় কথা বলো।",
    "client":         "এই ব্যক্তি একজন ক্লায়েন্ট। পেশাদার ও সহায়ক ভাষায় কথা বলো।",
    "employee":       "এই ব্যক্তি একজন কর্মচারী। অফিসিয়াল ও সহায়ক ভাষায় কথা বলো।",
    "candidate":      "এই ব্যক্তি একজন চাকরিপ্রার্থী। উৎসাহব্যঞ্জক ও তথ্যপূর্ণ ভাষায় কথা বলো।",
    "manager":        "এই ব্যক্তি একজন ম্যানেজার। সংক্ষিপ্ত ও সরাসরি উত্তর দাও।",
    "vendor":         "এই ব্যক্তি একজন ভেন্ডর। ব্যবসায়িক ভাষায় কথা বলো।",
    "family":         "এই ব্যক্তি পরিবারের সদস্য। ব্যক্তিগত ও উষ্ণ ভাষায় কথা বলো।",
    "unknown":        "এই ব্যক্তি অপরিচিত। সংক্ষিপ্ত পরিচয় নিয়ে সহায়তার প্রস্তাব দাও।",
}
_DEFAULT_PROMPT = _ROLE_PROMPTS["unknown"]


async def get_user_context(phone_canonical: str) -> dict:
    """
    Load full user context by canonical phone number.

    Returns a dict with keys: exists, phone, role, name, relationship_type,
    notes, memories, system_prompt_addition.
    """
    profile = await fetch_one(
        "SELECT * FROM user_profiles WHERE phone_canonical = $1",
        phone_canonical,
    )

    if not profile:
        return {
            "exists": False,
            "phone": phone_canonical,
            "role": "unknown",
            "name": None,
            "memories": [],
            "system_prompt_addition": _DEFAULT_PROMPT,
        }

    memories = await fetch_all(
        """SELECT memory_type, content
           FROM user_memory
           WHERE phone_canonical = $1
           ORDER BY created_at DESC
           LIMIT 10""",
        phone_canonical,
    )

    role: str = profile.get("role") or "unknown"
    return {
        "exists": True,
        "phone": phone_canonical,
        "role": role,
        "name": profile.get("name"),
        "relationship_type": profile.get("relationship_type"),
        "notes": profile.get("notes"),
        "memories": [
            {"type": m["memory_type"], "content": m["content"]}
            for m in memories
        ],
        "system_prompt_addition": _ROLE_PROMPTS.get(role, _DEFAULT_PROMPT),
    }


def build_context_for_llm(user_context: dict, kb_context: str = "") -> str:
    """
    Merge user_context dict and KB context into a single string for LLM injection.
    """
    parts: list[str] = []

    if user_context.get("name"):
        parts.append(f"ব্যক্তির নাম: {user_context['name']}")

    parts.append(f"Role: {user_context.get('role', 'unknown')}")

    if user_context.get("relationship_type"):
        parts.append(f"সম্পর্ক: {user_context['relationship_type']}")

    if user_context.get("notes"):
        parts.append(f"নোট: {user_context['notes']}")

    memories: list[dict] = user_context.get("memories", [])
    if memories:
        lines = [f"- [{m['type']}] {m['content']}" for m in memories[:5]]
        parts.append("পূর্বের তথ্য:\n" + "\n".join(lines))

    if kb_context:
        parts.append(f"Knowledge Base:\n{kb_context}")

    return "\n\n".join(parts)
