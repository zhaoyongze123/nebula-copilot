from __future__ import annotations

import math
import re
from dataclasses import dataclass
from hashlib import md5
from typing import Dict, List, Protocol, Sequence


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