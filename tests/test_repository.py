from pathlib import Path

from nebula_copilot.errors import DataSourceError, TraceNotFoundError, TraceValidationError
from nebula_copilot.mock_data import DEFAULT_TRACE_ID, write_mock_file
from nebula_copilot.repository import LocalJsonRepository


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
