"""
Test cases for ES importer module.

运行：
  pytest tests/test_es_importer.py -v
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from nebula_copilot.es_importer import ESImporter, ImportError, _count_spans, _extract_services, _has_error_span
from nebula_copilot.models import Span, TraceDocument


class TestESImporterTransformTraceToRun:
    """Test transform_trace_to_run() method."""

    def test_transform_simple_trace(self) -> None:
        """Test transforming a simple trace to run format."""
        span = Span(
            span_id="root",
            parent_span_id=None,
            service_name="test-service",
            operation_name="test-op",
            duration_ms=1000,
            status="OK",
            exception_stack=None,
            children=[],
        )
        trace = TraceDocument(trace_id="trace-001", root=span)

        run = ESImporter.transform_trace_to_run(trace)

        assert run["trace_id"] == "trace-001"
        assert run["status"] == "ok"
        assert run["metrics"]["span_count"] == 1
        assert run["metrics"]["duration_ms"] == 1000
        assert run["_source"] == "es_import"

    def test_transform_trace_with_children(self) -> None:
        """Test transforming a trace with child spans."""
        child1 = Span(
            span_id="child-1",
            parent_span_id="root",
            service_name="child-service",
            operation_name="child-op",
            duration_ms=500,
            status="OK",
            exception_stack=None,
            children=[],
        )
        root = Span(
            span_id="root",
            parent_span_id=None,
            service_name="root-service",
            operation_name="root-op",
            duration_ms=1000,
            status="OK",
            exception_stack=None,
            children=[child1],
        )
        trace = TraceDocument(trace_id="trace-002", root=root)

        run = ESImporter.transform_trace_to_run(trace)

        assert run["trace_id"] == "trace-002"
        assert run["metrics"]["span_count"] == 2
        assert run["metrics"]["service_count"] == 2
        assert "child-service" in run["history"][1]["service_name"] or run["history"][0]["service_name"] == "child-service"

    def test_transform_trace_with_error(self) -> None:
        """Test transforming a trace with ERROR status."""
        error_span = Span(
            span_id="error",
            parent_span_id=None,
            service_name="error-service",
            operation_name="error-op",
            duration_ms=100,
            status="ERROR",
            exception_stack="exception message",
            children=[],
        )
        trace = TraceDocument(trace_id="trace-error", root=error_span)

        run = ESImporter.transform_trace_to_run(trace)

        assert run["status"] == "failed"
        assert run["metrics"]["has_error"] is True

    def test_transform_trace_timeline_stops_at_first_error(self) -> None:
        """失败链路应在首个 ERROR 节点截断，后续 SKIPPED 不展示。"""
        skipped_child = Span(
            span_id="skipped",
            parent_span_id="error",
            service_name="downstream-service",
            operation_name="RPC downstream",
            duration_ms=5,
            status="SKIPPED",
            exception_stack="upstream aborted",
            children=[],
        )
        error_child = Span(
            span_id="error",
            parent_span_id="root",
            service_name="payment-service",
            operation_name="RPC pay",
            duration_ms=1200,
            status="ERROR",
            exception_stack="timeout",
            children=[skipped_child],
        )
        root = Span(
            span_id="root",
            parent_span_id=None,
            service_name="gateway-service",
            operation_name="HTTP POST /submit",
            duration_ms=1300,
            status="OK",
            exception_stack=None,
            children=[error_child],
        )
        trace = TraceDocument(trace_id="trace-stop-error", root=root)

        run = ESImporter.transform_trace_to_run(trace)
        timeline = run["history"]
        statuses = [ev["status"] for ev in timeline]
        span_ids = [ev["span_id"] for ev in timeline]

        assert run["status"] == "failed"
        assert "ERROR" in statuses
        assert "SKIPPED" not in statuses
        assert "skipped" not in span_ids

    def test_transform_trace_none_root_raises(self) -> None:
        """Test that transform_trace_to_run raises when root is invalid."""
        # TraceDocument requires a valid Span, so we can't create with None root
        # Instead, test the manual check in transform_trace_to_run
        class MockTrace:
            trace_id = "trace-none"
            root = None

        with pytest.raises(ValueError, match="root cannot be None"):
            ESImporter.transform_trace_to_run(MockTrace())  # type: ignore


class TestESImporterSaveRuns:
    """Test save_runs() method."""

    def test_save_new_runs(self) -> None:
        """Test saving new runs to a new file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "runs.json"

            runs = [
                {
                    "run_id": "run-1",
                    "trace_id": "trace-1",
                    "status": "ok",
                    "started_at": "2025-03-20T10:00:00",
                    "finished_at": "2025-03-20T10:00:01",
                },
                {
                    "run_id": "run-2",
                    "trace_id": "trace-2",
                    "status": "error",
                    "started_at": "2025-03-20T10:00:02",
                    "finished_at": "2025-03-20T10:00:03",
                },
            ]

            ESImporter.save_runs(runs, output_path)

            assert output_path.exists()
            saved_data = json.loads(output_path.read_text())
            assert len(saved_data) == 2
            assert saved_data[0]["trace_id"] in ["trace-1", "trace-2"]

    def test_save_runs_deduplicates_by_trace_id(self) -> None:
        """Test that save_runs deduplicates by trace_id."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "runs.json"

            # 保存第一批 runs
            runs1 = [
                {
                    "run_id": "run-1",
                    "trace_id": "trace-1",
                    "status": "ok",
                    "started_at": "2025-03-20T10:00:00",
                }
            ]
            ESImporter.save_runs(runs1, output_path)

            # 保存第二批 runs（包含相同的 trace_id）
            runs2 = [
                {
                    "run_id": "run-1-updated",
                    "trace_id": "trace-1",
                    "status": "error",
                    "started_at": "2025-03-20T10:00:00",
                },
                {
                    "run_id": "run-3",
                    "trace_id": "trace-3",
                    "status": "ok",
                    "started_at": "2025-03-20T10:00:02",
                },
            ]
            ESImporter.save_runs(runs2, output_path)

            # 验证去重
            saved_data = json.loads(output_path.read_text())
            assert len(saved_data) == 2
            trace_ids = {item["trace_id"] for item in saved_data}
            assert trace_ids == {"trace-1", "trace-3"}

            # 验证更新（新数据覆盖旧数据）
            trace_1_item = next(item for item in saved_data if item["trace_id"] == "trace-1")
            assert trace_1_item["status"] == "error"
            assert trace_1_item["run_id"] == "run-1-updated"

    def test_save_runs_sorts_by_started_at(self) -> None:
        """Test that saved runs are sorted by started_at in descending order."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "runs.json"

            runs = [
                {"run_id": "run-3", "trace_id": "trace-3", "started_at": "2025-03-20T10:00:02"},
                {"run_id": "run-1", "trace_id": "trace-1", "started_at": "2025-03-20T10:00:00"},
                {"run_id": "run-2", "trace_id": "trace-2", "started_at": "2025-03-20T10:00:01"},
            ]

            ESImporter.save_runs(runs, output_path)

            saved_data = json.loads(output_path.read_text())
            assert saved_data[0]["started_at"] == "2025-03-20T10:00:02"
            assert saved_data[1]["started_at"] == "2025-03-20T10:00:01"
            assert saved_data[2]["started_at"] == "2025-03-20T10:00:00"


class TestHelperFunctions:
    """Test helper functions for span analysis."""

    def test_count_spans_single(self) -> None:
        """Test counting a single span."""
        span = Span(
            span_id="root",
            parent_span_id=None,
            service_name="service",
            operation_name="op",
            duration_ms=100,
            status="OK",
            exception_stack=None,
            children=[],
        )
        assert _count_spans(span) == 1

    def test_count_spans_with_children(self) -> None:
        """Test counting spans with children."""
        child1 = Span(
            span_id="child-1",
            parent_span_id="root",
            service_name="service",
            operation_name="op",
            duration_ms=100,
            status="OK",
            exception_stack=None,
            children=[],
        )
        child2 = Span(
            span_id="child-2",
            parent_span_id="root",
            service_name="service",
            operation_name="op",
            duration_ms=100,
            status="OK",
            exception_stack=None,
            children=[],
        )
        root = Span(
            span_id="root",
            parent_span_id=None,
            service_name="service",
            operation_name="op",
            duration_ms=200,
            status="OK",
            exception_stack=None,
            children=[child1, child2],
        )
        assert _count_spans(root) == 3

    def test_extract_services(self) -> None:
        """Test extracting unique service names."""
        child = Span(
            span_id="child",
            parent_span_id="root",
            service_name="child-service",
            operation_name="op",
            duration_ms=100,
            status="OK",
            exception_stack=None,
            children=[],
        )
        root = Span(
            span_id="root",
            parent_span_id=None,
            service_name="root-service",
            operation_name="op",
            duration_ms=200,
            status="OK",
            exception_stack=None,
            children=[child],
        )
        services = _extract_services(root)
        assert services == {"root-service", "child-service"}

    def test_has_error_span_true(self) -> None:
        """Test detecting error status in span tree."""
        error_span = Span(
            span_id="error",
            parent_span_id="root",
            service_name="service",
            operation_name="op",
            duration_ms=100,
            status="ERROR",
            exception_stack="error message",
            children=[],
        )
        root = Span(
            span_id="root",
            parent_span_id=None,
            service_name="service",
            operation_name="op",
            duration_ms=200,
            status="OK",
            exception_stack=None,
            children=[error_span],
        )
        assert _has_error_span(root) is True

    def test_has_error_span_false(self) -> None:
        """Test when no error span exists."""
        child = Span(
            span_id="child",
            parent_span_id="root",
            service_name="service",
            operation_name="op",
            duration_ms=100,
            status="OK",
            exception_stack=None,
            children=[],
        )
        root = Span(
            span_id="root",
            parent_span_id=None,
            service_name="service",
            operation_name="op",
            duration_ms=200,
            status="OK",
            exception_stack=None,
            children=[child],
        )
        assert _has_error_span(root) is False
