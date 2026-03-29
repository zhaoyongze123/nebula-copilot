from pathlib import Path
import json
import time
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
    guard_path = tmp_path / "guard.json"
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
            "--run-guard-path",
            str(guard_path),
        ],
    )

    assert result.exit_code == 0
    assert "run_id:" in result.stdout
    assert "图执行完成" in result.stdout
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


def test_agent_analyze_llm_enabled_without_key_fallback_success(tmp_path: Path) -> None:
    output = tmp_path / "mock.json"
    runs_path = tmp_path / "runs.json"
    guard_path = tmp_path / "guard.json"
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_ENABLED=true\n", encoding="utf-8")
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
            "--run-guard-path",
            str(guard_path),
            "--env-file",
            str(env_file),
            "--llm-enabled",
        ],
    )

    assert result.exit_code == 0
    assert "run_id:" in result.stdout


def test_agent_analyze_notify_failed_degraded_not_blocking(tmp_path: Path) -> None:
    output = tmp_path / "mock.json"
    runs_path = tmp_path / "runs.json"
    guard_path = tmp_path / "guard.json"
    runner.invoke(app, ["seed", DEFAULT_TRACE_ID, "--output", str(output), "--scenario", "timeout"])

    with patch("nebula_copilot.cli.push_summary_reliable") as mocked_notify:
        mocked_notify.return_value = type(
            "NotifyResultStub",
            (),
            {"status": "failed", "deduplicated": False, "attempts": 3, "error": "webhook down"},
        )()

        result = runner.invoke(
            app,
            [
                "agent-analyze",
                DEFAULT_TRACE_ID,
                "--source",
                str(output),
                "--runs-path",
                str(runs_path),
                "--run-guard-path",
                str(guard_path),
                "--push-webhook",
                "https://example.com/webhook",
            ],
        )

    assert result.exit_code == 0
    assert runs_path.exists()
    records = json.loads(runs_path.read_text(encoding="utf-8"))
    latest = records[-1]
    assert latest["status"] == "degraded"
    assert latest["notify"]["status"] == "failed"


def test_agent_analyze_run_deduped(tmp_path: Path) -> None:
    output = tmp_path / "mock.json"
    runs_path = tmp_path / "runs.json"
    guard_path = tmp_path / "guard.json"
    runner.invoke(app, ["seed", DEFAULT_TRACE_ID, "--output", str(output), "--scenario", "timeout"])

    first = runner.invoke(
        app,
        [
            "agent-analyze",
            DEFAULT_TRACE_ID,
            "--source",
            str(output),
            "--runs-path",
            str(runs_path),
            "--run-guard-path",
            str(guard_path),
            "--run-dedupe-window-seconds",
            "300",
        ],
    )
    second = runner.invoke(
        app,
        [
            "agent-analyze",
            DEFAULT_TRACE_ID,
            "--source",
            str(output),
            "--runs-path",
            str(runs_path),
            "--run-guard-path",
            str(guard_path),
            "--run-dedupe-window-seconds",
            "300",
        ],
    )

    assert first.exit_code == 0
    assert second.exit_code == 0
    records = json.loads(runs_path.read_text(encoding="utf-8"))
    assert records[-1]["status"] == "deduped"


def test_agent_analyze_rate_limited(tmp_path: Path) -> None:
    output = tmp_path / "mock.json"
    runs_path = tmp_path / "runs.json"
    guard_path = tmp_path / "guard.json"
    runner.invoke(app, ["seed", DEFAULT_TRACE_ID, "--output", str(output), "--scenario", "timeout"])

    first = runner.invoke(
        app,
        [
            "agent-analyze",
            DEFAULT_TRACE_ID,
            "--source",
            str(output),
            "--runs-path",
            str(runs_path),
            "--run-guard-path",
            str(guard_path),
            "--run-dedupe-window-seconds",
            "1",
            "--run-rate-limit-per-minute",
            "1",
        ],
    )
    time.sleep(1.1)
    second = runner.invoke(
        app,
        [
            "agent-analyze",
            DEFAULT_TRACE_ID,
            "--source",
            str(output),
            "--runs-path",
            str(runs_path),
            "--run-guard-path",
            str(guard_path),
            "--run-dedupe-window-seconds",
            "1",
            "--run-rate-limit-per-minute",
            "1",
        ],
    )

    assert first.exit_code == 0
    assert second.exit_code == 0
    records = json.loads(runs_path.read_text(encoding="utf-8"))
    assert records[-1]["status"] == "rate_limited"


def test_query_runs_json(tmp_path: Path) -> None:
    runs_path = tmp_path / "runs.json"
    runs_path.write_text(
        json.dumps(
            [
                {
                    "run_id": "run-1",
                    "trace_id": "trace-1",
                    "status": "ok",
                    "finished_at": "2026-03-29T12:00:00",
                    "metrics": {"duration_ms": 120},
                },
                {
                    "run_id": "run-2",
                    "trace_id": "trace-2",
                    "status": "degraded",
                    "finished_at": "2026-03-29T12:01:00",
                    "metrics": {"duration_ms": 200},
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "query-runs",
            "--runs-path",
            str(runs_path),
            "--status",
            "degraded",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert "run-2" in result.stdout


def test_monitor_es_triggers_slow_trace(tmp_path: Path) -> None:
    runs_path = tmp_path / "runs.json"
    mock_trace = TraceDocument(
        trace_id="trace-monitor-1",
        root=Span(
            span_id="root",
            parent_span_id=None,
            service_name="inventory-service",
            operation_name="reserve",
            duration_ms=1800,
            status="ERROR",
            exception_stack="Read timed out",
            children=[],
        ),
    )

    with patch("nebula_copilot.cli.list_recent_trace_ids") as mocked_list, patch(
        "nebula_copilot.cli.ESRepository"
    ) as mocked_repo_cls:
        mocked_list.return_value = ["trace-monitor-1"]
        mocked_repo = mocked_repo_cls.return_value
        mocked_repo.get_trace.return_value = mock_trace

        result = runner.invoke(
            app,
            [
                "monitor-es",
                "--index",
                "nebula_metrics",
                "--max-iterations",
                "1",
                "--poll-interval-seconds",
                "5",
                "--slow-threshold-ms",
                "1000",
                "--runs-path",
                str(runs_path),
            ],
        )

    assert result.exit_code == 0
    assert runs_path.exists()
    records = json.loads(runs_path.read_text(encoding="utf-8"))
    latest = records[-1]
    assert latest["trace_id"] == "trace-monitor-1"
    assert latest["trigger_source"] == "monitor-es"


def test_monitor_es_deduplicates_between_iterations(tmp_path: Path) -> None:
    runs_path = tmp_path / "runs.json"
    mock_trace = TraceDocument(
        trace_id="trace-monitor-2",
        root=Span(
            span_id="root",
            parent_span_id=None,
            service_name="inventory-service",
            operation_name="reserve",
            duration_ms=1900,
            status="ERROR",
            exception_stack="Read timed out",
            children=[],
        ),
    )

    with patch("nebula_copilot.cli.list_recent_trace_ids") as mocked_list, patch(
        "nebula_copilot.cli.ESRepository"
    ) as mocked_repo_cls, patch("nebula_copilot.cli.sleep") as mocked_sleep:
        mocked_list.return_value = ["trace-monitor-2"]
        mocked_repo = mocked_repo_cls.return_value
        mocked_repo.get_trace.return_value = mock_trace
        mocked_sleep.return_value = None

        result = runner.invoke(
            app,
            [
                "monitor-es",
                "--index",
                "nebula_metrics",
                "--max-iterations",
                "2",
                "--poll-interval-seconds",
                "1",
                "--slow-threshold-ms",
                "1000",
                "--trigger-dedupe-seconds",
                "300",
                "--runs-path",
                str(runs_path),
            ],
        )

    assert result.exit_code == 0
    records = json.loads(runs_path.read_text(encoding="utf-8"))
    monitor_records = [item for item in records if item.get("trigger_source") == "monitor-es"]
    assert len(monitor_records) == 1
