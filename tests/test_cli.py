from pathlib import Path

from typer.testing import CliRunner

from nebula_copilot.cli import app
from nebula_copilot.mock_data import DEFAULT_TRACE_ID

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
