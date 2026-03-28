from nebula_copilot.analyzer import analyze_trace, build_alert_summary, classify_error
from nebula_copilot.mock_data import build_mock_trace


def test_bottleneck_timeout_scenario() -> None:
    trace = build_mock_trace("trace_t1", "timeout")
    result = analyze_trace(trace, top_n=3)

    assert result.bottleneck.span.service_name == "inventory-service"
    assert result.bottleneck.error_type == "Timeout"
    assert result.top_spans[0].span.duration_ms >= result.top_spans[1].span.duration_ms


def test_error_classification_db_and_downstream() -> None:
    db_trace = build_mock_trace("trace_db", "db")
    db_result = analyze_trace(db_trace, top_n=1)
    assert db_result.bottleneck.error_type == "DB"

    ds_trace = build_mock_trace("trace_ds", "downstream")
    ds_result = analyze_trace(ds_trace, top_n=1)
    assert ds_result.bottleneck.error_type == "Downstream"


def test_alert_summary_contains_key_fields() -> None:
    trace = build_mock_trace("trace_summary", "timeout")
    result = analyze_trace(trace, top_n=1)
    summary = build_alert_summary(result)

    assert "TraceID" in summary
    assert "瓶颈服务" in summary
    assert "建议动作" in summary


def test_classify_error_unknown_when_error_without_stack() -> None:
    trace = build_mock_trace("trace_t2", "timeout")
    span = trace.root  # ERROR but no exception stack
    assert classify_error(span) == "Unknown"
