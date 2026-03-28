from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Protocol

from nebula_copilot.analyzer import analyze_trace
from nebula_copilot.models import TraceDocument


class TraceTool(Protocol):
    def __call__(self, trace_id: str) -> Dict[str, Any]:
        ...


class JVMTool(Protocol):
    def __call__(self, service_name: str) -> Dict[str, Any]:
        ...


class LogsTool(Protocol):
    def __call__(self, service_name: str, keyword: str) -> Dict[str, Any]:
        ...


@dataclass
class ToolRegistry:
    query_trace: TraceTool
    query_jvm: JVMTool
    query_logs: LogsTool


@dataclass
class AgentContext:
    trace_id: str
    tool_registry: ToolRegistry


def tool_get_trace(trace_id: str, tool: TraceTool) -> Dict[str, Any]:
    """Phase 2 tool stub: get trace payload by trace id."""
    return tool(trace_id)


def tool_analyze_trace(trace: TraceDocument) -> Dict[str, Any]:
    """Phase 2 tool stub: run deterministic diagnosis and return structured JSON."""
    result = analyze_trace(trace)
    return result.to_dict()


def tool_get_jvm_metrics(service_name: str, tool: JVMTool) -> Dict[str, Any]:
    """Phase 2 tool stub: query JVM metrics for a service."""
    return tool(service_name)


def tool_search_logs(service_name: str, time_range: str, tool: LogsTool) -> Dict[str, Any]:
    """Phase 2 tool stub: query service logs by time range."""
    return tool(service_name, time_range)


def run_agent_poc(ctx: AgentContext) -> Dict[str, Any]:
    """Phase 2 POC: simple deterministic tool-calling chain."""
    trace_payload = tool_get_trace(ctx.trace_id, ctx.tool_registry.query_trace)
    bottleneck_service = trace_payload.get("bottleneck_service", "unknown-service")

    jvm_payload = tool_get_jvm_metrics(bottleneck_service, ctx.tool_registry.query_jvm)
    keyword = trace_payload.get("keyword", "timeout")
    logs_payload = tool_search_logs(bottleneck_service, keyword, ctx.tool_registry.query_logs)

    return {
        "trace_id": ctx.trace_id,
        "bottleneck_service": bottleneck_service,
        "trace": trace_payload,
        "jvm": jvm_payload,
        "logs": logs_payload,
        "agent_report": (
            f"根据 trace/jvm/logs 联合分析，瓶颈服务 {bottleneck_service} "
            f"可能由 {keyword} 导致，请优先检查连接池和下游依赖可用性。"
        ),
    }
