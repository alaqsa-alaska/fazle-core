# Bridge Media Acquisition → fazle-core OCR/STT Integration — IMPLEMENTATION PLAN

**Status:** IMPLEMENTED 2026-08-30 (code + tests + docs), NOT YET DEPLOYED — awaiting Owner approval for the fazle-core restart. Flag `BRIDGE_MEDIA_ACQUISITION_ENABLED` defaults OFF, so the code on disk is a dark change (behaviour identical to `7997cea` until the flag is set). No DB / bridge / systemd / env / routing change made. See §12.
**Prepared:** 2026-08-29. **Implemented:** 2026-08-30.
**fazle-core baseline this plan targets:** `7997cea5c641b7a6885cf5cc90df22cd83ac2313` (`fix: stop routing media placeholders as user text`).
**whatsapp-mcp baseline (already deployed, proven on all 3 bridges):** `c1eaf670d64385b3dc55afeac6211b98fdf71aec` (`fix(bridge): capture real media direct_path so /api/download works`).

> This is the follow-on to the bridge-fleet rollout. All three bridges now run the candidate binary, capture `direct_path`, and serve `POST /api/download` returning a decrypted local media file (proven: bridge1 image, bridge2 voice, bridge3 voice — HTTP 200, size == `file_length`, valid decoded media, idempotent). fazle-core has **not** been changed. This plan wires fazle-core to *use* `/api/download`.

---

## 1. Current architecture (traced from source, not assumed)

### 1.1 Inbound bridge media path — `modules/bridge_poller/__init__.py`

| Step | Location | Behavior |
|---|---|---|
| SQLite fetch | `_fetch_new_messages` (~L293) | Reads bridge `messages.db`. SELECT (built via `shared.sqlite_compat.processed_text_column()` — tolerates a store without `processed_text`) returns `id, chat_jid, sender, content, timestamp, media_type, <processed_text|NULL>, filename, url`. For a media row with no `content` and no `processed_text` **and** `filename`/`url` present → `msg["text"] = f"[media:{media_type}]"`. Rows with a media_type but no filename **and** no url are dropped (`skipped_no_text`). **`direct_path`/`media_key`/`file_sha256`/`file_enc_sha256`/`file_length` are NOT read by fazle-core** (they live in the bridge DB; the bridge's own `/api/download` reads them). |
| Per-bridge loop | `_poll_bridge` (~L855), `while True` | Iterates `BRIDGE_CONFIGS` = `[{name, messages_db, whatsapp_db, get_bridge}]` (L64-83). `get_bridge = config["get_bridge"]` (L860); `config["get_bridge"]` is `get_bridge1/2/3` — **each message carries its own bridge's client**. |
| Dedup | L917 `_is_processed(msg_id, bridge)` | `SELECT 1 FROM processed_bridge_messages WHERE message_id=$1 AND bridge=$2` → `dedup_skipped; continue`. |
| Raw canonical write | L929 `core_message_id = await _save_raw_core_copy(bridge_name, msg, phone, text)` | First `wbom_whatsapp_messages` INSERT (+ a `fazle_message_queue` row keyed `bridge_poller:{bridge}:{msg_id}`). |
| **Group guard** | L962 `if chat_jid.endswith("@g.us")` | `_mark_core_queue_status("skipped","group_read_index_only"); continue`. **Runs before any media handling — group media never reaches acquisition.** |
| **Media placeholder intercept (7997cea)** | **L980** `if is_media_placeholder(text) and not has_meaningful_text(msg.get("processed_text"))` | `_mark_processed` → `_finalize_core_copy(status="skipped", error="media_unprocessed")` → `_mark_core_queue_status("skipped","media_unprocessed")` → `log MEDIA_UNPROCESSED` → **`continue`**. **This is where every inbound media message currently ends** (the bridge historically provided no file). |
| Image pre-OCR (caption present) | L998 `if media_type=="image" and has_meaningful_text(text)` | `classify_slip_type(caption)`; if `release_slip` → `handle_ocr_release_slip`, set `text` to a Bengali ack. |
| Image 2-step OCR (no caption) | L1043 `if media_type=="image" and not has_meaningful_text(text) and msg.get("filename")` | `_fetch_image_context_rows` → `classify_from_context` / `_is_lightweight_image_eligible_for_ocr` → `escort_slip_extractor.extract_escort_slip(file_path)`. `file_path = os.path.join(os.path.dirname(messages_db), chat_jid, filename)`. `finally: _mark_processed`. |
| Audio/PTT STT | L1187 `if media_type in ("audio","ptt") and not has_meaningful_text(text) and msg.get("filename")` | `voice_processor.process_voice(file_path)`. Confident → `text = transcript; _extracted_text = transcript; _is_extracted_media = True`. Not confident → `text = "[audio: unclear]"`. Exception (incl. `FileNotFoundError`) → `_save_draft("[STT FAILED]…","stt_failed")` + `_finalize_core_copy(status="failed")` + `continue`. |
| Document/PDF | L1225 `elif (media_type in ("document","application") or filename endswith .pdf) and not has_meaningful_text(text) and msg.get("filename")` | `ocr_processor.process_document(file_path, filename)`. `text = extracted[:300]`; `_extracted_text = extracted`; `_is_extracted_media = True` (unless a cleanly-classified `auto_send_safe` candidate doc → `config["get_bridge"]().send(phone, ack)` + `_record_reply` + `_finalize_core_copy(status="done")` + `continue`). |
| Log + identity | L1266 `log "[bridge] from=… text=…"`; L1290 `detect_identity(phone, text)` → `_is_bridge3_admin_recognition` per-message override; `_finalize_core_copy(status="processing", extracted_text=_extracted_text)`. |
| Unresolved LID skip | L1361 `if phone.startswith("unresolved:")` | `_mark_core_queue_status("skipped","unresolved_lid"); continue`. |
| Route | L1407 `await process_bridge_inbound(bridge_name, phone, text, id_role=…, message_id=…, chat_jid=…, msg_timestamp=…, is_extracted_media=_is_extracted_media, core_message_id=…)` → then `_mark_core_queue_status("done")`. |
| Cursor | after loop: `[GAP_DIAG] poll_summary` (bridge1 only) + `_set_cursor`. |

**Because `_fetch_new_messages` always fills `[media:<type>]` for media without a caption, and the extraction blocks require `not has_meaningful_text(text)` (which a `[media:*]` placeholder satisfies) *plus* `msg.get("filename")` *plus* the file existing on disk — and the file never exists — the L980 intercept fires first and `continue`s for every media message. The extraction blocks at L1043/L1187/L1225 are effectively dead today.**

### 1.2 Routing entry — `process_bridge_inbound` (~L2382)

- **L2435 guard (7997cea):** `if is_media_placeholder(text) and not is_extracted_media: log + return`. Defense-in-depth for the webhook path / any future caller.
- Then: recruitment eligibility + funnel entry (L2442+), `classify_intent(text)`, `REPLY_COOLDOWN`, `_own_bridge_numbers()` / `_classify_internal_echo(source, sender, text)` (own-bridge silent-drop — pure provenance, `sender ∈ our bridge numbers` **and** arrived on a different bridge → internal echo), **LOOP_GUARD** (admin reply whose recipient == another bridge's own number → divert to draft), then `modules.message_router.process_message()` (RAG / semantic gate / Hermes), then the draft-vs-send decision (`_is_draft_always`, `should_generate_draft`, `phone_block.{phone}`).

### 1.3 `process_message` — `modules/message_router/__init__.py` (~L602)

`async def process_message(sender, text, source, *, message_id=None, is_extracted_media=False)`. **Shared by bridges and Meta.** `is_extracted_media` (cf_W001, 2026-08-25) *only* suppresses the bare-keyword `ec_trigger*` operational checks (L1542-1604) — it does **not** touch precise intent-classifier routing. Set it `True` whenever `text` is OCR/STT output.

### 1.4 `BridgeClient` — `app/bridge.py` (~L83)

`base_url` from `s.bridgeN_url` (**`.env` authoritative**: `BRIDGE1_URL=http://localhost:8081`, `BRIDGE2_URL=http://localhost:8082`, `BRIDGE3_URL=http://127.0.0.1:8083` — note `app/config.py` L102/L106 defaults have 8082/8081 *swapped*; harmless because `.env` overrides, but worth a separate 1-line cleanup). `httpx.AsyncClient(timeout=OUTBOUND_BRIDGE_TIMEOUT_S=10)`, `CircuitBreaker`. Methods: `send`, `send_strict`, `send_multi`, `ensure_enabled`, `status`, `close`. **No download method.** Singletons `get_bridge1/2/3()`.

### 1.5 Media-processor & existing extractors

- `media-processor.service` :8090 — `POST /transcribe|/ocr|/extract`, body `{"file_path": "..."}` → `{"text": "..."}`. Stateless. Proven working on a bridge-downloaded `.ogg` (session STT proof: HTTP 200, non-empty transcript; the transcript language was mis-detected on a short clip — a *quality* item, not a pipeline defect).
- `modules/voice_processor.process_voice(file_path, skip_intent=False) -> VoiceResult{transcript, word_count, confident, language_hint, intent, reply}`. `_call_transcribe`: 60s timeout, one retry at 45s, raises on non-timeout error, `FileNotFoundError` propagates.
- `modules/ocr_processor`: `process_document(file_path, filename) -> DocResult{doc_type, extracted_text, confidence_score, reply, auto_send_safe}`; `process_image(file_path) -> OcrResult{raw_text, slip_type, is_duplicate}`. `_call_ocr`/`_call_extract`: 30s timeout.
- **`modules/media_normalization.normalize_meta_media_message(msg)` — the already-built, tested Meta equivalent of what we need.** `_download_meta_media(media_id)` (Graph API) → temp file → `process_voice`/`process_image`/`process_document` → `MediaNormalizationResult{text, media_type, meta{normalized, …}}`. Combines as `text = f"[{type} message] {extracted}"`. On download fail / empty extraction → returns the placeholder with `meta.normalized=False`. Deletes the temp file in `finally`.

### 1.6 Meta WhatsApp

`_handle_meta_message` (`app/main.py` L2426) → `normalize_meta_media_message` (its own path) → `_process_message(sender, text, "meta")` → `process_message`. **Meta does not call `bridge_poller`, `process_bridge_inbound`, or `/api/download`.** The only functions shared with the planned bridge path are `voice_processor`/`ocr_processor`/`process_image`/`process_document` (**not modified by this plan**) and `process_message` (**not modified by this plan**).

### 1.7 Databases

**Bridge SQLite (`messages.db`), post-`c1eaf67`:** `id, chat_jid, sender, content, timestamp, is_from_me, media_type, filename, url, media_key, file_sha256, file_enc_sha256, file_length, [processed_text], direct_path`.
- `direct_path` present on **all 3** stores (migration applied at each candidate restart).
- `processed_text` present on bridge2 & bridge3 stores (legacy); **absent on bridge1's post-re-pair store** — fazle-core already handles this via `processed_text_column()` (`d7cbb85`).

**Postgres canonical (per inbound message):**
1. `wbom_whatsapp_messages` INSERT (`_save_raw_core_copy`) — raw text (`[media:*]` for media).
2. `fazle_message_queue` row, idempotency_key `bridge_poller:{bridge}:{msg_id}`.
3. `wbom_whatsapp_messages` UPDATE (`_finalize_core_copy`) — `message_body`, `message_type`, `is_processed`, `identity_role`, `identity_confidence`, `workflow_triggered`, `intent_detected`, **`extracted_text`**, metadata-JSON `status` ∈ {`processing`,`done`,`skipped`,`failed`} + `error`.
4. `fazle_message_queue` UPDATE (`_mark_core_queue_status`) — `status` + `last_error` (currently `media_unprocessed` for media).
5. `processed_bridge_messages` INSERT (`_mark_processed`) — dedup.
6. Downstream (`process_message`): recruitment sessions/candidates, `fazle_draft_replies`, etc. — unchanged.

**Where media outcome is recorded — no new column/table needed:**
| outcome | `wbom_whatsapp_messages` | `fazle_message_queue` |
|---|---|---|
| acquired + extracted + routed | `is_processed=true`, `extracted_text=<text>`, status `done` | `status=done` |
| acquired, extraction empty | status `skipped`, `error=media_extraction_empty` | `status=skipped` |
| download failed / not acquirable | status `skipped`, `error=media_unprocessed` (unchanged) | `status=skipped` |
| STT/OCR raised | status `failed`, `error=stt_failed:…` (unchanged) | `status=failed` |

### 1.8 Idempotency / replay (existing, sufficient — no new mechanism)

- `processed_bridge_messages (message_id, bridge)` → a re-fetched message is `dedup_skipped` before any work.
- `fazle_message_queue` idempotency_key.
- `_core_message_hash(source, source_message_ref)`.
- `/api/download` is bridge-side idempotent (`os.Stat` short-circuit — proven: repeat call, delta 0, same inode/SHA).
- `create_draft_reply` has a 5-minute same-sender+bridge collision guard.

---

## 2. Proposed minimal change

### 2.1 New — `app/bridge.py` `BridgeClient.download_media`

```python
async def download_media(self, message_id: str, chat_jid: str) -> Optional[str]:
    """POST /api/download on THIS bridge; return the local decrypted media
    path, or None. Never raises. Bridge-side idempotent."""
    if not self.breaker.allow():
        return None
    try:
        r = await self._client.post(
            f"{self.base_url}/api/download",
            json={"message_id": message_id, "chat_jid": chat_jid},
            timeout=float(os.getenv("BRIDGE_MEDIA_DOWNLOAD_TIMEOUT_S", "45")),
        )
        body = r.json() if r.headers.get("content-type","").startswith("application/json") else {}
        if r.status_code == 200 and body.get("success"):
            self.breaker.record_success()
            return body.get("path") or None
        self.breaker.record_failure()
        log.info("[%s] download_media no-file msg=%s http=%s msg=%r",
                 self.label, message_id, r.status_code, body.get("message"))
        return None
    except Exception as e:
        self.breaker.record_failure()
        log.warning("[%s] download_media error msg=%s: %s", self.label, message_id, e)
        return None
```

### 2.2 Modified — `modules/bridge_poller/__init__.py` L980 intercept

Replace the unconditional skip with **acquire-then-fall-through**, gated by a flag (default OFF):

```python
if is_media_placeholder(text) and not has_meaningful_text(msg.get("processed_text")):
    _mtype = (msg.get("media_type") or "").lower()
    _acquired = False
    if (
        os.getenv("BRIDGE_MEDIA_ACQUISITION_ENABLED", "false").lower() in ("true", "1", "yes")
        and msg.get("filename")
        and _mtype in ("audio", "ptt", "image", "document", "application")
        and not phone.startswith("unresolved:")
        and phone not in _own_bridge_numbers()
    ):
        try:
            _path = await asyncio.wait_for(
                config["get_bridge"]().download_media(str(msg["id"]), msg.get("chat_jid", "")),
                timeout=float(os.getenv("BRIDGE_MEDIA_DOWNLOAD_TIMEOUT_S", "45")) + 5,
            )
        except Exception as _dl_err:
            _path = None
            log.warning("[%s] media acquire error msg_id=%s: %s", bridge_name, msg_id, _dl_err)
        if _path and os.path.isfile(_path):
            _acquired = True
            log.info("[%s] MEDIA_ACQUIRED msg_id=%s type=%s path=%s", bridge_name, msg_id, _mtype, _path)
    if not _acquired:
        media_unprocessed_skipped += 1
        await _mark_processed(msg_id, bridge_name, phone)
        await _finalize_core_copy(core_message_id, bridge_name, msg, phone, text,
                                  msg_type=msg.get("media_type") or "media",
                                  status="skipped", error="media_unprocessed")
        await _mark_core_queue_status(bridge_name, msg, "skipped", "media_unprocessed")
        log.info("[%s] MEDIA_UNPROCESSED msg_id=%s type=%s phone=%s — placeholder only, not routed",
                 bridge_name, msg.get("media_type"), phone)
        continue
    # else: file is now at <store_dir>/<chat_jid>/<filename> — fall through
    #       so the existing L1043 / L1187 / L1225 blocks pick it up unchanged.
```

**Why fall-through works with zero change to the extraction blocks:** each block computes `file_path = os.path.join(os.path.dirname(messages_db), chat_jid, filename)`. `/api/download` wrote the decrypted file to `store/<chat_jid>/<filename>` relative to the bridge's CWD, which resolves (bridge1 via the `store` symlink; bridge2/bridge3 directly) to exactly `<store_dir>/<chat_jid>/<filename>`. Verified in the canary — `realpath` of a bridge1 download landed at `/home/azim/bridges/bridge1/store/…`.

### 2.3 Modified — empty-extraction fallback (small addition after the extraction blocks)

After the L1043 `else:` block completes (just before L1266 `log "[bridge] from=…"`), if the message was acquired media but **no** extraction populated it:

```python
if _acquired and not _is_extracted_media and not has_meaningful_text(text):
    # download succeeded but OCR/STT produced nothing usable
    await _mark_processed(msg_id, bridge_name, phone)   # if not already marked
    await _finalize_core_copy(core_message_id, bridge_name, msg, phone, text,
                              msg_type=msg.get("media_type") or "media",
                              status="skipped", error="media_extraction_empty")
    await _mark_core_queue_status(bridge_name, msg, "skipped", "media_extraction_empty")
    log.info("[%s] MEDIA_EXTRACTION_EMPTY msg_id=%s type=%s", bridge_name, msg_id, _mtype)
    continue
```

This stops `"[audio: unclear]"` / `"[document:<type>:<file>]"` from ever reaching routing. (`is_media_placeholder` does *not* match those strings, so without this they would route.)

### 2.4 No change to

- `_fetch_new_messages` (does not need `direct_path` etc.).
- The extraction blocks L1043/L1187/L1225 (they already set `_is_extracted_media` and `_extracted_text`).
- `process_bridge_inbound` / `process_message` — they receive real text + `is_extracted_media=True`, exactly as they already do for the rare pre-populated-`processed_text` case.
- The image release-slip / escort-slip branch, the document auto-ack branch — unchanged.
- Meta, `media_normalization`, `voice_processor`, `ocr_processor`.

### 2.5 Optional consolidation (recommend for v1 if cheap, else v2)

Factor `voice/image/document → text` selection into a shared helper (`modules/media_extract.extract_local_media(file_path, media_type, filename) -> ExtractResult`) reused by both `media_normalization` and `bridge_poller`. Reduces the two divergent copies of the same logic. **Not required** for correctness; the fall-through design needs zero extractor changes.

---

## 3. Processing flow: before → after

### Before (7997cea)
```
inbound voice note → bridge stores row + [c1eaf67: direct_path]
  → _fetch_new_messages: text = "[media:audio]"
  → _poll_bridge L980: is_media_placeholder → _finalize(skipped, media_unprocessed) → continue
  → NOT routed, no reply, MEDIA_UNPROCESSED logged
```

### After (flag ON)
```
inbound voice note → bridge stores row + direct_path/media_key/hashes
  → _fetch_new_messages: text = "[media:audio]"
  → _poll_bridge L980: placeholder + flag ON + audio + filename + resolved + not own-bridge
      → config["get_bridge"]().download_media(id, chat_jid)   [bridge1→:8081, bridge2→:8082, bridge3→:8083]
      → bridge /api/download → decrypts via direct_path → writes store/<chat>/<file> → returns path
      → MEDIA_ACQUIRED; fall through (no continue)
  → L1187 audio block: file now exists → process_voice(file_path) → transcript
      → text = transcript; _extracted_text = transcript; _is_extracted_media = True
  → L1290 identity; _finalize_core_copy(status=processing, extracted_text=transcript)
  → L1407 process_bridge_inbound(text=transcript, is_extracted_media=True, …)
      → L2435 guard passes (not a placeholder); own-bridge / LOOP_GUARD / cooldown unchanged
      → process_message(transcript, is_extracted_media=True) → normal routing/Hermes
  → _mark_core_queue_status(done); _mark_processed
```

Failure/empty → `MEDIA_UNPROCESSED` / `MEDIA_EXTRACTION_EMPTY` skip (fail-closed, identical to today's no-reply behavior).

---

## 4. Caption / media semantics (A–F, explicit)

| case | condition | v1 behavior | notes |
|---|---|---|---|
| **A. media only, extraction OK** | `text=="[media:X]"`, extraction non-empty | `text = <extracted>` (existing blocks already do this), `is_extracted_media=True`, route | primary case |
| **B. caption only** (no media, or media not a fetch target) | `has_meaningful_text(caption)` | **unchanged** — L980 not entered; caption routes as text; image caption still hits L998 release-slip check | |
| **C. media + caption** | `has_meaningful_text(text)` (text == caption) | **v1: unchanged** — L980 not entered; only the caption routes; media NOT fetched/extracted | **Deferred to v2.** Proposed v2 representation (one message, one reply): `text = f"{caption}\n\n[{media_type}] {extracted}"` with `is_extracted_media=True`. Requires widening the L980 trigger to also fire on `media_type present and filename present and not _is_extracted_media` regardless of caption. Do **not** create two user messages or two replies. |
| **D. extraction succeeds** | — | as A | |
| **E. extraction empty** | download OK, `process_voice` not confident / `process_document` empty / `process_image` no `raw_text` | `MEDIA_EXTRACTION_EMPTY` skip — **not routed**, no reply (§2.3) | prevents `[audio: unclear]` / `[document:…]` reaching the classifier |
| **F. download fails / not acquirable** | 403, timeout, circuit open, newsletter/group, expired CDN media, unsupported type, `unresolved:` sender, own-bridge sender | `MEDIA_UNPROCESSED` skip — unchanged from 7997cea, fail-closed | |

**STT/OCR raised an exception** (distinct from empty): existing L1201 `_audio_err` path — `_save_draft("[STT FAILED]…")` + `status=failed` + `continue`. **Keep as-is for v1** (admin-visible, not a customer reply, 5-min dedup on the draft). Flag for review: an unbounded voice-note failure rate could fill the draft queue — consider a per-conversation dedup in v2.

---

## 5. Security / routing protections — all preserved

| protection | how it stays intact |
|---|---|
| Group `@g.us` policy | L962 guard runs **before** L980. Group media never reaches `download_media`. Unchanged. |
| Own-bridge silent-drop / cross-bridge echo | `_classify_internal_echo` in `process_bridge_inbound` still runs after extraction. **Plus** the plan adds `phone not in _own_bridge_numbers()` to the download precondition so we don't even fetch media for an internal echo. |
| LOOP_GUARD (admin cross-bridge bounce → draft) | in `process_bridge_inbound`, unchanged. |
| `[media:*]` never reaches classifier/RAG/Hermes | On `not _acquired` or empty extraction → `continue` (skip). On success → `text` is real; `is_media_placeholder(real_text)` is False; `process_bridge_inbound` L2435 guard also confirms. |
| Fail-closed on download/extraction failure | `MEDIA_UNPROCESSED` / `MEDIA_EXTRACTION_EMPTY` skip, no reply, no fabricated content. |
| Recruitment duplicate protection | `process_bridge_inbound` eligibility + `REPLY_COOLDOWN`, unchanged. |
| Replay / idempotency | `processed_bridge_messages` + `fazle_message_queue` key + `/api/download` `os.Stat` short-circuit. No new mechanism. |
| cf_W001 (OCR text tripping keyword triggers) | `is_extracted_media=True` set by the existing extraction blocks → suppresses bare-keyword `ec_trigger*` checks. |
| No Go-bridge OCR/STT | The bridge only downloads/decrypts. Extraction is 100% Python (media-processor). |
| `unresolved:` LID | L1361 skip still applies; plan also excludes `unresolved:` from the download precondition. |
| `phone_block.{phone}` | applied in `process_bridge_inbound`'s send decision, unchanged. |

---

## 6. Meta WhatsApp regression considerations

- **No Meta code path is modified.** `_handle_meta_message` → `normalize_meta_media_message` is untouched.
- Shared functions `voice_processor` / `ocr_processor` / `process_image` / `process_document` / `process_message` are **not modified**.
- Required Meta regression tests (see §7): (a) Meta **text** message → `process_message` → identical reply/admin-notification as on `7997cea`; (b) Meta **media** (audio/image/document) → `normalize_meta_media_message` → identical `MediaNormalizationResult` (unit test with a mocked `_download_meta_media` + stubbed extractors); (c) `is_extracted_media` default still `False` on the Meta path (Meta does not pass it today — leave that as-is; not this plan's scope).
- **Conclusion:** Meta regression risk = **none from code**, but keep the shared-`process_message` regression tests as a tripwire.

---

## 7. Test matrix

### CODE / UNIT PROOF (`tests/unit/`, no live bridge, no live WhatsApp)

New file `test_bridge_media_acquisition.py`:

1. `BridgeClient.download_media` — 200+`success:true` → returns `path`; 500/`success:false` → `None`; timeout → `None`; circuit-open → `None` without HTTP call; never raises.
2. **Provenance:** `_poll_bridge` for bridge1 config calls `get_bridge1().download_media` (→ `:8081`); bridge2 → `get_bridge2` (`:8082`); bridge3 → `get_bridge3` (`:8083`). A bridge1 message must never call bridge2/bridge3's client. (Source-structure test on `config["get_bridge"]()` + a mock asserting the URL.)
3. **Correct endpoint:** the POST goes to `{bridge_url}/api/download` with `{"message_id","chat_jid"}` — exact shape.
4. **Flag OFF (default):** placeholder media → `MEDIA_UNPROCESSED` skip, `download_media` **never called** — byte-identical to `7997cea`.
5. **Flag ON, audio, download OK:** file staged at `store_dir/chat_jid/filename` → `process_voice` (stubbed) returns confident transcript → `text == transcript`, `is_extracted_media True`, routed (mock `process_bridge_inbound` asserts called with the transcript).
6. **Flag ON, image, download OK:** existing L1043 OCR block runs (stub `escort_slip_extractor`/`ocr_processor`).
7. **Flag ON, document, download OK:** L1225 `process_document` (stubbed) runs; extracted text routed; `auto_send_safe` candidate-doc branch still auto-acks via the bridge client.
8. **Caption preserved (B):** captioned media → L980 not entered → caption routes unchanged, `download_media` not called.
9. **Caption + extracted (C):** v1 → only caption routes (documented deferral); add an xfail/skip marker referencing the v2 combined representation.
10. **Download fails (F):** `download_media` → `None` → `MEDIA_UNPROCESSED` skip, not routed, no reply.
11. **Empty extraction (E):** download OK, `process_voice` `confident=False` → `MEDIA_EXTRACTION_EMPTY` skip; `"[audio: unclear]"` **not** routed. Same for `process_document` empty.
12. **Unsupported media (`video`):** flag ON → not a download target → `MEDIA_UNPROCESSED` skip; `[media:video]` never routed (also caught by L2435).
13. **Placeholder never reaches classifier/RAG/Hermes:** extends `test_media_placeholder_hardening.py` — with flag ON + download failing, `classify_intent`/`process_message` still not called.
14. **Own-bridge silent-drop:** media from `_settings.bridge2_number` arriving on bridge3 → `download_media` **not called** (precondition) → skip; and if it were, `_classify_internal_echo` still drops it.
15. **Group policy:** `@g.us` media → L962 skip before L980 → `download_media` never called.
16. **Admin/control loop guard:** an admin-relay message that also carries media → LOOP_GUARD still diverts to draft; no double send.
17. **Replay / idempotency:** same `msg_id` re-fetched after a successful acquire → `_is_processed` → `dedup_skipped`, no second `download_media`, no second reply, no second canonical write.
18. **`processed_text` present** (bridge2/bridge3 store shape) → `has_meaningful_text(processed_text)` True → L980 not entered → routes the stored OCR/STT text (legacy path), `download_media` not called.
19. **`processed_text` absent** (bridge1 store shape) → `processed_text_column()` yields `NULL` → placeholder path → acquisition attempted.
20. **No duplicate canonical write:** exactly one `_save_raw_core_copy` + one terminal `_finalize_core_copy` per message in every branch.
21. **No duplicate outbound reply:** success path → at most one `process_message`-driven reply; `_record_reply` / `create_draft_reply` dedup respected.
22. **Ordinary text messages unchanged:** a plain text inbound → identical routing as `7997cea` (regression guard).

### INTEGRATION PROOF (`tests/integration/`, real sqlite temp store + stubbed HTTP)

- Build a temp `messages.db` (both schema shapes), insert an audio row with `filename`, point `store_dir` at a temp dir, pre-place a real `.ogg`, stub `BridgeClient.download_media` to "stage" the file, stub media-processor `/transcribe`. Drive one `_poll_bridge` iteration (mirror pattern per `test_bridge_poller_orphan_message_fix.py` / `test_group_ingestion_stage_e3.py`) and assert the full chain: `MEDIA_ACQUIRED` → transcript → `wbom_whatsapp_messages.extracted_text` set → `fazle_message_queue.status=done` → `processed_bridge_messages` row → exactly one `process_message` call.
- Meta media: `normalize_meta_media_message` with a mocked `_download_meta_media` + stubbed extractors → unchanged result (regression).

### LIVE / ORGANIC PROOF (post-deploy, flag ON, one bridge at a time)

- Wait for organic keyed DM/group media (offset-aware timestamp filter — do **not** repeat the naive-vs-`+02:00` bug from the bridge1 canary).
- Verify: `MEDIA_ACQUIRED` logged → file present under that bridge's store → `AUDIO_TRANSCRIBED` / `DOC_PROCESSED` / OCR log → `wbom_whatsapp_messages.extracted_text` populated → `fazle_message_queue.status=done` → routing/reply consistent with the transcript → **no duplicate reply**, **no LOOP/echo regression**, bridge sessions untouched, `NRestarts` unchanged.
- Negative live check: a newsletter image → `MEDIA_UNPROCESSED` (no `media_key`), no error escalation.

### Regression suites to run before deploy

`tests/unit/test_media_placeholder_hardening.py`, `test_bridge_poller_*` (all), `test_media_normalization_phase3.py`, `test_voice_processor_skip_intent.py`, `test_chat_voice_endpoint.py`, `test_escort_ocr_pipeline.py`, `test_bridge_poller_processed_text_compat.py`, `test_fpe_bridge_path_authority.py`, `test_group_ingestion_stage_e3.py`, `test_wbom_actor_attribution_stage_b.py`, `test_admin_channel_group_isolation_stage_f.py`; then `scripts/restart_gate.sh --check-only` (checks 1-7 incl. 4/4b/4c recruitment golden + Phase C corpus).

---

## 8. Deployment sequence (plan — do NOT execute in the planning session)

1. Implement §2.1 + §2.2 + §2.3 (+ optional §2.5). Add env docs: `BRIDGE_MEDIA_ACQUISITION_ENABLED` (default `false`), `BRIDGE_MEDIA_DOWNLOAD_TIMEOUT_S` (default `45`). **Flag defaults OFF → dark deploy, zero behavior change.**
2. New unit tests (§7 CODE) + integration test. `go`/`gofmt` n/a (Python).
3. Run the regression suites (§7).
4. `git diff` review — confirm: only `app/bridge.py` + `modules/bridge_poller/__init__.py` (+ optional `modules/media_extract.py`) + tests + `.env.example`/docs touched; no routing/business-logic drift; no schema file.
5. Commit: `fix: acquire inbound bridge media via /api/download for OCR/STT` (+ update this doc's status line and `docs/NEXT_IMPLEMENTATION_QUEUE.md`).
6. `scripts/restart_gate.sh --check-only` → expect PASS.
7. **Explicit Owner approval.**
8. `scripts/restart_gate.sh --restart` — **fazle-core only**. Flag still OFF → production behavior identical to `7997cea`. Verify `/health`, pollers b1/b2/b3 ok.
9. **No bridge restart. No Meta change.**
10. Controlled live enable, one bridge at a time:
    a. Set `BRIDGE_MEDIA_ACQUISITION_ENABLED=true` (`.env` + `scripts/restart_gate.sh --restart`; or, if a runtime flag mechanism is wired — check `modules.wa_chat_frontend.get_runtime_setting` — prefer that to avoid a restart).
    b. Observe one bridge's organic media for a window; run the LIVE proof checklist (§7).
    c. If clean → leave enabled fleet-wide (the flag is global; there is no per-bridge flag today — if per-bridge control is wanted, add `BRIDGE_MEDIA_ACQUISITION_BRIDGES=bridge3` style allowlist in the same change).
11. Rollback criteria (any): duplicate reply observed; `[media:*]` or `[audio: unclear]` reaches RAG/Hermes; a bridge session disturbance; poll-loop latency regression (media download blocking text throughput); `MEDIA_UNPROCESSED`→routing leak; unexpected Hermes/business action from extracted text.

---

## 9. Rollback plan

| level | action | effect |
|---|---|---|
| **instant** | `BRIDGE_MEDIA_ACQUISITION_ENABLED=false` (runtime flag if available, else `.env` + `restart_gate.sh --restart`) | L980 reverts to the unconditional `media_unprocessed` skip — **byte-for-byte the `7997cea` behavior**. |
| **code** | `git revert <commit>` → fazle-core back to `7997cea`; `scripts/restart_gate.sh --restart` | removes `download_media` + the L980 change entirely. |
| **DB** | none | no schema change; `extracted_text` / status writes are forward-compatible and already used by other paths. |
| **bridges** | none | the bridge candidate (`c1eaf67`) is independent and stays — `direct_path` capture + `/api/download` are harmless when fazle-core doesn't call them. |
| **Meta** | none | never touched. |

Downloaded media files already written to `store/<chat_jid>/` are harmless and can stay (the bridge stored media there historically pre-2026-08-21). A retention/cleanup job is a **separate** follow-up (see Risks).

---

## 10. Risks

1. **Poll-loop latency:** `_poll_bridge` processes messages sequentially; a slow `/api/download` (large image/doc, CDN latency) blocks the rest of that bridge's batch. Mitigations: 45 s timeout + `asyncio.wait_for` + circuit breaker; media is a small fraction of traffic. Consider offloading acquisition to a worker queue if latency proves material (v2).
2. **Disk growth:** the bridge writes decrypted media into `store/<chat_jid>/` and never cleans it. Over months this grows. Follow-up: a retention job (e.g. delete media files older than N days after `extracted_text` is persisted). Not a v1 blocker; document.
3. **WhatsApp CDN expiry:** `direct_path` media is fetchable ~14 days. The **existing `[media:*]` backlog (since 2026-08-24) is not recoverable** — forward-only, as previously accepted.
4. **Transcription quality:** language mis-detection on short/noisy clips (observed in the STT proof). Route the transcript as-is; a garbled transcript typically yields `no_match`/clarification, not a wrong business action. Whisper language-detection tuning is a separate item.
5. **STT-failure draft volume:** unbounded voice-note failures could fill the admin draft queue. v2: per-conversation dedup on the `[STT FAILED]` draft.
6. **bridge1 store path is a symlink** (`whatsapp-bridge/store` → `/home/azim/bridges/bridge1/store`). The returned `/api/download` path is via the symlink; `os.path.isfile` and the extraction blocks' recomputed path both resolve correctly (canary-verified). Document so a future store-layout change is caught.
7. **`config.py` `bridge1_url`/`bridge2_url` defaults look swapped** (8082/8081). `.env` overrides make it correct today; a 1-line default fix should ride along (or be filed separately) to remove the footgun.
8. **Global flag only:** no per-bridge enable today. If a bridge-by-bridge live rollout is required, add an allowlist env in the same change.
9. **`process_message` is shared with Meta:** not modified, but the regression tests (§6/§7) are the tripwire if a future edit drifts.

---

## 11. Explicit implementation checklist (next session)

- [ ] `app/bridge.py`: add `BridgeClient.download_media(message_id, chat_jid) -> Optional[str]` (never raises; circuit-breaker aware; `BRIDGE_MEDIA_DOWNLOAD_TIMEOUT_S`).
- [ ] `modules/bridge_poller/__init__.py` L980: acquire-then-fall-through behind `BRIDGE_MEDIA_ACQUISITION_ENABLED` (default OFF); preconditions = flag ON + `filename` + `media_type ∈ {audio,ptt,image,document,application}` + not `unresolved:` + `phone not in _own_bridge_numbers()`.
- [ ] `modules/bridge_poller/__init__.py`: empty-extraction fallback → `MEDIA_EXTRACTION_EMPTY` skip (before the L1266 `from=` log); ensure `_mark_processed` not double-called.
- [ ] Confirm no `continue` path leaves `fazle_message_queue` non-terminal (mirror the 2026-07-09 stuck-row discipline).
- [ ] (optional) `modules/media_extract.py` shared `voice/image/document → text` helper; refactor `media_normalization` + `bridge_poller` onto it.
- [ ] `.env.example` + `docs/OPERATIONS.md`: document both env vars.
- [ ] `tests/unit/test_bridge_media_acquisition.py` (§7 CODE, items 1-22).
- [ ] `tests/integration/…`: one full `_poll_bridge` chain + Meta regression.
- [ ] Run regression suites (§7) + `scripts/restart_gate.sh --check-only`.
- [ ] `git diff` review — scope-limited to bridge/client/tests/docs.
- [ ] Commit; update this doc's status + `docs/NEXT_IMPLEMENTATION_QUEUE.md`.
- [ ] Owner approval → `scripts/restart_gate.sh --restart` (flag OFF) → verify identical behavior.
- [ ] Owner enables the flag; run LIVE proof on one bridge's organic media; then fleet.
- [ ] Update `MEMORY.md` / `project_bridge1_store_switch_maintenance_20260829.md`.

---

## 12. Final status

### As of 2026-08-30 implementation session

```
PRODUCTION CODE CHANGED = YES (dark — flag BRIDGE_MEDIA_ACQUISITION_ENABLED defaults OFF)
DATABASE CHANGED        = NO
BRIDGES RESTARTED       = NO
FAZLE-CORE RESTARTED    = NO  (stopped here for Owner approval)
META WHATSAPP CHANGED   = NO
```

**Files touched (implementation):**
- `app/bridge.py` — `BridgeClient.download_media()` + dedicated `media_breaker` (never trips the outbound-send breaker).
- `modules/bridge_poller/__init__.py` — per-message `_acquired` flag; L980 intercept becomes acquire-then-fall-through behind `BRIDGE_MEDIA_ACQUISITION_ENABLED` (default OFF); new `MEDIA_EXTRACTION_EMPTY` terminal skip for acquired-but-unextractable media.
- `tests/unit/test_bridge_media_acquisition.py` — 32 tests (download_media contract, breaker isolation, provenance, intercept structure, precondition mirror, routing-guard regression).
- `docs/OPERATIONS.md` — the two new env vars.
- `docs/BRIDGE_MEDIA_ACQUISITION_INTEGRATION_PLAN_2026-08-29.md` — this status.

**§2.5 optional shared `media_extract.py` helper: NOT done** — deliberately deferred (keeps scope to bridge/client/tests/docs, leaves the Meta `media_normalization` path untouched). Fall-through needs zero extractor changes.

**Config footgun (Risk 7) NOT fixed here** — `app/config.py` `bridge1_url`/`bridge2_url` defaults are still 8082/8081-swapped; `.env` overrides make production correct. Out of this change's scope; file separately.

**Verification done pre-deploy:** plan §7 regression suites + the new file = 225 passed, 6 xfailed (pre-existing), 0 failed. `scripts/restart_gate.sh --check-only` = all checks PASS except Check 1 (working-tree cleanliness — resolves on commit). Secret scan clean.

**Remaining (post Owner approval):** commit → `scripts/restart_gate.sh --restart` (flag OFF, verify identical behaviour) → Owner enables flag on one bridge's organic media → LIVE proof (§7) → fleet → update `MEMORY.md` / `project_bridge1_store_switch_maintenance_20260829.md`.

*(Recommended queue entry for `docs/NEXT_IMPLEMENTATION_QUEUE.md`: a Tier-1 line "Bridge media acquisition → fazle-core OCR/STT integration — IMPLEMENTED 2026-08-30, flag-gated OFF, awaiting restart; see `docs/BRIDGE_MEDIA_ACQUISITION_INTEGRATION_PLAN_2026-08-29.md`.")*
