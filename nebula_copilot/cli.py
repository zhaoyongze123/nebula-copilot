from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from time import sleep
from typing import Callable

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from nebula_copilot.analyzer import DiagnosisResult, SpanDiagnosis, analyze_trace, build_alert_summary
from nebula_copilot.errors import DataSourceError, TraceNotFoundError, TraceValidationError
from nebula_copilot.es_client import (
    ESQueryError,
    fetch_trace_by_id,
    list_recent_trace_ids,
    query_service_jvm_metrics,
    search_service_logs,
)
from nebula_copilot.mock_data import DEFAULT_TRACE_ID, write_mock_file
from nebula_copilot.notifier import NotifyError, push_summary, push_summary_reliable
from nebula_copilot.models import Span
from nebula_copilot.report_schema import NebulaReport, SpanReport
from nebula_copilot.agent.graph import run_agent_graph
from nebula_copilot.config import load_app_config
from nebula_copilot.llm.executor import LLMExecutor, LLMSettings
from nebula_copilot.runtime_guard import evaluate_run_guard
from nebula_copilot.repository import ESRepository, LocalJsonRepository
from nebula_copilot.tools.types import ToolRegistry

app = typer.Typer(add_completion=False, help="Nebula-Copilot CLI")
console = Console()
logger = logging.getLogger("nebula_copilot")

DEFAULT_DATA_PATH = Path("data/mock_trace.json")
SLOW_WARNING_MS = 1000
DEFAULT_RUNS_PATH = Path("data/agent_runs.json")
DEFAULT_ENV_PATH = Path(".env")


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


def _notify_with_reliability(
    push_webhook: str | None,
    summary: str,
    *,
    dedupe_key: str,
    dedupe_path: Path,
    dedupe_window_seconds: int,
    max_retries: int,
) -> dict[str, object]:
    if not push_webhook:
        return {
            "status": "skipped",
            "deduplicated": False,
            "attempts": 0,
            "error": None,
        }

    result = push_summary_reliable(
        push_webhook,
        summary,
        dedupe_key,
        dedupe_cache_path=dedupe_path,
        dedupe_window_seconds=dedupe_window_seconds,
        max_retries=max_retries,
    )
    if result.status == "ok":
        console.print("[green]Webhook push succeeded.[/green]")
    elif result.status == "skipped" and result.deduplicated:
        console.print("[yellow]Webhook skipped due to dedup window.[/yellow]")
    else:
        console.print(f"[red]Webhook push failed after retries: {result.error}[/red]")

    return {
        "status": result.status,
        "deduplicated": result.deduplicated,
        "attempts": result.attempts,
        "error": result.error,
    }


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


def _load_run_records(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _build_llm_executor(env_file: Path, cli_llm_enabled: bool) -> LLMExecutor:
    cfg = load_app_config(env_file)
    enabled = cli_llm_enabled or cfg.llm.enabled
    if not enabled:
        return LLMExecutor.disabled()

    return LLMExecutor(
        LLMSettings(
            enabled=enabled,
            provider=cfg.llm.provider,
            model=cfg.llm.model,
            api_key=cfg.llm.api_key,
            base_url=cfg.llm.base_url,
            timeout_ms=cfg.llm.timeout_ms,
            max_retry=cfg.llm.max_retry,
            report_polish_enabled=cfg.llm.report_polish_enabled,
        )
    )


def _build_es_enrichment_registry(
    *,
    query_trace: Callable[[str], dict[str, object]],
    es_url: str,
    index: str,
    username: str | None,
    password: str | None,
    verify_certs: bool,
    timeout_seconds: int,
    last_minutes: int,
    logs_limit: int,
) -> ToolRegistry:
    return ToolRegistry(
        query_trace=query_trace,
        query_jvm=lambda service_name: query_service_jvm_metrics(
            es_url=es_url,
            index=index,
            service_name=service_name,
            last_minutes=last_minutes,
            username=username,
            password=password,
            verify_certs=verify_certs,
            timeout_seconds=timeout_seconds,
        ),
        query_logs=lambda service_name, keyword: search_service_logs(
            es_url=es_url,
            index=index,
            service_name=service_name,
            keyword=keyword,
            last_minutes=last_minutes,
            limit=logs_limit,
            username=username,
            password=password,
            verify_certs=verify_certs,
            timeout_seconds=timeout_seconds,
        ),
    )


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
    env_file: Path = typer.Option(DEFAULT_ENV_PATH, "--env-file", help=".env 文件路径"),
    llm_enabled: bool = typer.Option(False, "--llm-enabled/--no-llm-enabled", help="启用 LLM 建议生成"),
    verbose: bool = typer.Option(False, "--verbose", help="Enable debug logs"),
) -> None:
    """Analyze trace from local JSON file."""
    _setup_logging(verbose)

    if top_n <= 0:
        console.print("[red]--top-n must be > 0[/red]")
        raise typer.Exit(code=2)

    try:
        llm_executor = _build_llm_executor(env_file, llm_enabled)
        repository = LocalJsonRepository(source)
        trace_doc = repository.get_trace(trace_id)

        result = analyze_trace(trace_doc, top_n=top_n, llm_executor=llm_executor)
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
    env_file: Path = typer.Option(DEFAULT_ENV_PATH, "--env-file", help=".env 文件路径"),
    llm_enabled: bool = typer.Option(False, "--llm-enabled/--no-llm-enabled", help="启用 LLM 建议生成"),
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
        llm_executor = _build_llm_executor(env_file, llm_enabled)
        repository = ESRepository(
            es_url=es_url,
            index=index,
            username=username,
            password=password,
            verify_certs=verify_certs,
            timeout_seconds=timeout_seconds,
        )
        trace_doc = repository.get_trace(trace_id)
        result = analyze_trace(trace_doc, top_n=top_n, llm_executor=llm_executor)
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
    enrich_index: str = typer.Option("nebula_metrics", "--enrich-index", help="用于JVM/日志补充查询的ES index"),
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
    enrich_last_minutes: int = typer.Option(30, "--enrich-last-minutes", help="JVM/日志补充查询时间窗（分钟）"),
    logs_limit: int = typer.Option(5, "--logs-limit", help="日志样本条数"),
    push_webhook: str | None = typer.Option(None, "--push-webhook", help="Feishu/DingTalk webhook URL"),
    runs_path: Path = typer.Option(DEFAULT_RUNS_PATH, "--runs-path", help="run_id 持久化文件路径"),
    notify_dedupe_path: Path = typer.Option(
        Path("data/notify_dedupe.json"),
        "--notify-dedupe-path",
        help="通知去重缓存文件路径",
    ),
    notify_dedupe_window_seconds: int = typer.Option(
        300,
        "--notify-dedupe-window-seconds",
        help="通知去重时间窗（秒）",
    ),
    notify_max_retries: int = typer.Option(3, "--notify-max-retries", help="通知最大重试次数"),
    run_guard_path: Path | None = typer.Option(None, "--run-guard-path", help="执行守卫缓存路径"),
    run_dedupe_window_seconds: int | None = typer.Option(None, "--run-dedupe-window-seconds", help="执行去重时间窗（秒）"),
    run_rate_limit_per_minute: int | None = typer.Option(None, "--run-rate-limit-per-minute", help="每分钟最大执行次数，0=不限制"),
    env_file: Path = typer.Option(DEFAULT_ENV_PATH, "--env-file", help=".env 文件路径"),
    llm_enabled: bool = typer.Option(False, "--llm-enabled/--no-llm-enabled", help="启用 LLM 能力"),
    llm_decision_required: bool = typer.Option(
        False,
        "--llm-decision-required/--no-llm-decision-required",
        help="要求LLM参与决策，失败则本次执行失败",
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Enable debug logs"),
) -> None:
    """Agent 入口：执行取数→诊断→补充信息→通知，并记录 run_id。"""
    _setup_logging(verbose)

    run_id = f"run-{uuid.uuid4().hex[:12]}"
    started_at = datetime.now().isoformat(timespec="seconds")
    cfg = load_app_config(env_file)

    effective_guard_path = run_guard_path or cfg.run_guard_path
    effective_dedupe_window = run_dedupe_window_seconds or cfg.run_dedupe_window_seconds
    effective_rate_limit = cfg.run_rate_limit_per_minute if run_rate_limit_per_minute is None else max(0, run_rate_limit_per_minute)
    if password is None and username and os.getenv("NEBULA_ES_PASSWORD"):
        password = os.getenv("NEBULA_ES_PASSWORD")

    try:
        llm_executor = _build_llm_executor(env_file, llm_enabled)
        repository = LocalJsonRepository(source)
        trace_doc = repository.get_trace(trace_id)
        tool_registry = _build_es_enrichment_registry(
            query_trace=lambda tid: {
                "trace_id": tid,
                "bottleneck_service": trace_doc.root.service_name,
                "keyword": str(trace_doc.root.status).lower(),
            },
            es_url=es_url,
            index=enrich_index,
            username=username,
            password=password,
            verify_certs=verify_certs,
            timeout_seconds=timeout_seconds,
            last_minutes=max(1, enrich_last_minutes),
            logs_limit=max(1, logs_limit),
        )

        guard_result = evaluate_run_guard(
            path=effective_guard_path,
            trace_id=trace_id,
            run_id=run_id,
            dedupe_window_seconds=max(1, effective_dedupe_window),
            rate_limit_per_minute=effective_rate_limit,
        )
        if not guard_result["allowed"]:
            status = str(guard_result["status"])
            _append_run_record(
                runs_path,
                {
                    "run_id": run_id,
                    "trace_id": trace_id,
                    "status": status,
                    "started_at": started_at,
                    "finished_at": datetime.now().isoformat(timespec="seconds"),
                    "guard": guard_result,
                    "notify": {"status": "skipped", "attempts": 0, "deduplicated": False, "error": None},
                    "metrics": {
                        "duration_ms": 0,
                        "history_events": 0,
                    },
                },
            )
            console.print(f"[yellow]Agent analyze skipped: {guard_result['status']} ({guard_result['reason']})[/yellow]")
            console.print(f"[cyan]run_id: {run_id}[/cyan]")
            return

        graph_result = run_agent_graph(
            trace_id,
            run_id,
            trace_doc,
            tool_registry,
            llm_executor=llm_executor,
            llm_decision_required=llm_decision_required,
        )
        summary = str(graph_result.get("summary") or "")
        notify_result = _notify_with_reliability(
            push_webhook,
            summary,
            dedupe_key=f"{trace_id}:{push_webhook or 'none'}",
            dedupe_path=notify_dedupe_path,
            dedupe_window_seconds=max(1, notify_dedupe_window_seconds),
            max_retries=max(1, notify_max_retries),
        )

        run_status = str(graph_result.get("status") or "failed")
        if notify_result["status"] == "failed" and run_status == "ok":
            run_status = "degraded"
        finished_at = datetime.now().isoformat(timespec="seconds")
        duration_ms = int((datetime.fromisoformat(finished_at) - datetime.fromisoformat(started_at)).total_seconds() * 1000)

        history = graph_result.get("history") if isinstance(graph_result.get("history"), list) else []
        failure_events = [item for item in history if isinstance(item, dict) and str(item.get("status")) in {"failed", "fallback"}]

        _append_run_record(
            runs_path,
            {
                **graph_result,
                "status": run_status,
                "guard": guard_result,
                "notify": notify_result,
                "started_at": started_at,
                "finished_at": finished_at,
                "metrics": {
                    "duration_ms": duration_ms,
                    "history_events": len(history),
                    "failure_events": len(failure_events),
                    "notify_status": notify_result.get("status"),
                },
            },
        )

        console.print(Panel(summary, title="Agent Analyze 完成", border_style="green"))
        console.print(f"[cyan]run_id: {run_id}[/cyan]")

        if run_status not in {"ok", "degraded"}:
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


@app.command("monitor-es")
def monitor_es(
    index: str = typer.Option(..., "--index", help="ES index name, e.g. nebula_metrics"),
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
    poll_interval_seconds: int = typer.Option(5, "--poll-interval-seconds", help="轮询间隔秒数"),
    last_minutes: int = typer.Option(5, "--last-minutes", help="扫描最近多少分钟 trace"),
    limit: int = typer.Option(20, "--limit", help="每轮扫描最大 trace 数"),
    slow_threshold_ms: int = typer.Option(1000, "--slow-threshold-ms", help="慢链路阈值（毫秒）"),
    trigger_dedupe_seconds: int = typer.Option(300, "--trigger-dedupe-seconds", help="同一 trace 触发去重窗口（秒）"),
    max_iterations: int = typer.Option(0, "--max-iterations", help="最大轮询次数，0 表示持续运行"),
    push_webhook: str | None = typer.Option(None, "--push-webhook", help="Feishu/DingTalk webhook URL"),
    runs_path: Path = typer.Option(DEFAULT_RUNS_PATH, "--runs-path", help="run 记录文件路径"),
    notify_dedupe_path: Path = typer.Option(Path("data/notify_dedupe.json"), "--notify-dedupe-path", help="通知去重缓存文件路径"),
    notify_dedupe_window_seconds: int = typer.Option(300, "--notify-dedupe-window-seconds", help="通知去重窗口（秒）"),
    notify_max_retries: int = typer.Option(3, "--notify-max-retries", help="通知最大重试次数"),
    env_file: Path = typer.Option(DEFAULT_ENV_PATH, "--env-file", help=".env 文件路径"),
    llm_enabled: bool = typer.Option(False, "--llm-enabled/--no-llm-enabled", help="启用 LLM 能力"),
    llm_decision_required: bool = typer.Option(
        False,
        "--llm-decision-required/--no-llm-decision-required",
        help="要求LLM参与决策，失败则本次执行记为failed",
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Enable debug logs"),
) -> None:
    """持续监控 ES，发现慢链路后自动触发诊断并推送通知。"""
    _setup_logging(verbose)

    if poll_interval_seconds <= 0:
        console.print("[red]--poll-interval-seconds must be > 0[/red]")
        raise typer.Exit(code=2)
    if last_minutes <= 0:
        console.print("[red]--last-minutes must be > 0[/red]")
        raise typer.Exit(code=2)
    if limit <= 0:
        console.print("[red]--limit must be > 0[/red]")
        raise typer.Exit(code=2)
    if slow_threshold_ms <= 0:
        console.print("[red]--slow-threshold-ms must be > 0[/red]")
        raise typer.Exit(code=2)
    if trigger_dedupe_seconds <= 0:
        console.print("[red]--trigger-dedupe-seconds must be > 0[/red]")
        raise typer.Exit(code=2)
    if max_iterations < 0:
        console.print("[red]--max-iterations must be >= 0[/red]")
        raise typer.Exit(code=2)

    if password is None and username and os.getenv("NEBULA_ES_PASSWORD"):
        password = os.getenv("NEBULA_ES_PASSWORD")

    llm_executor = _build_llm_executor(env_file, llm_enabled)
    repository = ESRepository(
        es_url=es_url,
        index=index,
        username=username,
        password=password,
        verify_certs=verify_certs,
        timeout_seconds=timeout_seconds,
    )

    triggered_cache: dict[str, datetime] = {}
    iteration = 0

    while True:
        iteration += 1
        now = datetime.now()
        triggered_cache = {
            tid: ts
            for tid, ts in triggered_cache.items()
            if now - ts <= timedelta(seconds=trigger_dedupe_seconds)
        }

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
        except ESQueryError as exc:
            console.print(f"[red]monitor-es query failed: {exc}[/red]")
            if max_iterations > 0 and iteration >= max_iterations:
                raise typer.Exit(code=1) from exc
            sleep(poll_interval_seconds)
            continue

        triggered_count = 0
        for trace_id in trace_ids:
            if trace_id in triggered_cache:
                continue

            try:
                trace_doc = repository.get_trace(trace_id)
                quick_result = analyze_trace(trace_doc, top_n=1, llm_executor=llm_executor)
            except (ESQueryError, TraceValidationError, DataSourceError) as exc:
                logger.warning("monitor-es skip trace_id=%s, reason=%s", trace_id, exc)
                continue

            bottleneck_ms = quick_result.bottleneck.span.duration_ms
            if bottleneck_ms < slow_threshold_ms:
                continue

            run_id = f"run-{uuid.uuid4().hex[:12]}"
            started_at = datetime.now().isoformat(timespec="seconds")

            keyword = str(quick_result.bottleneck.error_type or "timeout").lower()
            tool_registry = _build_es_enrichment_registry(
                query_trace=lambda tid: {
                    "trace_id": tid,
                    "bottleneck_service": quick_result.bottleneck.span.service_name,
                    "keyword": keyword,
                },
                es_url=es_url,
                index=index,
                username=username,
                password=password,
                verify_certs=verify_certs,
                timeout_seconds=timeout_seconds,
                last_minutes=last_minutes,
                logs_limit=5,
            )

            graph_result = run_agent_graph(
                trace_id,
                run_id,
                trace_doc,
                tool_registry,
                llm_executor=llm_executor,
                llm_decision_required=llm_decision_required,
            )
            summary = str(graph_result.get("summary") or "")

            notify_result = _notify_with_reliability(
                push_webhook,
                summary,
                dedupe_key=f"{trace_id}:{push_webhook or 'none'}",
                dedupe_path=notify_dedupe_path,
                dedupe_window_seconds=max(1, notify_dedupe_window_seconds),
                max_retries=max(1, notify_max_retries),
            )

            run_status = str(graph_result.get("status") or "failed")
            if notify_result["status"] == "failed" and run_status == "ok":
                run_status = "degraded"

            finished_at = datetime.now().isoformat(timespec="seconds")
            duration_ms = int((datetime.fromisoformat(finished_at) - datetime.fromisoformat(started_at)).total_seconds() * 1000)
            history = graph_result.get("history") if isinstance(graph_result.get("history"), list) else []

            _append_run_record(
                runs_path,
                {
                    **graph_result,
                    "status": run_status,
                    "trigger_source": "monitor-es",
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "notify": notify_result,
                    "metrics": {
                        "duration_ms": duration_ms,
                        "history_events": len(history),
                        "failure_events": len(
                            [
                                item
                                for item in history
                                if isinstance(item, dict) and str(item.get("status")) in {"failed", "fallback"}
                            ]
                        ),
                        "notify_status": notify_result.get("status"),
                        "bottleneck_duration_ms": bottleneck_ms,
                        "slow_threshold_ms": slow_threshold_ms,
                    },
                },
            )

            triggered_cache[trace_id] = datetime.now()
            triggered_count += 1
            console.print(
                f"[green]monitor-es triggered trace={trace_id}, bottleneck={bottleneck_ms}ms, status={run_status}[/green]"
            )

        console.print(
            f"[cyan]monitor-es iteration={iteration}, scanned={len(trace_ids)}, triggered={triggered_count}[/cyan]"
        )

        if max_iterations > 0 and iteration >= max_iterations:
            return

        sleep(poll_interval_seconds)


@app.command("query-runs")
def query_runs(
    runs_path: Path = typer.Option(DEFAULT_RUNS_PATH, "--runs-path", help="run_id 持久化文件路径"),
    trace_id: str | None = typer.Option(None, "--trace-id", help="按 trace_id 过滤"),
    status: str | None = typer.Option(None, "--status", help="按状态过滤"),
    limit: int = typer.Option(20, "--limit", help="返回条数上限"),
    format: str = typer.Option("rich", "--format", help="输出格式: rich/json"),
) -> None:
    """查询 agent 运行记录（支持 trace_id/status 过滤）。"""
    if limit <= 0:
        console.print("[red]--limit must be > 0[/red]")
        raise typer.Exit(code=2)

    records = _load_run_records(runs_path)
    filtered = []
    for item in records:
        if trace_id and str(item.get("trace_id")) != trace_id:
            continue
        if status and str(item.get("status")) != status:
            continue
        filtered.append(item)

    result = filtered[-limit:]
    if format == "json":
        console.print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if format != "rich":
        console.print("[red]--format only supports rich/json[/red]")
        raise typer.Exit(code=2)

    table = Table(title="Agent Runs", header_style="bold cyan")
    table.add_column("run_id")
    table.add_column("trace_id")
    table.add_column("status")
    table.add_column("duration_ms", justify="right")
    table.add_column("finished_at")

    for item in reversed(result):
        metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
        duration_ms = str(metrics.get("duration_ms", "-"))
        table.add_row(
            str(item.get("run_id", "")),
            str(item.get("trace_id", "")),
            str(item.get("status", "")),
            duration_ms,
            str(item.get("finished_at", "")),
        )
    console.print(table)


if __name__ == "__main__":
    app()
