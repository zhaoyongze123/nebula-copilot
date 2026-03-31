from nebula_copilot.config import VectorConfig
from nebula_copilot.vector_store import LocalVectorStore, build_vector_store


def test_build_vector_store_local_provider() -> None:
    result = build_vector_store(
        VectorConfig(enabled=True, provider="local", collection_name="kb", persist_dir=None)
    )

    assert isinstance(result.store, LocalVectorStore)
    assert result.provider == "local"


def test_build_vector_store_unknown_provider_falls_back_to_local() -> None:
    result = build_vector_store(
        VectorConfig(enabled=True, provider="pgvector", collection_name="kb", persist_dir=None)
    )

    assert isinstance(result.store, LocalVectorStore)
    assert result.provider == "local"


def test_build_vector_store_chroma_fallback_when_dependency_missing() -> None:
    result = build_vector_store(
        VectorConfig(enabled=True, provider="chroma", collection_name="kb", persist_dir=None)
    )

    # In environments without chromadb, it must degrade to local rather than crash.
    assert result.provider in {"chroma", "local"}


def test_vector_store_build_result_tracks_provider() -> None:
    from nebula_copilot.vector_store import VectorStoreBuildResult

    local_result = build_vector_store(
        VectorConfig(enabled=True, provider="local", collection_name="kb", persist_dir=None)
    )
    assert local_result.provider == "local"
    assert isinstance(local_result.store, LocalVectorStore)

    unknown_result = build_vector_store(
        VectorConfig(enabled=True, provider="nonexistent", collection_name="kb", persist_dir=None)
    )
    assert unknown_result.provider == "local"
    assert isinstance(unknown_result.store, LocalVectorStore)
