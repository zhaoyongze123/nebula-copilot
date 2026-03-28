from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Protocol


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


def run_agent_poc(ctx: AgentContext) -> Dict[str, Any]:
    """Phase 2 POC: simple deterministic tool-calling chain."""
    trace_payload = ctx.tool_registry.query_trace(ctx.trace_id)
    bottleneck_service = trace_payload.get("bottleneck_service", "unknown-service")

    jvm_payload = ctx.tool_registry.query_jvm(bottleneck_service)
    keyword = trace_payload.get("keyword", "timeout")
    logs_payload = ctx.tool_registry.query_logs(bottleneck_service, keyword)

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
