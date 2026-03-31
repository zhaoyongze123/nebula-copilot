"""Historical diagnosis vector database for case-based reasoning.

This module extracts structured information from past agent runs and builds
a vector index to enable similarity-based retrieval of historical cases.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from nebula_copilot.config import VectorConfig
from nebula_copilot.vector_store import VectorRecord, VectorSearchHit, VectorStore, build_vector_store


@dataclass(frozen=True)
class DiagnosisCase:
    """Structured representation of a historical diagnosis case."""

    case_id: str
    trace_id: str
    service_name: str
    operation_name: str
    error_type: str
    exception_stack: Optional[str]
    summary: str
    action_suggestion: str
    jvm_indicators: List[str]
    log_samples: List[str]
    timestamp: str
    run_status: str


@dataclass(frozen=True)
class HistoricalMatch:
    """A matched historical case with similarity score."""

    case_id: str
    score: float
    service_name: str
    error_type: str
    summary: str
    action_suggestion: str


class HistoryVectorStore:
    """Vector store for historical diagnosis cases."""

    def __init__(
        self,
        vector_config: Optional[VectorConfig] = None,
        vector_store: Optional[VectorStore] = None,
    ) -> None:
        self._vector_config = vector_config or VectorConfig(
            enabled=True,
            collection_name="nebula_diagnosis_history",
            top_k=5,
            min_score=0.4,
        )

        self._vector_store: Optional[VectorStore] = None
        self._vector_provider = "none"
        self._cases: Dict[str, DiagnosisCase] = {}

        if self._vector_config.enabled:
            if vector_store is not None:
                self._vector_store = vector_store
                self._vector_provider = "custom"
            else:
                build_result = build_vector_store(self._vector_config)
                self._vector_store = build_result.store
                self._vector_provider = build_result.provider

    def index_from_runs_file(self, runs_path: Path) -> int:
        """Load and index historical cases from agent_runs.json file.

        Args:
            runs_path: Path to agent_runs.json file

        Returns:
            Number of cases indexed
        """
        if not runs_path.exists():
            return 0

        try:
            with open(runs_path, "r", encoding="utf-8") as f:
                runs = json.load(f)
        except Exception:
            return 0

        cases = self._extract_cases_from_runs(runs)
        return self._index_cases(cases)

    def search(
        self,
        service_name: str,
        operation_name: str,
        error_type: str,
        exception_stack: Optional[str] = None,
    ) -> List[HistoricalMatch]:
        """Search for similar historical cases.

        Args:
            service_name: Service name of current issue
            operation_name: Operation name of current issue
            error_type: Error type of current issue
            exception_stack: Optional exception stack trace

        Returns:
            List of matched historical cases sorted by relevance
        """
        if self._vector_store is None:
            return []

        # Build search query from current issue context
        query_parts = [
            service_name,
            operation_name,
            error_type,
        ]
        if exception_stack:
            query_parts.append(exception_stack[:500])  # Limit stack size

        query_text = " ".join(query_parts)

        # Perform vector search
        hits = self._vector_store.search(query_text, top_k=self._vector_config.top_k)

        # Filter and transform results
        matches: List[HistoricalMatch] = []
        for hit in hits:
            if hit.score < self._vector_config.min_score:
                continue

            case_id = hit.record_id
            case = self._cases.get(case_id)
            if case is None:
                continue

            # Boost score if same service/error_type
            adjusted_score = hit.score
            if case.service_name == service_name:
                adjusted_score = min(0.99, adjusted_score + 0.1)
            if case.error_type == error_type:
                adjusted_score = min(0.99, adjusted_score + 0.1)

            matches.append(
                HistoricalMatch(
                    case_id=case_id,
                    score=round(adjusted_score, 4),
                    service_name=case.service_name,
                    error_type=case.error_type,
                    summary=case.summary,
                    action_suggestion=case.action_suggestion,
                )
            )

        # Re-sort after score adjustment
        matches.sort(key=lambda m: m.score, reverse=True)
        return matches[: self._vector_config.top_k]

    def _extract_cases_from_runs(self, runs: List[Dict[str, Any]]) -> List[DiagnosisCase]:
        """Extract structured diagnosis cases from run records."""
        cases: List[DiagnosisCase] = []

        for run in runs:
            # Only index successful runs with meaningful diagnosis
            if run.get("status") != "ok":
                continue

            diagnosis = run.get("diagnosis", {})
            if not diagnosis:
                continue

            bottleneck = diagnosis.get("bottleneck", {})
            if not bottleneck:
                continue

            # Extract key fields
            service_name = bottleneck.get("service_name", "unknown")
            operation_name = bottleneck.get("operation_name", "unknown")
            error_type = bottleneck.get("error_type", "None")
            exception_stack = bottleneck.get("exception_stack")
            action_suggestion = bottleneck.get("action_suggestion", "")

            # Extract JVM indicators
            jvm = run.get("jvm", {})
            jvm_indicators = []
            if isinstance(jvm, dict):
                jvm_summary = jvm.get("summary", "")
                if jvm_summary:
                    jvm_indicators.append(jvm_summary)

            # Extract log samples
            logs = run.get("logs", {})
            log_samples = []
            if isinstance(logs, dict):
                samples = logs.get("sample", [])
                if isinstance(samples, list):
                    log_samples = samples[:5]  # Keep top 5 samples

            # Build case summary
            summary = run.get("summary", "") or diagnosis.get("summary", "")

            # Generate unique case ID
            case_id = self._generate_case_id(
                run_id=run.get("run_id", ""),
                trace_id=run.get("trace_id", ""),
                service_name=service_name,
                error_type=error_type,
            )

            case = DiagnosisCase(
                case_id=case_id,
                trace_id=run.get("trace_id", ""),
                service_name=service_name,
                operation_name=operation_name,
                error_type=error_type,
                exception_stack=exception_stack,
                summary=summary,
                action_suggestion=action_suggestion,
                jvm_indicators=jvm_indicators,
                log_samples=log_samples,
                timestamp=run.get("started_at", ""),
                run_status=run.get("status", ""),
            )

            cases.append(case)

        return cases

    def _index_cases(self, cases: Sequence[DiagnosisCase]) -> int:
        """Index cases into vector store."""
        if self._vector_store is None or not cases:
            return 0

        records: List[VectorRecord] = []
        for case in cases:
            # Store case in memory for retrieval
            self._cases[case.case_id] = case

            # Build indexable text from case fields
            text_parts = [
                case.service_name,
                case.operation_name,
                case.error_type,
                case.summary,
                case.action_suggestion,
            ]

            if case.exception_stack:
                text_parts.append(case.exception_stack[:500])

            text_parts.extend(case.jvm_indicators)
            text_parts.extend(case.log_samples)

            text = " ".join(str(p) for p in text_parts if p)

            # Create vector record
            record = VectorRecord(
                record_id=case.case_id,
                text=text,
                metadata={
                    "trace_id": case.trace_id,
                    "service": case.service_name,
                    "error_type": case.error_type,
                    "timestamp": case.timestamp,
                },
            )
            records.append(record)

        self._vector_store.upsert(records)
        return len(records)

    def _generate_case_id(
        self, run_id: str, trace_id: str, service_name: str, error_type: str
    ) -> str:
        """Generate deterministic case ID for deduplication."""
        key = f"{trace_id}:{service_name}:{error_type}:{run_id}"
        return hashlib.md5(key.encode("utf-8")).hexdigest()[:16]

    @property
    def provider(self) -> str:
        """Return the vector store provider name."""
        return self._vector_provider

    @property
    def case_count(self) -> int:
        """Return the number of indexed cases."""
        return len(self._cases)
