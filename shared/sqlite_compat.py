"""
Fazle Core — Schema-tolerant reads of the WhatsApp bridge SQLite stores.
=======================================================================

The bridge ``messages`` table schema is **not owned by fazle-core**. The
stock whatsmeow / whatsapp-mcp bridge binary creates that table *without* a
``processed_text`` column. ``processed_text`` came from a former in-house
media OCR/STT writeback extension: it still exists on the older bridge2 /
bridge3 stores (their DB files predate the extension's removal), but a store
created by the current stock binary — e.g. bridge1's store after its
2026-08-24 re-pair — does not have it.

Any SELECT that names ``processed_text`` unconditionally therefore raises
``sqlite3.OperationalError: no such column: processed_text`` against a stock
store. Call ``has_column()`` on the already-open read-only connection and
build the column list from the result so the row shape stays identical in
both cases.

Read-only: issues only ``PRAGMA table_info`` — never a write, and never
mutates the bridge store.
"""

import sqlite3


def has_column(con: sqlite3.Connection, table: str, column: str) -> bool:
    """
    Return True if ``table`` has a column named ``column``.

    Uses ``PRAGMA table_info`` only. Returns False (never raises) if the
    table is missing or the pragma fails. ``table`` is expected to be a
    trusted literal supplied by calling code, not user input.
    """
    try:
        rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return False
    return any(str(row[1]) == column for row in rows)


def processed_text_column(con: sqlite3.Connection, table: str = "messages") -> str:
    """
    SQL fragment for the ``processed_text`` column in a SELECT list:

    * ``"processed_text"``          when the store has the column
    * ``"NULL AS processed_text"``  when it does not (row shape unchanged)
    """
    if has_column(con, table, "processed_text"):
        return "processed_text"
    return "NULL AS processed_text"
