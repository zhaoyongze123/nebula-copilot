from __future__ import annotations

from typing import Any, Dict

from nebula_copilot.models import TraceDocument
from nebula_copilot.tools.analysis_tools import analyze_trace_tool
from nebula_copilot.tools.jvm_tools import get_jvm_metrics_tool
from nebula_copilot.tools.logs_tools import search_logs_tool
from nebula_copilot.tools.trace_tools import get_trace_tool
from nebula_copilot.tools.types import AgentContext, JVMTool, LogsTool, ToolRegistry, TraceTool


def tool_get_trace(trace_id: str, tool: TraceTool) -> Dict[str, Any]:
    """兼容入口：保留 tooling.py 调用方式。"""
    return get_trace_tool(trace_id, tool)


def tool_analyze_trace(trace: TraceDocument) -> Dict[str, Any]:
    """兼容入口：保留 tooling.py 调用方式。"""
    return analyze_trace_tool(trace)


def tool_get_jvm_metrics(service_name: str, tool: JVMTool) -> Dict[str, Any]:
    """兼容入口：保留 tooling.py 调用方式。"""
    return get_jvm_metrics_tool(service_name, tool)


def tool_search_logs(service_name: str, time_range: str, tool: LogsTool) -> Dict[str, Any]:
    """兼容入口：保留 tooling.py 调用方式。"""
    return search_logs_tool(service_name, time_range, tool)


def run_agent_poc(ctx: AgentContext) -> Dict[str, Any]:
    """Phase 2 POC: simple deterministic tool-calling chain."""
    trace_result = tool_get_trace(ctx.trace_id, ctx.tool_registry.query_trace)
    trace_payload = trace_result.get("payload", {})
    bottleneck_service = trace_payload.get("bottleneck_service", "unknown-service")

    jvm_result = tool_get_jvm_metrics(bottleneck_service, ctx.tool_registry.query_jvm)
    keyword = trace_payload.get("keyword", "timeout")
    logs_result = tool_search_logs(bottleneck_service, keyword, ctx.tool_registry.query_logs)

    return {
        "trace_id": ctx.trace_id,
        "bottleneck_service": bottleneck_service,
        "trace": trace_result,
        "jvm": jvm_result,
        "logs": logs_result,
        "agent_report": (
            f"根据 trace/jvm/logs 联合分析，瓶颈服务 {bottleneck_service} "
            f"可能由 {keyword} 导致，请优先检查连接池和下游依赖可用性。"
        ),
    }
