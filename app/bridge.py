"""
Fazle Core — Bridge Client
Wraps HTTP calls to both QR WhatsApp bridges.
"""
import logging
import os
import time
import httpx
from app.config import get_settings

log = logging.getLogger("fazle.bridge")

# 2026-07-27: Owner directive \u2014 no footer, marker, or any other character
# (visible or invisible) may be appended to an outbound message body. The
# previous invisible zero-width-space marker (also named _AUTOMATED_SUFFIX)
# has been removed outright; outbound text is sent byte-for-byte as given.
# bridge_poller._is_system_generated_notification() no longer depends on a
# body marker \u2014 it now identifies our own notifications solely by their
# known title prefixes (modules.message_router.SYSTEM_NOTIFICATION_TITLE_PREFIXES).


class BridgeSendError(Exception):
    """Raised by BridgeClient.send_strict on any non-2xx or transport failure."""


class CircuitBreaker:
    """Per-bridge breaker. CLOSED → OPEN after N failures in window. HALF_OPEN allows 1 probe."""

    def __init__(self, label: str, failure_threshold: int = 5,
                 window_seconds: int = 60, open_seconds: int = 60):
        self.label = label
        self.failure_threshold = failure_threshold
        self.window_seconds = window_seconds
        self.open_seconds = open_seconds
        self._failures: list[float] = []
        self._opened_at: float | None = None
        self._half_open_in_flight = False

    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if time.time() - self._opened_at >= self.open_seconds:
            return "half_open"
        return "open"

    def allow(self) -> bool:
        st = self.state()
        if st == "closed":
            return True
        if st == "open":
            return False
        # half_open: allow exactly one probe
        if self._half_open_in_flight:
            return False
        self._half_open_in_flight = True
        return True

    def record_success(self) -> None:
        if self._opened_at is not None:
            log.info(f"[breaker:{self.label}] closing after success")
        self._failures.clear()
        self._opened_at = None
        self._half_open_in_flight = False

    def record_failure(self) -> bool:
        """Returns True if the breaker just opened on this failure."""
        now = time.time()
        # half_open failure → re-open
        if self._opened_at is not None:
            self._opened_at = now
            self._half_open_in_flight = False
            return False  # already-open re-arm; not 'just opened'
        # CLOSED state: trim window
        self._failures = [t for t in self._failures if now - t < self.window_seconds]
        self._failures.append(now)
        if len(self._failures) >= self.failure_threshold:
            self._opened_at = now
            log.warning(f"[breaker:{self.label}] OPEN ({len(self._failures)} fails in {self.window_seconds}s)")
            return True
        return False


class BridgeClient:
    def __init__(self, base_url: str, label: str):
        self.base_url = base_url.rstrip("/")
        self.label = label
        timeout = float(os.getenv("OUTBOUND_BRIDGE_TIMEOUT_S", "10"))
        self._client = httpx.AsyncClient(timeout=timeout)
        self.breaker = CircuitBreaker(label)
        # Separate breaker for /api/download: an expired-CDN media fetch
        # failing is common and benign — it must never trip `self.breaker`,
        # which gates (and alerts on) all outbound sends via modules.outbound.
        self.media_breaker = CircuitBreaker(f"{label}:media")

    async def _set_send(self, allow: bool):
        try:
            await self._client.post(
                f"{self.base_url}/api/send-control",
                json={"allow": allow},
                timeout=5.0,
            )
        except Exception:
            pass  # nosec: optional permission toggle, real send still attempted

    async def send(self, jid: str, text: str) -> bool:
        """Best-effort send (legacy). Returns bool. Use send_strict for queue."""
        try:
            await self.send_strict(jid, text)
            return True
        except BridgeSendError as e:
            log.error(f"[{self.label}] send error to {jid}: {e}")
            return False

    async def send_strict(self, jid: str, text: str) -> None:
        """Strict send: raises BridgeSendError on any failure. Used by outbound queue."""
        from modules.message_router import record_inbound_action
        if not jid.endswith("@s.whatsapp.net") and not jid.endswith("@g.us"):
            jid = jid + "@s.whatsapp.net"
        log.info(f"[BRIDGE_SEND_START] label={self.label} jid={jid} body={text[:60]!r}")
        record_inbound_action("direct_send", transport=self.label, recipient=jid, outcome="attempted")
        try:
            await self._set_send(True)
            r = await self._client.post(
                f"{self.base_url}/api/send",
                json={"recipient": jid, "message": text},
            )
            if r.status_code != 200:
                record_inbound_action("direct_send", transport=self.label, recipient=jid,
                                      outcome="failed", http_status=r.status_code)
                log.error(f"[BRIDGE_SEND_FAIL] label={self.label} jid={jid} http={r.status_code} body={r.text[:100]}")
                raise BridgeSendError(f"http {r.status_code}: {r.text[:200]}")
            log.info(f"[BRIDGE_SEND_SUCCESS] label={self.label} jid={jid}")
            record_inbound_action("direct_send", transport=self.label, recipient=jid,
                                  outcome="send_accepted", delivery_outcome="unconfirmed")
        except httpx.HTTPError as e:
            record_inbound_action("direct_send", transport=self.label, recipient=jid,
                                  outcome="failed", error_type=type(e).__name__)
            log.error(f"[BRIDGE_SEND_FAIL] label={self.label} jid={jid} transport={e}")
            raise BridgeSendError(f"transport: {e}") from e
        finally:
            await self._set_send(False)

    async def send_multi(self, jid: str, messages: list[str]) -> bool:
        from modules.message_router import record_inbound_action
        if not jid.endswith("@s.whatsapp.net") and not jid.endswith("@g.us"):
            jid = jid + "@s.whatsapp.net"
        try:
            await self._set_send(True)
            for msg in messages:
                record_inbound_action("direct_send", transport=self.label, recipient=jid, outcome="attempted")
                response = await self._client.post(
                    f"{self.base_url}/api/send",
                    json={"recipient": jid, "message": msg},
                )
                record_inbound_action("direct_send", transport=self.label, recipient=jid,
                                      outcome="send_accepted" if response.status_code == 200 else "failed",
                                      delivery_outcome="unconfirmed", http_status=response.status_code)
            return True
        except Exception as e:
            log.error(f"[{self.label}] send_multi error: {e}")
            return False
        finally:
            await self._set_send(False)

    async def ensure_enabled(self) -> bool:
        """Enable outbound send-control and confirm. Called at startup to survive bridge restarts."""
        try:
            await self._client.post(
                f"{self.base_url}/api/send-control",
                json={"allow": True},
                timeout=5.0,
            )
            r = await self._client.get(f"{self.base_url}/api/send-status", timeout=5.0)
            allowed = r.json().get("allowed", False)
            log.info(f"[{self.label}] send-control ensure: allowed={allowed}")
            return bool(allowed)
        except Exception as e:
            log.warning(f"[{self.label}] send-control ensure failed: {e}")
            return False

    async def status(self) -> dict:
        try:
            r = await self._client.get(f"{self.base_url}/api/send-status", timeout=5.0)
            return r.json()
        except Exception:
            return {"allowed": False, "error": "unreachable"}

    async def download_media(self, message_id: str, chat_jid: str) -> str | None:
        """POST /api/download on THIS bridge to fetch + decrypt an inbound
        media file. Returns the local file path the bridge wrote, or None.

        Never raises. The bridge endpoint is idempotent (an `os.Stat`
        short-circuit returns the already-downloaded file on a repeat call).
        Uses `self.media_breaker`, never the outbound-send breaker.
        """
        if not message_id or not chat_jid:
            return None
        if not self.media_breaker.allow():
            log.info(f"[{self.label}] download_media skip msg={message_id}: media breaker open")
            return None
        timeout = float(os.getenv("BRIDGE_MEDIA_DOWNLOAD_TIMEOUT_S", "45"))
        try:
            r = await self._client.post(
                f"{self.base_url}/api/download",
                json={"message_id": message_id, "chat_jid": chat_jid},
                timeout=timeout,
            )
            body: dict = {}
            if r.headers.get("content-type", "").startswith("application/json"):
                try:
                    body = r.json()
                except Exception:
                    body = {}
            if r.status_code == 200 and body.get("success"):
                self.media_breaker.record_success()
                path = body.get("path") or None
                log.info(f"[{self.label}] download_media ok msg={message_id} path={path}")
                return path
            self.media_breaker.record_failure()
            log.info(
                f"[{self.label}] download_media no-file msg={message_id} "
                f"http={r.status_code} detail={str(body.get('message'))[:120]!r}"
            )
            return None
        except Exception as e:
            self.media_breaker.record_failure()
            log.warning(f"[{self.label}] download_media error msg={message_id}: {e}")
            return None

    async def close(self):
        await self._client.aclose()


# Singleton instances — created once at startup
_bridge1: BridgeClient | None = None
_bridge2: BridgeClient | None = None
_bridge3: BridgeClient | None = None


def get_bridge1() -> BridgeClient:
    global _bridge1
    if _bridge1 is None:
        s = get_settings()
        _bridge1 = BridgeClient(s.bridge1_url, "BR1")
    return _bridge1


def get_bridge2() -> BridgeClient:
    global _bridge2
    if _bridge2 is None:
        s = get_settings()
        _bridge2 = BridgeClient(s.bridge2_url, "BR2")
    return _bridge2


def get_bridge3() -> BridgeClient:
    global _bridge3
    if _bridge3 is None:
        s = get_settings()
        _bridge3 = BridgeClient(s.bridge3_url, "BR3")
    return _bridge3
