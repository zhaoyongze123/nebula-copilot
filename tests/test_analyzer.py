from nebula_copilot.analyzer import analyze_trace, build_alert_summary, classify_error
from nebula_copilot.config import VectorConfig
from nebula_copilot.knowledge_base import KnowledgeBase
from nebula_copilot.models import Span, TraceDocument
from nebula_copilot.mock_data import build_mock_trace
from nebula_copilot.vector_store import VectorRecord, VectorSearchHit


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


def test_analyze_trace_adds_vector_pattern_when_rule_not_hit() -> None:
    class FakeVectorStore:
        def upsert(self, records: list[VectorRecord]) -> None:
            return None

        def search(self, query: str, top_k: int) -> list[VectorSearchHit]:
            return [
                VectorSearchHit(
                    record_id="dependency_outage",
                    score=0.87,
                    metadata={
                        "name": "dependency_outage",
                        "label": "依赖挂掉",
                        "description": "向量召回依赖故障模式",
                    },
                )
            ]

    trace = TraceDocument(
        trace_id="trace_kb_vector",
        root=Span(
            span_id="root",
            parent_span_id=None,
            service_name="gateway-service",
            operation_name="POST /api/pay",
            duration_ms=350,
            status="OK",
            exception_stack=None,
            children=[
                Span(
                    span_id="biz-1",
                    parent_span_id="root",
                    service_name="payment-service",
                    operation_name="RPC settle",
                    duration_ms=1400,
                    status="ERROR",
                    exception_stack="UnhandledRuntimeException",
                    children=[],
                )
            ],
        ),
    )
    knowledge_base = KnowledgeBase(
        vector_config=VectorConfig(enabled=True, provider="local", top_k=3, min_score=0.5),
        vector_store=FakeVectorStore(),
    )

    result = analyze_trace(trace, top_n=1, knowledge_base=knowledge_base)
    insight = result.bottleneck.knowledge_insight

    assert insight is not None
    assert insight.matched_patterns
    assert insight.matched_patterns[0].get("label") == "依赖挂掉"
    assert insight.matched_patterns[0].get("match_source") == "vector"
    assert insight.matched_patterns[0].get("vector_provider") == "custom"
    assert insight.matched_patterns[0].get("vector_score") == 0.87


def test_knowledge_base_from_app_config_with_vector_enabled() -> None:
    from pathlib import Path
    from nebula_copilot.config import AppConfig
    from nebula_copilot.knowledge_base import KnowledgeBase

    app_config = AppConfig(
        llm=None,
        vector=VectorConfig(enabled=True, provider="local", top_k=3, min_score=0.5),
    )

    kb = KnowledgeBase.from_app_config(app_config)

    assert kb._vector_config.enabled is True
    assert kb._vector_store is not None


def test_knowledge_base_from_app_config_with_vector_disabled() -> None:
    from nebula_copilot.config import AppConfig, LLMConfig
    from nebula_copilot.knowledge_base import KnowledgeBase

    app_config = AppConfig(
        llm=LLMConfig(),
        vector=VectorConfig(enabled=False, provider="local"),
    )

    kb = KnowledgeBase.from_app_config(app_config)

    assert kb._vector_config.enabled is False
    assert kb._vector_store is None


def test_vector_evidence_fields_captured_in_diagnosis() -> None:
    class FakeVectorStore:
        def upsert(self, records: list[VectorRecord]) -> None:
            return None

        def search(self, query: str, top_k: int) -> list[VectorSearchHit]:
            return [
                VectorSearchHit(
                    record_id="consumer_backlog",
                    score=0.72,
                    metadata={
                        "name": "consumer_backlog",
                        "label": "消费积压",
                        "description": "向量召回消费积压问题",
                    },
                )
            ]

    trace = TraceDocument(
        trace_id="trace_vector_evidence",
        root=Span(
            span_id="root",
            parent_span_id=None,
            service_name="gateway",
            operation_name="POST /api/process",
            duration_ms=400,
            status="OK",
            exception_stack=None,
            children=[
                Span(
                    span_id="consumer-1",
                    parent_span_id="root",
                    service_name="consumer-svc",
                    operation_name="process_messages",
                    duration_ms=2500,
                    status="ERROR",
                    exception_stack="RuntimeException: unknown critical error occurred",
                    children=[],
                )
            ],
        ),
    )
    kb = KnowledgeBase(
        vector_config=VectorConfig(enabled=True, provider="local", top_k=3, min_score=0.5),
        vector_store=FakeVectorStore(),
    )

    result = analyze_trace(trace, top_n=1, knowledge_base=kb)
    patterns = result.bottleneck.knowledge_insight.matched_patterns

    # Verify evidence fields are present
    assert len(patterns) > 0
    vector_pattern = next((p for p in patterns if p.get("match_source") == "vector"), None)
    assert vector_pattern is not None
    assert "vector_provider" in vector_pattern
    assert "vector_score" in vector_pattern
    assert vector_pattern["vector_score"] == 0.72
