"""
Regression tests for the 2026-08-20 salam-leading fix (Owner-directed
template audit). Real regression case: draft 8801...1596 reply to
"Uthlam ghum theke" (no salam in the sender's message) came back
salam-prefixed anyway, both in modules.message_router's family-role
deflection reply and modules.social_auto_reply's recruitment greeting
reply. Both paths are covered here, plus the shared detection helper
itself -- see shared/greeting.py's own docstring for why it's shared.
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

    def test_salam_in_sender_message_gets_salam_back(self):
        from modules.message_router import _family_deflection_reply
        reply = _family_deflection_reply("আপনি", "আসসালামু আলাইকুম বাবা")
        assert reply.startswith("আস-সালামু আলাইকুম")

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

    def test_salam_intent_gets_salam_back(self):
        from modules.social_auto_reply.intelligent_generator import _combined_rule_reply
        reply = _combined_rule_reply(
            "meta_whatsapp", "আসসালামু আলাইকুম", ["greeting"], [], None,
        )
        assert reply.startswith("আসসালামু আলাইকুম")

    def test_already_answered_greeting_not_repeated(self):
        """Section 7's 'continuing conversation -> don't repeat greeting'
        requirement -- already satisfied by the pre-existing `already`
        state check, confirmed still intact after this fix."""
        from modules.social_auto_reply.intelligent_generator import _combined_rule_reply
        reply = _combined_rule_reply(
            "meta_whatsapp", "আসসালামু আলাইকুম আবার", ["greeting"], [],
            {"answered_intents": ["greeting"]},
        )
        assert "আসসালামু আলাইকুম।" not in reply
        assert "হ্যালো।" not in reply
