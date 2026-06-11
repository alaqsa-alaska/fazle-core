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
  • await search(q, k=5, min_score=0.0) → list[dict]
  • await stats()                    → diagnostics dict
  • await answer(q, k=3, min_score=1.0) → {answer, citations} or None
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import time
from collections import Counter, deque
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


def _tokenize(text: str) -> list[str]:
    if not text:
        return []
    toks = _TOKEN_RE.findall(text.lower())
    return [t for t in toks if len(t) >= MIN_TOKEN_LEN]


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
def _load_resource_files() -> list[tuple[str, str, str, bool]]:
    """Return list of (source_id, title, text, safe_for_customer) for each approved file."""
    out: list[tuple[str, str, str, bool]] = []
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
        out.append((f"file:{name}", title, text, True))
    return out


async def _load_kb_rows() -> list[tuple[str, str, str, bool]]:
    """Return list of (source_id, title, text, safe_for_customer) for KB rows."""
    out: list[tuple[str, str, str, bool]] = []
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
        title = f"{key} ({r.get('category') or 'kb'})"
        text = r.get("reply_text") or ""
        if text.strip():
            out.append((f"kb:{key}", title, text, True))  # KB rows are admin-curated
    return out


# ── Build ──────────────────────────────────────────────────────────────────────
async def build_index() -> dict[str, Any]:
    """(Re)build the in-memory RAG index. Safe under concurrency."""
    async with _IDX.lock:
        t0 = time.monotonic()
        _IDX.reset()

        sources: list[tuple[str, str, str, bool]] = []
        sources.extend(_load_resource_files())
        sources.extend(await _load_kb_rows())

        unsafe_count = 0
        for source_id, title, text, source_safe in sources:
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
                    "safe_for_customer": True,  # guaranteed: unsafe chunks never stored
                })

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


# ── Search ─────────────────────────────────────────────────────────────────────
async def search(q: str, k: int = 5, min_score: float = 0.0) -> list[dict[str, Any]]:
    await ensure_index()
    q_tokens = _tokenize(q)
    if not q_tokens or not _IDX.docs:
        return []
    n_docs = len(_IDX.docs)
    scored = []
    for d in _IDX.docs:
        # PATCH 2: only score chunks tagged as safe for customers
        if not d.get("safe_for_customer", True):
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
    # PATCH 7: source traceability — log every search with top hit details
    if out:
        _q_repr = repr(q[:80])
        log.info(
            f"[RAG_SEARCH] q={_q_repr} hits={len(out)} "
            f"top_score={out[0]['score']} source={out[0]['source']!r} "
            f"chunk={out[0]['chunk_idx']} title={out[0]['title']!r}"
        )
    else:
        _q_repr = repr(q[:80])
        log.debug(f"[RAG_SEARCH] q={_q_repr} hits=0 min_score={min_score}")
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
        sources: list[tuple[str, str, str, bool]] = []
        sources.extend(_load_resource_files())
        sources.extend(await _load_kb_rows())
        unsafe_count = 0
        for source_id, title, text, source_safe in sources:
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
                })
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
        return await _stats_locked()


async def recent_searches() -> list[dict]:
    """STEP 2: Return last 50 RAG search audit records for incident debugging."""
    return list(_RECENT_SEARCHES)
