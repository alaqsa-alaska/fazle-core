"""Offline qualification for the startup BM25/Qdrant mutation boundary."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit


def _source_files():
    return [("file:test.txt", "test", "recruitment opportunity", True, frozenset({"candidate"}))]


@pytest.fixture
def clean_rag(monkeypatch):
    import modules.rag as rag

    rag._IDX.reset()
    rag._IDX.built_at = None
    monkeypatch.setattr(rag, "_load_resource_files", _source_files)
    monkeypatch.setattr(rag, "_load_knowledge_base_files", lambda: [])
    monkeypatch.setattr(rag, "_load_kb_rows", AsyncMock(return_value=[]))
    yield rag
    rag._IDX.reset()
    rag._IDX.built_at = None


async def test_startup_sync_enabled_preserves_qdrant_build(clean_rag, monkeypatch):
    rag = clean_rag
    monkeypatch.setattr(rag, "HYBRID_SEARCH_ENABLED", True)
    qdrant_build = AsyncMock()
    monkeypatch.setattr(rag, "_build_qdrant_index", qdrant_build)

    stats = await rag.build_index(sync_qdrant=True)

    assert stats["docs"] == 1
    qdrant_build.assert_awaited_once()


async def test_startup_sync_disabled_builds_bm25_without_qdrant_mutation(clean_rag, monkeypatch):
    rag = clean_rag
    monkeypatch.setattr(rag, "HYBRID_SEARCH_ENABLED", True)
    qdrant_build = AsyncMock()
    monkeypatch.setattr(rag, "_build_qdrant_index", qdrant_build)

    stats = await rag.build_index(sync_qdrant=False)

    assert stats["docs"] == 1
    assert rag._IDX.docs[0]["text"] == "recruitment opportunity"
    qdrant_build.assert_not_awaited()


async def test_lazy_retrieval_build_is_bm25_only_and_vector_retrieval_stays_enabled(clean_rag, monkeypatch):
    rag = clean_rag
    monkeypatch.setattr(rag, "HYBRID_SEARCH_ENABLED", True)
    qdrant_build = AsyncMock()
    vector_search = AsyncMock(return_value=[{
        "source": "file:test.txt",
        "title": "test",
        "chunk_idx": 0,
        "text": "recruitment opportunity",
        "vector_score": 0.9,
        "fact_authority": [],
    }])
    monkeypatch.setattr(rag, "_build_qdrant_index", qdrant_build)
    monkeypatch.setattr(rag, "_vector_search", vector_search)

    hits = await rag.search("recruitment", k=1)

    assert hits
    vector_search.assert_awaited_once()
    qdrant_build.assert_not_awaited()


async def test_scheduled_rag_rebuild_keeps_existing_default_build_behavior(monkeypatch):
    import modules.rag as rag
    from modules import scheduler

    build = AsyncMock(return_value={"docs": 1})
    monkeypatch.setattr(rag, "build_index", build)

    result = await scheduler.job_rag_rebuild()

    assert result["status"] == "ok"
    build.assert_awaited_once_with()


def test_startup_sync_setting_defaults_false_when_unset(monkeypatch):
    from app.config import Settings

    monkeypatch.delenv("RAG_STARTUP_QDRANT_SYNC_ENABLED", raising=False)
    settings = Settings(database_url="postgresql://test")

    assert settings.rag_startup_qdrant_sync_enabled is False


def test_authority_lock_ignore_is_exact_and_unknown_runtime_paths_remain_visible():
    repo = Path(__file__).resolve().parents[2]
    lock = "knowledge_base/00_governance/recruitment_fact_authority.json.lock"
    canonical = "knowledge_base/00_governance/recruitment_fact_authority.json"
    reviewed = "knowledge_base/04_business_rules/recruitment_lifecycle_authority.md"
    unknown_runtime = "knowledge_base/00_governance/unreviewed-runtime-artifact.txt"

    def check(path: str):
        return subprocess.run(
            ["git", "check-ignore", "--no-index", "--", path],
            cwd=repo,
            capture_output=True,
            text=True,
        )

    assert check(lock).returncode == 0
    assert check(canonical).returncode != 0
    assert check(reviewed).returncode != 0
    assert check(unknown_runtime).returncode != 0


def test_authority_lock_file_and_register_are_preserved():
    repo = Path(__file__).resolve().parents[2]
    lock = repo / "knowledge_base/00_governance/recruitment_fact_authority.json.lock"
    register = repo / "knowledge_base/00_governance/recruitment_fact_authority.json"

    assert lock.exists()
    assert register.exists()
    assert lock.stat().st_size == 0


@pytest.fixture
def isolated_lifespan(monkeypatch):
    """Run the actual lifespan; replace all external startup/shutdown work."""
    import asyncio
    import sys
    from types import ModuleType
    from unittest.mock import MagicMock
    from app import main
    from modules import rbac

    for name in (
        "init_db", "close_db", "load_feature_flags_from_db",
        "ensure_wa_chat_tables", "start_pollers", "start_fpe", "stop_fpe",
        "start_social_auto_reply", "stop_social_auto_reply",
    ):
        monkeypatch.setattr(main, name, AsyncMock(return_value={}))
    monkeypatch.setattr(rbac, "ensure_bootstrap_admins", AsyncMock(return_value=0))
    monkeypatch.setattr(main, "outbound_queue", MagicMock(
        stop_background_worker=AsyncMock()))
    monkeypatch.setattr(main, "fazle_scheduler", MagicMock())
    monkeypatch.setattr(main, "_start_stale_draft_expiry_worker", lambda: None)

    # Lazy imports must resolve only to harmless stubs, including cleanup.
    for module_name, async_names, sync_names in (
        ("app.ollama_daemon", ("start", "warm_model", "stop"), ()),
        ("shared.realtime", (), ("start_event_bridge",)),
        ("shared.runtime_gateway", ("start_gateway", "stop_gateway"), ()),
        ("shared.queue_arbiter", ("start_arbiter_recovery", "stop_arbiter_recovery"), ()),
        ("shared.frontend_sync", ("start_sync_monitoring", "stop_sync_monitoring"), ()),
        ("shared.bridge_orchestrator", ("start_orchestrator", "stop_orchestrator"), ()),
        ("shared.self_heal", ("start_self_healer", "stop_self_healer"), ()),
        ("modules.escort_roster.db", (
            "cleanup_empty_drafts", "cleanup_junk_drafts",
            "cleanup_exact_confirmed_duplicates"), ()),
    ):
        stub = ModuleType(module_name)
        for name in async_names:
            setattr(stub, name, AsyncMock(return_value={"deleted": []}))
        for name in sync_names:
            setattr(stub, name, MagicMock())
        monkeypatch.setitem(sys.modules, module_name, stub)
        parent, _, attr = module_name.rpartition(".")
        if parent in sys.modules:
            monkeypatch.setattr(sys.modules[parent], attr, stub, raising=False)

    # Reproduce the production import failure, even if another test loaded it.
    monkeypatch.setitem(sys.modules, "config", None)
    tasks = []
    create_task = asyncio.create_task

    def track_task(coro):
        task = create_task(coro)
        tasks.append(task)
        return task

    monkeypatch.setattr(main.asyncio, "create_task", track_task)
    return main, tasks


@pytest.mark.parametrize("env_value,expected", [(None, False), ("false", False), ("true", True)])
async def test_real_lifespan_schedules_bm25_and_controls_qdrant(
    env_value, expected, isolated_lifespan, clean_rag, monkeypatch, caplog,
):
    import asyncio
    import hashlib
    import logging
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    import numpy as np
    from app.config import Settings

    main, tasks = isolated_lifespan
    rag = clean_rag
    if env_value is None:
        monkeypatch.delenv("RAG_STARTUP_QDRANT_SYNC_ENABLED", raising=False)
    else:
        monkeypatch.setenv("RAG_STARTUP_QDRANT_SYNC_ENABLED", env_value)
    settings = Settings(_env_file=None, database_url="postgresql://test")
    assert settings.rag_startup_qdrant_sync_enabled is expected
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(rag, "HYBRID_SEARCH_ENABLED", True)
    client = MagicMock()
    client.get_collections.return_value = SimpleNamespace(
        collections=[SimpleNamespace(name=rag.QDRANT_COLLECTION_NAME)])
    client.retrieve.return_value = []
    client.scroll.return_value = ([SimpleNamespace(id="stale-fixture-point")], None)
    client.search.return_value = [SimpleNamespace(score=0.9, payload={
        "source": "file:test.txt", "title": "test", "chunk_idx": 0,
        "text": "recruitment opportunity", "safe_for_customer": True,
    })]
    monkeypatch.setattr(rag, "_get_qdrant_client", MagicMock(return_value=client))
    encoder = MagicMock()
    encoder.encode.return_value = np.zeros((1, rag.VECTOR_DIM), dtype="float32")
    monkeypatch.setattr(rag, "_get_encoder", MagicMock(return_value=encoder))
    monkeypatch.setattr(rag, "_seed_embedding_cache", AsyncMock())
    monkeypatch.setattr(rag, "_QDRANT_BUILD_LOCK", asyncio.Lock())
    monkeypatch.setattr(rag, "_query_embedding_cache", rag._EmbeddingLRUCache(10))
    rag._query_embedding_cache.put(
        hashlib.sha1(b"recruitment").digest(), np.zeros(rag.VECTOR_DIM))
    build = AsyncMock(wraps=rag.build_index)
    monkeypatch.setattr(rag, "build_index", build)
    caplog.set_level(logging.INFO, logger="fazle.app")

    async with main.lifespan(main.app):
        await asyncio.gather(*tasks)
        build.assert_awaited_once_with(sync_qdrant=expected)
        assert rag._IDX.built_at is not None
        assert len(rag._IDX.docs) == 1
        assert rag._bm25_score(["recruitment"], rag._IDX.docs[0], 1) > 0
        if expected:
            client.upsert.assert_called_once()
            client.delete.assert_called_once()
        else:
            client.upsert.assert_not_called()
            client.delete.assert_not_called()
            client.create_collection.assert_not_called()
            rag._get_encoder.assert_not_called()
        assert await rag.search("recruitment", k=1)
        client.search.assert_called_once()
        assert rag.HYBRID_SEARCH_ENABLED is True
    assert "index build scheduled (startup_qdrant_sync=" in caplog.text
    assert "startup build failed to schedule" not in caplog.text


async def test_real_lifespan_logs_rag_scheduling_failure(isolated_lifespan, monkeypatch, caplog):
    import asyncio
    import logging
    from unittest.mock import MagicMock
    from app.config import Settings
    from modules import rag

    main, tasks = isolated_lifespan
    monkeypatch.setattr(main, "settings", Settings(_env_file=None, database_url="postgresql://test"))
    build = MagicMock(side_effect=RuntimeError("fixture scheduling failure"))
    monkeypatch.setattr(rag, "build_index", build)
    caplog.set_level(logging.WARNING, logger="fazle.app")
    async with main.lifespan(main.app):
        await asyncio.gather(*tasks)
    build.assert_called_once_with(sync_qdrant=False)
    assert "[rag] startup build failed to schedule: fixture scheduling failure" in caplog.text
