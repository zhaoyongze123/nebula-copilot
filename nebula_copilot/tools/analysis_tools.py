from __future__ import annotations

from typing import Any, Dict

from nebula_copilot.analyzer import analyze_trace
from nebula_copilot.models import TraceDocument
from nebula_copilot.tools.response import build_tool_response


def analyze_trace_tool(trace: TraceDocument) -> Dict[str, Any]:
    result = analyze_trace(trace)
    full_payload = result.to_dict()
    raw_bottleneck = full_payload.get("bottleneck", {})
    bottleneck: Dict[str, Any] = {}
    if isinstance(raw_bottleneck, dict):
        bottleneck = {
            "service_name": raw_bottleneck.get("service_name"),
            "operation_name": raw_bottleneck.get("operation_name"),
            "duration_ms": raw_bottleneck.get("duration_ms"),
            "status": raw_bottleneck.get("status"),
            "error_type": raw_bottleneck.get("error_type"),
            "action_suggestion": raw_bottleneck.get("action_suggestion"),
            "knowledge_insight": raw_bottleneck.get("knowledge_insight"),
        }
    top_spans = full_payload.get("top_spans", [])
    compact_top = []
    if isinstance(top_spans, list):
        for item in top_spans[:3]:
            if not isinstance(item, dict):
                continue
            compact_top.append(
                {
                    "service_name": item.get("service_name"),
                    "operation_name": item.get("operation_name"),
                    "duration_ms": item.get("duration_ms"),
                    "status": item.get("status"),
                    "error_type": item.get("error_type"),
                }
            )

    payload = {
        "trace_id": full_payload.get("trace_id", trace.trace_id),
        "total_spans": full_payload.get("total_spans", 0),
        "bottleneck": bottleneck,
        "top_spans": compact_top,
        "summary": full_payload.get("summary", ""),
    }
    summary = f"诊断完成：trace={trace.trace_id}，瓶颈服务={result.bottleneck.span.service_name}"
    return build_tool_response("tool_analyze_trace", trace.trace_id, payload, summary=summary)
