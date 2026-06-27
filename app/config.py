"""
Fazle Core — Configuration
Reads from .env file. All settings in one place.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import field_validator
from typing import Optional
import re


class Settings(BaseSettings):
    # Database
    database_url: str

    # Redis
    redis_url: str = "redis://localhost:6379/9"

    # Ollama
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"

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

    # Bridge SQLite store paths — override in .env if the store directory moves.
    # Defaults match the current production layout on this VPS.
    bridge1_db_path: str = "/home/azim/whatsapp1/store/messages.db"
    bridge1_whatsapp_db_path: str = "/home/azim/whatsapp1/store/whatsapp.db"
    bridge2_db_path: str = "/home/azim/whatsapp2/store/messages.db"
    bridge2_whatsapp_db_path: str = "/home/azim/whatsapp2/store/whatsapp.db"

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

    # App
    app_port: int = 8200
    log_level: str = "INFO"
    debug: bool = False
    internal_api_key: str = ""

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

    # Company
    company_name: str = "Al-Aqsa Security Service"
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
    draft_always_names: str = "al-aqsa,escort,client,office,operation,tcis,gms,dalal"
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
            "unknown", "blocked",
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
