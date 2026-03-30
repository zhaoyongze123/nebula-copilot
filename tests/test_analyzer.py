from nebula_copilot.analyzer import analyze_trace, build_alert_summary, classify_error
from nebula_copilot.models import Span, TraceDocument
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
    assert "模式比对" in summary
    assert "关联查询" in summary
    assert "链路排查建议" in summary


def test_classify_error_unknown_when_error_without_stack() -> None:
    trace = build_mock_trace("trace_t2", "timeout")
    span = trace.root  # ERROR but no exception stack
    assert classify_error(span) == "Unknown"


def test_analyze_trace_prefers_llm_action_when_available() -> None:
    class FakeLLM:
        def suggest_action(self, error_type: str, service_name: str, exception_stack: str | None) -> str:
            return f"LLM建议: {service_name} 先排查 {error_type}"

    trace = build_mock_trace("trace_llm_action", "timeout")
    result = analyze_trace(trace, top_n=1, llm_executor=FakeLLM())

    assert result.bottleneck.action_suggestion.startswith("LLM建议")


def test_analyze_trace_fallback_when_llm_fails() -> None:
    class FailingLLM:
        def suggest_action(self, error_type: str, service_name: str, exception_stack: str | None) -> str:
            raise RuntimeError("model unavailable")

    trace = build_mock_trace("trace_llm_fallback", "timeout")
    result = analyze_trace(trace, top_n=1, llm_executor=FailingLLM())

    assert "优先检查" in result.bottleneck.action_suggestion


def test_analyze_trace_ignores_synthetic_trace_root() -> None:
    trace = TraceDocument(
        trace_id="trace_synthetic_root",
        root=Span(
            span_id="root",
            parent_span_id=None,
            service_name="trace-root",
            operation_name="trace:trace_synthetic_root",
            duration_ms=2500,
            status="OK",
            exception_stack=None,
            children=[
                Span(
                    span_id="s1",
                    parent_span_id="root",
                    service_name="order-service",
                    operation_name="createOrder",
                    duration_ms=1300,
                    status="OK",
                    exception_stack=None,
                    children=[],
                ),
                Span(
                    span_id="s2",
                    parent_span_id="root",
                    service_name="inventory-service",
                    operation_name="reserveStock",
                    duration_ms=1800,
                    status="ERROR",
                    exception_stack="java.net.SocketTimeoutException: Read timed out",
                    children=[],
                ),
            ],
        ),
    )

    result = analyze_trace(trace, top_n=1)

    assert result.bottleneck.span.service_name == "inventory-service"
    assert result.bottleneck.error_type == "Timeout"


def test_analyze_trace_adds_knowledge_insight() -> None:
    trace = build_mock_trace("trace_kb_timeout", "timeout")

    result = analyze_trace(trace, top_n=1)

    insight = result.bottleneck.knowledge_insight
    assert insight is not None
    assert insight.matched_patterns
    assert any(item.get("label") == "依赖挂掉" for item in insight.matched_patterns)
    assert "关联服务指标" in insight.relation_query_hint
    assert insight.linkage_investigation_suggestion is not None


def test_analyze_trace_matches_config_drift_pattern() -> None:
    trace = TraceDocument(
        trace_id="trace_kb_config_drift",
        root=Span(
            span_id="root",
            parent_span_id=None,
            service_name="gateway-service",
            operation_name="POST /api/checkout",
            duration_ms=520,
            status="ERROR",
            exception_stack=None,
            children=[
                Span(
                    span_id="biz-1",
                    parent_span_id="root",
                    service_name="order-service",
                    operation_name="RPC createOrder",
                    duration_ms=1600,
                    status="ERROR",
                    exception_stack="IllegalArgumentException: config version mismatch for property order.timeout.ms",
                    children=[],
                )
            ],
        ),
    )

    result = analyze_trace(trace, top_n=1)
    insight = result.bottleneck.knowledge_insight

    assert insight is not None
    assert any(item.get("label") == "配置漂移" for item in insight.matched_patterns)
