"""
Thread grouping / interleaving protection (Phase B Stage 2, Part 5) +
existing-draft awareness (Part 7).

Pure-ish helpers used by thread_reconstruction._observe_stage2(). Kept
separate from __init__.py so the grouping heuristic -- the part most
likely to need tuning once real conversation data is observed -- isn't
tangled up with the DB-facing merge/persistence primitives.

Grouping problem (Part 5): a client phone can have more than one escort
order in flight (Case B: two different lighters/master mobiles sent close
together) -- naively merging every message from a phone into one thread
would silently combine two unrelated orders. Conversely, a genuinely
split single order (Case A) must merge. The discriminator used here is
whether the incoming message's *identity-defining* fields (mother_vessel,
lighter_vessel, master_mobile) agree or disagree with a candidate
thread's already-known values for those same fields -- not just whether
the phone number matches.
"""
from __future__ import annotations

from typing import Any, Optional

IDENTITY_FIELDS = ("mother_vessel", "lighter_vessel", "master_mobile")

# Grouping (Part 5) deliberately uses a NARROWER set than completeness
# (Part 6.4) does. Case A's own example is a mother-vessel value arriving
# in a *separate* message from the lighter/master-mobile message -- so
# mother_vessel disagreement is weak, single-field evidence and must not
# by itself fork a new candidate thread. lighter_vessel and master_mobile
# are the fields Section 5's Case B explicitly names as the signal for
# "this is a different order" -- they are the hard grouping anchors.
# mother_vessel conflicts are still caught, just at the merge_field level
# (within the thread grouping already picked), matching Part 4's ordinary
# "conflicting high-confidence values -> conflicted" rule instead of the
# thread-selection layer.
GROUPING_ANCHOR_FIELDS = ("lighter_vessel", "master_mobile")


def normalize_for_comparison(field_name: str, value: Any) -> Optional[str]:
    """Canonical comparable form for one field value -- Part 4's
    "normalized-equivalent values are not conflicts" rule. Phone numbers
    go through the same BD normalizer used everywhere else in this repo
    (modules.escort._normalize_mobile); every other field is
    whitespace-collapsed and uppercased so e.g. "Bushra Biva-1" and
    "bushra  biva-1" compare equal without being byte-identical."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if field_name == "master_mobile":
        from modules.escort import _normalize_mobile
        return _normalize_mobile(text) or " ".join(text.split()).upper()
    return " ".join(text.split()).upper()


def values_equivalent(field_name: str, a: Any, b: Any) -> bool:
    na, nb = normalize_for_comparison(field_name, a), normalize_for_comparison(field_name, b)
    return na is not None and na == nb


class GroupingDecision:
    """action: 'merge_existing' | 'new_thread' | 'needs_review'
    thread: the target thread dict when action == 'merge_existing', else None
    reason: short machine-readable code (Section 9 wants structured, not prose)"""

    def __init__(self, action: str, thread: Optional[dict], reason: str):
        self.action = action
        self.thread = thread
        self.reason = reason

    def __repr__(self) -> str:
        tid = self.thread["id"] if self.thread else None
        return f"GroupingDecision(action={self.action!r}, thread_id={tid}, reason={self.reason!r})"


def classify_threads_for_message(threads: list[dict], parsed_fields: dict) -> GroupingDecision:
    """Decide which (if any) of the candidate open threads this newly
    parsed single-message field set belongs to.

    parsed_fields: {field_name: value, ...} -- raw single-message parser
    output for THIS message only (not yet merged into anything).
    """
    if not threads:
        return GroupingDecision("new_thread", None, "no_open_thread_for_identity")

    incoming_identity = {
        f: parsed_fields[f] for f in GROUPING_ANCHOR_FIELDS
        if parsed_fields.get(f)
    }

    matches: list[dict] = []
    conflicts: list[dict] = []
    neutral: list[dict] = []

    for thread in threads:
        thread_fields = thread.get("fields") or {}
        has_match = False
        has_conflict = False
        for f, incoming_value in incoming_identity.items():
            existing_entry = thread_fields.get(f)
            if not existing_entry:
                continue  # thread doesn't have this identity field yet -- no signal either way
            existing_value = existing_entry.get("value")
            if values_equivalent(f, existing_value, incoming_value):
                has_match = True
            else:
                has_conflict = True
        if has_conflict:
            conflicts.append(thread)
        elif has_match:
            matches.append(thread)
        else:
            neutral.append(thread)

    # Case A generalized: exactly one thread agrees on every identity
    # field it already has an opinion about, and nothing disagrees.
    if len(matches) == 1 and not conflicts:
        return GroupingDecision("merge_existing", matches[0], "identity_fields_match_single_thread")

    if len(matches) > 1:
        # Same client, same identity values echoed across more than one
        # open thread -- shouldn't normally happen, but don't guess which
        # one is "the" thread.
        return GroupingDecision("needs_review", None, "multiple_threads_match_ambiguously")

    # No identity-field agreement anywhere, but also no disagreement, and
    # there's exactly one live thread -- the message likely supplements
    # that thread (e.g. a bare "Chattogram" reply) with no new identity
    # information to check it against. Safe to merge only when it's the
    # sole candidate.
    if not matches and not conflicts and len(neutral) == 1:
        return GroupingDecision("merge_existing", neutral[0], "single_open_thread_no_identity_conflict")

    if not matches and not conflicts and len(neutral) > 1:
        # Several live threads, message carries no identity fields to
        # disambiguate with -- Case C. Do not guess.
        return GroupingDecision("needs_review", None, "no_identity_fields_multiple_open_threads")

    if conflicts and not matches:
        # Case B: the message disagrees with every thread that has an
        # opinion on a shared identity field -- looks like a second,
        # different order from the same phone. Only safe to treat as a
        # fresh, self-sufficient candidate if it carries its own identity
        # fields; otherwise it's genuinely ambiguous (a fragment that
        # contradicts the existing thread but can't stand on its own).
        if incoming_identity:
            return GroupingDecision("new_thread", None, "conflicting_identity_fields_treated_as_new_order")
        return GroupingDecision("needs_review", None, "conflicting_but_no_identity_fields_of_its_own")

    # Mixed matches+conflicts against different threads simultaneously --
    # genuinely ambiguous.
    return GroupingDecision("needs_review", None, "mixed_match_and_conflict_across_threads")


async def check_existing_draft_relation(fields: dict) -> tuple[str, dict]:
    """Part 7: read-only relation of this (accumulating) thread to
    existing wbom_escort_programs drafts. Must never modify anything and
    deliberately does NOT reuse modules.escort._create_confirmed_from_
    admin()'s dedup/replacement logic (that logic is for admin program
    confirmation, not client-order-intake candidate detection -- a
    different decision with different consequences).

    Returns (relation, detail) where relation is one of:
    no_candidate | one_likely | several_ambiguous | likely_duplicate | likely_correction
    """
    from app.database import fetch_all

    master_mobile = (fields.get("master_mobile") or {}).get("value")
    lighter_vessel = (fields.get("lighter_vessel") or {}).get("value")
    mother_vessel = (fields.get("mother_vessel") or {}).get("value")

    norm_mobile = normalize_for_comparison("master_mobile", master_mobile) if master_mobile else None
    norm_lighter = normalize_for_comparison("lighter_vessel", lighter_vessel) if lighter_vessel else None
    norm_mother = normalize_for_comparison("mother_vessel", mother_vessel) if mother_vessel else None

    if not (norm_mobile or norm_lighter or norm_mother):
        return "no_candidate", {"reason": "no_identity_fields_yet"}

    rows = await fetch_all(
        """SELECT program_id, status, mother_vessel, lighter_vessel, master_mobile
           FROM wbom_escort_programs
           WHERE status IN ('draft', 'confirmed', 'running')
           ORDER BY program_id DESC LIMIT 200"""
    )

    scored = []
    for r in rows:
        row_mobile = normalize_for_comparison("master_mobile", r["master_mobile"]) if r["master_mobile"] else None
        row_lighter = normalize_for_comparison("lighter_vessel", r["lighter_vessel"]) if r["lighter_vessel"] else None
        row_mother = normalize_for_comparison("mother_vessel", r["mother_vessel"]) if r["mother_vessel"] else None

        score = 0
        matched_on = []
        if norm_mobile and row_mobile == norm_mobile:
            score += 2
            matched_on.append("master_mobile")
        if norm_lighter and row_lighter == norm_lighter:
            score += 1
            matched_on.append("lighter_vessel")
        if norm_mother and row_mother == norm_mother:
            score += 1
            matched_on.append("mother_vessel")
        if score > 0:
            scored.append({"program_id": r["program_id"], "status": r["status"], "score": score, "matched_on": matched_on})

    if not scored:
        return "no_candidate", {"checked": len(rows)}

    scored.sort(key=lambda s: s["score"], reverse=True)
    top = scored[0]
    strong_matches = [s for s in scored if s["score"] == top["score"]]

    if len(strong_matches) > 1:
        return "several_ambiguous", {"candidates": strong_matches}

    if "master_mobile" in top["matched_on"] and "lighter_vessel" in top["matched_on"] and "mother_vessel" in top["matched_on"]:
        return "likely_duplicate", {"candidate": top}

    if top["score"] >= 2:
        return "likely_correction", {"candidate": top}

    return "one_likely", {"candidate": top}
