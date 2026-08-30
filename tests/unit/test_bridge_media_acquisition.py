"""Bridge media acquisition → fazle-core OCR/STT integration (2026-08-30).

See docs/BRIDGE_MEDIA_ACQUISITION_INTEGRATION_PLAN_2026-08-29.md.

Baseline behaviour (commit 7997cea): an inbound media message with no caption
and no legacy `processed_text` becomes a bare "[media:<type>]" placeholder and
is intercepted in _poll_bridge — finalized terminal `media_unprocessed`, never
routed, because the bridge delivered no file to OCR/STT.

This change adds, behind `BRIDGE_MEDIA_ACQUISITION_ENABLED` (default OFF):

  * app.bridge.BridgeClient.download_media(message_id, chat_jid) — POST
    /api/download on THIS bridge, returns the local decrypted file path or
    None, never raises, uses a dedicated `media_breaker` (never the
    outbound-send breaker).
  * _poll_bridge L980 intercept: when the flag is ON and the message is a
    fetch-eligible media placeholder, download the file and FALL THROUGH so
    the existing image / audio / document extraction blocks pick it up
    (each recomputes <store_dir>/<chat_jid>/<filename>). Any failure →
    unchanged `media_unprocessed` skip (fail-closed).
  * Empty-extraction guard: acquired but no usable text → terminal
    `media_extraction_empty` skip, so "[audio: unclear]" /
    "[document:unknown:file]" never reach the classifier.

_poll_bridge is a single monolithic infinite function; per this repo's
established convention (see test_bridge_poller_orphan_message_fix.py,
test_media_placeholder_hardening.py) the poller-side tests here are source
structure assertions plus a control-flow mirror, not a live loop invocation.
"""
from __future__ import annotations

import inspect
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.bridge import BridgeClient, CircuitBreaker

pytestmark = pytest.mark.unit


# ── helpers ────────────────────────────────────────────────────────────────

def _resp(status_code=200, json_body=None, *, content_type="application/json"):
    r = MagicMock()
    r.status_code = status_code
    r.headers = {"content-type": content_type}
    r.json = MagicMock(return_value=json_body if json_body is not None else {})
    return r


def _client(base_url="http://localhost:8081", label="BR1"):
    bc = BridgeClient(base_url, label)
    bc._client = MagicMock()
    bc._client.post = AsyncMock()
    return bc


# ── 1. BridgeClient.download_media ─────────────────────────────────────────

class TestDownloadMedia:
    async def test_success_returns_path(self):
        bc = _client()
        bc._client.post.return_value = _resp(
            200, {"success": True, "path": "/home/azim/bridges/bridge1/store/x@s.whatsapp.net/a.ogg"}
        )
        out = await bc.download_media("MSG1", "x@s.whatsapp.net")
        assert out == "/home/azim/bridges/bridge1/store/x@s.whatsapp.net/a.ogg"

    async def test_posts_to_api_download_with_exact_body(self):
        bc = _client(base_url="http://localhost:8082")
        bc._client.post.return_value = _resp(200, {"success": True, "path": "/tmp/a.ogg"})
        await bc.download_media("MSG2", "chat@s.whatsapp.net")
        (url,), kwargs = bc._client.post.call_args
        assert url == "http://localhost:8082/api/download"
        assert kwargs["json"] == {"message_id": "MSG2", "chat_jid": "chat@s.whatsapp.net"}

    async def test_http_500_success_false_returns_none(self):
        bc = _client()
        bc._client.post.return_value = _resp(500, {"success": False, "message": "expired media"})
        assert await bc.download_media("MSG3", "c@s.whatsapp.net") is None

    async def test_200_but_success_false_returns_none(self):
        bc = _client()
        bc._client.post.return_value = _resp(200, {"success": False, "message": "nope"})
        assert await bc.download_media("MSG3b", "c@s.whatsapp.net") is None

    async def test_success_true_but_empty_path_returns_none(self):
        bc = _client()
        bc._client.post.return_value = _resp(200, {"success": True, "path": ""})
        assert await bc.download_media("MSG3c", "c@s.whatsapp.net") is None

    async def test_non_json_response_returns_none_not_raises(self):
        bc = _client()
        bc._client.post.return_value = _resp(
            400, content_type="text/plain; charset=utf-8"
        )
        assert await bc.download_media("MSG4", "c@s.whatsapp.net") is None

    async def test_transport_exception_returns_none_not_raises(self):
        import httpx
        bc = _client()
        bc._client.post.side_effect = httpx.ConnectError("refused")
        assert await bc.download_media("MSG5", "c@s.whatsapp.net") is None

    async def test_arbitrary_exception_returns_none_not_raises(self):
        bc = _client()
        bc._client.post.side_effect = RuntimeError("boom")
        assert await bc.download_media("MSG6", "c@s.whatsapp.net") is None

    async def test_missing_ids_short_circuit_without_http(self):
        bc = _client()
        assert await bc.download_media("", "c@s.whatsapp.net") is None
        assert await bc.download_media("MSG7", "") is None
        bc._client.post.assert_not_awaited()

    async def test_media_breaker_open_skips_http_call(self):
        bc = _client()
        # trip the dedicated media breaker
        for _ in range(bc.media_breaker.failure_threshold):
            bc.media_breaker.record_failure()
        assert bc.media_breaker.allow() is False
        out = await bc.download_media("MSG8", "c@s.whatsapp.net")
        assert out is None
        bc._client.post.assert_not_awaited()

    async def test_download_failure_never_touches_the_send_breaker(self):
        """A flood of expired-CDN media failures must not open self.breaker —
        that breaker gates (and alerts on) every outbound send via
        modules.outbound."""
        bc = _client()
        bc._client.post.return_value = _resp(500, {"success": False, "message": "x"})
        for _ in range(bc.breaker.failure_threshold + 3):
            await bc.download_media("MSGX", "c@s.whatsapp.net")
        assert bc.breaker.allow() is True
        assert bc.breaker.state() == "closed"

    async def test_success_resets_media_breaker(self):
        bc = _client()
        bc.media_breaker.record_failure()
        bc.media_breaker.record_failure()
        bc._client.post.return_value = _resp(200, {"success": True, "path": "/tmp/a.ogg"})
        await bc.download_media("MSG9", "c@s.whatsapp.net")
        assert bc.media_breaker.state() == "closed"

    def test_media_breaker_is_a_distinct_instance(self):
        bc = _client()
        assert isinstance(bc.media_breaker, CircuitBreaker)
        assert bc.media_breaker is not bc.breaker

    async def test_download_timeout_env_is_honoured(self, monkeypatch):
        monkeypatch.setenv("BRIDGE_MEDIA_DOWNLOAD_TIMEOUT_S", "12")
        bc = _client()
        bc._client.post.return_value = _resp(200, {"success": True, "path": "/tmp/a.ogg"})
        await bc.download_media("MSG10", "c@s.whatsapp.net")
        assert bc._client.post.call_args.kwargs["timeout"] == 12.0


# ── 2. Provenance: each bridge downloads via its own client ────────────────

class TestProvenance:
    def test_bridge_configs_map_each_bridge_to_its_own_client(self):
        from modules.bridge_poller import BRIDGE_CONFIGS
        from app.bridge import get_bridge1, get_bridge2, get_bridge3
        by_name = {c["name"]: c for c in BRIDGE_CONFIGS}
        assert by_name["bridge1"]["get_bridge"] is get_bridge1
        assert by_name["bridge2"]["get_bridge"] is get_bridge2
        assert by_name["bridge3"]["get_bridge"] is get_bridge3

    def test_intercept_downloads_via_this_bridges_own_client(self):
        src = _poll_src()
        i = src.index("is_media_placeholder(text) and not has_meaningful_text")
        j = src.index("if not _acquired:", i)
        block = src[i:j]
        # the acquire call uses THIS iteration's own config client, never a
        # hardcoded get_bridge1/2/3
        assert 'config["get_bridge"]().download_media(' in block
        assert "get_bridge1()" not in block
        assert "get_bridge2()" not in block
        assert "get_bridge3()" not in block

    def test_get_bridgeN_singletons_target_distinct_urls(self):
        from app.bridge import get_bridge1, get_bridge2, get_bridge3
        urls = {get_bridge1().base_url, get_bridge2().base_url, get_bridge3().base_url}
        assert len(urls) == 3


# ── 3. _poll_bridge intercept structure ───────────────────────────────────

def _poll_src():
    import modules.bridge_poller as bp
    return inspect.getsource(bp._poll_bridge)


class TestInterceptStructure:
    def test_acquisition_is_flag_gated_default_off(self):
        src = _poll_src()
        assert 'os.getenv("BRIDGE_MEDIA_ACQUISITION_ENABLED", "false")' in src

    def test_acquisition_preconditions_present(self):
        src = _poll_src()
        i = src.index("is_media_placeholder(text) and not has_meaningful_text")
        j = src.index("if not _acquired:", i)
        block = src[i:j]
        assert 'msg.get("filename")' in block
        assert '_mtype in ("audio", "ptt", "image", "document", "application")' in block
        assert 'phone.startswith("unresolved:")' in block
        assert "phone not in _own_bridge_numbers()" in block
        assert "asyncio.wait_for(" in block
        assert "os.path.isfile(_path)" in block
        assert "MEDIA_ACQUIRED" in block

    def test_failed_acquire_falls_through_to_unchanged_media_unprocessed_skip(self):
        src = _poll_src()
        i = src.index("if not _acquired:")
        j = src.index("# else: file is now staged", i)
        block = src[i:j]
        assert 'status="skipped", error="media_unprocessed"' in block
        assert '_mark_core_queue_status(bridge_name, msg, "skipped", "media_unprocessed")' in block
        assert "MEDIA_UNPROCESSED" in block
        assert "continue" in block

    def test_acquired_fall_through_reaches_extraction_then_routing(self):
        src = _poll_src()
        i_intercept = src.index("is_media_placeholder(text) and not has_meaningful_text")
        i_img = src.index('media_type") == "image" and not has_meaningful_text(text)')
        i_audio = src.index('("audio", "ptt") and not has_meaningful_text(text)')
        i_route = src.index("await process_bridge_inbound(")
        assert i_intercept < i_img < i_route
        assert i_intercept < i_audio < i_route

    def test_empty_extraction_guard_present_and_before_routing(self):
        src = _poll_src()
        assert "MEDIA_EXTRACTION_EMPTY" in src
        i_guard = src.index("MEDIA_EXTRACTION_EMPTY")
        i_route = src.index("await process_bridge_inbound(")
        i_fromlog = src.index('from={phone} text=')
        assert i_guard < i_fromlog < i_route
        # guard body
        g = src.index("if _acquired and not has_meaningful_text(_extracted_text)")
        block = src[g:src.index("continue", g) + len("continue")]
        assert 'error="media_extraction_empty"' in block
        assert 'text.lstrip().startswith(("[audio:", "[document:", "[image:"))' in block

    def test_acquired_flag_initialised_per_message(self):
        src = _poll_src()
        assert "_acquired = False" in src
        i_init = src.index("_acquired = False")
        i_loop = src.index("for msg in messages:")
        i_intercept = src.index("is_media_placeholder(text) and not has_meaningful_text")
        assert i_loop < i_init < i_intercept

    def test_existing_media_eligibility_gates_untouched(self):
        src = _poll_src()
        assert src.count('not has_meaningful_text(text) and msg.get("filename")') == 3


# ── 4. Control-flow mirror of the intercept decision ──────────────────────

def _acquire_precondition(*, flag, media_type, filename, phone, own_numbers):
    """Mirrors the boolean gate in the _poll_bridge L980 intercept."""
    return (
        os.getenv("BRIDGE_MEDIA_ACQUISITION_ENABLED", "false").strip().lower() in ("true", "1", "yes")
        and bool(filename)
        and (media_type or "").lower() in ("audio", "ptt", "image", "document", "application")
        and not phone.startswith("unresolved:")
        and phone not in own_numbers
    )


class TestAcquirePreconditionMirror:
    def test_flag_off_never_acquires(self, monkeypatch):
        monkeypatch.delenv("BRIDGE_MEDIA_ACQUISITION_ENABLED", raising=False)
        assert not _acquire_precondition(
            flag=False, media_type="audio", filename="a.ogg",
            phone="8801700000001", own_numbers={},
        )

    def test_flag_on_audio_with_file_acquires(self, monkeypatch):
        monkeypatch.setenv("BRIDGE_MEDIA_ACQUISITION_ENABLED", "true")
        assert _acquire_precondition(
            flag=True, media_type="ptt", filename="a.ogg",
            phone="8801700000001", own_numbers={},
        )

    def test_flag_on_video_not_a_target(self, monkeypatch):
        monkeypatch.setenv("BRIDGE_MEDIA_ACQUISITION_ENABLED", "1")
        assert not _acquire_precondition(
            flag=True, media_type="video", filename="v.mp4",
            phone="8801700000001", own_numbers={},
        )

    def test_flag_on_no_filename_not_acquired(self, monkeypatch):
        monkeypatch.setenv("BRIDGE_MEDIA_ACQUISITION_ENABLED", "yes")
        assert not _acquire_precondition(
            flag=True, media_type="image", filename=None,
            phone="8801700000001", own_numbers={},
        )

    def test_flag_on_unresolved_sender_not_acquired(self, monkeypatch):
        monkeypatch.setenv("BRIDGE_MEDIA_ACQUISITION_ENABLED", "true")
        assert not _acquire_precondition(
            flag=True, media_type="audio", filename="a.ogg",
            phone="unresolved:12345", own_numbers={},
        )

    def test_flag_on_own_bridge_number_not_acquired(self, monkeypatch):
        monkeypatch.setenv("BRIDGE_MEDIA_ACQUISITION_ENABLED", "true")
        assert not _acquire_precondition(
            flag=True, media_type="audio", filename="a.ogg",
            phone="8801888888888", own_numbers={"8801888888888": "bridge2"},
        )


# ── 5. process_bridge_inbound placeholder guard still holds ───────────────

class TestRoutingGuardUnchanged:
    async def test_media_placeholder_without_extracted_flag_not_routed(self, monkeypatch):
        import modules.bridge_poller as bp
        classify_spy = MagicMock(return_value="unknown")
        elig_spy = AsyncMock(side_effect=RuntimeError("__past_guard__"))
        monkeypatch.setattr(bp, "classify_intent", classify_spy)
        monkeypatch.setattr(bp, "recruitment_eligibility", elig_spy)
        await bp.process_bridge_inbound(
            "bridge1", "8801700000009", "[media:audio]",
            id_role="unknown", id_conf=0, id_name="",
            message_id="M1", chat_jid="8801700000009@s.whatsapp.net",
            is_extracted_media=False, core_message_id=None,
        )
        assert classify_spy.call_count == 0
        assert elig_spy.call_count == 0

    async def test_extracted_media_text_routes(self, monkeypatch):
        import modules.bridge_poller as bp
        classify_spy = MagicMock(return_value="unknown")
        elig_spy = AsyncMock(side_effect=RuntimeError("__past_guard__"))
        monkeypatch.setattr(bp, "classify_intent", classify_spy)
        monkeypatch.setattr(bp, "recruitment_eligibility", elig_spy)
        try:
            await bp.process_bridge_inbound(
                "bridge1", "8801700000009", "আমি কাজ করতে চাই — from a voice note",
                id_role="unknown", id_conf=0, id_name="",
                message_id="M2", chat_jid="8801700000009@s.whatsapp.net",
                is_extracted_media=True, core_message_id=None,
            )
        except RuntimeError as e:
            if "__past_guard__" not in str(e):
                raise
        assert classify_spy.call_count == 1
