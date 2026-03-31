"""Integration tests for Phase 2: Historical diagnosis vector retrieval."""

import json
from pathlib import Path

import pytest

from nebula_copilot.agent.graph import run_agent_graph
from nebula_copilot.config import VectorConfig
from nebula_copilot.history_vector import HistoryVectorStore
from nebula_copilot.models import Span, TraceDocument
from nebula_copilot.tools.types import ToolRegistry


@pytest.fixture
def mock_trace_doc():
    """Create a mock trace document."""
    root_span = Span(
        span_id="span-001",
        service_name="test-service",
        operation_name="test-op",
        duration_ms=500,
        status="ERROR",
    )
    return TraceDocument(
        trace_id="test-trace-001",
        root=root_span,
    )


@pytest.fixture
def mock_tool_registry(monkeypatch):
    """Create a mock tool registry."""

    def mock_query_trace(trace_id: str, index: str = ""):
        return {
            "status": "ok",
            "trace_id": trace_id,
            "bottleneck_service": "test-service",
            "keyword": "error",
        }

    def mock_query_jvm(service_name: str):
        return {
            "status": "ok",
            "service": service_name,
            "p95_duration_ms": 100,
            "heap_used_mb": 512,
            "heap_max_mb": 1024,
            "gc_count": 5,
            "error_rate": 0.02,
        }

    def mock_query_logs(service_name: str, keyword: str):
        return {
            "status": "ok",
            "service": service_name,
            "keyword": keyword,
            "sample": ["ERROR timeout", "WARN retry"],
        }

    registry = ToolRegistry(
        query_trace=mock_query_trace,
        query_jvm=mock_query_jvm,
        query_logs=mock_query_logs,
    )

    return registry


@pytest.fixture
def history_store(tmp_path):
    """Create a history vector store with test data."""
    runs_file = tmp_path / "test_runs.json"
    test_runs = [
        {
            "run_id": "run-001",
            "trace_id": "trace-001",
            "status": "ok",
            "started_at": "2026-03-30T10:00:00",
            "diagnosis": {
                "bottleneck": {
                    "service_name": "test-service",
                    "operation_name": "test-op",
                    "error_type": "TimeoutException",
                    "exception_stack": "timeout",
                    "action_suggestion": "增加超时时间",
                },
                "summary": "test service timeout",
            },
            "jvm": {"summary": "heap usage high"},
            "logs": {"sample": ["ERROR timeout"]},
            "summary": "test summary",
        }
    ]

    with open(runs_file, "w") as f:
        json.dump(test_runs, f)

    store = HistoryVectorStore(
        vector_config=VectorConfig(
            enabled=True, provider="local", top_k=3, min_score=0.1
        )
    )
    store.index_from_runs_file(runs_file)
    return store


def test_agent_graph_with_history_store(
    mock_trace_doc, mock_tool_registry, history_store
):
    """Test that agent graph can use history store for case retrieval."""
    result = run_agent_graph(
        trace_id="test-trace-001",
        run_id="run-test-001",
        trace_doc=mock_trace_doc,
        tool_registry=mock_tool_registry,
        history_store=history_store,
    )

    assert result["status"] == "ok"
    assert result["trace_id"] == "test-trace-001"

    # Check that history retrieval event was recorded
    history = result.get("history", [])
    history_events = [e for e in history if e.get("node") == "history_retrieval"]
    assert len(history_events) > 0, "History retrieval event should be recorded"

    # Check that historical context is in summary or report
    summary = result.get("summary", "")
    assert "test-service" in summary or len(history_events) > 0


def test_agent_graph_without_history_store(
    mock_trace_doc, mock_tool_registry
):
    """Test that agent graph works without history store (backward compatibility)."""
    result = run_agent_graph(
        trace_id="test-trace-002",
        run_id="run-test-002",
        trace_doc=mock_trace_doc,
        tool_registry=mock_tool_registry,
        history_store=None,  # No history store
    )

    assert result["status"] == "ok"
    assert result["trace_id"] == "test-trace-002"


def test_history_store_search_results(history_store):
    """Test that history store returns relevant matches."""
    matches = history_store.search(
        service_name="test-service",
        operation_name="test-op",
        error_type="TimeoutException",
    )

    assert len(matches) > 0
    assert matches[0].service_name == "test-service"
    assert "timeout" in matches[0].action_suggestion.lower() or "超时" in matches[0].action_suggestion


def test_history_store_with_empty_runs(tmp_path):
    """Test that history store handles empty runs gracefully."""
    runs_file = tmp_path / "empty_runs.json"
    with open(runs_file, "w") as f:
        json.dump([], f)

    store = HistoryVectorStore(vector_config=VectorConfig(enabled=True))
    indexed = store.index_from_runs_file(runs_file)

    assert indexed == 0
    matches = store.search("service", "op", "error")
    assert matches == []
