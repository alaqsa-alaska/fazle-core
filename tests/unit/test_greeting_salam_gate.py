"""
Regression tests for the 2026-08-20 salam-leading fix, sharpened by a
2nd-pass Owner directive the same day.

Pass 1 (original fix): several reply-generation paths prepended a salam
greeting unconditionally whenever a message was classified
"greeting"/"family-personal", rather than gating it on whether the
sender's own message actually contained a salam pattern first. Real
regression case: draft 8801...1596 reply to "Uthlam ghum theke" (no salam
in the sender's message) came back salam-prefixed anyway.

Pass 2 (this file, same day): the Owner sharpened the rule further --
Hermes/Fazle Core customer-facing replies must NEVER open with
"ওয়ালাইকুম আস্সালাম"/"ওয়ালাইকুম সালাম"/"Wa Alaikum Salam" or equivalent,
EVEN WHEN the customer sent salam first. Pass 1's "echo salam back if
they said it first" behavior is therefore itself now wrong and is
replaced here with a neutral opener in all cases. `shared.greeting.
sender_used_salam()` itself is untouched (still a correct salam-detector,
just no longer used to pick an opener in the two callers below) --
kept as the single source of truth in case a future caller needs the raw
detection signal for something other than choosing a greeting opener.

This file also covers the previously-ungated hardcoded-salam paths found
in the same 2026-08-20 audit: modules.reply_templates (recruitment/
greeting templates), modules.social_auto_reply.reply_rules (WELCOME_REPLY/
LOCATION_REPLY), and modules.knowledge_base's keyword-fallback table --
none of these three were touched by Pass 1's fix or its original tests.
"""
from shared.greeting import sender_used_salam


class TestSenderUsedSalam:
    def test_salam_present_bangla(self):
        assert sender_used_salam("আসসালামু আলাইকুম, চাকরির খবর কি?") is True

    def test_salam_present_english(self):
        assert sender_used_salam("Assalamualaikum, how are you?") is True

    def test_salam_absent_regression_case(self):
        """The actual real-world regression message."""
        assert sender_used_salam("Uthlam ghum theke") is False

    def test_hello_alone_is_not_salam(self):
        assert sender_used_salam("hello") is False
        assert sender_used_salam("hi") is False

    def test_empty_text_is_not_salam(self):
        assert sender_used_salam("") is False
        assert sender_used_salam(None) is False

    def test_case_insensitive(self):
        assert sender_used_salam("ASSALAMU ALAIKUM") is True


class TestFamilyDeflectionReply:
    def test_no_salam_in_sender_message_does_not_force_salam(self):
        from modules.message_router import _family_deflection_reply
        reply = _family_deflection_reply("আপনি", "Uthlam ghum theke")
        assert not reply.startswith("আস-সালামু আলাইকুম")
        assert "হ্যালো" in reply

    def test_salam_in_sender_message_still_gets_no_salam(self):
        """Pass 2 flip: previously this asserted salam WAS echoed back.
        The rule is now stricter -- never open with salam, even when the
        sender said it first."""
        from modules.message_router import _family_deflection_reply
        reply = _family_deflection_reply("আপনি", "আসসালামু আলাইকুম বাবা")
        assert not reply.startswith("আস-সালামু আলাইকুম")
        assert not reply.startswith("আসসালামু আলাইকুম")
        assert "হ্যালো" in reply

    def test_english_transliterated_salam_still_no_salam(self):
        from modules.message_router import _family_deflection_reply
        reply = _family_deflection_reply("আপনি", "Assalamualaikum bhai")
        assert "আস-সালামু" not in reply
        assert "আসসালামু" not in reply
        assert "ওয়ালাইকুম" not in reply

    def test_core_deflection_text_always_present(self):
        from modules.message_router import _family_deflection_reply
        reply = _family_deflection_reply("আপনি", "Uthlam ghum theke")
        assert "এটা অফিসের নম্বর" in reply


class TestRecruitmentGreetingReply:
    def test_bare_hello_intent_does_not_force_salam(self):
        from modules.social_auto_reply.intelligent_generator import _combined_rule_reply
        reply = _combined_rule_reply("meta_whatsapp", "hello", ["greeting"], [], None)
        assert not reply.startswith("আসসালামু আলাইকুম")
        assert reply.startswith("হ্যালো")

    def test_salam_intent_still_no_salam_opener(self):
        """Pass 2 flip: previously this asserted the reply DID start with
        salam. The rule is now stricter -- never, even if the sender said
        salam first."""
        from modules.social_auto_reply.intelligent_generator import _combined_rule_reply
        reply = _combined_rule_reply(
            "meta_whatsapp", "আসসালামু আলাইকুম", ["greeting"], [], None,
        )
        assert not reply.startswith("আসসালামু আলাইকুম")
        assert reply.startswith("হ্যালো")

    def test_english_wa_alaikum_salam_still_no_salam_opener(self):
        from modules.social_auto_reply.intelligent_generator import _combined_rule_reply
        reply = _combined_rule_reply(
            "meta_whatsapp", "wa alaikum salam", ["greeting"], [], None,
        )
        assert "আসসালামু" not in reply
        assert "ওয়ালাইকুম" not in reply

    def test_already_answered_greeting_not_repeated(self):
        """Section 7's 'continuing conversation -> don't repeat greeting'
        requirement -- already satisfied by the pre-existing `already`
        state check, confirmed still intact after both passes of this
        fix."""
        from modules.social_auto_reply.intelligent_generator import _combined_rule_reply
        reply = _combined_rule_reply(
            "meta_whatsapp", "আসসালামু আলাইকুম আবার", ["greeting"], [],
            {"answered_intents": ["greeting"]},
        )
        assert "আসসালামু আলাইকুম।" not in reply
        assert "হ্যালো।" not in reply


class TestReplyTemplatesNoUnconditionalSalam:
    """modules.reply_templates -- confirmed reachable only via the
    shadow-only conversation_layer.recruitment.simulate_current_core_reply(),
    but fixed anyway since it's an exported public API with its own tests."""

    def test_greeting_template_never_opens_with_salam(self):
        from modules.reply_templates import get_template, _GREETING_NORMAL
        seen = set()
        # get_template() rotates per (sender, intent) key -- use the SAME
        # sender across calls so successive calls actually advance through
        # every rotation variant instead of each restarting at index 0.
        for _ in range(len(_GREETING_NORMAL) * 2):
            reply = get_template("greeting", "same_sender_for_rotation", frustrated=False)
            assert reply is not None
            assert not reply.startswith("ওয়ালাইকুম")
            assert not reply.startswith("আস্সালামু")
            assert not reply.startswith("আসসালামু")
            seen.add(reply)
        # Sanity: both rotation variants actually got exercised.
        assert len(seen) == len(_GREETING_NORMAL)

    def test_recruitment_template_never_opens_with_salam(self):
        from modules.reply_templates import get_template, _RECRUITMENT_NORMAL
        seen = set()
        for _ in range(len(_RECRUITMENT_NORMAL) * 2):
            reply = get_template("recruitment", "same_sender_for_rotation", frustrated=False)
            assert reply is not None
            assert not reply.startswith("আস্সালামু")
            assert not reply.startswith("আসসালামু")
            seen.add(reply)
        assert len(seen) == len(_RECRUITMENT_NORMAL)

    def test_recruitment_template_facts_preserved(self):
        """Business facts (office location/hours) must survive the
        salam-stripping -- only the opener word changed."""
        from modules.reply_templates import _RECRUITMENT_NORMAL
        joined = "\n".join(_RECRUITMENT_NORMAL)
        assert "ভিক্টোরিয়া গেইট, পাহাড়তলী, চট্টগ্রাম" in joined
        assert "সকাল ৯টা থেকে বিকাল ৫টা" in joined
        assert "নাম, লোকেশন আর অভিজ্ঞতা" in joined


class TestReplyRulesNoUnconditionalSalam:
    """modules.social_auto_reply.reply_rules.WELCOME_REPLY/LOCATION_REPLY --
    live, reachable via reply_generator.generate_reply() and as
    _combined_rule_reply()'s last-resort fallback."""

    def test_welcome_reply_no_salam_opener(self):
        from modules.social_auto_reply import reply_rules as rules
        assert not rules.WELCOME_REPLY.startswith("আসসালামু")
        assert not rules.WELCOME_REPLY.startswith("ওয়ালাইকুম")

    def test_location_reply_no_salam_opener(self):
        from modules.social_auto_reply import reply_rules as rules
        assert not rules.LOCATION_REPLY.startswith("আসসালামু")
        assert not rules.LOCATION_REPLY.startswith("ওয়ালাইকুম")

    def test_welcome_reply_facts_preserved(self):
        from modules.social_auto_reply import reply_rules as rules
        assert "Survey Scout" in rules.WELCOME_REPLY
        assert "১০,০০০" in rules.WELCOME_REPLY
        assert "১৮,০০০" in rules.WELCOME_REPLY

    def test_location_reply_facts_preserved(self):
        from modules.social_auto_reply import reply_rules as rules
        assert rules.OFFICE_ADDRESS in rules.LOCATION_REPLY
        assert rules.OFFICE_NUMBERS in rules.LOCATION_REPLY


class TestKnowledgeBaseFallbackNoSalam:
    """modules.knowledge_base._FALLBACK's salam/menu/hi/start-triggered
    entry -- confirmed live, high-traffic (message_router, route_b,
    conversation_layer.recruitment all fall back to this), and previously
    fired "ওয়ালাইকুম আস্সালাম" on ANY of its trigger keywords, including
    non-salam ones like "menu"/"hi"/"start"."""

    async def _get_reply_forcing_fallback(self, text, monkeypatch):
        """get_reply() tries a DB lookup first; force a miss by making the
        DB fetch return no rows so the hardcoded _FALLBACK table is
        reliably exercised without needing a live DB."""
        import modules.knowledge_base as kb

        async def _empty_fetch_all(*args, **kwargs):
            return []

        monkeypatch.setattr(kb, "fetch_all", _empty_fetch_all)
        return await kb.get_reply(text)

    async def test_greeting_fallback_no_salam_opener(self, monkeypatch):
        reply = await self._get_reply_forcing_fallback("আসসালামু আলাইকুম", monkeypatch)
        assert reply is not None
        assert not reply.startswith("ওয়ালাইকুম")
        assert not reply.startswith("আসসালামু")

    async def test_menu_trigger_no_salam_opener(self, monkeypatch):
        """The false-positive-trigger case: "menu" isn't a salam at all,
        but shared the same reply text before this fix."""
        reply = await self._get_reply_forcing_fallback("menu", monkeypatch)
        assert reply is not None
        assert not reply.startswith("ওয়ালাইকুম")

    async def test_fallback_intro_text_preserved(self, monkeypatch):
        """Only the salam line was removed -- the self-intro and menu
        options must be unchanged."""
        reply = await self._get_reply_forcing_fallback("hello", monkeypatch)
        assert "আমি ফজলে" in reply
        assert "চাকরি সম্পর্কে জানতে" in reply
