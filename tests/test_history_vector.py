"""Tests for historical diagnosis vector store."""

import json
from pathlib import Path

import pytest

from nebula_copilot.config import VectorConfig
from nebula_copilot.history_vector import DiagnosisCase, HistoryVectorStore
from nebula_copilot.vector_store import LocalVectorStore


@pytest.fixture
def sample_runs():
    """Sample agent run records."""
    return [
        {
            "run_id": "run-abc123",
            "trace_id": "trace-123",
            "status": "ok",
            "started_at": "2026-03-30T10:00:00",
            "diagnosis": {
                "bottleneck": {
                    "service_name": "order-service",
                    "operation_name": "POST /orders",
                    "error_type": "TimeoutException",
                    "exception_stack": "java.util.concurrent.TimeoutException: Request timeout after 3000ms",
                    "action_suggestion": "检查下游服务响应时间和连接池配置",
                },
                "summary": "order-service 出现超时异常",
            },
            "jvm": {"summary": "heap_usage=85% gc_count=20"},
            "logs": {"sample": ["ERROR timeout", "WARN retry attempt 3"]},
            "summary": "order-service 请求超时，建议检查依赖服务",
        },
        {
            "run_id": "run-def456",
            "trace_id": "trace-456",
            "status": "ok",
            "started_at": "2026-03-30T11:00:00",
            "diagnosis": {
                "bottleneck": {
                    "service_name": "payment-service",
                    "operation_name": "POST /pay",
                    "error_type": "DatabaseException",
                    "exception_stack": "java.sql.SQLException: Connection pool exhausted",
                    "action_suggestion": "增加数据库连接池大小",
                },
                "summary": "payment-service 数据库连接池耗尽",
            },
            "jvm": {},
            "logs": {"sample": ["ERROR database pool exhausted"]},
            "summary": "payment-service 连接池问题",
        },
        {
            "run_id": "run-failed",
            "trace_id": "trace-999",
            "status": "failed",
            "diagnosis": {},
        },
    ]


@pytest.fixture
def temp_runs_file(tmp_path, sample_runs):
    """Create temporary runs file."""
    runs_file = tmp_path / "agent_runs.json"
    with open(runs_file, "w", encoding="utf-8") as f:
        json.dump(sample_runs, f)
    return runs_file


def test_history_vector_store_initialization():
    """Test HistoryVectorStore can be initialized."""
    store = HistoryVectorStore(vector_config=VectorConfig(enabled=True, provider="local"))
    assert store.provider in {"local", "custom"}
    assert store.case_count == 0


def test_index_from_runs_file(temp_runs_file):
    """Test indexing cases from runs file."""
    store = HistoryVectorStore(vector_config=VectorConfig(enabled=True, provider="local"))

    indexed = store.index_from_runs_file(temp_runs_file)

    # Should index 2 successful runs (skip failed one)
    assert indexed == 2
    assert store.case_count == 2


def test_index_from_nonexistent_file():
    """Test indexing from non-existent file returns 0."""
    store = HistoryVectorStore(vector_config=VectorConfig(enabled=True, provider="local"))

    indexed = store.index_from_runs_file(Path("/nonexistent/file.json"))

    assert indexed == 0
    assert store.case_count == 0


def test_search_historical_cases(temp_runs_file):
    """Test searching for similar historical cases."""
    store = HistoryVectorStore(
        vector_config=VectorConfig(enabled=True, provider="local", top_k=5, min_score=0.1)
    )
    store.index_from_runs_file(temp_runs_file)

    # Search for timeout issue
    matches = store.search(
        service_name="order-service",
        operation_name="POST /orders",
        error_type="TimeoutException",
        exception_stack="TimeoutException timeout 3000ms",
    )

    assert len(matches) > 0
    # Should find the order-service timeout case
    assert any(m.service_name == "order-service" for m in matches)
    assert any(m.error_type == "TimeoutException" for m in matches)


def test_search_boosts_same_service_and_error_type(temp_runs_file):
    """Test that search boosts cases with same service/error_type."""
    store = HistoryVectorStore(
        vector_config=VectorConfig(enabled=True, provider="local", top_k=5, min_score=0.1)
    )
    store.index_from_runs_file(temp_runs_file)

    matches = store.search(
        service_name="order-service",
        operation_name="POST /orders",
        error_type="TimeoutException",
    )

    # Best match should be order-service with TimeoutException
    if matches:
        top_match = matches[0]
        assert top_match.service_name == "order-service"
        assert top_match.error_type == "TimeoutException"


def test_search_returns_action_suggestions(temp_runs_file):
    """Test that search results include action suggestions."""
    store = HistoryVectorStore(
        vector_config=VectorConfig(enabled=True, provider="local", top_k=5, min_score=0.1)
    )
    store.index_from_runs_file(temp_runs_file)

    matches = store.search(
        service_name="payment-service",
        operation_name="POST /pay",
        error_type="DatabaseException",
    )

    assert len(matches) > 0
    # Should include action suggestion from historical case
    assert any("连接池" in m.action_suggestion for m in matches)


def test_search_with_vector_disabled():
    """Test search returns empty when vector is disabled."""
    store = HistoryVectorStore(vector_config=VectorConfig(enabled=False))

    matches = store.search(
        service_name="test-service", operation_name="test", error_type="TestError"
    )

    assert matches == []


def test_extract_cases_filters_failed_runs(sample_runs):
    """Test that failed runs are not indexed."""
    store = HistoryVectorStore(vector_config=VectorConfig(enabled=True, provider="local"))

    cases = store._extract_cases_from_runs(sample_runs)

    # Should extract only 2 successful cases
    assert len(cases) == 2
    assert all(case.run_status == "ok" for case in cases)


def test_diagnosis_case_structure(temp_runs_file):
    """Test DiagnosisCase contains expected fields."""
    store = HistoryVectorStore(vector_config=VectorConfig(enabled=True, provider="local"))
    store.index_from_runs_file(temp_runs_file)

    # Access internal cases for verification
    assert store.case_count > 0
    case = list(store._cases.values())[0]

    assert isinstance(case, DiagnosisCase)
    assert case.case_id
    assert case.trace_id
    assert case.service_name
    assert case.error_type
    assert case.summary
    assert case.action_suggestion
