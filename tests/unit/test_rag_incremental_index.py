"""
Unit tests — modules.rag._build_qdrant_index incremental upsert (2026-07-13 fix)

Verifies: unchanged chunks are skipped (no re-encode), changed/new chunks
are encoded+upserted, stale points get cleaned up, and the collection is
never dropped. All Qdrant/encoder calls are mocked — no real Qdrant/model
dependency.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

pytestmark = pytest.mark.unit


def _fake_point(point_id: str, content_hash: str):
    return SimpleNamespace(id=point_id, payload={"content_hash": content_hash})


def _chunk(source="file:a.txt", chunk_idx=0, text="hello world"):
    return {
        "source": source,
        "title": "A",
        "chunk_idx": chunk_idx,
        "text": text,
        "allowed_roles": frozenset({"candidate"}),
    }


@pytest.fixture
def mock_qdrant_client():
    client = MagicMock()
    client.get_collections.return_value = SimpleNamespace(collections=[SimpleNamespace(name="fazle_rag_chunks")])
    client.scroll.return_value = ([], None)  # no stale points by default
    return client


@pytest.fixture(autouse=True)
def _reset_lock():
    import asyncio
    import modules.rag as rag
    # Ensure a fresh, unlocked lock per test (module-level singleton).
    rag._QDRANT_BUILD_LOCK = asyncio.Lock()
    yield


class TestIncrementalSkip:
    async def test_unchanged_chunk_is_skipped_no_encode(self, mock_qdrant_client):
        import modules.rag as rag

        chunk = _chunk()
        point_id = rag._chunk_point_id(chunk)
        content_hash = rag._chunk_content_hash(chunk["text"])
        mock_qdrant_client.retrieve.return_value = [_fake_point(point_id, content_hash)]

        with patch.object(rag, "_get_qdrant_client", return_value=mock_qdrant_client), \
             patch.object(rag, "_qdrant_ensure_collection"), \
             patch.object(rag, "_get_encoder") as mock_get_encoder, \
             patch.object(rag, "_seed_embedding_cache", new=AsyncMock(return_value=None)):
            await rag._build_qdrant_index([chunk])

        mock_get_encoder.assert_not_called()
        mock_qdrant_client.upsert.assert_not_called()
        mock_qdrant_client.delete_collection.assert_not_called()

    async def test_changed_chunk_is_encoded_and_upserted(self, mock_qdrant_client):
        import modules.rag as rag

        chunk = _chunk(text="new content")
        point_id = rag._chunk_point_id(chunk)
        # Existing point has a DIFFERENT hash — content changed since last build.
        mock_qdrant_client.retrieve.return_value = [_fake_point(point_id, "stale-hash-0000")]

        fake_encoder = MagicMock()
        fake_encoder.encode.return_value = np.zeros((1, 4), dtype="float32")

        with patch.object(rag, "_get_qdrant_client", return_value=mock_qdrant_client), \
             patch.object(rag, "_qdrant_ensure_collection"), \
             patch.object(rag, "_get_encoder", return_value=fake_encoder), \
             patch.object(rag, "_seed_embedding_cache", new=AsyncMock(return_value=None)):
            await rag._build_qdrant_index([chunk])

        fake_encoder.encode.assert_called_once()
        mock_qdrant_client.upsert.assert_called_once()
        upserted_points = mock_qdrant_client.upsert.call_args.kwargs["points"]
        assert len(upserted_points) == 1
        assert upserted_points[0].payload["content_hash"] == rag._chunk_content_hash("new content")

    async def test_new_chunk_with_no_existing_point_is_encoded(self, mock_qdrant_client):
        import modules.rag as rag

        chunk = _chunk(text="brand new")
        mock_qdrant_client.retrieve.return_value = []  # nothing exists yet

        fake_encoder = MagicMock()
        fake_encoder.encode.return_value = np.zeros((1, 4), dtype="float32")

        with patch.object(rag, "_get_qdrant_client", return_value=mock_qdrant_client), \
             patch.object(rag, "_qdrant_ensure_collection"), \
             patch.object(rag, "_get_encoder", return_value=fake_encoder), \
             patch.object(rag, "_seed_embedding_cache", new=AsyncMock(return_value=None)):
            await rag._build_qdrant_index([chunk])

        fake_encoder.encode.assert_called_once()
        mock_qdrant_client.upsert.assert_called_once()

    async def test_collection_is_never_dropped(self, mock_qdrant_client):
        import modules.rag as rag

        chunk = _chunk()
        mock_qdrant_client.retrieve.return_value = []
        fake_encoder = MagicMock()
        fake_encoder.encode.return_value = np.zeros((1, 4), dtype="float32")

        with patch.object(rag, "_get_qdrant_client", return_value=mock_qdrant_client), \
             patch.object(rag, "_qdrant_ensure_collection") as mock_ensure, \
             patch.object(rag, "_get_encoder", return_value=fake_encoder), \
             patch.object(rag, "_seed_embedding_cache", new=AsyncMock(return_value=None)):
            await rag._build_qdrant_index([chunk])

        mock_qdrant_client.delete_collection.assert_not_called()
        mock_ensure.assert_called_once()

    async def test_stale_points_are_cleaned_up(self, mock_qdrant_client):
        import modules.rag as rag

        chunk = _chunk()
        point_id = rag._chunk_point_id(chunk)
        content_hash = rag._chunk_content_hash(chunk["text"])
        mock_qdrant_client.retrieve.return_value = [_fake_point(point_id, content_hash)]
        # Scroll returns the current point PLUS one stale point no longer in the corpus.
        mock_qdrant_client.scroll.return_value = (
            [SimpleNamespace(id=point_id), SimpleNamespace(id="stale-point-id")], None,
        )

        with patch.object(rag, "_get_qdrant_client", return_value=mock_qdrant_client), \
             patch.object(rag, "_qdrant_ensure_collection"), \
             patch.object(rag, "_get_encoder"), \
             patch.object(rag, "_seed_embedding_cache", new=AsyncMock(return_value=None)):
            await rag._build_qdrant_index([chunk])

        mock_qdrant_client.delete.assert_called_once()
        deleted_ids = mock_qdrant_client.delete.call_args.kwargs["points_selector"]
        assert deleted_ids == ["stale-point-id"]

    async def test_empty_chunks_skips_entirely(self, mock_qdrant_client):
        import modules.rag as rag
        with patch.object(rag, "_get_qdrant_client", return_value=mock_qdrant_client):
            await rag._build_qdrant_index([])
        mock_qdrant_client.retrieve.assert_not_called()


class TestIsRebuildingDecoupledLock:
    async def test_is_rebuilding_true_during_qdrant_phase_only(self):
        import modules.rag as rag

        assert rag.is_rebuilding() is False

        async def _hold_lock_and_check():
            async with rag._QDRANT_BUILD_LOCK:
                # _IDX.lock is NOT held here, but is_rebuilding() must still be True.
                assert not rag._IDX.lock.locked()
                assert rag.is_rebuilding() is True

        await _hold_lock_and_check()
        assert rag.is_rebuilding() is False
