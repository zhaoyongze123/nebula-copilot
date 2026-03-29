from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from nebula_copilot.analyzer import DiagnosisResult, SpanDiagnosis, analyze_trace, build_alert_summary
from nebula_copilot.errors import DataSourceError, TraceNotFoundError, TraceValidationError
from nebula_copilot.es_client import ESQueryError, fetch_trace_by_id, list_recent_trace_ids
from nebula_copilot.mock_data import DEFAULT_TRACE_ID, write_mock_file
from nebula_copilot.notifier import NotifyError, push_summary
from nebula_copilot.models import Span
from nebula_copilot.report_schema import NebulaReport, SpanReport
from nebula_copilot.agent.graph import run_agent_graph
from nebula_copilot.repository import ESRepository, LocalJsonRepository
from nebula_copilot.tools.types import ToolRegistry

app = typer.Typer(add_completion=False, help="Nebula-Copilot CLI")
console = Console()
logger = logging.getLogger("nebula_copilot")

DEFAULT_DATA_PATH = Path("data/mock_trace.json")
SLOW_WARNING_MS = 1000
DEFAULT_RUNS_PATH = Path("data/agent_runs.json")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )


def _span_path(root: Span, target: SpanDiagnosis) -> str:
    path: list[str] = []

    def _dfs(node: Span, current: list[str]) -> bool:
        current.append(node.service_name)
        if node.span_id == target.span.span_id:
            path.extend(current)
            return True
        for child in node.children:
            if _dfs(child, current.copy()):
                return True
        return False

    if not _dfs(root, []):
        return target.span.service_name
    return " > ".join(path)


def _severity_style(duration_ms: int, status: str) -> str:
    if status.upper() == "ERROR":
        return "bold red"
    if duration_ms > SLOW_WARNING_MS:
        return "yellow"
    return "green"


def _print_header_panel(result: DiagnosisResult) -> None:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text = Text.assemble(
        ("TraceID: ", "bold"),
        (result.trace_id, "bold cyan"),
        ("\n分析时间: ", "bold"),
        (generated_at, "white"),
        ("\n节点总数: ", "bold"),
        (str(result.total_spans), "white"),
    )
    console.print(Panel(text, title="Nebula Trace Analyzer", border_style="blue"))


def _print_summary_table(result: DiagnosisResult, root: Span) -> None:
    b = result.bottleneck
    table = Table(title="诊断摘要", header_style="bold magenta")
    table.add_column("Bottleneck Service")
    table.add_column("Duration(ms)", justify="right")
    table.add_column("Status")
    table.add_column("Path")

    style = _severity_style(b.span.duration_ms, b.span.status)
    table.add_row(
        f"[{style}]{b.span.service_name}[/{style}]",
        f"[{style}]{b.span.duration_ms}[/{style}]",
        f"[{style}]{b.span.status}[/{style}]",
        _span_path(root, b),
    )
    console.print(table)


def _print_top_spans_table(result: DiagnosisResult) -> None:
    table = Table(title=f"Top {len(result.top_spans)} Slow Spans", header_style="bold cyan")
    table.add_column("Service")
    table.add_column("Operation")
    table.add_column("Duration(ms)", justify="right")
    table.add_column("Status")
    table.add_column("ErrorType")

    for item in result.top_spans:
        span = item.span
        style = _severity_style(span.duration_ms, span.status)
        table.add_row(
            f"[{style}]{span.service_name}[/{style}]",
            span.operation_name,
            f"[{style}]{span.duration_ms}[/{style}]",
            f"[{style}]{span.status}[/{style}]",
            item.error_type,
        )

    console.print(table)


def _print_diagnosis(result: DiagnosisResult) -> None:
    bottleneck = result.bottleneck
    err_summary = bottleneck.span.exception_stack or (
        "有错误但未提供堆栈" if bottleneck.span.status.upper() == "ERROR" else "无异常"
    )

    diagnosis = Text.assemble(
        "瓶颈节点为 ",
        (bottleneck.span.service_name, "bold red"),
        "，耗时 ",
        (f"{bottleneck.span.duration_ms}ms", "bold yellow"),
        "，状态 ",
        (bottleneck.span.status, "bold red" if bottleneck.span.status.upper() == "ERROR" else "green"),
        "。\n异常摘要：",
        (err_summary, "red" if bottleneck.span.status.upper() == "ERROR" else "green"),
        "\n建议动作：",
        (bottleneck.action_suggestion, "cyan"),
    )

    console.print(Panel(diagnosis, title="可读结论", border_style="red"))


def _to_span_report(item: SpanDiagnosis) -> SpanReport:
    return SpanReport(
        service_name=item.span.service_name,
        operation_name=item.span.operation_name,
        duration_ms=item.span.duration_ms,
        status=item.span.status,
        error_type=item.error_type,
        exception_stack=item.span.exception_stack,
        action_suggestion=item.action_suggestion,
    )


def _build_report(result: DiagnosisResult) -> NebulaReport:
    summary = build_alert_summary(result)
    return NebulaReport(
        trace_id=result.trace_id,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        summary=summary,
        bottleneck=_to_span_report(result.bottleneck),
        top_spans=[_to_span_report(item) for item in result.top_spans],
        channel_text=summary,
    )


def _render_result(trace_root: Span, result: DiagnosisResult, output_format: str) -> str:
    report = _build_report(result)
    fmt = output_format.lower()

    if fmt == "table":
        fmt = "rich"

    if fmt == "json":
        console.print(report.model_dump_json(indent=2, ensure_ascii=False))
        return report.channel_text

    if fmt != "rich":
        console.print("[red]--format only supports rich/json (table is alias of rich)[/red]")
        raise typer.Exit(code=2)

    _print_header_panel(result)
    _print_summary_table(result, trace_root)
    _print_top_spans_table(result)
    _print_diagnosis(result)
    console.print(Panel(report.channel_text, title="可贴群排障摘要", border_style="blue"))
    return report.channel_text


def _maybe_push_webhook(push_webhook: str | None, summary: str) -> None:
    if not push_webhook:
        return
    try:
        push_summary(push_webhook, summary)
        console.print("[green]Webhook push succeeded.[/green]")
    except NotifyError as exc:
        console.print(f"[red]Webhook push failed: {exc}[/red]")
        raise typer.Exit(code=1) from exc


def _print_data_error(prefix: str, exc: Exception) -> None:
    if isinstance(exc, TraceNotFoundError):
        console.print(f"[red]{prefix}: 未找到目标 Trace。{exc}[/red]")
        console.print("[yellow]建议：请检查 trace_id 或 mock 文件内容。[/yellow]")
    elif isinstance(exc, TraceValidationError):
        console.print(f"[red]{prefix}: Trace 数据校验失败。{exc}[/red]")
        console.print("[yellow]建议：请检查 JSON 字段是否完整且类型正确。[/yellow]")
    elif isinstance(exc, DataSourceError):
        console.print(f"[red]{prefix}: 数据源读取失败。{exc}[/red]")
        console.print("[yellow]建议：请检查文件路径和读取权限。[/yellow]")
    else:
        raise exc


def _append_run_record(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                data = []
        except (json.JSONDecodeError, OSError):
            data = []
    else:
        data = []

    data.append(record)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@app.command()
def seed(
    trace_id: str = typer.Argument(DEFAULT_TRACE_ID, help="Trace id for mock data"),
    output: Path = typer.Option(DEFAULT_DATA_PATH, "--output", "-o", help="Output JSON file"),
    scenario: str = typer.Option(
        "timeout",
        "--scenario",
        help="Scenario: timeout/db/downstream",
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Enable debug logs"),
) -> None:
    """Generate mock trace data locally."""
    _setup_logging(verbose)
    try:
        path = write_mock_file(output, trace_id, scenario)
        logger.info("Mock trace generated: %s", path)
        console.print(f"Mock trace saved to [bold cyan]{path}[/bold cyan]")
    except Exception as exc:  # pragma: no cover
        logger.exception("Failed to generate mock trace")
        raise typer.Exit(code=1) from exc


@app.command()
def analyze(
    trace_id: str = typer.Argument(..., help="Trace id to analyze"),
    source: Path = typer.Option(DEFAULT_DATA_PATH, "--source", "-s", help="Trace JSON file"),
    format: str = typer.Option("rich", "--format", help="Output format: rich/json"),
    top_n: int = typer.Option(3, "--top-n", help="Top N slow spans"),
    push_webhook: str | None = typer.Option(None, "--push-webhook", help="Feishu/DingTalk webhook URL"),
    verbose: bool = typer.Option(False, "--verbose", help="Enable debug logs"),
) -> None:
    """Analyze trace from local JSON file."""
    _setup_logging(verbose)

    if top_n <= 0:
        console.print("[red]--top-n must be > 0[/red]")
        raise typer.Exit(code=2)

    try:
        repository = LocalJsonRepository(source)
        trace_doc = repository.get_trace(trace_id)

        result = analyze_trace(trace_doc, top_n=top_n)
        summary = _render_result(trace_doc.root, result, format)
        _maybe_push_webhook(push_webhook, summary)

    except (TraceNotFoundError, TraceValidationError, DataSourceError) as exc:
        logger.error("Analyze failed: %s", exc)
        _print_data_error("Analyze failed", exc)
        raise typer.Exit(code=1) from exc
    except typer.Exit:
        raise
    except Exception as exc:  # pragma: no cover
        logger.exception("Analyze failed")
        console.print(f"[red]Analyze failed: {exc}[/red]")
        raise typer.Exit(code=1) from exc


@app.command("analyze-es")
def analyze_es(
    trace_id: str = typer.Argument(..., help="Trace id to analyze from Elasticsearch"),
    index: str = typer.Option(..., "--index", help="ES index name, e.g. nebula-trace-*"),
    es_url: str = typer.Option(
        "http://localhost:9200",
        "--es-url",
        envvar="NEBULA_ES_URL",
        help="Elasticsearch URL, can set by NEBULA_ES_URL",
    ),
    username: str | None = typer.Option(
        None,
        "--username",
        envvar="NEBULA_ES_USERNAME",
        help="ES username",
    ),
    password: str | None = typer.Option(
        None,
        "--password",
        envvar="NEBULA_ES_PASSWORD",
        help="ES password",
        prompt=False,
        hide_input=True,
    ),
    verify_certs: bool = typer.Option(True, "--verify-certs/--no-verify-certs", help="Verify TLS certs"),
    timeout_seconds: int = typer.Option(10, "--timeout-seconds", help="ES request timeout"),
    format: str = typer.Option("rich", "--format", help="Output format: rich/json"),
    top_n: int = typer.Option(3, "--top-n", help="Top N slow spans"),
    push_webhook: str | None = typer.Option(None, "--push-webhook", help="Feishu/DingTalk webhook URL"),
    verbose: bool = typer.Option(False, "--verbose", help="Enable debug logs"),
) -> None:
    """Analyze trace by querying Elasticsearch directly."""
    _setup_logging(verbose)

    if top_n <= 0:
        console.print("[red]--top-n must be > 0[/red]")
        raise typer.Exit(code=2)

    if password is None and username and os.getenv("NEBULA_ES_PASSWORD"):
        password = os.getenv("NEBULA_ES_PASSWORD")

    try:
        repository = ESRepository(
            es_url=es_url,
            index=index,
            username=username,
            password=password,
            verify_certs=verify_certs,
            timeout_seconds=timeout_seconds,
        )
        trace_doc = repository.get_trace(trace_id)
        result = analyze_trace(trace_doc, top_n=top_n)
        summary = _render_result(trace_doc.root, result, format)
        _maybe_push_webhook(push_webhook, summary)

    except ESQueryError as exc:
        logger.error("ES query failed: %s", exc)
        console.print(f"[red]ES query failed: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    except typer.Exit:
        raise
    except Exception as exc:  # pragma: no cover
        logger.exception("Analyze ES failed")
        console.print(f"[red]Analyze ES failed: {exc}[/red]")
        raise typer.Exit(code=1) from exc


@app.command("list-traces")
def list_traces(
    index: str = typer.Option(..., "--index", help="ES index name, e.g. nebula_metrics"),
    last_minutes: int = typer.Option(30, "--last-minutes", help="Time window in minutes"),
    limit: int = typer.Option(20, "--limit", help="Maximum trace IDs to return"),
    es_url: str = typer.Option(
        "http://localhost:9200",
        "--es-url",
        envvar="NEBULA_ES_URL",
        help="Elasticsearch URL, can set by NEBULA_ES_URL",
    ),
    username: str | None = typer.Option(None, "--username", envvar="NEBULA_ES_USERNAME", help="ES username"),
    password: str | None = typer.Option(
        None,
        "--password",
        envvar="NEBULA_ES_PASSWORD",
        help="ES password",
        prompt=False,
        hide_input=True,
    ),
    verify_certs: bool = typer.Option(True, "--verify-certs/--no-verify-certs", help="Verify TLS certs"),
    timeout_seconds: int = typer.Option(10, "--timeout-seconds", help="ES request timeout"),
    format: str = typer.Option("rich", "--format", help="Output format: rich/json"),
    verbose: bool = typer.Option(False, "--verbose", help="Enable debug logs"),
) -> None:
    """List recent trace IDs from Elasticsearch."""
    _setup_logging(verbose)

    if last_minutes <= 0:
        console.print("[red]--last-minutes must be > 0[/red]")
        raise typer.Exit(code=2)
    if limit <= 0:
        console.print("[red]--limit must be > 0[/red]")
        raise typer.Exit(code=2)

    try:
        trace_ids = list_recent_trace_ids(
            es_url=es_url,
            index=index,
            last_minutes=last_minutes,
            limit=limit,
            username=username,
            password=password,
            verify_certs=verify_certs,
            timeout_seconds=timeout_seconds,
        )

        if format == "json":
            console.print(json.dumps({"trace_ids": trace_ids}, ensure_ascii=False, indent=2))
            return

        normalized_format = "rich" if format == "table" else format
        if normalized_format != "rich":
            console.print("[red]--format only supports rich/json (table is alias of rich)[/red]")
            raise typer.Exit(code=2)

        table = Table(title=f"Recent Trace IDs (last {last_minutes} minutes)", header_style="bold cyan")
        table.add_column("#", justify="right")
        table.add_column("TraceID")
        for idx, trace_id in enumerate(trace_ids, start=1):
            table.add_row(str(idx), trace_id)
        console.print(table)

    except ESQueryError as exc:
        logger.error("List traces failed: %s", exc)
        console.print(f"[red]List traces failed: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    except typer.Exit:
        raise
    except Exception as exc:  # pragma: no cover
        logger.exception("List traces failed")
        console.print(f"[red]List traces failed: {exc}[/red]")
        raise typer.Exit(code=1) from exc


@app.command("agent-analyze")
def agent_analyze(
    trace_id: str = typer.Argument(..., help="Trace id to analyze"),
    source: Path = typer.Option(DEFAULT_DATA_PATH, "--source", "-s", help="Trace JSON file"),
    push_webhook: str | None = typer.Option(None, "--push-webhook", help="Feishu/DingTalk webhook URL"),
    runs_path: Path = typer.Option(DEFAULT_RUNS_PATH, "--runs-path", help="run_id 持久化文件路径"),
    verbose: bool = typer.Option(False, "--verbose", help="Enable debug logs"),
) -> None:
    """Agent 入口：执行取数→诊断→补充信息→通知，并记录 run_id。"""
    _setup_logging(verbose)

    run_id = f"run-{uuid.uuid4().hex[:12]}"
    started_at = datetime.now().isoformat(timespec="seconds")

    try:
        repository = LocalJsonRepository(source)

        tool_registry = ToolRegistry(
            query_trace=lambda tid: {
                "trace_id": tid,
                "bottleneck_service": repository.get_trace(tid).root.service_name,
                "keyword": "timeout",
            },
            query_jvm=lambda service_name: {"service": service_name, "heap_used_mb": 512, "gc_count": 3},
            query_logs=lambda service_name, keyword: {
                "service": service_name,
                "keyword": keyword,
                "sample": ["timeout while waiting for downstream", "retry exhausted"],
            },
        )

        trace_doc = repository.get_trace(trace_id)
        graph_result = run_agent_graph(trace_id, run_id, trace_doc, tool_registry)
        summary = str(graph_result.get("summary") or "")

        _maybe_push_webhook(push_webhook, summary)

        _append_run_record(
            runs_path,
            {
                **graph_result,
                "started_at": started_at,
                "finished_at": datetime.now().isoformat(timespec="seconds"),
            },
        )

        console.print(Panel(summary, title="Agent Analyze 完成", border_style="green"))
        console.print(f"[cyan]run_id: {run_id}[/cyan]")

        if graph_result.get("status") != "ok":
            raise typer.Exit(code=1)

    except (TraceNotFoundError, TraceValidationError, DataSourceError) as exc:
        _append_run_record(
            runs_path,
            {
                "run_id": run_id,
                "trace_id": trace_id,
                "status": "failed",
                "started_at": started_at,
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "error": str(exc),
            },
        )
        _print_data_error("Agent analyze failed", exc)
        raise typer.Exit(code=1) from exc
    except NotifyError as exc:
        _append_run_record(
            runs_path,
            {
                "run_id": run_id,
                "trace_id": trace_id,
                "status": "failed",
                "started_at": started_at,
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "error": str(exc),
            },
        )
        console.print(f"[red]Agent analyze notify failed: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    except typer.Exit:
        raise
    except Exception as exc:  # pragma: no cover
        _append_run_record(
            runs_path,
            {
                "run_id": run_id,
                "trace_id": trace_id,
                "status": "failed",
                "started_at": started_at,
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "error": str(exc),
            },
        )
        logger.exception("Agent analyze failed")
        console.print(f"[red]Agent analyze failed: {exc}[/red]")
        raise typer.Exit(code=1) from exc


if __name__ == "__main__":
    app()
