from __future__ import annotations

import math
import re
from dataclasses import dataclass
from hashlib import md5
from typing import Dict, List, Optional, Protocol, Sequence

from nebula_copilot.config import VectorConfig


@dataclass(frozen=True)
class VectorRecord:
    record_id: str
    text: str
    metadata: Dict[str, str]


@dataclass(frozen=True)
class VectorSearchHit:
    record_id: str
    score: float
    metadata: Dict[str, str]


@dataclass(frozen=True)
class VectorStoreBuildResult:
    store: VectorStore
    provider: str


class VectorStore(Protocol):
    def upsert(self, records: Sequence[VectorRecord]) -> None: ...

    def search(self, query: str, top_k: int) -> List[VectorSearchHit]: ...


class LocalVectorStore:
    """A dependency-free vector store based on hashed bag-of-words embeddings."""

    def __init__(self, dimension: int = 256) -> None:
        self._dimension = max(64, dimension)
        self._entries: dict[str, tuple[VectorRecord, List[float]]] = {}

    def upsert(self, records: Sequence[VectorRecord]) -> None:
        for record in records:
            self._entries[record.record_id] = (record, self._embed(record.text))

    def search(self, query: str, top_k: int) -> List[VectorSearchHit]:
        if top_k <= 0 or not self._entries:
            return []

        query_embedding = self._embed(query)
        hits: List[VectorSearchHit] = []
        for record_id, (record, embedding) in self._entries.items():
            score = self._cosine(query_embedding, embedding)
            if score <= 0:
                continue
            hits.append(
                VectorSearchHit(
                    record_id=record_id,
                    score=round(score, 4),
                    metadata=dict(record.metadata),
                )
            )

        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[:top_k]

    def _embed(self, text: str) -> List[float]:
        vector = [0.0] * self._dimension
        for token in self._tokenize(text):
            slot = int(md5(token.encode("utf-8")).hexdigest(), 16) % self._dimension
            vector[slot] += 1.0

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]

    def _tokenize(self, text: str) -> List[str]:
        return re.findall(r"[a-z0-9_]+", text.lower())

    def _cosine(self, left: Sequence[float], right: Sequence[float]) -> float:
        if len(left) != len(right):
            return 0.0
        return sum(a * b for a, b in zip(left, right))


class ChromaVectorStore:
    def __init__(self, collection_name: str, persist_dir: Optional[str] = None) -> None:
        try:
            import chromadb
        except Exception as exc:  # pragma: no cover - depends on optional package
            raise RuntimeError("chromadb not installed") from exc

        if persist_dir:
            client = chromadb.PersistentClient(path=persist_dir)
        else:
            client = chromadb.Client()

        self._collection = client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def upsert(self, records: Sequence[VectorRecord]) -> None:
        if not records:
            return
        self._collection.upsert(
            ids=[record.record_id for record in records],
            documents=[record.text for record in records],
            metadatas=[record.metadata for record in records],
        )

    def search(self, query: str, top_k: int) -> List[VectorSearchHit]:
        if top_k <= 0:
            return []

        result = self._collection.query(
            query_texts=[query],
            n_results=top_k,
            include=["metadatas", "distances"],
        )

        ids = (result.get("ids") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]

        hits: List[VectorSearchHit] = []
        for record_id, distance, metadata in zip(ids, distances, metadatas):
            # Chroma cosine distance: lower is better, convert to similarity.
            score = max(0.0, min(1.0, 1.0 - float(distance)))
            hits.append(
                VectorSearchHit(
                    record_id=str(record_id),
                    score=round(score, 4),
                    metadata=dict(metadata or {}),
                )
            )
        return hits


def build_vector_store(config: VectorConfig) -> VectorStoreBuildResult:
    provider = (config.provider or "local").strip().lower()
    if provider == "chroma":
        try:
            chroma_store = ChromaVectorStore(
                collection_name=config.collection_name,
                persist_dir=config.persist_dir,
            )
            return VectorStoreBuildResult(store=chroma_store, provider="chroma")
        except Exception:
            # Ensure production path keeps running when optional dependency is absent.
            return VectorStoreBuildResult(store=LocalVectorStore(), provider="local")

    return VectorStoreBuildResult(store=LocalVectorStore(), provider="local")