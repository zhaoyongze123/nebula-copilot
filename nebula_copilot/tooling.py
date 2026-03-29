from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
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


def _json_size(payload: Dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False))


def _truncate_payload(payload: Dict[str, Any], max_bytes: int = 2048) -> tuple[Dict[str, Any], Dict[str, Any]]:
    raw = json.dumps(payload, ensure_ascii=False)
    original_size = len(raw)
    if original_size <= max_bytes:
        return payload, {
            "is_truncated": False,
            "original_size": original_size,
            "returned_size": original_size,
        }

    truncated_raw = raw[: max_bytes - 3] + "..."
    truncated_payload = {
        "_truncated_json": truncated_raw,
        "_note": "payload too large, returned compact preview",
    }
    returned_size = _json_size(truncated_payload)
    return truncated_payload, {
        "is_truncated": True,
        "original_size": original_size,
        "returned_size": returned_size,
    }


def _tool_response(
    tool_name: str,
    target: str,
    payload: Dict[str, Any],
    status: str = "ok",
    summary: str | None = None,
) -> Dict[str, Any]:
    payload_compact, truncation = _truncate_payload(payload)
    return {
        "status": status,
        "tool": tool_name,
        "target": target,
        "payload": payload_compact,
        "error": None,
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "payload_size": _json_size(payload_compact),
        },
        "summary": summary or f"{tool_name} 执行完成，目标={target}",
        "truncation": truncation,
    }


def tool_get_trace(trace_id: str, tool: TraceTool) -> Dict[str, Any]:
    """Phase 2 tool stub: get trace payload by trace id."""
    payload = tool(trace_id)
    summary = f"已获取 Trace：{trace_id}"
    return _tool_response("tool_get_trace", trace_id, payload, summary=summary)


def tool_analyze_trace(trace: TraceDocument) -> Dict[str, Any]:
    """Phase 2 tool stub: run deterministic diagnosis and return structured JSON."""
    result = analyze_trace(trace)
    payload = result.to_dict()
    summary = f"诊断完成：trace={trace.trace_id}，瓶颈服务={result.bottleneck.span.service_name}"
    return _tool_response("tool_analyze_trace", trace.trace_id, payload, summary=summary)


def tool_get_jvm_metrics(service_name: str, tool: JVMTool) -> Dict[str, Any]:
    """Phase 2 tool stub: query JVM metrics for a service."""
    payload = tool(service_name)
    summary = f"JVM 指标已获取：service={service_name}"
    return _tool_response("tool_get_jvm_metrics", service_name, payload, summary=summary)


def tool_search_logs(service_name: str, time_range: str, tool: LogsTool) -> Dict[str, Any]:
    """Phase 2 tool stub: query service logs by time range."""
    payload = tool(service_name, time_range)
    summary = f"日志检索完成：service={service_name}，keyword={time_range}"
    return _tool_response("tool_search_logs", service_name, payload, summary=summary)


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
