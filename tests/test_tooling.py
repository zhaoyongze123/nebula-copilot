from nebula_copilot.mock_data import build_mock_trace
from nebula_copilot.tooling import tool_analyze_trace, tool_get_jvm_metrics, tool_get_trace, tool_search_logs


def test_tool_get_trace_schema() -> None:
    result = tool_get_trace("trace-1", lambda trace_id: {"trace_id": trace_id, "bottleneck_service": "inventory"})

    assert set(result.keys()) == {"status", "tool", "target", "payload", "error", "meta", "summary", "truncation"}
    assert result["status"] == "ok"
    assert result["tool"] == "tool_get_trace"
    assert result["target"] == "trace-1"
    assert result["payload"]["trace_id"] == "trace-1"
    assert result["error"] is None
    assert "generated_at" in result["meta"]
    assert "已获取 Trace" in result["summary"]
    assert result["truncation"]["is_truncated"] is False


def test_tool_analyze_trace_schema() -> None:
    trace = build_mock_trace("trace-analyze", "timeout")

    result = tool_analyze_trace(trace)

    assert set(result.keys()) == {"status", "tool", "target", "payload", "error", "meta", "summary", "truncation"}
    assert result["status"] == "ok"
    assert result["tool"] == "tool_analyze_trace"
    assert result["target"] == "trace-analyze"
    assert "bottleneck" in result["payload"]
    assert result["error"] is None
    assert "诊断完成" in result["summary"]


def test_tool_get_jvm_metrics_schema() -> None:
    result = tool_get_jvm_metrics("inventory-service", lambda service_name: {"heap_used": 512, "service": service_name})

    assert set(result.keys()) == {"status", "tool", "target", "payload", "error", "meta", "summary", "truncation"}
    assert result["tool"] == "tool_get_jvm_metrics"
    assert result["target"] == "inventory-service"
    assert result["payload"]["service"] == "inventory-service"
    assert "JVM 指标" in result["summary"]


def test_tool_search_logs_schema() -> None:
    result = tool_search_logs("inventory-service", "timeout", lambda service_name, keyword: {"service": service_name, "keyword": keyword})

    assert set(result.keys()) == {"status", "tool", "target", "payload", "error", "meta", "summary", "truncation"}
    assert result["tool"] == "tool_search_logs"
    assert result["target"] == "inventory-service"
    assert result["payload"]["keyword"] == "timeout"
    assert "日志检索完成" in result["summary"]
