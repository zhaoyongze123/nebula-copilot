from nebula_copilot.analyzer import analyze_trace, build_alert_summary
from nebula_copilot.mock_data import build_mock_trace
from nebula_copilot.report_schema import NebulaReport, SpanReport


def _to_span_report(item) -> SpanReport:
    return SpanReport(
        service_name=item.span.service_name,
        operation_name=item.span.operation_name,
        duration_ms=item.span.duration_ms,
        status=item.span.status,
        error_type=item.error_type,
        exception_stack=item.span.exception_stack,
        action_suggestion=item.action_suggestion,
    )


def test_nebula_report_json_contract_fields_complete() -> None:
    trace = build_mock_trace("trace-contract", "timeout")
    diagnosis = analyze_trace(trace, top_n=2)
    summary = build_alert_summary(diagnosis)

    report = NebulaReport(
        trace_id=diagnosis.trace_id,
        generated_at="2026-03-28T10:00:00",
        summary=summary,
        bottleneck=_to_span_report(diagnosis.bottleneck),
        top_spans=[_to_span_report(item) for item in diagnosis.top_spans],
        channel_text=summary,
    )

    payload = report.model_dump()

    assert set(payload.keys()) == {
        "trace_id",
        "generated_at",
        "summary",
        "bottleneck",
        "top_spans",
        "channel_text",
    }

    assert set(payload["bottleneck"].keys()) == {
        "service_name",
        "operation_name",
        "duration_ms",
        "status",
        "error_type",
        "exception_stack",
        "action_suggestion",
    }

    assert isinstance(payload["top_spans"], list)
    assert len(payload["top_spans"]) >= 1
    assert set(payload["top_spans"][0].keys()) == {
        "service_name",
        "operation_name",
        "duration_ms",
        "status",
        "error_type",
        "exception_stack",
        "action_suggestion",
    }
