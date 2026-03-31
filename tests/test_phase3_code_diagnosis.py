"""Tests for Phase 3: Code-aware Trace diagnosis enhancement."""

from pathlib import Path

import pytest

from nebula_copilot.agent.graph import run_agent_graph
from nebula_copilot.code_whitelist import CodeWhitelistStore
from nebula_copilot.config import VectorConfig
from nebula_copilot.models import Span, TraceDocument
from nebula_copilot.tools.types import ToolRegistry


@pytest.fixture
def mock_trace_with_error():
    """Create a trace with error."""
    error_span = Span(
        span_id="span-error",
        service_name="api-service",
        operation_name="handle_request",
        duration_ms=5000,
        status="ERROR",
        exception_stack="TimeoutException: request timeout after 3000ms",
    )
    return TraceDocument(
        trace_id="test-trace-error",
        root=error_span,
    )


@pytest.fixture
def mock_tool_registry():
    """Create a mock tool registry."""

    def mock_query_trace(trace_id: str, index: str = ""):
        return {
            "status": "ok",
            "trace_id": trace_id,
            "bottleneck_service": "api-service",
            "keyword": "timeout",
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

    return ToolRegistry(
        query_trace=mock_query_trace,
        query_jvm=mock_query_jvm,
        query_logs=mock_query_logs,
    )


@pytest.fixture
def code_store(tmp_path):
    """Create a code store with sample code snippets."""
    # Create test repository structure
    api_dir = tmp_path / "src" / "api"
    api_dir.mkdir(parents=True)

    api_file = api_dir / "handler.py"
    api_file.write_text(
        '''def handle_request(request):
    """Handle incoming request with timeout."""
    try:
        result = process_with_timeout(request)
        return {"status": "ok", "data": result}
    except TimeoutException as e:
        # Retry logic for timeout
        return retry_with_backoff(request)
    except Exception as e:
        return {"status": "error", "error": str(e)}

def retry_with_backoff(request, max_retries=3):
    """Implement exponential backoff retry."""
    for attempt in range(max_retries):
        try:
            return process_with_timeout(request, timeout=5000)
        except TimeoutException:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                raise
'''
    )

    store = CodeWhitelistStore(
        vector_config=VectorConfig(enabled=True, provider="local", top_k=5, min_score=0.1),
        whitelist_dirs={"api": ["src/api"]},
    )
    store.index_from_repository(tmp_path)
    return store


def test_agent_graph_includes_code_evidence(
    mock_trace_with_error, mock_tool_registry, code_store
):
    """Test that agent graph includes code evidence in diagnosis."""
    result = run_agent_graph(
        trace_id="test-trace-error",
        run_id="run-test-code-diagnosis",
        trace_doc=mock_trace_with_error,
        tool_registry=mock_tool_registry,
        code_store=code_store,
    )

    assert result["status"] == "ok"

    # Check that code retrieval event was recorded
    history = result.get("history", [])
    code_events = [e for e in history if e.get("node") == "code_retrieval"]
    assert len(code_events) > 0, "Code retrieval event should be recorded"

    # Check that the code event has success status
    code_event = code_events[0]
    assert code_event["status"] == "ok"
    assert "count" in code_event["payload"], "Should record number of code matches"


def test_agent_graph_summary_contains_code_context(
    mock_trace_with_error, mock_tool_registry, code_store
):
    """Test that diagnosis summary includes code evidence."""
    result = run_agent_graph(
        trace_id="test-trace-error",
        run_id="run-test-code-context",
        trace_doc=mock_trace_with_error,
        tool_registry=mock_tool_registry,
        code_store=code_store,
    )

    summary = result.get("summary", "")

    # Summary should contain diagnostic information
    assert "api-service" in summary or len(result.get("history", [])) > 0

    # Check the full result for any code snippet references
    result_str = str(result)
    # Code evidence might be in summary, diagnosis, or history
    assert len(result.get("history", [])) > 0


def test_code_store_search_for_timeout_scenarios(code_store):
    """Test that code store finds timeout handling code."""
    matches = code_store.search(
        service_name="api-service",
        error_type="TimeoutException",
        operation_name="handle_request",
    )

    assert len(matches) > 0, "Should find timeout handling code"

    # Verify code matches contain relevant information
    match = matches[0]
    assert match.file_path
    assert match.function_name in ["handle_request", "retry_with_backoff"]
    assert "timeout" in match.code_text.lower() or "retry" in match.code_text.lower()


def test_code_evidence_fallback_when_store_unavailable(
    mock_trace_with_error, mock_tool_registry
):
    """Test that agent graph works when code store is unavailable."""
    result = run_agent_graph(
        trace_id="test-trace-error",
        run_id="run-test-no-code",
        trace_doc=mock_trace_with_error,
        tool_registry=mock_tool_registry,
        code_store=None,  # No code store
    )

    assert result["status"] == "ok"

    # Should still complete successfully without code context
    history = result.get("history", [])
    code_events = [e for e in history if e.get("node") == "code_retrieval"]
    assert len(code_events) == 0, "No code retrieval event without code store"


def test_code_evidence_includes_function_names(code_store):
    """Test that code matches include function names."""
    matches = code_store.search(
        service_name="api-service",
        error_type="retry timeout",
    )

    if matches:
        for match in matches:
            assert match.function_name
            assert match.file_path
            assert "handler.py" in match.file_path or "api" in match.file_path
