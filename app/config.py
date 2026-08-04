"""
Fazle Core — Configuration
Reads from .env file. All settings in one place.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import field_validator
import re


class Settings(BaseSettings):
    # Database
    database_url: str

    # Redis
    redis_url: str = "redis://localhost:6379/9"

    # Ollama
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "phi4-mini:latest"
    # Task-tier model routing (falls back to ollama_model when empty)
    ollama_runtime_model: str = "phi4-mini"
    ollama_business_model: str = "qwen3:8b"
    ollama_memory_model: str = "phi4-mini"
    ollama_general_model: str = "phi4-mini"
    ollama_deep_reasoning_model: str = "qwen3:8b"
    # Safe bounded concurrency for local inference paths
    ollama_infer_concurrency: int = 1
    ollama_chat_concurrency: int = 1
    # Shared daemon HTTP pool tuning
    ollama_http_max_connections: int = 16
    ollama_http_max_keepalive_connections: int = 8
    # Runtime tuning (bounded at call-site before use)
    ollama_num_ctx_default: int = 512
    ollama_num_ctx_classify: int = 256
    ollama_num_predict_reply: int = 50
    ollama_num_predict_recruitment: int = 50
    ollama_num_predict_chat: int = 100
    ollama_num_predict_rag: int = 150
    ollama_temperature_reply: float = 0.4
    ollama_temperature_recruitment: float = 0.2
    ollama_temperature_chat: float = 0.3
    ollama_temperature_rag: float = 0.3

    # GitHub Models (OpenAI-compatible — https://models.github.ai/inference)
    github_token: str = ""
    github_token_2: str = ""
    github_token_3: str = ""
    github_model_name: str = "openai/gpt-4o-mini"
    github_model_endpoint: str = "https://models.github.ai/inference"
    primary_ai_provider: str = "ollama"   # compatibility setting; runtime order prefers Ollama
    # When True, Ollama is skipped for customer-facing WhatsApp replies.
    # Fallbacks then run Groq → GitHub Models → polite holding message.
    # Ollama still runs for intent classification, RAG, and learning memory unless unavailable.
    ollama_reply_disabled: bool = False

    # Groq fallback provider (free tier: 14,400 req/day, 30 RPM)
    # Used after Ollama and before GitHub Models.
    # Get key at: https://console.groq.com/keys
    groq_api_key: str = ""
    groq_model_name: str = "llama-3.1-8b-instant"

    # Bridges
    bridge1_url: str = "http://localhost:8082"
    bridge1_number: str = ""
    bridge1_label: str = "HR"

    bridge2_url: str = "http://localhost:8081"
    bridge2_number: str = ""
    bridge2_label: str = "OPS"

    # bridge3 (REC2) — Phase E: Admin-reply-only reachability, wired 2026-08-04.
    # Not part of ADMIN_BRIDGE_NAME/BRIDGE_PRIORITY admin-notification failover;
    # see shared/bridge_orchestrator.py docstring.
    bridge3_url: str = "http://localhost:8083"
    bridge3_number: str = ""
    bridge3_label: str = "REC2"

    # Bridge SQLite store paths — override in .env if the store directory moves.
    # Defaults match the current production layout on this VPS.
    bridge1_db_path: str = "/home/azim/whatsapp1/store/messages.db"
    bridge1_whatsapp_db_path: str = "/home/azim/whatsapp1/store/whatsapp.db"
    bridge2_db_path: str = "/home/azim/whatsapp2/store/messages.db"
    bridge2_whatsapp_db_path: str = "/home/azim/whatsapp2/store/whatsapp.db"
    bridge3_db_path: str = "/home/azim/whatsapp3/store/messages.db"
    bridge3_whatsapp_db_path: str = "/home/azim/whatsapp3/store/whatsapp.db"

    # Meta WhatsApp
    meta_phone_number_id: str = ""
    meta_api_token: str = ""
    meta_api_url: str = "https://graph.facebook.com/v23.0"
    meta_app_secret: str = ""
    meta_verify_token: str = ""
    # Webhook auth rolls out in audit mode first so existing QR bridge sessions
    # are never disconnected. Enable enforcement only after bridges send HMAC.
    bridge_webhook_secret: str = ""
    webhook_signature_enforcement: bool = False
    meta_webhook_signature_enforcement: bool = True

    # Media
    media_processor_url: str = "http://localhost:8090"

    # LocationWhere adapter (Phase 2 / FG1)
    locationwhere_base_url: str = "http://127.0.0.1:3000/api/v1"
    locationwhere_jwt_token: str = ""
    locationwhere_employee_location_endpoint: str = "/location/live"
    locationwhere_device_status_endpoint: str = "/device/{device_id}/status"
    locationwhere_health_endpoint: str = "/gateway/status"
    location_adapter_timeout_s: float = 2.0
    location_adapter_retries: int = 2
    location_adapter_cache_ttl_s: int = 30
    location_adapter_circuit_max_failures: int = 5
    location_adapter_circuit_window_s: int = 120
    location_adapter_circuit_open_s: int = 60

    # App
    app_port: int = 8200
    log_level: str = "INFO"
    debug: bool = False
    internal_api_key: str = ""
    ai_console_chat_enabled: bool = False

    @field_validator("debug", mode="before")
    @classmethod
    def _parse_debug(cls, v):
        # Some runtime env files use DEBUG=release; treat as debug disabled.
        if isinstance(v, str) and v.strip().lower() in {"release", "prod", "production"}:
            return False
        return v

    # Safe mode — no customer replies when False, except the explicit
    # recruitment candidate-intake bypass below.
    auto_reply_enabled: bool = False
    # Operational/admin notifications are independent from customer auto-reply.
    internal_notifications_enabled: bool = True

    # Office Payroll Hardening (Owner decision, 2026-07-11): job_daily_payroll
    # must not create authoritative payroll for every active employee until
    # the Owner explicitly signs off on the new attendance-based eligibility
    # pipeline. Default False = dry-run-only (job still runs and logs a
    # preview summary, but calls compute_all_for_period only in dry-run mode,
    # never posting real wbom_payroll_runs rows for office staff). Flip to
    # True only after Owner sign-off per docs/NEXT_IMPLEMENTATION_QUEUE.md.
    office_payroll_authoritative_enabled: bool = False

    # Batch 11 — per-intent auto-reply allow-list.
    # When auto_reply_enabled=False, recruitment messages from non-admin
    # senders (job-trigger keyword OR active intake session) still auto-reply.
    # Everything else (escort, payment, attendance) stays draft-only.
    recruitment_autoreply_enabled: bool = True

    # Per-source auto-reply: comma-separated source names allowed to reply.
    # Sources NOT listed here are sync-only: messages are saved but no reply
    # and no draft is created.
    auto_reply_sources: str = "bridge1,bridge2,meta"

    # When False, _save_draft() is a no-op — no entries created in
    # fazle_draft_replies from inbound message processing.
    draft_creation_enabled: bool = True

    # Facebook Page credentials (Messenger + comments auto-reply)
    fb_page_access_token: str = ""
    fb_page_id: str = ""

    # Company (2026-07-22: single source of truth for the company's brand
    # name -- every WhatsApp reply, AI prompt, and frontend page should read
    # this instead of a hardcoded literal, so a future rebrand is a one-line
    # .env/default edit here, not a repo-wide find-and-replace. Previously
    # defined but never actually read anywhere (dead config) while ~20 files
    # each hardcoded their own copy of the name, independently drifting
    # across two rebrands -- see management_decisions.md 2026-07-22.
    company_name: str = "Al-Rifa'i Integrated Services"
    accountant_phone: str = ""
    admin_numbers: str = ""
    admin_meta_number: str = ""
    admin_bridge1_number: str = ""
    admin_bridge2_number: str = ""

    # Escort order configuration
    # Comma-separated phone numbers that are authorised to send escort client orders.
    # When EMPTY (default), all inbound phones are accepted (open mode).
    escort_client_phones: str = ""
    # Comma-separated bridge IDs trusted to submit new escort orders.
    # Example: "bridge1,bridge2,meta"
    escort_trusted_sources: str = "bridge1,bridge2,meta"

    # 2026-07-22: the one WhatsApp group whose ADMIN OUTBOUND messages (via
    # bridge2 only) are a trusted source of escort orders/confirmations --
    # "Al-Aqsa Escort Program Group", resolved from bridge2's own messages.db
    # `chats` table to its real, stable JID (120363428977781821@g.us as of
    # 2026-07-22 -- group *names* can change, this JID doesn't). Empty
    # (default) = feature fully inert, group messages stay excluded exactly
    # per the pre-existing 2026-04-27 "no groups" policy. Never hardcode the
    # JID in code -- always resolve it from the live bridge2 store and set
    # it here before enabling.
    escort_order_group_jid: str = ""

    # ── Draft-always gate ────────────────────────────────────────────────────
    # Contacts matching any of these criteria are ALWAYS drafted (never auto-sent)
    # even when AUTO_REPLY_ENABLED=true.
    # Roles: identity_role strings (e.g. accountant, vip_client)
    draft_always_roles: str = "accountant,client_escort_buyer,vip_client,repeat_client"
    # Phones: explicit E.164 phone numbers (without +)
    draft_always_phones: str = ""
    # Names: case-insensitive display name substrings
    # Default: if a saved contact name contains these tokens, never auto-send
    # (reply becomes a draft for admin review).
    draft_always_names: str = "al-aqsa,al-manaratayn,escort,client,office,operation,tcis,gms,dalal"
    # Prefixes: contact display_name starts with any of these words → always draft
    draft_name_prefixes: str = "client,escort,office,operation,tcis,gms,dalal"

    # ── AI Safety Mode (STEP 5) ──────────────────────────────────────────
    # When True: long replies, low-confidence, and uncertain-intent auto-replies
    # become drafts instead of being sent. Useful during production incidents.
    ai_safe_mode: bool = False

    # ── Reviewed Reply Memory (Batch 26) ─────────────────────────────────
    # When True: admin-edited drafts are persisted for future reuse; lookup
    # runs between KB and AI fallback. Safe to disable with =false at any time.
    reviewed_reply_memory_enabled: bool = True

    # ── Per-contact risk levels (STEP 6) ───────────────────────────────
    # Format: "phone:level,phone:level,..."
    # Levels: trusted | monitored | admin_review_only
    # admin_review_only → ALL AI replies require manual approval (always drafted)
    contact_risk_levels: str = ""

    # Draft lifecycle
    # Hours after which a pending payment draft is auto-expired by the scheduler.
    draft_ttl_hours: int = 24

    # Phase B — Conversation Memory Engine (2026-07-19). All default OFF/
    # shadow -- Part 13's explicit "shadow mode first, no autonomous
    # confirmation" requirement. TTL configurable by domain per Part 4.5;
    # never hardcode a business TTL inside parser functions.
    conversation_memory_enabled: bool = False
    whatsapp_thread_reconstruction_enabled: bool = False
    multi_message_escort_parser_enabled: bool = False
    bangla_semantic_parser_enabled: bool = False
    conversation_ttl_escort_minutes: int = 30
    conversation_ttl_recruitment_hours: int = 24
    conversation_ttl_general_hours: int = 2
    # Stage 4 fail-safe requirement: shadow observation is awaited inline
    # in the real message-routing path, so it must be bounded -- a slow
    # DB or slow extraction must never be able to hang real routing.
    conversation_memory_shadow_timeout_seconds: float = 5.0

    # Route B conversation handoff (2026-07-22, Owner-approved staged rollout).
    # Master switch for Stage 1 (recruitment handoff) and Stage 2 (employee/
    # former-employee claim admin-review path) — both OFF by default until
    # the implementation report is separately reviewed and approved.
    route_b_handoff_enabled: bool = False
    # Stage 3 (escort/security_guard multi-message operational handoff) is
    # gated by its own, separate flag on top of the master switch above —
    # "prepared but not activated until separately reviewed" per Owner
    # decision, distinct from Stage 1/2's review.
    route_b_escort_handoff_enabled: bool = False
    # Cooldown window for the handoff-audit dedup check (idempotency/loop
    # prevention) — same number of hours a repeated ambiguous claim from the
    # same phone is suppressed from creating a second admin draft.
    route_b_handoff_cooldown_hours: int = 6

    # Fazle Payroll Engine (FPE)
    fpe_sync_chat_jids: str = ""   # comma-separated target JIDs for historical sync
    # Phones authorized to send "Cash <phone> <name> <amount>" commands
    fpe_cash_authorized_phones: str = ""
    # Phones authorized to send "Income <phone> <name> <amount>" commands
    fpe_income_authorized_phones: str = ""

    @property
    def fpe_cash_authorized_phone_list(self) -> list[str]:
        return [n.strip() for n in self.fpe_cash_authorized_phones.split(",") if n.strip()]

    @property
    def fpe_income_authorized_phone_list(self) -> list[str]:
        return [n.strip() for n in self.fpe_income_authorized_phones.split(",") if n.strip()]

    @property
    def admin_number_list(self) -> list[str]:
        return [n.strip() for n in self.admin_numbers.split(",") if n.strip()]

    @property
    def auto_reply_source_list(self) -> list[str]:
        return [s.strip() for s in self.auto_reply_sources.split(",") if s.strip()]

    @property
    def draft_always_role_set(self) -> frozenset:
        return frozenset(r.strip().lower() for r in self.draft_always_roles.split(",") if r.strip())

    @property
    def draft_always_role_name_tokens(self) -> list:
        """Non-role values kept in DRAFT_ALWAYS_ROLES for contact-name matching."""
        known_roles = {
            "accountant", "client_escort_buyer", "vip_client", "repeat_client",
            "admin", "family", "vendor", "employee", "supervisor", "candidate",
            "unknown",
        }
        return [
            r.strip().lower()
            for r in self.draft_always_roles.split(",")
            if r.strip() and r.strip().lower() not in known_roles
        ]

    @property
    def draft_always_phone_set(self) -> frozenset:
        return frozenset(p.strip() for p in self.draft_always_phones.split(",") if p.strip())

    @property
    def draft_always_name_list(self) -> list:
        tokens = [n.strip().lower() for n in self.draft_always_names.split(",") if n.strip()]
        tokens.extend(t for t in self.draft_always_role_name_tokens if t not in tokens)
        return tokens

    @property
    def draft_name_prefix_list(self) -> list:
        prefixes = [p.strip().lower() for p in self.draft_name_prefixes.split(",") if p.strip()]
        for token in self.draft_always_role_name_tokens:
            if token and token not in prefixes:
                prefixes.append(token)
        return prefixes

    @property
    def contact_risk_map(self) -> dict:
        """Parse contact_risk_levels into {phone: level} mapping (STEP 6)."""
        result: dict = {}
        for entry in self.contact_risk_levels.split(","):
            entry = entry.strip()
            if ":" in entry:
                phone, _, level = entry.partition(":")
                phone, level = phone.strip(), level.strip().lower()
                if phone and level:
                    result[phone] = level
        return result

    class Config:
        # Prefer repo-local `.env` by default (production path is /home/azim/core/.env).
        # BaseSettings still reads from real environment variables if provided by systemd/Docker.
        env_file = str(Path(__file__).resolve().parents[1] / ".env")
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def update_repo_env_value(key: str, value: str) -> None:
    """Update one key in the repo-local `.env` file and clear cached settings."""
    env_path = Path(Settings.Config.env_file)
    text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    new_line = f"{key}={value}"
    if pattern.search(text):
        text = pattern.sub(new_line, text)
    else:
        text = text.rstrip() + ("\n" if text.strip() else "") + new_line + "\n"
    env_path.write_text(text, encoding="utf-8")
    get_settings.cache_clear()
