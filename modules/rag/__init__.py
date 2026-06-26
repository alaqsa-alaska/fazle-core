"""
Fazle Core — Batch 21
Lightweight Retrieval-Augmented Generation (RAG) layer.

Design constraints:
  • No external embedding/LLM service (offline-first VPS deployment)
  • Bilingual corpus (Bangla + English) — BM25 with Unicode-aware tokenizer
  • Small corpus (<10 MB) → in-process index, rebuild in <1s
  • Deterministic, no network calls during query

Sources indexed:
  1. Plain-text files under fazle-core/resources/*.txt
  2. Active rows of fazle_knowledge_base (key, reply_text)

Public API (all async, safe to call from FastAPI handlers):
  • await build_index()              → (re)build the in-memory index
  • await ensure_index()             → build if not yet built
  • await search(q, k=5, min_score=0.0, role="candidate") → list[dict]
  • await stats()                    → diagnostics dict
  • await answer(q, k=3, min_score=1.0) → {answer, citations} or None
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import re
import time
from collections import Counter, OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from app.database import fetch_all

log = logging.getLogger("fazle.rag")

# ── Configuration ──────────────────────────────────────────────────────────────
RESOURCES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "resources",
)
CHUNK_SIZE = int(os.getenv("RAG_CHUNK_SIZE", "320"))      # chars per chunk
CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "60"))  # chars overlap
MIN_TOKEN_LEN = 2

# BM25 params
_K1 = 1.5
_B = 0.75

# ── HYBRID RAG — Milestone 1 Scaffold ─────────────────────────────────────────
# All new symbols are inert while HYBRID_SEARCH_ENABLED=false (the default).
# Imports of sentence_transformers and qdrant_client are deferred inside each
# lazy function so no import-time side effects occur when the flag is off.
# No existing function (build_index, search, answer, rebuild_index) is modified.

HYBRID_SEARCH_ENABLED: bool = os.getenv("HYBRID_SEARCH_ENABLED", "false").lower() == "true"

QDRANT_STORE_PATH: str = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "store", "qdrant",
)
QDRANT_COLLECTION_NAME: str = "fazle_rag_chunks"
VECTOR_DIM: int = 384           # paraphrase-multilingual-MiniLM-L12-v2
EMBEDDING_MODEL_NAME: str = "paraphrase-multilingual-MiniLM-L12-v2"
_QDRANT_UPSERT_BATCH: int = 100  # points per client.upsert() call

# ── Milestone 3.6 — Qdrant Server Mode support ────────────────────────────────
# When QDRANT_HOST is set, _get_qdrant_client() uses server mode.
# When unset (default), embedded file-based mode is used (original behaviour).
# Do NOT set these without management approval of production .env change.
_QDRANT_HOST: str = os.getenv("QDRANT_HOST", "")
_QDRANT_PORT: int = int(os.getenv("QDRANT_PORT", "6333"))

# ── Milestone 3.8 — Runtime Optimization ──────────────────────────────────────
# Query embedding cache: exact-match only. Zero quality change.
# Bounded LRU to prevent unbounded RAM growth. Configurable via env var.
_EMBEDDING_CACHE_MAX_SIZE: int = int(os.getenv("EMBEDDING_CACHE_MAX_SIZE", "2000"))


class _EmbeddingLRUCache:
    """Bounded LRU cache: sha1(query_bytes) → ndarray[384].

    Thread-safe for single-writer / concurrent-reader access under Python GIL.
    OrderedDict move_to_end + popitem(last=False) gives O(1) LRU eviction.
    """
    __slots__ = ("_cache", "_maxsize")

    def __init__(self, maxsize: int) -> None:
        self._cache: OrderedDict[bytes, Any] = OrderedDict()
        self._maxsize: int = maxsize

    def get(self, key: bytes) -> Optional[Any]:
        if key not in self._cache:
            return None
        self._cache.move_to_end(key)
        return self._cache[key]

    def put(self, key: bytes, value: Any) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
        else:
            if len(self._cache) >= self._maxsize:
                self._cache.popitem(last=False)  # evict least-recently-used
            self._cache[key] = value

    def __len__(self) -> int:
        return len(self._cache)


_query_embedding_cache: _EmbeddingLRUCache = _EmbeddingLRUCache(
    maxsize=_EMBEDDING_CACHE_MAX_SIZE
)

# Dedicated 1-thread executor for MiniLM encoding.
# CPU-bound encoding uses exactly 1 thread to prevent cross-call contention
# on this shared-VPS AMD EPYC (8-thread default caused 200ms+ spikes).
# Qdrant network I/O uses the default asyncio thread pool (I/O-bound, different resource).
_encoding_executor: ThreadPoolExecutor = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="rag-encoder",
)

# Production-approved seed queries pre-loaded into the embedding cache at build time.
# Append-only. Never remove an entry that may still appear in real WhatsApp traffic.
# Covers the highest-frequency intents observed across Recruitment / Payroll /
# Attendance / Escort / Office-Address categories.
_SEED_QUERIES: tuple[str, ...] = (
    # Recruitment (Bangla)
    "কোন পদে নিয়োগ চলছে", "নিয়োগ বিজ্ঞপ্তি", "চাকরির সুযোগ",
    "কোন পদে লোক নেওয়া হচ্ছে", "নিয়োগ হচ্ছে", "পদ শূন্য",
    # Payroll (Bangla)
    "বেতন কত", "বেতন কবে পাব", "বেতন কখন দেবে", "মাসিক বেতন",
    # Payroll (English)
    "salary", "salary amount", "salary payment date",
    # Attendance / Leave
    "হাজিরা", "ছুটি", "অনুপস্থিতি", "attendance",
    # Escort
    "এসকর্ট ডিউটি", "এসকর্ট কাজ", "এসকর্ট নিয়ম", "escort duty",
    # Office / Address
    "অফিসের ঠিকানা", "অফিস কোথায়", "কোম্পানির ঠিকানা", "office address",
    # Cross-language common
    "recruitment", "নিয়োগ",
)

# ── END Milestone 3.8 constants ───────────────────────────────────────────────

# ── Phase 3 — Role-Aware Visibility ───────────────────────────────────────────
# Maps identity_brain role names to the access tier used for KB filtering.
# Non-mapped roles (e.g. unexpected strings) default to "candidate" (most restrictive).
_ROLE_ACCESS_LEVEL: dict[str, str] = {
    "candidate":           "candidate",
    "new_lead":            "candidate",   # unknown sender; treat as candidate
    "unknown":             "candidate",   # no identity match; most restrictive
    "employee":            "employee",
    "supervisor":          "supervisor",
    "accountant":          "accountant",
    "repeat_client":       "employee",    # known client → employee-level
    "vendor":              "employee",
    "family":              "employee",
    "client":              "employee",
    "admin":               "admin",
    "developer":           "developer",
}
# KB category → set of access tiers allowed to receive this content.
# Partial match (missing category) defaults to _DEFAULT_ALLOWED (all tiers = current behaviour).
_CATEGORY_ROLE_MAP: dict[str, frozenset[str]] = {
    "recruitment": frozenset({"candidate", "employee", "supervisor", "accountant", "admin", "developer"}),
    "faq":         frozenset({"candidate", "employee", "supervisor", "accountant", "admin", "developer"}),
    "general":     frozenset({"candidate", "employee", "supervisor", "accountant", "admin", "developer"}),
    "office":      frozenset({"candidate", "employee", "supervisor", "accountant", "admin", "developer"}),
    "policy":      frozenset({"candidate", "employee", "supervisor", "accountant", "admin", "developer"}),
    "complaint":   frozenset({"employee", "supervisor", "accountant", "admin", "developer"}),
    "payment":     frozenset({"employee", "supervisor", "accountant", "admin", "developer"}),
    "payroll":     frozenset({"employee", "supervisor", "accountant", "admin", "developer"}),
    "salary":      frozenset({"employee", "supervisor", "accountant", "admin", "developer"}),
    "attendance":  frozenset({"employee", "supervisor", "accountant", "admin", "developer"}),
    "advance":     frozenset({"employee", "supervisor", "accountant", "admin", "developer"}),
    "escort":      frozenset({"employee", "supervisor", "accountant", "admin", "developer"}),
    "admin":       frozenset({"admin", "developer"}),
    "financial":   frozenset({"accountant", "admin", "developer"}),
    "fpe":         frozenset({"admin", "developer"}),
    "internal":    frozenset({"admin", "developer"}),
}
# Default: allow all tiers (preserves current behaviour for unclassified content).
_DEFAULT_ALLOWED: frozenset[str] = frozenset({
    "candidate", "employee", "supervisor", "accountant", "admin", "developer"
})
# ── END Phase 3 constants ──────────────────────────────────────────────────────

# Module-level singletons — None until first lazy call.
_encoder_instance = None        # SentenceTransformer; loaded once, stays resident
_qdrant_client_instance = None  # QdrantClient; server or embedded, opened once


def _get_encoder():
    """
    Load SentenceTransformer model once (blocking).
    Must be called from a thread-pool executor — never called at import time or
    during normal service operation while HYBRID_SEARCH_ENABLED=false.
    Model download (~118 MB) happens only on the very first call; cached afterwards.
    """
    global _encoder_instance
    if _encoder_instance is None:
        from sentence_transformers import SentenceTransformer  # deferred import
        log.info("[rag] loading embedding model %r ...", EMBEDDING_MODEL_NAME)
        _encoder_instance = SentenceTransformer(EMBEDDING_MODEL_NAME)
        log.info("[rag] embedding model loaded OK")
    return _encoder_instance


def _get_qdrant_client():
    """
    Open QdrantClient in server mode (QDRANT_HOST set) or embedded fallback.
    Server mode: QdrantClient(host=..., port=...) — no file lock, low latency.
    Embedded fallback: QdrantClient(path=...) — original behaviour, always safe.
    If server is unreachable, logs warning and falls back to embedded automatically.
    Must only be called when HYBRID_SEARCH_ENABLED=true.
    """
    global _qdrant_client_instance
    if _qdrant_client_instance is None:
        from qdrant_client import QdrantClient  # deferred import
        if _QDRANT_HOST:
            try:
                client = QdrantClient(host=_QDRANT_HOST, port=_QDRANT_PORT)
                client.get_collections()  # connectivity probe — raises if unreachable
                _qdrant_client_instance = client
                log.info("[rag] Qdrant client → server mode %s:%d", _QDRANT_HOST, _QDRANT_PORT)
            except Exception as exc:
                log.warning(
                    "[HYBRID] Falling back to embedded Qdrant. server=%s:%d error_type=%s error=%s",
                    _QDRANT_HOST, _QDRANT_PORT, type(exc).__name__, exc,
                )
                os.makedirs(QDRANT_STORE_PATH, exist_ok=True)
                _qdrant_client_instance = QdrantClient(path=QDRANT_STORE_PATH)
                log.info("[rag] Qdrant client → embedded mode %r", QDRANT_STORE_PATH)
        else:
            os.makedirs(QDRANT_STORE_PATH, exist_ok=True)
            _qdrant_client_instance = QdrantClient(path=QDRANT_STORE_PATH)
            log.info("[rag] Qdrant client → embedded mode %r", QDRANT_STORE_PATH)
    return _qdrant_client_instance


def _qdrant_ensure_collection() -> None:
    """
    Create the fazle_rag_chunks collection if it does not already exist.
    Idempotent — safe to call on every build_index() when flag is true.
    Must only be called when HYBRID_SEARCH_ENABLED=true.
    """
    from qdrant_client.models import VectorParams, Distance  # deferred import
    client = _get_qdrant_client()
    existing = [c.name for c in client.get_collections().collections]
    if QDRANT_COLLECTION_NAME not in existing:
        client.create_collection(
            collection_name=QDRANT_COLLECTION_NAME,
            vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
        )
        log.info("[rag] Qdrant collection %r created", QDRANT_COLLECTION_NAME)
    else:
        log.debug("[rag] Qdrant collection %r already exists", QDRANT_COLLECTION_NAME)


async def qdrant_status() -> dict[str, Any]:
    """
    Endpoint-oriented Qdrant health/status probe.

    This intentionally checks the configured Qdrant API/client path rather than
    relying on an OS service name, because production may run Qdrant in a
    container or embedded fallback.
    """
    if not HYBRID_SEARCH_ENABLED:
        return {
            "status": "disabled",
            "hybrid_search_enabled": False,
            "collection": QDRANT_COLLECTION_NAME,
        }

    try:
        client = _get_qdrant_client()

        def _probe() -> dict[str, Any]:
            collections = client.get_collections().collections
            names = [c.name for c in collections]
            out: dict[str, Any] = {
                "status": "ok",
                "hybrid_search_enabled": True,
                "mode": "server" if _QDRANT_HOST else "embedded",
                "host": _QDRANT_HOST or None,
                "port": _QDRANT_PORT if _QDRANT_HOST else None,
                "collection": QDRANT_COLLECTION_NAME,
                "collection_exists": QDRANT_COLLECTION_NAME in names,
                "collections": names,
            }
            if QDRANT_COLLECTION_NAME in names:
                info = client.get_collection(QDRANT_COLLECTION_NAME)
                out["points_count"] = int(getattr(info, "points_count", 0) or 0)
                out["vectors_count"] = int(getattr(info, "vectors_count", 0) or 0)
                out["optimizer_status"] = str(getattr(info, "optimizer_status", "") or "")
            return out

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _probe)
    except Exception as exc:
        return {
            "status": "degraded",
            "hybrid_search_enabled": True,
            "mode": "server" if _QDRANT_HOST else "embedded",
            "host": _QDRANT_HOST or None,
            "port": _QDRANT_PORT if _QDRANT_HOST else None,
            "collection": QDRANT_COLLECTION_NAME,
            "error": str(exc)[:300],
        }

# ── END HYBRID RAG Milestone 1 Scaffold ───────────────────────────────────────

# ── PATCH 1: Ingestion exclude-lists ──────────────────────────────────────────
# Directories (relative to RESOURCES_DIR) that must never be indexed
_EXCLUDED_DIRS: frozenset = frozenset({
    "_internal_archived", "_internal", "prompts", "debug", "tests",
    "drafts", "internal", "ai", "training", "examples", "temp",
})
# Any filename containing these keywords (case-insensitive) is blocked
_EXCLUDED_NAME_KEYWORDS: tuple = (
    "analysis", "prompt", "intent", "debug", "test", "sample",
    "chain", "reasoning", "internal", "archived",
    # system_context.txt is VPS/server documentation — never show to customers
    "system_context",
)
# PATCH 3: Backup/temp file guard — substrings that mark non-authoritative files.
# Blocks *.bak*, *.backup*, *.old*, *.tmp* regardless of whether they end in .txt.
# Current .bak.20260526-* files are already caught by the endswith(".txt") check;
# this guard closes the remaining gap (e.g. file.bak.txt, file.backup.txt).
_EXCLUDED_FILE_PATTERNS: tuple = (
    ".bak", ".backup", ".old", ".tmp",
)

# ── PATCH 2: Chunk-level safety patterns ──────────────────────────────────────
# A chunk containing any of these is NOT safe to send to customers
_CHUNK_UNSAFE_PATTERNS: tuple = (
    "এআই-এর বিশ্লেষণ", "এআই-এর ইনটেন্ট", "| :--- |",
    "chain_of_thought", "Intent)", "প্রার্থীর মেসেজ",
    "প্রার্থীর সম্ভাব্য প্রশ্ন", "Semantic Analysis", "Tokenization",
    "RAG pipeline", "LLM pipeline", "prompt template", "OCR raw",
    "বিশ্লেষণ (Intent)", "reasoning_trace",
    # PATCH 4: Additional internal instruction markers found in resource files
    "AI ব্যবহারের জন্য বিশেষ নির্দেশিকা",
    "এআই ব্যবহারের নির্দেশিকা",
    "CASE A —", "CASE B —",
    "এআই-এর ইনটেন্ট অনুধাবন",
    "এআই-এর প্রতিক্রিয়া (Action)",
    "AI সিস্টেমের বিশেষত্ব",
    "AI-এর একটি বিশেষ অটো-রিপ্লাই",
    "অটো-রিপ্লাই সিস্টেমের জন্য",
    # PATCH 5 (Phase 1C QA): Inline AI answer annotations found in policy file
    "এআই উত্তর",        # "এআই উত্তর (প্রশ্ন):" inline annotations in employee_policy file
    "AI উত্তর",
    "উত্তর — প্রার্থী",  # "এআই উত্তর — প্রার্থী (Candidate):"
    "উত্তর — কর্মচারী", # "এআই উত্তর — কর্মচারী:"
    # PATCH 5: English AI instruction text found in Cash Payment Accountant-Admin.txt
    "The AI manages",
    "that the AI tracks",
    "the AI tracks",
    "AI manages internal",
)


def _is_chunk_safe(text: str) -> bool:
    """Return False if this chunk contains internal/analysis content."""
    return not any(p in text for p in _CHUNK_UNSAFE_PATTERNS)


# ── Tokenizer (Unicode-friendly: works for Bangla + Latin) ─────────────────────
# Keep letters/digits, drop everything else. Bangla codepoints 0x0980-0x09FF.
_TOKEN_RE = re.compile(r"[A-Za-z0-9\u0980-\u09FF]+", re.UNICODE)


# True function words (grammatical glue) that carry no document-discriminating signal.
# Rule: only include a word if removing it cannot hurt recall for ANY plausible query.
# "লোক","পদে","নেওয়া","হচ্ছে" were here before but caused recall loss on recruitment
# queries like "কোন পদে লোক নেওয়া হচ্ছে" — removed because they ARE content words.
_STOP_WORDS: frozenset = frozenset({
    # Bangla conjunctions / particles
    "এবং","বা","কিন্তু","তবে","যদি","তাহলে","তখন","কারণ","তাই","আর",
    # Bangla negations & auxiliary verbs (no standalone meaning in search)
    # হচ্ছে/হচ্ছেন = progressive marker — appears in ALL intents, not discriminating
    "না","নয়","নি","হয়","হবে","হচ্ছে","হচ্ছেন","হচ্ছিল","হয়েছে","করা","করে","করেন",
    "করবেন","করুন","করতে","আছে","ছিল","থাকে","থাকবে","দিতে","যাবে","দেওয়া",
    # Bangla pronouns
    "আমি","আমার","আমরা","আমাদের","আপনি","আপনার","আপনারা","তুমি",
    "তোমার","সে","তার","তারা","তাদের",
    # Bangla demonstratives / deictic words
    "এই","এটি","এটা","এখানে","এখন","সেই","সেটা","সেখানে",
    # Bangla question words (do NOT filter কোন — it appears in "কোন পদে")
    "কি","কী","কিভাবে","কেন","কখন","কোনো","কে","কার",
    # Bangla relative / indefinite pronouns
    "যা","যে","যার","যখন","কিছু","সব","সকল",
    # Bangla postpositions
    "থেকে","দিয়ে","উপর","নিচে","ভেতরে","জন্য","কাছে","সাথে","মতো","মধ্যে","বিষয়ে",
    # Bangla quantifiers/adverbs (no discriminating power)
    "একটি","একটা","একজন","অনেক","বেশি","কম","খুব","বেশ",
    # English function words
    "the","a","an","and","or","but","in","on","at","to","for","of",
    "with","by","from","is","are","was","were","be","been","have","has",
    "had","do","does","did","will","would","can","could","this","that",
    "these","those","it","its","we","our","you","your","he","she","they",
    "their","my","me","not","no","if","as","so","up","out","about","than",
})


def _tokenize(text: str) -> list[str]:
    if not text:
        return []
    toks = _TOKEN_RE.findall(text.lower())
    return [t for t in toks if len(t) >= MIN_TOKEN_LEN and t not in _STOP_WORDS]


# ── Chunker ────────────────────────────────────────────────────────────────────
def _chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks = []
    step = max(1, size - overlap)
    for i in range(0, len(text), step):
        piece = text[i : i + size].strip()
        if piece:
            chunks.append(piece)
        if i + size >= len(text):
            break
    return chunks


# ── Index state ────────────────────────────────────────────────────────────────
class _Index:
    def __init__(self):
        self.docs: list[dict[str, Any]] = []   # [{source, title, text, tokens}]
        self.df: Counter = Counter()           # token -> # docs containing it
        self.avgdl: float = 0.0
        self.built_at: Optional[float] = None
        self.build_ms: Optional[int] = None
        self.lock = asyncio.Lock()

    def reset(self):
        self.docs = []
        self.df = Counter()
        self.avgdl = 0.0


_IDX = _Index()

# ── STEP 2: Recent-searches audit ring buffer (last 50 queries) ───────────────
_RECENT_SEARCHES: deque = deque(maxlen=50)


# ── Source loaders ─────────────────────────────────────────────────────────────
def _load_resource_files() -> list[tuple[str, str, str, bool, frozenset[str]]]:
    """Return list of (source_id, title, text, safe_for_customer, allowed_roles) for each approved file."""
    out: list[tuple[str, str, str, bool, frozenset[str]]] = []
    if not os.path.isdir(RESOURCES_DIR):
        log.warning(f"[rag] resources dir missing: {RESOURCES_DIR}")
        return out
    for name in sorted(os.listdir(RESOURCES_DIR)):
        path = os.path.join(RESOURCES_DIR, name)
        # PATCH 1: skip subdirectories — log internal ones
        if os.path.isdir(path):
            if name in _EXCLUDED_DIRS or name.startswith("_"):
                log.info(f"[RAG_FILE_SKIPPED_INTERNAL] dir={name!r} reason=excluded_dir")
            continue
        if not name.lower().endswith(".txt"):
            continue
        # PATCH 3: skip backup/temp files by substring pattern
        name_lower = name.lower()
        skip_pat = next((p for p in _EXCLUDED_FILE_PATTERNS if p in name_lower), None)
        if skip_pat:
            log.info(
                f"[RAG_FILE_SKIPPED_BACKUP] file={name!r} reason=backup_pattern:{skip_pat}"
            )
            continue
        # PATCH 1: skip files with internal keywords in filename
        skip_kw = next((kw for kw in _EXCLUDED_NAME_KEYWORDS if kw in name_lower), None)
        if skip_kw:
            log.warning(
                f"[RAG_FILE_SKIPPED_INTERNAL] file={name!r} reason=keyword:{skip_kw}"
            )
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except Exception as e:
            log.warning(f"[rag] failed to read {name}: {e}")
            continue
        if not text.strip():
            continue
        title = name.rsplit(".", 1)[0]
        out.append((f"file:{name}", title, text, True, _DEFAULT_ALLOWED))
    return out


async def _load_kb_rows() -> list[tuple[str, str, str, bool, frozenset[str]]]:
    """Return list of (source_id, title, text, safe_for_customer, allowed_roles) for KB rows."""
    out: list[tuple[str, str, str, bool, frozenset[str]]] = []
    try:
        rows = await fetch_all(
            "SELECT key, COALESCE(category,'') AS category, reply_text "
            "FROM fazle_knowledge_base WHERE is_active = true",
        )
    except Exception as e:
        log.debug(f"[rag] KB table unavailable: {e}")
        return out
    for r in rows:
        key = r.get("key") or "kb"
        cat = (r.get("category") or "").lower()
        title = f"{key} ({cat or 'kb'})"
        text = r.get("reply_text") or ""
        allowed = _CATEGORY_ROLE_MAP.get(cat, _DEFAULT_ALLOWED)
        if text.strip():
            out.append((f"kb:{key}", title, text, True, allowed))
    return out


# ── HYBRID RAG — Milestone 2: Vector Index Build Path ─────────────────────────

async def _build_qdrant_index(safe_chunks: list[dict[str, Any]]) -> None:
    """
    Build the Qdrant vector index from BM25-verified safe chunks.

    CONTRACT:
      - Called ONLY after BM25 build has already succeeded.
      - Called ONLY when HYBRID_SEARCH_ENABLED=True.
      - Never raises — all exceptions are caught, logged, and swallowed.
      - BM25 search continues uninterrupted if this function fails at any point.
      - Runs under the caller's _IDX.lock for index consistency.
      - All heavy imports are deferred (no import-time side effects).
      - Temporary allocations (texts, vectors, points) are explicitly released.

    Sequence:
      1. Validate input
      2. Load encoder singleton via thread-pool executor (non-blocking)
      3. Encode all chunk texts via thread-pool executor (CPU-bound)
      4. Drop existing Qdrant collection only if it exists (never delete blindly)
      5. Recreate collection fresh
      6. Build PointStruct list with deterministic UUIDs
      7. Upsert in batches of _QDRANT_UPSERT_BATCH
      8. Release temporary allocations
      9. Log completion metrics
    """
    if not safe_chunks:
        log.info("[HYBRID] no safe chunks available — Qdrant build skipped")
        return

    t_start: float = time.monotonic()
    log.info("[HYBRID] Build started — chunks=%d", len(safe_chunks))

    try:
        loop = asyncio.get_running_loop()

        # ── Step 1: Load encoder singleton ────────────────────────────────────
        # _get_encoder() is blocking (may load ~118 MB model on first call).
        # Uses dedicated _encoding_executor (1 thread) — same thread as query
        # encoding so the model stays warm and no context switch occurs.
        encoder = await loop.run_in_executor(_encoding_executor, _get_encoder)
        t_encoder_done: float = time.monotonic()
        log.info(
            "[HYBRID] Encoder loaded — elapsed_ms=%d",
            int((t_encoder_done - t_start) * 1000),
        )

        # ── Step 2: Encode all chunk texts (CPU-bound) ─────────────────────────
        texts: list[str] = [c["text"] for c in safe_chunks]

        def _encode_sync(enc: Any, chunk_texts: list[str]) -> Any:
            """Synchronous encode helper — executed in thread-pool executor."""
            return enc.encode(
                chunk_texts,
                batch_size=32,
                show_progress_bar=False,
                convert_to_numpy=True,
            )

        vectors = await loop.run_in_executor(_encoding_executor, _encode_sync, encoder, texts)
        del texts  # release string list; vectors now held in numpy array
        t_encode_done: float = time.monotonic()
        log.info(
            "[HYBRID] Encoding complete — chunks=%d shape=%s encode_ms=%d",
            len(safe_chunks),
            list(vectors.shape),
            int((t_encode_done - t_encoder_done) * 1000),
        )

        # ── Step 3: Safe Qdrant collection rebuild ─────────────────────────────
        log.info("[HYBRID] Qdrant rebuild started")
        client = _get_qdrant_client()

        # Verify collection exists BEFORE deleting — never delete accidentally.
        existing_names: list[str] = [
            c.name for c in client.get_collections().collections
        ]
        if QDRANT_COLLECTION_NAME in existing_names:
            client.delete_collection(QDRANT_COLLECTION_NAME)
            log.info("[HYBRID] Qdrant collection dropped — rebuilding fresh")

        _qdrant_ensure_collection()
        log.info("[HYBRID] Qdrant collection ready")

        # ── Step 4: Build PointStruct list with deterministic UUIDs ───────────
        # Deferred imports — only executed when HYBRID_SEARCH_ENABLED=True.
        from qdrant_client.models import PointStruct  # noqa: PLC0415
        import uuid as _uuid                           # noqa: PLC0415

        indexed_at: float = time.time()
        points: list[PointStruct] = []

        for chunk, vec in zip(safe_chunks, vectors):
            # Deterministic UUID: same source+chunk_idx always → same UUID.
            # Enables upsert semantics and future incremental sync.
            point_id: str = str(_uuid.uuid5(
                _uuid.NAMESPACE_DNS,
                f"{chunk['source']}:{chunk['chunk_idx']}",
            ))
            points.append(PointStruct(
                id=point_id,
                vector=vec.tolist(),  # numpy float32 row → Python list[float]
                payload={
                    "source":            chunk["source"],
                    "title":             chunk["title"],
                    "chunk_idx":         chunk["chunk_idx"],
                    "text":              chunk["text"],
                    "safe_for_customer": True,           # invariant: unsafe chunks never reach here
                    "source_kind":       chunk["source"].split(":", 1)[0],  # "file" | "kb"
                    "indexed_at":        indexed_at,
                    "allowed_roles":     sorted(chunk.get("allowed_roles", _DEFAULT_ALLOWED)),  # Phase 3
                },
            ))

        total_points: int = len(points)
        del vectors  # release numpy array — payload data now serialised in points list

        # ── Step 5: Upsert in batches ──────────────────────────────────────────
        t_upsert_start: float = time.monotonic()
        for i in range(0, total_points, _QDRANT_UPSERT_BATCH):
            client.upsert(
                collection_name=QDRANT_COLLECTION_NAME,
                points=points[i : i + _QDRANT_UPSERT_BATCH],
            )
        t_upsert_done: float = time.monotonic()
        log.info(
            "[HYBRID] Upsert completed — points=%d upsert_ms=%d",
            total_points,
            int((t_upsert_done - t_upsert_start) * 1000),
        )

        del points  # release serialised point list

        # ── Step 6: Final metrics ──────────────────────────────────────────────
        log.info(
            "[HYBRID] Build completed — points=%d total_ms=%d "
            "encode_ms=%d upsert_ms=%d",
            total_points,
            int((time.monotonic() - t_start) * 1000),
            int((t_encode_done - t_encoder_done) * 1000),
            int((t_upsert_done - t_upsert_start) * 1000),
        )

        # ── Step 7: Seed embedding cache with common queries ───────────────────
        # Encoder is already warm; seeding adds <500ms at build time and ensures
        # zero cold-encoding latency for the first production request.
        await _seed_embedding_cache()

    except Exception as exc:
        # Hybrid failure is non-fatal. BM25 is already committed.
        # Log full error type and message for diagnosis; never crash production.
        log.error(
            "[HYBRID] FAILED — BM25 index still active and serving requests. "
            "error_type=%s error=%s",
            type(exc).__name__,
            exc,
        )

# ── END HYBRID RAG Milestone 2: Vector Index Build Path ───────────────────────

# ── Build ──────────────────────────────────────────────────────────────────────
async def build_index() -> dict[str, Any]:
    """(Re)build the in-memory RAG index. Safe under concurrency."""
    async with _IDX.lock:
        t0 = time.monotonic()
        _IDX.reset()

        sources: list[tuple[str, str, str, bool, frozenset[str]]] = []
        sources.extend(_load_resource_files())
        sources.extend(await _load_kb_rows())

        safe_chunks: list[dict[str, Any]] = []  # collected for Hybrid vector build
        unsafe_count = 0
        for source_id, title, text, source_safe, allowed_roles in sources:
            for idx, chunk in enumerate(_chunk_text(text)):
                tokens = _tokenize(chunk)
                if not tokens:
                    continue
                # PATCH 2 + STEP 1: Unsafe chunks are PURGED from the index entirely.
                # They are counted for logging but never stored — unsafe_docs=0 guaranteed.
                chunk_safe = source_safe and _is_chunk_safe(chunk)
                if not chunk_safe:
                    unsafe_count += 1
                    log.warning(
                        f"[RAG_CHUNK_PURGED] source={source_id!r} chunk_idx={idx} "
                        f"— unsafe chunk excluded from index entirely"
                    )
                    continue  # STEP 1: do NOT store in _IDX.docs
                _IDX.docs.append({
                    "source": source_id,
                    "title": title,
                    "chunk_idx": idx,
                    "text": chunk,
                    "tokens": tokens,
                    "len": len(tokens),
                    "safe_for_customer": True,       # guaranteed: unsafe chunks never stored
                    "allowed_roles": allowed_roles,  # Phase 3: role-aware visibility
                })
                safe_chunks.append(_IDX.docs[-1])  # same dict ref — zero copy overhead

        # document frequencies
        for d in _IDX.docs:
            for t in set(d["tokens"]):
                _IDX.df[t] += 1
        _IDX.avgdl = (
            sum(d["len"] for d in _IDX.docs) / len(_IDX.docs) if _IDX.docs else 0.0
        )
        _IDX.built_at = time.time()
        _IDX.build_ms = int((time.monotonic() - t0) * 1000)
        safe_count = sum(1 for d in _IDX.docs if d.get("safe_for_customer", True))
        log.info(
            f"[rag] index built docs={len(_IDX.docs)} safe={safe_count} "
            f"unsafe={unsafe_count} vocab={len(_IDX.df)} "
            f"avgdl={_IDX.avgdl:.1f} in {_IDX.build_ms}ms"
        )
        # BM25 build is complete and committed. Attempt Hybrid vector build only
        # if the flag is enabled. If Hybrid fails, BM25 serves requests normally.
        if HYBRID_SEARCH_ENABLED:
            await _build_qdrant_index(safe_chunks)
        del safe_chunks  # release list container; dicts remain in _IDX.docs
        return await _stats_locked()


async def ensure_index() -> None:
    if _IDX.built_at is None:
        await build_index()


# ── BM25 scoring ───────────────────────────────────────────────────────────────
def _bm25_score(q_tokens: list[str], doc: dict[str, Any], n_docs: int) -> float:
    if not q_tokens or not doc["tokens"]:
        return 0.0
    tf = Counter(doc["tokens"])
    score = 0.0
    dl = doc["len"]
    for q in q_tokens:
        df = _IDX.df.get(q, 0)
        if df == 0:
            continue
        idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
        f = tf.get(q, 0)
        if f == 0:
            continue
        denom = f + _K1 * (1 - _B + _B * dl / (_IDX.avgdl or 1.0))
        score += idf * (f * (_K1 + 1)) / denom
    return score


# ── HYBRID RAG — Milestone 3: Search Helpers ──────────────────────────────────

async def _vector_search(query: str, k: int) -> list[dict[str, Any]]:
    """
    Encode query (with LRU cache) and search Qdrant for nearest neighbours.

    CONTRACT:
      - Called ONLY when HYBRID_SEARCH_ENABLED=True.
      - Never raises — all exceptions are caught, logged, and [] returned.
      - Applies visibility filter before returning: only safe_for_customer=True.
      - Cache hit: <1ms (no encoding). Cache miss: encoding via _encoding_executor.
      - Qdrant I/O uses default executor (I/O-bound, separate resource).
    """
    try:
        loop = asyncio.get_running_loop()

        # ── Embedding cache lookup ─────────────────────────────────────────────
        _cache_key = hashlib.sha1(query.encode()).digest()
        _cached = _query_embedding_cache.get(_cache_key)

        if _cached is not None:
            qvec = _cached
        else:
            # Cache miss — encode via dedicated 1-thread CPU executor
            encoder = await loop.run_in_executor(_encoding_executor, _get_encoder)

            def _encode_query(enc: Any, text: str) -> Any:
                return enc.encode(
                    [text],
                    show_progress_bar=False,
                    convert_to_numpy=True,
                )[0]

            qvec = await loop.run_in_executor(_encoding_executor, _encode_query, encoder, query)
            _query_embedding_cache.put(_cache_key, qvec)

        # ── Qdrant ANN search (I/O-bound → default executor) ──────────────────
        client = _get_qdrant_client()

        def _qdrant_search(cli: Any, qv: Any, limit: int) -> Any:
            return cli.search(
                collection_name=QDRANT_COLLECTION_NAME,
                query_vector=qv.tolist(),
                limit=limit,
                with_payload=True,
                with_vectors=False,  # payload only — stored vectors not needed
            )

        raw_hits = await loop.run_in_executor(None, _qdrant_search, client, qvec, k)

        results: list[dict[str, Any]] = []
        for h in raw_hits:
            payload = h.payload or {}
            # Visibility filter — applied BEFORE fusion, never after.
            # Qdrant payload has safe_for_customer=True for all indexed chunks
            # (set by _build_qdrant_index CONTRACT), but we re-verify defensively.
            if not payload.get("safe_for_customer", False):
                continue
            results.append({
                "source":       payload.get("source", ""),
                "title":        payload.get("title", ""),
                "chunk_idx":    payload.get("chunk_idx", 0),
                "text":         payload.get("text", ""),
                "vector_score": h.score,
            })
        return results

    except Exception as exc:
        log.warning(
            "[HYBRID] Vector search failed — BM25 continues alone. "
            "error_type=%s error=%s",
            type(exc).__name__,
            exc,
        )
        return []


async def _seed_embedding_cache() -> None:
    """Pre-encode _SEED_QUERIES into the embedding cache.

    Called once at the end of _build_qdrant_index(), after the encoder is
    already loaded. Subsequent requests for any seeded query cost <1ms.
    Idempotent — skips any query already in the cache.
    """
    if not HYBRID_SEARCH_ENABLED:
        return
    encoder = _get_encoder()  # singleton already resident after build; no I/O
    loop = asyncio.get_running_loop()
    seeded = 0

    def _encode_one(enc: Any, text: str) -> Any:
        return enc.encode([text], show_progress_bar=False, convert_to_numpy=True)[0]

    for q in _SEED_QUERIES:
        key = hashlib.sha1(q.encode()).digest()
        if _query_embedding_cache.get(key) is not None:
            continue  # already seeded or already hit — skip
        vec = await loop.run_in_executor(_encoding_executor, _encode_one, encoder, q)
        _query_embedding_cache.put(key, vec)
        seeded += 1

    log.info(
        "[HYBRID] Embedding cache seeded — new=%d total=%d maxsize=%d",
        seeded,
        len(_query_embedding_cache),
        _EMBEDDING_CACHE_MAX_SIZE,
    )


def _rrf_fuse(
    bm25_candidates: list[tuple[float, dict[str, Any]]],
    vector_hits: list[dict[str, Any]],
    k_rrf: int = 60,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """
    Standard Reciprocal Rank Fusion (k=60, no tuning).

    Formula (1-indexed rank):
      rrf_score(doc) = 1/(60 + bm25_rank) + 1/(60 + vector_rank)

    bm25_candidates: sorted (bm25_score, doc_dict) pairs, highest first.
    vector_hits: sorted by vector cosine score, highest first.
    Returns top_k results in search() output format {score, source, ...}.
    """
    rrf_scores: dict[tuple[str, int], float] = {}
    doc_map: dict[tuple[str, int], dict[str, Any]] = {}

    for rank, (_, doc) in enumerate(bm25_candidates):
        key = (doc["source"], doc["chunk_idx"])
        rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (k_rrf + rank + 1)
        if key not in doc_map:
            doc_map[key] = doc

    for rank, vhit in enumerate(vector_hits):
        key = (vhit["source"], vhit["chunk_idx"])
        rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (k_rrf + rank + 1)
        if key not in doc_map:
            # Chunk reached by vector but not by BM25 — still safe (visibility
            # filter was applied in _vector_search before fusion).
            doc_map[key] = {
                "source":            vhit["source"],
                "title":             vhit["title"],
                "chunk_idx":         vhit["chunk_idx"],
                "text":              vhit["text"],
                "tokens":            [],   # internal BM25 field — not used in output
                "len":               0,
                "safe_for_customer": True,
            }

    ranked = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
    out: list[dict[str, Any]] = []
    for (source, chunk_idx), rrf_score in ranked[:top_k]:
        doc = doc_map[(source, chunk_idx)]
        out.append({
            "score":     round(rrf_score, 6),
            "source":    doc["source"],
            "title":     doc["title"],
            "chunk_idx": doc["chunk_idx"],
            "text":      doc["text"],
        })
    return out


async def _hybrid_search(
    query: str,
    q_tokens: list[str],
    n_docs: int,
    top_k: int,
    role: str = "candidate",
) -> list[dict[str, Any]]:
    """
    Full hybrid retrieval: BM25 candidates + Vector search → Visibility → Role filter → RRF.

    Called ONLY when HYBRID_SEARCH_ENABLED=True.
    Never raises — _vector_search() is self-contained with its own try/except.
    If vector search returns [], RRF produces BM25-only ranking (graceful degrade).

    Note: min_score is intentionally not applied here. BM25 min_score is a
    BM25-domain threshold; RRF scores (max ~0.033) are on a different scale.
    RRF rank cutoff is enforced by top_k alone.
    """
    _candidates = max(top_k * 3, 20)  # over-fetch for better fusion coverage

    # BM25 candidate collection — no min_score (RRF handles ranking quality)
    bm25_candidates: list[tuple[float, dict[str, Any]]] = []
    for d in _IDX.docs:
        if not d.get("safe_for_customer", True):  # visibility filter
            continue
        if role not in d.get("allowed_roles", _DEFAULT_ALLOWED):  # Phase 3: role filter
            continue
        s = _bm25_score(q_tokens, d, n_docs)
        if s > 0.0:  # exclude zero-score docs; include all positive scorers
            bm25_candidates.append((s, d))
    bm25_candidates.sort(key=lambda x: x[0], reverse=True)

    # Vector search with visibility filter applied inside _vector_search
    vector_hits = await _vector_search(query, k=_candidates)

    # Phase 3: role-filter vector hits using BM25 index as source of truth for allowed_roles.
    # (Qdrant payload now stores allowed_roles on new indexes; cross-reference handles old ones.)
    _role_safe: set[tuple[str, int]] = {
        (d["source"], d["chunk_idx"])
        for d in _IDX.docs
        if role in d.get("allowed_roles", _DEFAULT_ALLOWED)
    }
    vector_hits = [
        h for h in vector_hits
        if (h["source"], h["chunk_idx"]) in _role_safe
    ]

    log.info(
        "[HYBRID] Search — bm25_candidates=%d vector_hits=%d top_k=%d",
        min(len(bm25_candidates), _candidates),
        len(vector_hits),
        top_k,
    )

    return _rrf_fuse(bm25_candidates[:_candidates], vector_hits, k_rrf=60, top_k=top_k)

# ── END HYBRID RAG Milestone 3 Search Helpers ─────────────────────────────────


# ── Search ─────────────────────────────────────────────────────────────────────
async def search(
    q: str,
    k: int = 5,
    min_score: float = 0.0,
    role: str = "candidate",
) -> list[dict[str, Any]]:
    """Search the RAG index.

    role: identity role of the caller (default "candidate" = most restrictive).
          Unknown roles are treated as "candidate". Pass role=None to use default.
          Callers that do not pass role get candidate-level access — same as current behaviour.
    """
    await ensure_index()
    q_tokens = _tokenize(q)
    if not q_tokens or not _IDX.docs:
        return []
    n_docs = len(_IDX.docs)

    # Phase 3: normalise role to a known access tier; unknown → "candidate"
    _effective_role: str = _ROLE_ACCESS_LEVEL.get(role or "candidate", "candidate")

    # ── HYBRID RAG — Milestone 3 ───────────────────────────────────────────────
    if HYBRID_SEARCH_ENABLED:
        out = await _hybrid_search(q, q_tokens, n_docs, k, role=_effective_role)
    else:
        # Exact production BM25 path
        scored = []
        for d in _IDX.docs:
            # PATCH 2: only score chunks tagged as safe for customers
            if not d.get("safe_for_customer", True):
                continue
            # Phase 3: role filter — skip chunks the caller's tier cannot access
            if _effective_role not in d.get("allowed_roles", _DEFAULT_ALLOWED):
                continue
            s = _bm25_score(q_tokens, d, n_docs)
            if s > min_score:
                scored.append((s, d))
        scored.sort(key=lambda x: x[0], reverse=True)
        out = []
        for s, d in scored[:k]:
            out.append({
                "score": round(s, 4),
                "source": d["source"],
                "title": d["title"],
                "chunk_idx": d["chunk_idx"],
                "text": d["text"],
            })
    # ── END HYBRID RAG Milestone 3 ─────────────────────────────────────────────

    # PATCH 7: source traceability — log every search with top hit details
    if out:
        _q_repr = repr(q[:80])
        log.info(
            f"[RAG_SEARCH] q={_q_repr} role={_effective_role!r} hits={len(out)} "
            f"top_score={out[0]['score']} source={out[0]['source']!r} "
            f"chunk={out[0]['chunk_idx']} title={out[0]['title']!r}"
        )
    else:
        _q_repr = repr(q[:80])
        log.debug(f"[RAG_SEARCH] q={_q_repr} role={_effective_role!r} hits=0 min_score={min_score}")
    # STEP 2: append to recent-searches ring buffer for incident debugging
    _RECENT_SEARCHES.append({
        "ts": time.time(),
        "query": q[:100],
        "hits": len(out),
        "top_score": out[0]["score"] if out else None,
        "top_source": out[0]["source"] if out else None,
        "chunk_preview": out[0]["text"][:80] if out else None,
        "safe": True,  # STEP 1 guarantees only safe chunks are indexed
    })
    return out


# ── Templated answer (extractive, no LLM) ─────────────────────────────────────
async def answer(q: str, k: int = 3, min_score: float = 1.0) -> Optional[dict[str, Any]]:
    """Build an extractive answer from top-k chunks. Returns None if no good hit."""
    hits = await search(q, k=k, min_score=min_score)
    if not hits:
        return None
    parts = []
    citations = []
    for i, h in enumerate(hits, 1):
        parts.append(f"[{i}] {h['text']}")
        citations.append({"n": i, "source": h["source"], "title": h["title"], "score": h["score"]})
    return {
        "answer": "\n\n".join(parts),
        "citations": citations,
        "top_score": hits[0]["score"],
    }


# ── Stats ──────────────────────────────────────────────────────────────────────
async def _stats_locked() -> dict[str, Any]:
    by_source: Counter = Counter()
    safe_count = 0
    unsafe_count = 0
    for d in _IDX.docs:
        by_source[d["source"].split(":", 1)[0]] += 1
        if d.get("safe_for_customer", True):
            safe_count += 1
        else:
            unsafe_count += 1
    return {
        "built_at": _IDX.built_at,
        "build_ms": _IDX.build_ms,
        "docs": len(_IDX.docs),
        "safe_docs": safe_count,
        "unsafe_docs": unsafe_count,
        "vocab": len(_IDX.df),
        "avgdl": round(_IDX.avgdl, 2),
        "by_source_kind": dict(by_source),
        "chunk_size": CHUNK_SIZE,
        "chunk_overlap": CHUNK_OVERLAP,
    }


async def stats() -> dict[str, Any]:
    await ensure_index()
    async with _IDX.lock:
        return await _stats_locked()


async def rebuild_index() -> dict[str, Any]:
    """PATCH 6: Force full index rebuild. Holds lock continuously from wipe through rebuild
    to prevent ensure_index() from triggering a concurrent build during the gap."""
    log.warning("[rag] [RAG_REBUILD] Forced index rebuild requested — clearing poisoned index")
    async with _IDX.lock:
        _IDX.reset()
        t0 = time.monotonic()
        sources: list[tuple[str, str, str, bool, frozenset[str]]] = []
        sources.extend(_load_resource_files())
        sources.extend(await _load_kb_rows())
        safe_chunks: list[dict[str, Any]] = []  # collected for Hybrid vector build
        unsafe_count = 0
        for source_id, title, text, source_safe, allowed_roles in sources:
            for idx, chunk in enumerate(_chunk_text(text)):
                tokens = _tokenize(chunk)
                if not tokens:
                    continue
                chunk_safe = source_safe and _is_chunk_safe(chunk)
                if not chunk_safe:
                    unsafe_count += 1
                    log.warning(
                        f"[RAG_CHUNK_PURGED] source={source_id!r} chunk_idx={idx} "
                        f"— unsafe chunk excluded from rebuild"
                    )
                    continue
                _IDX.docs.append({
                    "source": source_id,
                    "title": title,
                    "chunk_idx": idx,
                    "text": chunk,
                    "tokens": tokens,
                    "len": len(tokens),
                    "safe_for_customer": True,
                    "allowed_roles": allowed_roles,
                })
                safe_chunks.append(_IDX.docs[-1])  # same dict ref — zero copy overhead
        for d in _IDX.docs:
            for t in set(d["tokens"]):
                _IDX.df[t] += 1
        _IDX.avgdl = (
            sum(d["len"] for d in _IDX.docs) / len(_IDX.docs) if _IDX.docs else 0.0
        )
        _IDX.built_at = time.time()
        _IDX.build_ms = int((time.monotonic() - t0) * 1000)
        safe_count = len(_IDX.docs)
        log.info(
            f"[rag] rebuild done docs={len(_IDX.docs)} safe={safe_count} "
            f"unsafe={unsafe_count} vocab={len(_IDX.df)} in {_IDX.build_ms}ms"
        )
        # BM25 rebuild is complete and committed. Attempt Hybrid vector rebuild only
        # if the flag is enabled. If Hybrid fails, BM25 serves requests normally.
        if HYBRID_SEARCH_ENABLED:
            await _build_qdrant_index(safe_chunks)
        del safe_chunks  # release list container; dicts remain in _IDX.docs
        return await _stats_locked()


async def recent_searches() -> list[dict]:
    """STEP 2: Return last 50 RAG search audit records for incident debugging."""
    return list(_RECENT_SEARCHES)
