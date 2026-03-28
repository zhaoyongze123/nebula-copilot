from pathlib import Path
from unittest.mock import patch

from nebula_copilot.errors import DataSourceError, TraceNotFoundError, TraceValidationError
from nebula_copilot.mock_data import DEFAULT_TRACE_ID, write_mock_file
from nebula_copilot.models import Span, TraceDocument
from nebula_copilot.repository import ESRepository, LocalJsonRepository


def test_local_repository_reads_valid_trace(tmp_path: Path) -> None:
    source = tmp_path / "mock.json"
    write_mock_file(source, DEFAULT_TRACE_ID, "timeout")

    repo = LocalJsonRepository(source)
    trace_doc = repo.get_trace(DEFAULT_TRACE_ID)

    assert trace_doc.trace_id == DEFAULT_TRACE_ID


def test_local_repository_file_not_exists(tmp_path: Path) -> None:
    source = tmp_path / "not-exists.json"
    repo = LocalJsonRepository(source)

    try:
        repo.get_trace("trace_x")
        assert False, "expected DataSourceError"
    except DataSourceError:
        assert True


def test_local_repository_validation_error(tmp_path: Path) -> None:
    source = tmp_path / "bad.json"
    source.write_text("{bad json}")

    repo = LocalJsonRepository(source)
    try:
        repo.get_trace("trace_x")
        assert False, "expected TraceValidationError"
    except TraceValidationError:
        assert True


def test_local_repository_trace_not_found(tmp_path: Path) -> None:
    source = tmp_path / "mock.json"
    write_mock_file(source, DEFAULT_TRACE_ID, "timeout")

    repo = LocalJsonRepository(source)
    try:
        repo.get_trace("trace_x")
        assert False, "expected TraceNotFoundError"
    except TraceNotFoundError:
        assert True


def test_es_repository_get_trace_calls_es_client() -> None:
    trace_doc = TraceDocument(
        trace_id="trace-123",
        root=Span(
            span_id="root",
            parent_span_id=None,
            service_name="svc",
            operation_name="op",
            duration_ms=1,
            status="OK",
            exception_stack=None,
            children=[],
        ),
    )

    with patch("nebula_copilot.repository.fetch_trace_by_id", return_value=trace_doc) as mocked:
        repo = ESRepository(
            es_url="http://localhost:9200",
            index="nebula-trace-*",
            username="user",
            password="pass",
            verify_certs=False,
            timeout_seconds=12,
        )
        result = repo.get_trace("trace-123")

    assert result == trace_doc
    mocked.assert_called_once_with(
        es_url="http://localhost:9200",
        index="nebula-trace-*",
        trace_id="trace-123",
        username="user",
        password="pass",
        verify_certs=False,
        timeout_seconds=12,
    )
