from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from nebula_copilot.cli import app
from nebula_copilot.mock_data import DEFAULT_TRACE_ID
from nebula_copilot.models import Span, TraceDocument

runner = CliRunner()


def test_seed_generates_mock_file(tmp_path: Path) -> None:
    output = tmp_path / "mock.json"
    result = runner.invoke(app, ["seed", DEFAULT_TRACE_ID, "--output", str(output), "--scenario", "timeout"])
    assert result.exit_code == 0
    assert output.exists()


def test_analyze_json_output(tmp_path: Path) -> None:
    output = tmp_path / "mock.json"
    runner.invoke(app, ["seed", DEFAULT_TRACE_ID, "--output", str(output), "--scenario", "timeout"])

    result = runner.invoke(
        app,
        [
            "analyze",
            DEFAULT_TRACE_ID,
            "--source",
            str(output),
            "--format",
            "json",
            "--top-n",
            "2",
        ],
    )
    assert result.exit_code == 0
    assert "bottleneck" in result.stdout


def test_analyze_invalid_format(tmp_path: Path) -> None:
    output = tmp_path / "mock.json"
    runner.invoke(app, ["seed", DEFAULT_TRACE_ID, "--output", str(output), "--scenario", "timeout"])

    result = runner.invoke(
        app,
        [
            "analyze",
            DEFAULT_TRACE_ID,
            "--source",
            str(output),
            "--format",
            "xml",
        ],
    )
    assert result.exit_code == 2


def test_analyze_table_format_alias(tmp_path: Path) -> None:
    output = tmp_path / "mock.json"
    runner.invoke(app, ["seed", DEFAULT_TRACE_ID, "--output", str(output), "--scenario", "timeout"])

    result = runner.invoke(
        app,
        [
            "analyze",
            DEFAULT_TRACE_ID,
            "--source",
            str(output),
            "--format",
            "table",
        ],
    )
    assert result.exit_code == 0


def test_analyze_trace_id_not_found(tmp_path: Path) -> None:
    output = tmp_path / "mock.json"
    runner.invoke(app, ["seed", DEFAULT_TRACE_ID, "--output", str(output), "--scenario", "timeout"])

    result = runner.invoke(
        app,
        [
            "analyze",
            "another-trace",
            "--source",
            str(output),
        ],
    )
    assert result.exit_code == 1
    assert "未找到目标 Trace" in result.stdout


def test_list_traces_validation() -> None:
    result = runner.invoke(
        app,
        [
            "list-traces",
            "--index",
            "nebula_metrics",
            "--last-minutes",
            "0",
        ],
    )
    assert result.exit_code == 2


def test_analyze_es_uses_repository_abstraction() -> None:
    trace_doc = TraceDocument(
        trace_id="trace-es-1",
        root=Span(
            span_id="root",
            parent_span_id=None,
            service_name="gateway",
            operation_name="GET /api",
            duration_ms=120,
            status="OK",
            exception_stack=None,
            children=[],
        ),
    )

    with patch("nebula_copilot.cli.ESRepository") as mocked_repo_cls:
        mocked_repo = mocked_repo_cls.return_value
        mocked_repo.get_trace.return_value = trace_doc

        result = runner.invoke(
            app,
            [
                "analyze-es",
                "trace-es-1",
                "--index",
                "nebula-trace-*",
                "--format",
                "json",
            ],
        )

    assert result.exit_code == 0
    mocked_repo_cls.assert_called_once()
    mocked_repo.get_trace.assert_called_once_with("trace-es-1")
    assert "trace-es-1" in result.stdout


def test_agent_analyze_success(tmp_path: Path) -> None:
    output = tmp_path / "mock.json"
    runs_path = tmp_path / "runs.json"
    runner.invoke(app, ["seed", DEFAULT_TRACE_ID, "--output", str(output), "--scenario", "timeout"])

    result = runner.invoke(
        app,
        [
            "agent-analyze",
            DEFAULT_TRACE_ID,
            "--source",
            str(output),
            "--runs-path",
            str(runs_path),
        ],
    )

    assert result.exit_code == 0
    assert "run_id:" in result.stdout
    assert runs_path.exists()


def test_agent_analyze_trace_not_found(tmp_path: Path) -> None:
    output = tmp_path / "mock.json"
    runs_path = tmp_path / "runs.json"
    runner.invoke(app, ["seed", DEFAULT_TRACE_ID, "--output", str(output), "--scenario", "timeout"])

    result = runner.invoke(
        app,
        [
            "agent-analyze",
            "unknown-trace",
            "--source",
            str(output),
            "--runs-path",
            str(runs_path),
        ],
    )

    assert result.exit_code == 1
    assert "未找到目标 Trace" in result.stdout
    assert runs_path.exists()
