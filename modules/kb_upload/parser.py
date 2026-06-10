"""
File parser — PDF, DOCX, TXT → text extraction + chunking.
Uses PyMuPDF (fitz) for PDF, python-docx for DOCX.
"""
from __future__ import annotations
import logging
import os
from typing import List

log = logging.getLogger("fazle.kb_upload")

CHUNK_SIZE = 320
CHUNK_OVERLAP = 60


def extract_text(filepath: str, filename: str) -> str:
    """Extract full text from PDF, DOCX, or TXT file."""
    ext = os.path.splitext(filename)[1].lower()

    if ext == ".pdf":
        return _extract_pdf(filepath)
    elif ext in (".docx", ".doc"):
        return _extract_docx(filepath)
    elif ext in (".txt", ".text", ".md", ".csv"):
        return _extract_text(filepath)
    else:
        raise ValueError(f"Unsupported file type: {ext}")


def _extract_pdf(filepath: str) -> str:
    """PyMuPDF (fitz) — reliable Bengali text extraction."""
    import fitz
    text_parts = []
    doc = fitz.open(filepath)
    for page_num, page in enumerate(doc, start=1):
        page_text = page.get_text("text")
        if page_text.strip():
            text_parts.append(f"[Page {page_num}]\n{page_text.strip()}")
    doc.close()
    full_text = "\n\n".join(text_parts)
    log.info("PDF extracted: %d pages, %d chars", len(text_parts), len(full_text))
    return full_text


def _extract_docx(filepath: str) -> str:
    """python-docx — DOCX text extraction."""
    from docx import Document
    doc = Document(filepath)
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    full_text = "\n".join(paragraphs)
    log.info("DOCX extracted: %d paragraphs, %d chars", len(paragraphs), len(full_text))
    return full_text


def _extract_text(filepath: str) -> str:
    """Plain text file."""
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    log.info("TXT loaded: %d chars", len(text))
    return text


def chunk_text(text: str,
               chunk_size: int = CHUNK_SIZE,
               overlap: int = CHUNK_OVERLAP) -> List[str]:
    """Split text into overlapping chunks."""
    if not text or not text.strip():
        return []

    text = text.strip()
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    chunks: List[str] = []
    current_chunk = ""

    for para in paragraphs:
        if len(current_chunk) + len(para) + 2 <= chunk_size:
            current_chunk = (current_chunk + "\n\n" + para).strip()
        else:
            if current_chunk:
                chunks.append(current_chunk)
            if len(para) > chunk_size:
                sentences = _split_sentences(para)
                sub_chunk = ""
                for sent in sentences:
                    if len(sub_chunk) + len(sent) + 1 <= chunk_size:
                        sub_chunk = (sub_chunk + " " + sent).strip()
                    else:
                        if sub_chunk:
                            chunks.append(sub_chunk)
                        sub_chunk = sent[:chunk_size]
                if sub_chunk:
                    chunks.append(sub_chunk)
                current_chunk = ""
            else:
                current_chunk = para

    if current_chunk:
        chunks.append(current_chunk)

    if overlap > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        for i in range(1, len(chunks)):
            prev_tail = chunks[i - 1][-overlap:]
            overlapped.append(prev_tail + " " + chunks[i])
        chunks = overlapped

    log.info("Chunked: %d chunks from %d chars", len(chunks), len(text))
    return chunks


def _split_sentences(text: str) -> List[str]:
    """Simple sentence splitter for Bengali/English mixed text."""
    import re
    sentences = re.split(r'(?<=[।.?!])\s+', text)
    return [s.strip() for s in sentences if s.strip()]
