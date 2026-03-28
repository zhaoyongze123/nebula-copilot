from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from nebula_copilot.analyzer import analyze_trace, build_alert_summary
from nebula_copilot.es_client import ESQueryError, fetch_trace_by_id, list_recent_trace_ids
from nebula_copilot.mock_data import DEFAULT_TRACE_ID, load_mock_file, write_mock_file
from nebula_copilot.notifier import NotifyError, push_summary

app = typer.Typer(add_completion=False, help="Nebula-Copilot CLI")
console = Console()
logger = logging.getLogger("nebula_copilot")

DEFAULT_DATA_PATH = Path("data/mock_trace.json")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )


def _print_table(trace_id: str, result) -> None:
    table = Table(title=f"Nebula Trace Spans - {trace_id}", header_style="bold magenta")
    table.add_column("Service")
    table.add_column("Operation")
    table.add_column("Duration(ms)", justify="right")
    table.add_column("Status")
    table.add_column("ErrorType")

    for item in result.top_spans:
        span = item.span
        status_style = "red" if span.status.upper() == "ERROR" else "green"
        table.add_row(
            span.service_name,
            span.operation_name,
            str(span.duration_ms),
            f"[{status_style}]{span.status}[/{status_style}]",
            item.error_type,
        )

    console.print(table)


def _print_diagnosis(result) -> None:
    bottleneck = result.bottleneck
    diagnosis = Text.assemble(
        "瓶颈节点: ",
        (bottleneck.span.service_name, "bold red"),
        "，耗时: ",
        (f"{bottleneck.span.duration_ms}ms", "bold yellow"),
        "，异常类型: ",
        (bottleneck.error_type, "bold red"),
    )
    if bottleneck.span.exception_stack:
        diagnosis.append("\n错误信息: ")
        diagnosis.append(bottleneck.span.exception_stack, "red")

    diagnosis.append("\n建议动作: ")
    diagnosis.append(bottleneck.action_suggestion, "cyan")

    console.print(Panel(diagnosis, title="诊断结论", border_style="red"))


def _render_result(trace_id: str, result, output_format: str) -> str:
    summary = build_alert_summary(result)

    fmt = output_format.lower()
    if fmt == "json":
        console.print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return summary

    if fmt != "table":
        console.print("[red]--format only supports table/json[/red]")
        raise typer.Exit(code=2)

    _print_table(trace_id, result)
    _print_diagnosis(result)
    console.print(Panel(summary, title="可贴群排障摘要", border_style="blue"))
    return summary


def _maybe_push_webhook(push_webhook: str | None, summary: str) -> None:
    if not push_webhook:
        return
    try:
        push_summary(push_webhook, summary)
        console.print("[green]Webhook push succeeded.[/green]")
    except NotifyError as exc:
        console.print(f"[red]Webhook push failed: {exc}[/red]")
        raise typer.Exit(code=1) from exc


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
    format: str = typer.Option("table", "--format", help="Output format: table/json"),
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
        if not source.exists():
            console.print("[yellow]Mock data not found, generating default trace...[/yellow]")
            write_mock_file(source, trace_id, "timeout")

        trace_doc = load_mock_file(source)
        if trace_doc.trace_id != trace_id:
            logger.warning("Trace ID mismatch: expected=%s actual=%s", trace_id, trace_doc.trace_id)
            console.print(
                f"[yellow]Trace ID mismatch. Expected {trace_id}, got {trace_doc.trace_id}. Using file data.[/yellow]"
            )

        result = analyze_trace(trace_doc, top_n=top_n)
        summary = _render_result(trace_doc.trace_id, result, format)
        _maybe_push_webhook(push_webhook, summary)

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
    format: str = typer.Option("table", "--format", help="Output format: table/json"),
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
        trace_doc = fetch_trace_by_id(
            es_url=es_url,
            index=index,
            trace_id=trace_id,
            username=username,
            password=password,
            verify_certs=verify_certs,
            timeout_seconds=timeout_seconds,
        )
        result = analyze_trace(trace_doc, top_n=top_n)
        summary = _render_result(trace_doc.trace_id, result, format)
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
    format: str = typer.Option("table", "--format", help="Output format: table/json"),
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

        if format != "table":
            console.print("[red]--format only supports table/json[/red]")
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


if __name__ == "__main__":
    app()
