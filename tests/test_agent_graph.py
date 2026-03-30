from nebula_copilot.agent.graph import run_agent_graph
from nebula_copilot.mock_data import build_mock_trace
from nebula_copilot.models import Span, TraceDocument
from nebula_copilot.tools.types import ToolRegistry


def _registry() -> ToolRegistry:
    return ToolRegistry(
        query_trace=lambda tid: {
            "trace_id": tid,
            "bottleneck_service": "inventory-service",
            "keyword": "timeout",
        },
        query_jvm=lambda service_name: {
            "service": service_name,
            "heap_used_mb": 512,
            "gc_count": 3,
        },
        query_logs=lambda service_name, keyword: {
            "service": service_name,
            "keyword": keyword,
            "sample": ["timeout while waiting for downstream", "retry exhausted"],
        },
    )


def test_run_agent_graph_dual_route_success() -> None:
    trace_doc = build_mock_trace("trace-m3-timeout", "timeout")

    result = run_agent_graph(
        trace_id="trace-m3-timeout",
        run_id="run-m3-001",
        trace_doc=trace_doc,
        tool_registry=_registry(),
    )

    assert result["status"] == "ok"
    assert result["jvm"]["service"] == "inventory-service"
    assert result["logs"]["keyword"] == "timeout"
    assert "JVM证据" in str(result["summary"])
    assert "日志证据" in str(result["summary"])
    assert "建议动作" in str(result["summary"])
    assert "模式比对" in str(result["summary"])
    assert "关联查询" in str(result["summary"])
    assert "链路排查建议" in str(result["summary"])
    assert "优先检查 inventory-service" in str(result["summary"])
    route_event = next(item for item in result["history"] if item["node"] == "route")
    assert route_event["payload"]["route"] == "dual"


def test_run_agent_graph_jvm_route_success() -> None:
    trace_doc = build_mock_trace("trace-m3-db", "db")

    result = run_agent_graph(
        trace_id="trace-m3-db",
        run_id="run-m3-002",
        trace_doc=trace_doc,
        tool_registry=_registry(),
    )

    assert result["status"] == "ok"
    route_event = next(item for item in result["history"] if item["node"] == "route")
    assert route_event["payload"]["route"] == "jvm"
    assert result["jvm"]


def test_run_agent_graph_fallback_on_tool_failure() -> None:
    trace_doc = build_mock_trace("trace-m3-failure", "timeout")

    failing_registry = ToolRegistry(
        query_trace=lambda tid: {
            "trace_id": tid,
            "bottleneck_service": "inventory-service",
            "keyword": "timeout",
        },
        query_jvm=lambda service_name: (_ for _ in ()).throw(RuntimeError("jvm backend unavailable")),
        query_logs=lambda service_name, keyword: {"service": service_name, "keyword": keyword},
    )

    result = run_agent_graph(
        trace_id="trace-m3-failure",
        run_id="run-m3-003",
        trace_doc=trace_doc,
        tool_registry=failing_registry,
    )

    assert result["status"] == "failed"
    assert "jvm backend unavailable" in (result["error"] or "")
    fallback_event = next(item for item in result["history"] if item["node"] == "fallback")
    assert fallback_event["status"] == "failed"


def test_run_agent_graph_retry_then_success() -> None:
    trace_doc = build_mock_trace("trace-m3-retry-ok", "timeout")
    jvm_calls = {"count": 0}

    def flaky_jvm(service_name: str) -> dict:
        jvm_calls["count"] += 1
        if jvm_calls["count"] < 2:
            raise RuntimeError("jvm timeout")
        return {"service": service_name, "heap_used_mb": 640, "gc_count": 4}

    flaky_registry = ToolRegistry(
        query_trace=lambda tid: {
            "trace_id": tid,
            "bottleneck_service": "inventory-service",
            "keyword": "timeout",
        },
        query_jvm=flaky_jvm,
        query_logs=lambda service_name, keyword: {
            "service": service_name,
            "keyword": keyword,
            "sample": ["timeout once then recovered"],
        },
    )

    result = run_agent_graph(
        trace_id="trace-m3-retry-ok",
        run_id="run-m3-004",
        trace_doc=trace_doc,
        tool_registry=flaky_registry,
    )

    assert result["status"] == "ok"
    assert jvm_calls["count"] == 2
    retry_ok_event = next(
        item
        for item in result["history"]
        if item["node"] == "enrich_jvm" and item["status"] == "retry_ok"
    )
    assert retry_ok_event["payload"]["attempt"] == 2


def test_run_agent_graph_retry_exhausted_then_fallback() -> None:
    trace_doc = build_mock_trace("trace-m3-retry-fail", "db")

    always_fail_registry = ToolRegistry(
        query_trace=lambda tid: {
            "trace_id": tid,
            "bottleneck_service": "inventory-service",
            "keyword": "db",
        },
        query_jvm=lambda service_name: (_ for _ in ()).throw(RuntimeError("jvm always fail")),
        query_logs=lambda service_name, keyword: {"service": service_name, "keyword": keyword},
    )

    result = run_agent_graph(
        trace_id="trace-m3-retry-fail",
        run_id="run-m3-005",
        trace_doc=trace_doc,
        tool_registry=always_fail_registry,
    )

    assert result["status"] == "failed"
    retry_failed_events = [
        item
        for item in result["history"]
        if item["node"] == "enrich_jvm" and item["status"] == "retry_failed"
    ]
    assert len(retry_failed_events) == 3


def test_run_agent_graph_report_polish_with_llm() -> None:
    class FakeLLM:
        def diagnose_incident(self, context: dict) -> dict:
            return {
                "problem_type": "Downstream",
                "root_cause": "下游连接池耗尽导致请求堆积",
                "action": "优先扩容连接池并检查下游实例健康",
                "confidence": 0.92,
                "linkage_suspected": True,
                "linkage_action": "补查 Kafka lag 与重试队列深度，确认是否由背压传播引起",
            }

        def polish_summary(self, summary: str) -> str:
            return f"LLM润色: {summary}"

    trace_doc = build_mock_trace("trace-m3-polish", "timeout")

    result = run_agent_graph(
        trace_id="trace-m3-polish",
        run_id="run-m3-006",
        trace_doc=trace_doc,
        tool_registry=_registry(),
        llm_executor=FakeLLM(),
    )

    assert result["status"] == "ok"
    assert str(result["summary"]).startswith("LLM润色")
    assert "优先扩容连接池并检查下游实例健康" in str(result["summary"])
    assert "Kafka lag" in str(result["summary"])
    polish_event = next(item for item in result["history"] if item["node"] == "report_polish")
    assert polish_event["status"] == "ok"
    decision_event = next(item for item in result["history"] if item["node"] == "llm_decision")
    assert decision_event["status"] == "ok"


def test_run_agent_graph_normalizes_none_keyword_for_logs_route() -> None:
    trace_doc = TraceDocument(
        trace_id="trace-m3-none-keyword",
        root=Span(
            span_id="root",
            parent_span_id=None,
            service_name="gateway-service",
            operation_name="HTTP GET /api/v1/order/confirm",
            duration_ms=120,
            status="OK",
            exception_stack=None,
            children=[
                Span(
                    span_id="s1",
                    parent_span_id="root",
                    service_name="order-service",
                    operation_name="RPC createOrder",
                    duration_ms=420,
                    status="OK",
                    exception_stack=None,
                    children=[],
                )
            ],
        ),
    )

    seen = {"keyword": None}

    def capture_logs(service_name: str, keyword: str) -> dict:
        seen["keyword"] = keyword
        return {
            "service": service_name,
            "keyword": keyword,
            "status": "ok",
            "sample": ["request finished successfully"],
            "doc_count": 1,
        }

    registry = ToolRegistry(
        query_trace=lambda tid: {
            "trace_id": tid,
            "bottleneck_service": "order-service",
            "keyword": "none",
        },
        query_jvm=lambda service_name: {"service": service_name, "status": "ok"},
        query_logs=capture_logs,
    )

    result = run_agent_graph(
        trace_id="trace-m3-none-keyword",
        run_id="run-m3-007",
        trace_doc=trace_doc,
        tool_registry=registry,
    )

    assert result["status"] == "ok"
    assert seen["keyword"] == ""


def test_run_agent_graph_fail_when_llm_decision_required_but_unavailable() -> None:
    trace_doc = build_mock_trace("trace-m3-required-llm", "timeout")

    result = run_agent_graph(
        trace_id="trace-m3-required-llm",
        run_id="run-m3-008",
        trace_doc=trace_doc,
        tool_registry=_registry(),
        llm_executor=None,
        llm_decision_required=True,
    )

    assert result["status"] == "failed"
    assert "LLM decision required" in str(result.get("error") or "")
